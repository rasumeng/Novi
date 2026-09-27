"""Background surfaces deliver progress and cancellation during execution."""

import threading
from types import SimpleNamespace

from novi.runtime.agent_loop import AgentLoop
from novi.runtime.provider_adapter import LangChainTurnProvider
from novi.runtime.run_contracts import RunEventType, RunStatus
from novi.services.run_render import execute_text
from novi.services.run_service import RunService
from novi.services.run_store import RunStore


def context():
    return SimpleNamespace(model_service=SimpleNamespace(
        resolve_primary=lambda: ("fake", "model")))


def test_progress_delivered_before_provider_finishes(tmp_path):
    events = []

    class Model:
        def stream(self, messages):
            yield SimpleNamespace(content="First")
            assert any(e.type is RunEventType.MESSAGE_DELTA for e in events)
            assert not any(e.type is RunEventType.RUN_COMPLETED for e in events)
            yield SimpleNamespace(content=" second")

    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(
        LangChainTurnProvider(Model()), None, cancelled=cancelled))
    try:
        _, state, replay = execute_text(service, context(), "go", "background",
                                        on_event=events.append)
        assert state.status is RunStatus.COMPLETED
        assert tuple(events) == replay
        assert service._listeners == []
    finally:
        service.close()


def test_stop_during_provider_io_cancels_before_next_chunk(tmp_path):
    stop = threading.Event()
    cancelled_event = threading.Event()

    class Model:
        def stream(self, messages):
            yield SimpleNamespace(content="First")
            stop.set()
            assert cancelled_event.wait(3), "Cancellation waited for provider completion"
            yield SimpleNamespace(content="must not be delivered")

    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(
        LangChainTurnProvider(Model()), None, cancelled=cancelled))
    def on_event(event):
        if event.type is RunEventType.RUN_CANCELLED:
            cancelled_event.set()
    try:
        _, state, events = execute_text(service, context(), "go", "background",
            on_event=on_event, stop_check=stop.is_set)
        assert state.status is RunStatus.CANCELLED
        assert events[-1].type is RunEventType.RUN_CANCELLED
        assert sum(e.type is RunEventType.RUN_CANCELLED for e in events) == 1
        assert all("must not" not in str(e.payload) for e in events)
    finally:
        service.close()


def test_stop_before_execution_never_calls_provider(tmp_path):
    class Model:
        def stream(self, messages):
            raise AssertionError("Provider called after cancellation")
    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(
        LangChainTurnProvider(Model()), None, cancelled=cancelled))
    try:
        _, state, events = execute_text(service, context(), "go", "background",
                                        stop_check=lambda: True)
        assert state.status is RunStatus.CANCELLED
        assert [e.type for e in events] == [RunEventType.RUN_CANCELLED]
    finally:
        service.close()
