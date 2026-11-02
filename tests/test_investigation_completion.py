"""A local fake provider must lead collection into saved, reviewable conclusions."""
import copy
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
from diskpilot.trace import DeveloperTrace
from diskpilot.windows import _item


def response(*calls, text=None):
    return {'choices': [{'message': {'role': 'assistant', 'content': text,
        'tool_calls': [{'id': ident, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}
            for ident, name, arguments in calls]}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}


def call(name, arguments, ident='call'):
    return response((ident, name, arguments))


class InvestigationCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-completion-')
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)
        self.state = SimulationState(self.profile)
        self.addCleanup(self.state.close)
        self.requests, self.queue = [], []

        def transport(_url, payload, _headers, _timeout):
            self.requests.append(payload)
            # Every assistant tool invocation must have its own complete result
            # before a subsequent request, including budget-rejected calls.
            pending = set()
            for message in payload['messages']:
                if message['role'] == 'assistant':
                    self.assertFalse(pending)
                    pending.update(row['id'] for row in message.get('tool_calls', []))
                elif message['role'] == 'tool':
                    self.assertIn(message['tool_call_id'], pending)
                    pending.remove(message['tool_call_id'])
            self.assertFalse(pending)
            if not self.queue:
                raise AssertionError('No unbounded provider retries are allowed')
            step = self.queue.pop(0)
            return step(payload) if callable(step) else step

        self.assistant = ModelAssistant(self.profile, transport=transport)
        self.assistant.save_settings({'base_url': 'http://127.0.0.1:1/v1', 'model': 'offline-fixture'})
        self.trace = DeveloperTrace(self.profile, lambda text: self.assistant.clean_text(text, limit=1024 * 1024))
        self.addCleanup(self.trace.close)
        self.trace.update(enabled=True)
        self.assistant.trace_hook = self.trace.record
        self.harness = TaskHarness(self.state, self.assistant, trace=self.trace)
        self.rows = [_item('startup', f'owned-{index}', f'Owned app {index}', 'Owned fixture', '开机启动',
            'disable_startup', {'synthetic': True}, reason='Disposable fixture') for index in range(8)]
        self.inventory = patch('diskpilot.tasks.inventory', return_value={
            'items': self.rows, 'warnings': ['只使用可丢弃样本'], 'summary': {'read_only': True}})
        self.inventory_mock = self.inventory.start()
        self.addCleanup(self.inventory.stop)
        self.task = self.harness.create(preset='background')

    def run_turn(self, cancel=None):
        self.harness.claim(self.task['id'])
        return self.harness.investigate(self.task['id'], cancel=cancel)

    def ratings(self, count=6):
        return {'items': [dict(id=row['id'], conclusion='review', cleanup_rating=3,
            purpose='开机启动注册项', reason='只核对注册元数据，未证明可以停用', impact='关闭后不会自动就绪')
            for row in self.rows[:count]]}

    def findings(self):
        return {'summary': '已核对样本启动项并保存建议；未检查其他自启动机制，没有修改系统。', 'needs_input': False}

    def tools(self, payload):
        return {row['function']['name'] for row in payload['tools']}

    def test_background_collects_then_reserves_scoring_and_findings_within_existing_budget(self):
        self.queue = [call('collect_apps', {'section': 'background'}, 'inventory'),
            call('list_items', {'kind': 'startup'}, 'list'),
            call('inspect_items', {'ids': [row['id'] for row in self.rows[:6]]}, 'inspect'),
            call('cache_experience', {'ids': [row['id'] for row in self.rows[:6]]}, 'experience')]

        def score(payload):
            self.assertEqual(self.tools(payload), {'record_advice', 'propose_plan', 'present_findings', 'ask_user'})
            return call('record_advice', self.ratings(), 'scores')

        def finish(payload):
            self.assertEqual(self.tools(payload), {'present_findings', 'ask_user'})
            return call('present_findings', self.findings(), 'findings')

        self.queue += [score, finish]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['advice_total'], 6)
        self.assertEqual(result['usage']['model_requests'], 6)
        self.assertEqual(result['usage']['tool_calls'], 6)
        self.assertEqual(result['usage']['tool_failures'], 0)
        self.assertEqual(self.inventory_mock.call_count, 1)
        self.assertFalse(self.state.history())
        self.assertIsNone(result['last_result'])

    def test_last_round_long_findings_with_modules_complete_and_keep_full_text_and_source(self):
        text = '已核对本次样本启动项。' * 70
        self.assertGreater(len(text), 160)
        self.assertLessEqual(len(text), 1000)
        modules = {'highlights': ['6项建议已保存，分数表示倾向而非处理许可。'],
                   'coverage': ['仅覆盖样本启动项，没有核对全部自启动机制。']}
        self.queue = [call('collect_apps', {'section': 'background'}, 'inventory')]
        self.queue += [call('list_items', {}, f'list-{n}') for n in range(3)]
        self.queue += [call('record_advice', self.ratings(), 'ratings'),
                      call('present_findings', dict(summary=text, briefing=modules, needs_input=False), 'findings')]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['model_requests'], 6)
        self.assertEqual(result['usage']['tool_failures'], 0)
        self.assertEqual(result['advice_total'], 6)
        self.assertEqual(result['summary'], text)
        self.assertEqual(result['briefing'], modules)
        source = result['briefing_provenance']
        self.assertEqual(source['source'], 'llm')
        self.assertEqual(source['model'], 'offline-fixture')
        self.assertIsNotNone(source['generated_at'])
        self.assistant.save_settings({'model': 'different-model'})
        restored = TaskHarness(self.state, self.assistant).public(self.task['id'])
        self.assertEqual(restored['summary'], text)
        self.assertEqual(restored['briefing'], modules)
        self.assertEqual(restored['briefing_provenance'], source)
        self.assertFalse(self.state.history())

    def test_collection_call_ceiling_leaves_real_conclusion_capacity_and_pairs_all_rejections(self):
        self.queue = [response(('collect', 'collect_apps', {'section': 'background'}),
            *[(f'first-{n}', 'list_items', {}) for n in range(7)]),
            response(*[(f'second-{n}', 'list_items', {}) for n in range(8)]),
            call('record_advice', self.ratings(), 'rating'),
            call('present_findings', self.findings(), 'finish')]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['model_requests'], 4)
        self.assertEqual(result['usage']['tool_calls'], 12)
        rejected = [message for message in self.requests[2]['messages'] if message['role'] == 'tool'
                    and '此调用未执行' in message['content']]
        self.assertEqual(len(rejected), 6)
        self.assertEqual(result['advice_total'], 6)
        self.assertFalse(self.state.history())

    def test_response_budget_recovery_uses_saved_evidence_and_never_replays_partial_tool(self):
        def overflow(_payload):
            raise ResponseBudgetExceeded('sse_wire_bytes', 524289, 524288)

        def score(payload):
            self.assertNotIn('collect_apps', self.tools(payload))
            self.assertIn('fact-', json.dumps(payload, ensure_ascii=False))
            return call('record_advice', self.ratings(), 'rating')

        self.queue = [call('collect_apps', {'section': 'background'}, 'inventory'), overflow,
                      score, call('present_findings', self.findings(), 'finish')]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.inventory_mock.call_count, 1)
        self.assertEqual(result['usage']['model_requests'], 4)
        self.assertEqual(result['usage']['reported_usage_calls'], 3)
        self.assertEqual(result['facts']['total'], 1)
        self.assertEqual(result['advice_total'], 6)
        self.assertEqual(result['investigation_notice']['code'], 'response_budget')
        self.assertTrue(result['investigation_notice']['recovered'])
        self.assertEqual(result['investigation_notice']['budget'], dict(boundary='sse_wire_bytes', observed=524289, limit=524288))
        errors = [entry for entry in self.trace.page(limit=30)['entries'] if entry['kind'] == 'error']
        self.assertEqual(errors[0]['payload']['payload']['budget']['boundary'], 'sse_wire_bytes')
        self.assertFalse(self.state.history())

    def test_explicit_upstream_token_cutoff_recovers_without_executing_partial_advice(self):
        truncated = {'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'partial-advice', 'type': 'function', 'function': {
                'name': 'record_advice', 'arguments': '{"items":['}}]}}]}
        self.queue = [call('collect_apps', {'section': 'background'}, 'inventory'), truncated,
            call('record_advice', self.ratings(), 'complete-advice'),
            call('present_findings', self.findings(), 'findings')]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['model_requests'], 4)
        self.assertEqual(result['usage']['tool_calls'], 3)
        self.assertEqual(result['usage']['tool_failures'], 0)
        self.assertEqual(result['advice_total'], 6)
        self.assertEqual(result['investigation_notice']['budget'], dict(
            boundary='generation_tokens', observed=None, limit=3500, signal='length'))
        self.assertTrue(result['investigation_notice']['recovered'])

    def test_ordinary_unfinished_network_stream_does_not_trigger_automatic_recovery(self):
        unfinished = b'data: {"choices":[{"index":0,"delta":{"content":"unfinished"}}]}\n\n'
        self.queue = [call('collect_apps', {'section': 'background'}, 'inventory'), lambda _: iter([unfinished])]
        result = self.run_turn()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['usage']['model_requests'], 2)
        self.assertEqual(result['investigation_notice']['code'], 'stream_incomplete')
        self.assertEqual(result['advice_total'], 0)
        self.assertEqual(result['facts']['total'], 1)

    def test_persistent_response_budget_failure_stops_after_one_recovery_without_fabricating_findings(self):
        def overflow(_payload):
            raise ResponseBudgetExceeded('arguments_chars', 128001, 128000)

        self.queue = [overflow] * 2
        result = self.run_turn()
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(result['usage']['model_requests'], 2)
        self.assertEqual(result['usage']['tool_calls'], 0)
        self.assertFalse(result['investigation_notice']['recovered'])
        self.assertTrue(result['investigation_notice']['recovery_attempted'])
        self.assertEqual(result['advice_total'], 0)
        self.assertEqual(result['facts']['total'], 0)
        self.assertNotIn('检查模型连接', result['summary'])

    def test_plain_first_reply_is_saved_without_reading_an_unassigned_execution_result(self):
        self.queue = [response(text='请说明希望保留哪些后台功能。')]
        result = self.run_turn()
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(result['summary'], '请说明希望保留哪些后台功能。')
        self.assertEqual(result['last_public_reply']['text'], result['summary'])
        self.assertIsNone(result['last_result'])
        self.assertIsNone(result['investigation_notice'])

    def test_plain_conclusion_after_scoring_uses_existing_completion_checks(self):
        self.queue = [call('collect_apps', {'section': 'background'}, 'inventory')]
        self.queue += [call('list_items', {}, f'list-{n}') for n in range(3)]
        self.queue += [call('record_advice', self.ratings(), 'rating'), response(text='已保存6项启动项建议，未实际修改系统。')]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['advice_total'], 6)
        self.assertEqual(result['last_public_reply']['text'], '已保存6项启动项建议，未实际修改系统。')
        self.assertIsNone(result['last_result'])

    def test_model_batch_limit_matches_schema_without_weakening_historical_host_tool(self):
        self.queue = [call('collect_apps', {'section': 'background'}, 'inventory'),
            call('record_advice', self.ratings(7), 'oversized'),
            call('record_advice', self.ratings(), 'corrected'),
            call('present_findings', self.findings(), 'finish')]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['tool_failures'], 1)
        self.assertEqual(result['advice_total'], 6)
        # Host/history compatibility remains separate from model's small batch.
        self.harness.tool(self.task['id'], 'record_advice', self.ratings(7), cancel=threading.Event())
        self.assertEqual(self.harness.advice_page(self.task['id'])['total'], 7)

    def test_finalization_rejects_new_collection_instead_of_silently_exceeding_budget(self):
        self.queue = [call('collect_apps', {'section': 'background'}, 'inventory')]
        self.queue += [call('list_items', {}, f'list-{n}') for n in range(3)]
        self.queue += [call('collect_apps', {'section': 'background'}, 'forbidden-repeat'),
                       call('present_findings', self.findings(), 'finish')]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['tool_failures'], 1)
        self.assertEqual(self.inventory_mock.call_count, 1)

    def test_last_round_budget_failure_preserves_scored_advice_and_last_complete_reply(self):
        first = call('collect_apps', {'section': 'background'}, 'inventory')
        first['choices'][0]['message']['content'] = '已开始只读核对样本。'

        def overflow(_payload):
            raise ResponseBudgetExceeded('sse_frames', 20001, 20000)

        self.queue = [first] + [call('list_items', {}, f'list-{n}') for n in range(3)]
        self.queue += [call('record_advice', self.ratings(), 'rating'), overflow]
        result = self.run_turn()
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(result['usage']['model_requests'], 6)
        self.assertEqual(result['advice_total'], 6)
        self.assertEqual(result['last_public_reply']['text'], '已开始只读核对样本。')
        self.assertFalse(result['investigation_notice']['recovery_attempted'])
        self.assertFalse(result['investigation_notice']['recovered'])

    def test_plain_finalization_cannot_hide_unfinished_detailed_coverage(self):
        real_metadata = copy.deepcopy(self.rows)
        for row in real_metadata:
            row['details']['synthetic'] = False
        self.state.publish(real_metadata, 'owned-background-metadata')
        self.task = self.harness.create_analysis({'section': 'background',
            'ids': [row['id'] for row in real_metadata], 'depth': 'detailed'})
        self.queue = [call('list_items', {}, f'list-{n}') for n in range(4)]
        self.queue += [call('record_advice', self.ratings(), 'rating'),
                      response(text='已保存6项建议，还有2项未逐项确认，需要继续。')]
        result = self.run_turn()
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(result['advice_total'], 6)
        self.assertEqual(self.harness._coverage(self.harness._get(self.task['id']))['remaining_count'], 2)
        self.assertIsNone(result['last_result'])

    def test_cancel_during_failed_output_does_not_start_a_finalization_request(self):
        cancel = threading.Event()

        def cancelled(_payload):
            cancel.set()
            raise ResponseBudgetExceeded('sse_frames', 20001, 20000)

        self.queue = [call('collect_apps', {'section': 'background'}, 'inventory'), cancelled]
        result = self.run_turn(cancel=cancel)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(result['facts']['total'], 1)
        self.assertFalse(self.state.history())


if __name__ == '__main__':
    unittest.main()
