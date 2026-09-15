"""Exercise automatic saving with a real selected model in disposable vaults.

Development fixtures only. Never reads or writes the user's configured vault.
The report records model decisions separately from store/application failures.
"""
import argparse
import json
import socket
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from novi.brain import Brain, Turn
from novi.brain.curation.jobs import MemoryJobs
from novi.brain.curation.resources import has_headroom
from novi.brain.layers.knowledge import KnowledgeLayer
from novi.brain.layers.scenarios import ScenarioLayer
from novi.brain.storage.conversation_store import ConversationStore
from novi.brain.storage.markdown_store import MarkdownStore
from novi.brain.storage.relationship_store import RelationshipStore
from novi.brain.storage.scenario_store import ScenarioStore
from novi.brain.storage.vector_store import VectorStore
from novi.brain.curation.worker import MemoryWorker
from novi.models.registry import ModelRegistry
from novi.models.service import ModelService
from novi.providers.base import ModelInfo
from novi.services.inference_coordinator import InferenceCoordinator
from tests.test_m2_markdown_sync import FakeEmbed

from .packets import development_packet
from .run import OwnedServer, api


def stop_owned(owner):
    try:
        owner.stop()
    except RuntimeError:
        # taskkill can race an exiting Ollama child on Windows.
        for _ in range(50):
            if owner.process is not None and owner.process.poll() is not None:
                break
            sleep(.1)
        if owner.process is None or owner.process.poll() is None:
            raise
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 11439))
        if owner.log:
            owner.log.close()


def run_case(case, model):
    with TemporaryDirectory(prefix='novi-memory-disposable-') as directory:
        root = Path(directory)
        vault = root / 'vault'
        if case['packet']['notes']:
            development_packet(case, vault)
        markdown = MarkdownStore(vault)
        vectors = VectorStore(root / 'brain', FakeEmbed())
        scenarios = ScenarioStore(root / 'brain')
        conversations = ConversationStore(root / 'brain')
        relationships = RelationshipStore(root / 'brain')
        brain = Brain(markdown_store=markdown,
                      knowledge_layer=KnowledgeLayer(vectors),
                      scenario_layer=ScenarioLayer(scenarios),
                      conversation_store=conversations,
                      relationship_store=relationships,
                      extractor=None)
        brain.reconcile_markdown()
        for turn in case['packet']['turns']:
            if turn['actor'] != 'user':
                # The normal observe path persists complete user/assistant turns.
                brain.observe(Turn(user='', assistant=turn['text'], conversation_id=case['id'],
                                   timestamp=datetime.now() - timedelta(hours=1)))
            else:
                brain.observe(Turn(user=turn['text'], assistant='', conversation_id=case['id'],
                                   timestamp=datetime.now() - timedelta(hours=1)))
        jobs = MemoryJobs(brain._conversation_store.database_path)
        registry = ModelRegistry()
        registry.update('ollama', [ModelInfo(name=model, provider='ollama')])
        models = ModelService({'llm': {'primary_model': model},
            'providers': {'default': 'ollama', 'ollama': {'url': 'http://127.0.0.1:11439',
                                                        'keep_alive': '30s'}}}, registry)
        models.inference = InferenceCoordinator(clock=lambda: 100)
        models.inference._last_foreground = 0
        worker = MemoryWorker(brain, jobs, models,
            lambda: {'memory': {'enabled': True, 'automatic_updates': True}},
            headroom=has_headroom)
        started = monotonic()
        result = worker.run_once()
        notes = []
        for path in markdown.list_files():
            meta, body = markdown.parse(path)
            notes.append({'path': path.name, 'id': meta.get('id'), 'status': meta.get('status'),
                          'body': body, 'manifest': meta.get('memory_manifest')})
        row = {'id': case['id'], 'state': worker.snapshot()['state'],
               'reason': worker.snapshot()['reason'], 'result': result,
               'job': jobs.recent(1), 'note_ids': worker.snapshot()['note_ids'],
               'notes': notes, 'seconds': round(monotonic()-started, 3)}
        jobs.close()
        conversations.close()
        relationships.close()
        scenarios.close()
        vectors.close()
        del worker, brain, vectors
        import gc
        gc.collect()
        return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ollama-exe', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', default=['dev-01', 'dev-14', 'managed-02'])
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; preserve trial results')
    cases = {case['id']: case for name in ('dev30', 'managed6')
             for case in json.loads((Path('tests/fixtures/memory_curation') / f'{name}.json').read_text(encoding='utf-8'))}
    if any(name not in cases for name in args.cases):
        parser.error('Unknown development case')
    owner = OwnedServer(args.ollama_exe)
    try:
        owner.start()
        installed = next((row for row in api('tags')['models'] if row['name'] == args.model), None)
        if installed is None:
            raise RuntimeError('Selected model is not installed; no pull attempted')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as report:
            report.write(json.dumps({'kind': 'manifest', 'model': installed,
                'runtime': api('version'), 'disposable_vault': True,
                'embedding': 'deterministic trial embedding', 'cases': args.cases}) + '\n')
            for name in args.cases:
                try:
                    row = run_case(cases[name], args.model)
                except Exception as exc:
                    import traceback
                    row = {'id': name, 'state': 'harness_error', 'error': repr(exc),
                           'traceback': traceback.format_exc()}
                report.write(json.dumps(row, ensure_ascii=False) + '\n')
                report.flush()
                print(name, row['state'], row.get('seconds'), flush=True)
    finally:
        stop_owned(owner)


if __name__ == '__main__':
    main()
