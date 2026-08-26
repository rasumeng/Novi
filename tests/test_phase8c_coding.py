"""Phase 8C — Agentic Coding tests.

Real verification through the frozen boundary:

VerificationReport (8C.1)
  - bounded tails, structured exit-code authority, environment vs
    implementation vs permission classification (8C.5).

Boundary discipline (8C.2)
  - the graph NEVER executes commands itself; verification flows through
    ToolExecutor.execute() with its permission/risk pipeline intact
    (destructive commands stay gated).

Repair loop (8C.6/8C.7)
  - failing verification feeds REAL failure output into the next attempt;
    retries are bounded; cancellation terminates immediately.

Workspace confinement (8C.3) + structured shell results (8C.4)
  - write/edit reject absolute escapes and traversal; shell runs pinned to
    the workspace root; exit codes travel as data, never parsed prose.
"""

import ast
import inspect

import pytest

from novi.graphs import CodingGraph
from novi.graphs import coding_intel as ci
from novi.graphs.coding_intel import VerificationReport


# ── helpers ───────────────────────────────────────────────────────────────


class _FakeToolResult:
    def __init__(self, output="", success=True, error=None,
                 latency_ms=12.5, structured=None):
        self.output = output
        self.success = success
        self.error = error
        self.latency_ms = latency_ms
        self.structured = structured or {}


class _StubModel:
    def __init__(self, answer="implemented"):
        self.answer = answer
        self.calls = 0

    def invoke(self, msgs):
        self.calls += 1
        return type("R", (), {"content": self.answer})()


def _report(passed=False, classification="implementation", exit_code=1,
            command="python -m pytest -q", stdout="", stderr=""):
    return VerificationReport(
        kind="test", exit_code=exit_code, stdout_tail=stdout,
        stderr_tail=stderr, duration_ms=10.0, passed=passed,
        command=command, classification=classification)


_EDIT_EVENTS = [("tool_call", "write_file", {"path": "a.py"}, "c1"),
                ("tool_result", "write_file", "[ok]", "c1",
                 {"text": "+++ a.py", "added": 1, "removed": 0})]


def _state(**kw):
    state = {
        "user_input": "add a logging helper",
        "analysis": None,
        "retrieval_plan": None,
        "system_prompt": "system",
        "plan_step_index": 0,
        "answer": "",
        "stop_reason": "",
        "attempt": 0,
        "max_attempts": 2,
    }
    state.update(kw)
    return state


# ── VerificationReport construction & classification ──────────────────────


def test_report_from_structured_payload():
    tr = _FakeToolResult(structured={
        "exit_code": 2, "stdout_tail": "2 failed, 8 passed",
        "stderr_tail": "", "duration_ms": 1500.0,
        "timed_out": False, "blocked": False,
    })
    r = ci.report_from_tool_result(tr, command="pytest")
    assert r.exit_code == 2
    assert r.passed is False
    assert r.classification == "implementation"
    assert r.stdout_tail == "2 failed, 8 passed"


def test_report_tails_bounded():
    tr = _FakeToolResult(structured={
        "exit_code": 1, "stdout_tail": "x" * (ci.TAIL_CHARS + 5000),
        "stderr_tail": "", "duration_ms": 1.0,
        "timed_out": False, "blocked": False,
    })
    r = ci.report_from_tool_result(tr)
    assert len(r.stdout_tail) == ci.TAIL_CHARS


def test_environment_failure_classified():
    tr = _FakeToolResult(output="Error: [WinError 2] The system cannot find "
                                "the file specified", success=False)
    r = ci.report_from_tool_result(tr, command="missing_runner --version")
    assert r.classification == "environment"
    assert r.passed is False


def test_permission_denial_classified():
    tr = _FakeToolResult(
        output="Error: the user DENIED permission for run_command.",
        success=False)
    r = ci.report_from_tool_result(tr)
    assert r.classification == "permission_denied"


def test_blocked_command_classified():
    tr = _FakeToolResult(structured={
        "exit_code": None, "stdout_tail": "", "stderr_tail": "",
        "duration_ms": 0.0, "timed_out": False, "blocked": True,
    })
    r = ci.report_from_tool_result(tr, command="rm -rf /")
    assert r.classification == "permission_denied"
    assert r.exit_code is None


def test_overall_priority_environment_over_implementation():
    passed, cls = ci.overall([_report(classification="environment"),
                              _report()])
    assert passed is False and cls == "environment"
    passed, cls = ci.overall([_report(), _report(passed=True)])
    assert passed is False and cls == "implementation"
    passed, cls = ci.overall([])
    assert passed is True and cls == ""


