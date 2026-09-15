"""Import one already-approved, verified artifact into an isolated Ollama store.

No download or inference. Does not alter normal Ollama models or Novi settings.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

from .run import OwnedServer, api, digest, check_binding


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file', required=True)
    p.add_argument('--alias', required=True)
    p.add_argument('--ollama-exe', type=Path, required=True)
    args = p.parse_args()
    manifest = json.loads(Path(__file__).with_name('artifacts.json').read_text())
    item = next(f for f in manifest['files'] if f['file'] == args.file)
    root = Path('model_cache/qualification').resolve()
    artifact = Path(item.get('reuse') or Path(manifest['storage']) / item['file'])
    if artifact.stat().st_size != item['bytes'] or digest(artifact) != item['sha256']:
        raise ValueError('artifact verification failed')
    if not args.alias.startswith('novi-qual-') or not all(c.isalnum() or c == '-' for c in args.alias):
        raise ValueError('evaluation alias must be novi-qual- followed by letters/digits/hyphens')
    store = root / 'ollama-store'; store.mkdir(parents=True, exist_ok=True)
    modelfile = root / (args.alias + '.Modelfile')
    modelfile.write_text(f'FROM "{artifact}"\n', encoding='utf-8')
    owner = OwnedServer(args.ollama_exe, store)
    try:
        owner.start()
        env = os.environ.copy(); env['OLLAMA_HOST'] = '127.0.0.1:11439'
        result = subprocess.run([str(args.ollama_exe), 'create', args.alias, '-f', str(modelfile)],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(result.stderr)
        show = api('show', {'model': args.alias})
        binding = check_binding(show, artifact, item['sha256'])
        (root / (args.alias + '-show.json')).write_text(json.dumps(show, indent=2), encoding='utf-8')
        print(json.dumps({'alias': args.alias, 'sha256': item['sha256'],
            'capabilities': show.get('capabilities'), 'model_store': str(store), 'binding': binding}), flush=True)
    finally:
        owner.stop()


if __name__ == '__main__': main()
