"""Application-wide admission for background memory and interactive model work.

Cancellation requests do not release a lease: only the owner can confirm that
backend execution has ended. This prevents overlapping calls after a timeout.
"""
from contextlib import contextmanager
from threading import Condition, Event
from time import monotonic
from functools import wraps


def foreground_run(method):
    """Guard an entire streamed runtime, including nested advisory/tool calls."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        from contextlib import nullcontext
        coordinator = getattr(self.model_service, 'inference', None)
        if coordinator is not None and coordinator.memory_active:
            yield ('status', 'Pausing memory update to respond…')
        with coordinator.acquire_foreground(self.stop_event) if coordinator else nullcontext():
            yield from method(self, *args, **kwargs)
    return wrapped


class InferenceCoordinator:
    def __init__(self, clock=monotonic):
        self._condition = Condition()
        self._clock = clock
        self._foreground = 0
        self._waiting = 0
        self._memory = None
        self._closed = False
        self._last_foreground = clock()

    @property
    def memory_active(self):
        with self._condition:
            return self._memory is not None

    @property
    def foreground_active(self):
        with self._condition:
            return bool(self._foreground or self._waiting)

    @contextmanager
    def acquire_foreground(self, cancel_event=None, timeout=10):
        deadline = self._clock() + timeout
        acquired = False
        with self._condition:
            self._waiting += 1
            try:
                if self._memory is not None:
                    self._memory.set()
                while self._memory is not None:
                    if self._closed or (cancel_event and cancel_event.is_set()):
                        raise InterruptedError('Model handoff cancelled')
                    if self._clock() >= deadline:
                        raise TimeoutError('Memory inference has not released the model')
                    self._condition.wait(.05)
                if self._closed or (cancel_event and cancel_event.is_set()):
                    raise InterruptedError('Inference stopped')
                self._foreground += 1
                acquired = True
            finally:
                self._waiting -= 1
        try:
            yield
        finally:
            if acquired:
                with self._condition:
                    self._foreground -= 1
                    self._last_foreground = self._clock()
                    self._condition.notify_all()

    @contextmanager
    def try_acquire_memory(self, cancel_event=None, idle_seconds=60):
        token = cancel_event if cancel_event is not None else Event()
        with self._condition:
            admitted = not (self._closed or self._foreground or self._waiting or
                            self._memory is not None or token.is_set() or
                            self._clock() - self._last_foreground < idle_seconds)
            if admitted:
                self._memory = token
        try:
            yield token if admitted else None
        finally:
            if admitted:
                with self._condition:
                    self._memory = None
                    self._condition.notify_all()

    def pause_memory(self):
        with self._condition:
            if self._memory is not None:
                self._memory.set()

    def close(self):
        with self._condition:
            self._closed = True
            if self._memory is not None:
                self._memory.set()
            self._condition.notify_all()
