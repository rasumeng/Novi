"""Brain facade tests (Phases A + B).

Phase A: facade establishes seams without adding behavior; every cognition
method delegates to an injected component; knowledge model types match the
blueprint.

Phase B: observe() persists to the conversation store, keeps the legacy
memory shim alive, then emits ConversationObserved — in that order, with the
event describing only completed persistence.
"""

import pytest

from cozmo.brain import (
    Brain,
    ContextResolution,
    EdgeKind,
    KnowledgeForm,
    KnowledgeItem,
    KnowledgeStatus,
    QueryContext,
    RecallItem,
    RecallResult,
    ReflectionReport,
    Turn,
)
from cozmo.brain.types import Scenario, ScenarioStatus


class StubMemory:
    def __init__(self):
        self.interactions = []
        self.facts = []
        self.queries = []
        self.consolidations = 0
        self._results = []

    def add_interaction(self, user, assistant):
        self.interactions.append((user, assistant))

    def store_fact(self, statement):
        self.facts.append(statement)

    def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
        self.queries.append((text, k, distance_threshold, memory_types))
        return self._results

    def consolidate(self):
        self.consolidations += 1
        return 2


class StubStore:
    def __init__(self):
        self.appended = []
        self.fail = False

    def append(self, turn, conversation_id):
        if self.fail:
            raise RuntimeError("store down")
        self.appended.append((turn, conversation_id))


class RecordingBus:
    def __init__(self, raise_on_emit=False):
        self.events = []
        self._raise_on_emit = raise_on_emit

    def emit(self, event_type, **data):
        if self._raise_on_emit:
            raise RuntimeError("bus down")
        self.events.append((event_type, data))


class StubProjectIndex:
    def __init__(self, root):
        self.root = root


def test_observe_legacy_shim_writes_memory():
    mem = StubMemory()
    Brain(memory=mem).observe(Turn(user="u", assistant="a"))
    assert mem.interactions == [("u", "a")]


def test_observe_persists_and_emits():
    mem = StubMemory()
    store = StubStore()
    bus = RecordingBus()
    brain = Brain(memory=mem, conversation_store=store, event_bus=bus)

    brain.observe(Turn(user="u", assistant="a"))

    assert len(store.appended) == 1
    turn, cid = store.appended[0]
    assert turn.user == "u"
    assert turn.assistant == "a"
    assert cid.startswith("conv-")
    assert mem.interactions == [("u", "a")]
    assert len(bus.events) == 1
    kind, data = bus.events[0]
    assert kind == "conversation.observed"
    assert data["conversation_id"] == cid
    assert data["user"] == "u"
    assert data["assistant"] == "a"


def test_observe_order_store_legacy_event():
    order = []

    class OrderedMemory(StubMemory):
        def add_interaction(self, user, assistant):
            order.append("memory")
            super().add_interaction(user, assistant)

    class OrderedStore(StubStore):
        def append(self, turn, conversation_id):
            order.append("store")
            super().append(turn, conversation_id)

    class OrderedBus(RecordingBus):
        def emit(self, event_type, **data):
            order.append("event")
            super().emit(event_type, **data)

    brain = Brain(
        memory=OrderedMemory(),
        conversation_store=OrderedStore(),
        event_bus=OrderedBus(),
    )
    brain.observe(Turn(user="u", assistant="a"))
    assert order == ["store", "memory", "event"]


def test_observe_honors_turn_conversation_id():
    store = StubStore()
    brain = Brain(memory=StubMemory(), conversation_store=store)
    brain.observe(Turn(user="u", assistant="a", conversation_id="conv-abc"))
    assert store.appended[0][1] == "conv-abc"


def test_observe_emitted_id_matches_turn_id():
    bus = RecordingBus()
    brain = Brain(
        memory=StubMemory(), conversation_store=StubStore(), event_bus=bus
    )
    brain.observe(Turn(user="u", assistant="a", conversation_id="conv-xyz"))
    assert bus.events[0][1]["conversation_id"] == "conv-xyz"


def test_observe_without_store_or_bus():
    mem = StubMemory()
    brain = Brain(memory=mem)
    brain.observe(Turn(user="u", assistant="a"))
    assert mem.interactions == [("u", "a")]


def test_observe_with_nothing_wired_is_noop():
    brain = Brain()
    brain.observe(Turn(user="u", assistant="a"))


def test_observe_store_failure_does_not_emit():
    store = StubStore()
    store.fail = True
    mem = StubMemory()
    bus = RecordingBus()
    brain = Brain(memory=mem, conversation_store=store, event_bus=bus)
    brain.observe(Turn(user="u", assistant="a"))
    assert mem.interactions == [("u", "a")]
    assert bus.events == []


