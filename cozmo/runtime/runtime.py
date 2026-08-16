"""
CozmoRuntime — native tool-calling agentic loop.

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
import threading
import time
from datetime import datetime
from pathlib import Path
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from ..orchestrator.intent import classify_intent
from .model_selector import ModelSelector, model_capabilities
from .trace import DebugTraceEvent, ExecutionTrace, StepTrace, TraceAction
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

log = logging.getLogger("cozmo.runtime")

SKILLS_DIR = Path.home() / ".cozmo" / "skills"

_TOOL_CATEGORIES: dict[str, str] = {
    "read": "workspace",
    "read_file": "workspace",
    "write_file": "workspace",
    "edit_file": "workspace",
    "glob": "workspace",
    "glob_search": "workspace",
    "grep": "workspace",
    "grep_search": "workspace",
    "list_directory": "workspace",
    "diagnostics": "workspace",
    "sourcegraph": "workspace",
    "bash": "python",
    "run_command": "python",
    "execute_python": "python",
    "calculator": "python",
    "web_search": "web",
    "web_search_pipeline": "web",
    "web_fetch": "web",
    "fetch_url": "web",
    "webfetch": "web",
    "git_diff": "git",
    "git_log": "git",
    "read_knowledge": "memory",
    "search_knowledge": "memory",
    "write_knowledge": "memory",
    "schedule_task": "memory",
    "list_schedules": "memory",
    "remove_schedule": "memory",
    "screenshot": "workspace",
    "analyze_image": "workspace",
    "clipboard_read": "workspace",
    "telegram_send": "other",
}

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

# Sentinel emitted by CozmoRuntime._run_agent_loop carrying the loop outcome.
# Payload: (_LOOP_DONE, final_text, stop_reason, success)
_LOOP_DONE = "__plan_step_done__"

_IDENTITY = (
    "You are Cozmo, a capable local AI assistant running entirely on-device via Ollama. "
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

class CozmoRuntime:
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
                       project_context: str = "") -> str:
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
        if memory_context:
            parts.append(f"\nRelevant memory from past sessions:{memory_context}")
        lessons = self.lesson_store.get_context(tool_names=allowed_tools if allowed_tools else None)
        if lessons:
            parts.append(lessons)
        if project_context:
            parts.append(f"\nRelevant project context:\n{project_context}")
        if getattr(self, '_project_context', None):
            parts.append(f"\nProject context:\n{self._project_context}")
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
                   force_mode: str | None = None,
                   force_capability: str | None = None,
                   force_model: str | None = None,
                   execution_plan: object | None = None,
                   context: ExecutionContext | None = None,
                   conversation_id: str | None = None,
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
            elif force_mode is not None:
                log.warning("force_mode='%s' is deprecated. Use force_capability / force_model.", force_mode)
                cap_name = ctx.force_capability or force_mode
                ctx.allowed_tools = self._capability_registry.get_tool_names(
                    self._intent_cap_ids.get(cap_name, ["conversation"]))
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

            mm = self.model_service
            runnable = (mm.bind_model(ctx.model_name, lc_tools, temperature=ctx.temperature)
                        if lc_tools else mm.client_for_model(ctx.model_name, ctx.temperature))

            full_grounding = ctx.grounding_text

            base_msgs = [SystemMessage(content=self._system_prompt(
                user_input, intent_str, full_grounding,
                grounding_error=ctx.grounding_error,
                attachments=ctx.attachments, activated_skills=ctx.activated_skills,
                allowed_tools=ctx.allowed_tools, analysis=ctx.analysis, trace=ctx.trace,
                memory_context=ctx.memory_context, project_context=ctx.project_context))]
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
            step_budget = ctx.max_steps
            if plan_steps:
                step_budget = max(1, ctx.max_steps // max(1, remaining_steps))

            final = ""
            stop_reason = "completed"

            if plan_steps:
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
                    for chunk in self._run_agent_loop(
                            ctx, mm, runnable, intent_str, step_budget, base_msgs,
                            step=plan_step, step_index_base=len(ctx.trace.steps)):
                        if chunk[0] == _LOOP_DONE:
                            step_final, step_reason, step_ok = chunk[1], chunk[2], chunk[3]
                        else:
                            if chunk[0] == "tool_call" and len(chunk) >= 3:
                                step_tools.append(("tool_call", chunk))
                            elif chunk[0] == "tool_result" and len(chunk) >= 4:
                                step_tools.append(("tool_result", chunk))
                            yield chunk

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
                for chunk in self._run_agent_loop(
                        ctx, mm, runnable, intent_str, step_budget, base_msgs,
                        step=None, step_index_base=0):
                    if chunk[0] == _LOOP_DONE:
                        final, stop_reason, _ = chunk[1], chunk[2], chunk[3]
                    else:
                        yield chunk

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
    def _run_agent_loop(self, ctx, mm, runnable, intent_str, step_budget,
                        base_msgs, step=None, step_index_base=0):
        """Run the ReAct loop for one plan step (or a whole unplanned run).

        Yields the runtime streaming events (token/reasoning/thinking/tool_*)
        exactly as the legacy single loop did. Ends by yielding the
        ``_LOOP_DONE`` sentinel carrying ``(final, stop_reason, success)`` so the
        caller can drive sequential plan steps or terminate on failure.

        ``step`` is an optional :class:`PlanStep`. When given, its objective is
        injected as a trailing system instruction so the model executes that
        specific step. ``step_index_base`` offsets StepTrace indexing so plan
        steps accumulate into one global trace.

        Future checkpoint execution: the loop is index-addressed and
        idempotent per step. A future Job-driven executor (which already owns
        Checkpoint step/messages/tool_states in cozmo/jobs) can resume by
        feeding ``Checkpoint.step`` as ``step_index_base`` and the restored
        messages as ``base_msgs`` — no re-planning and no new planning logic
        needed here. Runtime stays the generic per-step executor.
        """
        msgs = list(base_msgs)
        if step is not None:
            msgs.append(SystemMessage(
                content=f"CURRENT STEP ({step.id}): {step.description}"
            ))
        seen_calls: set[str] = set()
        final = ""
        try:
            for outer_step in range(step_budget):
                idx = step_index_base + outer_step
                acc = None
                content_buf = ""
                step_start = time.time()
                tokens_in_step = 0

                for chunk in runnable.stream(msgs):
                    if self.stop_event and self.stop_event.is_set():
                        self.tracer.finalize(ctx.trace, "stopped")
                        yield (_LOOP_DONE, "", "stopped", False)
                        return
                    acc = chunk if acc is None else acc + chunk
                    reasoning_content = chunk.additional_kwargs.get("reasoning_content", "")
                    if reasoning_content:
                        yield ("reasoning", reasoning_content)
                    piece = chunk.content or ""
                    if piece:
                        content_buf += piece
                        tokens_in_step += 1
                        yield ("token", piece)

                ai = acc if acc is not None else AIMessage(content=content_buf)
                model_ms = round((time.time() - step_start) * 1000, 2)
                while len(ctx.trace.steps) <= idx:
                    ctx.trace.steps.append(StepTrace(step=len(ctx.trace.steps)))
                ctx.trace.steps[idx].model_inference_ms = model_ms
                ctx.trace.steps[idx].tokens_generated = tokens_in_step

                calls = self.tool_executor.extract_calls(ai)

                if not calls:
                    newly = self._scan_skills(content_buf, ctx.activated_skills)
                    if newly:
                        ctx.activated_skills.extend(newly)
                        names = ", ".join(s["name"] for s in newly)
                        yield ("thinking", f"Activating skill: {names}",
                               f"Loading skill instructions: {names}", None)
                        msgs.append(ai if isinstance(ai, AIMessage)
                                    else AIMessage(content=content_buf))
                        for sk in newly:
                            msgs.append(SystemMessage(content=self._skill_block(sk)))
                        continue
                    recovery_decision = self.retrieval_executor.recommend_when_model_answered(ctx)
                    if recovery_decision.action != RecoveryAction.NONE:
                        if recovery_decision.action == RecoveryAction.UPGRADE_SEARCH:
                            search_tools = self._capability_registry.get_tool_names(["search"])
                            ctx.allowed_tools = list(set(ctx.allowed_tools) | set(search_tools))
                            lc_tools = self.tool_executor.tools_for_mode(allowed_tools=ctx.allowed_tools)
                            mm = self.model_service
                            runnable = mm.bind_model(ctx.model_name, lc_tools, temperature=ctx.temperature)
                            msgs.append(SystemMessage(
                                content="[Web search tools (web_search, web_fetch) are now available. "
                                        "Use them if you need current information.]"
                            ))
                            state = self.retrieval_executor.commit_recovery(
                                ctx, recovery_decision, recovery_decision.action.value)
                            self.tracer.debug(ctx.trace, "recovery", {
                                "action": recovery_decision.action.value,
                                "reason": recovery_decision.reason,
                                "attempt": state.attempts_used,
                                "allowed_tools": list(ctx.allowed_tools),
                            })
                        continue
                    final = content_buf.strip()
                    break

                msgs.append(ai if isinstance(ai, AIMessage)
                            else AIMessage(content=content_buf))
                names = ", ".join(c["name"] for c in calls)
                arg_sigs = [json.dumps(c["args"], sort_keys=True, default=str) for c in calls]
                calls_detail = "; ".join(
                    f"{c['name']}({sig[:200]})"
                    for c, sig in zip(calls, arg_sigs)
                )
                yield ("thinking", f"Running: {names}", calls_detail, None)

                for c, args_sig in zip(calls, arg_sigs):
                    if self.stop_event and self.stop_event.is_set():
                        self.tracer.finalize(ctx.trace, "stopped")
                        yield (_LOOP_DONE, "", "stopped", False)
                        return
                    sig = f"{c['name']}:{args_sig}"
                    call_id = f"call-{idx}-{c['name']}"
                    yield ("tool_call", c["name"], c["args"], call_id, self.tool_executor.tool_category(c["name"]))
                    if self.event_bus:
                        try:
                            self.event_bus.emit("tool_called", tool=c["name"], args=c["args"], step=idx)
                        except Exception:
                            pass
                    if sig in seen_calls:
                        out = (f"Error: you already made this exact {c['name']} call "
                               f"and have its result above. Use it, or try a "
                               f"DIFFERENT call — do not repeat yourself.")
                        tool_success = False
                        tool_t0 = time.time()
                        diff = self.tool_executor.compute_diff(c["name"], c["args"])
                        tool_ms = round((time.time() - tool_t0) * 1000, 2)
                        self.tracer.record_tool(
                            step_idx=idx, name=c["name"], args=c["args"],
                            result=out, latency_ms=tool_ms, success=tool_success,
                            error=out if out.startswith("Error") else None,
                            trace=ctx.trace,
                        )
                    else:
                        seen_calls.add(sig)
                        result = self.tool_executor.execute(
                            c["name"], c["args"],
                            coordinator=ctx.retrieval_coordinator,
                            step_idx=idx, trace=ctx.trace,
                        )
                        out = result.output
                        tool_success = result.success
                        diff = result.diff
                    yield ("tool_result", c["name"], out, call_id, diff)
                    if self.event_bus:
                        try:
                            self.event_bus.emit("tool_result", tool=c["name"], call_id=call_id,
                                                is_error=out.startswith("Error"))
                        except Exception:
                            pass
                    msgs.append(ToolMessage(content=out, tool_call_id=c["id"]))

                    # Post-tool recovery: executor detects empty knowledge result → escalate to web
                    recovery_decision = self.retrieval_executor.recommend_after_tool(ctx, c["name"], out)
                    if (recovery_decision.action == RecoveryAction.ESCALATE_WEB
                            and not any(s in ctx.allowed_tools for s in
                                        self._capability_registry.get_tool_names(["search"]))):
                        search_tools = self._capability_registry.get_tool_names(["search"])
                        ctx.allowed_tools = list(set(ctx.allowed_tools) | set(search_tools))
                        new_lc_tools = self.tool_executor.tools_for_mode(allowed_tools=ctx.allowed_tools)
                        runnable = mm.bind_model(ctx.model_name, new_lc_tools, temperature=ctx.temperature)
                        state = self.retrieval_executor.commit_recovery(ctx, recovery_decision, "post_tool_escalation")
                        msgs.append(SystemMessage(
                            content="[Knowledge base returned no results. Web search tools "
                                    "(web_search, web_fetch) are now available. Use them to find "
                                    "current information.]"
                        ))
                        if self.debug_trace and ctx.trace is not None:
                            ctx.trace.debug_events.append(DebugTraceEvent(
                                category="recovery",
                                data={
                                    "action": "post_tool_escalation",
                                    "reason": recovery_decision.reason,
                                    "attempt": state.attempts_used,
                                    "step": idx,
                                    "tool": c["name"],
                                },
                            ))

                    if self.stop_event and self.stop_event.is_set():
                        self.tracer.finalize(ctx.trace, "stopped")
                        yield (_LOOP_DONE, "", "stopped", False)
                        return
                yield ("thinking", "Thinking...", "Processing tool results and forming response", None)
            else:
                final = ("I ran out of steps before finishing. Here's where I "
                         "got to — ask me to continue if you want me to keep going.")
                yield ("token", final)

            if not final:
                final = "(no response — the model returned empty output; try rephrasing)"
                yield ("token", final)

            stop_reason = "completed"
            if not final.strip():
                stop_reason = "empty"
            elif "ran out of steps" in final:
                stop_reason = "max_steps"
            success = stop_reason not in ("max_steps", "error")
            yield (_LOOP_DONE, final, stop_reason, success)
        except Exception as e:
            final = f"I hit an error: {e}"
            yield ("token", final)
            yield (_LOOP_DONE, final, "error", False)
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
            return
        if self.memory and hasattr(self.memory, "add_interaction"):
            try:
                self.memory.add_interaction(user_input, final)
            except Exception:
                pass
    def _compact(self):
        keep = self.max_history // 2
        old, self.history = self.history[:-keep], self.history[-keep:]
        text = "\n".join(f"User: {u}\nCozmo: {a}" for u, a in old)
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
