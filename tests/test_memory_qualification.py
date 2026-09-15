"""Deterministic harness safety tests; no inference or vault writes."""
import copy
import json
import unittest
from pathlib import Path
import tempfile
import struct
from unittest.mock import patch

from scripts.memory_qualification.build_dev import build
from scripts.memory_qualification.contracts import Proposal, Verification, validate
from scripts.memory_qualification.run import cycle, check_binding, digest, supply_spans, bounded_schema


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.packet = {'turns': [{'id': 't1', 'actor': 'user', 'text': 'I like café.'}], 'notes': []}
        self.proposal = {'outcome': 'propose', 'reason': 'Explicit preference.', 'operations': [{
            'kind': 'remember', 'target': '', 'actor': 'user', 'scope': 'general',
            'markdown': 'The user likes café.', 'relation': 'none',
            'evidence': [{'turn_id': 't1', 'actor': 'user', 'start': 0, 'end': 12, 'quote': 'I like café.'}]}]}

    def test_unicode_traceability(self):
        validate(Proposal.model_validate(self.proposal), self.packet)

    def test_forgery_unknown_target_and_actor_rejected(self):
        for field, value in [('quote', 'I like tea.'), ('actor', 'assistant'), ('end', 99), ('turn_id', 'fake')]:
            proposal = copy.deepcopy(self.proposal)
            proposal['operations'][0]['evidence'][0][field] = value
            with self.assertRaises(ValueError): validate(Proposal.model_validate(proposal), self.packet)
        proposal = copy.deepcopy(self.proposal)
        proposal['operations'][0].update(kind='correct', target='../vault', relation='supersedes')
        with self.assertRaises(ValueError): validate(Proposal.model_validate(proposal), self.packet)

    def test_extra_policy_fields_rejected(self):
        self.proposal['verified'] = True
        with self.assertRaises(ValueError): Proposal.model_validate(self.proposal)

    def test_duplicate_operations_and_abstention_with_edits_rejected(self):
        self.proposal['operations'] *= 2
        with self.assertRaises(ValueError): validate(Proposal.model_validate(self.proposal), self.packet)
        self.proposal['operations'] = self.proposal['operations'][:1]
        self.proposal['outcome'] = 'abstain'
        with self.assertRaises(ValueError): validate(Proposal.model_validate(self.proposal), self.packet)

    def test_appropriate_abstention_still_gets_separate_verification(self):
        responses = iter(['{"outcome":"abstain","operations":[],"reason":"Filler"}',
                          '{"verdict":"approve","reason":"No durable content"}'])
        result = cycle(self.packet, lambda *a: next(responses))
        self.assertEqual(result['status'], 'accepted_shadow')
        self.assertEqual(result['proposal']['operations'], [])

    def test_second_revise_defers_without_unbounded_retry(self):
        responses = iter([json.dumps(self.proposal), '{"verdict":"revise","reason":"scope"}'] * 2)
        result = cycle(self.packet, lambda *a: next(responses))
        self.assertEqual(result['status'], 'deferred')
        self.assertEqual(len(result['attempts']), 2)

    def test_schema_limits(self):
        self.proposal['operations'] *= 5
        with self.assertRaises(ValueError): Proposal.model_validate(self.proposal)

    def test_fresh_verifier_and_bounded_repair(self):
        calls = []
        responses = iter([json.dumps(self.proposal), '{"verdict":"revise","reason":"Check scope"}',
                          json.dumps(self.proposal), '{"verdict":"approve","reason":"Supported"}'])
        def generate(system, payload, schema):
            calls.append((payload, schema)); return next(responses)
        result = cycle(self.packet, generate)
        self.assertEqual(result['repairs'], 1)
        self.assertEqual(result['status'], 'accepted_shadow')
        self.assertEqual([c[1] for c in calls], [Proposal, Verification, Proposal, Verification])
        self.assertEqual(set(calls[1][0]), {'packet', 'proposal'})
        self.assertNotIn('rubric', json.dumps(calls[1][0]))

    def test_invalid_proposal_never_reaches_verifier(self):
        calls = []
        def generate(system, payload, schema):
            calls.append(schema); return '{}'
        self.assertEqual(cycle(self.packet, generate)['status'], 'deferred')
        self.assertEqual(calls, [Proposal, Proposal])

    def test_reject_not_accepted(self):
        responses = iter([json.dumps(self.proposal), '{"verdict":"reject","reason":"Unsupported"}'])
        self.assertEqual(cycle(self.packet, lambda *a: next(responses))['status'], 'reject')

    def test_dataset_unique_and_labeled(self):
        cases = build()
        self.assertEqual(len(cases), 30)
        self.assertEqual(len({c['id'] for c in cases}), 30)
        self.assertTrue(all(c['rubric']['forbidden'] and c['split'] == 'development' for c in cases))

    def test_import_binding_checks_content_not_original_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'download.gguf'; source.write_bytes(b'fixture weights')
            blob = Path(tmp) / 'sha256-blob'; blob.write_bytes(source.read_bytes())
            show = {'modelfile': f'FROM {blob}\n'}
            check_binding(show, source, digest(source))
            blob.write_bytes(b'different weights')
            with self.assertRaises(ValueError): check_binding(show, source, digest(source))

    def test_supplied_spans_are_unicode_exact_and_do_not_mutate_sources(self):
        packet = supply_spans(self.packet)
        evidence = packet['turns'][0]['available_evidence']
        self.assertEqual(evidence['end'], len('I like café.'))
        self.assertEqual(evidence['quote'], self.packet['turns'][0]['text'])
        self.assertNotIn('available_evidence', self.packet['turns'][0])
        self.assertNotIn('rubric', packet)

    def test_pure_baseline_preserves_actor(self):
        from scripts.memory_qualification.baseline import load_extractor
        Turn, Extractor, _ = load_extractor()
        result = Extractor().extract((Turn(user='', assistant='I prefer local models for Novi.'),))
        self.assertTrue(result.claims)
        self.assertTrue(all(c.speaker == 'assistant' for c in result.claims))

    def test_verifier_challenges_are_traceable_despite_semantic_defects(self):
        cases = json.loads(Path('tests/fixtures/memory_curation/verifier_challenges.json').read_text())
        for case in cases:
            validate(Proposal.model_validate(case['proposal']), case['packet'])

    def test_owned_server_memory_is_not_subtracted_from_budget(self):
        from scripts.memory_qualification.windows_metrics import Monitor
        idle = {'working_set': 100, 'private_commit': 200, 'cpu_seconds': 3,
                'pids': [42], 'unreadable_pids': []}
        with patch('scripts.memory_qualification.windows_metrics.tree_metrics', return_value=dict(idle)):
            monitor = Monitor(42, 1000, 1000, 100, Path('unused-stop'))
        self.assertEqual(monitor.baseline['working_set'], 0)
        self.assertEqual(monitor.baseline['private_commit'], 0)
        self.assertEqual(monitor.idle_server, idle)
        self.assertEqual(monitor.baseline['cpu_seconds'], 3)

    def test_bounded_schema_limits_ids_but_does_not_supply_memory_text(self):
        schema = bounded_schema(Proposal, supply_spans(self.packet))
        op = schema['$defs']['Operation']['properties']
        self.assertEqual(op['target']['enum'], [''])
        self.assertEqual(op['kind']['enum'], ['remember'])
        self.assertNotIn('enum', op['markdown'])
        self.assertEqual(schema['$defs']['Evidence']['properties']['end']['enum'], [12])
        self.assertNotIn('enum', Proposal.model_json_schema()['$defs']['Operation']['properties']['target'])

    def test_report_separates_approved_abstention_from_memory_proposals(self):
        from scripts.memory_qualification.report import summarize
        row = {'kind': 'case', 'id': 'example',
               'result': {'status': 'accepted_shadow', 'proposal': {'outcome': 'abstain'}},
               'telemetry': {'samples': [], 'baseline': {'working_set': 0, 'private_commit': 0},
                             'failure': None},
               'cycle_seconds_including_unload': 1, 'unloaded': True, 'calls': []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run.jsonl'
            path.write_text(json.dumps(row), encoding='utf-8')
            report = summarize(path)
        self.assertEqual(report['accepted_outcomes'], {'abstain': 1})
        self.assertIsNone(report['quality_metrics'])
        self.assertEqual(report['rows'][0]['accepted_outcome'], 'abstain')

    def test_explicit_task_keeps_evidence_as_data_and_selects_pass(self):
        from scripts.memory_qualification.run import user_content
        payload = {'packet': self.packet, 'repair': None}
        self.assertEqual(json.loads(user_content(payload)), payload)
        construction = user_content(payload, True)
        self.assertTrue(construction.startswith('Construct a memory proposal'))
        self.assertTrue(construction.endswith(json.dumps(payload, ensure_ascii=False)))
        review = user_content({'packet': self.packet, 'proposal': {}}, True)
        self.assertTrue(review.startswith('Review the supplied proposal'))
        self.assertNotIn('rubric', construction)

    def test_gguf_reorder_is_allowed_but_weight_change_is_not(self):
        from scripts.memory_qualification.gguf import equivalent
        def fixture(keys, payload=b'weights'):
            data = b'GGUF' + struct.pack('<IQQ', 3, 0, len(keys))
            for key in keys:
                data += struct.pack('<Q', len(key)) + key + struct.pack('<II', 4, 7)
            return data + b'\0' * (-len(data) % 32) + payload
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.gguf', Path(tmp) / 'b.gguf'
            a.write_bytes(fixture([b'alpha', b'beta']))
            b.write_bytes(fixture([b'beta', b'alpha']))
            self.assertEqual(equivalent(a, b)['transformation'], 'metadata serialization order only')
            b.write_bytes(fixture([b'beta', b'alpha'], b'changed'))
            with self.assertRaises(ValueError): equivalent(a, b)


if __name__ == '__main__': unittest.main()
