"""Streaming must reach socket clients before the provider finishes its turn."""

from types import SimpleNamespace

import pytest

from novi.runtime.agent_loop import AgentLoop
from novi.runtime.provider_adapter import LangChainTurnProvider
from novi.runtime.run_contracts import RunEvent, RunEventType, RunRequest
from novi.services.run_service import RunService
from novi.services.run_store import RunStore
from novi.services.run_transport import RunSocketBridge, event_to_socket_message


def test_provider_chunks_reach_socket_before_next_chunk(tmp_path):
    messages = []

    class Model:
        def stream(self, transcript):
            yield SimpleNamespace(content="Hello")
            # This runs before generation resumes, not after collecting a response.
            assert [m["text"] for m in messages if m["type"] == "token"] == ["Hello"]
            assert not any(m["type"] in {"message_end", "done"} for m in messages)
            yield SimpleNamespace(content=" **world**")
            assert [m["text"] for m in messages if m["type"] == "token"] == [
                "Hello", " **world**"]

    class Dispatcher:
        def dispatch(self, call):
            raise AssertionError("This reply must not use tools")

    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(
        LangChainTurnProvider(Model()), Dispatcher(), cancelled=cancelled))
    bridge = RunSocketBridge(service, messages.append)
    try:
        service.start(RunRequest(conversation_id="stream-test", user_message_id="u1",
                                 user_text="Say hello"))
        assert [m["type"] for m in messages] == [
            "run_state", "message_start", "token", "token", "message_end", "done"]
        assert [m["sequence"] for m in messages] == list(range(1, 7))
        assert "".join(m["text"] for m in messages if m["type"] == "token") == "Hello **world**"
    finally:
        bridge.detach()
        service.close()


@pytest.mark.parametrize("kind", list(RunEventType))
def test_every_run_event_preserves_socket_sequence(kind):
    event = RunEvent(id="e1", sequence=7, run_id="r1", conversation_id="c1",
                     type=kind, payload={})
    message = event_to_socket_message(event)
    # Omitting an event creates a permanent gap in the frontend's ordered reducer.
    assert message is not None, f"Dropped {kind.value} from the socket stream"
    assert message["sequence"] == 7
    assert message["runId"] == "r1"
    assert message["conversationId"] == "c1"


def test_native_reasoning_reaches_socket_separately_from_answer(tmp_path):
    messages = []

    class Model:
        def stream(self, transcript):
            yield SimpleNamespace(content='', additional_kwargs={'reasoning_content': 'Let me '})
            assert messages[-1]['type'] == 'reasoning'
            yield SimpleNamespace(content='', additional_kwargs={'reasoning_content': 'check.'})
            assert not any(m['type'] == 'token' for m in messages)
            yield SimpleNamespace(content='The **answer**.')

    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(
        LangChainTurnProvider(Model()), None, cancelled=cancelled))
    bridge = RunSocketBridge(service, messages.append)
    try:
        service.start(RunRequest(conversation_id='reasoning', user_message_id='u', user_text='hello'))
        assert [m['type'] for m in messages] == [
            'run_state', 'message_start', 'reasoning', 'reasoning', 'token', 'message_end', 'done']
        assert ''.join(m['text'] for m in messages if m['type'] == 'reasoning') == 'Let me check.'
        assert ''.join(m['text'] for m in messages if m['type'] == 'token') == 'The **answer**.'
    finally:
        bridge.detach()
        service.close()


@pytest.mark.parametrize('cancel', [False, True])
def test_chat_composition_blocks_memory_until_provider_actually_exits(tmp_path, cancel):
    from threading import Event
    from novi.services.inference_coordinator import InferenceCoordinator
    from novi.services.run_composition import build_run_service

    coordinator = InferenceCoordinator()
    entered, release = Event(), Event()

    class Model:
        def stream(self, transcript):
            yield SimpleNamespace(content='Hello')
            entered.set()
            assert release.wait(5)
            yield SimpleNamespace(content=' world')

    ctx = SimpleNamespace(config={'permissions': {}}, brain=None, model_service=SimpleNamespace(
        inference=coordinator, resolve_primary=lambda: ('ollama', 'test'),
        bind_model=lambda *args, **kwargs: Model()))
    service = build_run_service(ctx, persist_dir=tmp_path, skill_root=tmp_path/'skills', registry={})
    bridge = RunSocketBridge(service, lambda event: None)
    try:
        run_id = bridge.start(RunRequest(conversation_id='guard', user_message_id='u', user_text='hello'))
        assert entered.wait(5)
        assert coordinator.foreground_active
        if cancel:
            service.cancel(run_id)
        with coordinator.try_acquire_memory(idle_seconds=0) as token:
            assert token is None  # Cancellation alone must not free an active model.
        release.set()
        bridge._worker.join(5)
        assert not bridge._worker.is_alive()
        assert not coordinator.foreground_active
        with coordinator.try_acquire_memory() as token:
            assert token is None  # Normal automatic updates still wait for the idle period.
        with coordinator.try_acquire_memory(idle_seconds=0) as token:
            assert token is not None
    finally:
        release.set()
        if bridge._worker:
            bridge._worker.join(5)
        bridge.detach()
        service.close()


def test_chat_cancels_memory_and_waits_for_its_release(tmp_path):
    from threading import Event, Thread
    from novi.services.inference_coordinator import InferenceCoordinator

    coordinator = InferenceCoordinator()
    entered = Event()

    class Model:
        def stream(self, transcript):
            entered.set()
            yield SimpleNamespace(content='Hello')

    service = RunService(RunStore(tmp_path), lambda state, cancelled: AgentLoop(
        LangChainTurnProvider(Model()), None, cancelled=cancelled), inference=coordinator)
    run_id = service.prepare(RunRequest(conversation_id='handoff', user_message_id='u', user_text='hello'))
    thread = Thread(target=service.execute, args=(run_id,))
    try:
        with coordinator.try_acquire_memory(idle_seconds=0) as memory_cancel:
            thread.start()
            assert memory_cancel.wait(5)
            assert not entered.is_set()
        thread.join(5)
        assert entered.is_set()
        assert service.snapshot(run_id).finished
    finally:
        thread.join(5)
        service.close()
