"""Knowledge-first lookup regressions: real policy and persistence boundaries."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from novi.brain.types import KnowledgeStatus


def test_current_status_requires_evidence_even_in_chat():
    from novi.runtime.knowledge_cycle import classify_request
    request = classify_request('is Charlie Kirk still alive?')
    assert request.freshness == 'changing'
    assert request.needs_evidence
    assert not classify_request('explain a binary tree').needs_evidence
    assert classify_request('look online for binary trees').refresh
    assert classify_request('do not search online; is he alive?').offline


def test_external_claim_reuse_does_not_refresh_verification():
    from novi.brain.reasoning.external import build_item, reusable
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    sources = [dict(url='https://example.org/a', excerpt='Atlas was founded in 1990.'),
               dict(url='https://example.net/b', excerpt='Atlas was founded in 1990.')]
    item = build_item('Atlas was founded in 1990.', sources, 'stable', now, independent=True)
    assert item.status == KnowledgeStatus.VERIFIED
    assert reusable(item, now + timedelta(days=400), 'stable')
    changing = build_item('Atlas is open.', sources, 'changing', now, independent=True)
    changing.last_seen_at = now + timedelta(days=20)
    assert not reusable(changing, now + timedelta(days=20), 'changing')


def test_one_source_or_repeated_domain_is_not_independent_verification():
    from novi.brain.reasoning.external import build_item
    sources = [dict(url='https://example.org/a', excerpt='Atlas is open.'),
               dict(url='https://www.example.org/b', excerpt='Atlas is open.')]
    item = build_item('Atlas is open.', sources, 'changing')
    assert item.status == KnowledgeStatus.CANDIDATE


def test_negations_and_changed_values_are_not_duplicates():
    from novi.brain.reasoning.verification import find_near_duplicate
    from novi.brain.types import KnowledgeItem, KnowledgeForm
    item = KnowledgeItem('a', KnowledgeForm.ATOMIC, 'Atlas is open in 2026.', .9)
    assert find_near_duplicate([item], 'Atlas is not open in 2026.') is None
    assert find_near_duplicate([item], 'Atlas is open in 2025.') is None


def test_relevance_cannot_match_query_header():
    from novi.runtime.retrieval import RetrievalExecutor
    from novi.runtime.evidence import EvidenceCollector
    from novi.tools.search_pipeline import SearchResult
    bundle = EvidenceCollector._merge('Charlie Kirk alive', [
        SearchResult(title='Encryption algorithms', url='https://example.org', snippet='Symmetric encryption.')])
    assert RetrievalExecutor.evidence_relevance(bundle, ['charlie', 'kirk', 'alive']) == 0


def test_private_page_addresses_are_rejected():
    import pytest
    from novi.search.reader import validate_public_url
    for url in ['file:///etc/passwd', 'http://127.0.0.1', 'http://[::1]',
                'http://169.254.169.254/', 'https://user:pass@example.org']:
        with pytest.raises(ValueError):
            validate_public_url(url)


def test_external_evidence_survives_lance_and_markdown_roundtrip(tmp_path):
    from tests.test_vector_store import FakeEmbed
    from novi.brain import Brain
    from novi.brain.layers.knowledge import KnowledgeLayer
    from novi.brain.storage.vector_store import VectorStore
    from novi.brain.storage.markdown_store import MarkdownStore
    store = VectorStore(tmp_path / 'db', embed_model=FakeEmbed())
    markdown = MarkdownStore(tmp_path / 'notes')
    brain = Brain(knowledge_layer=KnowledgeLayer(store), markdown_store=markdown)
    claim = dict(statement='Atlas was founded in 1990.', volatility='stable', independent=True,
                 sources=[dict(url='https://example.org/a', excerpt='Atlas was founded in 1990.'),
                          dict(url='https://example.net/b', excerpt='The founding of Atlas was in 1990.')])
    report = brain.ingest_evidence([claim])
    assert report['ok'], report
    item_id = report['item_ids'][0]
    assert brain.ingest_evidence([claim])['item_ids'] == [item_id]
    assert store.count() == 1
    restored = markdown.read_item(markdown.find_for_id(item_id))
    assert restored.evidence['verified_at']
    assert restored.sources == tuple(s['url'] for s in claim['sources'])
    reopened = VectorStore(tmp_path / 'db', embed_model=FakeEmbed())
    assert reopened.item_from_row(reopened.get(item_id)).evidence == restored.evidence


def test_cycle_reuses_supported_memory_without_network(monkeypatch):
    import json
    from novi.runtime.knowledge_cycle import KnowledgeCycle
    from novi.runtime.retrieval import RetrievalExecutor
    from novi.runtime.execution_context import ExecutionContext
    from novi.runtime.retrieval_coordinator import RetrievalCoordinator
    from novi.brain.types import RecallItem, RecallResult
    from novi.brain.reasoning.external import build_item
    item = build_item('Atlas was founded in 1990.', [
        dict(url='https://example.org/a', excerpt='Atlas was founded in 1990.'),
        dict(url='https://example.net/a', excerpt='Atlas began in 1990.')], 'stable', independent=True)
    recalled = RecallItem(item.content, .9, 'knowledge', dict(id=item.id, evidence=item.evidence, status='verified'))
    brain = SimpleNamespace(recall=lambda *a: RecallResult('Atlas', (recalled,)))
    ex = RetrievalExecutor(brain=brain)
    monkeypatch.setattr('novi.runtime.retrieval._memory_enabled', lambda: True)
    llm = SimpleNamespace(invoke=lambda *a: json.dumps(dict(source='public', sufficient=True,
                         memory_ids=[item.id], query='Atlas founded', freshness='stable')))
    cycle = KnowledgeCycle(ex, llm, lambda *a: (_ for _ in ()).throw(AssertionError('network used')))
    ctx = ExecutionContext(user_input='When was Atlas founded?')
    ctx.retrieval_coordinator = RetrievalCoordinator()
    assert cycle.prepare(ctx, ctx.user_input)
    assert ctx.metadata['knowledge_origin'] == 'memory'
    assert ctx.retrieval_coordinator.network_session.searches == 0


def test_network_session_denial_and_exact_request_budget():
    import pytest
    from novi.search.session import SearchSession
    denied = SearchSession(lambda *a: False)
    with pytest.raises(PermissionError):
        denied.reserve('search', {'query': 'Atlas'})
    session = SearchSession(lambda *a: True)
    session.reserve('search', {'query': 'Atlas founded'})
    session.reserve('search', {'query': 'Atlas founded primary source'})
    with pytest.raises(ValueError):
        session.reserve('search', {'query': 'third query'})
    assert session.searches == 2


def test_do_not_remember_and_offline_are_independent():
    from novi.runtime.knowledge_cycle import classify_request
    request = classify_request("Search online, but don't remember this")
    assert request.refresh and not request.offline and not request.retain
