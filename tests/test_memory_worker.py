from datetime import datetime, timedelta
from threading import Event
from types import SimpleNamespace
import json
from novi.brain.curation.jobs import MemoryJobs
from novi.brain.curation.worker import MemoryWorker
from novi.brain.storage.conversation_store import ConversationStore
from novi.brain.types import Turn
from novi.services.inference_coordinator import InferenceCoordinator
from novi.providers.memory import OllamaMemoryClient


def fixture(tmp_path):
    store = ConversationStore(tmp_path)
    store.append(Turn(user='Thanks.', assistant='', timestamp=datetime.now()-timedelta(hours=1)), 'c')
    jobs = MemoryJobs(store.database_path)
    calls = []
    events = []
    models = SimpleNamespace(inference=InferenceCoordinator(),
                             resolve_primary_snapshot=lambda: SimpleNamespace(provider='ollama', model='selected', config={}))
    def client(resolved, **kwargs):
        calls.append(resolved.model)
        def generate(system, payload, schema, cancel):
            if schema.__name__ == 'Proposal':
                return json.dumps({'outcome': 'abstain', 'operations': [], 'reason': 'Filler'})
            return json.dumps({'verdict': 'approve', 'reason': 'Filler'})
        return SimpleNamespace(generate=generate)
    brain = SimpleNamespace(memory_packet=lambda packet: packet, emit_memory_activity=events.append,
                            apply_memory=lambda *args: (_ for _ in ()).throw(AssertionError('unexpected write')))
    worker = MemoryWorker(brain, jobs, models, lambda: {'memory': {'automatic_updates': True}},
                          client_factory=client, headroom=lambda: True)
    return worker, calls, events, store


def test_automatic_saving_is_a_user_setting_enabled_by_default():
    from novi.configuration.bootstrap import build_registry
    from novi.configuration.schema import Visibility
    setting = build_registry().get('memory.automatic_updates')
    assert setting.default is True
    assert setting.visibility is Visibility.USER


def test_automatic_work_runs_without_a_model_qualification_report(tmp_path):
    worker, calls, events, _ = fixture(tmp_path)
    worker.models.inference = InferenceCoordinator(clock=lambda: 100)
    worker.models.inference._last_foreground = 0
    assert worker.run_once()['state'] == 'abstained'
    assert calls == ['selected']
    assert worker.jobs.recent()[0]['mode'] == 'apply'


def test_updates_off_reports_pending_without_claiming_work(tmp_path):
    worker, calls, _, _ = fixture(tmp_path)
    worker.config = lambda: {'memory': {'automatic_updates': False}}
    assert worker.run_once() is None
    assert worker.snapshot()['state'] == 'disabled'
    assert 'remain pending' in worker.snapshot()['reason']
    assert worker.jobs.recent() == []
    assert calls == []


def test_memory_off_does_not_claim_even_when_automatic_setting_is_on(tmp_path):
    worker, calls, _, _ = fixture(tmp_path)
    worker.config = lambda: {'memory': {'enabled': False, 'automatic_updates': True}}
    assert worker.run_once() is None
    assert worker.snapshot()['state'] == 'disabled'
    assert worker.jobs.recent() == []
    assert calls == []


def test_manual_shadow_runs_selected_model_and_never_applies(tmp_path):
    worker, calls, events, _ = fixture(tmp_path)
    result = worker.run_once(manual_shadow=True)
    assert result['state'] == 'abstained'
    assert calls == ['selected']
    assert [e['state'] for e in events] == ['proposing', 'verifying', 'abstained']
    assert worker.jobs.recent()[0]['mode'] == 'shadow'


def test_foreground_and_close_prevent_shadow_calls(tmp_path):
    worker, calls, _, _ = fixture(tmp_path)
    with worker.models.inference.acquire_foreground():
        assert worker.run_once(manual_shadow=True) is None
    worker.close()
    assert worker.run_once(manual_shadow=True) is None
    assert not calls


def test_pause_is_durable(tmp_path):
    worker, _, _, store = fixture(tmp_path)
    worker.pause()
    reopened = MemoryJobs(store.database_path)
    assert reopened.control('paused') is True


