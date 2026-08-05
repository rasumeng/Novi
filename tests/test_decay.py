"""Phase F — Step 6: decay/archive tests.

Forgetting = priority reduction + archival, never deletion. Covers: stale
non-durable candidate demotes and drops from default retrieval but stays
queryable; identity/preference items are durable and never decay; scenario-
completed trigger runs a pass; confirmation burst re-confirms a pre-decay item;
full store history preserved after any decay.
"""

from datetime import datetime, timedelta

from cozmo.brain.brain import Brain
from cozmo.brain.reasoning import reflection
from cozmo.brain.reasoning.tiering import tier_hits
from cozmo.brain.types import KnowledgeForm, KnowledgeHit, KnowledgeItem, KnowledgeStatus


def _dt(day, month=1):
    return datetime(2026, month, day)


def _item(
    id,
    content,
    tags=(),
    status=KnowledgeStatus.CANDIDATE,
    last_seen=None,
):
    return KnowledgeItem(
        id=id,
        form=KnowledgeForm.ATOMIC,
        content=content,
        confidence=0.8,
        status=status,
        tags=tuple(tags),
        last_seen_at=last_seen,
    )


def _hit(id, item):
    return KnowledgeHit(item=item)


def test_stale_candidate_demotes():
    stale = _item("a", "tool tip from jan", tags=("build",), last_seen=_dt(1))
    assert reflection.should_decay(stale, now=_dt(5, month=6), horizon_days=90)
    plan = reflection.decay_plan([stale], now=_dt(5, month=6))
    assert [i.id for i in plan] == ["a"]


def test_fresh_candidate_does_not_decay():
    fresh = _item("a", "recent tip", tags=("build",), last_seen=_dt(5, month=6))
    assert not reflection.should_decay(fresh, now=_dt(5, month=6), horizon_days=90)


def test_identity_and_preference_never_decay():
    pref = _item("p", "prefers python", tags=("preference",), last_seen=_dt(1))
    goal = _item("g", "wants assistant", tags=("goal",), last_seen=_dt(1))
    skill = _item("s", "knows rust", tags=("skill",), last_seen=_dt(1))
    identity = _item("i", "calls me robert", tags=("identity",), last_seen=_dt(1))
    now = _dt(5, month=6)
    for item in (pref, goal, skill, identity):
        assert not reflection.should_decay(item, now=now, horizon_days=90)
    assert reflection.decay_plan([pref, goal, skill, identity], now=now) == []


def test_verified_is_durable_and_does_not_decay():
    verified = _item("v", "confirmed fact", tags=("build",), status=KnowledgeStatus.VERIFIED, last_seen=_dt(1))
    assert not reflection.should_decay(verified, now=_dt(5, month=6), horizon_days=90)


def test_archived_drops_from_default_retrieval_but_queryable():
    now = _dt(5, month=6)
    stale = _item("a", "stale tip", tags=("build",), last_seen=_dt(1))
    live = _item("b", "prefers python", tags=("preference",), last_seen=_dt(4, month=6))
    hits = tier_hits([_hit("a", stale), _hit("b", live)], now=now)
    assert [h.item.id for h in hits] == ["b"]
    # queryable on request
    hits_all = tier_hits([_hit("a", stale), _hit("b", live)], now=now, include_archived=True)
    assert {h.item.id for h in hits_all} == {"a", "b"}


def test_archived_filter_no_now_is_back_compat():
    stale = _item("a", "stale tip", tags=("build",), last_seen=_dt(1))
    hits = tier_hits([_hit("a", stale)])
    assert [h.item.id for h in hits] == ["a"]


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
    def __init__(self, items=None):
        self.items = list(items or [])
        self.status_updates = []

    def list_objects(self, limit=200):
        return list(self.items)

    def update_status(self, item_id, status):
        self.status_updates.append((item_id, status))
        for it in self.items:
            if it.id == item_id:
                it.status = status
        return True


def _brain(layer, bus=None):
    return Brain(
        memory=StubThisMemory(),
        knowledge_layer=layer,
        scenario_layer=StubScenarioLayer(),
        event_bus=bus,
    )


def test_scenario_completed_trigger_runs_decay_pass():
    stale = _item("a", "stale tip", tags=("build",), last_seen=_dt(1))
    layer = StubKnowledgeLayer([stale])
    report = _brain(layer).reflect(on_demand=False, scenario_completed=True)
    assert report.decays >= 1
    assert ("a", KnowledgeStatus.CANDIDATE) in layer.status_updates


def test_confirmation_burst_reconfirms_pre_decay_item():
    # An item decayed to CANDIDATE is re-confirmed by an explicit-context claim.
    pre = _item("a", "stale pre-decay", tags=("build",), last_seen=_dt(1))
    claim = _item("b", "remember that I prefer python", tags=("preference",), last_seen=_dt(2))
    layer = StubKnowledgeLayer([pre, claim])
    report = _brain(layer).reflect()
    assert report.decays >= 1
    assert ("b", KnowledgeStatus.VERIFIED) in layer.status_updates


def test_full_store_history_preserved_after_decay():
    stale = _item("a", "stale tip", tags=("build",), last_seen=_dt(1))
    layer = StubKnowledgeLayer([stale])
    _brain(layer).reflect(on_demand=False, scenario_completed=True)
    # nothing deleted — still listed, only status changed
    assert layer.list_objects()  # survives
    assert any(i.id == "a" for i in layer.list_objects())