# ── repair feedback (8C.6) ────────────────────────────────────────────────


def test_repair_feedback_carries_real_failure_output():
    fb = ci.build_repair_feedback([
        _report(stdout="FAILED tests/test_auth.py::test_login",
                stderr="AssertionError: expected 200 got 500")])
    assert "tests/test_auth.py::test_login" in fb
    assert "expected 200 got 500" in fb
    assert "Exit code" in fb


def test_repair_feedback_bounded():
    big = _report(stdout="y" * 100000)
    fb = ci.build_repair_feedback([big, big, big])
    assert len(fb) <= ci.MAX_FEEDBACK_CHARS


def test_repair_feedback_empty_when_passed():
    assert ci.build_repair_feedback([_report(passed=True, exit_code=0)]) == ""


# ── graph repair loop ─────────────────────────────────────────────────────


def test_failing_verification_triggers_feedback_repair_and_reverify():
    attempts = []
    verifications = []

    def run_loop(state):
        attempts.append({
            "attempt": state.get("attempt", 0),
            "repair_context": state.get("repair_context") or "",
        })
        return (list(_EDIT_EVENTS), "edited files", "completed", True)

    def verify(state):
        n = len(verifications)
        verifications.append(1)
        if n == 0:
            return [_report(stdout="1 failed, 2 passed")]
        return [_report(passed=True, exit_code=0, stdout="3 passed")]

    g = CodingGraph(run_loop=run_loop, verify=verify, max_attempts=2)
    result = g.run(_state())

    assert len(attempts) == 2, "failure must schedule exactly one repair"
    assert attempts[0]["repair_context"] == ""
    assert "1 failed, 2 passed" in attempts[1]["repair_context"], (
        "attempt 2 must receive attempt 1's actual failure output")
    assert len(verifications) == 2, "verification must re-run after repair"
    assert result["completion_reason"] == "completed"
    assert result["verification_passed"] is True
    phases = [e["phase"] for e in result["stream_events"]]
    assert "verifying" in phases
    assert "verification_failed" in phases
    assert "retrying" in phases


def test_retry_limit_enforced_without_infinite_loop():
    implements = []
    verifications = []

    def run_loop(state):
        implements.append(1)
        return (list(_EDIT_EVENTS), "edited", "completed", True)

    def verify(state):
        verifications.append(1)
        return [_report(stdout=f"still failing #{len(verifications)}")]

    g = CodingGraph(run_loop=run_loop, verify=verify, max_attempts=2)
    result = g.run(_state())

    assert len(implements) == 2
    assert len(verifications) == 2
    assert result["completion_reason"] == "verification_failed"


def test_environment_failure_terminates_without_blind_edits():
    implements = []

    def run_loop(state):
        implements.append(1)
        return (list(_EDIT_EVENTS), "edited", "completed", True)

    def verify(state):
        return [_report(classification="environment",
                        stderr="pytest: executable not found")]

    g = CodingGraph(run_loop=run_loop, verify=verify)
    result = g.run(_state())

    assert len(implements) == 1, "environment failure must NOT trigger repair"
    assert result["completion_reason"] == "environment_error"
    kinds = [e.kind for e in result.get("errors") or []]
    assert "environment" in kinds


def test_permission_denied_verification_terminates():
    implements = []

    def run_loop(state):
        implements.append(1)
        return (list(_EDIT_EVENTS), "edited", "completed", True)

    def verify(state):
        return [_report(classification="permission_denied", exit_code=None)]

    g = CodingGraph(run_loop=run_loop, verify=verify)
    result = g.run(_state())
    assert len(implements) == 1
    assert result["completion_reason"] == "permission_denied"


def test_passing_verification_finalizes_immediately():
    implements = []
    verifications = []

    def run_loop(state):
        implements.append(1)
        return (list(_EDIT_EVENTS), "edited", "completed", True)

    def verify(state):
        verifications.append(1)
        return [_report(passed=True, exit_code=0)]

    g = CodingGraph(run_loop=run_loop, verify=verify)
    result = g.run(_state())
    assert len(implements) == 1 and len(verifications) == 1
    assert result["completion_reason"] == "completed"


