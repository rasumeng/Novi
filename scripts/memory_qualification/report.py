"""Summarize measurements without mislabeling self-approval as accuracy."""
import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(path):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    cases = [r for r in rows if r['kind'] == 'case']
    result = {'run': str(path), 'attempted_cases': len(cases),
        'statuses': dict(Counter(r['result']['status'] for r in cases)),
        'accepted_outcomes': dict(Counter(
            r['result']['proposal']['outcome'] for r in cases
            if r['result']['status'] == 'accepted_shadow')),
        'quality_metrics': None, 'quality_reason': 'Requires human claim/link/correction annotations.',
        'foreground_latency_regression': None, 'rows': []}
    for row in cases:
        telemetry = row['telemetry']; samples = telemetry['samples']; baseline = telemetry['baseline']
        peak = {key: max((s[key] for s in samples), default=baseline[key]) - baseline[key]
                for key in ['working_set', 'private_commit']}
        result['rows'].append({'id': row['id'], 'seconds': row['cycle_seconds_including_unload'],
            'accepted_outcome': row['result'].get('proposal', {}).get('outcome'),
            'peak_incremental_bytes': peak, 'unloaded': row['unloaded'],
            'memory_failure': telemetry['failure'], 'completed_calls': len(row['calls']),
            'cold_load_seconds': row['calls'][0]['response'].get('load_duration', 0)/1e9 if row['calls'] else None,
            'repairs': row['result'].get('repairs'),
            'min_system_available': min((s['available'] for s in samples), default=None)})
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('run', type=Path); args = p.parse_args()
    print(json.dumps(summarize(args.run), indent=2))
