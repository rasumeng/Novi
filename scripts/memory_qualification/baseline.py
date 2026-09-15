"""Run the existing pure extractor without importing Brain or opening stores.

Loads unchanged source files into a private package to avoid novi.brain.__init__
constructing application dependencies. This measures extraction only, not the
production retention, consolidation, graph or persistence policy.
"""
import argparse
from dataclasses import asdict
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import ModuleType

from .run import digest


def load_extractor():
    root = Path(__file__).resolve().parents[2] / 'novi/brain'
    for name, path in [('_qualification_brain', root), ('_qualification_brain.reasoning', root / 'reasoning')]:
        package = ModuleType(name); package.__path__ = [str(path)]; sys.modules[name] = package
    modules = []
    for name, path in [('_qualification_brain.types', root / 'types.py'),
                       ('_qualification_brain.reasoning.extraction', root / 'reasoning/extraction.py')]:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module
        spec.loader.exec_module(module); modules.append(module)
    return modules[0].Turn, modules[1].KnowledgeExtractor, root


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', type=Path, default=Path('tests/fixtures/memory_curation/dev30.json'))
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    cases = json.loads(args.dataset.read_text(encoding='utf-8'))
    if any(c['split'] != 'development' for c in cases): raise ValueError('development only')
    Turn, Extractor, root = load_extractor()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        output.write(json.dumps({'kind': 'manifest', 'dataset_sha256': digest(args.dataset),
            'extractor_sha256': digest(root / 'reasoning/extraction.py'),
            'types_sha256': digest(root / 'types.py'), 'quality': 'ungraded',
            'scope': 'pure extractor only; no storage or lifecycle evaluation'}) + '\n')
        for case in cases:
            turns = []
            for t in case['packet']['turns']:
                turns.append(Turn(user=t['text'] if t['actor'] == 'user' else '',
                    assistant=t['text'] if t['actor'] == 'assistant' else '',
                    tool_outputs=(t['text'],) if t['actor'] == 'tool' else (),
                    timestamp=datetime(2026, 9, 11), conversation_id=case['id']))
            start = time.perf_counter()
            result = Extractor().extract(tuple(turns))
            output.write(json.dumps({'kind': 'case', 'id': case['id'],
                'seconds': time.perf_counter() - start, 'result': asdict(result)}, ensure_ascii=False) + '\n')
    print(f'Wrote {len(cases)} extraction-only baseline cases to {args.output}')


if __name__ == '__main__': main()
