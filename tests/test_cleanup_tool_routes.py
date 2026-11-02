"""Specialist routes use owned fixtures and virtual receipts, never live cleanup."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.scanner import scan_directory
from diskpilot.server import Application
from diskpilot.simulation import AppError


class CleanupToolRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'files'
        self.scope.mkdir()
        (self.scope / 'cache.bin').write_bytes(b'owned specialist route fixture')
        (self.scope / 'other.txt').write_bytes(b'owned protected-by-scope fixture')
        self.before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.scope.iterdir()}
        self.app = Application(self.root / 'data')
        self.addCleanup(self.app.close)
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'owned-route-fixtures')
        self.items = {item['name']: item for item in self.app.state.snapshot([])['items']}
        self.item = self.items['cache.bin']
        self.task_id = self.app.harness.create_analysis({'ids': [self.item['id']], 'section': 'files'})['id']
        self.source = dict(task_id=self.task_id, item_id=self.item['id'], signature=self.item['signature'])
        self.no_process = patch('subprocess.run', side_effect=AssertionError('cleanup route must not spawn a command'))
        self.no_process.start()
        self.addCleanup(self.no_process.stop)
        self.no_popen = patch('subprocess.Popen', side_effect=AssertionError('cleanup route must not spawn a process'))
        self.no_popen.start()
        self.addCleanup(self.no_popen.stop)

    def assert_files_unchanged(self):
        self.assertEqual({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.scope.iterdir()}, self.before)

    def prepare(self, **changes):
        payload = dict(tool_id='browser_cache', parameters={}, source=self.source)
        payload.update(changes)
        return self.app.post('/api/cleanup-tools/prepare', payload)

    def execute(self, plan, **changes):
        payload = dict(plan_id=plan['id'], version=plan['version'])
        payload.update(changes)
        return self.app.post('/api/cleanup-tools/execute', payload)

    def extra(self):
        value = dict(id='options', title='专项方式', explanation='通过产品自身清理。', impact='缓存可能重新生成。',
                     item_ids=[self.item['id']], options=[dict(id='tool', kind='tool', label='查看专项工具', tool_id='browser_cache'),
                     dict(id='keep', kind='answer', label='保留')])
        return self.app.harness.tool(self.task_id, 'record_specialist_suggestions', {'items': [value]}, cancel=threading.Event())

    def test_list_detail_history_and_frozen_plan_are_available_without_model(self):
        with patch.object(self.app.assistant, 'agent_turn', side_effect=AssertionError('tool UI must not require LLM')):
            listing = self.app.get('/api/cleanup-tools', {})
            self.assertGreaterEqual(listing['total'], 20)
            self.assertTrue(listing['simulation_only'])
            detail = self.app.get('/api/cleanup-tools/browser_cache', {})
            self.assertEqual(detail['id'], 'browser_cache')
            self.assertTrue(detail['parameters'])
            self.assertEqual(self.app.get('/api/cleanup-tools/history', {}), {'history': []})
            plan = self.prepare()
            saved = self.app.get('/api/cleanup-plans/' + plan['id'], {})
            self.assertEqual(saved['frozen_hash'], plan['frozen_hash'])
            self.assertEqual(self.app.get('/api/cleanup-tools/history', {})['history'][0]['id'], plan['id'])
        self.assert_files_unchanged()

    def test_inspect_only_uses_fixed_readers(self):
        with patch('diskpilot.cleanup_tools.volume_snapshot', return_value={'status': 'fixture', 'free_bytes': 1024}) as read:
            result = self.app.post('/api/cleanup-tools/inspect', {'tool_id': 'windows_temp', 'parameters': {'volume': 'E:'}})
        self.assertTrue(result['read_only'])
        self.assertEqual(result['evidence']['free_bytes'], 1024)
        read.assert_called_once_with('E:\\')
        self.assertEqual(self.app.get('/api/cleanup-tools/history', {})['history'], [])
        self.assert_files_unchanged()

    def test_execute_version_and_duplicate_request_are_receipt_idempotent(self):
        plan = self.prepare()
        with self.assertRaises(AppError):
            self.execute(plan, version=plan['version'] + 1)
        first = self.execute(plan)
        self.assertEqual(first['succeeded'], 1)
        self.assertTrue(first['simulation_only'])
        self.assertEqual(first['actual_reclaimed_bytes'], 0)
        second = self.execute(plan)
        self.assertEqual(first, second)
        self.assertEqual(self.app.state.db.execute('SELECT COUNT(*) FROM cleanup_tool_receipts').fetchone()[0], 1)
        task = self.app.harness.public(self.task_id)
        self.assertEqual(task['last_specialist_result']['plan_id'], plan['id'])
        self.assertFalse(task['last_specialist_result']['windows_changed'])
        facts = self.app.harness.facts_page(self.task_id)['items']
        self.assertEqual(sum(row['tool'] == 'specialist_result' for row in facts), 1)
        self.assert_files_unchanged()

    def test_cancel_before_execute_keeps_no_receipt_and_undo_only_changes_virtual_state(self):
        cancelled = self.prepare()
        result = self.app.post('/api/cleanup-tools/cancel', {'plan_id': cancelled['id'], 'version': cancelled['version']})
        self.assertEqual(result['status'], 'cancelled')
        self.execute(cancelled)
        self.assertEqual(self.app.state.db.execute('SELECT COUNT(*) FROM cleanup_tool_receipts').fetchone()[0], 0)
        plan = self.prepare()
        self.execute(plan)
        undone = self.app.post('/api/cleanup-tools/undo', {'plan_id': plan['id']})
        self.assertEqual(undone['status'], 'undone')
        self.assertEqual(undone['result']['undone'], 1)
        repeated = self.execute(plan)
        self.assertEqual(repeated['undone'], 1)
        self.assertEqual(self.app.harness.public(self.task_id)['last_specialist_result']['plan_status'], 'undone')
        self.assert_files_unchanged()

    def test_prepare_many_deduplicates_same_tool_parameters(self):
        plan = self.app.post('/api/cleanup-tools/prepare-many', dict(items=[
            {'tool_id': 'browser_cache'}, {'tool_id': 'browser_cache', 'parameters': {}},
            {'tool_id': 'hibernation', 'parameters': {'mode': 'full'}}], source=self.source))
        self.assertEqual(len(plan['entries']), 2)
        result = self.execute(plan)
        self.assertEqual(result['total'], 2)
        with self.assertRaises(AppError):
            self.app.post('/api/cleanup-tools/prepare-many', dict(items=[
                {'tool_id': 'hibernation', 'parameters': {'mode': 'off'}},
                {'tool_id': 'hibernation', 'parameters': {'mode': 'full'}}]))
        self.assert_files_unchanged()

    def test_partial_runtime_result_is_preserved_in_source_task(self):
        plan = self.app.post('/api/cleanup-tools/prepare-many', dict(items=[
            {'tool_id': 'browser_cache'}, {'tool_id': 'hibernation'}], source=self.source))
        runtime = self.app.cleanup_tools.runtime
        with patch.object(runtime, 'fault', lambda entry, when: when == 'before' and entry['tool_id'] == 'hibernation'):
            result = self.execute(plan)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual((result['succeeded'], result['failed'], result['total']), (1, 1, 2))
        source = self.app.harness.public(self.task_id)['last_specialist_result']
        self.assertEqual(source['status'], 'partial')
        self.assertEqual(source['failed'], 1)
        self.assertFalse(source['windows_changed'])
        self.assert_files_unchanged()

    def test_task_only_and_navigation_only_sources_do_not_grant_object_scope(self):
        source = {'task_id': self.task_id}
        plan = self.prepare(source=source)
        self.assertEqual(plan['source'], source)
        nav = {'return_context': {'view': 'files', 'scroll_y': 240}}
        self.assertEqual(self.prepare(source=nav)['source'], nav)
        self.extra()
        self.prepare(source={'task_id': self.task_id, 'suggestion_id': 'options'})
        for invalid in ({'task_id': self.task_id, 'suggestion_id': 'missing'}, {'signature': 'detached'},
                        {'suggestion_id': 'options'}, False, 0, []):
            with self.subTest(source=invalid), self.assertRaises(AppError):
                self.prepare(source=invalid)
        self.assert_files_unchanged()

    def test_source_requires_real_task_target_signature_and_current_advice_version(self):
        for source in (dict(task_id='missing-task'), dict(item_id='missing-item', signature='x'),
                       dict(self.source, signature='old-signature'),
                       dict(task_id=self.task_id, item_id=self.item['id']),
                       dict(task_id=self.task_id, item_id=self.items['other.txt']['id'], signature=self.items['other.txt']['signature'])):
            with self.subTest(source=source), self.assertRaises(AppError):
                self.prepare(source=source)
        self.app.harness.tool(self.task_id, 'record_advice', {'items': [dict(id=self.item['id'], conclusion='review',
            purpose='缓存', reason='使用工具', impact='重新生成', specialist={'tool_id': 'browser_cache'})]}, cancel=threading.Event())
        link = self.app.get('/api/specialist', {'item_id': [self.item['id']], 'signature': [self.item['signature']], 'task_id': [self.task_id]})
        self.prepare(source=link['source'])
        stale = dict(link['source'], advice_version=link['source']['advice_version'] + 1)
        with self.assertRaises(AppError):
            self.prepare(source=stale)
        self.assert_files_unchanged()

    def test_execute_rechecks_source_before_recording(self):
        plan = self.prepare()
        row = self.app.state.db.execute('SELECT payload FROM cleanup_tool_plans WHERE id=?', (plan['id'],)).fetchone()
        frozen = json.loads(row[0])
        # Mutating a stored source cannot redirect a previously reviewed plan.
        frozen['source']['signature'] = 'changed'
        with self.app.state.lock, self.app.state.db:
            self.app.state.db.execute('UPDATE cleanup_tool_plans SET payload=? WHERE id=?', (json.dumps(frozen), plan['id']))
        with self.assertRaises(AppError):
            self.execute(plan)
        self.assertEqual(self.app.state.db.execute('SELECT COUNT(*) FROM cleanup_tool_receipts').fetchone()[0], 0)
        self.assert_files_unchanged()

    def test_open_settings_uses_registered_destination_only_and_never_marks_applied(self):
        with patch('diskpilot.server.open_entry', return_value=dict(status='opened', applied=False, label='打开磁盘清理')) as launch:
            result = self.app.post('/api/cleanup-tools/open-settings', dict(tool_id='windows_temp', source=self.source))
            self.assertEqual(result['status'], 'opened')
            self.assertFalse(result['applied'])
            self.assertEqual(launch.call_args.args[0]['entry_id'], 'disk_cleanup')
            self.assertEqual(launch.call_args.args[0]['parameters'], {'volume': 'C:'})
            with self.assertRaises(AppError):
                self.app.post('/api/cleanup-tools/open-settings', dict(tool_id='windows_temp', target='evil.exe'))
            self.assertEqual(launch.call_count, 1)
        facts = self.app.harness.facts_page(self.task_id)['items']
        self.assertTrue(any(row['tool'] == 'specialist_settings_opened' for row in facts))
        self.assertEqual(self.app.state.db.execute('SELECT COUNT(*) FROM cleanup_tool_receipts').fetchone()[0], 0)
        self.assert_files_unchanged()

    def test_browser_entry_opens_registered_app_and_open_failures_are_not_success(self):
        detail = self.app.get('/api/cleanup-tools/browser_cache', {})
        index = len(detail['settings']) - 1
        self.assertNotEqual(detail['settings'][index]['kind'], 'official_method')
        with patch('diskpilot.server.open_entry', return_value=dict(status='opened', applied=False, label='打开Firefox隐私设置')) as launch:
            result = self.app.post('/api/cleanup-tools/open-settings', dict(tool_id='browser_cache', index=index))
            self.assertFalse(result['applied'])
            self.assertEqual(launch.call_args.args[0]['entry_id'], 'firefox_clear')
        with patch('diskpilot.server.open_entry', side_effect=AppError('未能打开对应程序', 'entrypoint_unavailable')), self.assertRaises(AppError):
            self.app.post('/api/cleanup-tools/open-settings', dict(tool_id='browser_cache', index=index))
        self.assert_files_unchanged()

    def test_extra_answer_route_checks_version_and_does_not_execute(self):
        self.extra()
        path = '/api/tasks/' + self.task_id + '/specialist-answer'
        with self.assertRaises(AppError):
            self.app.post(path, {'suggestion_id': 'options', 'option_id': 'tool', 'version': 0})
        with patch.object(self.app.cleanup_tools, 'execute', side_effect=AssertionError('answer cannot execute')):
            answer = self.app.post(path, {'suggestion_id': 'options', 'option_id': 'tool', 'version': 1})
        self.assertEqual(answer['specialist']['tool_id'], 'browser_cache')
        self.assertFalse(answer['execution_authorized'])
        self.assertEqual(self.app.get('/api/cleanup-tools/history', {})['history'], [])
        self.assertEqual(self.app.harness.public(self.task_id)['extra_suggestions'][0]['selection']['option_id'], 'tool')
        self.assert_files_unchanged()

    def test_unknown_payloads_cannot_inject_commands_or_settings_targets(self):
        for path, payload in [('/api/cleanup-tools/execute', {'plan_id': 'missing', 'version': 1, 'command': 'cmd'}),
                              ('/api/cleanup-tools/inspect', {'tool_id': 'browser_cache', 'shell': 'powershell'}),
                              ('/api/cleanup-tools/run', {'command': 'cmd'}),
                              ('/api/cleanup-tools/open-settings', {'tool_id': 'browser_cache', 'index': True})]:
            with self.subTest(path=path), self.assertRaises(AppError):
                self.app.post(path, payload)
        self.assert_files_unchanged()


if __name__ == '__main__':
    unittest.main()
