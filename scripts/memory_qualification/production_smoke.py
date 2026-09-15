"""Explicit selected-model shadow smoke test through the production boundary.

Run from repo root with a working environment. Never constructs production Brain.
The managed suite creates disposable Markdown fixtures in a temporary directory.
Uses the already-installed model; owns and cleans up only its test Ollama server.
"""
import argparse
import json
from pathlib import Path
from threading import Event
from time import monotonic
from tempfile import TemporaryDirectory
from .run import OwnedServer, api
from novi.models.service import ModelService
from novi.models.registry import ModelRegistry
from novi.providers.base import ModelInfo
from novi.brain.curation.pipeline import curate
from novi.brain.curation.resources import has_headroom


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--ollama-exe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=1)
    parser.add_argument('--suite', choices=['dev30', 'managed6'], default='dev30')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Preserve existing test results')
    if not has_headroom(incremental_bytes=5*1024**3):
        parser.error('Insufficient headroom for this explicit smoke test')
    dataset = Path('tests/fixtures/memory_curation') / f'{args.suite}.json'
    cases = json.loads(dataset.read_text(encoding='utf-8'))
    if not 1 <= args.limit <= len(cases):
        parser.error('Invalid development case limit')
    owner = OwnedServer(args.ollama_exe)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        owner.start()
        installed = next((m for m in api('tags')['models'] if m['name'] == args.model), None)
        if installed is None:
            raise ValueError('Selected model is not installed; no pull')
        registry = ModelRegistry()
        registry.update('ollama', [ModelInfo(name=args.model, provider='ollama')])
        models = ModelService({'llm': {'primary_model': args.model},
                               'providers': {'default': 'ollama', 'ollama': {'url': 'http://127.0.0.1:11439', 'keep_alive': '30s'}}}, registry)
        resolved = models.resolve_primary_snapshot()
        with args.output.open('x', encoding='utf-8') as output:
            output.write(json.dumps({'kind': 'manifest', 'model': installed, 'runtime': api('version'),
                                     'real_vault_writes': False, 'suite': args.suite,
                                     'temporary_note_fixtures': args.suite == 'managed6',
                                     'settings': {'context': 16384, 'output': 1800, 'timeout': 180,
                                                  'temperature': 0, 'thinking': False,
                                                  'provider': resolved.config},
                                     'quality': 'development, not human graded',
                                     'source_snapshot': {str(path): path.read_text(encoding='utf-8') for path in [
                                         Path(__file__), Path('novi/brain/curation/contracts.py'),
                                         Path('novi/brain/curation/draft.py'),
                                         dataset, Path('scripts/memory_qualification/packets.py'),
                                         Path('novi/brain/curation/pipeline.py'), Path('novi/providers/memory.py')]}})+'\n')
            output.flush()
            for case in cases[:args.limit]:
                packet = {'conversation_id': case['id'], 'project_id': None,
                          'turns': case['packet']['turns'], 'notes': [dict(n, revision='test-revision',
                          body=n.get('content', n.get('text', '')), sections={}, claims={}) for n in case['packet']['notes']]}
                if args.suite == 'managed6':
                    from .packets import development_packet
                    with TemporaryDirectory(prefix='novi-memory-development-') as temp:
                        packet = development_packet(case, Path(temp))
                client = models.memory_client(resolved, context=16384, output=1800, timeout=180, headroom=has_headroom)
                start = monotonic()
                try:
                    result = curate(packet, client, Event())
                except Exception as exc:
                    result = {'state': 'runtime_failure', 'error': repr(exc)}
                row = {'kind': 'case', 'id': case['id'], 'packet': packet, 'result': result, 'calls': client.calls,
                       'seconds': monotonic()-start}
                output.write(json.dumps(row, ensure_ascii=False)+'\n')
                output.flush()
                print(case['id'], result['state'], round(row['seconds'], 2), flush=True)
                if result['state'] == 'runtime_failure':
                    break
    finally:
        owner.stop()


if __name__ == '__main__':
    main()
