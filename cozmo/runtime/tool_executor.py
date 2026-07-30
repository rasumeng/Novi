"""ToolExecutor — single execution pipeline with permission gating,
validation, sanitization, fallback chains, diff, and tracing.

Pipeline:
  ToolCall → Permission → Validation → Execution → Sanitization
  → Normalization → Fallback → Record → ToolResult
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from langchain_core.messages import AIMessage

from .tool_risk import ToolRisk, get_tool_risk
from .tool_registry import ToolRegistry

log = logging.getLogger("cozmo.runtime")

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

_TEXT_TOOLCALL_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class ToolResult:
    """Normalized result of a single tool execution."""

    output: str
    success: bool
    error: str | None = None
    diff: dict | None = None
    latency_ms: float = 0.0


class ToolExecutor:
    """Single pipeline for tool execution.

    Public entry point is execute(). All pipeline stages are private.
    Utility methods (compute_diff, record_tool_call) remain public for
    callers that need them outside the pipeline (e.g. dedup path in runtime).
    """

    def __init__(
        self,
        registry: ToolRegistry,
        perms: object,
        lesson_store: object,
        lc_tools: dict,
        tool_fallbacks: dict[str, list[str]],
        max_tool_output: int,
        perm_mode: str = "manual",
        debug_trace: bool = False,
        event_bus=None,
    ):
        self._registry = registry
        self._perms = perms
        self.lesson_store = lesson_store
        self._lc_tools = lc_tools
        self._tool_fallbacks = tool_fallbacks
        self.max_tool_output = max_tool_output
        self._perm_mode = perm_mode
        self.debug_trace = debug_trace
        self.event_bus = event_bus
        self._permission_callback: Callable | None = None

    def set_permission_callback(self, callback: Callable | None):
        self._permission_callback = callback

    # ── tool collection ────────────────────────────────────────────────

    def build_lc_tools(self) -> dict:
        return self._registry.as_lc_tools()

    def tools_for_mode(
        self,
        capability: str = "",
        profile=None,
        allowed_tools: list[str] | None = None,
    ) -> list:
        if allowed_tools is not None:
            allowed = set(allowed_tools)
            return [t for t in self._lc_tools.values() if t.name in allowed]
        tools = list(self._lc_tools.values())
        if profile and hasattr(profile, "tool_whitelist") and profile.tool_whitelist:
            whitelist = set(profile.tool_whitelist)
            tools = [t for t in tools if t.name in whitelist]
        return tools

    # ── call extraction ────────────────────────────────────────────────

    def extract_calls(self, ai) -> list[dict]:
        native = getattr(ai, "tool_calls", None)
        if native:
            return [
                {
                    "name": c["name"],
                    "args": c.get("args", {}),
                    "id": c.get("id") or c["name"],
                }
                for c in native
            ]
        return self._parse_text_toolcall(getattr(ai, "content", "") or "")

    def _parse_text_toolcall(self, content: str) -> list[dict]:
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

    # ── category (static lookup, used before execute in loop) ──────────

    @staticmethod
    def tool_category(name: str) -> str:
        return _TOOL_CATEGORIES.get(name, "other")

    # ── unified execution pipeline ─────────────────────────────────────

    def execute(
        self,
        name: str,
        args: dict,
        coordinator=None,
        perm_mode: str | None = None,
        permission_callback: Callable | None = None,
        step_idx: int | None = None,
        trace=None,
    ) -> ToolResult:
        """Run one tool through the full pipeline.

        Pipeline stages (all private):
          1. Coordinator intercept (budget / dedup gate)
          2. Registry lookup — fail if unknown
          3. Permission gate — fail if denied
          4. Tool execution — catch TypeError/Exception
          5. Sanitization — truncate oversized output
          6. Normalization — reject empty / timeout / permission-denied
          7. Fallback chain — retry with alternative tool
          8. Record — lesson store + coordinator
          9. Diff computation
          10. Trace recording (if step_idx + trace provided)
        """
        t0 = time.time()
        coord = coordinator
        error: str | None = None
        fallback_used: str | None = None

        # Stage 1: Coordinator intercept
        if coord is not None and coord.is_web_tool(name):
            blocked = coord.intercept(name, args)
            if blocked is not None:
                out = blocked
                diff = self.compute_diff(name, args)
                lat = round((time.time() - t0) * 1000, 2)
                self.record_tool_call(step_idx or 0, name, args, out, lat,
                                       False, error=out[:200] if out.startswith("Error") else None, trace=trace)
                return ToolResult(output=out, success=False, diff=diff, latency_ms=lat)

        # Stage 2: Registry lookup
        info = self._registry.get(name)
        if info is None:
            known = ", ".join(sorted(t.name for t in self._registry.list()))
            out = f"Error: unknown tool '{name}'. Available tools: {known}"
            self.lesson_store.record(name, args, out)
            if coord is not None:
                coord.record(name, args, out)
            lat = round((time.time() - t0) * 1000, 2)
            self.record_tool_call(step_idx or 0, name, args, out, lat,
                                   False, error=out, trace=trace)
            return ToolResult(output=out, success=False, error=out, latency_ms=lat)

        # Stage 3: Permission gate
        if not self._check_permission(name, args, perm_mode, permission_callback):
            out = (
                f"Error: the user DENIED permission for {name}. Do not retry "
                f"this call — explain what you wanted to do and ask the user."
            )
            self.lesson_store.record(name, args, out)
            if coord is not None:
                coord.record(name, args, out)
            lat = round((time.time() - t0) * 1000, 2)
            self.record_tool_call(step_idx or 0, name, args, out, lat,
                                   False, error=out, trace=trace)
            return ToolResult(output=out, success=False, error=out, latency_ms=lat)

        # Stage 4: Tool execution
        try:
            raw = str(info.fn(**args))
        except TypeError as e:
            out = f"Error: bad arguments for {name}: {e}. Check the tool schema and retry."
            self.lesson_store.record(name, args, out)
            if coord is not None:
                coord.record(name, args, out)
            lat = round((time.time() - t0) * 1000, 2)
            self.record_tool_call(step_idx or 0, name, args, out, lat,
                                   False, error=out, trace=trace)
            return ToolResult(output=out, success=False, error=out, latency_ms=lat)
        except Exception as e:
            raw = f"Error: {e}"

        # Stage 5: Sanitization
        result = self._sanitize(raw)
        # Stage 6: Normalization
        result = self._normalize_result(name, result)

        success = not result.startswith("Error:")

        # Stage 7: Fallback chain
        if not success and name in self._tool_fallbacks:
            for fb_name in self._tool_fallbacks[name]:
                fb_info = self._registry.get(fb_name)
                if fb_info is None:
                    continue
                try:
                    fb_raw = str(fb_info.fn(**args))
                    fb_result = self._sanitize(fb_raw)
                    fb_result = self._normalize_result(fb_name, fb_result)
                    if not fb_result.startswith("Error:"):
                        self.lesson_store.record(name, args, result)
                        self.lesson_store.record(fb_name, args, fb_result)
                        if coord is not None:
                            coord.record(name, args, fb_result)
                        fallback_used = fb_name
                        result = fb_result
                        success = True
                        # Skip Stage 8 — already recorded above
                        diff = self.compute_diff(name, args)
                        lat = round((time.time() - t0) * 1000, 2)
                        self.record_tool_call(step_idx or 0, name, args, result, lat,
                                               True, fallback_used=fb_name, trace=trace)
                        return ToolResult(output=result, success=True, diff=diff, latency_ms=lat)
                except Exception:
                    continue

        # Stage 8: Record
        self.lesson_store.record(name, args, result)
        if coord is not None:
            coord.record(name, args, result)

        # Stage 9: Diff
        diff = self.compute_diff(name, args)

        # Stage 10: Trace
        lat = round((time.time() - t0) * 1000, 2)
        error = result if not success else None
        self.record_tool_call(step_idx or 0, name, args, result, lat,
                               success, error=error, fallback_used=fallback_used, trace=trace)

        return ToolResult(
            output=result,
            success=success,
            error=error,
            diff=diff,
            latency_ms=lat,
        )

    # ── pipeline stages (private) ──────────────────────────────────────

    def _check_permission(
        self,
        name: str,
        args: dict,
        perm_mode: str | None = None,
        permission_callback: Callable | None = None,
    ) -> bool:
        mode = perm_mode if perm_mode is not None else self._perm_mode
        if mode == "plan":
            return False
        if mode == "bypass":
            return True
        if mode == "accept-edits" and name in ("edit_file", "write_file"):
            return True
        if mode == "auto":
            risk = get_tool_risk(name)
            if risk == ToolRisk.LOW:
                return True
            if risk == ToolRisk.CRITICAL:
                return False
        decision = self._perms.resolve(name, args, agent="cozmo")
        if decision == "allow":
            return True
        if decision == "deny":
            return False
        risk = get_tool_risk(name)
        if risk == ToolRisk.CRITICAL:
            return False
        cb = permission_callback or self._permission_callback
        if cb:
            return cb(name, args)
        return False

    def _sanitize(self, text: str) -> str:
        if len(text) > self.max_tool_output:
            head = self.max_tool_output // 3
            tail = self.max_tool_output - head
            text = (
                text[:head]
                + f"\n... [{len(text) - self.max_tool_output} chars truncated] ...\n"
                + text[-tail:]
            )
        return text

    def _normalize_result(self, name: str, result: str) -> str:
        if not result or not result.strip():
            return f"Error: {name} returned empty output"
        if "permission denied" in result.lower():
            return f"Error: {name} — permission denied. Try a different approach."
        if "timed out" in result.lower() or "timeout" in result.lower():
            return f"Error: {name} timed out. Try a simpler query or different tool."
        return result

    # ── diff / tracing (utility, kept public for dedup path) ───────────

    @staticmethod
    def compute_diff(name: str, args: dict) -> dict | None:
        if name == "edit_file":
            old = (args.get("old_text") or "").splitlines(keepends=True)
            new = (args.get("new_text") or "").splitlines(keepends=True)
            diff = list(
                difflib.unified_diff(
                    old, new, fromfile=args.get("path", "?"), tofile=args.get("path", "?"), n=3
                )
            )
            text = "".join(diff[2:]) if len(diff) > 2 else ""
            added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
            return {"text": text, "added": added, "removed": removed}
        if name == "write_file":
            new = (args.get("content") or "").splitlines()
            return {
                "text": "\n".join(f"+{l}" for l in new),
                "added": len(new),
                "removed": 0,
            }
        return None

    def record_tool_call(
        self,
        step_idx: int,
        name: str,
        args: dict,
        result: str,
        latency_ms: float,
        success: bool,
        error: str | None = None,
        fallback_used: str | None = None,
        trace=None,
    ):
        if trace is None:
            return
        from .trace import StepTrace, ToolCallTrace

        while len(trace.steps) <= step_idx:
            trace.steps.append(StepTrace(step=len(trace.steps)))
        step = trace.steps[step_idx]
        step.tool_calls.append(
            ToolCallTrace(
                name=name,
                args=dict(args),
                result_preview=(result or "")[:200],
                latency_ms=round(latency_ms, 2),
                success=success,
                error=error,
                fallback_used=fallback_used,
            )
        )
