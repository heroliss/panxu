"""Relaxed model batching uses the same scoped facts and bounded wire parser."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot import providers, streaming
from diskpilot.llm import ModelAssistant
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness, tool_specs
from diskpilot.windows import _item
from tests.test_investigation_completion import call
from tests.test_llm_request_budget import response


class LlmLimitPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-limit-policy-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.state = SimulationState(self.directory / 'state')
        self.addCleanup(self.state.close)
        self.queue, self.requests = [], []

        def transport(url, payload, headers, timeout):
            self.requests.append(copy.deepcopy(payload))
            if not self.queue:
                raise AssertionError('No extra model request was expected.')
            return self.queue.pop(0)

        self.assistant = ModelAssistant(self.directory / 'profile', transport=transport)
        self.assistant.save_settings(dict(base_url='http://127.0.0.1:1234/v1', model='owned-fixture'))
        self.harness = TaskHarness(self.state, self.assistant)
        self.rows = [_item('startup', f'owned-startup-{n}', f'人工启动项{n}', 'Owned fixture',
            'startup', 'disable_startup', dict(state='Enabled', enabled=True),
            path=f'C:/OwnedLimitFixtures/{n}/inert.exe') for n in range(60)]
        self.task = self.harness.create(preset='background')

    def ratings(self, count):
        return dict(items=[dict(id=row['id'], conclusion='review', cleanup_rating=3,
            purpose='人工启动注册项', reason='仅供分批调查回归，不是实际系统判断。', impact='本次只保存建议，不执行操作。')
            for row in self.rows[:count]])

    def run_queue(self):
        with patch('diskpilot.tasks.inventory', return_value=dict(items=self.rows, warnings=[])):
            self.harness.claim(self.task['id'])
            return self.harness.investigate(self.task['id'])

    def test_one_reply_can_save_thirty_advice_items_after_inspecting_fifty(self):
        self.queue = [call('collect_apps', dict(section='background'), 'collect'),
            call('inspect_items', dict(ids=[row['id'] for row in self.rows[:50]]), 'inspect'),
            call('record_advice', self.ratings(30), 'save'),
            call('present_findings', dict(summary='已保存30项人工样本建议；其余尚未分析。', needs_input=False), 'done')]
        result = self.run_queue()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['advice_total'], 30)
        self.assertEqual(result['usage']['model_requests'], 4)
        self.assertEqual(result['usage']['tool_failures'], 0)
        self.assertEqual(self.state.history(), [])
        spec = {row['function']['name']: row['function']['parameters'] for row in tool_specs()}
        self.assertEqual(spec['record_advice']['properties']['items']['maxItems'], 30)
        self.assertEqual(spec['inspect_items']['properties']['ids']['maxItems'], 50)
        # Detailed option packages and content hashing keep their separate bounds.
        self.assertEqual(spec['record_specialist_suggestions']['properties']['items']['maxItems'], 6)
        self.assertEqual(spec['compare_files']['properties']['ids']['maxItems'], 20)

    def test_inspection_still_rejects_unknown_or_duplicate_ids_and_overlarge_batch(self):
        with patch('diskpilot.tasks.inventory', return_value=dict(items=self.rows, warnings=[])):
            self.harness.tool(self.task['id'], 'collect_apps', dict(section='background'), cancel=threading.Event())
        for ids in ([self.rows[0]['id'], self.rows[0]['id']],
                    [row['id'] for row in self.rows[:51]], ['not-observed']):
            with self.subTest(ids_count=len(ids)), self.assertRaises(AppError):
                self.harness.tool(self.task['id'], 'inspect_items', dict(ids=ids), cancel=threading.Event())
        with self.assertRaises(AppError):
            self.harness.tool(self.task['id'], 'record_advice', self.ratings(31), cancel=threading.Event())
        self.assertEqual(self.harness.public(self.task['id'])['advice_total'], 0)
        self.assertEqual(self.state.history(), [])

    def test_custom_and_local_services_use_the_same_output_allowance_as_other_protocols(self):
        for provider, protocol in [('custom', value) for value in providers.PROTOCOLS] + [('ollama', 'chat_completions')]:
            with self.subTest(provider=provider, protocol=protocol), tempfile.TemporaryDirectory() as directory:
                observed = []

                def transport(url, payload, headers, timeout):
                    observed.append(copy.deepcopy(payload))
                    return response(protocol)

                assistant = ModelAssistant(Path(directory), transport=transport)
                assistant.save_settings(dict(provider=provider, protocol=protocol,
                    base_url='http://127.0.0.1:1234/v1', model='owned-fixture'))
                result = assistant.agent_turn([dict(role='user', content='Only an artificial protocol fixture.')], [], stream=False)
                self.assertEqual(result['content'], 'Done.')
                payload = observed[0]
                actual = (payload.get('max_tokens') or payload.get('max_output_tokens')
                          or payload.get('generationConfig', {}).get('maxOutputTokens'))
                self.assertEqual(actual, streaming.MAX_GENERATION_TOKENS)


if __name__ == '__main__':
    unittest.main()
