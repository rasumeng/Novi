"""
CozmoRuntime — native tool-calling agentic loop.

Unified pipeline (Phase 1): no mode branching. Intent detected at entry,
then the same ReAct loop runs regardless of intent. Grounding search
is triggered for research intent only.

Loop:
  USER INPUT
  → detect intent → build tools + prompt
  → research: FORCED grounding search before the loop (small local models
    skip tools and hallucinate current events if you let them choose)
  → LOOP: model.invoke → tool_calls? → permission gate → exec → feed back
                       ↘ no calls → stream final answer → done
  → compact history when it grows past the window
"""

import difflib
import json
import base64
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool

from ..orchestrator.intent import classify_intent, IntentType
from .model_router import ModelRequirement
from .trace import DebugTraceEvent, ExecutionTrace, StepTrace, ToolCallTrace, TraceAction, TraceEvent
from .execution_context import ExecutionContext
from .evidence import EvidenceBundle, EvidenceCollector, RetrievalQuality
from .retrieval_policy import RetrievalPlan, RetrievalSource, RetrievalStrategy
from .retrieval_coordinator import RetrievalBudget, RetrievalCoordinator
from ..capabilities import CapabilityRegistry
from ..capabilities.builtin import register_builtin_capabilities
from .model_router import ModelRouter

_INTENT_TO_CAP_IDS = {
    "conversation": ["conversation"],
    "research": ["research", "conversation"],
    "coding": ["coding", "filesystem", "terminal"],
    "planning": ["planning", "conversation"],
    "vision": ["vision", "conversation"],
}

_INTENT_TO_ROLE = {
    "conversation": "chat",
    "research": "planner",
    "coding": "coder",
    "planning": "planner",
    "vision": "vision",
}

# Capability → model role mapping (used when orchestrator analysis available)
_CAPABILITY_TO_ROLE = {
    "coding": "coder",
    "planning": "planner",
    "research": "planner",
    "conversation": "chat",
    "vision": "vision",
}

log = logging.getLogger("cozmo.runtime")

ATTACHMENTS_DIR = Path.home() / ".cozmo" / "attachments"
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
    "search_web": "web",
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
    "task": "other",
}


# ── Skill loading ─────────────────────────────────────────────────────────────

_SKILL_RE = re.compile(r"@skill\s+([a-z0-9][a-z0-9-]*)", re.IGNORECASE)

# Max chars of bundled skill files injected into the prompt at once.
# SKILL.md itself is always included; extra files are trimmed to this budget.
_MAX_SKILL_FILES_CHARS = 6000


def _load_all_skills() -> dict[str, dict]:
    """Return {name: {name, description, content, files, path}} for every installed skill."""
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
from .tool_risk import ToolRisk, get_tool_risk, risk_to_label
from .tool_registry import ToolRegistry
from .event_bus import EventBus, EventType
from .lessons import LessonStore
from ..tools import TOOL_REGISTRY
from ..memory.knowledge_index import get_knowledge_index
from ..models import ModelUnavailableError


class _RouterLLM:
    """Lightweight wrapper around ModelService for intent classification & summarization.

    Provides the simple `invoke(prompt) -> str` API that `classify_intent`
    and history compaction expect. Falls back to None if no chat model configured.
    """

    def __init__(self, model_service, role: str = "chat"):
        self._model_service = model_service
        self._role = role
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                self._client = self._model_service.client_for_role(self._role)
            except ModelUnavailableError:
                return None
        return self._client

    def invoke(self, prompt: str, **kwargs) -> str:
        client = self._get_client()
        if client is None:
            raise ModelUnavailableError("chat", None, [])
        result = client.invoke(prompt, **kwargs)
        return result.content if hasattr(result, 'content') else str(result)


# ── Prompts ──────────────────────────────────────────────────────────────────

_IDENTITY = (
    "You are Cozmo, a capable local AI assistant running entirely on-device via Ollama. "
    "You help with coding, file editing, debugging, running commands, research, writing, "
    "analysis, and general questions.\n"
    "Today's date is {date}. Your training data is older than this — for "
    "anything time-sensitive, trust tool results over your own knowledge.\n\n"
    "AGENT BEHAVIOR:\n"
    "- You work in a LOOP. Call a tool, read its result, then decide the next "
    "step. Keep going until the task is actually done, then give a final answer.\n"
    "- Prefer acting with tools over guessing. To answer questions about files "
    "or the codebase, READ them first — never invent file contents.\n"
    "- If a tool returns an error, read the error and try a corrected call — "
    "do not give up after one failure, and do not repeat the identical call.\n"
    "- Take ONE concrete step at a time. Don't announce a plan and stop; execute it.\n"
    "- When the task is complete, respond with a normal message and NO tool call. "
    "That message is shown to the user as the final answer.\n"
    "- Be concise and direct. No hedging ('as of my last update'), no filler.\n"
    "- When provided with search results, use them as your primary source.\n"
    "- Your internal knowledge supplements, not replaces, current evidence.\n"
)

_COLLAB_PLAN_PROMPT = """You are planning a multi-step task. Review the context and generate a clear, numbered plan.

CONTEXT:
{context}

USER REQUEST: {query}

Generate a numbered plan with concrete steps. Each step should say what you will do, which tools you'll use, and the expected output.

Format:
## Plan
1. [Step description] — tools: [tool names] — output: [expected result]
2. [Step description] — tools: [tool names] — output: [expected result]

Keep steps focused and actionable. 3-7 steps is typical for most tasks."""

_COMPACT_PROMPT = """Condense this conversation into a short context note (4-6 sentences max).
Keep: what the user is working on, key facts established, decisions made, user preferences.
Drop: greetings, pleasantries, resolved dead-ends.

{text}

Context note:"""

# text-fallback: models that don't emit native tool_calls sometimes emit JSON.
_TEXT_TOOLCALL_RE = re.compile(r"\{.*\}", re.DOTALL)


class RecoveryAction(str, Enum):
    NONE = "none"
    UPGRADE_SEARCH = "upgrade_search"


@dataclass
class RecoveryDecision:
    action: RecoveryAction = RecoveryAction.NONE
    reason: str = ""


# ── Runtime ──────────────────────────────────────────────────────────────────

