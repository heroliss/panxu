import json
from pathlib import Path
import tempfile
import threading
import unittest

from diskpilot.catalog import FileCatalog
from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness, tool_specs


class SpecialistHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'scope'
        self.scope.mkdir()
        (self.scope / 'cache').mkdir()
        (self.scope / 'cache' / 'a.bin').write_bytes(b'disposable specialist fixture')
        (self.scope / 'other.bin').write_bytes(b'keep fixture')
        self.state = SimulationState(self.root / 'state')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(self.scope))['items'], 'fixture')
        self.assistant = ModelAssistant(self.root / 'state')
        self.harness = TaskHarness(self.state, self.assistant)
        self.items = {item['name']: item for item in self.state.snapshot([])['items']}
        self.cache = self.items['cache']
        self.child = self.items['a.bin']
        self.task_id = self.harness.create_analysis({'ids': [self.cache['id']], 'section': 'files'})['id']
        self.cancel = threading.Event()

    def tool(self, name, args, **kw):
        return self.harness.tool(self.task_id, name, args, cancel=self.cancel, **kw)

    def advice(self, item=None, **changes):
        item = item or self.cache
        result = dict(id=item['id'], conclusion='recommend', cleanup_rating=5,
                      purpose='应用缓存', reason='建议通过专用工具处理。', impact='重新访问可能重建缓存。')
        result.update(changes)
        return result

    def extra(self, **changes):
        value = dict(id='cache-options', title='清理缓存的方式', explanation='可以使用产品自身清理功能。',
                     impact='不应直接删除程序目录。', item_ids=[self.cache['id']], options=[
                         dict(id='tool', label='查看浏览器清理', kind='tool', tool_id='browser_cache'),
                         dict(id='keep', label='暂时保留', kind='answer')])
        value.update(changes)
        return value

    def test_tools_discover_on_demand_and_no_execution_capability(self):
        names = {spec['function']['name'] for spec in tool_specs()}
        self.assertTrue({'discover_cleanup_tools', 'inspect_cleanup_tool', 'record_specialist_suggestions'} <= names)
        self.assertNotIn('execute_cleanup_tool', names)
        result = self.tool('discover_cleanup_tools', {'query': '缓存'})
        self.assertTrue(result['items'])
        self.assertEqual(self.state.history(), [])
        context = self.harness._context(self.harness._get(self.task_id))
        self.assertNotIn('initial_objects', context)
        self.assertNotIn('cleanup_tools', context)

    def test_discovered_specialist_can_conclude_within_existing_six_request_budget(self):
        requests = []
        commands = [('discover_cleanup_tools', {'query': '浏览器'}),
                    ('list_items', {}), ('list_items', {}), ('list_items', {}),
                    ('record_specialist_suggestions', {'items': [self.extra()]}),
                    ('present_findings', {'summary': '已提供专项选项，未执行清理。', 'needs_input': False})]

        def transport(_url, payload, _headers, _timeout):
            index = len(requests)
            requests.append(payload)
            names = {row['function']['name'] for row in payload['tools']}
            if index == 4:
                self.assertIn('record_specialist_suggestions', names)
                self.assertNotIn('discover_cleanup_tools', names)
            if index == 5:
                self.assertEqual(names, {'present_findings', 'ask_user'})
            self.assertLess(index, 6)
            name, args = commands[index]
            return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': f'specialist-{index}', 'type': 'function',
                 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}}]}

        self.assistant._transport = transport
        self.assistant.save_settings({'base_url': 'http://127.0.0.1:1/v1', 'model': 'owned-specialist-fixture'})
        self.harness.claim(self.task_id)
        result = self.harness.investigate(self.task_id)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['model_requests'], 6)
        self.assertEqual(result['usage']['tool_calls'], 6)
        self.assertEqual(result['usage']['tool_failures'], 0)
        self.assertEqual(result['extra_suggestions'][0]['id'], 'cache-options')
        self.assertEqual(self.state.history(), [])

    def test_direct_link_persists_without_changing_text_or_score(self):
        self.tool('record_advice', {'items': [self.advice(specialist={'tool_id': 'browser_cache'})]})
        restored = TaskHarness(self.state, self.assistant)
        advice = restored.public(self.task_id)['advice'][0]
        self.assertEqual(advice['cleanup_rating'], 5)
        self.assertEqual(advice['reason'], '建议通过专用工具处理。')
        link = restored.specialist_for(self.cache['id'], self.cache['signature'], self.task_id)
        self.assertEqual(link['specialist']['tool_id'], 'browser_cache')
        self.assertEqual(link['specialist']['mode'], 'supplement')
        self.assertEqual(link['source']['item_id'], self.cache['id'])
        self.assertFalse(link['inherited'])
        self.assertEqual(self.state.history(), [])

    def test_invalid_reference_does_not_save_partial_advice(self):
        with self.assertRaises(AppError):
            self.tool('record_advice', {'items': [self.advice(specialist={'tool_id': 'not-a-tool'})]})
        self.assertEqual(self.harness.public(self.task_id)['advice'], [])
        self.assertEqual(self.harness.assessments.revision(), '0')

    def test_replace_link_blocks_auto_delete_and_model_proposal(self):
        self.tool('record_advice', {'items': [self.advice(specialist={'tool_id': 'browser_cache', 'mode': 'replace'})]})
        catalog = FileCatalog(self.state, lambda: [], self.harness.assessments)
        row = next(row for row in catalog.page()['items'] if row['id'] == self.cache['id'])
        self.assertIn('specialist_required', row['assessment']['blocked_reasons'])
        self.assertFalse(row['can_prepare'])
        self.assertFalse(row['assessment']['eligible_direct'])
        with self.assertRaises(AppError) as error:
            self.tool('propose_plan', {'summary': '错误整删', 'items': [dict(id=self.cache['id'], action='delete', reason='整删', impact='删除目录')]})
        self.assertEqual(error.exception.code, 'specialist_required')
        # An explicit host deletion remains a distinct, reviewable user choice.
        result = self.harness.direct_plan(self.task_id, {'ids': [self.cache['id']], 'action': 'delete'})
        self.assertEqual(result['plan']['origin'], 'user_direct')
        self.assertEqual(self.state.history(), [])

    def test_inheritance_reuses_original_target_without_expanding_tool_scope(self):
        self.tool('record_advice', {'items': [self.advice(inheritance='subtree', specialist={'tool_id': 'browser_cache', 'mode': 'replace'})]})
        link = self.harness.specialist_for(self.child['id'], self.child['signature'], self.task_id)
        self.assertTrue(link['inherited'])
        self.assertEqual(link['source']['item_id'], self.cache['id'])
        self.assertEqual(link['source']['signature'], self.cache['signature'])
        self.assertEqual(link['requested_item_id'], self.child['id'])
        self.assertNotIn('path', link['specialist']['parameters'])

    def test_stale_object_link_is_rejected(self):
        self.tool('record_advice', {'items': [self.advice(specialist={'tool_id': 'browser_cache'})]})
        with self.assertRaises(AppError):
            self.harness.specialist_for(self.cache['id'], 'old-signature', self.task_id)

    def test_extra_suggestion_choice_is_fact_not_execution(self):
        result = self.tool('record_specialist_suggestions', {'items': [self.extra()]})
        self.assertFalse(result['execution_authorized'])
        self.tool('record_advice', {'items': [self.advice(extra_suggestion_id='cache-options')]})
        link = self.harness.specialist_for(self.cache['id'], self.cache['signature'], self.task_id)
        self.assertEqual(link['extra_suggestion']['id'], 'cache-options')
        self.assertEqual(link['source']['suggestion_id'], 'cache-options')
        answer = self.harness.answer_specialist(self.task_id, 'cache-options', 'tool', version=1)
        self.assertEqual(answer['specialist']['tool_id'], 'browser_cache')
        self.assertFalse(answer['execution_authorized'])
        self.assertFalse(answer['selection']['execution_authorized'])
        restored = TaskHarness(self.state, self.assistant)
        self.assertEqual(restored.public(self.task_id)['extra_suggestions'][0]['selection']['option_id'], 'tool')
        facts = restored.facts_page(self.task_id)['items']
        self.assertTrue(any(fact['tool'] == 'specialist_answer' for fact in facts))
        self.assertTrue(any(fact['tool'] == 'record_specialist_suggestions' for fact in facts))
        self.assertEqual(self.state.history(), [])

    def test_reanalysis_changes_version_and_rejects_old_choice(self):
        self.tool('record_specialist_suggestions', {'items': [self.extra()]})
        self.tool('record_specialist_suggestions', {'items': [self.extra(title='新的专项说明')]})
        with self.assertRaises(AppError) as error:
            self.harness.answer_specialist(self.task_id, 'cache-options', 'tool', version=1)
        self.assertEqual(error.exception.code, 'specialist_stale')
        current = self.harness.answer_specialist(self.task_id, 'cache-options', 'keep', version=2)
        self.assertNotIn('specialist', current)
        self.assertEqual(current['extra_suggestion']['title'], '新的专项说明')

    def test_extra_targets_cannot_expand_task_and_failed_batch_is_atomic(self):
        outside = self.items['other.bin']
        with self.assertRaises(AppError):
            self.tool('record_specialist_suggestions', {'items': [self.extra(), self.extra(id='outside', item_ids=[outside['id']])]})
        self.assertEqual(self.harness.public(self.task_id)['extra_suggestions'], [])

    def test_extra_suggestion_rejects_shell_and_invalid_tool_options(self):
        for option in (dict(id='run', label='运行', kind='shell', instructions='Remove-Item anything'),
                       dict(id='run', label='运行', kind='tool', tool_id='shell'),
                       dict(id='run', label='回答', kind='answer', tool_id='browser_cache')):
            with self.subTest(option=option), self.assertRaises(AppError):
                self.tool('record_specialist_suggestions', {'items': [self.extra(options=[option, dict(id='keep', label='保留', kind='answer')])]})
        self.assertEqual(self.harness.public(self.task_id)['extra_suggestions'], [])

    def test_manual_instructions_and_source_are_preserved(self):
        options = [dict(id='manual', label='查看人工步骤', kind='instructions', instructions='打开应用设置，按产品说明处理。'),
                   dict(id='later', label='稍后再说', kind='answer')]
        source = dict(source='llm', provider='fixture', model='fixture-model', protocol='chat_completions', generated_at='2026-11-02T00:00:00+00:00')
        self.tool('record_specialist_suggestions', {'items': [self.extra(options=options)]}, provenance=source)
        answer = self.harness.answer_specialist(self.task_id, 'cache-options', 'manual', version=1)
        self.assertEqual(answer['extra_suggestion']['provenance']['model'], 'fixture-model')
        self.assertEqual(answer['extra_suggestion']['options'][0]['instructions'], options[0]['instructions'])
        self.assertNotIn('specialist', answer)
        self.assertEqual(self.state.history(), [])

    def test_tool_receipt_returns_to_task_as_idempotent_fact(self):
        tools = self.harness.cleanup_tools
        plan = tools.prepare('browser_cache', source={'task_id': self.task_id, 'item_id': self.cache['id'], 'signature': self.cache['signature']})
        self.assertIsNone(self.harness.record_specialist_result(plan))
        tools.execute(plan['id'], plan['version'])
        record = self.harness.record_specialist_result(plan)
        self.assertFalse(record['windows_changed'])
        self.assertTrue(record['simulation_only'])
        self.assertEqual(record['actual_reclaimed_bytes'], 0)
        before = self.harness.facts_page(self.task_id)['total']
        self.harness.record_specialist_result(plan)
        self.assertEqual(self.harness.facts_page(self.task_id)['total'], before)
        restored = TaskHarness(self.state, self.assistant)
        self.assertEqual(restored.public(self.task_id)['last_specialist_result']['plan_id'], plan['id'])
        tools.undo(plan['id'])
        undo = self.harness.record_specialist_result(plan)
        self.assertEqual(undo['plan_status'], 'undone')
        self.assertEqual(self.harness.facts_page(self.task_id)['total'], before + 1)
        self.assertTrue((self.scope / 'cache' / 'a.bin').exists())

    def test_old_advice_remains_without_a_specialist_link(self):
        self.tool('record_advice', {'items': [self.advice()]})
        self.assertNotIn('specialist', self.harness.public(self.task_id)['advice'][0])
        with self.assertRaises(AppError):
            self.harness.specialist_for(self.cache['id'], self.cache['signature'], self.task_id)


if __name__ == '__main__':
    unittest.main()