def test_pause_during_application_retains_cancellation_after_model_release(tmp_path, monkeypatch):
    worker, _, _, _ = fixture(tmp_path)
    worker.models.inference = InferenceCoordinator(clock=lambda: 100)
    worker.models.inference._last_foreground = 0
    monkeypatch.setattr('novi.brain.curation.worker.curate',
                        lambda *args: {'state': 'approved', 'proposal': {'operations': []}})
    def apply(job, result, packet, jobs, cancel):
        assert not worker.models.inference.memory_active
        worker.pause()
        assert cancel.is_set()
        raise InterruptedError('Memory application paused')
    worker.brain.apply_memory = apply
    assert worker.run_once()['state'] == 'deferred'
    assert worker.jobs.recent()[0]['state'] == 'deferred'
    assert worker._active_cancel is None


def test_switching_off_automatic_saving_cancels_before_write(tmp_path, monkeypatch):
    worker, _, _, _ = fixture(tmp_path)
    worker.models.inference = InferenceCoordinator(clock=lambda: 100)
    worker.models.inference._last_foreground = 0
    settings = {'enabled': True, 'automatic_updates': True}
    worker.config = lambda: {'memory': settings}
    applied = []
    worker.brain.apply_memory = lambda *args: applied.append(args)

    def propose(packet, client, cancel, on_review):
        settings['automatic_updates'] = False
        worker.configuration_changed()
        assert cancel.is_set()
        return {'state': 'approved', 'proposal': {'operations': []}}

    monkeypatch.setattr('novi.brain.curation.worker.curate', propose)
    assert worker.run_once()['state'] == 'deferred'
    assert applied == []
    assert worker.jobs.recent()[0]['state'] == 'deferred'


def test_foreground_demand_interrupts_application_between_writes(tmp_path, monkeypatch):
    worker, _, _, _ = fixture(tmp_path)
    worker.models.inference = InferenceCoordinator(clock=lambda: 100)
    worker.models.inference._last_foreground = 0
    monkeypatch.setattr('novi.brain.curation.worker.curate',
                        lambda *args: {'state': 'approved', 'proposal': {'operations': []}})

    def apply(job, result, packet, jobs, cancel):
        with worker.models.inference.acquire_foreground():
            assert cancel.is_set()
            raise InterruptedError('Foreground demand during application')

    worker.brain.apply_memory = apply
    assert worker.run_once()['state'] == 'deferred'
    assert worker.jobs.recent()[0]['state'] == 'deferred'


def test_split_source_never_advances_past_failed_segment(tmp_path):
    store = ConversationStore(tmp_path)
    text = 'é' * 10000
    store.append(Turn(user=text, assistant='', timestamp=datetime.now()-timedelta(hours=1)), 'long')
    jobs = MemoryJobs(store.database_path)
    first = jobs.claim()
    jobs.finish(first, 'deferred', error='retry')
    assert jobs.claim() is None  # Later segments cannot jump the failed one.
    jobs.finish(first, 'shadow', {})
    all_text = ''.join(t['text'] for t in first['packet']['turns'])
    while (job := jobs.claim()) is not None:
        all_text += ''.join(t['text'] for t in job['packet']['turns'])
        jobs.finish(job, 'shadow', {})
    assert all_text == text
    assert jobs.db.execute("SELECT next_seq FROM memory_progress WHERE mode='shadow'").fetchone()[0] == 1


def test_each_job_pins_one_selected_model_across_proposal_review_and_repair(tmp_path):
    worker, _, _, store = fixture(tmp_path)
    selected = {'model': 'first', 'url': 'http://127.0.0.1:11434'}
    calls = []
    worker.models.resolve_primary_snapshot = lambda: SimpleNamespace(
        provider='ollama', model=selected['model'], config={'url': selected['url']})

    def client(resolved, **kwargs):
        identity = (resolved.provider, resolved.model, resolved.config['url'])
        def generate(system, payload, schema, cancel):
            calls.append((identity, schema.__name__))
            if schema.__name__ == 'Proposal':
                if len(calls) == 1:
                    selected.update(model='second', url='http://127.0.0.1:9999')
                return json.dumps({'outcome': 'abstain', 'operations': [], 'reason': 'Filler'})
            return json.dumps({'verdict': 'revise' if len(calls) == 2 else 'approve',
                               'reason': 'Recheck'})
        return SimpleNamespace(generate=generate)

    worker.client_factory = client
    assert worker.run_once(manual_shadow=True)['state'] == 'abstained'
    assert calls == [(('ollama', 'first', 'http://127.0.0.1:11434'), pass_name)
                     for pass_name in ('Proposal', 'Review', 'Proposal', 'Review')]
    store.append(Turn(user='Thanks again.', assistant='', timestamp=datetime.now()-timedelta(hours=1)), 'c')
    calls.clear()
    assert worker.run_once(manual_shadow=True)['state'] == 'abstained'
    assert calls[0][0] == ('ollama', 'second', 'http://127.0.0.1:9999')


