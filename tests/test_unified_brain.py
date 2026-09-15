"""End-to-end vault and durable learning contracts, with real local stores."""
from pathlib import Path

import pytest

from novi.brain import Brain, Turn
from novi.brain.types import KnowledgeItem, KnowledgeForm, KnowledgeStatus
from novi.brain.layers.knowledge import KnowledgeLayer
from novi.brain.layers.scenarios import ScenarioLayer
from novi.brain.reasoning.extraction import KnowledgeExtractor, ExtractionResult, ExtractedClaim
from novi.brain.reasoning.verification import find_near_duplicate
from novi.brain.storage.conversation_store import ConversationStore
from novi.brain.storage.markdown_store import MarkdownStore
from novi.brain.storage.relationship_store import RelationshipStore
from novi.brain.storage.scenario_store import ScenarioStore
from novi.brain.storage.vector_store import VectorStore
from tests.test_m2_markdown_sync import FakeEmbed


def build(root, extractor=None):
    return Brain(
        markdown_store=MarkdownStore(root / 'vault'),
        knowledge_layer=KnowledgeLayer(VectorStore(root / 'brain', FakeEmbed())),
        scenario_layer=ScenarioLayer(ScenarioStore(root / 'brain')),
        conversation_store=ConversationStore(root / 'brain'),
        relationship_store=RelationshipStore(root / 'brain'),
        extractor=extractor or KnowledgeExtractor(),
    )


def test_vault_edit_rename_delete_and_rebuild(tmp_path):
    brain = build(tmp_path)
    vault = tmp_path / 'vault'
    vault.mkdir()
    note = vault / 'Decision.md'
    note.write_text('---\nowner: Alice\n---\nWe use SQLite for offline operation.', encoding='utf-8')
    brain.reconcile_markdown()
    meta, body = brain._markdown_store.parse(note)
    kid = meta['id']
    assert meta['owner'] == 'Alice'
    assert len(list(vault.glob('*.md'))) == 1
    note.write_text(note.read_text(encoding='utf-8').replace('SQLite', 'PostgreSQL'), encoding='utf-8')
    renamed = note.rename(vault / 'Database.md')
    brain.reconcile_markdown()
    assert brain._markdown_store.parse(renamed)[0]['id'] == kid
    assert 'PostgreSQL' in brain._knowledge_layer.store.get(kid)['text']
    brain._knowledge_layer.store.delete(kid)
    brain.reconcile_markdown()
    assert brain._knowledge_layer.store.get(kid) is not None
    renamed.unlink()
    brain.reconcile_markdown()
    assert brain._knowledge_layer.store.get(kid)['status'] == 'superseded'


def test_opposite_claims_do_not_merge():
    item = KnowledgeItem('a', KnowledgeForm.ATOMIC, 'The user prefers Python.', .8)
    assert find_near_duplicate([item], 'The user dislikes Python.') is None
    assert find_near_duplicate([item], 'The user does not prefer Python.') is None


def test_repetition_counts_independent_sources(tmp_path):
    brain = build(tmp_path)
    layer = brain._knowledge_layer
    result = ExtractionResult(claims=(ExtractedClaim('The build uses Python version 3.12.', .8),))
    for cid in ('a', 'b', 'c', 'd', 'd'):
        layer.store_extracted(cid, 's', result)
    brain.reflect()
    items = layer.list_objects()
    assert len(items) == 1
    assert set(items[0].sources) == {'a', 'b', 'c', 'd'}
    assert items[0].status == KnowledgeStatus.VERIFIED


def test_short_conversation_recovered_after_restart(tmp_path):
    brain = build(tmp_path)
    brain.observe(Turn('The build uses Python version 3.12.', '', conversation_id='a'))
    assert brain._knowledge_layer.store.count() == 0
    restarted = build(tmp_path)
    restarted.maintain()
    assert restarted._knowledge_layer.store.count() > 0
    count = restarted._knowledge_layer.store.count()
    restarted.maintain()
    assert restarted._knowledge_layer.store.count() == count


