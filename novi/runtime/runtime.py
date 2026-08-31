"""
NoviRuntime — native tool-calling agentic loop.

Loop:
  USER INPUT
  → detect intent → build tools + prompt
  → pre-loop retrieval (research intent)
  → LOOP: model.invoke → tool_calls? → permission gate → exec → feed back
                       ↘ no calls → stream final answer → done
  → compact history when it grows past the window
"""

import json
import base64
import logging
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
)
from ..orchestrator.intent import classify_intent
from .model_selector import ModelSelector, model_capabilities
from .trace import DebugTraceEvent, ExecutionTrace, TraceAction
from .execution_context import ExecutionContext
from .retrieval import RecoveryAction, RetrievalExecutor
from .sources import KnowledgeRetrievalSource
from ..brain.types import Turn
from ..memory.knowledge_index import get_knowledge_index
from ..capabilities import CapabilityRegistry
from ..capabilities.builtin import register_builtin_capabilities
from .model_selector import ModelSelector, model_capabilities
from .tracer import RuntimeTracer
from .trace import TraceAction

_INTENT_TO_CAP_IDS = {"conversation": ["conversation"], "research": ["research", "conversation"], "coding": ["coding", "filesystem", "terminal"], "planning": ["planning", "conversation"], "vision": ["vision", "conversation"]}
# Phase 2: capability/intent → workload. The workload's configured model is
# the ONLY model used. Capabilities never upgrade, substitute, or rank.
_CAPABILITY_TO_WORKLOAD = {
    "coding": "code",
    "planning": "research",
    "research": "research",
    "conversation": "general",
    "vision": "general",
    "filesystem": "general",
    "terminal": "general",
    "memory": "general",
    "search": "general",
}

log = logging.getLogger("novi.runtime")

from ..paths import home as app_home

SKILLS_DIR = app_home() / "skills"

# Phase 8A: the tool-category table lives in ONE place —
# ``tool_registry.TOOL_CATEGORIES``. The former duplicate copies here and in
# tool_executor are gone; ToolExecutor.tool_category reads the single source.

_SKILL_RE = re.compile(r"@skill\s+([a-z0-9][a-z0-9-]*)", re.IGNORECASE)

_MAX_SKILL_FILES_CHARS = 6000

def _load_all_skills() -> dict[str, dict]:
    skills: dict[str, dict] = {}
    if not SKILLS_DIR.is_dir():
        return skills
    for folder in sorted(SKILLS_DIR.iterdir()):
        if not folder.is_dir():
            continue
        skill_file = folder / "SKILL.md"
        if not skill_file.exists():
            continue
        content = skill_file.read_text("utf-8")
        name = folder.name
        description = ""
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end != -1:
                import yaml
                try:
                    fm = yaml.safe_load(content[3:end])
                    if isinstance(fm, dict):
                        description = fm.get("description", "") or ""
                        name = fm.get("name", name)
                except Exception:
                    pass
        files: dict[str, str] = {}
        for f in folder.rglob("*"):
            if not f.is_file() or f.name == "SKILL.md":
                continue
            if f.suffix == ".pyc" or "__pycache__" in str(f):
                continue
            try:
                rel = str(f.relative_to(folder))
                files[rel] = f.read_text("utf-8")
            except Exception:
                pass
        skills[name] = {
            "name": name,
            "description": description,
            "content": content,
            "files": files,
            "path": folder,
        }
    return skills
from .permissions import PermissionResolver
from .tool_registry import ToolRegistry
from .lessons import LessonStore
from ..models import ModelUnavailableError
from .retrieval import RetrievalExecutor
from .tool_executor import ToolExecutor
from .event_bus import EventType

# Phase 9B: the ReAct loop body lives in the generic single-attempt executor
# (novi/runtime/react_attempt.py). The sentinel and its payload contract
# ((_LOOP_DONE, final_text, stop_reason, success)) are re-exported here
# because run_stream's branches and graph collaborators consume them.
from .react_attempt import _LOOP_DONE, run_react_attempt


def _empty_run_error(label: str) -> tuple:
    """Visible failure for a graph run that ended without any answer.

    Silent-empty terminals used to produce NO frames: no token, no error.
    The UI just stopped (Deep Research re-synthesis failure). A run must
    always surface its outcome."""
    return ("error", f"{label} finished without producing an answer. "
                     f"Try rephrasing your question, or ask me to continue.")

_IDENTITY = (
    "You are Novi, a capable local AI assistant running entirely on-device via Ollama. "
    "You help with coding, file editing, debugging, running commands, research, writing, "
    "analysis, and general questions.\n"
    "Today's date is {date}. Your training data is older than this — for "
    "anything time-sensitive, trust tool results over your own knowledge.\n\n"
    "Take ONE concrete step at a time. Call a tool, read its result, decide next step. "
    "When done, respond normally with NO tool call.\n"
    "- Prefer reading files over guessing contents.\n"
    "- If a tool errors, try a corrected call — don't give up after one failure.\n"
    "- Be concise and direct. No hedging, no filler.\n"
    "- Use search results as primary source; internal knowledge supplements.\n"
)

_COMPACT_PROMPT = """Condense into 4-6 sentences. Keep: user goal, key facts, decisions, assumptions. Drop: greetings, dead-ends.

{text}

Context note:"""

