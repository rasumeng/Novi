"""Single authoritative provider/tool loop for one agent run."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable, Protocol
from uuid import uuid4

from novi.runtime.context_builder import (
    ContextBuilder, ContextInputs, ContextOverflowError, TranscriptCompactor,
)
from novi.runtime.run_contracts import (
    ModelTurn, RunEvent, RunEventType, RunState, RunStatus, ToolCall,
    ToolResult, ToolResultStatus,
)
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage


class TurnProvider(Protocol):
    def stream(self, transcript: tuple[TranscriptMessage, ...]) -> Iterable[ModelTurn]: ...


class ToolDispatcher(Protocol):
    def dispatch(self, call: ToolCall) -> ToolResult: ...


@dataclass(frozen=True)
class AgentLoopLimits:
    max_model_turns: int = 20
    max_tool_calls: int = 100
    max_progress_messages: int = 20


@dataclass(frozen=True)
class AgentLoopResult:
    state: RunState
    events: tuple[RunEvent, ...]


class AgentLoop:
    def __init__(self, provider: TurnProvider, dispatcher: ToolDispatcher, *,
                 cancelled: Callable[[], bool] | None = None,
                 limits: AgentLoopLimits | None = None,
                 context_builder: ContextBuilder | None = None,
                 compactor: TranscriptCompactor | None = None,
                 context_inputs: ContextInputs | None = None,
                 skill_service=None) -> None:
        self._provider = provider
        self._dispatcher = dispatcher
        self._cancelled = cancelled or (lambda: False)
        self._limits = limits or AgentLoopLimits()
        self._context_builder = context_builder
        self._compactor = compactor
        self._context_inputs = context_inputs
        self._skill_service = skill_service
        self._event_sink: Callable[[RunEvent], None] | None = None

    def set_event_sink(self, sink: Callable[[RunEvent], None] | None) -> None:
        """Observe events synchronously as the loop emits them."""
        self._event_sink = sink

    def run(self, initial: RunState) -> AgentLoopResult:
        if initial.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
            raise ValueError(f"cannot run state in {initial.status.value}")
        starting_status = initial.status
        events: list[RunEvent] = []
        state = replace(initial, status=RunStatus.RUNNING)
        sequence = 0

        def emit(kind: RunEventType, payload: dict | None = None) -> None:
            nonlocal sequence
            sequence += 1
            events.append(RunEvent(id=f"evt-{uuid4().hex}", sequence=sequence,
                run_id=state.id, conversation_id=state.conversation_id,
                type=kind, payload=payload or {}))
            if self._event_sink is not None:
                self._event_sink(events[-1])

        def append_message(message: TranscriptMessage) -> None:
            nonlocal state
            state = replace(state, transcript=state.transcript + (message,))

        def public_message(content: str, *, record: bool = True) -> None:
            if not content:
                return
            message_id = f"msg-{uuid4().hex}"
            emit(RunEventType.MESSAGE_STARTED, {"message_id": message_id})
            emit(RunEventType.MESSAGE_DELTA, {"message_id": message_id, "content": content})
            if record:
                append_message(TranscriptMessage(id=message_id, role=MessageRole.ASSISTANT,
                    blocks=(ContentBlock(type=ContentBlockType.TEXT, text=content),),
                    source="model", trust="untrusted"))
            emit(RunEventType.MESSAGE_COMPLETED, {"message_id": message_id, "content": content})

        def tool_result_message(result: ToolResult) -> None:
            append_message(TranscriptMessage(id=f"tool-{result.call_id}", role=MessageRole.TOOL,
                visible_to_user=False, source="tool", trust="untrusted", blocks=(ContentBlock(
                    type=ContentBlockType.TOOL_RESULT, call_id=result.call_id,
                    result={"status": result.status.value, "output": result.output,
                            "error": result.error, "artifact_ids": list(result.artifact_ids),
                            "diff": result.diff, "provenance": list(result.provenance),
                            "structured": result.structured}),)))

        def terminal(status: RunStatus, reason: str, kind: RunEventType) -> AgentLoopResult:
            nonlocal state
            state = replace(state, status=status, terminal_reason=reason)
            emit(kind, {"status": status.value, "reason": reason})
            return AgentLoopResult(state=state, events=tuple(events))

        if not state.transcript:
            append_message(TranscriptMessage(id=state.request.user_message_id,
                role=MessageRole.USER, source="user", trust="trusted",
                blocks=(ContentBlock(type=ContentBlockType.TEXT, text=state.request.user_text),)))
        if starting_status is RunStatus.QUEUED:
            emit(RunEventType.RUN_STARTED, {"status": RunStatus.RUNNING.value})
        else:
            emit(RunEventType.RUN_STATE_CHANGED, {"status": RunStatus.RUNNING.value})
        progress_count = 0

        while state.model_turns < self._limits.max_model_turns:
            if self._cancelled():
                return terminal(RunStatus.CANCELLED, "cancelled_before_provider", RunEventType.RUN_CANCELLED)
            if self._context_builder is not None and self._context_inputs is not None:
                provider_context = self._context_builder.build(state, self._context_inputs)
                if not provider_context.breakdown.fits:
                    if self._compactor is None:
                        return terminal(RunStatus.BLOCKED, "context_budget_exceeded",
                                        RunEventType.RUN_BLOCKED)
                    emit(RunEventType.CONTEXT_COMPACTING, {
                        "before_tokens": provider_context.breakdown.total,
                        "context_window": provider_context.breakdown.context_window,
                        "count_source": provider_context.breakdown.count_source,
                    })
                    try:
                        compacted = self._compactor.compact(state, self._context_inputs)
                    except ContextOverflowError as exc:
                        return terminal(RunStatus.BLOCKED, f"context_overflow: {exc}",
                                        RunEventType.RUN_BLOCKED)
                    state = compacted.state
                    emit(RunEventType.CONTEXT_COMPACTED, {
                        "before_tokens": compacted.before_tokens,
                        "after_tokens": compacted.after_tokens,
                        "removed_message_ids": list(compacted.removed_message_ids),
                    })
            try:
                chunks: list[ModelTurn] = []
                for chunk in self._provider.stream(state.transcript):
                    if self._cancelled():
                        return terminal(RunStatus.CANCELLED, "cancelled_during_provider", RunEventType.RUN_CANCELLED)
                    if not isinstance(chunk, ModelTurn):
                        raise TypeError("provider emitted an unsupported turn chunk")
                    chunks.append(chunk)
            except Exception as exc:
                return terminal(RunStatus.FAILED, f"provider_error: {exc}", RunEventType.RUN_FAILED)
            turn = _combine_turn(chunks)
            state = replace(state, model_turns=state.model_turns + 1,
                input_tokens=_add_usage(state.input_tokens, turn.input_tokens),
                output_tokens=_add_usage(state.output_tokens, turn.output_tokens))
            if not turn.text.strip() and not turn.calls:
                return terminal(RunStatus.FAILED, "empty_model_turn", RunEventType.RUN_FAILED)
            if not _valid_calls(turn.calls):
                return terminal(RunStatus.FAILED, "malformed_tool_call", RunEventType.RUN_FAILED)

            call_blocks = tuple(ContentBlock(type=ContentBlockType.TOOL_CALL,
                call_id=call.id, tool_name=call.name, arguments=call.arguments) for call in turn.calls)
            if turn.calls:
                append_message(TranscriptMessage(id=f"assistant-{uuid4().hex}",
                    role=MessageRole.ASSISTANT, visible_to_user=bool(turn.text.strip()),
                    source="model", trust="untrusted",
                    blocks=((ContentBlock(type=ContentBlockType.TEXT, text=turn.text),)
                            if turn.text.strip() else ()) + call_blocks))
            if turn.text.strip():
                public_message(turn.text.strip(), record=not turn.calls)
            if not turn.calls:
                return terminal(RunStatus.FAILED, "missing_finish_task", RunEventType.RUN_FAILED)

            has_external = any(call.name not in {"report_progress", "activate_skill", "finish_task"}
                               for call in turn.calls)
            cancelled_after_effect = False
            for index, call in enumerate(turn.calls):
                if cancelled_after_effect or self._cancelled():
                    result = ToolResult(call_id=call.id, status=ToolResultStatus.CANCELLED,
                                        error="run cancelled before dispatch")
                    tool_result_message(result)
                    emit(RunEventType.TOOL_COMPLETED, _result_payload(call, result))
                    cancelled_after_effect = True
                    continue
                emit(RunEventType.TOOL_REQUESTED, {"call_id": call.id, "name": call.name,
                                                   "arguments": call.arguments})
                if call.name == "report_progress":
                    message = call.arguments.get("message")
                    if not isinstance(message, str) or not message.strip():
                        result = ToolResult(call_id=call.id, status=ToolResultStatus.FAILED,
                                            error="report_progress requires a non-empty message")
                    elif progress_count >= self._limits.max_progress_messages:
                        result = ToolResult(call_id=call.id, status=ToolResultStatus.FAILED,
                                            error="progress message budget exhausted")
                    else:
                        progress_count += 1
                        result = ToolResult(call_id=call.id, status=ToolResultStatus.SUCCEEDED,
                                            output=message.strip())
                    tool_result_message(result)
                    emit(RunEventType.TOOL_COMPLETED, _result_payload(call, result))
                    if result.status is ToolResultStatus.SUCCEEDED:
                        public_message(result.output)
                    continue
                if call.name == "finish_task":
                    summary = call.arguments.get("summary")
                    outcome = call.arguments.get("outcome")
                    if has_external:
                        result = ToolResult(call_id=call.id, status=ToolResultStatus.FAILED,
                            error="cannot finish in a batch containing external calls")
                        tool_result_message(result)
                        emit(RunEventType.TOOL_COMPLETED, _result_payload(call, result))
                        continue
                    if not isinstance(summary, str) or not summary.strip() or outcome not in {"completed", "blocked"}:
                        result = ToolResult(call_id=call.id, status=ToolResultStatus.FAILED,
                            error="finish_task requires summary and outcome completed|blocked")
                        tool_result_message(result)
                        emit(RunEventType.TOOL_COMPLETED, _result_payload(call, result))
                        continue
                    result = ToolResult(call_id=call.id, status=ToolResultStatus.SUCCEEDED, output=summary.strip())
                    tool_result_message(result)
                    emit(RunEventType.TOOL_COMPLETED, _result_payload(call, result))
                    public_message(summary.strip())
                    if outcome == "completed":
                        return terminal(RunStatus.COMPLETED, "finish_task", RunEventType.RUN_COMPLETED)
                    return terminal(RunStatus.BLOCKED, "finish_task_blocked", RunEventType.RUN_BLOCKED)
                if call.name == "activate_skill":
                    name = call.arguments.get("name")
                    try:
                        if self._skill_service is None or not isinstance(name, str) or not name:
                            raise ValueError("activate_skill requires an available skill name")
                        active = self._skill_service.activate(state.id, name)
                        structured = {"name": active.name, "description": active.description,
                            "instructions": active.instructions,
                            "content_hash": active.content_hash,
                            "support_files": list(active.support_files)}
                        result = ToolResult(call_id=call.id, status=ToolResultStatus.SUCCEEDED,
                            output=f"Activated skill: {active.name}", structured=structured)
                        if self._context_inputs is not None:
                            block = (f"[Active skill: {active.name}@{active.content_hash}]\n"
                                     f"{active.instructions}")
                            if block not in self._context_inputs.skill_blocks:
                                self._context_inputs = replace(self._context_inputs,
                                    skill_blocks=self._context_inputs.skill_blocks + (block,))
                    except Exception as exc:
                        result = ToolResult(call_id=call.id, status=ToolResultStatus.FAILED,
                                            error=f"skill activation failed: {exc}")
                    tool_result_message(result)
                    emit(RunEventType.TOOL_COMPLETED, _result_payload(call, result))
                    continue
                if state.tool_calls >= self._limits.max_tool_calls:
                    result = ToolResult(call_id=call.id, status=ToolResultStatus.CANCELLED,
                                        error="tool call budget exhausted")
                else:
                    authorization = None
                    authorize = getattr(self._dispatcher, "authorize", None)
                    execute_authorized = getattr(self._dispatcher, "execute_authorized", None)
                    if callable(authorize) and callable(execute_authorized):
                        authorization = authorize(call)
                    if authorization is not None and authorization.kind.value == "pending":
                        request = authorization.request
                        state = replace(state, status=RunStatus.AWAITING_PERMISSION,
                            pending_call_ids=(call.id,), pending_permission_ids=(request.id,))
                        emit(RunEventType.PERMISSION_REQUESTED, {
                            "request_id": request.id, "call_id": call.id, "name": call.name,
                            "arguments": request.canonical_arguments,
                            "argument_digest": request.argument_digest,
                            "effects": list(request.effects),
                            "expires_at": request.expires_at.isoformat(),
                            "proposed_diff": request.proposed_diff,
                        })
                        return AgentLoopResult(state=state, events=tuple(events))
                    if authorization is not None and authorization.kind.value in {"denied", "blocked"}:
                        result = ToolResult(call_id=call.id, status=ToolResultStatus.DENIED,
                            error=authorization.reason or authorization.error_code.value)
                        tool_result_message(result)
                        emit(RunEventType.TOOL_COMPLETED, _result_payload(call, result))
                        if authorization.kind.value == "blocked":
                            return terminal(RunStatus.BLOCKED, "headless_approval_required",
                                            RunEventType.RUN_BLOCKED)
                        continue
                    emit(RunEventType.TOOL_STARTED, {"call_id": call.id, "name": call.name,
                                                     "arguments": call.arguments})
                    try:
                        result = (execute_authorized(call) if authorization is not None
                                  else self._dispatcher.dispatch(call))
                        if result.call_id != call.id:
                            raise ValueError("dispatcher returned a mismatched call id")
                    except Exception as exc:
                        result = ToolResult(call_id=call.id, status=ToolResultStatus.FAILED,
                                            error=f"dispatcher_error: {exc}")
                    state = replace(state, tool_calls=state.tool_calls + 1)
                tool_result_message(result)
                emit(RunEventType.TOOL_COMPLETED, _result_payload(call, result))
                if self._cancelled():
                    cancelled_after_effect = True
            if cancelled_after_effect:
                return terminal(RunStatus.CANCELLED, "cancelled_after_tool_result", RunEventType.RUN_CANCELLED)

        return terminal(RunStatus.BLOCKED, "model_turn_budget_exhausted", RunEventType.RUN_BLOCKED)

    def resume_permission(self, initial: RunState) -> AgentLoopResult:
        if initial.status is not RunStatus.AWAITING_PERMISSION:
            raise ValueError("run is not awaiting permission")
        calls = _pending_calls(initial)
        if not calls:
            failed = replace(initial, status=RunStatus.FAILED,
                             terminal_reason="pending_permission_call_missing")
            event = RunEvent(id=f"evt-{uuid4().hex}", sequence=1, run_id=initial.id,
                conversation_id=initial.conversation_id, type=RunEventType.RUN_FAILED,
                payload={"status": "failed", "reason": failed.terminal_reason})
            return AgentLoopResult(failed, (event,))
        state = replace(initial, status=RunStatus.RUNNING,
                        pending_call_ids=(), pending_permission_ids=())
        events: list[RunEvent] = []

        def emit(kind: RunEventType, payload: dict) -> None:
            events.append(RunEvent(id=f"evt-{uuid4().hex}", sequence=len(events) + 1,
                run_id=state.id, conversation_id=state.conversation_id,
                type=kind, payload=payload))
            if self._event_sink is not None:
                self._event_sink(events[-1])

        for call in calls:
            authorize = getattr(self._dispatcher, "authorize", None)
            execute = getattr(self._dispatcher, "execute_authorized", None)
            if not callable(authorize) or not callable(execute):
                result = ToolResult(call_id=call.id, status=ToolResultStatus.FAILED,
                                    error="dispatcher cannot resume permission")
            else:
                authorization = authorize(call)
                emit(RunEventType.PERMISSION_RESOLVED, {
                    "request_id": next(iter(initial.pending_permission_ids), None),
                    "call_id": call.id, "decision": authorization.kind.value})
                if authorization.kind.value == "allowed":
                    emit(RunEventType.TOOL_STARTED, {"call_id": call.id, "name": call.name,
                                                     "arguments": call.arguments})
                    result = execute(call)
                elif authorization.kind.value == "pending":
                    return AgentLoopResult(initial, tuple(events))
                else:
                    result = ToolResult(call_id=call.id, status=ToolResultStatus.DENIED,
                        error=authorization.reason or authorization.error_code.value)
            state = replace(state, transcript=state.transcript + (TranscriptMessage(
                id=f"tool-{call.id}", role=MessageRole.TOOL, visible_to_user=False,
                source="tool", trust="untrusted", blocks=(ContentBlock(
                    type=ContentBlockType.TOOL_RESULT, call_id=call.id,
                    result={"status": result.status.value, "output": result.output,
                            "error": result.error, "artifact_ids": list(result.artifact_ids),
                            "diff": result.diff, "provenance": list(result.provenance),
                            "structured": result.structured}),)),),
                tool_calls=state.tool_calls + (1 if result.status is not ToolResultStatus.DENIED else 0))
            emit(RunEventType.TOOL_COMPLETED, _result_payload(call, result))
        continued = self.run(state)
        offset = len(events)
        events.extend(replace(event, sequence=event.sequence + offset) for event in continued.events)
        return AgentLoopResult(continued.state, tuple(events))


def _combine_turn(chunks: list[ModelTurn]) -> ModelTurn:
    if not chunks:
        return ModelTurn()
    return ModelTurn(text="".join(chunk.text for chunk in chunks),
        calls=tuple(call for chunk in chunks for call in chunk.calls),
        finish_reason=next((chunk.finish_reason for chunk in reversed(chunks) if chunk.finish_reason), None),
        input_tokens=next((chunk.input_tokens for chunk in reversed(chunks) if chunk.input_tokens is not None), None),
        output_tokens=next((chunk.output_tokens for chunk in reversed(chunks) if chunk.output_tokens is not None), None))


def _valid_calls(calls: tuple[ToolCall, ...]) -> bool:
    if any(not isinstance(call, ToolCall) for call in calls):
        return False
    ids = [call.id for call in calls]
    return len(ids) == len(set(ids))


def _pending_calls(state: RunState) -> tuple[ToolCall, ...]:
    wanted = set(state.pending_call_ids)
    calls: list[ToolCall] = []
    for message in state.transcript:
        for block in message.blocks:
            if block.type is ContentBlockType.TOOL_CALL and block.call_id in wanted:
                calls.append(ToolCall(id=block.call_id, name=block.tool_name or "",
                                      arguments=block.arguments or {}))
    return tuple(calls)


def _add_usage(current: int | None, addition: int | None) -> int | None:
    if addition is None:
        return current
    return (current or 0) + addition


def _result_payload(call: ToolCall, result: ToolResult) -> dict:
    return {"call_id": call.id, "name": call.name, "status": result.status.value,
            "output": result.output, "error": result.error,
            "artifact_ids": list(result.artifact_ids), "diff": result.diff,
            "provenance": list(result.provenance), "structured": result.structured}
