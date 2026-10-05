"""Application owner for durable foreground agent runs."""

from __future__ import annotations

import threading
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Protocol
from uuid import uuid4

from novi.runtime.agent_loop import AgentLoop, AgentLoopResult
from novi.runtime.run_contracts import RunEvent, RunEventType, RunRequest, RunState, RunStatus
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage
from novi.services.permission_service import PermissionDecision, PermissionDecisionKind, PermissionService
from novi.services.run_store import RunStore


class LoopFactory(Protocol):
    def __call__(self, state: RunState, cancelled: Callable[[], bool]) -> AgentLoop: ...


class RunBusyError(RuntimeError):
    pass


class RunService:
    def __init__(self, store: RunStore, loop_factory: LoopFactory, *,
                 permission_service: PermissionService | None = None,
                 id_factory: Callable[[], str] | None = None,
                 recover_on_start: bool = False,
                 brain=None, inference=None) -> None:
        self._store = store
        self._loop_factory = loop_factory
        self._permissions = permission_service
        self._id_factory = id_factory or (lambda: f"run-{uuid4().hex}")
        self._brain = brain
        self._inference = inference
        self._lock = threading.RLock()
        self._active_run_id: str | None = None
        self._loops: dict[str, AgentLoop] = {}
        self._cancel_signals: dict[str, threading.Event] = {}
        self._listeners: list[Callable[[RunEvent], None]] = []
        self._permission_timers: dict[str, threading.Timer] = {}
        self._closing = False
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
            blocks = [ContentBlock(type=ContentBlockType.TEXT,
                                   text=request.user_text)]
            blocks.extend(ContentBlock.image(image) for image in request.images)
            user_message = TranscriptMessage(id=request.user_message_id,
                role=MessageRole.USER, source="user", trust="trusted",
                blocks=tuple(blocks))
            state = RunState(id=run_id, request=request,
                             transcript=inherited + (user_message,))
            signal = threading.Event()
            loop = self._loop_factory(state, signal.is_set)
            self._store.create(state)
            set_sink = getattr(loop, "set_event_sink", None)
            if callable(set_sink):
                set_sink(self._persist_live)
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
        return self._execute_loop(state, loop.run)

    def _execute_loop(self, state: RunState, run) -> RunState:
        # Hold admission through the whole turn, including tools and persistence.
        # Cancelling the UI does not release it while provider I/O is still alive.
        signal = self._cancel_signals.get(state.id)
        admission = self._inference.acquire_foreground(signal) if self._inference else nullcontext()
        try:
            with admission:
                if self._store.snapshot(state.id).finished:
                    return self._store.snapshot(state.id)
                return self._finish_execution(run(state))
        except (InterruptedError, TimeoutError) as exc:
            with self._lock:
                current = self._store.snapshot(state.id)
                if current.finished:
                    return current
                failed = replace(current, status=RunStatus.FAILED, terminal_reason=str(exc))
                event = RunEvent(id=f"evt-{uuid4().hex}",
                    sequence=len(self._store.events(state.id)) + 1,
                    run_id=state.id, conversation_id=state.conversation_id,
                    type=RunEventType.RUN_FAILED, payload={"reason": str(exc)})
                self._store.append(event, failed)
            self._notify(event)
            self._release_if_terminal(failed)
            return failed

    def _finish_execution(self, result: AgentLoopResult) -> RunState:
        self._persist(result)
        final_state = self._store.snapshot(result.state.id)
        self._ingest_memory(final_state)
        self._release_if_terminal(final_state)
        self._schedule_permission_expiry(final_state)
        return final_state

    def _schedule_permission_expiry(self, state: RunState) -> None:
        if self._permissions is None or state.status is not RunStatus.AWAITING_PERMISSION:
            return
        with self._lock:
            if self._closing or self._store.snapshot(state.id).finished:
                return
            self._permission_timers = {key: timer for key, timer in self._permission_timers.items()
                                       if timer.is_alive()}
            for request_id in state.pending_permission_ids:
                if request_id in self._permission_timers:
                    continue
                request = self._permissions.request(request_id)
                delay = max(0, (request.expires_at - datetime.now(timezone.utc)).total_seconds())
                timer = threading.Timer(delay, self._expire_permission, args=(request_id,))
                timer.daemon = True
                self._permission_timers[request_id] = timer
                timer.start()

    def _expire_permission(self, request_id: str) -> None:
        request = self._permissions.request(request_id)
        decision = PermissionDecision(request_id=request_id,
            kind=PermissionDecisionKind.EXPIRED, origin="system",
            argument_digest=request.argument_digest)
        try:
            self.respond_permission(request_id, decision)
        except ValueError:
            # A response or cancellation won the race with the timer.
            pass

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
        with self._lock:
            self._closing = True
            timers = tuple(self._permission_timers.values())
            self._permission_timers.clear()
            for timer in timers:
                timer.cancel()
        for timer in timers:
            if timer is not threading.current_thread():
                timer.join()
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
            timer = self._permission_timers.get(request_id)
            if timer:
                timer.cancel()
        return self._execute_loop(state, loop.resume_permission)

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
            terminal_event = RunEvent(id=f"evt-{uuid4().hex}", sequence=sequence,
                run_id=run_id, conversation_id=state.conversation_id,
                type=RunEventType.RUN_CANCELLED,
                payload={"status": "cancelled", "reason": "cancelled_by_user"})
            self._store.append(terminal_event, cancelled)
            for request_id in state.pending_permission_ids:
                timer = self._permission_timers.get(request_id)
                if timer:
                    timer.cancel()
            self._notify(terminal_event)
            self._active_run_id = None
            self._loops.pop(run_id, None)
            self._cancel_signals.pop(run_id, None)
            # Even cancelled runs ingest user fact (not tool success)
            self._ingest_memory(cancelled)
            return cancelled

    def _persist(self, result: AgentLoopResult) -> None:
        # A user cancellation may win the race while the loop is returning its
        # own terminal result. The durable terminal transition is authoritative;
        # never append a second terminal event from the losing worker.
        current = self._store.snapshot(result.state.id)
        if current.finished:
            return
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

    def _persist_live(self, source: RunEvent, state: RunState) -> None:
        """Journal an emitted event before returning control to the agent loop."""
        with self._lock:
            if self._store.snapshot(state.id).finished:
                return
            existing = len(self._store.events(state.id))
            event = replace(source, sequence=existing + 1)
            self._store.append(event, state)
        self._notify(event)

    def _notify(self, event: RunEvent) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                continue

    def _ingest_memory(self, state: RunState) -> None:
        if self._brain is None or not state.finished:
            return
        try:
            from novi.services.run_memory import ingest_run
            ingest_run(state, self._brain)
        except Exception:
            pass

    def _release_if_terminal(self, state: RunState) -> None:
        if not state.finished:
            return
        with self._lock:
            if self._active_run_id == state.id:
                self._active_run_id = None
            self._loops.pop(state.id, None)
            self._cancel_signals.pop(state.id, None)
