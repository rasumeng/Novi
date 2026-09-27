"""Persist finalized run evidence to Brain once, with scoped provenance.

Phase 8: agent activity and long-term memory agree about what happened.

Contract:
- One Turn per finalized run (stable run id, conversation id, project scope).
- Public assistant messages joined once; private/tool messages never conflated.
- Distinct evidence classes: progress (report_progress), assistant speculation,
  tool text/denial/error, terminal outcome.
- Failed/cancelled runs keep user facts but do not imply successful tool effects.
- No-retention (``classify_request(...).retain == False``) skips ingestion.
- Coherence: tool outputs carry stable coordinates (run_id:call_id) and
  the surrounding qualification/correction lives in the same Turn assistant text,
  not split by a byte window. Segmentation overlap lives in jobs.py.
- Idempotent: second ingest of the same terminal state is a no-op.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone

from novi.brain.types import Turn
from novi.runtime.run_contracts import RunState, RunStatus
from novi.runtime.transcript import ContentBlockType, MessageRole

log = logging.getLogger("novi.services.run_memory")


def _should_retain(request_text: str) -> bool:
    try:
        from novi.runtime.knowledge_cycle import classify_request
        return bool(classify_request(request_text).retain)
    except Exception:
        return True


def _public_assistant_text(state: RunState) -> str:
    """Join all visible assistant TEXT blocks, preserving order.

    Hidden/assistant speculation that is not visible_to_user is excluded.
    Private reasoning is never reconstructed into the user transcript.
    """
    parts: list[str] = []
    for msg in state.transcript:
        if msg.role is MessageRole.ASSISTANT and msg.visible_to_user:
            for block in msg.blocks:
                if block.type is ContentBlockType.TEXT and block.text and block.text.strip():
                    parts.append(block.text.strip())
    if state.finished and state.terminal_reason:
        qualifier = state.status.value
        parts.append(f"[run {qualifier} {state.id}: {state.terminal_reason}]")
    elif state.finished:
        parts.append(f"[run {state.status.value} {state.id}]")
    return "\n\n".join(parts).strip()


def _tool_call_names(state: RunState) -> dict[str, str]:
    names: dict[str, str] = {}
    for msg in state.transcript:
        for block in msg.blocks:
            if block.type is ContentBlockType.TOOL_CALL and block.call_id and block.tool_name:
                names[block.call_id] = block.tool_name
    return names


def _tool_outputs(state: RunState) -> tuple[str, ...]:
    """Build distinct, actor-tagged tool evidence strings.

    Each entry is a single tool actor turn (``tool_outputs`` slot) with
    stable coordinates ``run_id:call_id`` so evidence validation can
    anchor to a durable location rather than a byte window.
    """
    call_names = _tool_call_names(state)
    out: list[str] = []
    for msg in state.transcript:
        if msg.role is not MessageRole.TOOL:
            continue
        for block in msg.blocks:
            if block.type is not ContentBlockType.TOOL_RESULT and block.result is None:
                continue
            if block.type is not ContentBlockType.TOOL_RESULT:
                continue
            result = block.result or {}
            status = str(result.get("status", "unknown"))
            output = str(result.get("output", "") or "")
            error = str(result.get("error", "") or "")
            call_id = str(block.call_id or result.get("call_id", "") or "")
            name = call_names.get(call_id, result.get("tool_name", "unknown_tool") or "unknown_tool")
            # Choose the primary text by status: succeeded uses output, others use error.
            if status == "succeeded":
                body = output
                header = f"[tool:{name} call_id:{call_id} status:succeeded run:{state.id}]"
            elif status == "denied":
                body = error or output
                header = f"[tool:{name} call_id:{call_id} status:denied run:{state.id}] (explicit deny)"
            elif status == "failed":
                body = error or output
                header = f"[tool:{name} call_id:{call_id} status:failed run:{state.id}]"
            elif status == "cancelled":
                body = error or output
                header = f"[tool:{name} call_id:{call_id} status:cancelled run:{state.id}]"
            elif status == "timed_out":
                body = error or output
                header = f"[tool:{name} call_id:{call_id} status:timed_out run:{state.id}]"
            elif status == "pending_permission":
                body = error or output
                header = f"[tool:{name} call_id:{call_id} status:pending_permission run:{state.id}]"
            else:
                body = output or error
                header = f"[tool:{name} call_id:{call_id} status:{status} run:{state.id}]"
            # Provenance/diff retained verbatim when present; never hidden.
            provenance = result.get("provenance")
            diff = result.get("diff")
            artifact_ids = result.get("artifact_ids")
            pieces = [header]
            if body:
                pieces.append(body)
            if diff:
                pieces.append(f"[diff:{diff}]")
            if provenance:
                pieces.append(f"[provenance:{provenance}]")
            if artifact_ids:
                pieces.append(f"[artifacts:{artifact_ids}]")
            # Outcome-qualified: failed runs must not imply success.
            # The header already encodes status, so no additional inference.
            out.append("\n".join(pieces))
    return tuple(out)


def build_turn(state: RunState) -> Turn | None:
    """Translate a finalized RunState into a single Brain Turn.

    Returns None when retention rules forbid it or the request is empty.
    The Turn carries project/global scope via ``project_id`` (empty == global).
    """
    req = state.request
    if not req.user_text or not req.user_text.strip():
        return None
    if not _should_retain(req.user_text):
        return None
    # Conversation history is carried into each new run for inference. Memory
    # must ingest only the current turn, not reattribute earlier evidence.
    for index, message in enumerate(state.transcript):
        if message.id == req.user_message_id and message.role is MessageRole.USER:
            state = replace(state, transcript=state.transcript[index:])
            break
    assistant = _public_assistant_text(state)
    tool_outputs = _tool_outputs(state)
    # Even failed/cancelled runs with no assistant progress may contain a
    # valid user fact (e.g. "my email is …"). Keep the user fact, but do not
    # synthesize an assistant claim of success.
    if not assistant and not tool_outputs and not req.user_text.strip():
        return None
    ts = state.updated_at or state.created_at or datetime.now(timezone.utc)
    # project/global scope as explicit metadata; workspace fallback preserves isolation.
    project_scope = (req.project_id or req.workspace or "").strip()
    return Turn(
        user=req.user_text.strip(),
        assistant=assistant,
        tool_outputs=tool_outputs,
        conversation_id=req.conversation_id,
        project_id=project_scope,
        timestamp=ts,
    )


def ingest_run(state: RunState, brain) -> bool:
    """Persist finalized run evidence once via ``brain.observe``.

    Idempotent within a process: a second call for the same terminal
    ``state.id`` is a no-op. Cross-process dedup is naturally handled by
    the conversation_store's append-only seq — callers must not re-ingest
    without checking ``state.id`` already appears in tool evidence,
    but this function also checks the last few turns for that marker.
    Returns True iff a Turn was observed.
    """
    if brain is None:
        return False
    if not state.finished:
        log.debug("run %s not terminal (%s) — skipping memory ingest", state.id, state.status.value)
        return False
    turn = build_turn(state)
    if turn is None:
        return False
    # Idempotence: if the same run_id already appears in recent tool evidence,
    # do not duplicate. Also prevents double ingestion after permission resume
    # where ``state`` is re-persisted.
    try:
        marker = f"run:{state.id}"
        # Check via durable store
        store = getattr(brain, "_conversation_store", None)
        recent = ()
        if store is not None:
            try:
                recent = store.turns(state.conversation_id, limit=8)
            except Exception:
                recent = ()
            for t in recent:
                if t.user == turn.user and t.assistant == turn.assistant and tuple(t.tool_outputs) == turn.tool_outputs:
                    return False
                if any(marker in (to or "") for to in (t.tool_outputs or ())):
                    return False
                if marker in (t.assistant or ""):
                    return False
        # Fallback for in-memory fakes that keep turns in brain.turns
        turns_mem = getattr(brain, "turns", None)
        if isinstance(turns_mem, list):
            for t in turns_mem[-8:]:
                if getattr(t, "user", None) == turn.user and getattr(t, "assistant", None) == turn.assistant and tuple(getattr(t, "tool_outputs", ()) or ()) == turn.tool_outputs:
                    return False
                if any(marker in (to or "") for to in (getattr(t, "tool_outputs", ()) or ())):
                    return False
                if marker in (getattr(t, "assistant", "") or ""):
                    return False
    except Exception:
        pass
    try:
        brain.observe(turn)
        return True
    except Exception:
        log.warning("run memory ingest failed for %s", state.id, exc_info=True)
        return False
