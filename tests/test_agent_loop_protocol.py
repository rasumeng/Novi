from __future__ import annotations

from dataclasses import dataclass, replace

from novi.runtime.agent_loop import AgentLoop, AgentLoopLimits
from novi.runtime.context_builder import ContextBuilder, ContextInputs, TranscriptCompactor
from novi.runtime.run_contracts import (
    ModelSnapshot, ModelTurn, RunRequest, RunState, RunStatus, ToolCall, ToolResult, ToolResultStatus,
)
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage
from novi.services.permission_service import (
    AuthorizedToolDispatcher, PermissionService, ToolAuthorizationPolicy, ToolDescriptor,
)
from novi.skills.catalog import SkillCatalog
from novi.skills.service import SkillService


class ScriptedProvider:
    def __init__(self, turns):
        self.turns = list(turns)
        self.transcripts = []

    def stream(self, transcript):
        self.transcripts.append(tuple(transcript))
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        yield turn


@dataclass
class RecordingDispatcher:
    results: dict[str, ToolResult]

    def __post_init__(self):
        self.calls = []

    def dispatch(self, call):
        self.calls.append(call)
        return self.results.get(call.id, ToolResult(call_id=call.id, status=ToolResultStatus.SUCCEEDED,
                                                    output=f"result:{call.name}"))


def state():
    return RunState(id="run-1", request=RunRequest(
        conversation_id="conv-1", user_message_id="user-1", user_text="do the work"))


def test_progress_two_tools_progress_and_explicit_finish():
    provider = ScriptedProvider([
        ModelTurn(text="Checking first.", calls=(
            ToolCall(id="p1", name="report_progress", arguments={"message": "Starting."}),
            ToolCall(id="c1", name="read_file", arguments={"path": "a.py"}),
            ToolCall(id="c2", name="read_file", arguments={"path": "b.py"}),
        )),
        ModelTurn(calls=(
            ToolCall(id="p2", name="report_progress", arguments={"message": "Both files checked."}),
            ToolCall(id="c3", name="write_file", arguments={"path": "a.py", "content": "fixed"}),
        )),
        ModelTurn(calls=(ToolCall(id="f1", name="finish_task",
                                  arguments={"summary": "Fixed and verified.", "outcome": "completed"}),)),
    ])
    dispatcher = RecordingDispatcher({})

    result = AgentLoop(provider, dispatcher).run(state())

    assert result.state.status is RunStatus.COMPLETED
    assert [call.id for call in dispatcher.calls] == ["c1", "c2", "c3"]
    messages = [event.payload["content"] for event in result.events
                if event.type.value == "message.completed"]
    assert messages == ["Checking first.", "Starting.", "Both files checked.", "Fixed and verified."]
    assert len([event for event in result.events if event.type.value == "run.completed"]) == 1


def test_every_assistant_call_has_one_result_before_next_provider_turn():
    provider = ScriptedProvider([
        ModelTurn(calls=(ToolCall(id="c1", name="read_file", arguments={"path": "a"}),)),
        ModelTurn(calls=(ToolCall(id="f1", name="finish_task",
                                  arguments={"summary": "done", "outcome": "completed"}),)),
    ])
    result = AgentLoop(provider, RecordingDispatcher({})).run(state())
    second_input = provider.transcripts[1]
    call_ids = [block.call_id for msg in second_input for block in msg.blocks if block.type.value == "tool_call"]
    result_ids = [block.call_id for msg in second_input for block in msg.blocks if block.type.value == "tool_result"]
    assert call_ids == result_ids == ["c1"]
    assert result.state.finished


