"""Phase 8C coding intelligence — real verification contracts.

Pure helpers owned by the graph layer. The CodingGraph decides WHEN
verification is needed and HOW to interpret results; every actual execution
still flows through ToolExecutor (permission/risk/sanitization boundaries).
Nothing here executes subprocesses, resolves models, reads configuration,
or persists anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_REPORTS = 6                  # retained VerificationReports per run
MAX_FEEDBACK_CHARS = 2500        # bounded repair-prompt injection
TAIL_CHARS = 4000                # per-stream tail bound inside a report
MAX_TRACKED_FILES = 16           # bounded changed-file metric

_EDIT_TOOLS = frozenset({"write_file", "edit_file", "create_project_file"})

# Structured shell output keys produced by the workspace-pinned runner.
SHELL_KEYS = ("exit_code", "stdout_tail", "stderr_tail", "duration_ms",
              "timed_out", "blocked")

_ENVIRONMENT_PATTERNS = (
    "is not recognized",
    "no such file or directory",
    "command not found",
    "cannot find the file",
    "cannot find the path",
    "is not an executable",
    "no module named pytest",
    "no module named pip",
)

_PERMISSION_MARKERS = (
    "denied permission",
    "permission denied",
)

# ── structured verification status vocabulary (Phase 8 remediation, E) ───
# Zero executed commands is NEVER a pass: the graph distinguishes an honest
# aggregate verdict from the mere absence of verification.
VS_VERIFIED = "verified"          # ≥1 command ran, all passed
VS_FAILED = "failed"              # ≥1 command ran, at least one failed
VS_UNAVAILABLE = "unavailable"    # verifier present but ZERO commands executed
VS_SKIPPED = "skipped"            # no verifier / no edits — by design


@dataclass(frozen=True)
class VerificationReport:
    """Bounded outcome of ONE verification command.

    ``stdout_tail`` / ``stderr_tail`` are hard-bounded so graph state can
    never grow unbounded through verbose test output. ``exit_code`` is None
    only when the process never ran (blocked / spawn failure / timeout).
    """

    kind: str                 # "test" | "lint" | "environment"
    exit_code: int | None
    stdout_tail: str
    stderr_tail: str
    duration_ms: float
    passed: bool
    command: str = ""
    classification: str = "implementation"
    # implementation | environment | permission_denied | timeout


def classify_output(exit_code: int | None, combined: str,
                    blocked: bool, timed_out: bool = False) -> str:
    """Deterministic failure taxonomy.

    permission_denied  — explicitly blocked/denied; terminal, no repair.
    environment        — missing interpreter/test runner/executable; terminal,
                         no repair (repairing project files cannot fix this).
    timeout            — the command exceeded its time budget. Phase 8
                         remediation (audit F): a timeout may be a slow suite,
                         deadlock, or infrastructure trouble and is NOT, by
                         itself, evidence of a code defect — it must never
                         blind-trigger code repair.
    implementation     — everything else (real test/build failures).
    """
    low = (combined or "").lower()
    if blocked or any(m in low for m in _PERMISSION_MARKERS):
        return "permission_denied"
    if timed_out:
        return "timeout"
    if any(p in low for p in _ENVIRONMENT_PATTERNS):
        return "environment"
    return "implementation"


def _tail(text: str) -> str:
    text = (text or "").strip()
    return text[-TAIL_CHARS:] if len(text) > TAIL_CHARS else text


def report_from_tool_result(result, command: str = "",
                            kind: str = "test") -> VerificationReport:
    """Build a VerificationReport from a ToolExecutor ToolResult.

    Prefers the STRUCTURED payload attached by the workspace-pinned shell
    runner (exit code is authoritative); falls back to the executor's own
    success normalization when no structure is available. Never parses
    human-readable formatting for success semantics.
    """
    structured = getattr(result, "structured", None) or {}
    output = getattr(result, "output", "") or ""
    error = getattr(result, "error", None) or ""

    blocked = bool(structured.get("blocked"))
    timed_out = bool(structured.get("timed_out"))
    exit_code = structured.get("exit_code")
    if exit_code is not None and not isinstance(exit_code, int):
        try:
            exit_code = int(exit_code)
        except (TypeError, ValueError):
            exit_code = None
    stdout = _tail(structured.get("stdout_tail") or "")
    stderr = _tail(structured.get("stderr_tail") or "")
    duration = float(structured.get("duration_ms") or
                     getattr(result, "latency_ms", 0.0) or 0.0)

    if not structured:
        # Legacy tools without structured payloads: trust the executor's
        # normalized success signal; keep the raw text as the tail evidence.
        stdout = _tail(output)
        timed_out = "timed out" in output.lower()

    combined = f"{stdout}\n{stderr}\n{output}\n{error}"
    classification = classify_output(exit_code, combined, blocked,
                                     timed_out=timed_out)

    if blocked:
        passed = False
        exit_code = None
    elif timed_out:
        passed = False
    elif exit_code is not None:
        passed = exit_code == 0
    else:
        passed = bool(getattr(result, "success", False)) and \
            classification == "implementation"

    return VerificationReport(
        kind=kind,
        exit_code=exit_code,
        stdout_tail=stdout,
        stderr_tail=stderr,
        duration_ms=round(duration, 2),
        passed=bool(passed),
        command=(command or "")[:300],
        classification=classification,
    )


def overall(reports: list[VerificationReport]) -> tuple[bool, str]:
    """Aggregate verdict: ``(all_passed, blocking_classification)``.

    Priority: permission_denied > timeout > environment > implementation —
    a missing interpreter or a hung command outranks a test failure as the
    thing worth reporting (Phase 8 remediation, audit F adds timeout).
    """
    if not reports:
        return True, ""
    for cls in ("permission_denied", "timeout", "environment"):
        if any(r.classification == cls for r in reports):
            return False, cls
    return all(r.passed for r in reports), "implementation"


def build_repair_feedback(reports: list[VerificationReport]) -> str:
    """Bounded, factual failure summary for the repair prompt (8C.6).

    Attempt N+1 MUST see what attempt N saw fail — but bounded: commands,
    exit codes, and tails only, capped at MAX_FEEDBACK_CHARS.
    """
    failed = [r for r in reports if not r.passed]
    if not failed:
        return ""
    parts = [
        "The previous attempt's verification FAILED. Fix the reported "
        "failures before considering the task complete."
    ]
    budget = MAX_FEEDBACK_CHARS - len(parts[0])
    for r in failed[:3]:
        block = (
            f"\n\nVerification ({r.kind}) failed.\nCommand: {r.command}\n"
            f"Exit code: {r.exit_code}\n"
            f"Output:\n{r.stdout_tail}"
        )
        if r.stderr_tail:
            block += f"\nErrors:\n{r.stderr_tail}"
        if len(block) > budget // 2:
            block = block[: max(400, budget // 2)] + "\n…[truncated]"
        parts.append(block)
        budget -= len(block)
        if budget <= 200:
            break
    return "\n".join(parts)[:MAX_FEEDBACK_CHARS]


# ── edit observation (8C.8 avoid unnecessary edits) ──────────────────────

_DIFF_FILE_RE = re.compile(r"^\+\+\+ (.+)$", re.MULTILINE)


def scan_edits(events: list) -> dict:
    """Extract bounded edit metrics from one implement attempt's events.

    Events are the runtime's replay tuples; edit/write tool_results carry a
    diff dict (added/removed/text). Returns counts only — never reverts.
    """
    files: list[str] = []
    added = removed = edits = 0
    for ev in events or []:
        if not (isinstance(ev, tuple) and ev and ev[0] == "tool_result"):
            continue
        name = ev[1] if len(ev) > 1 else ""
        if name not in _EDIT_TOOLS:
            continue
        edits += 1
        diff = ev[4] if len(ev) > 4 else None
        if isinstance(diff, dict):
            added += int(diff.get("added") or 0)
            removed += int(diff.get("removed") or 0)
            text = diff.get("text") or ""
            for m in _DIFF_FILE_RE.findall(text):
                path = m.strip().split("\t")[0]
                if path and path not in files and len(files) < MAX_TRACKED_FILES:
                    files.append(path)
    return {
        "edits": edits,
        "files": files,
        "diff_added": added,
        "diff_removed": removed,
    }


def merge_metrics(total: dict, attempt_metrics: dict) -> dict:
    """Accumulate per-attempt edit metrics into the run-level bounded total."""
    total = dict(total or {})
    files = list(total.get("files") or [])
    for f in attempt_metrics.get("files") or []:
        if f not in files and len(files) < MAX_TRACKED_FILES:
            files.append(f)
    total["files"] = files
    total["edits"] = int(total.get("edits") or 0) + int(attempt_metrics.get("edits") or 0)
    total["diff_added"] = int(total.get("diff_added") or 0) + \
        int(attempt_metrics.get("diff_added") or 0)
    total["diff_removed"] = int(total.get("diff_removed") or 0) + \
        int(attempt_metrics.get("diff_removed") or 0)
    total["verifications"] = int(total.get("verifications") or 0)
    return total


def had_edits(events: list) -> bool:
    """Whether an implement attempt actually modified files (8C gating).

    Verification runs only against REAL edits — explaining a concept or
    answering a question with coding intent must not trigger a test suite.
    """
    for ev in events or []:
        if isinstance(ev, tuple) and ev and ev[0] == "tool_call":
            if (len(ev) > 1 and ev[1]) in _EDIT_TOOLS:
                return True
    return False
