"""Phase F — verification + promotion (pure reasoning) tests."""

from cozmo.brain.reasoning.promotion import PromotionOutcome, decide
from cozmo.brain.reasoning.verification import corroboration, is_confirm, tokens
from cozmo.brain.types import (
    EdgeKind,
    KnowledgeForm,
    KnowledgeItem,
    KnowledgeStatus,
)


def item(content, status=KnowledgeStatus.CANDIDATE, tags=("preference",), iid="kn-1"):
    return KnowledgeItem(
        id=iid,
        form=KnowledgeForm.ATOMIC,
        content=content,
        confidence=0.9,
        status=status,
        tags=tuple(tags),
    )


class TestConfirm:
    def test_explicit_confirmation_prefix(self):
        assert is_confirm("remember that I prefer python")
        assert is_confirm("I always use uv for builds")
        assert is_confirm("never call the service 'cozmo'")

    def test_plain_statement_is_not_confirmation(self):
        assert not is_confirm("the build uses uv")
        assert not is_confirm("python is a language")


class TestCorroboration:
    def test_repeat_claim_counts(self):
        items = [
            item("User prefers the python variant"),
            item("the user prefers python"),
            item("python is preferred by the user"),
            item("something unrelated"),
        ]
        assert corroboration(items, 0) == 2

    def test_distinct_claims_do_not_count(self):
        items = [
            item("prefers rust"),
            item("uses uv for builds"),
            item("likes the beach"),
        ]
        assert corroboration(items, 0) == 0

    def test_empty_items(self):
        assert corroboration([], 0) == 0


class TestPromotion:
    def test_candidate_without_evidence_stays_candidate(self):
        out = decide(item("prefers python"), corroborations=0)
        assert out.new_status == KnowledgeStatus.CANDIDATE
        assert out.supersedes is None

    def test_single_corroboration_promotes_to_corroborated(self):
        out = decide(item("prefers python"), corroborations=1)
        assert out.new_status == KnowledgeStatus.CORROBORATED

    def test_many_corroborations_verify(self):
        out = decide(item("prefers python"), corroborations=3)
        assert out.new_status == KnowledgeStatus.VERIFIED

    def test_explicit_confirmation_verifies_instantly(self):
        out = decide(item("prefers python"), corroborations=0, confirmed=True)
        assert out.new_status == KnowledgeStatus.VERIFIED

    def test_supersedes_prior_verified_identity(self):
        old = item("prefers rust", status=KnowledgeStatus.VERIFIED, iid="old")
        new = item("prefers python", iid="new")
        out = decide(new, corroborations=3, existing_verified=old)
        assert out.supersedes is not None
        assert out.supersedes.source_id == "new"
        assert out.supersedes.target_id == "old"
        assert out.supersedes.kind == EdgeKind.SUPERSEDES

    def test_same_content_does_not_supersede(self):
        old = item("prefers rust", status=KnowledgeStatus.VERIFIED, iid="old")
        new = item("prefers rust", iid="same")
        out = decide(new, corroborations=3, existing_verified=old)
        assert out.supersedes is None