def test_observe_event_failure_does_not_raise():
    store = StubStore()
    bus = RecordingBus(raise_on_emit=True)
    brain = Brain(memory=StubMemory(), conversation_store=store, event_bus=bus)
    brain.observe(Turn(user="u", assistant="a"))
    assert len(store.appended) == 1


def test_recall_without_memory_raises():
    from cozmo.memory import manager as memory_module

    saved = memory_module._memory_manager
    memory_module._memory_manager = None
    try:
        brain = Brain()
        with pytest.raises(RuntimeError):
            brain.recall("what do i prefer")
    finally:
        memory_module._memory_manager = saved


def test_recall_packages_query_results():
    mem = StubMemory()
    mem._results = [
        {"id": "1", "text": "prefers python", "metadata": {"type": "preference"}, "score": 0.8}
    ]
    brain = Brain(memory=mem)
    result = brain.recall(
        "what do i prefer",
        QueryContext(top_k=3, distance_threshold=0.4, memory_types=("preference",)),
    )
    assert mem.queries == [("what do i prefer", 3, 0.4, ["preference"])]
    assert isinstance(result, RecallResult)
    assert result.query == "what do i prefer"
    assert len(result.items) == 1
    item = result.items[0]
    assert isinstance(item, RecallItem)
    assert item.text == "prefers python"
    assert item.score == 0.8
    assert item.source == "memory"
    assert item.metadata == {"type": "preference"}


def test_learn_delegates_to_memory():
    mem = StubMemory()
    Brain(memory=mem).learn("The build uses uv", source="write_knowledge")
    assert mem.facts == ["The build uses uv"]


def test_resolve_uses_project_root():
    brain = Brain(memory=StubMemory(), project_index=StubProjectIndex("/tmp/proj"))
    res = brain.resolve("where are we")
    assert isinstance(res, ContextResolution)
    assert res.project_id == "/tmp/proj"
    assert res.scenario_id is None
    assert res.method == "cwd"


def test_resolve_without_project_is_empty():
    res = Brain(memory=StubMemory()).resolve("anything")
    assert isinstance(res, ContextResolution)
    assert res.project_id is None
    assert res.scenario_id is None
    assert res.method == "none"


def test_reflect_delegates_to_consolidate():
    mem = StubMemory()
    report = Brain(memory=mem).reflect()
    assert isinstance(report, ReflectionReport)
    assert report.merges == 2
    assert mem.consolidations == 1


def test_knowledge_item_defaults():
    item = KnowledgeItem(
        id="k1", form=KnowledgeForm.ATOMIC, content="prefers python", confidence=0.7
    )
    assert item.status is KnowledgeStatus.CANDIDATE
    assert item.tags == ()
    assert item.sources == ()
    assert item.scenario_id is None
    assert item.embedding is None


def test_edge_kinds_bounded():
    assert {e.value for e in EdgeKind} == {
        "derived_from",
        "observed_in",
        "supersedes",
        "references",
        "conflicts_with",
        "contains",
    }


def test_scenario_lifecycle_statuses():
    assert {s.value for s in ScenarioStatus} == {
        "created",
        "active",
        "paused",
        "completed",
        "archived",
    }
    scenario = Scenario(
        id="s1", name="Fix build", purpose="recover from failed build", project_id="p1"
    )
    assert scenario.status is ScenarioStatus.CREATED


def test_facade_over_real_memory_manager(tmp_path):
    from cozmo.memory.manager import MemoryManager
    from cozmo.services.embedding import EmbeddingService

    class StubLLM:
        def invoke(self, prompt):
            return "User prefers python over java."

    class FakeEmbed(EmbeddingService):
        def __init__(self, dim: int = 384):
            super().__init__({"embedding": {"model": "fake-embed"}})
            self._dim = dim

        @property
        def model_name(self):
            return "fake-embed"

        def encode(self, text, normalize=True):
            return [0.01] * self._dim

        @property
        def dimension(self):
            return self._dim

    mm = MemoryManager(
        StubLLM(),
        persist_dir=str(tmp_path / "mem"),
        embed_model=FakeEmbed(),
        max_turns=100,
        max_short_term_pairs=10,
    )
    brain = Brain(memory=mm)
    brain.learn("prefers python over java")
    result = brain.recall("what do they prefer")
    assert len(result.items) >= 1
    assert result.items[0].source == "memory"


def test_observe_with_real_conversation_store(tmp_path):
    from cozmo.brain.storage.conversation_store import ConversationStore

    store = ConversationStore(persist_dir=str(tmp_path / "convs"))
    brain = Brain(memory=StubMemory(), conversation_store=store)
    brain.observe(Turn(user="u", assistant="a", conversation_id="conv-1"))
    rec = store.get("conv-1")
    assert rec is not None
    assert rec.turn_count == 1
    assert len(store.turns("conv-1")) == 1
