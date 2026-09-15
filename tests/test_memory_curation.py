import json
from threading import Event
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
from novi.brain.storage.conversation_store import ConversationStore
from novi.brain.storage.markdown_store import MarkdownStore
from novi.brain.types import Turn
from novi.brain.curation.jobs import MemoryJobs
from novi.brain.curation.contracts import Proposal, validate
from novi.brain.curation.pipeline import curate
from novi.brain.curation.packet import build_packet, digest
from novi.brain.curation.apply import apply_verified


def source():
    return {'conversation_id': 'chat', 'project_id': 'novi', 'notes': [], 'turns': [
        {'id': 'chat:0:user', 'actor': 'user', 'text': 'For Novi I prefer local models.', 'timestamp': None}]}


def proposal():
    return {'outcome': 'propose', 'reason': 'Explicit preference', 'operations': [{
        'id': 'op1', 'action': 'add', 'target_id': '', 'expected_revision': '', 'section_id': '',
        'actor': 'user', 'subject': 'Novi model preference',
        'scopes': [{'text': 'For Novi', 'source_id': 'chat:0:user'}], 'temporal': '',
        'markdown': 'For Novi the user prefers local models.',
        'evidence': [{'source_id': 'chat:0:user', 'start': 0, 'end': len('For Novi I prefer local models.'),
                      'quote': 'For Novi I prefer local models.'}], 'link_id': '', 'relation': 'none'}]}


def stores(tmp_path):
    conversations = ConversationStore(tmp_path)
    conversations.append(Turn(user='For Novi I prefer local models.', assistant='', project_id='novi',
                              timestamp=datetime.now()-timedelta(hours=1)), 'chat')
    return conversations, MemoryJobs(conversations.database_path), MarkdownStore(tmp_path/'vault')


def test_scope_is_visible_and_attribution_exact():
    p = proposal()
    validate(Proposal.model_validate(p), source())
    p['operations'][0]['markdown'] = 'The user prefers local models.'
    with pytest.raises(ValueError, match='Scope missing'):
        validate(Proposal.model_validate(p), source())
    p = proposal()
    p['operations'][0]['actor'] = 'assistant'
    with pytest.raises(ValueError, match='actor mismatch'):
        validate(Proposal.model_validate(p), source())


def test_repair_is_reviewed_again_with_original_evidence():
    seen = []
    responses = [json.dumps(proposal()), '{"verdict":"revise","reason":"Clarify scope"}',
                 json.dumps(proposal()), '{"verdict":"approve","reason":"Faithful"}']
    def generate(system, payload, schema, cancel):
        seen.append(payload)
        return responses.pop(0)
    result = curate(source(), SimpleNamespace(generate=generate), Event())
    assert result['state'] == 'approved'
    assert len(seen) == 4
    assert seen[1]['packet'] == seen[3]['packet'] == source()
    assert 'repair' not in seen[3]


def test_shadow_progress_does_not_consume_apply_work(tmp_path):
    conversations, jobs, _ = stores(tmp_path)
    shadow = jobs.claim(mode='shadow')
    assert shadow['packet']['project_id'] == 'novi'
    jobs.finish(shadow, 'shadow', {})
    assert jobs.claim(mode='shadow') is None
    assert jobs.claim(mode='apply')['start_seq'] == 0
    assert conversations.pending_extraction('chat')[0] == 1


def test_expired_work_recovers_and_failed_work_keeps_watermark(tmp_path):
    conversations, jobs, _ = stores(tmp_path)
    now = [datetime.now().timestamp()]
    jobs.clock = lambda: now[0]
    job = jobs.claim(lease_seconds=10)
    now[0] += 11
    recovered = jobs.claim()
    assert recovered['id'] == job['id']
    jobs.finish(recovered, 'deferred', error='failure')
    assert jobs.claim() is None
    now[0] += 4000
    assert jobs.claim()['id'] == job['id']


def test_journaled_add_replays_after_projection_failure(tmp_path):
    _, jobs, markdown = stores(tmp_path)
    job = jobs.claim(mode='apply')
    result = {'state': 'approved', 'proposal': proposal()}
    def fail():
        raise RuntimeError('projection unavailable')
    with pytest.raises(RuntimeError):
        apply_verified(job, result, source(), markdown, jobs, fail, Event())
    assert len(markdown.list_files()) == 1
    ids = apply_verified(job, result, source(), markdown, jobs, lambda: None, Event())
    assert len(ids) == len(markdown.list_files()) == 1
    meta, body = markdown.parse(markdown.list_files()[0])
    assert meta['status'] == 'candidate'
    assert 'For Novi' in body
    assert jobs.operation(job['id'], 'op1')['done']


