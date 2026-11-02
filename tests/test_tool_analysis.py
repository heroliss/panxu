"""The tool list shares the persistent task loop without file/action scope."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.cleanup_tools import REGISTRY
from diskpilot.llm import ModelAssistant
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness
from tests.prompt_fixture import parse_host_data


def observed(size=100, *, status='observed'):
    return dict(at='2026-11-03T00:00:00+00:00', evidence=dict(status=status,
        complete=status == 'observed', observed_bytes=size, observed_files=2,
        roots_found=1, coverage_paths=[r'C:\PrivateFixture\cache'], coverage_complete=True),
        presentation=dict(kind='cache', status=status, complete=status == 'observed',
            metrics=[dict(key='observed_bytes', value=size), dict(key='observed_files', value=2)],
            items=[], warnings=[]))


def response(name, arguments):
    return dict(choices=[dict(message=dict(role='assistant', content=None, tool_calls=[dict(
        id='call-' + name, type='function', function=dict(name=name,
        arguments=json.dumps(arguments, ensure_ascii=False)))]))])


class CleanupToolAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = SimulationState(self.root / 'state')
        self.addCleanup(self.state.close)
        self.requests, self.steps = [], []

        def transport(url, payload, headers, timeout):
            self.requests.append(copy.deepcopy(payload))
            if not self.steps:
                raise AssertionError('unexpected model request')
            step = self.steps.pop(0)
            return step(payload) if callable(step) else step

        self.assistant = ModelAssistant(self.root / 'state', transport=transport)
        self.assistant.save_settings(dict(base_url='http://127.0.0.1:12345/v1', model='fixture-model'))
        self.harness = TaskHarness(self.state, self.assistant)
        self.tick = 100.0
        self.harness.tool_assessments.clock = lambda: self.tick
        self.probe = patch.object(self.harness.cleanup_tools, 'inspect', return_value=observed()).start()
        self.addCleanup(patch.stopall)

    def checked(self, tool_id='gpu_cache', parameters=None):
        return self.harness.tool_assessments.inspect(tool_id, parameters)

    @staticmethod
    def ref(row):
        return dict(row['tool_ref'], inspection_id=row['inspection_id'])

    def create(self, *rows, **extra):
        return self.harness.create_tool_analysis(dict(items=[self.ref(row) for row in rows], **extra))

    def tool(self, task, name, args, provenance=None):
        return self.harness.tool(task['id'], name, args, cancel=threading.Event(), provenance=provenance)

    @staticmethod
    def advice(row, rating=4):
        return dict(id=row['id'], conclusion='recommend', cleanup_rating=rating,
                    impact_rating=2, purpose='可重建缓存。', reason='当前有已确认的缓存。', impact='下次使用会重新生成。')

    @staticmethod
    def provenance():
        return dict(source='llm', provider='custom', model='fixture-model', protocol='chat_completions',
                    generated_at='2026-11-03T00:00:00+00:00')

    def test_exact_frozen_scope_compact_context_and_no_catalog_publication(self):
        row = self.checked()
        before = copy.deepcopy(self.state.view()['items'])
        self.probe.reset_mock()
        with patch.object(self.state, 'snapshot', side_effect=AssertionError('catalog scan')):
            task = self.create(row)
            private = self.harness._get(task['id'])
            context = self.harness._context(private)
            listing = self.tool(task, 'list_items', {})
            detail = self.tool(task, 'inspect_items', dict(ids=[row['id']]))['items'][0]
        self.assertEqual(task['scope_kind'], 'cleanup_tools')
        self.assertEqual(context['scopes'], [])
        self.assertEqual(context['selected_tools'], [dict(id=row['id'], name=row['title'])])
        self.assertNotIn('diagnostic', listing['items'][0])
        self.assertEqual(detail['diagnostic']['benefit']['observed_bytes'], 100)
        self.assertNotIn('PrivateFixture', json.dumps(detail))
        self.assertNotIn('coverage_keys', json.dumps(detail))
        self.assertNotIn('file_keys', json.dumps(detail))
        self.assertNotIn(row['signature'], json.dumps(detail))
        self.assertEqual(self.state.view()['items'], before)
        self.probe.assert_not_called()

    def test_model_projection_omits_internal_overlap_and_file_identity_tokens(self):
        sample = observed()
        sample['evidence'].update(observed_file_keys=['file:private-inode-proof'],
            file_keys=['private-file-token'], coverage_keys=['path:private-safety-key'],
            internal_signature='host-only-signature')
        self.probe.return_value = sample
        row = self.checked()
        task = self.create(row)
        text = json.dumps(self.tool(task, 'inspect_items', dict(ids=[row['id']])), ensure_ascii=False)
        for unwanted in ('file_keys', 'coverage_keys', 'private-inode-proof', 'private-file-token',
                         'private-safety-key', 'host-only-signature', row['inspection_id'], row['signature']):
            self.assertNotIn(unwanted, text)

    def test_runtime_and_model_tools_both_reject_expansion_or_execution(self):
        row, other = self.checked(), self.checked('thumbnail_cache')
        task = self.create(row)
        specs = self.harness._task_tool_specs(self.harness._get(task['id']))
        names = {spec['function']['name'] for spec in specs}
        self.assertNotIn('propose_plan', names)
        self.assertNotIn('inspect_cleanup_tool', names)
        self.assertNotIn('survey_scope', names)
        self.assertNotIn('collect_apps', names)
        for name, args in [('propose_plan', {}), ('run_cleanup_diagnostics', {}),
                           ('inspect_cleanup_tool', dict(tool_id='gpu_cache')), ('collect_apps', dict(section='background'))]:
            with self.assertRaises(AppError) as caught:
                self.tool(task, name, args)
            self.assertEqual(caught.exception.code, 'outside_scope')
        with self.assertRaises(AppError):
            self.tool(task, 'inspect_items', dict(ids=[other['id']]))
        with self.assertRaises(AppError):
            self.tool(task, 'record_advice', dict(items=[dict(self.advice(row), specialist=dict(tool_id='hibernation'))]))
        self.assertEqual(self.state.history(), [])

    def test_analysis_requires_exact_current_inspection_and_accepts_unknown(self):
        row = self.checked()
        for wrong in [dict(tool_id='gpu_cache'), dict(self.ref(row), inspection_id='made-up')]:
            with self.assertRaises(AppError):
                self.harness.create_tool_analysis(dict(items=[wrong]))
        unknown = self.checked('pagefile')
        task = self.create(unknown)
        self.assertEqual(task['analysis']['target_count'], 1)
        self.assertTrue(self.tool(task, 'inspect_items', dict(ids=[unknown['id']]))['items'][0]['requires_parameters'])
        self.tick += 61
        with self.assertRaises(AppError) as caught:
            self.create(row)
        self.assertEqual(caught.exception.code, 'tool_inspection_changed')

    def test_all_48_tools_share_one_task_without_48_model_requests(self):
        rows = [self.checked(tool_id) for tool_id in REGISTRY]
        self.probe.reset_mock()
        task = self.create(*rows)
        self.assertEqual(task['analysis']['target_count'], 48)
        self.assertEqual(len(task['cleanup_tool_targets']), 48)
        self.assertEqual(self.tool(task, 'list_items', dict(limit=40))['total'], 48)
        self.probe.assert_not_called()
        self.assertEqual(self.requests, [])

    def test_ratings_are_bound_persistent_and_stale_results_remain_readable(self):
        row = self.checked()
        task = self.create(row)
        self.tool(task, 'inspect_items', dict(ids=[row['id']]))
        self.tool(task, 'record_advice', dict(items=[self.advice(row)]), self.provenance())
        current = self.harness.tool_assessments.catalog()['items']
        projected = next(item for item in current if item['id'] == row['id'])
        self.assertEqual(projected['recommendation']['source'], 'llm')
        self.assertEqual(projected['recommendation']['rating'], 4)
        with self.assertRaises(AppError) as caught:
            self.create(projected)
        self.assertEqual(caught.exception.code, 'nothing_to_analyze')
        self.assertEqual(self.create(projected, reanalyze=True)['analysis']['target_count'], 1)
        forced = self.create(projected, skip_analyzed=False)
        self.assertEqual(self.tool(forced, 'list_items', {})['total'], 1)
        self.tick += 61
        stale = self.harness.public(task['id'])
        self.assertTrue(stale['advice'][0]['stale'])
        self.assertTrue(stale['cleanup_tool_targets'][0]['stale'])
        self.assertEqual(stale['advice'][0]['cleanup_rating'], 4)
        self.assertEqual(stale['cleanup_tool_targets'][0]['diagnostic']['benefit']['observed_bytes'], 100)
        restored = TaskHarness(self.state, self.assistant)
        self.assertEqual(restored.public(task['id'])['advice'][0]['reason'], '当前有已确认的缓存。')
        self.assertTrue(restored.public(task['id'])['advice'][0]['stale'])

    def test_changed_evidence_does_not_inherit_old_ai_rating(self):
        row = self.checked()
        task = self.create(row)
        self.tool(task, 'inspect_items', dict(ids=[row['id']]))
        self.tool(task, 'record_advice', dict(items=[self.advice(row)]), self.provenance())
        self.probe.return_value = observed(300)
        changed = self.harness.tool_assessments.inspect('gpu_cache', refresh=True)
        self.assertNotEqual(row['signature'], changed['signature'])
        self.assertEqual(changed['recommendation']['source'], 'builtin')
        self.assertTrue(self.harness.public(task['id'])['advice'][0]['stale'])
        self.assertEqual(self.create(changed)['analysis']['target_count'], 1)

    def test_older_late_response_cannot_replace_newer_intent(self):
        row = self.checked()
        old, new = self.create(row), self.create(row)
        self.tool(old, 'inspect_items', dict(ids=[row['id']]))
        self.tool(new, 'inspect_items', dict(ids=[row['id']]))
        self.tool(new, 'record_advice', dict(items=[self.advice(row, 5)]), self.provenance())
        self.tool(old, 'record_advice', dict(items=[self.advice(row, 4)]), self.provenance())
        current = self.harness.tool_assessments.inspect('gpu_cache')
        self.assertEqual(current['recommendation']['rating'], 5)
        self.assertEqual(current['recommendation']['task_id'], new['id'])
        self.assertEqual(self.harness.public(old['id'])['advice'][0]['cleanup_rating'], 4)

    def test_scoring_requires_saved_diagnostic_read_not_just_tool_name(self):
        row = self.checked()
        task = self.create(row)
        with self.assertRaises(AppError) as caught:
            self.tool(task, 'record_advice', dict(items=[self.advice(row)]), self.provenance())
        self.assertEqual(caught.exception.code, 'inspection_required')
        self.tool(task, 'inspect_items', dict(ids=[row['id']]))
        self.assertEqual(self.tool(task, 'record_advice', dict(items=[self.advice(row)]), self.provenance())['recorded_count'], 1)

    def test_existing_loop_fact_readback_scoring_and_completion(self):
        rows = [self.checked(), self.checked('thumbnail_cache')]
        task = self.create(*rows)
        self.steps = [response('list_items', {}), response('inspect_items', dict(ids=[row['id'] for row in rows])),
                      response('record_advice', dict(items=[self.advice(row) for row in rows])),
                      response('present_findings', dict(summary='两项已检查缓存可按需清理。', needs_input=False))]
        self.harness.claim(task['id'])
        result = self.harness.investigate(task['id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['analysis']['advised_count'], 2)
        self.assertIsNone(result['plan'])
        self.assertEqual(len(self.requests), 4)
        self.assertTrue(all(row['provenance']['source'] == 'llm' for row in result['advice']))
        initial = parse_host_data(self.requests[0]['messages'][1]['content'])
        self.assertEqual(len(initial['selected_tools']), 2)
        self.assertNotIn('tool_targets', initial)
        self.assertNotIn('PrivateFixture', json.dumps(self.requests))
        facts = self.tool(task, 'read_facts', dict(limit=20))['items']
        inspected = next(fact for fact in facts if fact['tool'] == 'inspect_items')
        self.assertEqual(inspected['result']['items'][0]['diagnostic']['benefit']['observed_bytes'], 100)
        self.assertEqual(self.state.history(), [])
        self.probe.assert_called()

    def test_existing_question_feedback_and_tool_receipt_return_to_same_task(self):
        row = self.checked()
        task = self.create(row)
        self.tool(task, 'ask_user', dict(question='近期还使用吗？', options=[dict(id='yes', label='使用'), dict(id='no', label='不使用')]))
        question = self.harness.public(task['id'])['question']
        self.harness.answer(task['id'], dict(option_id='no', question_id=question['id'], version=question['version']))
        source = self.harness.validate_specialist_source(dict(task_id=task['id']))
        exact_source = self.harness.validate_specialist_source(dict(task_id=task['id'], item_id=row['id'], signature=row['signature']))
        self.assertEqual(exact_source['item_id'], row['id'])
        plan = self.harness.cleanup_tools.prepare('gpu_cache', source=source)
        self.harness.cleanup_tools.execute(plan['id'], plan['version'])
        receipt = self.harness.record_specialist_result(plan)
        self.assertTrue(receipt['simulation_only'])
        self.assertFalse(receipt['windows_changed'])
        self.assertEqual(receipt['actual_reclaimed_bytes'], 0)
        public = self.harness.public(task['id'])
        self.assertEqual(public['last_specialist_result']['plan_id'], plan['id'])
        self.assertTrue(public['feedback'])


if __name__ == '__main__':
    unittest.main()