def test_finish_mixed_with_external_call_does_not_drop_external_work():
    provider = ScriptedProvider([
        ModelTurn(calls=(
            ToolCall(id="f1", name="finish_task", arguments={"summary": "too soon", "outcome": "completed"}),
            ToolCall(id="c1", name="write_file", arguments={"path": "a", "content": "x"}),
        )),
        ModelTurn(calls=(ToolCall(id="f2", name="finish_task",
                                  arguments={"summary": "now done", "outcome": "completed"}),)),
    ])
    dispatcher = RecordingDispatcher({})
    result = AgentLoop(provider, dispatcher).run(state())
    assert [call.id for call in dispatcher.calls] == ["c1"]
    assert result.state.status is RunStatus.COMPLETED
    assert any("cannot finish" in (block.result or {}).get("error", "")
               for msg in provider.transcripts[1] for block in msg.blocks if block.type.value == "tool_result")


def test_empty_turn_and_provider_error_fail_honestly():
    empty = AgentLoop(ScriptedProvider([ModelTurn()]), RecordingDispatcher({})).run(state())
    assert empty.state.status is RunStatus.FAILED
    assert empty.state.terminal_reason == "empty_model_turn"
    errored = AgentLoop(ScriptedProvider([RuntimeError("offline")]), RecordingDispatcher({})).run(state())
    assert errored.state.status is RunStatus.FAILED
    assert "offline" in (errored.state.terminal_reason or "")


def test_denial_is_recorded_and_model_can_finish_blocked():
    provider = ScriptedProvider([
        ModelTurn(calls=(ToolCall(id="c1", name="write_file", arguments={"path": "a"}),)),
        ModelTurn(calls=(ToolCall(id="f1", name="finish_task",
                                  arguments={"summary": "Permission was denied.", "outcome": "blocked"}),)),
    ])
    denied = ToolResult(call_id="c1", status=ToolResultStatus.DENIED, error="user denied")
    result = AgentLoop(provider, RecordingDispatcher({"c1": denied})).run(state())
    assert result.state.status is RunStatus.BLOCKED
    assert any(event.payload.get("status") == "denied" for event in result.events)


def test_cancellation_after_effect_preserves_result_and_cancels_run():
    checks = iter([False, False, False, True])
    cancelled = lambda: next(checks, True)
    provider = ScriptedProvider([ModelTurn(calls=(
        ToolCall(id="c1", name="write_file", arguments={"path": "a"}),
        ToolCall(id="c2", name="write_file", arguments={"path": "b"}),
    ))])
    result = AgentLoop(provider, RecordingDispatcher({}), cancelled=cancelled).run(state())
    assert result.state.status is RunStatus.CANCELLED
    completed = [event for event in result.events if event.type.value == "tool.completed"]
    assert [event.payload["call_id"] for event in completed] == ["c1", "c2"]
    assert completed[0].payload["status"] == "succeeded"
    assert completed[1].payload["status"] == "cancelled"


def test_turn_budget_blocks_instead_of_claiming_success():
    provider = ScriptedProvider([ModelTurn(calls=(ToolCall(
        id="p1", name="report_progress", arguments={"message": "working"}),))])
    result = AgentLoop(provider, RecordingDispatcher({}), limits=AgentLoopLimits(max_model_turns=1)).run(state())
    assert result.state.status is RunStatus.BLOCKED
    assert result.state.terminal_reason == "model_turn_budget_exhausted"


def test_malformed_native_call_fails_as_protocol_error():
    provider = ScriptedProvider([ModelTurn(calls=({"id": "bad", "name": "read_file"},))])  # type: ignore[arg-type]
    result = AgentLoop(provider, RecordingDispatcher({})).run(state())
    assert result.state.status is RunStatus.FAILED
    assert result.state.terminal_reason == "malformed_tool_call"


def test_cancellation_during_streaming_emits_no_partial_public_message():
    class StreamingProvider:
        def stream(self, transcript):
            yield ModelTurn(text="part one")
            yield ModelTurn(text="part two")

    provider = StreamingProvider()
    checks = iter([False, False, True])
    result = AgentLoop(provider, RecordingDispatcher({}),
                       cancelled=lambda: next(checks, True)).run(state())
    assert result.state.status is RunStatus.CANCELLED
    assert not any(event.type.value.startswith("message.") for event in result.events)


