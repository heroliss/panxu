"""Continuous analysis has durable whole-session bounds, not six-call tasks."""
import copy
import threading
import unittest
from unittest.mock import patch

from diskpilot import analysis_session
from diskpilot.simulation import AppError
from diskpilot.tasks import TaskHarness
from diskpilot.windows import _item
from tests import test_investigation_completion as fixtures

call, response = fixtures.call, fixtures.response


class AnalysisSessionUnitTests(unittest.TestCase):
    def test_timestamp_and_fact_identity_changes_are_not_new_progress(self):
        session = analysis_session.begin({}, analysis_session.DEFAULTS)
        result = {'items': [{'id': 'startup-a', 'state': 'Running'}], 'fact_id': 'fact-a', 'at': 'old'}
        self.assertTrue(analysis_session.observe(session, 'list_items', {}, result))
        result.update(fact_id='fact-b', at='later')
        self.assertFalse(analysis_session.observe(session, 'list_items', {}, result))
        result['items'][0]['state'] = 'Stopped'
        self.assertTrue(analysis_session.observe(session, 'list_items', {}, result))

    def test_progress_means_content_and_not_checkpoint_text(self):
        session = analysis_session.begin({}, analysis_session.DEFAULTS)
        for n in range(8):
            self.assertFalse(analysis_session.observe(session, 'checkpoint_task', {}, {'summary': str(n)}))
        args = dict(items=[dict(id='startup-a', cleanup_rating=4, reason='unused')])
        self.assertTrue(analysis_session.observe(session, 'record_advice', args, {'recorded_count': 1}))
        self.assertFalse(analysis_session.observe(session, 'record_advice', args, {'recorded_count': 1}))

    def test_unknown_usage_is_never_fabricated_and_partial_reports_are_explicit(self):
        session = analysis_session.begin({}, analysis_session.DEFAULTS)
        session['used']['model_requests'] = 2
        analysis_session.account_tokens(session, None)
        self.assertIsNone(session['used']['input_tokens'])
        analysis_session.account_tokens(session, dict(prompt_tokens=10))
        public = analysis_session.public(session)
        self.assertEqual(public['used']['input_tokens'], 10)
        self.assertIsNone(public['used']['output_tokens'])
        self.assertFalse(public['token_usage_complete'])
        self.assertIsNone(public['cost_estimate'])
        self.assertNotIn('fingerprints', public)

    def test_same_saved_advice_with_new_fact_references_is_not_new_progress(self):
        session = analysis_session.begin({}, analysis_session.DEFAULTS)
        arguments = dict(items=[dict(id='startup-a', cleanup_rating=4, reason='unused', evidence_ids=['fact-a'])])
        self.assertTrue(analysis_session.observe(session, 'record_advice', arguments, {'recorded_count': 1}))
        arguments['items'][0]['evidence_ids'] = ['fact-b']
        self.assertFalse(analysis_session.observe(session, 'record_advice', arguments, {'recorded_count': 1}))

    def test_pause_resume_retains_allowance_but_explicit_exhausted_continue_renews(self):
        task = {}
        session = analysis_session.begin(task, analysis_session.DEFAULTS)
        session['used']['model_requests'] = 5
        session['status'] = 'paused'
        resumed = analysis_session.begin(task, analysis_session.DEFAULTS)
        self.assertEqual(resumed['id'], session['id'])
        self.assertEqual(resumed['used']['model_requests'], 5)
        resumed['status'] = 'budget_exhausted'
        fresh = analysis_session.begin(task, analysis_session.DEFAULTS)
        self.assertNotEqual(fresh['id'], session['id'])
        self.assertEqual(fresh['used']['model_requests'], 0)
        self.assertEqual(task['analysis_session_history'][-1]['used']['model_requests'], 5)