def test_user_edit_after_preparation_is_never_overwritten(tmp_path):
    _, jobs, markdown = stores(tmp_path)
    job = jobs.claim(mode='apply')
    result = {'state': 'approved', 'proposal': proposal()}
    apply_verified(job, result, source(), markdown, jobs, lambda: None, Event())
    path = markdown.list_files()[0]
    path.write_text('User edit', encoding='utf-8')
    with pytest.raises(ValueError, match='changed after preparation'):
        apply_verified(job, result, source(), markdown, jobs, lambda: None, Event())
    assert path.read_text() == 'User edit'


def test_edited_managed_sections_are_not_update_targets(tmp_path):
    _, jobs, markdown = stores(tmp_path)
    job = jobs.claim(mode='apply')
    ids = apply_verified(job, {'state': 'approved', 'proposal': proposal()}, source(), markdown, jobs, lambda: None, Event())
    packet = build_packet(source(), markdown, ids)
    assert len(packet['notes'][0]['sections']) == 1
    path = markdown.list_files()[0]
    path.write_text(path.read_text().replace('the user prefers', 'the user no longer prefers'), encoding='utf-8')
    assert build_packet(source(), markdown, ids)['notes'][0]['sections'] == {}


def test_shadow_job_cannot_apply(tmp_path):
    _, jobs, markdown = stores(tmp_path)
    job = jobs.claim(mode='shadow')
    with pytest.raises(ValueError, match='approved apply'):
        apply_verified(job, {'state': 'approved', 'proposal': proposal()}, source(), markdown, jobs, lambda: None, Event())
    assert not markdown.list_files()


def test_update_preserves_history_and_rejects_changed_scope(tmp_path):
    _, jobs, markdown = stores(tmp_path)
    job = jobs.claim(mode='apply')
    ids = apply_verified(job, {'state': 'approved', 'proposal': proposal()}, source(), markdown, jobs, lambda: None, Event())
    packet = build_packet(source(), markdown, ids)
    note = packet['notes'][0]
    changed = proposal()
    op = changed['operations'][0]
    op.update(action='update', target_id=ids[0], expected_revision=note['revision'],
              section_id=next(iter(note['sections'])), relation='supersedes')
    op['scopes'] = []
    with pytest.raises(ValueError, match='Correction scope'):
        validate(Proposal.model_validate(changed), packet)
    op['scopes'] = proposal()['operations'][0]['scopes']
    op['markdown'] = 'For Novi the user currently prefers local models.'
    second_job = dict(job, id=job['id']+'-revision')
    apply_verified(second_job, {'state': 'approved', 'proposal': changed}, packet, markdown, jobs, lambda: None, Event())
    meta, _ = markdown.parse(markdown.list_files()[0])
    assert len(meta['memory_manifest']['history']) == 1
    assert 'For Novi the user prefers local models.' == meta['memory_manifest']['history'][0]['markdown']


def test_curated_note_rebuilds_real_store_and_provenance(tmp_path):
    from tests.test_unified_brain import build
    from novi.brain.types import EdgeKind
    brain = build(tmp_path)
    brain._extractor = None
    brain.observe(Turn(user=source()['turns'][0]['text'], assistant='', conversation_id='chat',
                       timestamp=datetime.now()-timedelta(hours=1)))
    jobs = MemoryJobs(brain._conversation_store.database_path)
    job = jobs.claim(mode='apply')
    ids = brain.apply_memory(job, {'state': 'approved', 'proposal': proposal()}, source(), jobs, Event())
    item = brain._knowledge_layer.store.get(ids[0])
    assert item['status'] == 'candidate'
    assert 'For Novi' in item['text']
    assert any(edge.target_id == 'chat' for edge in brain._relationship_store.outgoing(ids[0], kind=EdgeKind.DERIVED_FROM))
    brain._knowledge_layer.store.delete(ids[0])
    brain._relationship_store.remove(source_id=ids[0], kind=EdgeKind.DERIVED_FROM)
    brain.reconcile_markdown()
    assert brain._knowledge_layer.store.get(ids[0])
    assert brain._relationship_store.outgoing(ids[0], kind=EdgeKind.DERIVED_FROM)