def test_identical_reads_can_repeat_after_state_changes():
    same_args = {"path": "a.py"}
    provider = ScriptedProvider([
        ModelTurn(calls=(ToolCall(id="c1", name="read_file", arguments=same_args),)),
        ModelTurn(calls=(ToolCall(id="c2", name="read_file", arguments=same_args),)),
        ModelTurn(calls=(ToolCall(id="f1", name="finish_task",
                                  arguments={"summary": "verified twice", "outcome": "completed"}),)),
    ])
    dispatcher = RecordingDispatcher({})
    result = AgentLoop(provider, dispatcher).run(state())
    assert result.state.status is RunStatus.COMPLETED
    assert [call.id for call in dispatcher.calls] == ["c1", "c2"]


def test_permission_wait_emits_requested_without_claiming_tool_started():
    provider = ScriptedProvider([ModelTurn(calls=(
        ToolCall(id="c1", name="write_file", arguments={"path": "a", "content": "x"}),))])
    permissions = PermissionService(
        descriptors={"write_file": ToolDescriptor("write_file", effects=("write",))},
        policy=ToolAuthorizationPolicy(tool_rules={"write_file": "ask"}))
    dispatcher = AuthorizedToolDispatcher(permission_service=permissions,
        exposed_tools={"write_file"}, run_id="run-1",
        executors={"write_file": lambda args: "written"})
    result = AgentLoop(provider, dispatcher).run(state())
    assert result.state.status is RunStatus.AWAITING_PERMISSION
    assert result.state.pending_call_ids == ("c1",)
    assert [event.type.value for event in result.events if event.type.value.startswith(("permission.", "tool.started"))] == ["permission.requested"]


def test_loop_compacts_actual_transcript_before_provider_call():
    initial = state()
    initial = replace(initial, request=replace(initial.request,
        model=ModelSnapshot("test", "scripted", context_window=180)))
    transcript = list(initial.transcript)
    for i in range(6):
        transcript.extend((
            TranscriptMessage(f"a{i}", MessageRole.ASSISTANT, (
                ContentBlock(ContentBlockType.TOOL_CALL, call_id=f"c{i}",
                             tool_name="read_file", arguments={"path": "a"}),)),
            TranscriptMessage(f"t{i}", MessageRole.TOOL, (
                ContentBlock(ContentBlockType.TOOL_RESULT, call_id=f"c{i}",
                             result={"status": "succeeded", "output": "x" * 120}),),
                visible_to_user=False, source="tool"),
        ))
    initial = replace(initial, transcript=tuple(transcript))
    provider = ScriptedProvider([ModelTurn(calls=(ToolCall(
        id="f", name="finish_task", arguments={"summary": "done", "outcome": "completed"}),))])
    result = AgentLoop(provider, RecordingDispatcher({}), context_builder=ContextBuilder(),
        compactor=TranscriptCompactor(ContextBuilder()),
        context_inputs=ContextInputs(output_reserve=30)).run(initial)
    assert len(provider.transcripts[0]) < len(initial.transcript)
    kinds = [event.type.value for event in result.events]
    assert "context.compacting" in kinds
    assert "context.compacted" in kinds


def test_activate_skill_is_explicit_control_result_not_text_scanning(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.create("reviewer", "review code", "Pinned review instructions")
    skills = SkillService(catalog)
    skills.begin_run("run-1")
    provider = ScriptedProvider([
        ModelTurn(text="reviewer", calls=(ToolCall(
            id="s1", name="activate_skill", arguments={"name": "reviewer"}),)),
        ModelTurn(calls=(ToolCall(id="f", name="finish_task",
            arguments={"summary": "reviewed", "outcome": "completed"}),)),
    ])
    result = AgentLoop(provider, RecordingDispatcher({}), skill_service=skills).run(state())
    assert result.state.status is RunStatus.COMPLETED
    results = [block.result for message in provider.transcripts[1] for block in message.blocks
               if block.type is ContentBlockType.TOOL_RESULT]
    assert results[0]["structured"]["instructions"] == "Pinned review instructions"
    assert skills.active("run-1")[0].name == "reviewer"
