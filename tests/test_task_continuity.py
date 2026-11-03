import json
from pathlib import Path
import tempfile
import unittest
import threading

from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness
from tests.prompt_fixture import parse_host_data


def call(name, args, *, usage=None, reasoning=None):
    message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-' + name, 'type': 'function',
        'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}
    if reasoning:
        message['reasoning_content'] = reasoning
    return dict(choices=[{'message': message}], usage=usage or {})


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'selected'
        self.scope.mkdir()
        for index in range(70):
            (self.scope / f'file-{index}.txt').write_text('disposable fixture')
        self.state = SimulationState(self.root / 'state')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(self.scope))['items'], 'fixture')
        self.queue, self.requests = [], []
        def transport(url, payload, headers, timeout):
            self.requests.append(payload)
            value = self.queue.pop(0)
            return value(payload) if callable(value) else value
        self.assistant = ModelAssistant(self.root / 'state', transport=transport)
        self.assistant.save_settings({'base_url': 'http://127.0.0.1:12345/v1', 'model': 'model-a'})
        self.harness = TaskHarness(self.state, self.assistant)
        self.task = self.harness.create_analysis({'kind': 'file'})

    def run_turn(self):
        self.harness.claim(self.task['id'])
        return self.harness.investigate(self.task['id'])

    def tool(self, name, args):
        return self.harness.tool(self.task['id'], name, args, cancel=threading.Event())

    def test_usage_is_cumulative_partial_and_survives_reconstruction(self):
        self.queue = [call('list_items', {}, usage={'prompt_tokens': 11, 'completion_tokens': 7}),
                      call('ask_user', {'question': '是否需要保留离线副本？', 'options': [
                          {'id': 'keep', 'label': '保留'}, {'id': 'later', 'label': '稍后决定'}]})]
        result = self.run_turn()
        self.assertEqual(result['usage']['model_requests'], 2)
        self.assertEqual(result['usage']['reported_usage_calls'], 1)
        self.assertEqual(result['usage']['input_tokens'], 11)
        self.assertEqual(result['usage']['output_tokens'], 7)
        self.assertEqual(result['usage']['tool_calls'], 2)
        recovered = TaskHarness(self.state, self.assistant).public(self.task['id'])
        self.assertEqual(recovered['usage'], result['usage'])
        self.assertEqual(recovered['handoff']['reason'], 'needs_input')
        self.harness.answer(self.task['id'], {'version': recovered['question']['version'], 'skip': True})
        self.queue = [call('present_findings', {'summary': '建议已保存，具体项目由你勾选。', 'needs_input': False})]
        self.assertEqual(self.run_turn()['usage']['model_requests'], 3)

    def test_model_switch_within_turn_rebuilds_from_facts_without_opaque_state(self):
        def first(payload):
            self.assistant.save_settings({'model': 'model-b'})
            return call('list_items', {}, reasoning='opaque-private-sentinel')
        def second(payload):
            self.assertEqual(payload['model'], 'model-b')
            self.assertEqual([m['role'] for m in payload['messages']], ['system', 'user'])
            self.assertNotIn('opaque-private-sentinel', json.dumps(payload))
            context = parse_host_data(payload['messages'][1]['content'])
            self.assertTrue(context['connection_changed'])
            self.assertGreater(context['facts']['total'], 0)
            return call('present_findings', {'summary': '已使用历史事实继续。', 'needs_input': False})
        self.queue = [first, second]
        result = self.run_turn()
        self.assertTrue(result['connection_changed'])
        self.assertEqual(result['previous_connection']['model'], 'model-a')
        self.assertEqual(result['handoff']['model']['model'], 'model-b')
        stored = self.state.db.execute('SELECT payload FROM tasks WHERE id=?', (self.task['id'],)).fetchone()[0]
        self.assertNotIn('opaque-private-sentinel', stored)
        self.assertNotIn('_continuation', stored)

    def test_facts_have_sources_and_cannot_be_borrowed_from_another_task(self):
        self.queue = [call('list_items', {}), call('present_findings', {'summary': '这些资料由你决定。', 'needs_input': False})]
        self.run_turn()
        fact = self.harness.facts_page(self.task['id'])['items'][0]
        self.assertEqual(fact['tool'], 'list_items')
        self.assertEqual(fact['freshness'], 'historical_observation')
        self.assertTrue(fact['requires_recheck_for_action'])
        item = self.harness._items(self.harness._get(self.task['id']))[0]
        advice = dict(id=item['id'], conclusion='review', purpose='用户资料', reason='先确认用途', impact='由你选择', evidence_ids=[fact['id']])
        self.tool('record_advice', {'items': [advice]})
        other = self.harness.create_analysis({'ids': [item['id']]})
        with self.assertRaises(AppError):
            self.harness.tool(other['id'], 'record_advice', {'items': [advice]}, cancel=threading.Event())
        self.assertEqual(self.tool('read_facts', {'ids': [fact['id']]})['total'], 1)

    def test_checkpoint_and_budget_exhaustion_do_not_report_completion(self):
        self.harness.agent_budget.save_session({'model_requests': 6})
        self.queue = [call('checkpoint_task', {'summary': '已看到目录用途。', 'next_steps': ['核对安装包版本'], 'unresolved': ['是否保留离线副本']})]
        self.queue += [call('list_items', {}) for _ in range(5)]
        result = self.run_turn()
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(result['handoff']['summary'], '已看到目录用途。')
        self.assertIn('核对安装包版本', result['handoff']['next_steps'])
        self.assertGreater(result['facts']['total'], 0)
        self.assertEqual(result['usage']['model_requests'], 6)

    def test_interrupted_task_older_than_recent_fifty_is_recovered(self):
        self.harness.claim(self.task['id'])
        for index in range(55):
            self.harness.create('新任务' + str(index), str(self.scope))
        self.assertNotIn(self.task['id'], {t['id'] for t in self.harness._all()})
        recovered = TaskHarness(self.state, self.assistant).public(self.task['id'])
        self.assertEqual(recovered['status'], 'paused')
        self.assertEqual(recovered['handoff']['reason'], 'interrupted')

    def test_kept_directory_satisfies_skip_without_forcing_private_traversal(self):
        directory = next(i for i in self.state.view()['items'] if i['kind'] == 'directory')
        task = self.harness.create_analysis({'ids': [directory['id']], 'depth': 'detailed'})
        self.harness.feedback(task['id'], keep_ids=[directory['id']])
        result = self.harness.public(task['id'])['analysis']
        self.assertEqual(result['directories_skipped'], 1)
        self.assertTrue(result['directory_coverage_complete'])
        self.assertEqual(result['remaining_count'], 0)
        self.harness.tool(task['id'], 'present_findings', {'summary': '按你的要求保留整个目录。', 'needs_input': False}, cancel=threading.Event())

    def test_advice_lookup_by_ids_reaches_beyond_preview_and_is_bounded(self):
        items = self.harness._items(self.harness._get(self.task['id']))
        for offset in range(0, len(items), 30):
            self.tool('record_advice', {'items': [dict(id=i['id'], conclusion='review', purpose='资料', reason='保留判断', impact='由你决定', evidence_ids=[]) for i in items[offset:offset + 30]]})
        result = self.harness.advice_page(self.task['id'], limit=50, ids=[items[-1]['id']])
        self.assertEqual([i['id'] for i in result['items']], [items[-1]['id']])
        self.assertEqual(result['total'], 1)
        with self.assertRaises(AppError):
            self.harness.advice_page(self.task['id'], ids=['x'] * 51)

    def test_old_task_schema_receives_defaults_without_provider_state(self):
        task = self.harness._get(self.task['id'])
        task.pop('usage', None)
        task.pop('handoff', None)
        task.pop('scans', None)
        with self.state.db:
            self.state.db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(task), task['id']))
        public = self.harness.public(task['id'])
        self.assertEqual(public['usage']['model_requests'], 0)
        self.assertIsNone(public['usage']['input_tokens'])
        self.assertEqual(public['scans'], [])
        self.assertNotIn('_continuation', json.dumps(public))


if __name__ == '__main__':
    unittest.main()
