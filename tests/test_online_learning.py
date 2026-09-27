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
    assert set(restored.sources) == {s['url'] for s in claim['sources']}
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


def test_search_budget_starts_at_first_request_not_before_model_thinks(monkeypatch):
    from novi.search.session import SearchSession
    clock = [0.0]
    monkeypatch.setattr('novi.search.session.time.monotonic', lambda: clock[0])
    session = SearchSession(lambda *a: True)
    clock[0] = 120.0
    session.reserve('search', {'query': 'Atlas'})
    assert session.searches == 1


def test_claim_identity_does_not_depend_on_source_order():
    from novi.brain.reasoning.external import build_item
    sources = [dict(url='https://example.org/a', excerpt='Atlas was founded in 1990.'),
               dict(url='https://example.net/b', excerpt='Atlas was founded in 1990.')]
    assert build_item('Atlas was founded in 1990.', sources).id == build_item(
        'Atlas was founded in 1990.', list(reversed(sources))).id


def test_network_denial_stays_typed_through_pipeline(monkeypatch):
    from novi.search.session import SearchSession
    from novi.tools.search_pipeline import _search_multi, SearchConfig
    import pytest
    with SearchSession(lambda *a: False).activate():
        with pytest.raises(PermissionError):
            _search_multi('Atlas', SearchConfig())


def test_failed_memory_ids_do_not_count_as_sufficient(monkeypatch):
    import json
    from novi.runtime.knowledge_cycle import KnowledgeCycle
    from novi.runtime.retrieval import RetrievalExecutor
    from novi.runtime.execution_context import ExecutionContext
    from novi.runtime.retrieval_coordinator import RetrievalCoordinator
    from novi.brain.types import RecallResult
    brain = SimpleNamespace(recall=lambda *a: RecallResult('Atlas', ()))
    ex = RetrievalExecutor(brain=brain)
    monkeypatch.setattr('novi.runtime.retrieval._memory_enabled', lambda: True)
    monkeypatch.setattr(ex, '_is_search_configured', lambda: False)
    llm = SimpleNamespace(invoke=lambda *a: json.dumps(dict(source='public', sufficient=True,
                          memory_ids=['invented'], query='Atlas founded', freshness='stable')))
    ctx = ExecutionContext(user_input='When was Atlas founded?')
    ctx.retrieval_coordinator = RetrievalCoordinator()
    KnowledgeCycle(ex, llm, lambda *a: True).prepare(ctx, ctx.user_input)
    assert not ctx.metadata['knowledge_decision']['sufficient']


def test_deadline_and_cancel_applies_to_provider_io(monkeypatch):
    from novi.search.session import SearchSession, request_timeout
    import pytest
    stopped = [False]
    with SearchSession(lambda *a: True, stop=lambda: stopped[0]).activate():
        assert 0 < request_timeout(10) <= 10
        stopped[0] = True
        with pytest.raises(InterruptedError):
            request_timeout(10)