def test_no_edits_skips_verification_honestly():
    verifies = []

    def run_loop(state):
        return ([("token", "explanation")], "explanation", "completed", True)

    def verify(state):
        verifies.append(1)
        return []

    g = CodingGraph(run_loop=run_loop, verify=verify)
    result = g.run(_state())
    assert verifies == [], "explaining code must not trigger verification"
    assert result.get("verification_skipped") == "no_edits"
    assert result["completion_reason"] == "completed"


def test_cancellation_during_analysis_terminates_cleanly():
    stop = {"flag": False}

    def probe():
        return stop["flag"]

    def run_loop(state):
        stop["flag"] = True  # cancel fires between implement and analyze
        return (list(_EDIT_EVENTS), "edited", "completed", True)

    def verify(state):
        return [_report(stdout="1 failed")]

    g = CodingGraph(run_loop=run_loop, verify=verify)
    result = g.run(_state(should_stop=probe))
    assert result["completion_reason"] == "stopped"


def test_metrics_track_attempts_and_edit_size():
    captured = {}

    def run_loop(state):
        return (list(_EDIT_EVENTS), "edited", "completed", True)

    def verify(state):
        return [_report(passed=True, exit_code=0)]

    g = CodingGraph(run_loop=run_loop, verify=verify)
    result = g.run(_state())
    captured = result["metrics"]
    assert captured["attempts"] == 1
    assert captured["verifications"] == 1
    assert captured["edits"] == 1
    assert captured["diff_added"] == 1
    assert "a.py" in captured["files"]


# ── boundary discipline (8C.2) ────────────────────────────────────────────


def test_graph_never_imports_subprocess_or_direct_execution():
    """AST guard extension: the coding/research graphs may not touch process
    execution primitives — ToolExecutor is the sole execution gate."""
    import novi.graphs.coding_graph as cg
    import novi.graphs.research_graph as rg
    import novi.graphs.coding_intel as cint
    import novi.graphs.research_intel as rint

    forbidden_modules = ("subprocess", "os.system", "pty", "multiprocessing")
    forbidden_calls = {"system", "popen", "Popen", "run", "check_output"}
    for mod in (cg, rg, cint, rint):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in (
                        "subprocess", "multiprocessing", "pty"), (
                        f"{mod.__name__} imports {alias.name}")
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_modules), (
                    f"{mod.__name__} imports {node.module}")
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or \
                    getattr(node.func, "attr", None)
                # 'run' is allowed on models/messages but never as a bare
                # subprocess-style call from graph modules.
                if name == "Popen":
                    raise AssertionError(f"{mod.__name__} calls Popen")


def test_verification_flows_through_tool_executor_pipeline():
    """End-to-end: registry → ToolExecutor → workspace-pinned shell runner,
    with the structured payload surviving sanitization."""
    import tempfile
    from pathlib import Path

    from novi.runtime.tool_executor import ToolExecutor
    from novi.runtime.tool_registry import ToolRegistry
    from novi.tools import TOOL_REGISTRY
    from novi.tools.file_ops import set_allowed_root

    class _Allow:
        def resolve(self, name, args, agent="novi"):
            return "allow"

    class _Lessons:
        def record(self, *a, **k):
            pass

    with tempfile.TemporaryDirectory() as tmp:
        set_allowed_root(tmp)
        try:
            reg = ToolRegistry()
            reg.register("run_command", TOOL_REGISTRY["run_command"])
            ex = ToolExecutor(
                registry=reg, perms=_Allow(), lesson_store=_Lessons(),
                lc_tools={}, tool_fallbacks={}, max_tool_output=8000,
                perm_mode="bypass")

            result = ex.execute("run_command", {"command": "cmd /c exit 3"})
            assert result.success is False
            assert result.structured is not None, (
                "structured shell contract must survive the pipeline")
            assert result.structured["exit_code"] == 3
            report = ci.report_from_tool_result(result, command="cmd /c exit 3")
            assert report.exit_code == 3
            assert report.passed is False
            assert report.classification == "implementation"
        finally:
            set_allowed_root(Path.cwd())


# ── workspace confinement (8C.3) ──────────────────────────────────────────


@pytest.fixture()
def workspace(tmp_path):
    from novi.tools.file_ops import set_allowed_root

    old = None
    set_allowed_root(tmp_path)
    try:
        yield tmp_path
    finally:
        import os
        set_allowed_root(os.getcwd())


