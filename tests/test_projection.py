"""Phase F — Step 4: personal-context projection tests.

Covers: grouping by category, lexicographic §5 ranking (importance-first),
exclusion of SUPERSEDED items, transparent evidence labels, and the "never
invent an attribute" invariant (empty projection when nothing is stated).
"""

from datetime import datetime, timedelta

from novi.brain.brain import Brain
from novi.brain.projection import category_of, project
from novi.brain.types import KnowledgeForm, KnowledgeItem, KnowledgeStatus


def _item(
    id,
    content,
    tags,
    status=KnowledgeStatus.CANDIDATE,
    importance=0.0,
    last_seen=None,
    scenario_id=None,
    form=KnowledgeForm.ATOMIC,
):
    return KnowledgeItem(
        id=id,
        form=form,
        content=content,
        confidence=0.8,
        status=status,
        tags=tags,
        scenario_id=scenario_id,
        last_seen_at=last_seen,
        importance=importance,
    )


def _dt(day):
    return datetime(2026, 1, day)


def test_group_items_by_category():
    pref = _item("p", "prefers python", ("preference",))
    goal = _item("g", "wants personal assistant", ("goal",))
    skill = _item("s", "knows rust", ("skill",))
    out = project([pref, goal, skill])
    assert set(out.keys()) == {"preference", "goal", "skill"}
    assert out["preference"][0]["content"] == "prefers python"
    assert out["goal"][0]["content"] == "wants personal assistant"


def test_ranks_importance_first_rare_stable_beats_recent_low():
    stable = _item(
        "stable",
        "prefers dark mode",
        ("preference",),
        status=KnowledgeStatus.VERIFIED,
        importance=0.9,
        last_seen=_dt(1),
    )
    recent_low = _item(
        "recent",
        "currently bikes to work",
        ("preference",),
        status=KnowledgeStatus.VERIFIED,
        importance=0.2,
        last_seen=_dt(5),
    )
    out = project([recent_low, stable])
    assert [e["id"] for e in out["preference"]] == ["stable", "recent"]


def test_confidence_tiers_within_importance():
    verified = _item(
        "v", "prefers python", ("preference",), status=KnowledgeStatus.VERIFIED,
        importance=0.5, last_seen=_dt(1),
    )
    candidate = _item(
        "c", "maybe likes ruby", ("preference",), status=KnowledgeStatus.CANDIDATE,
        importance=0.5, last_seen=_dt(5),
    )
    out = project([candidate, verified])
    assert [e["id"] for e in out["preference"]] == ["v", "c"]


def test_excludes_superseded():
    old = _item("old", "prefers rust", ("preference",), status=KnowledgeStatus.SUPERSEDED)
    new = _item("new", "prefers python", ("preference",), status=KnowledgeStatus.VERIFIED)
    out = project([old, new])
    assert [e["id"] for e in out["preference"]] == ["new"]


def test_marks_evidence_transparently():
    verified = _item("v", "prefers python", ("preference",), status=KnowledgeStatus.VERIFIED)
    corr = _item("c", "likes vim", ("preference",), status=KnowledgeStatus.CORROBORATED)
    cand = _item("d", "maybe rust", ("preference",))
    out = project([verified, corr, cand])
    labels = {e["id"]: e["evidence"] for e in out["preference"]}
    assert labels == {"v": "verified", "c": "corroborated", "d": "candidate"}


def test_never_invents_attribute_empty_when_nothing_stated():
    assert project([]) == {}
    untagged = _item("u", "the sky is blue", ())
    assert project([untagged]) == {}
    composite = _item("s", "a summary", (), form=KnowledgeForm.COMPOSITE)
    assert project([composite]) == {}


def test_active_scenario_flag():
    a = _item("a", "prefers python", ("preference",), scenario_id="scn-1")
    b = _item("b", "prefers vim", ("preference",), scenario_id="scn-2")
    out = project([a, b], active_scenario_ids=["scn-2"])
    by_id = {e["id"]: e for e in out["preference"]}
    assert by_id["a"]["active"] is False
    assert by_id["b"]["active"] is True


def test_recency_tiebreak_equal_tiers():
    one = _item("one", "prefers python", ("preference",), importance=0.4, last_seen=_dt(1))
    five = _item("five", "prefers java", ("preference",), importance=0.4, last_seen=_dt(5))
    out = project([one, five])
    assert [e["id"] for e in out["preference"]] == ["five", "one"]


def test_category_of():
    assert category_of(_item("a", "x", ("goal",))) == "goal"
    assert category_of(_item("b", "x", ("preference", "identity"))) == "preference"
    assert category_of(_item("c", "x", ("build",))) is None


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

    def list_objects(self, limit=200):
        return list(self.items)

    def update_status(self, item_id, status):
        return True


def test_project_context_facade_read_only():
    layer = StubKnowledgeLayer(
        items=[
            _item("p", "prefers python", ("preference",), status=KnowledgeStatus.VERIFIED),
            _item("g", "wants personal assistant", ("goal",), status=KnowledgeStatus.VERIFIED),
        ]
    )
    brain = Brain(
        memory=StubThisMemory(),
        knowledge_layer=layer,
        scenario_layer=StubScenarioLayer(),
    )
    out = brain.project_context()
    assert set(out.keys()) == {"preference", "goal"}
    assert out["preference"][0]["content"] == "prefers python"


def test_project_context_empty_when_no_identity():
    brain = Brain(
        memory=StubThisMemory(),
        knowledge_layer=StubKnowledgeLayer([_item("u", "sky is blue", ())]),
        scenario_layer=StubScenarioLayer(),
    )
    assert brain.project_context() == {}