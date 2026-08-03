"""Brain facade tests (Phases A + B + C).

Phase A: facade establishes seams without adding behavior; every cognition
method delegates to an injected component; knowledge model types match the
blueprint.

Phase B: observe() persists to the conversation store, then emits
ConversationObserved — in that order, with the event describing only completed
persistence.

Phase C: observe() additionally runs buffered extraction (every 5 turns) →
KnowledgeItems + scenario → KnowledgeExtracted. The legacy MemoryManager shim
is gone; without extractor/layers, observe() behaves as Phase B.
"""

import pytest
from types import SimpleNamespace

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
from cozmo.brain.reasoning.extraction import ExtractedClaim, ExtractionResult
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
        self.links = {}
        self._records = {}

    def append(self, turn, conversation_id):
        if self.fail:
            raise RuntimeError("store down")
        self.appended.append((turn, conversation_id))

    def get(self, conversation_id):
        record = self._records.get(conversation_id)
        if record is None:
            record = SimpleNamespace(scenario_id=self.links.get(conversation_id))
            self._records[conversation_id] = record
        return record

    def set_scenario_id(self, conversation_id, scenario_id):
        self.links[conversation_id] = scenario_id
        record = self._records.get(conversation_id)
        if record is not None:
            record.scenario_id = scenario_id


class RecordingBus:
    def __init__(self, raise_on_emit=False):
        self.events = []
        self._raise_on_emit = raise_on_emit

    def emit(self, event_type, **data):
        if self._raise_on_emit:
            raise RuntimeError("bus down")
        self.events.append((event_type, data))


class StubExtractor:
    def __init__(self, fail=False, claims=1):
        self.fail = fail
        self.claims = claims
        self.extracted = 0

    def extract(self, turns):
        self.extracted += 1
        if self.fail:
            raise RuntimeError("extract down")
        return ExtractionResult(
            claims=tuple(
                ExtractedClaim(f"User prefers python {i}", 0.9, ("preference",))
                for i in range(self.claims)
            ),
            summary="The user prefers python.",
            name="Python preferences",
        )


class StubKnowledgeLayer:
    def __init__(self, fail=False):
        self.stored = []
        self.fail = fail

    def store_extracted(self, conversation_id, scenario_id, result):
        if self.fail:
            raise RuntimeError("knowledge write down")
        ids = [f"kn-{i}" for i in range(len(result.claims))]
        self.stored.append((conversation_id, scenario_id, result))
        return ids


class StubScenarioLayer:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
        self.n = 0

    def ensure_for_conversation(self, conversation, result):
        if self.fail:
            raise RuntimeError("scenario down")
        existing = getattr(conversation, "scenario_id", None)
        if existing:
            self.calls.append("update")
            return existing
        self.n += 1
        self.calls.append("create")
        return f"scn-{self.n}"


class StubProjectIndex:
    def __init__(self, root):
        self.root = root


def test_observe_does_not_write_legacy_memory():
    mem = StubMemory()
    Brain(memory=mem).observe(Turn(user="u", assistant="a"))
    assert mem.interactions == []


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
    assert mem.interactions == []
    assert len(bus.events) == 1
    kind, data = bus.events[0]
    assert kind == "conversation.observed"
    assert data["conversation_id"] == cid
    assert data["user"] == "u"
    assert data["assistant"] == "a"


def test_observe_order_store_then_event():
    order = []

    class OrderedStore(StubStore):
        def append(self, turn, conversation_id):
            order.append("store")
            super().append(turn, conversation_id)

    class OrderedBus(RecordingBus):
        def emit(self, event_type, **data):
            order.append("event")
            super().emit(event_type, **data)

    brain = Brain(
        memory=StubMemory(),
        conversation_store=OrderedStore(),
        event_bus=OrderedBus(),
    )
    brain.observe(Turn(user="u", assistant="a"))
    assert order == ["store", "event"]


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
    assert mem.interactions == []


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
    assert mem.interactions == []
    assert bus.events == []