class ContinuousAnalysisHarnessTests(unittest.TestCase):
    setUp = fixtures.InvestigationCompletionTests.setUp
    run_turn = fixtures.InvestigationCompletionTests.run_turn
    ratings = fixtures.InvestigationCompletionTests.ratings
    findings = fixtures.InvestigationCompletionTests.findings

    def test_twelve_items_continue_past_two_internal_checkpoints_without_user_roundtrip(self):
        self.rows.extend([_item('startup', f'owned-{index}', f'Owned app {index}', 'Owned fixture',
            '开机启动', 'disable_startup', {'synthetic': True}, reason='Disposable fixture') for index in range(8, 12)])
        for row in self.rows:
            row['details']['synthetic'] = False
        self.harness.tool(self.task['id'], 'collect_apps', {'section': 'background'}, cancel=threading.Event())
        self.task = self.harness.create_analysis({'section': 'background', 'ids': [row['id'] for row in self.rows]})
        self.queue = [call('list_items', {'kind': 'startup'}, 'list')]
        for index, row in enumerate(self.rows):
            advice = self.ratings()['items'][0]
            advice = dict(advice, id=row['id'])
            self.queue.append(call('record_advice', {'items': [advice]}, f'advice-{index}'))
        self.queue.append(call('present_findings', self.findings(), 'finish'))
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['advice_total'], 12)
        self.assertEqual(result['analysis_progress']['remaining_count'], 0)
        self.assertEqual(len(self.requests), 14)
        self.assertEqual(result['analysis_session']['used']['model_requests'], 14)
        self.assertEqual(result['analysis_session']['checkpoints'], 2)
        self.assertEqual(result['turn'], 1)
        self.assertEqual(result['analysis_session']['used']['input_tokens'], 140)
        self.assertTrue(result['analysis_session']['token_usage_complete'])
        self.assertFalse(self.state.history())
        self.assertEqual(self.queue, [])

    def test_repeated_reads_stop_on_no_progress_instead_of_consuming_whole_allowance(self):
        self.queue = [call('list_items', {}, str(n)) for n in range(12)]
        result = self.run_turn()
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(result['analysis_session']['stop_reason'], 'no_progress')
        self.assertLess(len(self.requests), 12)
        self.assertEqual(result['analysis_session']['meaningful_results'], 1)
        self.assertEqual(result['advice_total'], 0)

    def test_consecutive_tool_failures_stop_without_repeating_forever(self):
        self.queue = [call('not_a_tool', {}, str(n)) for n in range(10)]
        result = self.run_turn()
        self.assertEqual(result['analysis_session']['stop_reason'], 'repeated_failures')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(result['usage']['tool_failures'], 3)
        self.assertFalse(self.state.history())

    def test_overall_request_limit_and_restart_do_not_become_automatic_renewals(self):
        self.harness.agent_budget.save_session({'model_requests': 6})
        self.queue = [call('list_items', {}, str(n)) for n in range(6)]
        first = self.run_turn()
        self.assertEqual(first['analysis_session']['stop_reason'], 'session_requests')
        self.assertEqual(len(self.requests), 6)
        reloaded = TaskHarness(self.state, self.assistant)
        restored = reloaded.public(self.task['id'])
        self.assertEqual(restored['analysis_session']['used']['model_requests'], 6)
        self.assertEqual(restored['analysis_session']['id'], first['analysis_session']['id'])

    def test_manual_pause_and_resume_share_consumed_resources(self):
        cancel = threading.Event()
        def paused(_payload):
            cancel.set()
            return call('list_items', {}, 'cancelled')
        self.queue = [paused]
        first = self.run_turn(cancel)
        self.assertEqual(first['status'], 'paused')
        self.queue = [call('list_items', {}, 'new-read'), call('present_findings', self.findings(), 'finish')]
        second = self.run_turn()
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(second['analysis_session']['id'], first['analysis_session']['id'])
        self.assertEqual(second['analysis_session']['used']['model_requests'], 3)

    def test_reported_token_ceiling_preserves_usage_and_stops_tools(self):
        self.harness.agent_budget.save_session({'total_tokens': 1000})
        large = call('list_items', {}, 'read')
        large['usage'] = dict(prompt_tokens=800, completion_tokens=300)
        self.queue = [large]
        result = self.run_turn()
        self.assertEqual(result['analysis_session']['stop_reason'], 'session_tokens')
        self.assertEqual(result['analysis_session']['used']['input_tokens'], 800)
        self.assertEqual(result['analysis_session']['used']['tool_calls'], 0)

    def test_duration_bound_counts_only_active_work_and_stops_before_next_request(self):
        self.harness.agent_budget.save_session({'elapsed_minutes': 1})
        self.queue = [call('list_items', {}, 'read')]
        original = self.assistant.agent_turn
        clock = [0.0]
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] += 61.0
            return result
        with patch('diskpilot.tasks.time.monotonic', side_effect=lambda: clock[0]), patch.object(self.assistant, 'agent_turn', side_effect=delayed):
            result = self.run_turn()
        self.assertEqual(result['analysis_session']['stop_reason'], 'session_time')
        self.assertEqual(len(self.requests), 1)
        self.assertGreaterEqual(result['analysis_session']['used']['elapsed_ms'], 60_000)

    def test_session_settings_are_persistent_and_invalid_changes_are_atomic(self):
        original = self.harness.agent_budget.get_session()
        self.harness.agent_budget.save_session({'elapsed_minutes': 12})
        self.assertEqual(TaskHarness(self.state, self.assistant).agent_budget.get_session()['elapsed_minutes'], 12)
        for values in ({'model_requests': True}, {'total_tokens': -1}, {'unknown': 2}, {'elapsed_minutes': 0}, {}):
            with self.assertRaises(AppError):
                self.harness.agent_budget.save_session(values)
        self.assertEqual(self.harness.agent_budget.get_session(), dict(original, elapsed_minutes=12))


if __name__ == '__main__':
    unittest.main()
