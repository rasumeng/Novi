"""Phase F — Step 5: retrieval tiering tests.

Covers the §5 lexicographic hierarchy: a rare stable preference outranks a
recently discussed temporary topic; verified > corroborated > candidate at
equal importance; scenario-anchored beats out-of-scenario at equal tiers;
recency only breaks equal (importance, confidence) pairs; superseded excluded
by default, included on request.
"""

from datetime import datetime, timedelta

from cozmo.brain.reasoning.resolver import LayeredRetrievalResolver
from cozmo.brain.reasoning.tiering import (
    bucket_confidence,
    bucket_importance,
    tier_hits,
)
from cozmo.brain.types import (
    KnowledgeForm,
    KnowledgeHit,
    KnowledgeItem,
    KnowledgeStatus,
    QueryContext,
    Scenario,
    ScenarioStatus,
)


def _hit(id, content, score=0.7, status=KnowledgeStatus.VERIFIED,
         importance=0.0, last_seen=None, scenario_id=None, tags=("preference",)):
    return KnowledgeHit(
        item=KnowledgeItem(
            id=id,
            content=content,
            form=KnowledgeForm.ATOMIC,
            confidence=0.9,
            status=status,
            tags=tags,
            scenario_id=scenario_id,
            last_seen_at=last_seen,
            importance=importance,
        ),
        score=score,
        distance=1.0 - score,
    )


def _dt(day):
    return datetime(2026, 1, day)


def test_rare_stable_preference_beats_recent_temporary_topic():
    stable = _hit(
        "stable", "prefers dark mode", importance=0.9, last_seen=_dt(1)
    )
    recent = _hit(
        "recent", "bikes to work this week", importance=0.1, last_seen=_dt(5)
    )
    ordered = tier_hits([recent, stable])
    assert [h.item.id for h in ordered] == ["stable", "recent"]


def test_verified_beats_corroborated_beats_candidate_equal_importance():
    verified = _hit("v", "prefers python", status=KnowledgeStatus.VERIFIED, importance=0.5)
    corroborated = _hit("c", "likes vim", status=KnowledgeStatus.CORROBORATED, importance=0.5)
    candidate = _hit("d", "maybe rust", status=KnowledgeStatus.CANDIDATE, importance=0.5)
    ordered = tier_hits([candidate, corroborated, verified])
    assert [h.item.id for h in ordered] == ["v", "c", "d"]


def test_scenario_anchored_beats_out_of_scenario_equal_tiers():
    anchored = _hit("in", "prefers python", importance=0.5, scenario_id="scn-1")
    other = _hit("out", "likes vim", importance=0.5, scenario_id="scn-9")
    ordered = tier_hits([other, anchored], active_scenario_ids={"scn-1"})
    assert [h.item.id for h in ordered] == ["in", "out"]


def test_recency_breaks_ties_within_equal_importance_confidence():
    old = _hit("old", "prefers python", importance=0.5, last_seen=_dt(1))
    fresh = _hit("fresh", "likes vim", importance=0.5, last_seen=_dt(5))
    ordered = tier_hits([old, fresh])
    assert [h.item.id for h in ordered] == ["fresh", "old"]


def test_superseded_excluded_by_default():
    live = _hit("live", "prefers python")
    dead = _hit("dead", "prefers rust", status=KnowledgeStatus.SUPERSEDED)
    ordered = tier_hits([dead, live])
    assert [h.item.id for h in ordered] == ["live"]


def test_superseded_included_when_flag_set():
    live = _hit("live", "prefers python")
    dead = _hit("dead", "prefers rust", status=KnowledgeStatus.SUPERSEDED, last_seen=_dt(5))
    ordered = tier_hits([dead, live], include_superseded=True)
    assert {h.item.id for h in ordered} == {"live", "dead"}


def test_bucket_functions():
    assert bucket_importance(0.9) == 2
    assert bucket_importance(0.5) == 1
    assert bucket_importance(0.1) == 0
    assert bucket_confidence(KnowledgeStatus.VERIFIED) == 2
    assert bucket_confidence(KnowledgeStatus.CORROBORATED) == 1
    assert bucket_confidence(KnowledgeStatus.CANDIDATE) == 0


class FakeBackend:
    def __init__(self):
        self.knowledge = []
        self.scenarios = {}
        self.memory = []
        self.knowledge_calls = []

    def load(self, scenario_id):
        return self.scenarios.get(scenario_id)

    def query_scoped(self, query, scenario_id=None, k=5, distance_threshold=0.5):
        self.knowledge_calls.append((query, scenario_id, k, distance_threshold))
        return [h for h in self.knowledge if h.item.scenario_id == scenario_id]

    def query_memory(self, query, k, threshold):
        return self.memory


def make_scenario(sid="scn-1"):
    return Scenario(
        id=sid,
        name="Build",
        purpose="Working on the build.",
        project_id=None,
        status=ScenarioStatus.ACTIVE,
        summary="Working on the build.",
    )


def test_resolver_tiering_flag_on_reorders_knowledge():
    backend = FakeBackend()
    backend.scenarios["scn-1"] = make_scenario("scn-1")
    backend.knowledge = [
        _hit("recent", "bikes to work", score=0.95, importance=0.1, last_seen=_dt(5), scenario_id="scn-1"),
        _hit("stable", "prefers dark mode", score=0.8, importance=0.9, last_seen=_dt(1), scenario_id="scn-1"),
    ]
    res = LayeredRetrievalResolver(
        load_scenario=backend.load,
        query_knowledge=backend.query_scoped,
        query_memory=backend.query_memory,
        tiered=True,
    )
    result = res.recall("preferences", QueryContext(scenario_id="scn-1", top_k=5))
    ids = [i.metadata.get("id") for i in result.items if i.source == "knowledge"]
    assert ids[0] == "stable"
    assert ids[1] == "recent"


def test_resolver_tiering_off_preserves_similarity_order():
    backend = FakeBackend()
    backend.scenarios["scn-1"] = make_scenario("scn-1")
    backend.knowledge = [
        _hit("recent", "bikes to work", score=0.95, importance=0.1, last_seen=_dt(5), scenario_id="scn-1"),
        _hit("stable", "prefers dark mode", score=0.8, importance=0.9, last_seen=_dt(1), scenario_id="scn-1"),
    ]
    res = LayeredRetrievalResolver(
        load_scenario=backend.load,
        query_knowledge=backend.query_scoped,
        query_memory=backend.query_memory,
        tiered=False,
    )
    result = res.recall("preferences", QueryContext(scenario_id="scn-1", top_k=5))
    ids = [i.metadata.get("id") for i in result.items if i.source == "knowledge"]
    assert ids[0] == "recent"
    assert ids[1] == "stable"


def test_tiering_is_stable_equal_tiers_keep_store_order():
    a = _hit("a", "prefers python", score=0.9, importance=0.5)
    b = _hit("b", "likes vim", score=0.88, importance=0.5)
    ordered = tier_hits([a, b])
    assert [h.item.id for h in ordered] == ["a", "b"]