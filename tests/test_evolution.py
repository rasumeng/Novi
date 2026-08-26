"""Phase F — Step 2: knowledge evolution tests.

Covers: promotion via explicit confirmation, the single-observation rule
(one mention stays candidate), contradiction resolution (supersedes +
conflicts_with edges, old demoted with history preserved), user correction
(signature phrase demotes old record, correction kept as new verified), and
the "emit after durable write" invariant for ``knowledge.promoted``.
"""

import pytest

from novi.brain.brain import Brain
from novi.brain.events import KNOWLEDGE_PROMOTED
from novi.brain.types import (
    EdgeKind,
    KnowledgeForm,
    KnowledgeItem,
    KnowledgeStatus,
)


class RecordingBus:
    def __init__(self):
        self.events = []

    def emit(self, event_type, **data):
        self.events.append((event_type, data))


class StubThisMemory:
    def __init__(self):
        self.facts = []
        self.interactions = []


class StubKnowledgeLayer:
    def __init__(self, items=None, fail_status=False):
        self.items = list(items or [])
        self.status_updates = []
        self.write = []
        self.fail_status = fail_status

    def list_objects(self, limit=200):
        return list(self.items)

    def update_status(self, item_id, status):
        if self.fail_status:
            raise RuntimeError("status write down")
        self.status_updates.append((item_id, status))
        return True


class StubScenarioLayer:
    class _Store:
        def get(self, scenario_id):
            return None

    def __init__(self):
        self.store = self._Store()


class StubRelationshipStore:
    def __init__(self, fail=False):
        self.edges = []
        self.fail = fail

    def add_many(self, relationships):
        if self.fail:
            raise RuntimeError("edge write down")
        self.edges.extend(relationships)


def _item(
    id,
    content,
    status=KnowledgeStatus.CANDIDATE,
    tags=("preference",),
    form=KnowledgeForm.ATOMIC,
):
    return KnowledgeItem(
        id=id,
        form=form,
        content=content,
        confidence=0.9,
        status=status,
        tags=tags,
    )


def _brain(layer, rels=None, bus=None):
    return Brain(
        memory=StubThisMemory(),
        knowledge_layer=layer,
        scenario_layer=StubScenarioLayer(),
        relationship_store=rels,
        event_bus=bus,
    )


def test_confirmation_promotes_to_verified():
    layer = StubKnowledgeLayer(
        items=[_item("a", "remember that I prefer python")]
    )
    report = _brain(layer).reflect()
    assert report.promotions == 1
    assert ("a", KnowledgeStatus.VERIFIED) in layer.status_updates
    assert report.conflicts == 0
    assert report.superseded == 0


def test_single_mention_stays_candidate():
    layer = StubKnowledgeLayer(items=[_item("a", "user might use cookbooks")])
    report = _brain(layer).reflect()
    assert report.promotions == 0
    assert report.corroborated == 0
    assert layer.status_updates == []


def test_contradiction_supersedes_and_writes_both_edges():
    old = _item("old", "prefers rust", status=KnowledgeStatus.VERIFIED)
    new = _item("new", "I prefer python now")
    layer = StubKnowledgeLayer(items=[old, new])
    rels = StubRelationshipStore()
    report = _brain(layer, rels=rels).reflect()

    assert report.promotions == 1
    assert report.superseded == 1
    assert report.conflicts == 1
    assert ("old", KnowledgeStatus.SUPERSEDED) in layer.status_updates

    kinds = {e.kind for e in rels.edges}
    assert EdgeKind.SUPERSEDES in kinds
    assert EdgeKind.CONFLICTS_WITH in kinds
    supersedes = [e for e in rels.edges if e.kind == EdgeKind.SUPERSEDES]
    assert len(supersedes) == 1
    assert supersedes[0].source_id == "new"
    assert supersedes[0].target_id == "old"
    conflicts = [e for e in rels.edges if e.kind == EdgeKind.CONFLICTS_WITH]
    assert len(conflicts) == 1
    assert conflicts[0].source_id == "new"
    assert conflicts[0].target_id == "old"
    assert old.status == KnowledgeStatus.VERIFIED  # history preserved, demoted separately


def test_user_correction_demotes_old_records_correction():
    old = _item("old", "uses grpc for services", status=KnowledgeStatus.VERIFIED)
    correction = _item("new", "remember that I prefer rest api now")
    layer = StubKnowledgeLayer(items=[old, correction])
    rels = StubRelationshipStore()
    report = _brain(layer, rels=rels).reflect()

    assert correction.status != KnowledgeStatus.SUPERSEDED
    assert report.superseded == 1
    assert ("new", KnowledgeStatus.VERIFIED) in layer.status_updates
    assert ("old", KnowledgeStatus.SUPERSEDED) in layer.status_updates


def test_knowledge_promoted_emitted_after_durable_write():
    layer = StubKnowledgeLayer(
        items=[_item("a", "remember that I prefer python")]
    )
    bus = RecordingBus()
    report = _brain(layer, bus=bus).reflect()

    promoted = [e for e in bus.events if e[0] == KNOWLEDGE_PROMOTED]
    assert len(promoted) == 1
    payload = promoted[0][1]
    assert payload["item_ids"] == ["a"]
    assert payload["promotions"] == report.promotions
    assert payload["superseded"] == report.superseded
    assert payload["conflicts"] == report.conflicts
    assert layer.status_updates  # writes happened before emission


def test_knowledge_promoted_not_emitted_when_write_fails():
    layer = StubKnowledgeLayer(
        items=[_item("a", "remember that I prefer python")],
        fail_status=True,
    )
    bus = RecordingBus()
    with pytest.raises(RuntimeError):
        _brain(layer, bus=bus).reflect()
    assert bus.events == []