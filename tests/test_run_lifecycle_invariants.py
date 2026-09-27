"""Behavioral coverage migrated from the retired runtime/AgentRun suites."""

from types import SimpleNamespace

import pytest

from novi.runtime.agent_loop import AgentLoop, AgentLoopLimits
from novi.runtime.provider_adapter import LangChainTurnProvider
from novi.runtime.run_contracts import ModelTurn, RunRequest, RunStatus, ToolCall
from novi.runtime.transcript import ContentBlockType, MessageRole
from novi.services.run_service import RunService
from novi.services.run_store import RunStore
from novi.services.run_transport import event_to_socket_message


TERMINALS = {f"run.{s}" for s in ("completed", "failed", "cancelled", "blocked", "interrupted")}


class Provider:
    def __init__(self, turns):
        self.turns = iter(turns)

    def stream(self, transcript):
        turn = next(self.turns)
        if isinstance(turn, Exception):
            raise turn
        yield turn


class NoTools:
    def dispatch(self, call):
        raise AssertionError(f"Unexpected external effect: {call.name}")


def request():
    return RunRequest(conversation_id="c", user_message_id="u", user_text="hello")


@pytest.mark.parametrize("case,status", [
    ("success", RunStatus.COMPLETED), ("failure", RunStatus.FAILED),
    ("cancel", RunStatus.CANCELLED), ("budget", RunStatus.BLOCKED),
])
def test_one_terminal_is_last_and_snapshot_is_durable_before_notification(tmp_path, case, status):
    provider = Provider([RuntimeError("provider unavailable")] if case == "failure" else
                        [ModelTurn(text="Done", is_complete=True)])
    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(
        provider, NoTools(), cancelled=lambda: case == "cancel",
        limits=AgentLoopLimits(max_model_turns=0 if case == "budget" else 2)))
    observed = []
    service.listen(lambda event: observed.append((event, service.snapshot(event.run_id))))
    try:
        run_id = service.start(request())
        events = service.subscribe(run_id)
        terminal = [event for event in events if event.type.value in TERMINALS]
        assert len(terminal) == 1
        assert events[-1] == terminal[0]
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
        assert service.snapshot(run_id).status is status
        assert observed[-1][1].status is status
        assert observed[-1][1].finished
    finally:
        service.close()


def test_public_messages_keep_event_identity_and_status_is_ephemeral(tmp_path):
    turns = [ModelTurn(text="Checking.", calls=(ToolCall("p", "report_progress", {"message": "Working."}),), is_complete=True),
             ModelTurn(text="Done.", is_complete=True)]
    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(Provider(turns), NoTools()))
    try:
        run_id = service.start(request())
        events = service.subscribe(run_id)
        starts = [e.payload["message_id"] for e in events if e.type.value == "message.started"]
        ends = [e.payload["message_id"] for e in events if e.type.value == "message.completed"]
        state = service.snapshot(run_id)
        public = [m for m in state.transcript if m.role is MessageRole.ASSISTANT and m.visible_to_user]
        assert len(starts) == len(set(starts)) == 3
        assert starts == ends == [m.id for m in public]
        texts = [b.text for m in public for b in m.blocks if b.type is ContentBlockType.TEXT]
        assert texts == ["Checking.", "Working.", "Done."]
        assert not any("Calling report_progress" in text for text in texts)
    finally:
        service.close()


def test_usage_is_provider_reported_cumulative_once_per_turn(tmp_path):
    class Model:
        def __init__(self):
            self.calls = 0

        def stream(self, messages):
            self.calls += 1
            if self.calls == 1:
                yield SimpleNamespace(content="", usage_metadata={"input_tokens": 10, "output_tokens": 2})
                yield SimpleNamespace(content="", usage_metadata={"input_tokens": 10, "output_tokens": 3},
                    tool_calls=[{"id": "p", "name": "report_progress", "args": {"message": "Working"}}])
            else:
                yield SimpleNamespace(content="Done", response_metadata={"token_usage": {"prompt_tokens": 12, "completion_tokens": 4}})
    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(LangChainTurnProvider(Model()), NoTools()))
    try:
        run_id = service.start(request())
        state = service.snapshot(run_id)
        assert (state.input_tokens, state.output_tokens) == (22, 7)
        assert state.model_turns == 2
    finally:
        service.close()


def test_unavailable_usage_stays_unknown_and_diagnostics_stay_off_wire(tmp_path):
    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(
        Provider([ModelTurn(text="Done", is_complete=True)]), NoTools()))
    try:
        run_id = service.start(request())
        state = service.snapshot(run_id)
        assert state.input_tokens is None and state.output_tokens is None
        for event in service.subscribe(run_id):
            event.payload["internal_diagnostic"] = "private internal detail"
            assert "internal_diagnostic" not in event_to_socket_message(event)
    finally:
        service.close()


def test_memory_failure_does_not_discard_completed_run(tmp_path, monkeypatch):
    attempts = []
    def fail(state, brain):
        attempts.append(state.id)
        raise RuntimeError("memory unavailable")
    monkeypatch.setattr("novi.services.run_memory.ingest_run", fail)
    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(
        Provider([ModelTurn(text="Done", is_complete=True)]), NoTools()), brain=object())
    try:
        run_id = service.start(request())
        assert service.snapshot(run_id).status is RunStatus.COMPLETED
        assert attempts == [run_id]
        assert service.subscribe(run_id)[-1].type.value == "run.completed"
    finally:
        service.close()
