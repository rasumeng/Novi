"""Task 7 — Guardrails: no workload-specific step budgets, safety rail invariant.

Fails CI if workload-specific step budgets appear.
"""

import pathlib
import re


def test_no_workload_specific_max_steps():
    # Complexity: must not branch max_steps by workload
    text = pathlib.Path("novi/orchestrator/complexity.py").read_text(encoding="utf-8")
    lower = text.lower()
    # Guard: no per-workload max_steps map/branch like general=8 research=12
    assert not re.search(r"general.*\b8\b.*research.*\b12\b", text, re.I | re.S), \
        "per-workload step budget pattern found in complexity.py"
    # Forbid workload dict mapping to steps like {"general": 8, "research": 12} - irrespective of workload keyword, re.S for multiline
    assert not re.search(r'"general"\s*:\s*\d+|"research"\s*:\s*\d+', text, re.S), \
        "workload-specific max_steps dict mapping forbidden in complexity.py"
    assert not re.search(r"'general'\s*:\s*\d+|'research'\s*:\s*\d+", text, re.S), \
        "workload-specific max_steps dict mapping forbidden in complexity.py"
    # Workload-specific max_steps branching forbidden - use re.S, no has_workload gate
    assert not re.search(r"workload.{0,80}max_steps|max_steps.{0,80}workload", lower, re.S), \
        "workload-specific max_steps branching forbidden in complexity.py"
    # Broader check without requiring `if` - any workload dict mapping like general.*max_steps
    assert not re.search(r'"general"\s*:\s*\d+.*max_steps|max_steps.*\"general\"|general.{0,80}max_steps|research.{0,80}max_steps', lower, re.S), \
        "workload-specific max_steps mapping forbidden in complexity.py"
    # Also check quoted dict budgets like {"general": 8}
    assert not re.search(r'"general"\s*:\s*8|"research"\s*:\s*12', text, re.S), \
        "workload dict budget forbidden in complexity.py"
    # Ensure max_steps appears only as safety rail comment, not as completion boundary
    # complexity.py should declare safety rail invariant
    assert "safety rail" in lower, "complexity.py must contain safety rail comment"

    # Runtime: scan for per-workload step budgets
    rt = pathlib.Path("novi/runtime/runtime.py").read_text(encoding="utf-8")
    rt_lower = rt.lower()
    # No workload.*max_steps assignment pattern - with re.S, limited distance
    assert not re.search(r"workload.{0,80}max_steps|max_steps.{0,80}workload", rt_lower, re.S), \
        "per-workload max_steps assignment forbidden in runtime.py"
    # Max steps dict mapping forbidden like {"general": 8, "research": 12} - separate, no vacuous or
    assert not re.search(r"general.{0,80}max_steps|research.{0,80}max_steps|max_steps.{0,80}general|max_steps.{0,80}research", rt_lower, re.S), \
        "per-workload max_steps mapping forbidden"
    # Dict mapping irrespective of workload keyword
    assert not re.search(r'"general"\s*:\s*\d+|"research"\s*:\s*\d+', rt, re.S), \
        "per-workload max_steps dict mapping forbidden in runtime.py"
    assert not re.search(r"'general'\s*:\s*\d+|'research'\s*:\s*\d+", rt, re.S), \
        "per-workload max_steps dict mapping forbidden in runtime.py"
    # Also forbid quoted dict budget
    assert not re.search(r'"general"\s*:\s*8|"research"\s*:\s*12', rt, re.S), \
        "per-workload max_steps dict mapping forbidden in runtime.py"
    # Ensure safety rail phrase exists (invariant: step exhaustion is safety not completion)
    assert "safety rail" in rt_lower, "runtime.py must contain 'safety rail' comment"
    assert "not completion" in rt_lower or "not completion boundary" in rt_lower, \
        "runtime.py must contain safety rail completion phrase"

    # Also check react_attempt safety rail
    ra = pathlib.Path("novi/runtime/react_attempt.py").read_text(encoding="utf-8")
    assert "safety rail" in ra.lower(), "react_attempt.py must contain safety rail comment"
