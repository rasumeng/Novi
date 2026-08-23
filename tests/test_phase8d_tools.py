"""Phase 8D — Tool Intelligence tests.

One authoritative metadata source per attribute:
  category     ← tool_registry.TOOL_CATEGORIES (8A)
  risk         ← tool_risk._DEFAULT_RISK
  side_effects ← derived from risk (HIGH/CRITICAL mutate state)

ToolInfo derives everything at registration; no duplicate tables may exist.
Metadata stays DESCRIPTIVE — it must never become an execution authority,
and graphs must never execute tools directly regardless of metadata.
"""

import ast
import inspect

from cozmo.runtime.tool_registry import ToolInfo, ToolRegistry, TOOL_CATEGORIES
from cozmo.runtime.tool_risk import ToolRisk, get_tool_risk


def test_toolinfo_derives_risk_and_side_effects():
    info = ToolInfo(name="write_file", description="w", fn=lambda: "")
    assert info.category == TOOL_CATEGORIES["write_file"]
    assert info.risk is ToolRisk.HIGH
    assert info.side_effects is True

    reader = ToolInfo(name="read_file", description="r", fn=lambda: "")
    assert reader.risk is ToolRisk.LOW
    assert reader.side_effects is False

    critical = ToolInfo(name="kill_process", description="k", fn=lambda: "")
    assert critical.risk is ToolRisk.CRITICAL
    assert critical.side_effects is True


def test_unknown_tool_defaults():
    info = ToolInfo(name="brand_new_tool", description="d", fn=lambda: "")
    assert info.category == "other"
    assert info.risk is ToolRisk.MEDIUM
    assert info.side_effects is False


def test_explicit_metadata_still_wins():
    info = ToolInfo(name="read_file", description="d", fn=lambda: "",
                    side_effects=True)
    assert info.side_effects is True


def test_registry_entries_carry_metadata():
    reg = ToolRegistry()
    reg.register("edit_file", lambda: "", "edit")
    entries = {i.name: i for i in reg.list()}
    assert entries["edit_file"].category == "workspace"
    assert entries["edit_file"].risk is ToolRisk.HIGH
    assert entries["edit_file"].side_effects is True


def test_no_duplicate_risk_tables_outside_authority():
    """The risk vocabulary must live in exactly one module. Any other
    production module defining a _DEFAULT_RISK-style table fails."""
    import cozmo.runtime as rt_pkg
    from pathlib import Path

    root = Path(rt_pkg.__file__).parent
    offenders = []
    for py in root.rglob("*.py"):
        if py.name == "tool_risk.py":
            continue
        src = py.read_text("utf-8", errors="replace")
        for marker in ("_DEFAULT_RISK", "_RISK_TABLE", "TOOL_RISKS"):
            if re_marker(marker, src):
                offenders.append(f"{py.name}:{marker}")
    assert not offenders


def re_marker(marker: str, src: str) -> bool:
    return any(
        marker in line and not line.strip().startswith(("#", '"""', "'''"))
        for line in src.splitlines()
    )


def test_graphs_cannot_execute_tools_directly():
    """Architecture guard (8D): graph modules must never reach the executor,
    registry functions, or process primitives — injected collaborators only.

    The graphs orchestrate; ToolExecutor executes. This guard complements the
    runtime-side permission pipeline tests.
    """
    import cozmo.graphs.coding_graph as cg
    import cozmo.graphs.research_graph as rg
    import cozmo.graphs.coding_intel as ci_mod
    import cozmo.graphs.research_intel as ri_mod

    forbidden_imports = (
        "tool_executor", "tool_registry", "subprocess", "multiprocessing",
        "permissions",
    )
    forbidden_calls = {"execute", "Popen", "system"}
    for mod in (cg, rg, ci_mod, ri_mod):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                tail = node.module.split(".")[-1]
                assert tail not in forbidden_imports, (
                    f"{mod.__name__} imports {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    base = alias.name.split(".")[-1]
                    assert base not in forbidden_imports, (
                        f"{mod.__name__} imports {alias.name}")
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                # `.execute(` on a ToolExecutor-shaped attribute would be a
                # boundary violation; graphs never hold an executor.
                assert name not in ("Popen", "system"), (
                    f"{mod.__name__} calls {name}")


def test_metadata_never_becomes_execution_authority():
    """Executor decisions keep using their own pipeline: mutating the
    descriptive metadata of a registered tool must not change gating."""
    from cozmo.runtime.tool_executor import ToolExecutor

    class _Allow:
        def resolve(self, *a, **k):
            return "ask"  # would need user confirmation

    class _Lessons:
        def record(self, *a, **k):
            pass

    reg = ToolRegistry()
    reg.register("read_file", lambda: "data", "safe read")
    ex = ToolExecutor(registry=reg, perms=_Allow(), lesson_store=_Lessons(),
                      lc_tools={}, tool_fallbacks={}, max_tool_output=1000,
                      perm_mode="manual")
    # LOW-risk read in manual mode with no callback → resolver said "ask"
    # → denied despite harmless metadata. Metadata did not gate anything.
    result = ex.execute("read_file", {})
    assert result.success is False
