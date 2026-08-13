"""Redaction for step/tool execution context (Milestone 5 Phase 6C).

Checkpoint persistence carries a *minimal, schema-constrained* slice of what a
step did. That slice must never write API keys, bearer tokens, Authorization
headers, cookies, credentials, or secret tool arguments to durable state.

This module is the single source: the runtime redacts before building the
``STEP_COMPLETED`` payload, so anything that reaches ``Checkpoint.tool_states``
at the composition root is already safe. Runtime-side only — it deliberately
imports no configuration, jobs, or orchestrator modules (architecture guard).
"""

from __future__ import annotations

import re

_SENSITIVE_KEY = re.compile(
    r"(api[_-]?key|apikey|token|secret|passw(or)?d|pwd|credential|authorization"
    r"|auth|cookie|set-cookie|bearer|session|private|jwt|ssh|access[_-]?key)"
    r".*",
    re.IGNORECASE,
)

_REDACTED = "<redacted>"

_MAX_ARGS_CHARS = 2000


def redact_value(value, *, _depth: int = 0) -> object:
    """Recursively mask secret-looking keys and truncate oversized strings.

    Dict keys matching the sensitive pattern are replaced with ``<redacted>``.
    Long string leaves are truncated so a single pathological tool result
    cannot bloat a checkpoint.
    """
    if _depth > 8:
        return _REDACTED
    if isinstance(value, dict):
        return {
            str(k): _REDACTED if _SENSITIVE_KEY.match(str(k)) else redact_value(v, _depth=_depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_value(v, _depth=_depth + 1) for v in value]
    if isinstance(value, str):
        if len(value) > _MAX_ARGS_CHARS:
            return value[:_MAX_ARGS_CHARS]
        return value
    return value


def build_tool_record(name: str, args: dict, output: str, *, success: bool = True) -> dict:
    """A single redacted tool invocation for checkpoint ``tool_states``.

    Args are recursively redacted; output is a bounded, redacted preview.
    The result is a plain JSON-serializable dict (no runtime/jobs imports).
    """
    result = (output or "")[:500]
    return {
        "name": name,
        "args": redact_value(args),
        "ok": bool(success),
        "result": result,
    }