def test_observe_event_failure_does_not_raise():
    store = StubStore()
    bus = RecordingBus(raise_on_emit=True)
    brain = Brain(memory=StubMemory(), conversation_store=store, event_bus=bus)
    brain.observe(Turn(user="u", assistant="a"))
    assert len(store.appended) == 1


def test_observe_extracts_after_batch():
    store = StubStore()
    bus = RecordingBus()
    brain = Brain(
        memory=StubMemory(),
        conversation_store=store,
        event_bus=bus,
        extractor=StubExtractor(),
        knowledge_layer=StubKnowledgeLayer(),
        scenario_layer=StubScenarioLayer(),
    )
    for i in range(5):
        brain.observe(Turn(user=f"u{i}", assistant=f"a{i}"))

    kinds = [k for k, _ in bus.events]
    assert kinds.count("conversation.observed") == 5
    assert "knowledge.extracted" in kinds
    extracted = next(d for k, d in bus.events if k == "knowledge.extracted")
    assert extracted["knowledge_ids"] == ["kn-0"]
    assert extracted["conversation_id"].startswith("conv-")
    assert extracted["scenario_id"] == "scn-1"
    assert len(store.links) == 1


def test_observe_does_not_extract_before_batch_fills():
    bus = RecordingBus()
    brain = Brain(
        memory=StubMemory(),
        conversation_store=StubStore(),
        event_bus=bus,
        extractor=StubExtractor(),
        knowledge_layer=StubKnowledgeLayer(),
        scenario_layer=StubScenarioLayer(),
    )
    for i in range(4):
        brain.observe(Turn(user=f"u{i}", assistant=f"a{i}"))
    assert "knowledge.extracted" not in [k for k, _ in bus.events]


def test_observe_extraction_failure_keeps_persist():
    store = StubStore()
    bus = RecordingBus()
    brain = Brain(
        memory=StubMemory(),
        conversation_store=store,
        event_bus=bus,
        extractor=StubExtractor(fail=True),
        knowledge_layer=StubKnowledgeLayer(),
        scenario_layer=StubScenarioLayer(),
    )
    for i in range(5):
        brain.observe(Turn(user=f"u{i}", assistant=f"a{i}"))

    kinds = [k for k, _ in bus.events]
    assert kinds.count("conversation.observed") == 5
    assert "knowledge.extracted" not in kinds
    assert len(store.appended) == 5


def test_observe_knowledge_write_failure_keeps_persist():
    store = StubStore()
    bus = RecordingBus()
    brain = Brain(
        memory=StubMemory(),
        conversation_store=store,
        event_bus=bus,
        extractor=StubExtractor(),
        knowledge_layer=StubKnowledgeLayer(fail=True),
        scenario_layer=StubScenarioLayer(),
    )
    for i in range(5):
        brain.observe(Turn(user=f"u{i}", assistant=f"a{i}"))
    kinds = [k for k, _ in bus.events]
    assert kinds.count("conversation.observed") == 5
    assert "knowledge.extracted" not in kinds


def test_observe_extractor_absent_is_phase_b():
    store = StubStore()
    bus = RecordingBus()
    brain = Brain(memory=StubMemory(), conversation_store=store, event_bus=bus)
    for i in range(6):
        brain.observe(Turn(user=f"u{i}", assistant=f"a{i}"))
    assert [k for k, _ in bus.events] == ["conversation.observed"] * 6


def test_observe_reuses_scenario_across_batches():
    store = StubStore()
    bus = RecordingBus()
    scenario = StubScenarioLayer()
    brain = Brain(
        memory=StubMemory(),
        conversation_store=store,
        event_bus=bus,
        extractor=StubExtractor(),
        knowledge_layer=StubKnowledgeLayer(),
        scenario_layer=scenario,
    )
    for i in range(10):
        brain.observe(Turn(user=f"u{i}", assistant=f"a{i}"))

    assert scenario.calls == ["create", "update"]
    extracted = [
        d["scenario_id"] for k, d in bus.events if k == "knowledge.extracted"
    ]
    assert extracted == ["scn-1", "scn-1"]


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
