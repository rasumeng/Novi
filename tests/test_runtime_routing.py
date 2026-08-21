"""Phase B — runtime write-path routing tests.

Rule #2 enforcement at the unit level: the Runtime never writes to storage
directly; it reports events through the Brain. `_remember` must route
through `Brain.observe()` when a brain is present. The legacy
`memory.add_interaction` fallback has been removed — with no brain,
`_remember` only appends to the in-memory history.
"""

from cozmo.brain.types import Turn
from cozmo.runtime.runtime import CozmoRuntime


class RecordingBrain:
    def __init__(self):
        self.observed = []
        self.fail = False

    def observe(self, turn: Turn):
        if self.fail:
            raise RuntimeError("brain down")
        self.observed.append(turn)


class StubMemory:
    def __init__(self):
        self.interactions = []

    def add_interaction(self, user, assistant):
        self.interactions.append((user, assistant))


def make_runtime(memory, brain):
    rt = object.__new__(CozmoRuntime)
    rt.history = []
    rt.max_history = 10
    rt._summary = ""
    rt.simple_llm = None
    rt.memory = memory
    rt.brain = brain
    return rt


def test_remember_routes_through_brain_when_present():
    mem = StubMemory()
    brain = RecordingBrain()
    rt = make_runtime(mem, brain)
    rt._remember("hi", "hello there")
    assert len(brain.observed) == 1
    turn = brain.observed[0]
    assert isinstance(turn, Turn)
    assert turn.user == "hi"
    assert turn.assistant == "hello there"
    assert mem.interactions == []


def test_remember_appends_history_before_observing():
    brain = RecordingBrain()
    rt = make_runtime(None, brain)
    rt._remember("hi", "hello")
    assert rt.history == [("hi", "hello")]


def test_remember_brain_failure_is_swallowed():
    brain = RecordingBrain()
    brain.fail = True
    rt = make_runtime(StubMemory(), brain)
    rt._remember("hi", "hello")
    assert rt.history == [("hi", "hello")]


def test_remember_without_brain_only_appends_history():
    """No brain → no observe, no legacy memory fallback, history only."""
    mem = StubMemory()
    rt = make_runtime(mem, brain=None)
    rt._remember("hi", "hello")
    assert rt.history == [("hi", "hello")]
    assert mem.interactions == []


def test_remember_without_brain_or_memory_is_history_only():
    rt = make_runtime(None, brain=None)
    rt._remember("hi", "hello")
    assert rt.history == [("hi", "hello")]


def test_remember_compacts_history():
    brain = RecordingBrain()
    rt = make_runtime(StubMemory(), brain)
    rt.max_history = 3
    for i in range(6):
        rt._remember(f"u{i}", f"a{i}")
    assert len(rt.history) <= 3