def test_unsupported_selected_provider_defers_without_calling_another_model(tmp_path):
    worker, _, _, _ = fixture(tmp_path)
    worker.models.resolve_primary_snapshot = lambda: SimpleNamespace(
        provider='openai', model='selected-cloud', config={'url': 'https://example.test'})
    worker.client_factory = lambda resolved, **kwargs: OllamaMemoryClient(resolved, **kwargs)
    result = worker.run_once(manual_shadow=True)
    assert result is None
    assert worker.snapshot()['state'] == 'unavailable'
    assert 'local Ollama provider' in worker.snapshot()['reason']
    assert worker.jobs.recent() == []


def test_automatic_worker_saves_and_rebuilds_a_reviewed_note(tmp_path):
    from tests.test_unified_brain import build
    from novi.brain.types import EdgeKind

    brain = build(tmp_path)
    brain._extractor = None
    brain.observe(Turn(user='For Novi I prefer local models.', assistant='',
                       conversation_id='chat', project_id='novi',
                       timestamp=datetime.now()-timedelta(hours=1)))
    jobs = MemoryJobs(brain._conversation_store.database_path)
    coordinator = InferenceCoordinator(clock=lambda: 100)
    coordinator._last_foreground = 0
    models = SimpleNamespace(inference=coordinator, resolve_primary_snapshot=lambda:
                             SimpleNamespace(provider='ollama', model='chosen', config={}))
    passes = []

    def client(resolved, **kwargs):
        assert resolved.model == 'chosen'
        def generate(system, payload, schema, cancel):
            passes.append(schema.__name__)
            if schema.__name__ == 'Review':
                return json.dumps({'verdict': 'approve', 'reason': 'Supported by the user turn'})
            source = payload['packet']['turns'][0]
            return json.dumps({'outcome': 'propose', 'reason': 'Explicit scoped preference',
                               'operations': [{'id': 'op1', 'action': 'add', 'target_id': '',
                                               'expected_revision': '', 'section_id': '',
                                               'actor': 'user', 'subject': 'Novi model preference',
                                               'scopes': [{'text': 'For Novi', 'source_id': source['id']}],
                                               'temporal': '',
                                               'markdown': 'For Novi the user prefers local models.',
                                               'evidence': [{'source_id': source['id'], 'start': 0,
                                                             'end': len(source['text']), 'quote': source['text']}],
                                               'link_id': '', 'relation': 'none'}]})
        return SimpleNamespace(generate=generate)

    worker = MemoryWorker(brain, jobs, models, lambda: {'memory': {'automatic_updates': True}},
                          client_factory=client, headroom=lambda: True)
    result = worker.run_once()
    assert result['state'] == 'approved'
    assert passes == ['Proposal', 'Review']
    assert jobs.recent()[0]['state'] == 'applied'
    assert worker.snapshot()['state'] == 'applied'
    note_id = worker.snapshot()['note_ids'][0]
    assert brain._knowledge_layer.store.get(note_id)['status'] == 'candidate'
    assert brain._relationship_store.outgoing(note_id, kind=EdgeKind.DERIVED_FROM)
    brain._knowledge_layer.store.delete(note_id)
    brain.reconcile_markdown()
    assert brain._knowledge_layer.store.get(note_id)
    assert jobs.claim(mode='apply') is None


def test_automatic_deferral_is_parked_after_three_attempts_and_new_turns_can_continue(tmp_path, monkeypatch):
    worker, _, events, store = fixture(tmp_path)
    worker.models.inference = InferenceCoordinator(clock=lambda: 100)
    worker.models.inference._last_foreground = 0
    monkeypatch.setattr('novi.brain.curation.worker.curate',
                        lambda *args: {'state': 'deferred', 'reason': 'Ambiguous'})
    for attempt in range(3):
        worker.jobs.db.execute("UPDATE memory_jobs SET retry_at=0 WHERE state='deferred'")
        worker.jobs.db.commit()
        result = worker.run_once()
        assert result['state'] == ('parked' if attempt == 2 else 'deferred')
    assert worker.jobs.recent()[0]['state'] == 'parked'
    assert events[-1]['state'] == 'parked'

    store.append(Turn(user='A new fact.', assistant='', timestamp=datetime.now()-timedelta(hours=1)), 'c')
    assert worker.jobs.claim(mode='apply') is not None
