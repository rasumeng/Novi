"""Phase F — Step 7: acceptance + trust surface tests.

Integrated cases across the step suites plus the inspect/correct facades and a
bounded synthetic user-profile scenario (no LLM). Verifies the design §14
acceptance items: consolidation, contradiction, decay, importance>recency,
projection, inspect/correct, append-only, green suite.
"""

from datetime import datetime, timedelta

from cozmo.brain.brain import Brain
from cozmo.brain.projection import project
from cozmo.brain.reasoning import reflection, tiering, verification
from cozmo.brain.reasoning.promotion import decide
from cozmo.brain.types import (
    EdgeKind,
    KnowledgeForm,
    KnowledgeItem,
    KnowledgeStatus,
)


def _dt(day, month=1):
    return datetime(2026, month, day)


def _item(
    id,
    content,
    tags=(),
    status=KnowledgeStatus.CANDIDATE,
    importance=0.0,
    last_seen=None,
):
    return KnowledgeItem(
        id=id,
        form=KnowledgeForm.ATOMIC,
        content=content,
        confidence=0.9,
        status=status,
        tags=tuple(tags),
        last_seen_at=last_seen,
        importance=importance,
    )


class StubThisMemory:
    def __init__(self):
        self.facts = []
        self.interactions = []


class StubScenarioLayer:
    class _Store:
        def get(self, scenario_id):
            return None

        def list(self, limit=100):
            return ()

    def __init__(self):
        self.store = self._Store()


class StubKnowledgeLayer:
    def __init__(self, items=None, fail=False):
        self.items = list(items or [])
        self.status_updates = []
        self.written = []
        self.fail = fail

    def list_objects(self, limit=200):
        return list(self.items)

    def update_status(self, item_id, status):
        if self.fail:
            raise RuntimeError("write down")
        self.status_updates.append((item_id, status))
        for it in self.items:
            if it.id == item_id:
                it.status = status
        return True

    def write(self, statement, tags=()):
        if self.fail:
            raise RuntimeError("write down")
        new_id = f"kn-new-{len(self.written) + 1}"
        self.written.append((statement, tuple(tags)))
        self.items.append(_item(new_id, statement, tags, status=KnowledgeStatus.VERIFIED))
        return new_id


class StubRelationshipStore:
    def __init__(self, edges=None, fail=False):
        self.edges = list(edges or [])
        self.fail = fail

    def add_many(self, relationships):
        if self.fail:
            raise RuntimeError("edge write down")
        self.edges.extend(relationships)

    def list(self, limit=500):
        return tuple(self.edges)


def _brain(layer, rels=None, bus=None):
    return Brain(
        memory=StubThisMemory(),
        knowledge_layer=layer,
        scenario_layer=StubScenarioLayer(),
        relationship_store=rels,
        event_bus=bus,
    )


# ── §14 acceptance items ───────────────────────────────────────────────────

def test_acceptance_repeated_observations_verify_without_duplicates():
    # 4 near-identical claims corroborate → one winner verified, no siblings.
    a = _item("a", "the user prefers python for builds")
    b = _item("b", "user prefers python builds")
    c = _item("c", "prefers python builds")
    d = _item("d", "user really prefers python build tooling")
    layer = StubKnowledgeLayer([a, b, c, d])
    report = _brain(layer).reflect()
    assert report.promotions >= 1
    verified = [i for i in layer.items if i.status == KnowledgeStatus.VERIFIED]
    assert len(verified) >= 1
    assert len([u for u in layer.status_updates]) >= 3


def test_acceptance_contradiction_preserves_both_histories():
    old = _item("old", "prefers rust", ("preference",), status=KnowledgeStatus.VERIFIED)
    new = _item("new", "I prefer python now", ("preference",))
    layer = StubKnowledgeLayer([old, new])
    rels = StubRelationshipStore()
    report = _brain(layer, rels=rels).reflect()
    assert report.superseded == 1
    assert report.conflicts == 1
    kinds = {e.kind for e in rels.edges}
    assert EdgeKind.SUPERSEDES in kinds
    assert EdgeKind.CONFLICTS_WITH in kinds
    # both histories preserved: old demoted but still present
    assert {i.id for i in layer.items} == {"old", "new"}


def test_acceptance_old_unconfirmed_decays_out_of_projection():
    stale = _item("a", "old tool tip", tags=("build",), last_seen=_dt(1))
    layer = StubKnowledgeLayer([stale])
    report = _brain(layer).reflect(on_demand=False, scenario_completed=True)
    assert report.decays == 1
    assert stale.status == KnowledgeStatus.CANDIDATE
    # drops out of projection (not identity-tagged) and default tiering
    assert project(layer.items, ()) == {}


def test_acceptance_stable_preference_outranks_recent_topic():
    from cozmo.brain.types import KnowledgeHit

    stable = _item("s", "prefers dark mode", tags=("preference",), status=KnowledgeStatus.VERIFIED, importance=0.9, last_seen=_dt(1))
    recent = _item("r", "bikes to work", tags=("preference",), status=KnowledgeStatus.VERIFIED, importance=0.1, last_seen=_dt(5))
    ordered = tiering.tier_hits(
        [KnowledgeHit(item=recent), KnowledgeHit(item=stable)]
    )
    assert [h.item.id for h in ordered] == ["s", "r"]


