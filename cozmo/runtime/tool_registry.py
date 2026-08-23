from dataclasses import dataclass
from typing import Callable
from langchain_core.tools import StructuredTool

from .tool_risk import ToolRisk, get_tool_risk

# ── Single source of truth for tool categories (Phase 8A) ────────────────
# Descriptive metadata only — never an execution authority. ToolExecutor
# remains the sole permission/risk/validation/execution pipeline; this table
# exists so the runtime, executor, and UI all observe identical categories.
# The former duplicate copies in runtime.py and tool_executor.py are gone.
TOOL_CATEGORIES: dict[str, str] = {
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


def tool_category(name: str) -> str:
    """Category for a tool name. Unknown tools are "other"."""
    return TOOL_CATEGORIES.get(name, "other")


@dataclass
class ToolInfo:
    """Registry entry with descriptive metadata (Phase 8A/8D).

    All metadata is DERIVED from the single authoritative tables
    (``TOOL_CATEGORIES`` here and the risk table in ``tool_risk``) —
    registration never passes it explicitly, so per-tool copies can never
    drift.

    Metadata is descriptive only. It is NEVER an execution authority:
    ToolExecutor remains the sole permission/risk/validation pipeline.
    """

    name: str
    description: str
    fn: Callable
    category: str = "other"
    risk: ToolRisk | None = None
    side_effects: bool | None = None

    def __post_init__(self):
        # Category is registry-derived descriptive metadata; registration
        # never needs to pass it explicitly.
        if not self.category or self.category == "other":
            self.category = TOOL_CATEGORIES.get(self.name, "other")
        # Risk derives from the one risk table; side_effects is a coarse,
        # honest flag: anything HIGH+ mutates state or executes commands.
        if self.risk is None:
            self.risk = get_tool_risk(self.name)
        if self.side_effects is None:
            self.side_effects = self.risk in (ToolRisk.HIGH, ToolRisk.CRITICAL)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolInfo] = {}

    def register(self, name: str, fn: Callable, description: str = "") -> None:
        self._tools[name] = ToolInfo(
            name=name,
            description=description or (fn.__doc__ or "").strip(),
            fn=fn,
        )

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolInfo | None:
        return self._tools.get(name)

    def list(self) -> list[ToolInfo]:
        return list(self._tools.values())

    def as_lc_tools(self) -> dict[str, StructuredTool]:
        wrapped: dict[str, StructuredTool] = {}
        for name, info in self._tools.items():
            try:
                wrapped[name] = StructuredTool.from_function(
                    func=info.fn, name=name, description=info.description.split("\n")[0],
                )
            except Exception:
                continue
        return wrapped