def test_failed_extraction_retries(tmp_path):
    class Flaky:
        def extract(self, turns):
            raise RuntimeError('offline')
    brain = build(tmp_path, Flaky())
    brain.observe(Turn('The build uses Python version 3.12.', '', conversation_id='a'))
    result = brain.maintain()
    assert result['errors']
    brain._extractor = KnowledgeExtractor()
    assert not brain.maintain()['errors']
    assert brain._knowledge_layer.store.count() > 0


def test_assistant_preference_is_not_user_confirmation(tmp_path):
    brain = build(tmp_path)
    brain.observe(Turn('Please explain this decision.', 'I prefer Python for backend work.', conversation_id='a'))
    brain.maintain()
    assert all(i.status != KnowledgeStatus.VERIFIED for i in brain._knowledge_layer.list_objects())


def test_dimension_migration_retains_knowledge(tmp_path):
    store = VectorStore(tmp_path, FakeEmbed(16))
    store.add(KnowledgeItem('a', KnowledgeForm.ATOMIC, 'A durable database decision.', .8))
    migrated = VectorStore(tmp_path, FakeEmbed(32))
    assert migrated.get('a')['text'] == 'A durable database decision.'
    assert migrated.query('database', distance_threshold=None)


def test_recall_receives_conversation_scope_and_trust(tmp_path):
    from novi.brain.types import QueryContext
    brain = build(tmp_path)
    brain.observe(Turn('The project uses SQLite for offline deployment.', '', conversation_id='c'))
    brain.maintain()
    result = brain.recall('SQLite', QueryContext(conversation_id='c', distance_threshold=None))
    assert result.metrics['scenario_id'] == brain._conversation_store.get('c').scenario_id
    item = next(i for i in result.items if i.source == 'knowledge')
    assert 'status' in item.metadata and 'sources' in item.metadata


def test_retry_after_partial_projection_does_not_duplicate_evidence(tmp_path, monkeypatch):
    brain = build(tmp_path)
    brain.observe(Turn('The project uses SQLite for offline deployment.', '', conversation_id='c'))
    write = brain._markdown_store.write_item
    monkeypatch.setattr(brain._markdown_store, 'write_item', lambda *a, **kw: (_ for _ in ()).throw(OSError('disk full')))
    assert brain.maintain()['errors']
    count = brain._knowledge_layer.store.count()
    monkeypatch.setattr(brain._markdown_store, 'write_item', write)
    assert brain.maintain()['errors'] == []
    assert brain._knowledge_layer.store.count() == count
    assert all(len(i.sources) == 1 for i in brain._knowledge_layer.list_objects())


def test_unchanged_maintenance_does_not_embed(tmp_path, monkeypatch):
    brain = build(tmp_path)
    brain.learn('A persistent project decision.')
    brain.maintain()
    def forbidden(*args, **kwargs):
        raise AssertionError('Unchanged maintenance must not re-embed')
    monkeypatch.setattr(brain._knowledge_layer.store, '_embed', forbidden)
    assert brain.maintain()['errors'] == []


