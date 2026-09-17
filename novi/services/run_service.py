"""Application owner for durable foreground agent runs."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Callable, Protocol
from uuid import uuid4

from novi.runtime.agent_loop import AgentLoop, AgentLoopResult
from novi.runtime.run_contracts import RunEvent, RunEventType, RunRequest, RunState, RunStatus
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage
from novi.services.permission_service import PermissionDecision, PermissionService
from novi.services.run_store import RunStore


class LoopFactory(Protocol):
    def __call__(self, state: RunState, cancelled: Callable[[], bool]) -> AgentLoop: ...


class RunBusyError(RuntimeError):
    pass


class RunService:
    def __init__(self, store: RunStore, loop_factory: LoopFactory, *,
                 permission_service: PermissionService | None = None,
                 id_factory: Callable[[], str] | None = None,
                 recover_on_start: bool = False) -> None:
        self._store = store
        self._loop_factory = loop_factory
        self._permissions = permission_service
        self._id_factory = id_factory or (lambda: f"run-{uuid4().hex}")
        self._lock = threading.RLock()
        self._active_run_id: str | None = None
        self._loops: dict[str, AgentLoop] = {}
        self._cancel_signals: dict[str, threading.Event] = {}
        self._listeners: list[Callable[[RunEvent], None]] = []
        if recover_on_start:
            self._store.interrupt_active_runs()

    def start(self, request: RunRequest) -> str:
        run_id = self.prepare(request)
        self.execute(run_id)
        return run_id

    def prepare(self, request: RunRequest) -> str:
        """Create a queued run and return its stable id before execution."""
        with self._lock:
            if self._active_run_id is not None:
                active = self._store.snapshot(self._active_run_id)
                if not active.finished:
                    raise RunBusyError(f"foreground run already active: {active.id}")
                self._active_run_id = None
            run_id = self._id_factory()
            previous = self._store.runs_for_conversation(request.conversation_id)
            inherited = previous[-1].transcript if previous else ()
            user_message = TranscriptMessage(id=request.user_message_id,
                role=MessageRole.USER, source="user", trust="trusted",
                blocks=(ContentBlock(type=ContentBlockType.TEXT,
                                     text=request.user_text),))
            state = RunState(id=run_id, request=request,
                             transcript=inherited + (user_message,))
            self._store.create(state)
            signal = threading.Event()
            loop = self._loop_factory(state, signal.is_set)
            set_sink = getattr(loop, "set_event_sink", None)
            if callable(set_sink):
                set_sink(lambda event, initial=state: self._persist_live(event, initial))
            self._loops[run_id] = loop
            self._cancel_signals[run_id] = signal
            self._active_run_id = run_id
        return run_id

    def execute(self, run_id: str) -> RunState:
        """Execute a previously prepared run on the calling thread."""
        with self._lock:
            state = self._store.snapshot(run_id)
            loop = self._loops.get(run_id)
            if loop is None:
                raise ValueError("run is not prepared in this process")
        result = loop.run(state)
        self._persist(result)
        self._release_if_terminal(result.state)
        return result.state

    def listen(self, callback: Callable[[RunEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(callback)
        def unsubscribe() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)
        return unsubscribe

    def subscribe(self, run_id: str, after_sequence: int = 0) -> tuple[RunEvent, ...]:
        return self._store.events(run_id, after_sequence)

    def snapshot(self, run_id: str) -> RunState:
        return self._store.snapshot(run_id)

    def permission_request(self, request_id: str):
        if self._permissions is None:
            raise ValueError("permission service is unavailable")
        return self._permissions.request(request_id)

    def close(self) -> None:
        """Release the durable store owned by this service instance."""
        self._store.close()

    def respond_permission(self, request_id: str, decision: PermissionDecision) -> RunState:
        if self._permissions is None:
            raise ValueError("permission service is unavailable")
        request = self._permissions.request(request_id)
        with self._lock:
            state = self._store.snapshot(request.run_id)
            if state.status is not RunStatus.AWAITING_PERMISSION:
                raise ValueError("run is not awaiting permission")
            if request_id not in state.pending_permission_ids:
                raise ValueError("permission request does not belong to pending run state")
            loop = self._loops.get(state.id)
            if loop is None:
                raise ValueError("run cannot resume after process restart")
            self._permissions.resolve(request_id, decision)
        result = loop.resume_permission(state)
        self._persist(result)
        self._release_if_terminal(result.state)
        return result.state

    def cancel(self, run_id: str) -> RunState:
        with self._lock:
            state = self._store.snapshot(run_id)
            if state.finished:
                return state
            signal = self._cancel_signals.get(run_id)
            if signal is not None:
                signal.set()
            sequence = len(self._store.events(run_id)) + 1
            cancelled = replace(state, status=RunStatus.CANCELLED,
                                terminal_reason="cancelled_by_user")
            self._store.append(RunEvent(id=f"evt-{uuid4().hex}", sequence=sequence,
                run_id=run_id, conversation_id=state.conversation_id,
                type=RunEventType.RUN_CANCELLED,
                payload={"status": "cancelled", "reason": "cancelled_by_user"}), cancelled)
            self._notify(self._store.events(run_id, sequence - 1)[0])
            self._active_run_id = None
            self._loops.pop(run_id, None)
            self._cancel_signals.pop(run_id, None)
            return cancelled

    def _persist(self, result: AgentLoopResult) -> None:
        existing = len(self._store.events(result.state.id))
        if existing >= len(result.events):
            self._store.update_snapshot(result.state)
            return
        for index, source in enumerate(result.events):
            event = replace(source, sequence=existing + index + 1)
            if source.type in {RunEventType.RUN_COMPLETED, RunEventType.RUN_BLOCKED,
                               RunEventType.RUN_FAILED, RunEventType.RUN_CANCELLED,
                               RunEventType.RUN_INTERRUPTED}:
                snapshot = result.state
            elif source.type is RunEventType.PERMISSION_REQUESTED:
                snapshot = result.state
            else:
                snapshot = replace(result.state, status=RunStatus.RUNNING,
                                   terminal_reason=None)
            self._store.append(event, snapshot)

    def _persist_live(self, source: RunEvent, initial: RunState) -> None:
        """Journal an emitted event before returning control to the agent loop."""
        with self._lock:
            if self._store.snapshot(initial.id).finished:
                return
            existing = len(self._store.events(initial.id))
            event = replace(source, sequence=existing + 1)
            snapshot = replace(initial, status=RunStatus.RUNNING,
                               terminal_reason=None)
            self._store.append(event, snapshot)
        self._notify(event)

    def _notify(self, event: RunEvent) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                continue

    def _release_if_terminal(self, state: RunState) -> None:
        if not state.finished:
            return
        with self._lock:
            if self._active_run_id == state.id:
                self._active_run_id = None
            self._loops.pop(state.id, None)
            self._cancel_signals.pop(state.id, None)