def test_write_file_confined_to_workspace(workspace):
    from novi.tools import TOOL_REGISTRY

    ok = TOOL_REGISTRY["write_file"](path="src/new.py", content="x = 1\n")
    assert "Written" in ok
    assert (workspace / "src" / "new.py").exists()

    outside = str(workspace.parent / "escape.txt")
    denied = TOOL_REGISTRY["write_file"](path=outside, content="nope")
    assert denied.startswith("Error:")
    assert not (workspace.parent / "escape.txt").exists()

    traversal = TOOL_REGISTRY["write_file"](path="../escape2.txt", content="nope")
    assert traversal.startswith("Error:")


def test_edit_file_confined_to_workspace(workspace):
    from novi.tools import TOOL_REGISTRY

    TOOL_REGISTRY["write_file"](path="mod.py", content="value = 1\n")
    ok = TOOL_REGISTRY["edit_file"](path="mod.py", old_text="value = 1",
                                    new_text="value = 2")
    assert "Replaced" in ok

    outside = str(workspace.parent / "outside.py")
    denied = TOOL_REGISTRY["edit_file"](path=outside, old_text="a", new_text="b")
    assert denied.startswith("Error: path outside allowed directory")


def test_run_command_pinned_to_workspace(workspace):
    from novi.tools.code_ops import _run_command_structured

    out = _run_command_structured('python -c "import os; print(os.getcwd())"')
    assert out.data["exit_code"] == 0
    printed = out.text.strip().splitlines()[0].strip()
    assert printed.lower() == str(workspace).lower()


def test_run_command_destructive_remains_gated(workspace):
    from novi.tools.code_ops import _run_command_structured

    out = _run_command_structured("rm -rf something")
    assert out.data["blocked"] is True
    assert "blocked for safety" in out.text


def test_run_command_missing_executable_is_environment(workspace):
    from novi.tools.code_ops import _run_command_structured

    out = _run_command_structured("definitely_not_a_real_exe_9x7 --version")
    assert out.data["exit_code"] is None
    assert not out.data["timed_out"]
    report = ci.report_from_tool_result(_FakeToolResult(
        output=out.text, success=False, structured=out.data))
    assert report.classification == "environment"


# ── fixture repositories: real pytest through the whole stack ─────────────


def _make_project(root, test_body):
    import sys

    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n",
                                 encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "__init__.py").write_text("", encoding="utf-8")
    (tests / "test_app.py").write_text(test_body, encoding="utf-8")
    # Same-interpreter pytest command (system `python` may lack pytest).
    exe = sys.executable.replace("\\", "/")
    if " " in exe:
        exe = f'"{exe}"'
    return f"{exe} -m pytest -q"


def _verify_workspace_commands(commands, workdir):
    """Run verification exactly the way the runtime collaborator does."""
    from novi.runtime.tool_executor import ToolExecutor
    from novi.runtime.tool_registry import ToolRegistry
    from novi.tools import TOOL_REGISTRY
    from novi.tools.file_ops import set_allowed_root

    class _Allow:
        def resolve(self, name, args, agent="novi"):
            return "allow"

    class _Lessons:
        def record(self, *a, **k):
            pass

    set_allowed_root(workdir)
    try:
        reg = ToolRegistry()
        reg.register("run_command", TOOL_REGISTRY["run_command"])
        ex = ToolExecutor(registry=reg, perms=_Allow(), lesson_store=_Lessons(),
                          lc_tools={}, tool_fallbacks={},
                          max_tool_output=12000, perm_mode="bypass")
        reports = []
        for cmd in commands:
            tr = ex.execute("run_command", {"command": cmd})
            reports.append(ci.report_from_tool_result(tr, command=cmd))
            if not reports[-1].passed:
                break
        return reports
    finally:
        import os
        set_allowed_root(os.getcwd())


def test_fixture_repo_passing_tests_finalize(tmp_path):
    cmd = _make_project(tmp_path, "from app import add\n\n"
                        "def test_add():\n    assert add(1, 2) == 3\n")
    reports = _verify_workspace_commands([cmd], tmp_path)
    assert reports and reports[0].passed is True
    assert reports[0].exit_code == 0


def test_fixture_repo_failing_tests_produce_repair_context(tmp_path):
    cmd = _make_project(tmp_path, "from app import add\n\n"
                        "def test_add():\n    assert add(1, 2) == 4\n")
    reports = _verify_workspace_commands([cmd], tmp_path)
    assert reports[0].passed is False
    assert reports[0].exit_code != 0
    assert reports[0].classification == "implementation"

    feedback = ci.build_repair_feedback(reports)
    assert "test_app.py" in feedback, (
        "repair context must contain the actual failing test")
