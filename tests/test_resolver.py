"""Phase E — layered retrieval resolver tests."""

from cozmo.brain.brain import Brain
from cozmo.brain.reasoning.resolver import LayeredRetrievalResolver
from cozmo.brain.types import QueryContext, Scenario, ScenarioStatus


class FakeBackend:
    """In-memory stand-in for the injected read callables."""

    def __init__(self):
        self.scenarios = {}
        self.knowledge = []  # dict rows with scenario_id key
        self.memory = []
        self.knowledge_calls = []
        self.memory_calls = []
        self.loaded = []

    def load(self, scenario_id):
        self.loaded.append(scenario_id)
        return self.scenarios.get(scenario_id)

    def query_scoped(self, query, scenario_id=None, k=5, distance_threshold=0.5):
        self.knowledge_calls.append((query, scenario_id, k, distance_threshold))
        return [r for r in self.knowledge if r.get("scenario_id") == scenario_id]

    def query_memory(self, query, k, threshold):
        self.memory_calls.append((query, k, threshold))
        return self.memory


def make_scenario(sid="scn-1", summary="Working on the Cozmo build."):
    return Scenario(
        id=sid,
        name="Build",
        purpose=summary,
        project_id=None,
        status=ScenarioStatus.ACTIVE,
        summary=summary,
    )


def knowledge_row(sid, score, text):
    return {
        "id": f"kn-{score}",
        "text": text,
        "score": score,
        "scenario_id": sid,
        "metadata": {},
    }


def memory_row(text, score=0.3):
    return {"id": "mem", "text": text, "score": score, "metadata": {}}


def build(suf=0.4):
    fb = FakeBackend()
    res = LayeredRetrievalResolver(
        load_scenario=fb.load,
        query_knowledge=fb.query_scoped,
        query_memory=fb.query_memory,
        sufficiency=suf,
    )
    return res, fb


def test_scenario_anchors_recall_and_gates_at_knowledge():
    res, fb = build()
    fb.scenarios["scn-1"] = make_scenario()
    fb.knowledge = [knowledge_row("scn-1", 0.9, "Build uses uv and ruff.")]

    result = res.recall("how is the build run?", QueryContext(scenario_id="scn-1"))

    assert fb.loaded == ["scn-1"]
    assert fb.memory_calls == []
    sources = [i.source for i in result.items]
    assert sources == ["scenario", "knowledge"]
    assert result.metrics["gate"] == "knowledge"
    assert result.metrics["plan"].scoped_knowledge == 1
    assert result.metrics["plan"].layers == ("scenario", "knowledge")


def test_sufficiency_gate_expands_to_global_knowledge():
    res, fb = build(suf=0.6)
    fb.scenarios["scn-1"] = make_scenario()
    fb.knowledge = [
        knowledge_row("scn-1", 0.2, "low-relevance scoped item"),
        knowledge_row(None, 0.9, "high-relevance global knowledge"),
    ]

    result = res.recall(
        "some query",
        QueryContext(scenario_id="scn-1", top_k=3, distance_threshold=0.9),
    )

    assert fb.memory_calls == []
    assert result.metrics["gate"] == "knowledge"
    assert result.metrics["plan"].global_knowledge == 1
    assert result.metrics["plan"].scoped_knowledge == 1


def test_gate_fails_into_conversation_memory():
    res, fb = build(suf=0.8)
    fb.scenarios["scn-1"] = make_scenario()
    fb.knowledge = [
        knowledge_row("scn-1", 0.4, "weak scoped"),
        knowledge_row(None, 0.3, "weak global"),
    ]
    fb.memory = [memory_row("user once said use connection pooling")]

    result = res.recall("what did they say about pooling?", QueryContext(scenario_id="scn-1"))

    assert fb.memory_calls == [("what did they say about pooling?", 5, 0.5)]
    assert result.metrics["gate"] == "conversation"
    assert result.metrics["plan"].layers == ("scenario", "knowledge", "conversation")
    assert any(i.source == "memory" for i in result.items)


def test_no_scenario_skips_scenario_layer():
    res, fb = build()
    fb.knowledge = [knowledge_row(None, 0.9, "global knowledge hit")]

    result = res.recall("anything", QueryContext())

    assert fb.loaded == []
    assert result.metrics["plan"].scenario_id is None
    assert result.metrics["plan"].layers == ("knowledge",)


def test_weak_results_after_memory_failure_keeps_knowledge():
    res, fb = build(suf=0.9)
    fb.knowledge = [knowledge_row(None, 0.2, "weak global")]
    fb.memory = []

    result = res.recall("q", QueryContext())

    assert result.metrics["gate"] == "conversation"
    assert fb.memory_calls == [("q", 5, 0.5)]


def test_memory_backend_failure_does_not_raise():
    res, fb = build(suf=0.9)
    fb.knowledge = [knowledge_row(None, 0.2, "weak global")]
    fb.memory = None  # backend raises when touched

    def boom(*args):
        raise RuntimeError("memory store down")

    res = LayeredRetrievalResolver(
        load_scenario=fb.load,
        query_knowledge=fb.query_scoped,
        query_memory=boom,
        sufficiency=0.9,
    )

    result = res.recall("q", QueryContext())
    assert result.metrics["gate"] == "conversation"
    assert not any(i.source == "memory" for i in result.items)
    assert all(i.source == "knowledge" for i in result.items)


# ── Brain wiring ──────────────────────────────────────────────────────────


class MemoryManagerStub:
    def __init__(self, rows):
        self.rows = rows

    def query(self, **kwargs):
        return self.rows


class FakeLayerStore:
    def __init__(self, scenario=None):
        self.scenario = scenario

    def get(self, scenario_id):
        return self.scenario


class FakeKnowledgeLayer:
    def __init__(self, rows):
        self.rows = rows

    def query_scoped(self, text, *, scenario_id=None, k=5, distance_threshold=0.5, forms=None, tags=None):
        return [r for r in self.rows if r.get("scenario_id") == scenario_id]


class FakeScenarioLayer:
    def __init__(self, scenario):
        self.store = FakeLayerStore(scenario)


def test_brain_recall_routes_through_resolver_when_layers_present():
    scenario = make_scenario()
    brain = Brain(
        memory=FakeKnowledgeBackend(),
        knowledge_layer=FakeKnowledgeLayer(
            [knowledge_row("scn-1", 0.9, "Build uses uv and ruff.")]
        ),
        scenario_layer=FakeScenarioLayer(scenario),
    )

    result = brain.recall("how is the build run?", QueryContext(scenario_id="scn-1"))

    assert result.metrics["gate"] == "knowledge"
    assert result.metrics["plan"].scenario_id == "scn-1"
    sources = [i.source for i in result.items]
    assert "scenario" in sources
    assert "knowledge" in sources


def test_brain_recall_no_layers_is_legacy_memory():
    brain = Brain(memory=FakeKnowledgeBackend())
    result = brain.recall("anything", QueryContext())
    assert result.metrics == {}
    assert all(i.source == "memory" for i in result.items)


class FakeKnowledgeBackend:
    def query(self, **kwargs):
        return [{"text": "user prefers python", "score": 0.8, "metadata": {}}]
