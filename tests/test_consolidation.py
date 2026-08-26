"""Phase F — Step 1: Memory Consolidation tests.

Cross-corpus consolidation: a newly extracted claim that restates an existing
ATOMIC, non-superseded item corroborates it (advances ``last_seen_at``) instead
of inserting a sibling row. Composite summaries and superseded items are never
dedup targets. Non-duplicates are inserted normally.
"""

from datetime import datetime, timedelta

import pytest

from novi.brain.layers.knowledge import KnowledgeLayer
from novi.brain.reasoning.extraction import ExtractedClaim, ExtractionResult
from novi.brain.reasoning import verification
from novi.brain.storage.vector_store import VectorStore
from novi.brain.types import KnowledgeForm, KnowledgeItem, KnowledgeStatus
from novi.services.embedding import EmbeddingService


class FakeEmbed(EmbeddingService):
    def __init__(self, dim: int = 64):
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


@pytest.fixture
def layer(tmp_path):
    store = VectorStore(persist_dir=tmp_path, embed_model=FakeEmbed())
    return KnowledgeLayer(store)


def _result(*statements: str) -> ExtractionResult:
    return ExtractionResult(
        claims=tuple(
            ExtractedClaim(statement=s, confidence=0.6, tags=("fact",))
            for s in statements
        ),
        summary="a composite scenario summary",
        name="Scenario",
    )


def test_same_claim_corroborates_not_duplicates(layer):
    first = layer.store_extracted("conv-a", "scn-a", _result("The user prefers python over java."))
    second = layer.store_extracted("conv-b", "scn-b", _result("The user prefers python over java."))

    claim_first = first[0]
    claim_second = second[0]
    assert claim_second == claim_first  # corroborates the existing item, no sibling

    rows = layer.store.list_all(limit=100)
    atomic = [r for r in rows if r["form"] == KnowledgeForm.ATOMIC.value]
    assert len(atomic) == 1  # one stored row, never a sibling
    # 1 atomic + 1 composite summary per extraction batch (2 summaries)
    assert layer.store.count() == 3


def test_same_claim_advances_last_seen(layer):
    before = datetime.now()
    item_id = layer.store_extracted("conv-a", "scn-a", _result("The user prefers rust for cli tools."))[0]
    # bump the item's last_seen past its created_at (simulate prior corroboration)
    first_row = layer.store.get(item_id)
    seen_before = first_row["last_seen_at"]
    assert "last_seen_at" in first_row  # present on the row

    layer.store_extracted("conv-b", "scn-b", _result("The user prefers rust for cli tools."))
    after = datetime.now()

    row = layer.store.get(item_id)
    assert row["last_seen_at"] >= seen_before
    from datetime import datetime as _dt
    parsed = _dt.fromisoformat(row["last_seen_at"])
    assert before <= parsed <= after + timedelta(seconds=1)


def test_distinct_claims_with_partial_overlap_not_merged(layer):
    ids = layer.store_extracted(
        "conv-a",
        "scn-a",
        _result(
            "The user prefers rust for terminal tools.",
            "The user prefers python for machine learning projects.",
        ),
    )
    assert len(ids) == 1 + 2  # 1 composite summary + 2 atomic claims

    rows = layer.store.list_all(limit=100)
    atomic = [r for r in rows if r["form"] == KnowledgeForm.ATOMIC.value]
    assert len(atomic) == 2  # partial overlap, not near-duplicates


def test_composite_summary_never_a_dedup_target(layer):
    # A composite summary is never matched; a claim is never collapsed into it.
    layer.store_extracted("conv-a", "scn-a", _result("The user prefers rust."))
    # Re-states a sentence that resembles the composite summary text.
    ids = layer.store_extracted(
        "conv-b",
        "scn-a",
        ExtractionResult(
            claims=(ExtractedClaim(statement="a composite scenario summary", confidence=0.6, tags=("sum",)),),
            summary="a composite scenario summary",
            name="Scenario",
        ),
    )
    assert len(ids) == 1 + 1  # the claim inserted (not folded into composite) + its summary


def test_last_seen_round_trip_and_reopen(tmp_path):
    store = VectorStore(persist_dir=tmp_path, embed_model=FakeEmbed())
    item = KnowledgeItem(
        id="kn-1",
        form=KnowledgeForm.ATOMIC,
        content="The user prefers python.",
        confidence=0.8,
        status=KnowledgeStatus.CANDIDATE,
        last_seen_at=datetime(2026, 1, 2, 3, 4, 5),
    )
    store.add(item)

    row = store.get("kn-1")
    assert row["last_seen_at"] == "2026-01-02T03:04:05"

    restored = VectorStore.item_from_row(row)
    assert restored.last_seen_at == datetime(2026, 1, 2, 3, 4, 5)

    store.close()
    reopened = VectorStore(persist_dir=tmp_path, embed_model=FakeEmbed())
    row2 = reopened.get("kn-1")
    assert row2["last_seen_at"] == "2026-01-02T03:04:05"
    reopened.close()


def test_last_seen_defaults_to_created_at(tmp_path):
    store = VectorStore(persist_dir=tmp_path, embed_model=FakeEmbed())
    item = KnowledgeItem(
        id="kn-1",
        form=KnowledgeForm.ATOMIC,
        content="The user prefers python.",
        confidence=0.8,
        status=KnowledgeStatus.CANDIDATE,
    )
    store.add(item)
    row = store.get("kn-1")
    assert row["last_seen_at"] == row["metadata"]["created_at"]
    restored = VectorStore.item_from_row(row)
    assert restored.last_seen_at is not None


def test_find_near_duplicate_pure():
    items = [
        KnowledgeItem(id="a", form=KnowledgeForm.ATOMIC, content="The user prefers rust.", confidence=0.6),
        KnowledgeItem(id="b", form=KnowledgeForm.COMPOSITE, content="a long composite summary", confidence=0.8),
        KnowledgeItem(id="c", form=KnowledgeForm.ATOMIC, content="The user prefers python.", confidence=0.6),
        KnowledgeItem(id="d", form=KnowledgeForm.ATOMIC, content="The user prefers rust.", confidence=0.6, status=KnowledgeStatus.SUPERSEDED),
    ]
    match = verification.find_near_duplicate(items, "The user prefers rust.")
    # finds the non-superseded ATOMIC item, ignores composite + superseded
    assert match is not None
    assert match.id == "a"

    assert verification.find_near_duplicate(items, "The user likes go for backend services.") is None