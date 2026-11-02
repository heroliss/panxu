"""A completed analysis goes to selection; actual questions stay actionable."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness
from tests.prompt_fixture import parse_host_data


def call(name, arguments):
    return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'call-' + name, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}}]}


class FindingsProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-findings-protocol-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        scope = root / 'owned'; scope.mkdir()
        (scope / 'cache.tmp').write_bytes(b'owned artificial file')
        self.state = SimulationState(root / 'profile')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(scope))['items'], 'owned')
        self.queue, self.requests = [], []

        def transport(_url, payload, _headers, _timeout):
            self.requests.append(payload)
            step = self.queue.pop(0)
            return step(payload) if callable(step) else step

        self.assistant = ModelAssistant(root / 'profile', transport=transport)
        self.assistant.save_settings(dict(base_url='http://127.0.0.1:12345/v1', model='owned-model'))
        self.harness = TaskHarness(self.state, self.assistant)
        self.item = next(item for item in self.state.snapshot()['items'] if item['kind'] == 'file')
        self.key = self.harness.create_analysis({'ids': [self.item['id']]})['id']

    def run_turn(self):
        self.harness.claim(self.key)
        return self.harness.investigate(self.key)

    def advice(self):
        return {'items': [dict(id=self.item['id'], conclusion='review', cleanup_rating=4,
            purpose='人工测试缓存', reason='依据样本用途提供倾向', impact='具体项目由用户勾选')]}

    def test_unstructured_wait_flag_is_rejected_without_losing_saved_advice(self):
        self.harness.tool(self.key, 'record_advice', self.advice(), cancel=threading.Event())
        before = self.harness.public(self.key)
        with self.assertRaises(AppError) as raised:
            self.harness.tool(self.key, 'present_findings',
                dict(summary='需要我生成方案吗？', needs_input=True), cancel=threading.Event())
        self.assertEqual(raised.exception.code, 'question_required')
        after = self.harness.public(self.key)
        self.assertEqual(after['advice'], before['advice'])
        self.assertEqual(after['status'], before['status'])
        self.assertIsNone(after['question'])
        self.assertEqual(self.state.history(), [])

    def test_model_can_repair_selection_wait_flag_and_finish_without_approval(self):
        def repair(payload):
            error = parse_host_data(payload['messages'][-1]['content'])
            self.assertEqual(error['code'], 'question_required')
            return call('present_findings', dict(summary='建议已保存，请在清单中勾选。', needs_input=False))

        self.queue = [call('record_advice', self.advice()),
            call('present_findings', dict(summary='需要我生成方案吗？', needs_input=True)), repair]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['advice']), 1)
        self.assertIsNone(result['question'])
        self.assertIsNone(result['plan'])
        self.assertEqual(result['usage']['tool_failures'], 1)
        self.assertEqual(self.state.history(), [])

    def test_genuine_missing_information_uses_a_structured_question(self):
        def repair(payload):
            self.assertEqual(parse_host_data(payload['messages'][-1]['content'])['code'], 'question_required')
            return call('ask_user', dict(question='是否还需要离线安装包？', options=[
                dict(id='keep', label='需要保留'), dict(id='unused', label='已经不用')]))

        self.queue = [call('present_findings', dict(summary='不知道离线用途。', needs_input=True)), repair]
        result = self.run_turn()
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(len(result['question']['options']), 2)
        self.assertFalse(self.state.history())

    def test_plain_readable_reply_does_not_create_an_unanswerable_wait_state(self):
        self.queue = [call('list_items', {}), {'choices': [{'message': {
            'role': 'assistant', 'content': '已查看这些人工文件，可以继续看建议。', 'tool_calls': []}}]},
            call('present_findings', dict(summary='本轮已核对，具体项目由用户选择。', needs_input=False))]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(result['question'])
        self.assertEqual(result['usage']['model_requests'], 3)
        self.assertFalse(self.state.history())


if __name__ == '__main__':
    unittest.main()
