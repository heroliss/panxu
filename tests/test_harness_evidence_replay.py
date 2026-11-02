"""Replay evidence loss with owned metadata and an offline protocol transport."""
import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant
from diskpilot.simulation import SimulationState
from diskpilot.streaming import ResponseBudgetExceeded
from diskpilot.tasks import TaskHarness
from diskpilot.windows import _item
from tests.prompt_fixture import parse_host_data


def call(name, arguments, ident):
    return {'choices': [{'message': {'role': 'assistant', 'content': None,
        'tool_calls': [{'id': ident, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}, 'finish_reason': 'tool_calls'}]}


class EvidenceReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='panxu-evidence-replay-')
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name)
        self.state = SimulationState(self.profile)
        self.addCleanup(self.state.close)
        self.queue, self.requests = [], []
        self.transport_failure = None

        def transport(_url, payload, _headers, _timeout):
            self.requests.append(copy.deepcopy(payload))
            pending = set()
            for message in payload['messages']:
                if message['role'] == 'assistant':
                    pending.update(entry['id'] for entry in message.get('tool_calls', []))
                elif message['role'] == 'tool':
                    self.assertIn(message['tool_call_id'], pending)
                    pending.remove(message['tool_call_id'])
            self.assertFalse(pending, 'Only fully paired turns may be compacted or sent.')
            if not self.queue:
                raise AssertionError('The provider queue is finite; no hidden retries or cloud calls are allowed.')
            step = self.queue.pop(0)
            try:
                return step(payload) if callable(step) else step
            except ResponseBudgetExceeded:
                raise
            except Exception as error:
                self.transport_failure = error
                raise

        self.assistant = ModelAssistant(self.profile, transport=transport)
        self.assistant._windows = False
        self.assistant.save_settings(dict(base_url='http://127.0.0.1:1/v1', model='owned-model-a'))
        self.harness = TaskHarness(self.state, self.assistant)
        self.rows = [_item('startup', f'owned-app-{index}', f'Owned startup {index}', 'Owned fixture',
            '开机启动', 'disable_startup', {'synthetic': True},
            reason='This is observed registration metadata; it does not establish usage or dependencies. ' * 8)
            for index in range(35)]
        for row in self.rows:
            row['details']['observed_at'] = '2026-10-01T00:00:00+00:00'
        self.software = _item('software', 'owned-background-reference', 'Owned software reference',
            'Owned fixture', '软件', 'uninstall', {'synthetic': True}, reason='Background context only.')
        self.inventory = patch('diskpilot.tasks.inventory', return_value=dict(items=[*self.rows, self.software],
            warnings=['Owned metadata fixture; no Windows inventory is read.'], summary={'read_only': True}))
        self.inventory_mock = self.inventory.start()
        self.addCleanup(self.inventory.stop)
        foreign = self.harness.create(preset='background')
        self.harness._record_fact(foreign['id'], 'inspect_items', dict(items=[dict(
            id='other-task-object', name='Foreign task observation must never leak')]))
        self.task = self.harness.create(preset='background')

    def context(self, payload):
        for message in payload['messages']:
            if message['role'] == 'user':
                value = parse_host_data(message.get('content', ''))
                if isinstance(value, dict) and value.get('goal'):
                    return value
        self.fail('The current goal and authorized scope must be present.')

    @staticmethod
    def tools(payload):
        return {entry['function']['name'] for entry in payload['tools']}

    def assert_evidence(self, payload):
        context = self.context(payload)
        working = context.get('working_evidence')
        self.assertTrue(working and working['items'], 'A rebuilt turn must retain concrete observations.')
        rows = [row for fact in working['items'] if isinstance(fact.get('result'), dict)
                for row in fact['result'].get('items', []) if isinstance(row, dict)]
        first = next((row for row in rows if row.get('id') == self.rows[0]['id']), None)
        self.assertIsNotNone(first, 'An opaque fact index cannot replace the evidence needed to rate the target.')
        self.assertEqual(first['name'], 'Owned startup 0')
        self.assertEqual(first['observed_at'], '2026-10-01T00:00:00+00:00')
        source = next(fact for fact in working['items'] if isinstance(fact.get('result'), dict)
                      and any(row.get('id') == self.rows[0]['id'] for row in fact['result'].get('items', []) if isinstance(row, dict)))
        self.assertTrue(source['requires_recheck_for_action'])
        self.assertEqual(source['freshness'], 'historical_observation')
        self.assertIsNotNone(datetime.fromisoformat(source['at']).tzinfo)
        self.assertFalse(source['scope']['root_ids'], 'A background observation must not manufacture filesystem roots.')
        self.assertEqual(source['scope']['task_id'], self.task['id'])
        self.assertNotIn('Foreign task observation must never leak', json.dumps(payload, ensure_ascii=False))
        self.assertFalse({'survey_scope', 'inspect_directory', 'read_scan', 'continue_scan', 'validate_directory', 'compare_files'} & self.tools(payload))
        return context

    def ratings(self, ident=None):
        return {'items': [dict(id=ident or self.rows[0]['id'], conclusion='review', cleanup_rating=3,
            purpose='Observed startup registration', reason='The retained registration evidence does not establish regular use.',
            impact='Disabling startup would prevent automatic readiness after login.') ]}

    @staticmethod
    def findings():
        return dict(summary='One owned startup item was rated from retained evidence; other entries remain unassessed. No system change was made.', needs_input=False)

    def run_turn(self):
        self.harness.claim(self.task['id'])
        try:
            return self.harness.investigate(self.task['id'])
        finally:
            self.harness.release(self.task['id'])

    def assert_finished(self, result, request_count, *, failures=0):
        if self.transport_failure:
            raise self.transport_failure
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['advice_total'], 1)
        self.assertEqual(result['usage']['model_requests'], request_count)
        self.assertLessEqual(result['usage']['model_requests'], 6)
        self.assertLessEqual(result['usage']['tool_calls'], 12)
        self.assertEqual(result['usage']['tool_failures'], failures)
        self.assertEqual(self.inventory_mock.call_count, 1, 'Compaction or recovery must not replay collection.')
        self.assertFalse(self.state.history())
        self.assertIsNone(result['last_result'])
        self.assertFalse(self.queue)
        advice = self.harness.advice_page(self.task['id'])['items']
        self.assertEqual([entry['id'] for entry in advice], [self.rows[0]['id']])

    def test_large_results_read_facts_and_repeated_compaction_end_in_evidence_based_rating(self):
        self.queue = [call('collect_apps', {'section': 'background'}, 'collect')]

        def inspect(payload):
            context = self.assert_evidence(payload)
            self.assertEqual(context['context_state']['reason'], 'compacted')
            return call('inspect_items', {'ids': [row['id'] for row in self.rows[:4]]}, 'inspect')

        def read(payload):
            self.assert_evidence(payload)
            collected = next(row for row in self.harness.facts_page(self.task['id'])['items'] if row['tool'] == 'collect_apps')
            self.read_id = collected['id']
            return call('read_facts', {'ids': [self.read_id], 'limit': 1}, 'read-old-body')

        def read_again(payload):
            context = self.assert_evidence(payload)
            self.assertEqual(context['context_state']['reason'], 'compacted')
            self.assertEqual(context['working_evidence']['items'][0]['fact_id'], self.read_id)
            return call('read_facts', {'ids': [self.read_id], 'limit': 1}, 'read-without-recollect')

        def score(payload):
            self.assert_evidence(payload)
            self.assertIn('read_facts', self.tools(payload), 'Scoring may still need the persisted evidence body.')
            self.assertIn('collect_apps', self.tools(payload), 'Resource hints must not remove normal collection capabilities.')
            return call('record_advice', self.ratings(), 'score')

        def finish(payload):
            self.assert_evidence(payload)
            self.assertTrue({'present_findings', 'ask_user'} <= self.tools(payload))
            return call('present_findings', self.findings(), 'finish')

        self.queue += [inspect, read, read_again, score, finish]
        with patch('diskpilot.tasks.CONTEXT_TARGET_BYTES', 8000):
            result = self.run_turn()
        self.assert_finished(result, 6)
        self.assertFalse(self.harness._action_target(self.harness._get(self.task['id']), self.software))

    def test_connection_switch_hydrates_original_evidence_without_replaying_collect(self):
        def collect(_payload):
            self.assistant.save_settings({'model': 'owned-model-b'})
            return call('collect_apps', {'section': 'background'}, 'collect-before-switch')

        def score(payload):
            context = self.assert_evidence(payload)
            self.assertEqual(payload['model'], 'owned-model-b')
            self.assertEqual(context['context_state']['reason'], 'connection_changed')
            return call('record_advice', self.ratings(), 'score-after-switch')

        self.queue = [collect, score, call('present_findings', self.findings(), 'finish')]
        result = self.run_turn()
        self.assert_finished(result, 3)
        source = self.harness.advice_page(self.task['id'])['items'][0]['provenance']
        self.assertEqual(source['model'], 'owned-model-b')

    def test_output_budget_recovery_rebuilds_from_saved_evidence_and_does_not_recollect(self):
        def overflow(_payload):
            raise ResponseBudgetExceeded('generation_tokens', 3501, 3500)

        def score(payload):
            context = self.assert_evidence(payload)
            self.assertEqual(context['context_state']['reason'], 'output_recovery')
            self.assertIn('read_facts', self.tools(payload))
            self.assertNotIn('collect_apps', self.tools(payload))
            return call('record_advice', self.ratings(), 'score-after-recovery')

        self.queue = [call('collect_apps', {'section': 'background'}, 'collect'), overflow,
                      score, call('present_findings', self.findings(), 'finish')]
        result = self.run_turn()
        self.assert_finished(result, 4)
        self.assertTrue(result['investigation_notice']['recovered'])

    def test_missing_inspection_ids_returns_paired_actionable_error_then_model_repairs(self):
        def repair(payload):
            previous = next(message for message in payload['messages'] if message.get('role') == 'tool'
                            and message.get('tool_call_id') == 'missing-inspection-ids')
            error = parse_host_data(previous['content'])
            self.assertEqual(error['code'], 'invalid_tool_arguments')
            self.assertIn('ids', error['error'])
            self.assertIn('接受', error['error'])
            self.assertIn('不要扩大范围', error['next_step'])
            schema = next(row['function']['parameters'] for row in payload['tools']
                          if row['function']['name'] == 'inspect_items')
            self.assertIn('ids', schema['required'])
            return call('inspect_items', {'ids': [self.rows[0]['id']]}, 'correct-inspection-ids')

        def score(payload):
            previous = next(message for message in payload['messages'] if message.get('role') == 'tool'
                            and message.get('tool_call_id') == 'correct-inspection-ids')
            result = parse_host_data(previous['content'])
            self.assertEqual(result['items'][0]['id'], self.rows[0]['id'])
            self.assertEqual(result['items'][0]['name'], 'Owned startup 0')
            self.assertEqual(result['items'][0]['observed_at'], '2026-10-01T00:00:00+00:00')
            self.assertNotIn('error', result)
            self.assertNotIn('Foreign task observation must never leak', json.dumps(payload, ensure_ascii=False))
            return call('record_advice', self.ratings(), 'score-repaired-inspection')

        self.queue = [call('collect_apps', {'section': 'background'}, 'collect'),
                      call('inspect_items', {}, 'missing-inspection-ids'), repair, score,
                      call('present_findings', self.findings(), 'finish')]
        result = self.run_turn()
        self.assert_finished(result, 5, failures=1)
        self.assertIsNone(result['investigation_notice'], 'A repairable tool argument error is not a broken provider stream.')
        facts = self.harness.facts_page(self.task['id'])['items']
        self.assertEqual(sum(row['tool'] == 'inspect_items' for row in facts), 1,
                         'The failed call must not be saved as a successful inspection fact.')


if __name__ == '__main__':
    unittest.main()
