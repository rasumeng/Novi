"""Phase F — Step 3: reflection coordinator tests.

Covers: budget respected, deterministic oldest-first ordering, trigger gating
(no pass without pending work / without a trigger; pass on scenario-completion),
decision list applied by the Brain with accurate report counts, and no double
application when a pass runs against a store that still holds the same items.
"""

from datetime import datetime, timedelta

from novi.brain.brain import Brain
from novi.brain.reasoning import reflection
from novi.brain.types import KnowledgeForm, KnowledgeItem, KnowledgeStatus


class StubThisMemory:
    def __init__(self):
        self.facts = []
        self.interactions = []


class StubScenarioLayer:
    class _Store:
        def get(self, scenario_id):
            return None

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
        for item in self.items:
            if item.id == item_id:
                item.status = status
        return True


def _item(id, content, status=KnowledgeStatus.CANDIDATE, last_seen=None, created=None):
    return KnowledgeItem(
        id=id,
        form=KnowledgeForm.ATOMIC,
        content=content,
        confidence=0.9,
        status=status,
        tags=("preference",),
        last_seen_at=last_seen,
        created_at=created or datetime(2026, 1, 1),
    )


def test_budget_respected_stops_at_n():
    many = [
        _item(f"k{i}", f"some preference {i}") for i in range(50)
    ]
    chosen = reflection.budgeted(many, budget=10)
    assert len(chosen) == 10


def test_budgeted_is_oldest_first_and_deterministic():
    old = _item("old", "a", last_seen=datetime(2020, 1, 1))
    mid = _item("mid", "b", last_seen=datetime(2021, 1, 1))
    new = _item("new", "c", last_seen=datetime(2022, 1, 1))
    items = [new, old, mid]
    ordered = reflection.budgeted(items, budget=3)
    assert [i.id for i in ordered] == ["old", "mid", "new"]
    # deterministic across calls / shuffles
    ordered2 = reflection.budgeted([mid, new, old], budget=3)
    assert [i.id for i in ordered2] == ["old", "mid", "new"]


def test_budgeted_excludes_verified_and_superseded():
    done = _item("done", "x", status=KnowledgeStatus.VERIFIED)
    arch = _item("arch", "y", status=KnowledgeStatus.SUPERSEDED)
    cand = _item("cand", "z")
    chosen = reflection.budgeted([done, arch, cand], budget=10)
    assert [i.id for i in chosen] == ["cand"]


def test_should_reflect_needs_pending_and_trigger():
    assert not reflection.should_reflect(0, scenario_completed=True)
    assert not reflection.should_reflect(5)
    assert not reflection.should_reflect(5, idle_pending=False)
    assert reflection.should_reflect(5, scenario_completed=True)
    assert reflection.should_reflect(5, confirm_burst=True)
    assert reflection.should_reflect(5, idle_pending=True)
    assert reflection.should_reflect(5, on_demand=True)


def test_reflect_no_pass_without_trigger():
    layer = StubKnowledgeLayer([_item("a", "remember that I prefer python")])
    brain = Brain(
        memory=StubThisMemory(),
        knowledge_layer=layer,
        scenario_layer=StubScenarioLayer(),
    )
    report = brain.reflect(on_demand=False)
    assert report.promotions == 0
    assert layer.status_updates == []


def test_reflect_passes_on_scenario_completion():
    layer = StubKnowledgeLayer([_item("a", "remember that I prefer python")])
    brain = Brain(
        memory=StubThisMemory(),
        knowledge_layer=layer,
        scenario_layer=StubScenarioLayer(),
    )
    report = brain.reflect(on_demand=False, scenario_completed=True)
    assert report.promotions == 1
    assert report.touched_ids == ("a",)
    assert ("a", KnowledgeStatus.VERIFIED) in layer.status_updates


def test_reflect_on_demand_runs_pass():
    layer = StubKnowledgeLayer([_item("a", "remember that I prefer python")])
    brain = Brain(
        memory=StubThisMemory(),
        knowledge_layer=layer,
        scenario_layer=StubScenarioLayer(),
    )
    report = brain.reflect()
    assert report.promotions == 1
    assert report.touched_ids == ("a",)


def test_no_double_apply_with_in_flight_items():
    # Running the coordinator twice over the same (un-applied) decision list
    # must not apply twice: the Brain applies only outcomes whose status
    # differs, and the coordinator re-reads store state each pass.
    layer = StubKnowledgeLayer([_item("a", "remember that I prefer python")])
    brain = Brain(
        memory=StubThisMemory(),
        knowledge_layer=layer,
        scenario_layer=StubScenarioLayer(),
    )
    r1 = brain.reflect()
    assert r1.promotions == 1
    r2 = brain.reflect()
    # item is now VERIFIED → no longer processable → second pass does nothing
    assert r2.promotions == 0
    assert r2.touched_ids == ()
    assert len([u for u in layer.status_updates if u[1] == KnowledgeStatus.VERIFIED]) == 1


def test_make_plan_returns_outcomes_for_processable_only():
    done = _item("done", "x", status=KnowledgeStatus.VERIFIED)
    cand = _item("cand", "remember that I prefer python")
    outcomes = reflection.make_plan([done, cand])
    assert [o.item.id for o in outcomes] == ["cand"]