def test_acceptance_projection_no_invented_attributes():
    items = [
        _item("p", "prefers python", ("preference",), status=KnowledgeStatus.VERIFIED),
        _item("u", "the sky is blue", ("build",)),
    ]
    out = project(items, ())
    assert set(out.keys()) == {"preference"}
    assert out["preference"][0]["content"] == "prefers python"


def test_acceptance_verification_threshold():
    # single mention stays candidate; explicit confirmation verifies instantly.
    single = _item("a", "maybe likes ruby")
    assert verification.is_confirm(single.content) is False
    confirmed = _item("b", "remember that I prefer python")
    assert verification.is_confirm(confirmed.content) is True
    outcome = decide(confirmed, 0, confirmed=True)
    assert outcome.new_status == KnowledgeStatus.VERIFIED


# ── trust surface: inspect / correct ───────────────────────────────────────

def test_inspect_memory_lists_items_and_edges():
    layer = StubKnowledgeLayer(
        [
            _item("a", "prefers python", ("preference",), status=KnowledgeStatus.VERIFIED),
            _item("old", "prefers rust", ("preference",), status=KnowledgeStatus.SUPERSEDED),
        ]
    )
    rels = StubRelationshipStore(
        [
            type("R", (), {"source_id": "a", "target_id": "old", "kind": EdgeKind.SUPERSEDES})()
        ]
    )
    brain = _brain(layer, rels=rels)
    view = brain.inspect_memory()
    ids = {i["id"] for i in view["items"]}
    assert ids == {"a", "old"}
    a_view = next(i for i in view["items"] if i["id"] == "a")
    assert a_view["edges"] == ["supersedes:old"]
    assert "preference" in view["categories"]


def test_correct_memory_supersedes_and_records_correction():
    old = _item("old", "prefers rust", ("preference",), status=KnowledgeStatus.VERIFIED)
    layer = StubKnowledgeLayer([old])
    rels = StubRelationshipStore()
    brain = _brain(layer, rels=rels)
    result = brain.correct_memory(
        "old", statement="prefers python", action="superseded", tags=("preference",)
    )
    assert result["ok"] is True
    assert result["superseded"] == "old"
    assert result["recorded"] is not None
    assert old.status == KnowledgeStatus.SUPERSEDED
    assert len(rels.edges) == 1
    assert rels.edges[0].kind == EdgeKind.SUPERSEDES
    assert rels.edges[0].source_id == result["recorded"]
    assert rels.edges[0].target_id == "old"


def test_correct_memory_demote_and_archive():
    v = _item("v", "prefers python", ("preference",), status=KnowledgeStatus.VERIFIED)
    layer = StubKnowledgeLayer([v])
    brain = _brain(layer)
    d = brain.correct_memory("v", action="demote")
    assert d["ok"] and d.get("demoted") == "v"
    assert v.status == KnowledgeStatus.CORROBORATED
    a = brain.correct_memory("v", action="archive")
    assert a["ok"] and a.get("archived") == "v"
    assert v.status == KnowledgeStatus.CANDIDATE


def test_correct_memory_append_only_no_delete():
    old = _item("old", "prefers rust", ("preference",), status=KnowledgeStatus.VERIFIED)
    layer = StubKnowledgeLayer([old])
    rels = StubRelationshipStore()
    brain = _brain(layer, rels=rels)
    brain.correct_memory("old", statement="prefers python", action="superseded")
    assert {i.id for i in layer.items} == {"old", "kn-new-1"}
    assert len(layer.status_updates) == 1  # only the superseded write, no content mutation


def test_correct_memory_requires_item_id():
    brain = _brain(StubKnowledgeLayer())
    result = brain.correct_memory(None)
    assert result["ok"] is False


# ── synthetic evaluation harness (bounded, deterministic, no LLM) ──────────

def test_synthetic_user_profile_evolves_correctly():
    """A tiny profile: repeated observations verify; a contradiction supersedes
    with history; stable preference stays top-ranked."""
    pre1 = _item("p1", "the user prefers python for tooling", ("preference",), last_seen=_dt(1))
    pre2 = _item("p2", "user prefers python tooling", ("preference",), last_seen=_dt(2))
    pre3 = _item("p3", "prefers python tooling", ("preference",), last_seen=_dt(3))
    pre4 = _item("p4", "user prefers python build tooling", ("preference",), last_seen=_dt(4))
    goal = _item("g", "wants cozmo to be a personal assistant", ("goal",), last_seen=_dt(5))
    layer = StubKnowledgeLayer([pre1, pre2, pre3, pre4, goal])

    report = _brain(layer).reflect()
    assert report.promotions >= 1
    verified = [i for i in layer.items if i.status == KnowledgeStatus.VERIFIED]
    assert len(verified) >= 1

    out = project(layer.items, ())
    assert "preference" in out
    assert "goal" in out
    assert out["preference"][0]["status"] == KnowledgeStatus.VERIFIED

    # nothing deleted across the whole pass
    assert len(layer.items) == 5


def test_acceptance_tiering_superseded_excluded_by_default():
    live = _item("a", "prefers python", status=KnowledgeStatus.VERIFIED)
    dead = _item("b", "prefers rust", status=KnowledgeStatus.SUPERSEDED)
    from cozmo.brain.types import KnowledgeHit

    hits = [KnowledgeHit(item=dead), KnowledgeHit(item=live)]
    assert [h.item.id for h in tiering.tier_hits(hits)] == ["a"]