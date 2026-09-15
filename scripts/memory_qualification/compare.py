"""Compare run accounting and export evidence for human quality review.

Never derives semantic accuracy from the candidate model's own approval.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics


def percentile(values, fraction):
    if not values: return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = int(index); high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def compare(path):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    manifest = rows[0]; cases = rows[1:]
    completed = [r for r in cases if r.get('kind') == 'case']
    times = [r['cycle_seconds_including_unload'] for r in completed]
    peaks = {k: [] for k in ['working_set', 'private_commit']}
    loads = []; validations = Counter(); cpu = []; reasons = Counter()
    calls = 0; verified_cases = 0; accepted_operations = 0
    two_pass_times = []
    for row in completed:
        t = row['telemetry']; samples = t['samples']
        for key in peaks:
            peaks[key].append(max((s[key] for s in samples), default=t['baseline'][key]) - t['baseline'][key])
        cpu.append(max((s['cpu_seconds'] for s in samples), default=t['baseline']['cpu_seconds']) - t['baseline']['cpu_seconds'])
        if row['calls']:
            loads.append(row['calls'][0]['response'].get('load_duration', 0) / 1e9)
        calls += len(row['calls'])
        attempts = row['result'].get('attempts', [])
        verified_cases += any('verification_raw' in a for a in attempts)
        if not manifest['settings'].get('verification_only') and any('verification_raw' in a for a in attempts):
            two_pass_times.append(row['cycle_seconds_including_unload'])
        for attempt in attempts:
            if attempt.get('validation_error'):
                validations[attempt['validation_error'].split('\n')[0]] += 1
        if row['result']['status'] == 'accepted_shadow':
            accepted_operations += len(row['result']['proposal']['operations'])
        if t['failure']: reasons[t['failure']] += 1
    return {'run': str(path), 'model': manifest['model']['name'],
        'sha256': manifest['sha256'], 'attempted_cases': len(completed),
        'statuses': dict(Counter(r['result']['status'] for r in completed)),
        'completed_calls': calls, 'cases_reaching_verifier': verified_cases,
        'accepted_shadow_operations': accepted_operations,
        'repairs': sum(r['result'].get('repairs', 0) for r in completed),
        'validation_errors': dict(validations), 'resource_failures': dict(reasons),
        'all_unloaded': all(r['unloaded'] for r in completed),
        'cycle_p50_seconds': statistics.median(times) if times else None,
        'cycle_p95_seconds': percentile(times, .95),
        'completed_two_pass_cases': len(two_pass_times),
        'two_pass_cycle_p50_seconds': statistics.median(two_pass_times) if two_pass_times else None,
        'two_pass_cycle_p95_seconds': percentile(two_pass_times, .95),
        'cold_load_p50_seconds': statistics.median(loads) if loads else None,
        'peak_incremental_bytes': {k: max(v, default=None) for k,v in peaks.items()},
        'cpu_seconds_sampled_total': sum(cpu),
        'quality_metrics': None, 'foreground_slowdown': None,
        'qualification': 'not established; requires semantic grading and all resource gates'}


def review_export(path, dataset):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    cases = {c['id']: c for c in json.loads(dataset.read_text(encoding='utf-8'))}
    review = []
    for row in rows[1:]:
        case = cases[row['id']]
        review.append({'id': row['id'], 'packet': case['packet'], 'rubric': case['rubric'],
            'result': row['result'], 'human_reviewed': False,
            'annotation': {'supported_accepted_claims': None, 'unsupported_accepted_claims': None,
                'worthwhile_gold_claims': None, 'recovered_gold_claims': None,
                'correct_accepted_links': None, 'incorrect_accepted_links': None,
                'correct_corrections': None, 'incorrect_corrections': None,
                'critical_errors': None, 'comment': ''}})
    return review


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('runs', nargs='+', type=Path)
    p.add_argument('--review-dir', type=Path)
    args = p.parse_args()
    print(json.dumps([compare(path) for path in args.runs], indent=2))
    if args.review_dir:
        args.review_dir.mkdir(parents=True, exist_ok=True)
        for path in args.runs:
            out = args.review_dir / (path.stem + '-review.json')
            with out.open('x', encoding='utf-8') as f:
                json.dump(review_export(path, Path('tests/fixtures/memory_curation/dev30.json')),
                          f, ensure_ascii=False, indent=2)