def test_duplicate_note_ids_fail_without_retiring_original(tmp_path):
    brain = build(tmp_path)
    report = brain.learn('A persistent project decision.')
    note = tmp_path / 'vault' / report['markdown']['path']
    (note.parent / 'copy.md').write_text(note.read_text(encoding='utf-8'), encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate knowledge id'):
        brain.reconcile_markdown()
    assert brain._knowledge_layer.store.get(report['item_id'])['status'] == 'verified'


def test_note_index_refresh_and_failed_embedding_are_retryable(tmp_path, monkeypatch):
    from novi.memory.knowledge_index import KnowledgeIndex
    brain = build(tmp_path)
    index = KnowledgeIndex(tmp_path / 'vault', tmp_path / 'chunks', embed_model=FakeEmbed())
    brain._knowledge_index = index
    report = brain.learn('The database uses SQLite.')
    note = tmp_path / 'vault' / report['markdown']['path']
    note.write_text(note.read_text(encoding='utf-8').replace('SQLite', 'PostgreSQL'), encoding='utf-8')
    original = index.store.embed_func
    monkeypatch.setattr(index.store, 'embed_func', lambda text: (_ for _ in ()).throw(RuntimeError('embed offline')))
    with pytest.raises(RuntimeError, match='embed offline'):
        brain.reconcile_markdown()
    assert index.store.count() == 1  # failed replacement retained old projection
    monkeypatch.setattr(index.store, 'embed_func', original)
    brain.reconcile_markdown()
    assert all('SQLite' not in r['text'] for r in index.search('database', rerank=False))
    note.unlink()
    brain.reconcile_markdown()
    assert index.search('database', rerank=False) == []


def test_extracted_claims_have_navigable_wiki_links(tmp_path):
    from novi.brain.types import EdgeKind
    brain = build(tmp_path)
    brain.observe(Turn('The project uses SQLite for offline deployment.', '', conversation_id='c'))
    brain.maintain()
    claim = next(i for i in brain._knowledge_layer.list_objects() if i.form == KnowledgeForm.ATOMIC)
    path = brain._markdown_store.find_for_id(claim.id)
    meta, _ = brain._markdown_store.parse(path)
    assert meta['related'][0].startswith('[[')
    assert brain._relationship_store.outgoing(claim.id, kind=EdgeKind.REFERENCES)


def test_embedding_model_change_same_dimension_reembeds(tmp_path):
    class ChangedEmbed(FakeEmbed):
        calls = 0
        @property
        def model_name(self):
            return 'different-space'
        def encode(self, text, normalize=True):
            self.calls += 1
            return super().encode(text, normalize)
    original = VectorStore(tmp_path, FakeEmbed())
    original.add(KnowledgeItem('a', KnowledgeForm.ATOMIC, 'A durable database decision.', .8))
    changed = ChangedEmbed()
    reopened = VectorStore(tmp_path, changed)
    assert changed.calls == 1
    assert reopened.get('a')['text'] == 'A durable database decision.'


def test_embedding_migration_failure_preserves_active_table(tmp_path):
    class Offline(FakeEmbed):
        def encode(self, text, normalize=True):
            raise RuntimeError('offline')
    original = VectorStore(tmp_path, FakeEmbed(16))
    original.add(KnowledgeItem('a', KnowledgeForm.ATOMIC, 'A durable database decision.', .8))
    with pytest.raises(RuntimeError, match='offline'):
        VectorStore(tmp_path, Offline(32))
    assert original.get('a') is not None


def test_malformed_note_does_not_replace_or_retire_knowledge(tmp_path):
    brain = build(tmp_path)
    report = brain.learn('A persistent project decision.')
    path = tmp_path / 'vault' / report['markdown']['path']
    path.write_text('---\ntags: [broken\n---\nA changed claim.', encoding='utf-8')
    assert brain.maintain()['errors']
    assert brain._knowledge_layer.store.get(report['item_id'])['text'] == 'A persistent project decision.'


def test_delayed_write_preserves_human_edit(tmp_path):
    brain = build(tmp_path)
    report = brain.learn('The database uses SQLite.')
    path = tmp_path / 'vault' / report['markdown']['path']
    path.write_text(path.read_text(encoding='utf-8').replace('SQLite', 'PostgreSQL'), encoding='utf-8')
    brain._sync_markdown(report['item_id'])
    assert 'PostgreSQL' in brain._knowledge_layer.store.get(report['item_id'])['text']
    assert 'PostgreSQL' in brain._markdown_store.parse(path)[1]


def test_unavailable_vault_does_not_retire_every_note(tmp_path):
    brain = build(tmp_path)
    report = brain.learn('A persistent project decision.')
    (tmp_path / 'vault').rename(tmp_path / 'temporarily-offline')
    assert brain.maintain()['errors']
    assert brain._knowledge_layer.store.get(report['item_id'])['status'] == 'verified'
