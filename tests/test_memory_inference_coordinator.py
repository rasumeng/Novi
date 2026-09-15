from threading import Event, Thread
import pytest
from novi.services.inference_coordinator import InferenceCoordinator


def test_foreground_cancels_memory_but_waits_for_release():
    c = InferenceCoordinator()
    entered = Event()
    with c.try_acquire_memory(idle_seconds=0) as token:
        def foreground():
            with c.acquire_foreground():
                entered.set()
        thread = Thread(target=foreground)
        thread.start()
        assert token.wait(1)
        assert not entered.is_set()
        with c.try_acquire_memory(idle_seconds=0) as other:
            assert other is None
    thread.join(1)
    assert entered.is_set()


def test_foreground_and_idle_period_prevent_background():
    clock = [0]
    c = InferenceCoordinator(clock=lambda: clock[0])
    with c.acquire_foreground():
        clock[0] = 100
        with c.try_acquire_memory() as token:
            assert token is None
    with c.try_acquire_memory() as token:
        assert token is None
    clock[0] = 161
    with c.try_acquire_memory() as token:
        assert token is not None


def test_timeout_never_releases_an_active_backend_lease():
    c = InferenceCoordinator()
    with c.try_acquire_memory(idle_seconds=0):
        with pytest.raises(TimeoutError):
            with c.acquire_foreground(timeout=0):
                pytest.fail('overlapping call')
        with c.try_acquire_memory(idle_seconds=0) as token:
            assert token is None


def test_close_cancels_without_waiting_and_prevents_new_work():
    c = InferenceCoordinator()
    with c.try_acquire_memory(idle_seconds=0) as token:
        c.close()
        assert token.is_set()
    with pytest.raises(InterruptedError):
        with c.acquire_foreground():
            pass
    with c.try_acquire_memory(idle_seconds=0) as token:
        assert token is None
