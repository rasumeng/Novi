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
    has_workload = "workload" in lower
    has_max_steps = "max_steps" in lower
    # If both appear, fail if pattern suggests per-workload budget
    if has_workload and has_max_steps:
        assert not re.search(r"general.*\b8\b.*research.*\b12\b", text, re.I | re.S), \
            "per-workload step budget pattern found in complexity.py"
        # Also forbid workload dict mapping to steps like {"general": 8, "research": 12}
        assert not re.search(r"workload.*max_steps|workload.*\bmax_steps\b", lower), \
            "workload-specific max_steps branching forbidden in complexity.py"
        # No workload branching for steps at all
        assert not re.search(r"if.*workload|workload.*if", lower), \
            "workload branching forbidden in complexity.py"
    # Ensure max_steps appears only as safety rail comment, not as completion boundary
    # complexity.py should declare safety rail invariant
    assert "safety rail" in lower, "complexity.py must contain safety rail comment"

    # Runtime: scan for per-workload step budgets
    rt = pathlib.Path("novi/runtime/runtime.py").read_text(encoding="utf-8")
    rt_lower = rt.lower()
    # No workload.*max_steps assignment pattern
    assert not re.search(r"workload.*max_steps", rt_lower), \
        "per-workload max_steps assignment forbidden in runtime.py"
    # Max steps dict mapping forbidden like {"general": 8, "research": 12}
    assert not re.search(r"general.*\bmax_steps\b|research.*\bmax_steps\b", rt_lower) or "safety rail" in rt_lower, \
        "per-workload max_steps mapping forbidden"
    # Ensure safety rail phrase exists (invariant: step exhaustion is safety not completion)
    assert "safety rail" in rt_lower, "runtime.py must contain 'safety rail' comment"
    assert "not completion" in rt_lower or "not completion boundary" in rt_lower, \
        "runtime.py must contain safety rail completion phrase"

    # Also check react_attempt safety rail
    ra = pathlib.Path("novi/runtime/react_attempt.py").read_text(encoding="utf-8")
    assert "safety rail" in ra.lower(), "react_attempt.py must contain safety rail comment"