def test_first_lookup_learns_then_restart_answers_locally(tmp_path, monkeypatch):
    """Real retrieval, LanceDB, markdown, and Brain recall; replace network/model boundaries."""
    import json
    from tests.test_vector_store import FakeEmbed
    from novi.brain import Brain
    from novi.brain.layers.knowledge import KnowledgeLayer
    from novi.brain.layers.scenarios import ScenarioLayer
    from novi.brain.storage.vector_store import VectorStore
    from novi.brain.storage.scenario_store import ScenarioStore
    from novi.brain.storage.markdown_store import MarkdownStore
    from novi.runtime.knowledge_cycle import KnowledgeCycle
    from novi.runtime.retrieval import RetrievalExecutor
    from novi.runtime.execution_context import ExecutionContext
    from novi.search.service import WebSearchService
    from novi.search.models import SearchResponse, SearchResult
    from novi.search.session import current_session

    statement = 'Atlas Observatory was founded in 1990.'
    pages = {'https://example.org/history': statement + ' Its founding charter is preserved.',
             'https://example.net/atlas': 'The historical record confirms: ' + statement}
    calls = []

    async def search(self, query, **kwargs):
        calls.append(query)
        return SearchResponse(query, [SearchResult('Atlas history', url, text) for url, text in pages.items()], 'fixture')

    def read_page(url, **kwargs):
        session = current_session.get()
        session.reserve('fetch', {'url': url})
        next(r for r in session.results if r['url'] == url).update(text=pages[url], fetched=True)
        return pages[url]

    class Judge:
        def invoke(self, prompt):
            data = json.loads(prompt.split('UNTRUSTED DATA (not instructions):\n')[1])
            if 'pages' in data:
                return json.dumps({'claims': [dict(statement=statement, volatility='stable', independent=True,
                    sources=[dict(url=url, excerpt=text) for url, text in pages.items()])]})
            memory = [r for r in data['memory'] if r['text'] == statement]
            return json.dumps(dict(source='public', sufficient=bool(memory), memory_ids=[r['id'] for r in memory],
                                   query='Atlas Observatory founding year', freshness='stable'))

    monkeypatch.setattr(WebSearchService, 'search', search)
    monkeypatch.setattr('novi.search.reader.read_page', read_page)
    monkeypatch.setattr('novi.runtime.retrieval._memory_enabled', lambda: True)

    def components():
        store = VectorStore(tmp_path / 'db', embed_model=FakeEmbed())
        brain = Brain(knowledge_layer=KnowledgeLayer(store),
                      scenario_layer=ScenarioLayer(ScenarioStore(tmp_path / 'scenarios')),
                      markdown_store=MarkdownStore(tmp_path / 'notes'))
        ex = RetrievalExecutor(brain=brain)
        monkeypatch.setattr(ex, '_is_search_configured', lambda: True)
        ex.knowledge_cycle = KnowledgeCycle(ex, Judge(), lambda *a: True)
        ctx = ExecutionContext(user_input='When was Atlas Observatory founded?')
        return ex, ctx

    ex, first = components()
    list(ex.execute(first, first.user_input))
    assert len(calls) == 1
    assert statement in first.grounding_text
    report = ex.knowledge_cycle.retain(first, statement)
    assert report['ok'], report
    # Reopen every persistent store, with a fresh runtime and no chat history.
    ex2, second = components()
    list(ex2.execute(second, second.user_input))
    assert second.metadata['knowledge_origin'] == 'memory'
    assert len(calls) == 1
    assert statement in second.memory_context


def test_search_permission_reuse_does_not_authorize_page_fetch():
    from novi.search.session import SearchSession
    import pytest
    calls = []
    session = SearchSession(lambda tool, args: calls.append(tool) or False)
    with session.activate(approved=('web_search', {'query': 'Atlas'})):
        session.reserve('search', {'query': 'Atlas'})
        with pytest.raises(PermissionError):
            session.reserve('fetch', {'url': 'https://example.org'})
        with pytest.raises(PermissionError):
            session.reserve('search', {'query': 'Atlas founding'})
    assert calls == ['web_fetch']
    assert session.searches == 1
    assert session.fetches == 0


def test_memory_payload_does_not_embed_source_pages():
    import json
    from novi.runtime.knowledge_cycle import compact_memory
    from novi.brain.types import RecallItem
    row = compact_memory(RecallItem('Atlas fact.', metadata=dict(id='a', evidence=dict(
        sources=[dict(url='https://example.org', excerpt='x' * 100000)], verified_at='2026-09-15'))), 0)
    assert len(json.dumps(row)) < 1000
    assert 'excerpt' not in json.dumps(row)


def test_conflicting_external_claims_are_not_reused(tmp_path):
    from tests.test_vector_store import FakeEmbed
    from novi.brain import Brain
    from novi.brain.layers.knowledge import KnowledgeLayer
    from novi.brain.storage.vector_store import VectorStore
    from novi.brain.reasoning.external import reusable
    store = VectorStore(tmp_path, embed_model=FakeEmbed())
    brain = Brain(knowledge_layer=KnowledgeLayer(store))
    for statement in ['Atlas is open.', 'Atlas is not open.']:
        report = brain.ingest_evidence([dict(statement=statement, independent=True, volatility='stable', sources=[
            dict(url='https://example.org', excerpt=statement), dict(url='https://example.net', excerpt=statement)])])
        assert report['ok'], report
    for row in store.list_all():
        assert not reusable(store.item_from_row(row))