class NoviRuntime:
    """Single agentic runtime loop with native tool calling."""

    def __init__(
        self,
        model_service=None,
        memory=None,
        registry: ToolRegistry | None = None,
        project_index=None,
        cfg: dict | None = None,
        simple_llm: object | None = None,
        skills: dict | None = None,
        event_bus=None,
        orchestrator=None,
        debug_trace: bool = False,
        brain=None,
        mcp_permissions=None,
        research_graph=None,
        coding_graph=None,
        runtime_graph=None,
        workflow_engine: str = "legacy",
    ):
        self.model_service = model_service
        self.simple_llm = simple_llm
        self.memory = memory
        self.brain = brain
        self._registry = registry or ToolRegistry()
        self.project_index = project_index
        self.cfg = cfg or {}
        self.event_bus = event_bus
        self._orchestrator = orchestrator
        self.history: list[tuple[str, str]] = []
        self._summary: str = ""
        rt = self.cfg.get("runtime", {})
        self.max_history = rt.get("max_history", 10)
        self.max_steps = rt.get("max_steps", 10)
        self.max_tool_output = rt.get("max_tool_output_chars", 8000)
        self.memory_distance_threshold = rt.get("memory_distance_threshold", 0.5)
        self.max_memory_results = rt.get("max_memory_results", 3)
        self.max_project_results = rt.get("max_project_results", 3)
        self.temperature = rt.get("temperature", 0.4)
        self._perms = PermissionResolver(self.cfg)
        self._perm_mode = "manual"
        self.tracer = RuntimeTracer(event_bus, debug_trace)
        self._skills = skills if skills is not None else _load_all_skills()
        self.stop_event: threading.Event | None = None
        self._agent_system_extra: str = ""
        self.debug_trace = debug_trace
        self.lesson_store = LessonStore()
        # Workspace service (READ only for beta, extensible)
        workspace_svc = None
        try:
            from ..workspace.service import WorkspaceService
            from pathlib import Path as _P
            # lazy: only create if needed, but keep reference for retrieval
            workspace_svc = WorkspaceService()
        except Exception:
            workspace_svc = None
        self.retrieval_executor = RetrievalExecutor(
            event_bus=event_bus,
            debug_trace=debug_trace,
            memory=memory,
            brain=brain,
            project_index=project_index,
            max_memory_results=self.max_memory_results,
            memory_distance_threshold=self.memory_distance_threshold,
            max_project_results=self.max_project_results,
            knowledge_source=(
                KnowledgeRetrievalSource(brain)
                if brain is not None
                else KnowledgeRetrievalSource(get_knowledge_index())
            ),
            workspace_service=workspace_svc,
        )
        self._capability_registry = CapabilityRegistry()
        register_builtin_capabilities(self._capability_registry)
        routing = rt.get("routing", {})
        # Phase 2: ModelSelector resolves the configured workload model
        # verbatim at execution time — no default_model, no ranking, no
        # substitution, no resource/VRAM preference.
        self._model_selector = ModelSelector(self.model_service)
        self.force_capability = rt.get("force_capability", "") or ""
        self.force_model = rt.get("force_model", "") or ""
        if self.force_capability:
            log.info("force_capability set to %s (debug override)", self.force_capability)
        if self.force_model:
            log.info("force_model set to %s (debug override)", self.force_model)
        self._intent_cap_ids = routing.get("intent_capabilities", _INTENT_TO_CAP_IDS)
        tools_cfg = rt.get("tools", {})
        self._tool_fallbacks: dict[str, list[str]] = tools_cfg.get("fallbacks", {})
        self.tool_executor = ToolExecutor(
            registry=self._registry,
            perms=self._perms,
            lesson_store=self.lesson_store,
            lc_tools=self._registry.as_lc_tools(),
            tool_fallbacks=self._tool_fallbacks,
            max_tool_output=self.max_tool_output,
            perm_mode=self._perm_mode,
            debug_trace=self.debug_trace,
            event_bus=self.event_bus,
        )
        # M5.4: MCP server permission gate shared from the composition root.
        # None (CLI/standalone runtimes) = the existing permission path is used
        # exactly as before; MCP server permissions simply don't apply there.
        self.mcp_permissions = mcp_permissions
        if mcp_permissions is not None:
            self.tool_executor.mcp_permissions = mcp_permissions
        # Phase 7 Stage 3C: LangGraph research workflow. When wired (composition
        # root) the research intent executes through the graph's explicit
        # search/evaluate/synthesize/validate transitions instead of the
        # hand-rolled inline ReAct loop. None = legacy behavior unchanged.
        self._research_graph = research_graph
        # Phase 7 Stage 3D: LangGraph coding workflow. When wired the coding
        # intent executes through the graph's explicit
        # implement/verify transitions (bounded re-implement loop) while tool
        # execution stays in the runtime's ReAct loop / ToolExecutor. None =
        # legacy behavior unchanged.
        self._coding_graph = coding_graph
        # Dual-path migration: the general runtime workflow graph
        # (analyze → retrieve → reason → act → reflect → answer). Opt-in via
        # ``workflow_engine="langgraph"``; "legacy" (default) keeps
        # run_stream's ReAct path byte-identical. The graph composes existing
        # Novi components only — it never owns retrieval, storage, tools,
        # or model selection.
        self._runtime_graph = runtime_graph
        self._workflow_engine = workflow_engine
        # NOTE: legacy inline-planning knobs were removed in Milestone 5
        # Phase 3 — PlannerEngine is the sole planning authority.
    def set_config(self, **kwargs):
        """Apply configuration from external consumers."""
        for k, v in kwargs.items():
            if k == "force_model":
                self.force_model = v
            elif k == "max_steps":
                self.max_steps = int(v)
            elif k == "temperature":
                self.temperature = float(v)
            elif k == "agent_system_extra":
                self._agent_system_extra = v
            elif k == "project_context":
                self._project_context = v
            elif k == "project_index":
                self.project_index = v
                self.retrieval_executor.set_project_index(v)
            elif k == "permission_mode":
                self._perm_mode = v
                self._perms.auto = (v == "bypass")
                self.tool_executor.set_perm_mode(v)
            elif k == "stop_event":
                self.stop_event = v
            else:
                raise ValueError(f"Unknown config key: {k}")
    def get_status(self) -> dict:
        return {
            "model": getattr(self, "force_model", "") or "",
            "permission_mode": getattr(self, "_perm_mode", "manual"),
            "project_loaded": bool(getattr(self, "_project_context", "")),
        }
    def _system_prompt(self, user_input: str, intent: str = "conversation",
                       grounding: str = "",
                       grounding_error: str | None = None,
                       attachments: list[dict] | None = None,
                       activated_skills: list[dict] | None = None,
                       allowed_tools: list[str] | None = None,
                       analysis=None,
                       trace=None,
                       memory_context: str = "",
                       project_context: str = "",
                       workspace_context: str = "",
                       workspace_files: list[str] | None = None,
                       stable_state_text: str = "") -> str:
        parts = [_IDENTITY.format(date=datetime.now().strftime("%A, %B %d, %Y"))]
        if self._agent_system_extra:
            parts.append(f"AGENT INSTRUCTIONS:\n{self._agent_system_extra}")
        personality = (self.cfg.get("personality") or "").strip()
        if personality:
            parts.append(f"USER PREFERENCES:\n{personality}")
        if self._skills:
            skill_lines = "\n".join(
                f"  {n} — {s['description'][:120]}"
                for n, s in self._skills.items()
            )
            parts.append(f"SKILLS:\n{skill_lines}")
        if activated_skills:
            for sk in activated_skills:
                parts.append(self._skill_block(sk))
        if attachments:
            file_list = "\n".join(
                f"- {a['name']} ({a['type']}, {a.get('mime', 'unknown')}) — available at {a.get('path', a.get('url', 'unknown'))}"
                for a in attachments
            )
            parts.append(f"\nUser attached files:\n{file_list}\nReference these when relevant. For images, you can see them directly.")
        if self._summary:
            parts.append(f"\nContext from earlier in this session:\n{self._summary}")
        if stable_state_text:
            parts.append(f"\nStable execution state (from prior compaction):\n{stable_state_text[:1200]}")
        elif getattr(self, '_stable_state_text', None):
            parts.append(f"\nStable execution state (from prior compaction):\n{self._stable_state_text[:1200]}")
        if memory_context:
            parts.append(f"\nRelevant memory from past sessions:{memory_context}")
        lessons = self.lesson_store.get_context(tool_names=allowed_tools if allowed_tools else None)
        if lessons:
            parts.append(lessons)
        if project_context:
            parts.append(f"\nRelevant project context:\n{project_context}")
        if getattr(self, '_project_context', None):
            # legacy raw inject — kept for backward compat, but first-class
            # project_context from ExecutionContext is preferred and already budgeted
            if not project_context or self._project_context.strip() != project_context.strip():
                parts.append(f"\nProject context:\n{self._project_context}")
        if workspace_context:
            parts.append(f"\nRelevant workspace files:\n{workspace_context}")
            if workspace_files:
                parts.append(f"\nFiles used: {', '.join(workspace_files[:5])}")
        if grounding:
            parts.append(f"\nSearch results (use as primary source — prioritize over internal knowledge):\n{grounding}\n")
        elif grounding_error:
            parts.append("\nSearch failed. Rely on internal knowledge or suggest retry. Do NOT pretend info exists.")

        return "\n\n".join(parts)
    def _skill_block(self, sk: dict) -> str:
        out = [f"ACTIVATED SKILL: {sk['name']}\n{sk['content']}"]
        files = sk.get("files") or {}
        if files:
            rendered, skipped, used = [], [], 0
            for path, text in files.items():
                block = f"--- {path} ---\n{text}"
                if used + len(block) > _MAX_SKILL_FILES_CHARS:
                    skipped.append(path)
                    continue
                rendered.append(block)
                used += len(block)
            body = "\n\n".join(rendered)
            if skipped:
                skill_dir = sk.get("path", "the skill folder")
                body += (f"\n\n({len(skipped)} more skill file(s) not shown to "
                         f"save context: {', '.join(skipped)}. They live under "
                         f"{skill_dir} — read one with read_file if you need it.)")
            out.append(f"SKILL FILES ({sk['name']}):\n{body}")
        return "\n\n".join(out)
    def _scan_skills(self, text: str, already: list[dict]) -> list[dict]:
        found: list[dict] = []
        if not self._skills or not text:
            return found
        for m in _SKILL_RE.finditer(text):
            sk = self._skills.get(m.group(1).lower())
            if sk and sk not in already and sk not in found:
                found.append(sk)
        return found
    def _workload_for(self, ctx, hint: str) -> str:
        """Map capability/intent to a workload. ``general`` is the catch-all.

        Phase 2: capabilities describe the task, never upgrade or substitute
        the selected model — they only decide which workload slot is used.
        """
        cap = ctx.force_capability or hint
        if cap in _CAPABILITY_TO_WORKLOAD:
            return _CAPABILITY_TO_WORKLOAD[cap]
        for c in ctx.cap_ids:
            if c in _CAPABILITY_TO_WORKLOAD:
                return _CAPABILITY_TO_WORKLOAD[c]
        return "general"
    def _build_multimodal_content(self, text: str, attachments: list[dict]) -> list:
        content: list = [{"type": "text", "text": text}]
        for att in attachments:
            if att["type"] != "image":
                continue
            path = att.get("path", "")
            if not path or not Path(path).exists():
                continue
            try:
                data = Path(path).read_bytes()
                b64 = base64.b64encode(data).decode("utf-8")
                mime = att.get("mime", "image/png")
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            except Exception:
                content.append({"type": "text", "text": f"[Image: {att['name']} — failed to load]"})
        return content
    def run_stream(self, user_input: str | None = None,
                   attachments: list[dict] | None = None,
                   force_capability: str | None = None,
                   force_model: str | None = None,
                   execution_plan: object | None = None,
                   context: ExecutionContext | None = None,
                   conversation_id: str | None = None,
                   project_id: str | None = None,
                   resume_from: int | None = None):
        """Yield (kind, text) tuples from the agentic loop."""
        intent_str = "conversation"
        try:
            ctx = context
            if ctx is None:
                ctx = ExecutionContext(
                    user_input=user_input or "",
                    attachments=attachments or [],
                    history=list(self.history),
                    summary=self._summary,
                    force_model=force_model or self.force_model,
                    force_capability=force_capability or self.force_capability,
                )
            if conversation_id:
                ctx.conversation_id = conversation_id
            if project_id:
                ctx.project_id = project_id
            if resume_from is not None:
                ctx.resume_from = resume_from
            if execution_plan is not None:
                ctx.execution_plan = execution_plan
            if ctx.trace is None:
                ctx.trace = ExecutionTrace(user_input=ctx.user_input)
            user_input = ctx.user_input

            has_images = ctx.attachments and any(a.get("type") == "image" for a in ctx.attachments)
            ctx.has_images = has_images
            if not ctx.activated_skills:
                ctx.activated_skills = self._scan_skills(user_input, [])

            if ctx.execution_plan is not None:
                ctx.analysis = ctx.execution_plan.context.get("analysis")
                if not ctx.allowed_tools:
                    ctx.allowed_tools = list(ctx.execution_plan.tools)
            elif ctx.analysis is not None:
                if not ctx.allowed_tools:
                    ctx.allowed_tools = self._capability_registry.get_tool_names(ctx.cap_ids)
            elif self._orchestrator is not None:
                ctx.analysis = self._orchestrator.analyze(user_input, self.history, has_images)
                ctx.allowed_tools = self._capability_registry.get_tool_names(ctx.cap_ids)
            else:
                intent = classify_intent(user_input, self.simple_llm, self.history, has_images)
                cap_name = ctx.force_capability or intent.value
                ctx.allowed_tools = self._capability_registry.get_tool_names(
                    self._intent_cap_ids.get(cap_name, ["conversation"]))

            if ctx.analysis is not None:
                ctx.trace.complexity_score = ctx.analysis.complexity.score
                ctx.trace.plan_level = ctx.analysis.complexity.plan_level
                self.tracer.debug(ctx.trace, "analysis", {
                    "signals": [s.type for s in ctx.analysis.evidence.signals],
                    "confidence": ctx.analysis.evidence.confidence,
                    "needs_grounding": ctx.analysis.grounding.needs_grounding,
                    "grounding_confidence": ctx.analysis.grounding.confidence,
                    "grounding_source": ctx.analysis.grounding.source,
                })

            intent_str = ctx.intent_str
            ctx.trace.intent = intent_str

            yield ("status", "Analyzing request...")
            event = self.tracer.emit(
                action=TraceAction.UNDERSTANDING,
                category="reasoning",
                summary="Determining how to process this question.",
                trace=ctx.trace,
                debug_category="analysis",
                debug_data={
                    "intent": intent_str,
                    "has_orchestrator": self._orchestrator is not None,
                },
            )
            yield ("trace", event)
            if self.event_bus:
                try:
                    self.event_bus.emit("intent_set", intent=intent_str)
                except Exception:
                    pass
            if self.stop_event and self.stop_event.is_set():
                self.tracer.finalize(ctx.trace, "stopped")
                return
 
            for kind_value in self.retrieval_executor.execute(ctx, user_input):
                yield kind_value

            # ── Model resolution (Phase 2): capabilities → workload → model ──
            # The workload's configured model is used verbatim. No ranking,
            # substitution, VRAM/loaded preference, or fallback. A missing or
            # unset workload model raises ModelUnavailableError surfaced as an
            # explicit error — never an alternate selection.
            try:
                if ctx.execution_plan is not None:
                    ctx.model_name = ctx.execution_plan.model_spec.get("model", "") or ctx.force_model or ""
                    ctx.workload = self._workload_for(ctx, intent_str)
                    if not ctx.model_name:
                        ctx.model_name = self._model_selector.resolve(ctx.workload)
                    ctx.temperature = ctx.execution_plan.temperature
                    ctx.max_steps = ctx.execution_plan.max_steps
                    ctx.model_supports_tools = ctx.execution_plan.model_spec.get("supports_tools", True)
                elif ctx.analysis is not None:
                    if not ctx.model_name:
                        ctx.model_name = ctx.force_model or ""
                    ctx.workload = self._workload_for(ctx, intent_str)
                    if not ctx.model_name:
                        ctx.model_name = self._model_selector.resolve(ctx.workload)
                    ctx.temperature = self.temperature
                    ctx.max_steps = ctx.analysis.complexity.max_steps
                else:
                    cap_name = ctx.force_capability or intent_str
                    ctx.workload = self._workload_for(ctx, cap_name)
                    if not ctx.model_name:
                        ctx.model_name = ctx.force_model or ""
                    if not ctx.model_name:
                        ctx.model_name = self._model_selector.resolve(ctx.workload)
                    ctx.temperature = self.temperature
                    ctx.max_steps = self.max_steps
            except ModelUnavailableError as e:
                self.tracer.finalize(ctx.trace, "error")
                yield ("status", f"Model unavailable: {e}")
                yield ("error", str(e))
                yield (_LOOP_DONE, str(e), "error", False)
                return

            # ── ContextManager gatekeeper (agent-wide, before model) — pre-check
            # L3 Checkpoint contract: ctx.metadata["stable_state"] (StableState.to_dict())
            # is the canonical source for Checkpoint.stable. Job creation copies it via
            # ContextManager.checkpoint_stable(ctx).to_dict() or directly from
            # ctx.metadata["stable_state"]. We persist to ctx.trace.metadata here so
            # the caller can create Checkpoint without re-deriving.
            try:
                from .context_manager import ContextManager
                from novi.common.execution_state import StableState

                cm = ContextManager(model_name=ctx.model_name, simple_llm=self.simple_llm)
                level = cm.should_compact(ctx)
                if level in ("compact", "emergency"):
                    cm.compact_history(ctx)
                    # persist StableState for diagnostics BEFORE next model call
                    # compact_history already stores stable_state dict+text; re-derive to ensure fresh
                    try:
                        stable = StableState.from_context(ctx)
                        ctx.metadata["stable_state"] = stable.to_dict()
                        ctx.metadata["compacted"] = True
                    except Exception:
                        pass
                    if ctx.trace is not None:
                        try:
                            if not hasattr(ctx.trace, "metadata"):
                                ctx.trace.metadata = {}  # type: ignore[attr-defined]
                            ctx.trace.metadata["context_compacted"] = level  # type: ignore
                            if "stable_state" in ctx.metadata:
                                ctx.trace.metadata["stable_state"] = ctx.metadata["stable_state"]  # type: ignore
                        except Exception:
                            pass
            except Exception:
                pass

            if ctx.execution_plan is None and intent_str == "vision":
                ctx.model_supports_tools = False

            # Capability validation on the SELECTED model: images require a
            # vision-capable model. Reject explicitly — never substitute.
            if ctx.has_images:
                caps = model_capabilities(ctx.model_name)
                if not caps.supports_vision:
                    msg = (f"Model '{ctx.model_name}' for workload '{ctx.workload}' "
                           f"does not support image input. Select a vision-capable "
                           f"model for the general workload.")
                    self.tracer.finalize(ctx.trace, "error")
                    yield ("status", f"Model unavailable: {msg}")
                    yield ("error", msg)
                    yield (_LOOP_DONE, msg, "error", False)
                    return

            ctx.trace.model_selected = ctx.model_name
            ctx.trace.workload = ctx.workload
            if ctx.execution_plan is not None:
                ctx.trace.model_reason = "execution_plan"
                ctx.model_reason = "execution_plan"
            elif ctx.force_model:
                ctx.trace.model_reason = "config_override"
                ctx.model_reason = "config_override"
            elif ctx.force_capability:
                ctx.trace.model_reason = "force_capability"
                ctx.model_reason = "force_capability"
            else:
                ctx.trace.model_reason = "workload_match"
                ctx.model_reason = "workload_match"
            yield ("model", ctx.model_name)

            if self.stop_event and self.stop_event.is_set():
                self.tracer.finalize(ctx.trace, "stopped")
                return

            if self.stop_event and self.stop_event.is_set():
                self.tracer.finalize(ctx.trace, "stopped")
                return

            recovery_decision = self.retrieval_executor.recommend_pre_loop(ctx)
            if recovery_decision.action == RecoveryAction.UPGRADE_SEARCH:
                search_tools = self._capability_registry.get_tool_names(["search"])
                missing = set(search_tools) - set(ctx.allowed_tools)
                if missing:
                    ctx.allowed_tools = list(set(ctx.allowed_tools) | set(search_tools))
                if missing or not recovery_decision.commit_on_grant:
                    state = self.retrieval_executor.commit_recovery(
                        ctx, recovery_decision, recovery_decision.action.value)
                    if self.debug_trace and ctx.trace is not None:
                        ctx.trace.debug_events.append(DebugTraceEvent(
                            category="recovery",
                            data={
                                "action": recovery_decision.action.value,
                                "reason": recovery_decision.reason,
                                "attempt": state.attempts_used,
                                "added_tools": list(missing),
                                "allowed_tools": list(ctx.allowed_tools),
                            },
                        ))

            lc_tools = self.tool_executor.tools_for_mode(capability=intent_str, profile=None,
                                            allowed_tools=ctx.allowed_tools)
            ctx.trace.tools_bound = sorted(t.name for t in lc_tools)
            ctx.trace.tools_available = sorted(t.name for t in lc_tools)

            if not ctx.model_supports_tools:
                lc_tools = []

            runnable = self._bind_runnable(ctx, lc_tools)

            full_grounding = ctx.grounding_text

            # L2/L3: inject stable_state_text when history truncated (isolation preserved per project_id)
            # Deduped: prefer canonical StableState dict -> to_text, fallback to compacted text
            _stable_text = ""
            try:
                _meta = getattr(ctx, "metadata", {}) or {}
                _st = _meta.get("stable_state")
                _st_text = _meta.get("stable_state_text") or getattr(ctx, "summary", "")
                if isinstance(_st, dict) and _st:
                    from novi.common.execution_state import StableState as _SSPrompt
                    try:
                        _stable_text = _SSPrompt.from_dict(_st).to_text()
                    except Exception:
                        _stable_text = str(_st)[:1200]
                elif isinstance(_st, str) and _st:
                    _stable_text = _st[:1200]
                elif _meta.get("compacted") and _st_text:
                    _stable_text = str(_st_text)[:1200]
            except Exception:
                _stable_text = ""

            base_msgs = [SystemMessage(content=self._system_prompt(
                user_input, intent_str, full_grounding,
                grounding_error=ctx.grounding_error,
                attachments=ctx.attachments, activated_skills=ctx.activated_skills,
                allowed_tools=ctx.allowed_tools, analysis=ctx.analysis, trace=ctx.trace,
                memory_context=ctx.memory_context, project_context=ctx.project_context,
                workspace_context=getattr(ctx, "workspace_context", ""),
                workspace_files=getattr(ctx, "workspace_files_used", None),
                stable_state_text=_stable_text))]
            coord = ctx.retrieval_coordinator
            if coord is not None and coord.budget.max_web_searches > 0:
                base_msgs.append(SystemMessage(
                    content="[Retrieval guidance] Retrieval is in progress. Prefer using "
                            "existing retrieved evidence. Avoid repeated searches unless "
                            "previous evidence is clearly insufficient. Only one web search "
                            f"and one web fetch are allowed."
                ))
            for user, assistant in self.history[-self.max_history:]:
                base_msgs.append(HumanMessage(content=user))
                base_msgs.append(AIMessage(content=assistant))

            if has_images:
                multimodal = self._build_multimodal_content(user_input, attachments)
                base_msgs.append(HumanMessage(content=multimodal))
            else:
                base_msgs.append(HumanMessage(content=user_input))

            if self.stop_event and self.stop_event.is_set():
                self.tracer.finalize(ctx.trace, "stopped")
                return

            # ── Sequential plan execution ─────────────────────────────
            # Runtime is a deterministic executor: it consumes the ordered
            # PlanSteps on ExecutionPlan.plan.steps and runs them one at a
            # time, emitting step/plan lifecycle events. No planning lives
            # here — PlannerEngine owns plan generation.
            plan_steps: list = []
            plan_ref = None
            if ctx.execution_plan is not None:
                plan_ref = ctx.execution_plan.plan
                if plan_ref is not None:
                    plan_steps = list(getattr(plan_ref, "steps", None) or [])

            # Split the plan's model-step budget across sequential steps so
            # total ReAct iterations stay bounded regardless of step count.
            # When resuming, only the remaining steps consume budget.
            resume_from = ctx.resume_from if ctx.resume_from is not None else 0
            remaining_steps = len(plan_steps) - resume_from if plan_steps else 0
            # max_steps = safety rail, not completion boundary — per-segment safety, not failure
            step_budget = ctx.max_steps
            if plan_steps:
                step_budget = max(1, ctx.max_steps // max(1, remaining_steps))

            final = ""
            stop_reason = "completed"

            if self._research_graph is not None and intent_str == "research":
                # Phase 7 Stage 3C: LangGraph research workflow. The graph
                # composes the existing retrieval/evidence pipeline into
                # explicit search→evaluate→synthesize→validate transitions and
                # receives the runnable Novi bound for THIS run.
                #
                # Phase 8A honest step semantics: the whole graph execution is
                # ONE logical plan step. Exactly one STEP_STARTED/STEP_COMPLETED
                # (or STEP_FAILED) pair is emitted; remaining template steps are
                # marked CANCELLED instead of phantom-completed, so
                # Checkpoint.step reflects real progress.
                from ..planner.models import PlanStatus, PlanStepStatus

                exec_idx = self._graph_exec_step_index(ctx, plan_steps)
                graph_step = plan_steps[exec_idx] if plan_steps else None

                if plan_ref is not None:
                    plan_ref.status = PlanStatus.ACTIVE
                    self._emit_bus(EventType.PLAN_STARTED,
                                   task_id=ctx.execution_plan.task_id,
                                   plan_id=plan_ref.id,
                                   step_count=remaining_steps)
                    yield ("plan.started", plan_ref.id,
                           "Research workflow (LangGraph)")
                    if graph_step is not None:
                        graph_step.status = PlanStepStatus.RUNNING
                        self._emit_bus(EventType.STEP_STARTED,
                                       task_id=ctx.execution_plan.task_id,
                                       plan_id=plan_ref.id,
                                       step_id=graph_step.id,
                                       index=exec_idx,
                                       description="Research workflow")
                        yield ("step.started", graph_step.id,
                               "Research workflow")

                state = self._research_graph_state(ctx, runnable, base_msgs, user_input)
                # Live execution: phase/reasoning/token items stream out of
                # the graph as they happen (see _graph_live_stream); the
                # end-of-run buffer replays are gone because every buffered
                # item is now emitted live exactly once.
                seen_kinds: set = set()
                result = yield from self._drain_graph_live(
                    self._research_graph.run, state, seen_kinds)
                final = result.get("answer") or ""
                stop_reason = result.get("completion_reason") or (
                    "completed" if final.strip() else "empty")

                if plan_ref is not None:
                    yield from self._finalize_graph_plan_step(
                        ctx, plan_ref, plan_steps, exec_idx, graph_step,
                        final=final, stop_reason=stop_reason,
                        label="Research workflow")
                    if stop_reason == "completed":
                        plan_ref.status = PlanStatus.COMPLETED
                        self._emit_bus(EventType.PLAN_COMPLETED,
                                       task_id=ctx.execution_plan.task_id,
                                       plan_id=plan_ref.id,
                                       step_count=1)
                        yield ("plan.completed", plan_ref.id,
                               "Completed research workflow")

                if final.strip() and "token" not in seen_kinds:
                    yield ("token", final)
                elif (not final.strip() and "token" not in seen_kinds
                        and stop_reason != "stopped"):
                    yield _empty_run_error("Deep Research")
            elif self._coding_graph is not None and intent_str == "coding":
                # Phase 7 Stage 3D: LangGraph coding workflow. The graph
                # orchestrates implement→verify (bounded re-implement loop);
                # each implement attempt delegates to the runtime's ReAct
                # agent loop (``run_loop``), which routes every tool call
                # through ToolExecutor's permission/risk gate. The loop's
                # stream events are captured and replayed here, so streaming
                # and plan/step lifecycle stay the runtime's contract.
                #
                # Phase 8A honest step semantics: one logical plan step per
                # graph execution (see research branch comment).
                from ..planner.models import PlanStatus, PlanStepStatus

                exec_idx = self._graph_exec_step_index(ctx, plan_steps)
                graph_step = plan_steps[exec_idx] if plan_steps else None

                if plan_ref is not None:
                    plan_ref.status = PlanStatus.ACTIVE
                    self._emit_bus(EventType.PLAN_STARTED,
                                   task_id=ctx.execution_plan.task_id,
                                   plan_id=plan_ref.id,
                                   step_count=remaining_steps)
                    yield ("plan.started", plan_ref.id,
                           "Coding workflow (LangGraph)")
                    if graph_step is not None:
                        graph_step.status = PlanStepStatus.RUNNING
                        self._emit_bus(EventType.STEP_STARTED,
                                       task_id=ctx.execution_plan.task_id,
                                       plan_id=plan_ref.id,
                                       step_id=graph_step.id,
                                       index=exec_idx,
                                       description="Coding workflow")
                        yield ("step.started", graph_step.id,
                               "Coding workflow")

                state = self._coding_graph_state(
                    ctx, runnable, base_msgs, user_input, step_budget)
                # Live execution: the inner ReAct loop's thinking/tool/token
                # chunks and the graph's phase markers stream out as they
                # happen; nothing is buffered for an end-of-run replay.
                seen_kinds: set = set()
                result = yield from self._drain_graph_live(
                    self._coding_graph.run, state, seen_kinds)
                final = result.get("answer") or ""
                stop_reason = result.get("completion_reason") or (
                    result.get("stop_reason") or (
                        "completed" if final.strip() else "empty"))

                if plan_ref is not None:
                    yield from self._finalize_graph_plan_step(
                        ctx, plan_ref, plan_steps, exec_idx, graph_step,
                        final=final, stop_reason=stop_reason,
                        label="Coding workflow")
                    if stop_reason == "completed":
                        plan_ref.status = PlanStatus.COMPLETED
                        self._emit_bus(EventType.PLAN_COMPLETED,
                                       task_id=ctx.execution_plan.task_id,
                                       plan_id=plan_ref.id,
                                       step_count=1)
                        yield ("plan.completed", plan_ref.id,
                               "Completed coding workflow")

                if final.strip() and "token" not in seen_kinds:
                    yield ("token", final)
                elif (not final.strip() and "token" not in seen_kinds
                        and stop_reason != "stopped"):
                    yield _empty_run_error("The coding workflow")
            elif (self._runtime_graph is not None
                  and self._workflow_engine == "langgraph"):
                # Dual-path migration: general runtime workflow
                # (analyze → retrieve → reason → act → reflect → answer).
                # Same one-logical-step plan semantics as the research/coding
                # graphs; tool execution stays behind ToolExecutor; model is
                # the already-bound runnable; exceptions (including
                # ModelUnavailableError) propagate exactly like the legacy
                # loop into this generator's handler.
                from ..planner.models import PlanStatus, PlanStepStatus

                exec_idx = self._graph_exec_step_index(ctx, plan_steps)
                graph_step = plan_steps[exec_idx] if plan_steps else None

                if plan_ref is not None:
                    plan_ref.status = PlanStatus.ACTIVE
                    self._emit_bus(EventType.PLAN_STARTED,
                                   task_id=ctx.execution_plan.task_id,
                                   plan_id=plan_ref.id,
                                   step_count=remaining_steps)
                    yield ("plan.started", plan_ref.id,
                           "Runtime workflow (LangGraph)")
                    if graph_step is not None:
                        graph_step.status = PlanStepStatus.RUNNING
                        self._emit_bus(EventType.STEP_STARTED,
                                       task_id=ctx.execution_plan.task_id,
                                       plan_id=plan_ref.id,
                                       step_id=graph_step.id,
                                       index=exec_idx,
                                       description="Runtime workflow")
                        yield ("step.started", graph_step.id,
                               "Runtime workflow")

                state = self._runtime_graph_state(ctx, runnable, base_msgs, user_input)
                # Live execution: reasoning/token/thinking/tool_call/
                # tool_result/phase items stream out of the graph nodes as
                # they happen (analyze → retrieve → reason → act rounds), so
                # the WebUI observes the run in real time instead of
                # receiving one post-hoc burst.
                seen_kinds: set = set()
                result = yield from self._drain_graph_live(
                    self._runtime_graph.run, state, seen_kinds)
                final = result.get("answer") or ""
                stop_reason = result.get("completion_reason") or (
                    "completed" if final.strip() else "empty")

                if plan_ref is not None:
                    yield from self._finalize_graph_plan_step(
                        ctx, plan_ref, plan_steps, exec_idx, graph_step,
                        final=final, stop_reason=stop_reason,
                        label="Runtime workflow")
                    if stop_reason == "completed":
                        plan_ref.status = PlanStatus.COMPLETED
                        self._emit_bus(EventType.PLAN_COMPLETED,
                                       task_id=ctx.execution_plan.task_id,
                                       plan_id=plan_ref.id,
                                       step_count=1)
                        yield ("plan.completed", plan_ref.id,
                               "Completed runtime workflow")

                if final.strip() and "token" not in seen_kinds:
                    yield ("token", final)
                elif (not final.strip() and "token" not in seen_kinds
                        and stop_reason != "stopped"):
                    yield _empty_run_error("The workflow")
            elif plan_steps:
                from ..planner.models import PlanStatus, PlanStepStatus

                plan_ref.status = PlanStatus.ACTIVE
                self._emit_bus(EventType.PLAN_STARTED,
                               task_id=ctx.execution_plan.task_id,
                               plan_id=plan_ref.id,
                               step_count=remaining_steps)
                yield ("plan.started", plan_ref.id,
                       f"Executing {remaining_steps} step(s) (resuming at {resume_from})" if resume_from else f"Executing {remaining_steps} step(s)")

                step_finals: list[str] = []
                plan_failed = False

                # Steps before resume_from were completed in a prior attempt —
                # mark them completed without re-executing, and seed the final
                # result buffer so step indexes stay globally correct.
                for idx, plan_step in enumerate(plan_steps[:resume_from]):
                    plan_step.status = PlanStepStatus.COMPLETED
                    step_finals.append(getattr(plan_step, "result", "") or "")

                for idx, plan_step in enumerate(plan_steps):
                    if idx < resume_from:
                        continue
                    plan_step.status = PlanStepStatus.RUNNING
                    self._emit_bus(EventType.STEP_STARTED,
                                   task_id=ctx.execution_plan.task_id,
                                   plan_id=plan_ref.id,
                                   step_id=plan_step.id,
                                   index=idx,
                                   description=plan_step.description)
                    yield ("step.started", plan_step.id, plan_step.description)

                    step_final = ""
                    step_ok = True
                    step_reason = "completed"
                    step_tools: list[dict] = []
                    # Phase 9C: the sequential planned-step path drives the
                    # generic single-attempt ReAct executor directly.
                    for chunk in run_react_attempt(
                            ctx=ctx,
                            runnable=runnable,
                            tool_executor=self.tool_executor,
                            tracer=self.tracer,
                            retrieval_executor=self.retrieval_executor,
                            capability_registry=self._capability_registry,
                            scan_skills=self._scan_skills,
                            skill_block=self._skill_block,
                            bind_runnable=self._bind_runnable,
                            stop_probe=self._stop_probe(),
                            event_bus=self.event_bus,
                            debug_trace=self.debug_trace,
                            step_budget=step_budget,
                            base_msgs=base_msgs,
                            step=plan_step,
                            step_index_base=len(ctx.trace.steps)):
                        if chunk[0] == _LOOP_DONE:
                            step_final, step_reason, step_ok = chunk[1], chunk[2], chunk[3]
                        else:
                            if chunk[0] == "tool_call" and len(chunk) >= 3:
                                step_tools.append(("tool_call", chunk))
                            elif chunk[0] == "tool_result" and len(chunk) >= 4:
                                step_tools.append(("tool_result", chunk))
                            yield chunk
                            if chunk[0] == "tool_result":
                                self._maybe_compact_and_checkpoint(ctx)

                    tools_payload = self._build_step_tool_payload(step_tools)
                    if step_ok:
                        plan_step.status = PlanStepStatus.COMPLETED
                        self._emit_bus(EventType.STEP_COMPLETED,
                                       task_id=ctx.execution_plan.task_id,
                                       plan_id=plan_ref.id,
                                       step_id=plan_step.id,
                                       index=idx,
                                       result=step_final[:2000],
                                       tools=tools_payload)
                        yield ("step.completed", plan_step.id, step_final[:2000])
                        step_finals.append(step_final)
                    else:
                        plan_step.status = PlanStepStatus.FAILED
                        self._emit_bus(EventType.STEP_FAILED,
                                       task_id=ctx.execution_plan.task_id,
                                       plan_id=plan_ref.id,
                                       step_id=plan_step.id,
                                       index=idx,
                                       error=step_final[:2000])
                        yield ("step.failed", plan_step.id, step_final[:2000])
                        plan_ref.status = PlanStatus.FAILED
                        self._emit_bus(EventType.PLAN_FAILED,
                                       task_id=ctx.execution_plan.task_id,
                                       plan_id=plan_ref.id,
                                       step_id=plan_step.id,
                                       error=step_final[:2000])
                        yield ("plan.failed", plan_ref.id, step_final[:2000])
                        stop_reason = step_reason
                        final = step_final
                        plan_failed = True
                        break

                if not plan_failed:
                    plan_ref.status = PlanStatus.COMPLETED
                    self._emit_bus(EventType.PLAN_COMPLETED,
                                   task_id=ctx.execution_plan.task_id,
                                   plan_id=plan_ref.id,
                                   step_count=len(step_finals))
                    yield ("plan.completed", plan_ref.id, f"Completed {len(step_finals)} step(s)")
                    final = "\n\n".join(s for s in step_finals if s.strip())
                    stop_reason = "completed"
                    if not final.strip():
                        stop_reason = "empty"
            else:
                # Backward-compatible unplanned path. When no ExecutionPlan
                # (or a plan without steps) is supplied, the runtime runs a
                # single unplanned ReAct loop. This intentionally bypasses the
                # plan/step lifecycle events — they belong to planned execution.
                # Standalone/direct runs (no orchestrator) rely on this path.
                # Phase 9C: drives the generic executor directly.
                for chunk in run_react_attempt(
                        ctx=ctx,
                        runnable=runnable,
                        tool_executor=self.tool_executor,
                        tracer=self.tracer,
                        retrieval_executor=self.retrieval_executor,
                        capability_registry=self._capability_registry,
                        scan_skills=self._scan_skills,
                        skill_block=self._skill_block,
                        bind_runnable=self._bind_runnable,
                        stop_probe=self._stop_probe(),
                        event_bus=self.event_bus,
                        debug_trace=self.debug_trace,
                        step_budget=step_budget,
                        base_msgs=base_msgs,
                        step=None,
                        step_index_base=0):
                    if chunk[0] == _LOOP_DONE:
                        final, stop_reason, _ = chunk[1], chunk[2], chunk[3]
                    else:
                        yield chunk
                        if chunk[0] == "tool_result":
                            self._maybe_compact_and_checkpoint(ctx)

            if self.stop_event and self.stop_event.is_set():
                self.tracer.finalize(ctx.trace, "stopped")
                return

            ctx.trace.final_response_length = len(final)
            rc = ctx.retrieval_coordinator
            if rc is not None:
                ctx.trace.retrieval_search_count = rc.budget.searches_used
                ctx.trace.retrieval_fetch_count = rc.budget.fetches_used
                ctx.trace.retrieval_budget_exhausted = rc.budget.is_exhausted
            self.tracer.finalize(ctx.trace, stop_reason)

            self._remember(user_input, final, conversation_id=ctx.conversation_id)

        except Exception as e:
            self.tracer.finalize(ctx.trace, "error")
            msg = f"I hit an error: {e}"
            yield ("token", msg)
            self._remember(user_input, msg, conversation_id=ctx.conversation_id)
    def _build_step_tool_payload(self, step_tools: list[tuple[str, tuple]]) -> list[dict]:
        """Pair tool_call/tool_result chunks into redacted checkpoint records.

        ``step_tools`` is the ordered list of ``("tool_call", chunk)`` /
        ``("tool_result", chunk)`` pairs captured from the step's agent loop.
        Tool calls and results interleave in order (one result per call), so
        pairing by position keeps name/args attached to their result.

        Redaction happens here (runtime side) so anything reaching the
        composition root's ``STEP_COMPLETED`` event — and from there
        ``Checkpoint.tool_states`` — is already safe. Never returns None.
        """
        from .execution_redaction import build_tool_record

        calls: list[dict] = []
        pending: list[dict] = []
        for kind, chunk in step_tools:
            if kind == "tool_call":
                pending.append({"name": chunk[1], "args": chunk[2]})
            elif kind == "tool_result" and pending:
                call = pending.pop(0)
                calls.append(build_tool_record(
                    call["name"], call["args"], chunk[2],
                    success=not str(chunk[2]).startswith("Error"),
                ))
        return calls
    def _emit_bus(self, event_type, **data):
        """Publish a lifecycle event to the runtime event bus, if any."""
        if self.event_bus is None:
            return
        try:
            self.event_bus.emit(event_type, **data)
        except Exception:
            pass

    @staticmethod
    def _graph_exec_step_index(ctx, plan_steps: list) -> int:
        """Index of the ONE plan step representing graph execution.

        Honors ``resume_from`` (Checkpoint.step contract) but never exceeds
        the last step.
        """
        if not plan_steps:
            return 0
        return min(ctx.resume_from or 0, len(plan_steps) - 1)

    def _graph_live_stream(self, run, state: dict):
        """Run a LangGraph workflow with LIVE event forwarding.

        The graph executes in a worker thread; every ``emit_event`` the nodes
        push lands on a queue this generator drains and yields immediately.
        The caller observes tokens / phases / tool activity DURING execution
        instead of replaying buffered lists after the graph returns.

        Yields the raw stream tuples; ``return``s (StopIteration.value) the
        graph's final state. Contract for callers:

        * Every item yielded here was ALSO appended to the corresponding
          state buffer (``events`` / ``stream_events``) by the node that
          produced it — so after completion the caller replays only
          ``buffer[live_count:]`` to preserve exactly-once delivery even when
          the thread appended items between the last queue drain and exit.
        * Exceptions from the graph propagate unchanged (ModelUnavailableError
          et al.), preserving the legacy error contract.
        """
        import queue as _queue

        q: _queue.SimpleQueue = _queue.SimpleQueue()
        state["emit"] = q.put
        holder: dict = {}

        def _work():
            try:
                holder["result"] = run(state)
            except BaseException as e:  # noqa: BLE001 - re-raised below
                holder["error"] = e

        t = threading.Thread(target=_work, daemon=True,
                             name="novi-graph-live")
        t.start()
        while True:
            try:
                item = q.get(timeout=0.05)
            except _queue.Empty:
                if not t.is_alive():
                    break
                continue
            yield item
        # Thread exited: flush whatever landed after the final drain window.
        while True:
            try:
                item = q.get_nowait()
            except _queue.Empty:
                break
            yield item
        if "error" in holder:
            raise holder["error"]
        return holder.get("result") or {}

    def _drain_graph_live(self, run, state: dict, seen_kinds: set) -> dict:
        """Consume :meth:`_graph_live_stream` inside a plain generator body.

        Yields every live item upward while recording which item kinds were
        seen (so callers can suppress redundant post-hoc replays such as the
        fallback whole-answer token). Returns the graph's final state via
        StopIteration.value (use with ``yield from``).
        """
        gen = self._graph_live_stream(run, state)
        while True:
            try:
                item = next(gen)
            except StopIteration as st:
                return st.value or {}
            if isinstance(item, tuple) and item:
                seen_kinds.add(item[0])
            yield item

    def _finalize_graph_plan_step(self, ctx, plan_ref, plan_steps, exec_idx,
                                  graph_step, *, final: str, stop_reason: str,
                                  label: str):
        """Close out the single logical plan step a graph-backed run maps to.

        Honest semantics (Phase 8A):
          * completed  → exactly one STEP_COMPLETED; remaining template steps
            are marked CANCELLED (the workflow subsumed them), never
            phantom-completed. Caller emits the terminal PLAN_COMPLETED.
          * stopped    → no terminal step/plan events at all — mirrors the
            sequential path's user-stop behavior (plan stays ACTIVE; startup
            recovery / ContinuationService own what happens next).
          * otherwise  → STEP_FAILED + PLAN_FAILED, matching the sequential
            path's per-step failure handling.
        """
        from ..planner.models import PlanStatus, PlanStepStatus

        if graph_step is None or not plan_steps:
            return
        task_id = ctx.execution_plan.task_id
        plan_id = plan_ref.id

        if stop_reason == "stopped":
            return

        # Phase 8F partial-completion honesty: a step only counts as
        # COMPLETED when the workflow's own terminal reason says so. A
        # non-empty answer alongside environment_error / permission_denied /
        # verification_failed is a FAILED step carrying partial output —
        # never a phantom completion.
        if stop_reason == "completed" and final.strip():
            graph_step.status = PlanStepStatus.COMPLETED
            graph_step.result = final[:2000]
            self._emit_bus(EventType.STEP_COMPLETED,
                           task_id=task_id, plan_id=plan_id,
                           step_id=graph_step.id, index=exec_idx,
                           result=final[:2000], tools=[])
            yield ("step.completed", graph_step.id, final[:2000])
            for i, s in enumerate(plan_steps):
                if i == exec_idx:
                    continue
                if s.status in (PlanStepStatus.PENDING,
                                PlanStepStatus.RUNNING,
                                PlanStepStatus.IN_PROGRESS):
                    s.status = PlanStepStatus.CANCELLED
            return

        # Empty answer / error / max_steps: honest failure, like the
        # sequential path's failed step.
        graph_step.status = PlanStepStatus.FAILED
        error_text = f"{label} did not complete ({stop_reason})"
        self._emit_bus(EventType.STEP_FAILED,
                       task_id=task_id, plan_id=plan_id,
                       step_id=graph_step.id, index=exec_idx,
                       error=error_text)
        yield ("step.failed", graph_step.id, error_text)
        plan_ref.status = PlanStatus.FAILED
        self._emit_bus(EventType.PLAN_FAILED,
                       task_id=task_id, plan_id=plan_id,
                       step_id=graph_step.id, error=error_text)
        yield ("plan.failed", plan_id, error_text)

    def _research_graph_state(self, ctx, runnable, base_msgs, user_input):
        """Build the per-run state handed to the research graph.

        The graph receives the ALREADY-resolved, ALREADY-bound runnable
        (``state["model"]``), the runtime's system prompt, and per-run
        search/coordinator collaborators so it composes the runtime's own
        retrieval executor and budget authority.
        """
        graph = self._research_graph

        def search(query: str):
            return self.retrieval_executor.execute_search(query, trace=ctx.trace)

        return {
            "user_input": user_input,
            "analysis": ctx.analysis,
            "retrieval_plan": ctx.retrieval_plan,
            "grounding_text": ctx.grounding_text,
            "quality": ctx.grounding_quality or "",
            "query": user_input,
            "search_attempts": 0,
            "max_search_attempts": graph.max_search_attempts,
            "system_prompt": base_msgs[0].content if base_msgs else "",
            "plan_step_index": ctx.resume_from or 0,
            "model": runnable,
            "search": search,
            "coordinator": ctx.retrieval_coordinator,
            # Phase 8A cancellation seam: the runtime's stop signal, probed by
            # the graph at node boundaries. The control signal stays
            # runtime-owned; graphs never decide to stop on their own.
            "should_stop": self._stop_probe(),
        }

    def _coding_graph_state(self, ctx, runnable, base_msgs, user_input, step_budget):
        """Build the per-run state handed to the coding graph.

        The graph receives the ALREADY-resolved, ALREADY-bound runnable
        (``state["model"]``), a ``run_loop`` callable wrapping the runtime's
        ReAct agent loop for one implement attempt, and (Phase 8C) a
        ``verify`` collaborator that routes verification commands through
        ToolExecutor.execute() — the graph decides WHEN to verify, the
        executor decides WHAT may run.

        Repair attempts receive ``state["repair_context"]`` (bounded
        verification failure feedback) appended to their prompt so attempt
        N+1 never repeats attempt N blind.
        """

        # Phase 8F cross-attempt dedup: mutating calls (write/edit) with
        # byte-identical arguments are pointless on a repair attempt — the
        # first attempt's result is already known. Reads and commands stay
        # repeatable: their inputs/state legitimately change between attempts.
        prior_sigs: set[str] = set()
        _MUTATING_TOOLS = ("write_file", "edit_file")

        def run_loop(state):
            nonlocal prior_sigs
            from ..graphs.state import emit_event as _emit_live

            events = []
            final, reason, ok = "", "completed", True
            msgs = list(base_msgs)
            feedback = state.get("repair_context") or ""
            if feedback:
                msgs.append(SystemMessage(
                    content="VERIFICATION FEEDBACK from your previous "
                            f"attempt:\n{feedback}"))
            # Phase 9B/9C: implement attempts execute through the generic
            # single-attempt ReAct executor DIRECTLY — the historical
            # _run_agent_loop entry point was retired in Phase 9C and the
            # executor is the sole generic ReAct loop. Collaborator wiring is
            # identical to the sequential/unplanned call sites in run_stream,
            # so event tuples, dedup/cancellation/recovery semantics are
            # unchanged.
            for chunk in run_react_attempt(
                    ctx=ctx,
                    runnable=runnable,
                    tool_executor=self.tool_executor,
                    tracer=self.tracer,
                    retrieval_executor=self.retrieval_executor,
                    capability_registry=self._capability_registry,
                    scan_skills=self._scan_skills,
                    skill_block=self._skill_block,
                    bind_runnable=self._bind_runnable,
                    stop_probe=self._stop_probe(),
                    event_bus=self.event_bus,
                    debug_trace=self.debug_trace,
                    step_budget=step_budget,
                    base_msgs=msgs,
                    step=None,
                    step_index_base=0,
                    seed_seen=prior_sigs):
                events.append(chunk)
                # Live channel: every UI-visible chunk streams out while the
                # attempt runs. _LOOP_DONE stays internal to this wrapper.
                if chunk[0] != _LOOP_DONE:
                    _emit_live(state, chunk)
                if chunk[0] == _LOOP_DONE:
                    final, reason, ok = chunk[1], chunk[2], chunk[3]
            for ev in events:
                if (isinstance(ev, tuple) and len(ev) > 2
                        and ev[0] == "tool_call" and ev[1] in _MUTATING_TOOLS):
                    try:
                        prior_sigs.add(
                            f"{ev[1]}:{json.dumps(ev[2], sort_keys=True, default=str)}")
                    except Exception:
                        pass
            return events, final, reason, ok

        def verify(state):
            from ..graphs.coding_intel import report_from_tool_result

            reports = []
            for command in self._verification_commands():
                result = self.tool_executor.execute("run_command",
                                                    {"command": command})
                reports.append(report_from_tool_result(result, command=command))
                if not reports[-1].passed:
                    break  # first failure is enough context for repair
            return reports

        return {
            "user_input": user_input,
            "analysis": ctx.analysis,
            "retrieval_plan": ctx.retrieval_plan,
            "system_prompt": base_msgs[0].content if base_msgs else "",
            "plan_step_index": ctx.resume_from or 0,
            "model": runnable,
            "run_loop": run_loop,
            "verify": verify,
            # Phase 8A cancellation seam (see _research_graph_state).
            "should_stop": self._stop_probe(),
        }

    def _verification_commands(self) -> list[str]:
        """Verification commands for coding runs (runtime-owned config read).

        Explicit ``coding.verify_commands`` configuration wins. Otherwise a
        conservative default: run pytest ONLY when this workspace actually
        looks like a pytest project (tests dir or pytest config present) —
        explaining code must never trigger a test suite, and non-python
        workspaces stay untouched.
        """
        coding_cfg = self.cfg.get("coding", {}) or {}
        configured = coding_cfg.get("verify_commands")
        if isinstance(configured, list):
            return [str(c) for c in configured if str(c).strip()][:4]
        root = Path.cwd()
        looks_like_pytest = any(
            (root / name).exists()
            for name in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
        ) or (root / "tests").is_dir()
        if looks_like_pytest:
            # Resolve THIS interpreter explicitly so verification runs against
            # the same environment Novi runs in, not whichever `python` is
            # first on PATH.
            exe = sys.executable.replace("\\", "/")
            if " " in exe:
                exe = f'"{exe}"'
            return [f"{exe} -m pytest -q"]
        return []

    def _runtime_graph_state(self, ctx, runnable, base_msgs, user_input):
        """Build the per-run state handed to the general runtime workflow.

        Injection boundary (identical philosophy to the research/coding
        states): the graph receives the ALREADY-bound runnable, the already-
        computed analysis, a context SNAPSHOT callable (retrieval ran upstream
        through RetrievalExecutor/UnifiedRetriever/evidence — the graph never
        retrieves), and an execute_tool callable that routes every call
        through ToolExecutor with this run's coordinator/trace. Reflection is
        opt-in via workflow.reflect_on_run; default off preserves the current
        observe-per-turn semantics (Brain.observe still runs in the shared
        run_stream tail).
        """
        rt_cfg = self.cfg.get("runtime", {}) if isinstance(self.cfg, dict) else {}

        def prepare_context():
            return {
                "grounding_text": ctx.grounding_text or "",
                "memory_context": ctx.memory_context or "",
                "project_context": ctx.project_context or "",
                "evidence_context": ctx.evidence_context,
                "quality": ctx.grounding_quality or "",
            }

        def execute_tool(name: str, args: dict, step_idx: int):
            result = self.tool_executor.execute(
                name, args,
                coordinator=ctx.retrieval_coordinator,
                step_idx=step_idx, trace=ctx.trace,
            )
            return result.output, result.diff, result.success

        reflect = None
        brain_ref = self.brain
        if brain_ref is not None and rt_cfg.get("workflow.reflect_on_run"):
            def reflect():
                try:
                    return brain_ref.reflect(on_demand=True)
                except Exception:
                    return None

        seed_messages = list(base_msgs[1:]) if base_msgs else []

        return {
            "user_input": user_input,
            "analysis": ctx.analysis,
            "grounding_text": ctx.grounding_text or "",
            "memory_context": ctx.memory_context or "",
            "project_context": ctx.project_context or "",
            "evidence_context": ctx.evidence_context,
            "quality": ctx.grounding_quality or "",
            "system_prompt": base_msgs[0].content if base_msgs else "",
            "seed_messages": seed_messages,
            "messages": [],
            "attempt": 0,
            "max_steps": ctx.max_steps,
            "model": runnable,
            "analyze": None,  # analysis is precomputed upstream by executor/orchestrator
            "prepare_context": prepare_context,
            "execute_tool": execute_tool,
            "reflect": reflect,
            # Phase 8A cancellation seam (see _research_graph_state).
            "should_stop": self._stop_probe(),
        }

    def _stop_probe(self):
        """Runtime-owned cancellation probe for graph node boundaries."""

        def probe() -> bool:
            return bool(self.stop_event and self.stop_event.is_set())

        return probe

    def _bind_runnable(self, ctx, tools, temperature: float | None = None):
        """Single runtime binding seam.

        Constructs the LangChain runnable for the ALREADY-resolved
        ``ctx.model_name`` through ``ModelService → ModelRuntime → providers``.

        Recovery/escalation logic must call this instead of reconstructing
        LangChain runnables inline. When ``tools`` is non-empty the model is
        bound to them (``bind_model``); otherwise a plain chat client is built
        (``client_for_model``). ``ctx.model_name`` was resolved verbatim from
        ``llm.workloads.<workload>.model`` before this point — no selection,
        substitution, or fallback happens here.
        """
        temp = ctx.temperature if temperature is None else temperature
        mm = self.model_service
        if tools:
            return mm.bind_model(ctx.model_name, tools, temperature=temp)
        return mm.client_for_model(ctx.model_name, temperature=temp)

    def run(self, user_input: str, attachments: list[dict] | None = None) -> str:
        chunks = []
        for kind, text in self.run_stream(user_input, attachments):
            if kind == "token":
                chunks.append(text)
        return "".join(chunks).strip()
    def _remember(self, user_input: str, final: str, conversation_id: str | None = None):
        self.history.append((user_input, final))
        if len(self.history) > self.max_history:
            self._compact()
        if self.brain is not None:
            try:
                self.brain.observe(Turn(
                    user=user_input,
                    assistant=final,
                    conversation_id=conversation_id or None,
                ))
            except Exception:
                pass
    def _maybe_compact_and_checkpoint(self, ctx) -> None:
        """Mid-loop ContextManager check: compact + stable persist BEFORE next model call.

        L3: ctx.metadata["stable_state"] is canonical for Checkpoint.stable
        (see pre-check contract). Checks should_compact, compacts history
        when compact/emergency, persists stable dict to trace/metadata,
        handles overflow via ContextManager.compact_history's internal budget
        re-check.
        """
        try:
            from .context_manager import ContextManager
            from novi.common.execution_state import StableState

            cm = ContextManager(model_name=ctx.model_name, simple_llm=self.simple_llm)
            lvl = cm.should_compact(ctx)
            if lvl in ("compact", "emergency"):
                cm.compact_history(ctx)
                try:
                    st = StableState.from_context(ctx)
                    ctx.metadata["stable_state"] = st.to_dict()
                    if ctx.trace is not None:
                        if not hasattr(ctx.trace, "metadata"):
                            ctx.trace.metadata = {}  # type: ignore[attr-defined]
                        ctx.trace.metadata["context_compacted"] = lvl  # type: ignore
                        ctx.trace.metadata["stable_state"] = ctx.metadata["stable_state"]  # type: ignore
                except Exception:
                    pass
        except Exception:
            pass

    def _compact(self):
        keep = self.max_history // 2
        old, self.history = self.history[:-keep], self.history[-keep:]
        text = "\n".join(f"User: {u}\nNovi: {a}" for u, a in old)
        if self._summary:
            text = f"Earlier context:\n{self._summary}\n\n{text}"
        try:
            summary = self.simple_llm.invoke(_COMPACT_PROMPT.format(text=text))
            if summary and not summary.lower().startswith("error"):
                self._summary = summary.strip()
        except Exception as e:
            log.warning("history compaction failed: %s", e)
    def reset(self):
        self.history.clear()
        self._summary = ""