class CozmoRuntime:
    """Single agentic runtime loop with native tool calling."""

    def __init__(
        self,
        model_manager: object | None = None,
        model_service=None,
        memory=None,
        registry: ToolRegistry | None = None,
        project_index=None,
        cfg: dict | None = None,
        router_llm: object | None = None,
        skills: dict | None = None,
        event_bus=None,
        orchestrator=None,
        debug_trace: bool = False,
    ):
        self.model_manager = model_manager
        self.model_service = model_service
        self.router_llm = router_llm
        self.memory = memory
        self._registry = registry or ToolRegistry()
        self.project_index = project_index
        self.cfg = cfg or {}
        self.event_bus = event_bus
        self._orchestrator = orchestrator
        self.history: list[tuple[str, str]] = []
        self._summary: str = ""  # compacted old history

        rt = self.cfg.get("runtime", {})
        self.max_history = rt.get("max_history", 10)
        self.max_steps = rt.get("max_steps", 10)
        self.max_tool_output = rt.get("max_tool_output_chars", 8000)
        self.memory_distance_threshold = rt.get("memory_distance_threshold", 0.5)
        self.max_memory_results = rt.get("max_memory_results", 3)
        self.max_project_results = rt.get("max_project_results", 3)

        self.temperature = rt.get("temperature", 0.4)

        self._plan_callback = None  # UI hook: (plan_text) -> bool

        self._perms = PermissionResolver(self.cfg)
        self._permission_callback = None  # UI hook: (tool, args) -> bool
        self._perm_mode = "manual"
        self._lc_tools = self._build_lc_tools()
        # skills is shared/read-only when passed in by the server; only fall
        # back to a disk read when constructed standalone (e.g. CLI).
        self._skills = skills if skills is not None else _load_all_skills()
        self._skill_names_list = ", ".join(
            f"{n} ({s['description'][:60]})" for n, s in self._skills.items()
        ) if self._skills else "(none installed)"
        self.stop_event: threading.Event | None = None
        self._agent_system_extra: str = ""
        self.debug_trace = debug_trace
        self._active_coordinator: RetrievalCoordinator | None = None
        self.lesson_store = LessonStore()

        # Phase 4: capability-based tool resolution
        self._capability_registry = CapabilityRegistry()
        register_builtin_capabilities(self._capability_registry)

        llm_cfg = self.cfg.get("llm", {})
        default_model = llm_cfg.get("default_model") or "qwen3:8b"
        routing = rt.get("routing", {})
        cap_prefs = routing.get("capability_preferences")
        self._model_router = ModelRouter(
            default_model=default_model,
            resource_manager=None,
            capability_preferences=cap_prefs,
        )
        if self.model_service:
            self._model_router.populate_from_service(self.model_service, self.cfg)

        self.force_capability = rt.get("force_capability", "") or ""
        self.force_model = rt.get("force_model", "") or ""
        if self.force_capability:
            log.info("force_capability set to %s (debug override)", self.force_capability)
        if self.force_model:
            log.info("force_model set to %s (debug override)", self.force_model)

        # Config-driven routing (Phase 5C)
        routing = rt.get("routing", {})
        self._intent_cap_ids = routing.get("intent_capabilities", _INTENT_TO_CAP_IDS)
        self._intent_roles = routing.get("intent_roles", _INTENT_TO_ROLE)
        self._capability_roles = routing.get("capability_roles", _CAPABILITY_TO_ROLE)

        # Tool fallback chains (Phase 5E.2)
        tools_cfg = rt.get("tools", {})
        self._tool_fallbacks: dict[str, list[str]] = tools_cfg.get("fallbacks", {})

        # Planning threshold (Phase 5F): plan_level >= this triggers planning
        planning_cfg = rt.get("planning", {})
        self._planning_threshold = planning_cfg.get("auto_threshold", 1)

    def _check_stop(self):
        """Stop the generator early if stop_event was set."""
        if self.stop_event and self.stop_event.is_set():
            return True
        return False

    def set_permission_callback(self, callback):
        """callback(tool_name, args) -> bool. Set by the UI layer for 'ask' rules."""
        self._permission_callback = callback

    def set_plan_callback(self, callback):
        """callback(plan_text) -> bool. Set by the UI layer for agent plan approval."""
        self._plan_callback = callback

    # ── langchain tool wrappers ──────────────────────────────────────────

    def _build_lc_tools(self) -> dict:
        """Wrap registry functions as StructuredTools (schema from signatures)."""
        return self._registry.as_lc_tools()

    def _tools_for_mode(self, capability: str = "", profile=None,
                        allowed_tools: list[str] | None = None) -> list:
        """Return tools filtered by capability-resolved allowlist.

        If allowed_tools is provided, only those tools are returned.
        Otherwise returns all registered tools.
        """
        if allowed_tools is not None:
            allowed = set(allowed_tools)
            return [t for t in self._lc_tools.values() if t.name in allowed]

        tools = list(self._lc_tools.values())
        if profile and hasattr(profile, 'tool_whitelist') and profile.tool_whitelist:
            whitelist = set(profile.tool_whitelist)
            tools = [t for t in tools if t.name in whitelist]
        return tools

    # ── context ──────────────────────────────────────────────────────────

    def _history_messages(self) -> list:
        msgs = []
        for user, assistant in self.history[-self.max_history:]:
            msgs.append(HumanMessage(content=user))
            msgs.append(AIMessage(content=assistant))
        return msgs

    def _query_memory(self, user_input: str, intent: str = "conversation",
                      trace=None) -> str:
        if not self.memory:
            return ""
        t0 = time.time()
        try:
            type_filter = self._memory_types_for_intent(intent)
            results = self.memory.query(
                user_input,
                k=self.max_memory_results * 3,
                distance_threshold=self.memory_distance_threshold,
                memory_types=type_filter,
            )
            if trace is not None:
                trace.memory_queried = True
            if not results:
                return ""
            results = self._rank_memories(results)[:self.max_memory_results]
            if trace is not None:
                trace.memory_result_count = len(results)
                trace.memory_latency_ms = round((time.time() - t0) * 1000, 2)
            sections = []
            type_labels = set()
            for r in results:
                meta = r.get("metadata", {})
                t = meta.get("type", "")
                if t not in type_labels:
                    type_labels.add(t)
                    sections.append(f"\n--- {t.capitalize()} ---") if t else None
                sections.append(f"  {r['text']}")
            return "\n".join(sections) if sections else ""
        except Exception:
            return ""

    def _rank_memories(self, results: list[dict]) -> list[dict]:
        """Rank by importance (frequency × recency × distance)."""
        now = datetime.now()
        scored = []
        for r in results:
            meta = r.get("metadata", {})
            freq = meta.get("frequency", 1)
            ts = meta.get("timestamp", "")
            try:
                age_hours = (now - datetime.fromisoformat(ts)).total_seconds() / 3600 if ts else 24
            except Exception:
                age_hours = 24
            recency = max(0.1, 1.0 - age_hours / 168)
            distance = r.get("distance", 0.5)
            importance = freq * recency * (1.0 - distance)
            scored.append((importance, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]

    @staticmethod
    def _memory_types_for_intent(intent: str) -> list[str] | None:
        mapping = {
            "conversation": ["conversation", "preference", "fact"],
            "research": ["reference", "fact", "conversation"],
            "coding": ["project", "learning", "reference"],
            "planning": ["project", "reference", "fact"],
            "vision": ["reference", "conversation"],
        }
        return mapping.get(intent)

    def _query_project(self, user_input: str) -> str:
        if not self.project_index:
            return ""
        try:
            return self.project_index.query(user_input, k=self.max_project_results) or ""
        except Exception:
            return ""

    def _system_prompt(self, user_input: str, intent: str = "conversation",
                       grounding: str = "",
                       grounding_error: str | None = None,
                       attachments: list[dict] | None = None,
                       activated_skills: list[dict] | None = None,
                       profile=None,
                       allowed_tools: list[str] | None = None,
                       analysis=None,
                       trace=None) -> str:
        parts = [_IDENTITY.format(date=datetime.now().strftime("%A, %B %d, %Y"))]

        if profile and hasattr(profile, 'system_prompt_extra') and profile.system_prompt_extra:
            parts.append(f"PROFILE INSTRUCTIONS:\n{profile.system_prompt_extra}")

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
            parts.append(
                "AVAILABLE SKILLS (you can activate one by writing @skill <name> in your response):\n"
                f"{skill_lines}"
            )

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

        # Evidence-gated memory retrieval (Phase 5D)
        should_query = False
        if analysis is not None:
            should_query = analysis.evidence.needs_memory
        else:
            should_query = intent in ("conversation", "planning")
        memory = self._query_memory(user_input, intent, trace=trace) if should_query else ""
        if memory:
            parts.append(f"\nRelevant memory from past sessions:{memory}")

        lessons = self.lesson_store.get_context(tool_names=allowed_tools if allowed_tools else None)
        if lessons:
            parts.append(lessons)

        if intent in ("coding", "work"):
            project = self._query_project(user_input)
            if project:
                parts.append(f"\nRelevant project context:\n{project}")

        if getattr(self, '_project_context', None):
            parts.append(f"\nProject context:\n{self._project_context}")

        if grounding:
            parts.append(
                "\nSearch results for the user's question (use these as your "
                "primary source — they reflect current information):\n"
                f"{grounding}\n\n"
                "IMPORTANT: Prioritize the search results above over your "
                "internal knowledge. Use your internal knowledge only to "
                "supplement or explain. If the search results are incomplete "
                "or inconclusive, say so."
            )
        elif grounding_error:
            parts.append(
                "\nThe web search service encountered an error while looking "
                "up current information. You may need to rely on your internal "
                "knowledge, or suggest the user try again later. Do NOT "
                "pretend no information exists — explain that retrieval failed."
            )

        return "\n\n".join(parts)

    # ── trace event helper ──────────────────────────────────────────────

    def _trace_event(self, action: TraceAction, category: str, summary: str,
                     trace=None,
                     debug_category: str | None = None,
                     debug_data: dict | None = None) -> TraceEvent:
        """Build and yield a TraceEvent to both the stream and event bus.

        Translates internal decisions into user-facing explanations.
        Does NOT expose internal objects (GroundingDecision, EvidenceAnalysis).

        When debug_trace=True and trace is provided, also records a
        DebugTraceEvent with raw internal state in trace.debug_events.
        """
        event = TraceEvent(action=action, category=category, summary=summary)
        if trace is not None:
            trace.user_events.append(event)
        self._emit_bus("trace_event", trace_event=event.to_dict())
        if self.debug_trace and trace is not None and debug_category:
            dbg = DebugTraceEvent(category=debug_category, data=debug_data)
            trace.debug_events.append(dbg)
        return event

    # ── skills ────────────────────────────────────────────────────────────

    def _skill_block(self, sk: dict) -> str:
        """Render an activated skill for the prompt. Caps bundled file content
        so a large skill can't blow a small model's context window (progressive
        disclosure — SKILL.md always shown, files trimmed to a budget)."""
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
        """Return installed skills newly referenced via @skill in `text`
        (skipping any already activated). Used for both the user's message
        and the model's own output, so the model can self-activate skills."""
        found: list[dict] = []
        if not self._skills or not text:
            return found
        for m in _SKILL_RE.finditer(text):
            sk = self._skills.get(m.group(1).lower())
            if sk and sk not in already and sk not in found:
                found.append(sk)
        return found

    # ── forced grounding search (research mode) ──────────────────────────

    _SEARCH_STOPWORDS = {
        "what", "is", "the", "are", "how", "to", "in", "of", "for", "a", "an",
        "and", "or", "on", "at", "by", "with", "from", "do", "does", "can",
        "will", "would", "should", "could", "did", "has", "have", "had",
        "was", "were", "be", "been", "being", "get", "got", "am", "its",
        "it's", "its", "that", "this", "these", "those", "i", "my", "me",
        "you", "your", "we", "our", "they", "them", "their", "he", "she",
        "him", "her", "his", "tell", "give", "show", "find", "help",
        "when", "where", "why", "which", "who", "whom",
    }

    @staticmethod
    def _key_terms(query: str) -> list[str]:
        """Extract meaningful search terms, skipping stopwords."""
        tokens = re.findall(r"[A-Za-z0-9]+", query.lower())
        return [t for t in tokens if t not in CozmoRuntime._SEARCH_STOPWORDS and len(t) > 1]

    @staticmethod
    def _relevance_score(results_text: str, key_terms: list[str]) -> float:
        """Fraction of key terms appearing in results text."""
        if not key_terms:
            return 1.0
        lower = results_text.lower()
        hits = sum(1 for t in key_terms if t in lower)
        return hits / len(key_terms)

    @staticmethod
    def _reformulate_query(original: str, key_terms: list[str]) -> str:
        """Build a reformulated query from key terms when original failed."""
        return " ".join(key_terms[:6])

    def _grounding_search(self, user_input: str, trace=None) -> EvidenceBundle:
        """Search web, collect evidence, return structured grounding text.

        Uses EvidenceCollector for: search → rank (prioritize text) → fetch → merge.
        Returns EvidenceBundle — check bundle.error to distinguish search API failure
        from empty results. Sets bundle.quality to the final RetrievalQuality.
        """
        if not user_input or not user_input.strip():
            return EvidenceBundle(query=user_input)
        if self._check_stop():
            return EvidenceBundle(query=user_input)
        collector = EvidenceCollector()
        bundle = collector.collect(user_input, min_sources=2)

        if bundle.error:
            log.warning("grounding search failed for '%s': %s", user_input, bundle.error)
            bundle.quality = RetrievalQuality.FAILED
            if trace is not None:
                trace.debug_events.append(DebugTraceEvent(
                    category="retrieval",
                    data={
                        "status": "failed",
                        "error": bundle.error,
                        "query": user_input,
                        "provider": "searxng",
                        "quality": bundle.quality.value,
                    },
                ))
            return bundle

        if not bundle.results or bundle.source_count == 0:
            log.info("grounding search found no textual sources for '%s'", user_input)
            bundle.quality = RetrievalQuality.EMPTY
            if trace is not None:
                trace.debug_events.append(DebugTraceEvent(
                    category="retrieval",
                    data={
                        "status": "empty",
                        "query": user_input,
                        "provider": "searxng",
                        "quality": bundle.quality.value,
                    },
                ))
            return bundle

        # Check relevance, reformulate and retry once if low
        key_terms = self._key_terms(user_input)
        relevance = self._relevance_score(bundle.merged_text, key_terms) if key_terms else 1.0
        if key_terms and relevance < 0.3:
            reformulated = self._reformulate_query(user_input, key_terms)
            log.info("low relevance (%.2f) for '%s', retrying with '%s'",
                     relevance, user_input, reformulated)
            retry = collector.collect(reformulated, min_sources=1)
            if retry.results and retry.source_count > 0:
                bundle = retry
                relevance = self._relevance_score(bundle.merged_text, key_terms) if key_terms else 1.0

        # Assign quality based on final state
        has_text = bool(bundle.merged_text and bundle.merged_text.strip())
        bundle.quality = (
            RetrievalQuality.SUFFICIENT
            if has_text and relevance >= 0.3
            else RetrievalQuality.WEAK
        )
        return bundle

    # ── knowledge base retrieval (local) ─────────────────────────────────

    def _retrieve_knowledge(self, query: str, k: int = 5) -> str:
        """Query local knowledge base. Returns formatted text or empty string."""
        try:
            ki = get_knowledge_index()
        except Exception:
            return ""
        if ki is None:
            return ""
        try:
            results = ki.search(query, k=min(k, 20))
        except Exception:
            return ""
        if not results:
            return ""
        lines = []
        for r in results:
            meta = r.get("metadata", {})
            path = meta.get("path", "?")
            title = meta.get("title", path)
            score = r.get("score", 0.0)
            text = r.get("text", "")[:300].replace("\n", " ")
            lines.append(f"- **{title}** ({path}, score={score:.2f}): {text}")
        return "\n".join(lines)

    # ── retrieval plan execution ─────────────────────────────────────────

    def _execute_retrieval_plan(self, ctx, user_input):
        """Execute RetrievalPlan: KB-only, web-only, or KB→web escalation.

        Sets ctx.grounding_text, ctx.grounding_quality, ctx.grounding_error,
        ctx.retrieval_escalated, and trace retrieval fields.
        Yields trace events for the stream.
        """
        plan = ctx.analysis.retrieval_plan
        ctx.trace.retrieval_strategy = plan.strategy.value
        ctx.trace.retrieval_sources = ",".join(s.value for s in plan.sources)
        ctx.retrieval_plan = plan

        if plan.strategy == RetrievalStrategy.NONE:
            event = self._trace_event(
                action=TraceAction.RESPONDING,
                category="knowledge",
                summary="This is a stable concept well-covered in available knowledge.",
                trace=ctx.trace,
                debug_category="grounding",
                debug_data={
                    "retrieval_plan": plan.strategy.value,
                    "reason": plan.reason,
                },
            )
            yield ("trace", event)
            return

        if plan.strategy == RetrievalStrategy.WEB_ONLY:
            event = self._trace_event(
                action=TraceAction.RETRIEVING,
                category="information_retrieval",
                summary="This question may depend on recent information. Looking up current data.",
                trace=ctx.trace,
                debug_category="grounding",
                debug_data={
                    "retrieval_plan": plan.strategy.value,
                    "reason": plan.reason,
                },
            )
            yield ("trace", event)
            yield ("thinking", event.action.value, event.summary, user_input)
            t0 = time.time()
            bundle = self._grounding_search(user_input, trace=ctx.trace)
            ctx.grounding_text = bundle.merged_text
            ctx.grounding_error = bundle.error
            ctx.grounding_quality = bundle.quality.value if bundle.quality else ""
            ctx.trace.grounding_searched = bool(ctx.grounding_text) or bool(bundle.error)
            ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
            ctx.trace.grounding_quality = ctx.grounding_quality
            ctx.trace.grounding_source_count = bundle.source_count
            ctx.trace.grounding_relevance_score = bundle.quality.value if bundle.quality else 0.0
            return

        if plan.strategy == RetrievalStrategy.KNOWLEDGE_ONLY:
            event = self._trace_event(
                action=TraceAction.RETRIEVING,
                category="knowledge_retrieval",
                summary="Searching local knowledge base for relevant information.",
                trace=ctx.trace,
                debug_category="retrieval",
                debug_data={
                    "retrieval_plan": plan.strategy.value,
                    "reason": plan.reason,
                },
            )
            yield ("trace", event)
            yield ("thinking", "Searching knowledge base...", "", user_input)
            t0 = time.time()
            kb_text = self._retrieve_knowledge(user_input)
            ctx.grounding_text = kb_text
            ctx.grounding_quality = RetrievalQuality.SUFFICIENT.value if kb_text else RetrievalQuality.EMPTY.value
            ctx.trace.grounding_searched = bool(kb_text)
            ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
            ctx.trace.grounding_quality = ctx.grounding_quality
            ctx.trace.grounding_source_count = 1 if kb_text else 0
            ctx.trace.grounding_relevance_score = 1.0 if kb_text else 0.0
            return

        if plan.strategy == RetrievalStrategy.KNOWLEDGE_THEN_WEB:
            event = self._trace_event(
                action=TraceAction.RETRIEVING,
                category="information_retrieval",
                summary="Searching local knowledge first, then web if needed.",
                trace=ctx.trace,
                debug_category="retrieval",
                debug_data={
                    "retrieval_plan": plan.strategy.value,
                    "reason": plan.reason,
                },
            )
            yield ("trace", event)
            yield ("thinking", "Searching knowledge base...", "", user_input)
            t0 = time.time()
            kb_text = self._retrieve_knowledge(user_input)
            if kb_text:
                ctx.grounding_text = kb_text
                ctx.grounding_quality = RetrievalQuality.SUFFICIENT.value
                ctx.retrieval_escalated = False
                ctx.trace.grounding_searched = True
                ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
                ctx.trace.grounding_quality = ctx.grounding_quality
                ctx.trace.grounding_source_count = 1
                ctx.trace.grounding_relevance_score = 1.0
                return

            ctx.retrieval_escalated = True
            ctx.trace.retrieval_escalated = True
            yield ("thinking", "Escalating to web search...", "", user_input)
            bundle = self._grounding_search(user_input, trace=ctx.trace)
            ctx.grounding_text = bundle.merged_text
            ctx.grounding_error = bundle.error
            ctx.grounding_quality = bundle.quality.value if bundle.quality else ""
            ctx.trace.grounding_searched = bool(ctx.grounding_text) or bool(bundle.error)
            ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
            ctx.trace.grounding_quality = ctx.grounding_quality
            ctx.trace.grounding_source_count = bundle.source_count
            ctx.trace.grounding_relevance_score = bundle.quality.value if bundle.quality else 0.0
            return

    # ── agent mode: plan generation ──────────────────────────────────────

    def _gather_agent_context(self, user_input: str, trace=None) -> str:
        """Gather memory, project info, and search results for plan context."""
        parts = []
        memory = self._query_memory(user_input, trace=trace)
        if memory:
            parts.append(f"Memory from past sessions:\n{memory}")
        if self._project_context:
            parts.append(f"Project context:\n{self._project_context}")
        if self.project_index:
            try:
                project = self.project_index.query(user_input, k=self.max_project_results)
                if project:
                    parts.append(f"Relevant project files:\n{project}")
            except Exception:
                pass
        if self._summary:
            parts.append(f"Session summary:\n{self._summary}")
        return "\n\n".join(parts) if parts else "(no additional context)"

    def _should_plan(self, analysis) -> bool:
        if analysis is None:
            return False
        return analysis.complexity.plan_level >= self._planning_threshold

    def _generate_plan(self, user_input: str, context: str) -> str:
        """Use the research model to generate a structured plan."""
        try:
            if self.model_service:
                llm = self.model_service.client_for_role("research", temperature=0.2)
            else:
                raise RuntimeError("model_service required for plan generation")
            prompt = _COLLAB_PLAN_PROMPT.format(context=context, query=user_input)
            plan = llm.invoke(prompt)
            text = getattr(plan, "content", plan)
            return text.strip() if isinstance(text, str) else str(text).strip()
        except Exception as e:
            return f"1. Investigate the request: {user_input}\n2. Execute based on available tools and context.\n(Plan generation failed: {e})"

    # ── tool call extraction (native + text fallback) ────────────────────

    def _extract_calls(self, ai) -> list[dict]:
        native = getattr(ai, "tool_calls", None)
        if native:
            return [{"name": c["name"], "args": c.get("args", {}),
                     "id": c.get("id") or c["name"]} for c in native]
        return self._parse_text_toolcall(getattr(ai, "content", "") or "")

    def _parse_text_toolcall(self, content: str) -> list[dict]:
        """Fallback: some models emit {"name":..,"arguments":..} as plain text."""
        if "{" not in content:
            return []
        match = _TEXT_TOOLCALL_RE.search(content)
        if not match:
            return []
        try:
            obj = json.loads(match.group())
        except json.JSONDecodeError:
            return []
        name = obj.get("name") or obj.get("tool")
        args = obj.get("arguments") or obj.get("args") or {}
        if name in self._lc_tools and isinstance(args, dict):
            return [{"name": name, "args": args, "id": name}]
        return []

    # ── tool execution ───────────────────────────────────────────────────

    def _check_permission(self, name: str, args: dict) -> bool:
        mode = getattr(self, '_perm_mode', 'manual')
        # Plan: deny all tool execution (agent generates plan only)
        if mode == 'plan':
            return False
        # Bypass: allow everything without asking
        if mode == 'bypass':
            return True
        # Accept edits: auto-allow file changes, ask for other tools
        if mode == 'accept-edits' and name in ('edit_file', 'write_file'):
            return True
        # Auto: auto-allow LOW risk, ask for MEDIUM+, deny CRITICAL
        if mode == 'auto':
            risk = get_tool_risk(name)
            if risk == ToolRisk.LOW:
                return True
            if risk == ToolRisk.CRITICAL:
                return False
        # Fallback: config rules (resolve uses risk internally)
        decision = self._perms.resolve(name, args, agent="cozmo")
        if decision == "allow":
            return True
        if decision == "deny":
            return False
        # 'ask' — defer to the UI layer; no UI hook means deny (fail safe)
        risk = get_tool_risk(name)
        if risk == ToolRisk.CRITICAL:
            return False
        if self._permission_callback:
            return self._permission_callback(name, args)
        return False

    def _compute_diff(self, name: str, args: dict) -> dict | None:
        if name == "edit_file":
            old = (args.get("old_text") or "").splitlines(keepends=True)
            new = (args.get("new_text") or "").splitlines(keepends=True)
            diff = list(difflib.unified_diff(old, new,
                         fromfile=args.get("path","?"), tofile=args.get("path","?"), n=3))
            text = "".join(diff[2:]) if len(diff) > 2 else ""
            added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
            return {"text": text, "added": added, "removed": removed}
        if name == "write_file":
            new = (args.get("content") or "").splitlines()
            return {"text": "\n".join(f"+{l}" for l in new), "added": len(new), "removed": 0}
        return None

    def _exec_tool(self, name: str, args: dict) -> str:
        # Retrieval coordinator interception (budget + dedup)
        coord = getattr(self, '_active_coordinator', None)
        if coord is not None and coord.is_web_tool(name):
            blocked = coord.intercept(name, args)
            if blocked is not None:
                return blocked

        info = self._registry.get(name)
        if info is None:
            known = ", ".join(sorted(t.name for t in self._registry.list()))
            out = f"Error: unknown tool '{name}'. Available tools: {known}"
            self.lesson_store.record(name, args, out)
            if coord is not None:
                coord.record(name, args, out)
            return out
        if not self._check_permission(name, args):
            out = (f"Error: the user DENIED permission for {name}. Do not retry "
                    f"this call — explain what you wanted to do and ask the user.")
            self.lesson_store.record(name, args, out)
            if coord is not None:
                coord.record(name, args, out)
            return out
        try:
            raw = str(info.fn(**args))
        except TypeError as e:
            out = f"Error: bad arguments for {name}: {e}. Check the tool schema and retry."
            self.lesson_store.record(name, args, out)
            if coord is not None:
                coord.record(name, args, out)
            return out
        except Exception as e:
            raw = f"Error: {e}"
        result = self._sanitize(raw)
        result = self._validate_tool_result(name, result)

        # Retry with fallback chain (Phase 5E.2)
        if result.startswith("Error:") and name in self._tool_fallbacks:
            for fb_name in self._tool_fallbacks[name]:
                fb_info = self._registry.get(fb_name)
                if fb_info is None:
                    continue
                try:
                    fb_raw = str(fb_info.fn(**args))
                    fb_result = self._sanitize(fb_raw)
                    fb_result = self._validate_tool_result(fb_name, fb_result)
                    if not fb_result.startswith("Error:"):
                        self.lesson_store.record(name, args, result)
                        self.lesson_store.record(fb_name, args, fb_result)
                        if coord is not None:
                            coord.record(name, args, fb_result)
                        return fb_result
                except Exception:
                    continue

        self.lesson_store.record(name, args, result)
        if coord is not None:
            coord.record(name, args, result)
        return result

    def _validate_tool_result(self, name: str, result: str) -> str:
        if not result or not result.strip():
            return f"Error: {name} returned empty output"
        if "permission denied" in result.lower():
            return f"Error: {name} — permission denied. Try a different approach."
        if "timed out" in result.lower() or "timeout" in result.lower():
            return f"Error: {name} timed out. Try a simpler query or different tool."
        return result

    def _sanitize(self, text: str) -> str:
        if len(text) > self.max_tool_output:
            head = self.max_tool_output // 3
            tail = self.max_tool_output - head
            text = (text[:head]
                    + f"\n... [{len(text) - self.max_tool_output} chars truncated] ...\n"
                    + text[-tail:])
        return text

    @staticmethod
    def _tool_category(name: str) -> str:
        return _TOOL_CATEGORIES.get(name, "other")

    # ── recovery (Knowledge Assessment / Runtime Recovery) ────────────────

    def _evaluate_recovery(self, ctx, step, calls_this_step) -> RecoveryDecision:
        """Determine whether runtime recovery is warranted.

        Conditions (all must hold):
        1. Retrieval was attempted (quality recorded)
        2. Quality is not SUFFICIENT
        3. Model chose to answer without calling any tool
        4. Below recovery attempt limit
        5. Either: needs_grounding was True, OR a retrieval plan with sources was active
        """
        quality_str = ctx.grounding_quality
        if not quality_str:
            return RecoveryDecision(action=RecoveryAction.NONE, reason="no retrieval quality recorded")

        try:
            quality = RetrievalQuality(quality_str)
        except ValueError:
            return RecoveryDecision(action=RecoveryAction.NONE, reason="unrecognized quality")

        if quality == RetrievalQuality.SUFFICIENT:
            return RecoveryDecision(action=RecoveryAction.NONE, reason="retrieval was sufficient")

        needs_grounding = (
            ctx.analysis is not None
            and ctx.analysis.grounding.needs_grounding
        )
        had_plan = (
            ctx.analysis is not None
            and ctx.analysis.retrieval_plan is not None
            and ctx.analysis.retrieval_plan.strategy not in (RetrievalStrategy.NONE,)
        )
        if not needs_grounding and not had_plan:
            return RecoveryDecision(action=RecoveryAction.NONE, reason="no retrieval was requested")

        if calls_this_step > 0:
            return RecoveryDecision(action=RecoveryAction.NONE, reason="model is already using tools")

        if ctx.trace.recovery_attempts >= 1:
            return RecoveryDecision(action=RecoveryAction.NONE, reason="max recovery attempts reached")

        return RecoveryDecision(
            action=RecoveryAction.UPGRADE_SEARCH,
            reason=f"retrieval quality={quality_str}, model answered without tools",
        )

    def _apply_recovery(self, ctx, runnable, model_name, temperature, decision, msgs):
        """Apply the recovery decision: upgrade capabilities, rebind runnable,
        inject system message, log debug event."""
        if decision.action == RecoveryAction.NONE:
            return runnable

        if decision.action == RecoveryAction.UPGRADE_SEARCH:
            search_tools = self._capability_registry.get_tool_names(["search"])
            ctx.allowed_tools = list(set(ctx.allowed_tools) | set(search_tools))
            lc_tools = self._tools_for_mode(allowed_tools=ctx.allowed_tools)
            mm = self.model_service if self.model_service else self.model_manager
            runnable = mm.bind_model(model_name, lc_tools, temperature=temperature)
            msgs.append(SystemMessage(
                content="[Web search tools (web_search, web_fetch) are now available. "
                        "Use them if you need current information.]"
            ))
            ctx.trace.recovery_attempts += 1
            ctx.trace.recovery_action = decision.action.value

            if self.debug_trace and ctx.trace is not None:
                ctx.trace.debug_events.append(DebugTraceEvent(
                    category="recovery",
                    data={
                        "action": decision.action.value,
                        "reason": decision.reason,
                        "attempt": ctx.trace.recovery_attempts,
                        "allowed_tools": list(ctx.allowed_tools),
                    },
                ))

        return runnable

    # ── main streaming loop ──────────────────────────────────────────────

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

    def _emit_bus(self, event_type: str, **data):
        """Emit to event bus if one is attached."""
        if self.event_bus:
            try:
                self.event_bus.emit(event_type, **data)
            except Exception:
                pass

    # ── tracing ──────────────────────────────────────────────────────────

    def _finalize_trace(self, stop_reason: str = "completed", trace=None):
        if trace is None:
            return
        trace.total_latency_ms = round((time.time() - trace.started_at) * 1000, 2)
        trace.total_tool_calls = sum(len(s.tool_calls) for s in trace.steps)
        trace.stop_reason = stop_reason
        trace.emit_event(self.event_bus)

    def _record_tool_call(self, step_idx: int, name: str, args: dict,
                          result: str, latency_ms: float, success: bool,
                          error: str | None = None,
                          fallback_used: str | None = None,
                          trace=None):
        if trace is None:
            return
        while len(trace.steps) <= step_idx:
            trace.steps.append(StepTrace(step=len(trace.steps)))
        step = trace.steps[step_idx]
        step.tool_calls.append(ToolCallTrace(
            name=name,
            args=dict(args),
            result_preview=(result or "")[:200],
            latency_ms=round(latency_ms, 2),
            success=success,
            error=error,
            fallback_used=fallback_used,
        ))

    def run_stream(self, user_input: str | None = None,
                   attachments: list[dict] | None = None,
                   force_mode: str | None = None, agent_runtime=None,
                   force_capability: str | None = None,
                   force_model: str | None = None,
                   execution_plan: object | None = None,
                   context: ExecutionContext | None = None):
        """Yield (kind, text) tuples. Unified pipeline — no mode branching.

        Prefer passing a pre-built ExecutionContext via ``context=``.
        Legacy positional params are still supported for backward compat.

        force_mode is deprecated compat: logged, ignored for routing.
        AgentRuntime support via agent_runtime param (legacy path).
        execution_plan: if provided, use plan.tools / plan.model_spec directly,
                        skipping capability re-resolution.
        """
        intent_str = "conversation"
        try:
            # ── Build or adopt ExecutionContext ────────────────────────────
            ctx = context
            if ctx is None:
                # Legacy path: build context from old params
                ctx = ExecutionContext(
                    user_input=user_input or "",
                    attachments=attachments or [],
                    history=list(self.history),
                    summary=self._summary,
                    force_model=force_model or self.force_model,
                    force_capability=force_capability or self.force_capability,
                )
            # Ensure trace exists
            if ctx.trace is None:
                ctx.trace = ExecutionTrace(user_input=ctx.user_input)
            # Sync user_input for backward compat (e.g. _remember calls)
            user_input = ctx.user_input

            has_images = ctx.attachments and any(a.get("type") == "image" for a in ctx.attachments)
            ctx.has_images = has_images
            if not ctx.activated_skills:
                ctx.activated_skills = self._scan_skills(user_input, [])

            # ── Analysis phase: orchestrator or fallback ──────────────────
            if ctx.execution_plan is not None:
                # Pre-computed plan from webui orchestrator path
                ctx.analysis = ctx.execution_plan.context.get("analysis")
                if not ctx.allowed_tools:
                    ctx.allowed_tools = list(ctx.execution_plan.tools)
            elif ctx.analysis is not None:
                # Pre-populated from context (new path)
                ctx.trace.complexity_score = ctx.analysis.complexity.score
                ctx.trace.plan_level = ctx.analysis.complexity.plan_level
                if self.debug_trace:
                    ctx.trace.debug_events.append(DebugTraceEvent(
                        category="analysis",
                        data={
                            "signals": [s.type for s in ctx.analysis.evidence.signals],
                            "confidence": ctx.analysis.evidence.confidence,
                            "needs_grounding": ctx.analysis.grounding.needs_grounding,
                            "grounding_confidence": ctx.analysis.grounding.confidence,
                            "grounding_source": ctx.analysis.grounding.source,
                        },
                    ))
                if not ctx.allowed_tools:
                    ctx.allowed_tools = self._capability_registry.get_tool_names(ctx.cap_ids)
            elif self._orchestrator is not None:
                # Single analysis pipeline: TaskAnalysis drives routing
                ctx.analysis = self._orchestrator.analyze(user_input, self.history, has_images)
                ctx.allowed_tools = self._capability_registry.get_tool_names(ctx.cap_ids)
                ctx.trace.complexity_score = ctx.analysis.complexity.score
                ctx.trace.plan_level = ctx.analysis.complexity.plan_level
                if self.debug_trace:
                    ctx.trace.debug_events.append(DebugTraceEvent(
                        category="analysis",
                        data={
                            "signals": [s.type for s in ctx.analysis.evidence.signals],
                            "confidence": ctx.analysis.evidence.confidence,
                            "needs_grounding": ctx.analysis.grounding.needs_grounding,
                            "grounding_confidence": ctx.analysis.grounding.confidence,
                            "grounding_source": ctx.analysis.grounding.source,
                        },
                    ))
            elif force_mode is not None:
                log.warning("force_mode='%s' is deprecated. Use force_capability / force_model.", force_mode)
                cap_name = ctx.force_capability or force_mode
                ctx.allowed_tools = self._capability_registry.get_tool_names(
                    self._intent_cap_ids.get(cap_name, ["conversation"]))
            else:
                # Legacy fallback: standalone intent classification
                intent = classify_intent(user_input, self.router_llm, self.history, has_images)
                cap_name = ctx.force_capability or intent.value
                ctx.allowed_tools = self._capability_registry.get_tool_names(
                    self._intent_cap_ids.get(cap_name, ["conversation"]))

            intent_str = ctx.intent_str
            ctx.trace.intent = intent_str

            yield ("status", "Analyzing request...")
            event = self._trace_event(
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
            self._emit_bus("intent_set", intent=intent_str)
            # ── Stop checkpoint — after thinking, before routing ──────────
            if self._check_stop():
                self._finalize_trace("stopped", trace=ctx.trace)
                return
 
            # ── Retrieval (phase 2: pre-loop execution) ──────────────────
            if (ctx.analysis is not None
                    and ctx.analysis.retrieval_plan is not None
                    and ctx.analysis.retrieval_plan.strategy != RetrievalStrategy.NONE):
                for kind_value in self._execute_retrieval_plan(ctx, user_input):
                    yield kind_value
            elif ctx.analysis is not None and ctx.analysis.grounding.needs_grounding:
                grounding_data = {
                    "needs_grounding": True,
                    "confidence": ctx.analysis.grounding.confidence,
                    "source": ctx.analysis.grounding.source,
                    "reason": ctx.analysis.grounding.reason,
                    "signals": [s.type for s in ctx.analysis.evidence.signals],
                    "evidence_confidence": ctx.analysis.evidence.confidence,
                }
                event = self._trace_event(
                    action=TraceAction.RETRIEVING,
                    category="information_retrieval",
                    summary="This question may depend on recent information. Looking up current data.",
                    trace=ctx.trace,
                    debug_category="grounding",
                    debug_data=grounding_data,
                )
                yield ("trace", event)
                yield ("thinking", event.action.value, event.summary, user_input)
                t0 = time.time()
                bundle = self._grounding_search(user_input, trace=ctx.trace)
                ctx.grounding_text = bundle.merged_text
                ctx.grounding_error = bundle.error
                ctx.grounding_quality = bundle.quality.value if bundle.quality else ""
                ctx.trace.grounding_searched = bool(ctx.grounding_text) or bool(bundle.error)
                ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
                ctx.trace.grounding_quality = ctx.grounding_quality
                ctx.trace.grounding_source_count = bundle.source_count
                ctx.trace.grounding_relevance_score = bundle.quality.value if bundle.quality else 0.0
            elif ctx.analysis is not None:
                grounding_data = {
                    "needs_grounding": False,
                    "confidence": ctx.analysis.grounding.confidence,
                    "source": ctx.analysis.grounding.source,
                    "reason": ctx.analysis.grounding.reason,
                    "signals": [s.type for s in ctx.analysis.evidence.signals],
                    "evidence_confidence": ctx.analysis.evidence.confidence,
                }
                event = self._trace_event(
                    action=TraceAction.RESPONDING,
                    category="knowledge",
                    summary="This is a stable concept well-covered in available knowledge.",
                    trace=ctx.trace,
                    debug_category="grounding",
                    debug_data=grounding_data,
                )
                yield ("trace", event)
            elif intent_str == "research":
                event = self._trace_event(
                    action=TraceAction.RETRIEVING,
                    category="information_retrieval",
                    summary="This question depends on current information. Looking up data.",
                    trace=ctx.trace,
                    debug_category="grounding",
                    debug_data={
                        "intent": intent_str,
                        "source": "fallback_intent",
                    },
                )
                yield ("trace", event)
                yield ("thinking", event.action.value, event.summary, user_input)
                t0 = time.time()
                bundle = self._grounding_search(user_input, trace=ctx.trace)
                ctx.grounding_text = bundle.merged_text
                ctx.grounding_error = bundle.error
                ctx.trace.grounding_searched = bool(ctx.grounding_text) or bool(bundle.error)
                ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
                ctx.grounding_quality = bundle.quality.value if bundle.quality else ""
                ctx.trace.grounding_quality = ctx.grounding_quality
                ctx.trace.grounding_source_count = bundle.source_count
                ctx.trace.grounding_relevance_score = bundle.quality.value if bundle.quality else 0.0

            # ── Retrieval coordinator setup ─────────────────────────────
            coord = RetrievalCoordinator()
            if ctx.analysis is not None and ctx.analysis.retrieval_plan is not None:
                plan_strat = ctx.analysis.retrieval_plan.strategy
                if plan_strat == RetrievalStrategy.WEB_ONLY:
                    coord.budget.max_web_searches = 1
                    coord.budget.max_web_fetches = 1
                elif plan_strat == RetrievalStrategy.KNOWLEDGE_THEN_WEB:
                    coord.budget.max_web_searches = 1
                    coord.budget.max_web_fetches = 1
                elif plan_strat == RetrievalStrategy.KNOWLEDGE_ONLY:
                    coord.budget.max_web_searches = 0
                    coord.budget.max_web_fetches = 0
            if ctx.grounding_text and ctx.grounding_quality:
                coord.seed_cache(ctx.user_input, ctx.grounding_text)
            ctx.retrieval_coordinator = coord
            ctx.retrieval_budget = coord.budget
            self._active_coordinator = coord

            # ── Model / tool routing ─────────────────────────────────────
            profile = None
            if ctx.execution_plan is not None:
                ctx.model_name = ctx.execution_plan.model_spec.get("model", "") or ctx.force_model or ""
                ctx.role = self._intent_roles.get(intent_str, "chat")
                if self.model_service and not ctx.force_model:
                    try:
                        _, role_model = self.model_service.resolve(ctx.role)
                        if role_model:
                            ctx.model_name = role_model
                    except Exception:
                        pass
                ctx.temperature = ctx.execution_plan.temperature
                ctx.max_steps = ctx.execution_plan.max_steps
                ctx.model_supports_tools = ctx.execution_plan.model_spec.get("supports_tools", True)
            elif ctx.analysis is not None:
                # TaskAnalysis-driven routing: capabilities → role → model
                if not ctx.model_name:
                    ctx.model_name = ctx.force_model or ""
                preferred_cap = ctx.cap_ids[0] if ctx.cap_ids else "conversation"
                ctx.role = self._capability_roles.get(preferred_cap, "chat")
                if not ctx.model_name:
                    if self.model_service:
                        try:
                            _, role_model = self.model_service.resolve(ctx.role)
                            if role_model:
                                ctx.model_name = role_model
                        except Exception:
                            pass
                if not ctx.model_name:
                    req = [ModelRequirement(capability=preferred_cap)]
                    ctx.model_name = self._model_router.resolve(req)
                ctx.temperature = self.temperature
                ctx.max_steps = ctx.analysis.complexity.max_steps
            else:
                # Fallback: intent-based routing
                cap_name = ctx.force_capability or intent_str
                ctx.role = self._intent_roles.get(cap_name, "chat")
                if not ctx.model_name:
                    ctx.model_name = ctx.force_model or ""
                if not ctx.model_name:
                    if self.model_service:
                        try:
                            _, role_model = self.model_service.resolve(ctx.role)
                            if role_model:
                                ctx.model_name = role_model
                        except Exception:
                            pass
                if not ctx.model_name:
                    req = [ModelRequirement(capability=cap_name)]
                    ctx.model_name = self._model_router.resolve(req)
                ctx.temperature = self.temperature
                ctx.max_steps = self.max_steps

            # Set model_supports_tools for non-plan paths
            if ctx.execution_plan is None and intent_str == "vision":
                ctx.model_supports_tools = False

            ctx.trace.model_selected = ctx.model_name
            ctx.trace.role = ctx.role
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
                ctx.trace.model_reason = "role_match"
                ctx.model_reason = "role_match"
            yield ("model", ctx.model_name)

            # ── Stop checkpoint — before planning ────────────────────────
            if self._check_stop():
                self._finalize_trace("stopped", trace=ctx.trace)
                return

            # ── Optional plan injection (Phase 5F) ─────────────────────────
            if ctx.analysis is not None and self._should_plan(ctx.analysis):
                event = self._trace_event(
                    action=TraceAction.PLANNING,
                    category="planning",
                    summary="Analyzing request complexity and building execution plan.",
                    trace=ctx.trace,
                    debug_category="planning",
                    debug_data={
                        "plan_level": ctx.analysis.complexity.plan_level,
                        "complexity_score": ctx.analysis.complexity.score,
                    },
                )
                yield ("trace", event)
                yield ("thinking", event.action.value, event.summary, None)
                t0 = time.time()
                ctx.plan_context = self._generate_plan(user_input, ctx.grounding_text)
                ctx.trace.plan_generated = bool(ctx.plan_context)
                ctx.trace.plan_latency_ms = round((time.time() - t0) * 1000, 2)
                if ctx.plan_context:
                    yield ("thinking", "Plan generated", ctx.plan_context[:200], None)
            else:
                ctx.trace.plan_generated = False
                ctx.trace.plan_latency_ms = 0.0
            # ── Stop checkpoint — after planning, before tool binding ────
            if self._check_stop():
                self._finalize_trace("stopped", trace=ctx.trace)
                return

            # ── Phase 2: pre-loop tool availability (RetrievalPlan-aware) ─
            plan = ctx.analysis.retrieval_plan if ctx.analysis is not None else None
            if plan is not None and plan.strategy in (
                RetrievalStrategy.WEB_ONLY,
                RetrievalStrategy.KNOWLEDGE_THEN_WEB,
            ):
                search_tools = self._capability_registry.get_tool_names(["search"])
                missing = set(search_tools) - set(ctx.allowed_tools)
                if missing:
                    ctx.allowed_tools = list(set(ctx.allowed_tools) | set(search_tools))
                    ctx.trace.recovery_attempts += 1
                    ctx.trace.recovery_action = "upgrade_search"
                    if self.debug_trace and ctx.trace is not None:
                        ctx.trace.debug_events.append(DebugTraceEvent(
                            category="recovery",
                            data={
                                "action": "upgrade_search",
                                "reason": f"retrieval plan requires web: {plan.strategy.value}",
                                "attempt": ctx.trace.recovery_attempts,
                                "added_tools": list(missing),
                            },
                        ))
            elif ctx.grounding_quality and ctx.grounding_quality != "sufficient":
                if ctx.trace.recovery_attempts < 1:
                    search_tools = self._capability_registry.get_tool_names(["search"])
                    ctx.allowed_tools = list(set(ctx.allowed_tools) | set(search_tools))
                    ctx.trace.recovery_attempts += 1
                    ctx.trace.recovery_action = "upgrade_search"
                    if self.debug_trace and ctx.trace is not None:
                        ctx.trace.debug_events.append(DebugTraceEvent(
                            category="recovery",
                            data={
                                "action": "upgrade_search",
                                "reason": f"pre-loop: retrieval quality={ctx.grounding_quality}",
                                "attempt": ctx.trace.recovery_attempts,
                                "allowed_tools": list(ctx.allowed_tools),
                            },
                        ))

            # ── build ReAct loop ─────────────────────────────────────────
            lc_tools = self._tools_for_mode(capability=intent_str, profile=None,
                                            allowed_tools=ctx.allowed_tools)
            ctx.trace.tools_bound = sorted(t.name for t in lc_tools)
            ctx.trace.tools_available = sorted(t.name for t in lc_tools)

            if not ctx.model_supports_tools:
                lc_tools = []

            mm = self.model_service if self.model_service else self.model_manager
            runnable = (mm.bind_model(ctx.model_name, lc_tools, temperature=ctx.temperature)
                        if lc_tools else mm.client_for_model(ctx.model_name, ctx.temperature))

            # Append plan context to grounding if planning was triggered
            full_grounding = ctx.grounding_text
            if ctx.plan_context:
                full_grounding = (ctx.grounding_text + "\n\n" + ctx.plan_context) if ctx.grounding_text else ctx.plan_context

            msgs = [SystemMessage(content=self._system_prompt(
                user_input, intent_str, full_grounding,
                grounding_error=ctx.grounding_error,
                attachments=ctx.attachments, activated_skills=ctx.activated_skills, profile=profile,
                allowed_tools=ctx.allowed_tools, analysis=ctx.analysis, trace=ctx.trace))]
            coord = ctx.retrieval_coordinator
            if coord is not None and coord.budget.max_web_searches > 0:
                msgs.append(SystemMessage(
                    content="[Retrieval guidance] Retrieval is in progress. Prefer using "
                            "existing retrieved evidence. Avoid repeated searches unless "
                            "previous evidence is clearly insufficient. Only one web search "
                            f"and one web fetch are allowed."
                ))
            msgs += self._history_messages()

            if has_images:
                multimodal = self._build_multimodal_content(user_input, attachments)
                msgs.append(HumanMessage(content=multimodal))
            else:
                msgs.append(HumanMessage(content=user_input))

            # ── Stop checkpoint — before ReAct loop ─────────────────────
            if self._check_stop():
                self._finalize_trace("stopped", trace=ctx.trace)
                return

            final = ""
            seen_calls: set[str] = set()
            for step in range(ctx.max_steps):
                acc = None
                content_buf = ""
                step_start = time.time()
                tokens_in_step = 0

                for chunk in runnable.stream(msgs):
                    if self._check_stop():
                        self._finalize_trace("stopped", trace=ctx.trace)
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
                while len(ctx.trace.steps) <= step:
                    ctx.trace.steps.append(StepTrace(step=len(ctx.trace.steps)))
                ctx.trace.steps[step].model_inference_ms = model_ms
                ctx.trace.steps[step].tokens_generated = tokens_in_step

                calls = self._extract_calls(ai)

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
                    decision = self._evaluate_recovery(ctx, step, 0)
                    if decision.action != RecoveryAction.NONE:
                        runnable = self._apply_recovery(ctx, runnable, ctx.model_name,
                                                        ctx.temperature, decision, msgs)
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
                    if self._check_stop():
                        return
                    sig = f"{c['name']}:{args_sig}"
                    call_id = f"call-{step}-{c['name']}"
                    yield ("tool_call", c["name"], c["args"], call_id, self._tool_category(c["name"]))
                    self._emit_bus("tool_called", tool=c["name"], args=c["args"], step=step)
                    tool_t0 = time.time()
                    if sig in seen_calls:
                        out = (f"Error: you already made this exact {c['name']} call "
                               f"and have its result above. Use it, or try a "
                               f"DIFFERENT call — do not repeat yourself.")
                        tool_success = False
                    else:
                        seen_calls.add(sig)
                        out = self._exec_tool(c["name"], c["args"])
                        tool_success = not out.startswith("Error")
                    tool_ms = round((time.time() - tool_t0) * 1000, 2)
                    self._record_tool_call(
                        step_idx=step, name=c["name"], args=c["args"],
                        result=out, latency_ms=tool_ms, success=tool_success,
                        error=out if out.startswith("Error") else None,
                        trace=ctx.trace,
                    )

                    diff = self._compute_diff(c["name"], c["args"])
                    yield ("tool_result", c["name"], out, call_id, diff)
                    self._emit_bus("tool_result", tool=c["name"], call_id=call_id,
                                   is_error=out.startswith("Error"))
                    msgs.append(ToolMessage(content=out, tool_call_id=c["id"]))

                    # Post-tool recovery: search_knowledge returned empty → escalate to web
                    if (c["name"] == "search_knowledge"
                            and ("No matching knowledge found" in out or not out.strip())
                            and ctx.trace.recovery_attempts < 1
                            and not any(s in ctx.allowed_tools for s in
                                        self._capability_registry.get_tool_names(["search"]))):
                        search_tools = self._capability_registry.get_tool_names(["search"])
                        ctx.allowed_tools = list(set(ctx.allowed_tools) | set(search_tools))
                        new_lc_tools = self._tools_for_mode(allowed_tools=ctx.allowed_tools)
                        runnable = mm.bind_model(ctx.model_name, new_lc_tools, temperature=ctx.temperature)
                        ctx.trace.recovery_attempts += 1
                        ctx.trace.recovery_action = "post_tool_escalation"
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
                                    "reason": "search_knowledge returned empty in loop",
                                    "step": step,
                                    "tool": c["name"],
                                },
                            ))

                    if self._check_stop():
                        self._finalize_trace("stopped", trace=ctx.trace)
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
            ctx.trace.final_response_length = len(final)
            rc = ctx.retrieval_coordinator
            if rc is not None:
                ctx.trace.retrieval_search_count = rc.budget.searches_used
                ctx.trace.retrieval_fetch_count = rc.budget.fetches_used
                ctx.trace.retrieval_budget_exhausted = rc.budget.is_exhausted
            self._finalize_trace(stop_reason, trace=ctx.trace)

            self._remember(user_input, final)
            self._active_coordinator = None

        except Exception as e:
            self._active_coordinator = None
            self._finalize_trace("error", trace=ctx.trace)
            msg = f"I hit an error: {e}"
            yield ("token", msg)
            self._remember(user_input, msg)

    def run(self, user_input: str, attachments: list[dict] | None = None) -> str:
        """Synchronous run. Returns the final answer text."""
        chunks = []
        for kind, text in self.run_stream(user_input, attachments):
            if kind == "token":
                chunks.append(text)
        return "".join(chunks).strip()

    # ── persistence + compaction ─────────────────────────────────────────

    def _remember(self, user_input: str, final: str):
        self.history.append((user_input, final))
        if len(self.history) > self.max_history:
            self._compact()
        if self.memory and hasattr(self.memory, "add_interaction"):
            try:
                self.memory.add_interaction(user_input, final)
            except Exception:
                pass

    def _compact(self):
        """Summarize the older half of history into a context note instead of
        dropping it. Keeps long sessions coherent within a small ctx window."""
        keep = self.max_history // 2
        old, self.history = self.history[:-keep], self.history[-keep:]
        text = "\n".join(f"User: {u}\nCozmo: {a}" for u, a in old)
        if self._summary:
            text = f"Earlier context:\n{self._summary}\n\n{text}"
        try:
            summary = self.router_llm.invoke(_COMPACT_PROMPT.format(text=text))
            if summary and not summary.lower().startswith("error"):
                self._summary = summary.strip()
        except Exception as e:
            log.warning("history compaction failed: %s", e)

    def reset(self):
        """Clear conversation state (new chat)."""
        self.history.clear()
        self._summary = ""


