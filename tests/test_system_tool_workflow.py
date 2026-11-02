"""Owned integration fixtures for model-free tools and model-visible feedback."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.presets import system_tool_suggestions
from diskpilot.scanner import scan_directory
from diskpilot.server import Application
from diskpilot.simulation import AppError


class SystemToolWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'owned-files'
        self.scope.mkdir()
        (self.scope / 'keep.txt').write_bytes(b'Owned system-tool workflow fixture. Never delete.')
        self.before = self.hashes()
        self.app = Application(self.root / 'data')
        self.addCleanup(self.app.close)
        self.harness = self.app.harness
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'owned-system-tool-workflow')
        self.transport = self.enter_patch(patch.object(self.app.assistant, '_transport',
            side_effect=AssertionError('Tool choices must not contact a model provider.')))
        self.model = self.enter_patch(patch.object(self.app.assistant, 'agent_turn',
            side_effect=AssertionError('Host-authored tool workflow must not ask a model.')))
        self.enter_patch(patch('subprocess.run', side_effect=AssertionError('No external command is allowed.')))
        self.enter_patch(patch('subprocess.Popen', side_effect=AssertionError('No external process is allowed.')))

    def tearDown(self):
        self.assertEqual(self.hashes(), self.before)
        self.model.assert_not_called()
        self.transport.assert_not_called()

    def enter_patch(self, context):
        value = context.start()
        self.addCleanup(context.stop)
        return value

    def hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.scope.iterdir()}

    def create_system(self, preset='system_cleanup'):
        # No installed Windows volume or current-profile location is observed.
        spec = dict(title='系统盘夹具', goal='分析本测试范围，同时提供独立系统工具。',
                    kind='filesystem', paths=[(str(self.scope), '自有测试目录')], priority=[])
        with patch('diskpilot.tasks.resolve_preset', return_value=spec), patch(
                'diskpilot.tasks.system_tool_suggestions', return_value=system_tool_suggestions('R:\\')):
            return self.harness.create(preset=preset)

    def suggestion(self, task, tool_id='windows_temp'):
        return next(s for s in task['extra_suggestions']
                    if any(option.get('tool_id') == tool_id for option in s['options']))

    def answer(self, task, suggestion, option='skip', version=None):
        return self.app.post('/api/tasks/' + task['id'] + '/specialist-answer', dict(
            suggestion_id=suggestion['id'], option_id=option,
            version=suggestion['version'] if version is None else version))

    def prepare(self, tool_id='windows_temp', *, source=None, parameters=None):
        return self.app.post('/api/cleanup-tools/prepare', dict(
            tool_id=tool_id, parameters=parameters or {}, source=source or {}))

    def activity(self, status=None):
        rows = self.harness.tool_activity.recent(limit=20)
        return [row for row in rows if status is None or row['status'] == status]

    def test_system_preset_and_old_alias_supply_fixed_tools_without_a_model(self):
        for preset in ('system_cleanup', 'c_cleanup'):
            with self.subTest(preset=preset):
                task = self.create_system(preset)
                extras = task['extra_suggestions']
                self.assertEqual(len(extras), 3)
                self.assertEqual({s['options'][0]['tool_id'] for s in extras},
                                 {'windows_temp', 'component_store', 'recycle_bin'})
                self.assertTrue(all(s['origin'] == 'host' and s['provenance']['source'] == 'local'
                                    and s['provenance']['model'] is None for s in extras))
                volume_options = [option for s in extras for option in s['options']
                                  if option.get('parameters', {}).get('volume')]
                self.assertTrue(volume_options)
                self.assertTrue(all(option['parameters']['volume'] == 'R:' for option in volume_options))
                self.assertEqual(task['usage']['model_requests'], 0)
        self.assertEqual(self.app.cleanup_tools.history(), [])

    def test_host_tool_choice_can_prepare_without_approving_or_executing(self):
        task = self.create_system()
        response = self.answer(task, self.suggestion(task), 'tool')
        self.assertFalse(response['execution_authorized'])
        self.assertEqual(response['specialist'], {'tool_id': 'windows_temp', 'parameters': {'volume': 'R:'}})
        plan = self.prepare(response['specialist']['tool_id'], parameters=response['specialist']['parameters'],
                            source=response['source'])
        self.assertEqual(plan['status'], 'awaiting_review')
        self.assertIsNone(plan.get('result'))
        self.assertEqual(self.app.state.db.execute('SELECT COUNT(*) FROM cleanup_tool_receipts').fetchone()[0], 0)
        self.assertEqual([row['status'] for row in self.activity()], ['accepted'])
        self.assertEqual(self.harness.public(task['id'])['usage']['model_requests'], 0)

    def test_host_skip_is_idempotent_and_public_selection_is_hydrated_without_task_overwrite(self):
        task = self.create_system()
        suggestion = self.suggestion(task)
        before = self.app.state.db.execute('SELECT payload FROM tasks WHERE id=?', (task['id'],)).fetchone()[0]
        first = self.answer(task, suggestion)
        second = self.answer(task, suggestion)
        self.assertEqual(first, second)
        self.assertEqual(self.activity('declined')[0]['tool_id'], 'windows_temp')
        self.assertEqual(len(self.activity()), 1)
        after = self.app.state.db.execute('SELECT payload FROM tasks WHERE id=?', (task['id'],)).fetchone()[0]
        self.assertEqual(before, after)
        selected = self.suggestion(self.harness.public(task['id']))['selection']
        self.assertEqual(selected['option_id'], 'skip')
        self.assertFalse(selected['execution_authorized'])
        self.assertEqual(self.app.cleanup_tools.history(), [])

    def test_busy_host_choice_survives_a_running_models_stale_task_save(self):
        task = self.create_system()
        suggestion = self.suggestion(task)
        self.harness.claim(task['id'])
        self.addCleanup(self.harness.release, task['id'])
        in_flight = self.harness._get(task['id'])
        self.assertEqual(in_flight['status'], 'investigating')
        response = self.answer(task, suggestion)
        self.assertFalse(response['execution_authorized'])
        in_flight['feedback'].append(dict(role='user', content='保留这轮调查中的用户反馈。'))
        self.harness._save(in_flight)
        hydrated = self.harness.public(task['id'])
        self.assertEqual(hydrated['status'], 'investigating')
        self.assertEqual(self.suggestion(hydrated)['selection']['option_id'], 'skip')
        self.assertEqual(self.harness._get(task['id'])['feedback'][-1]['content'], '保留这轮调查中的用户反馈。')
        self.assertEqual(len(self.activity('declined')), 1)

    def test_ordinary_model_suggestion_still_rejects_choices_while_busy(self):
        task = self.create_system()
        row = dict(id='model-optional-cache', title='模型专项建议', explanation='核对后选择。',
                   impact='选择不会执行。', item_ids=[], options=[
                       dict(id='tool', label='查看工具', kind='tool', tool_id='browser_cache'),
                       dict(id='skip', label='保留', kind='answer')])
        self.harness.tool(task['id'], 'record_specialist_suggestions', {'items': [row]}, cancel=threading.Event())
        suggestion = next(s for s in self.harness.public(task['id'])['extra_suggestions'] if s['id'] == row['id'])
        self.harness.claim(task['id'])
        self.addCleanup(self.harness.release, task['id'])
        with self.assertRaises(AppError) as caught:
            self.answer(task, suggestion)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, 'task_busy'))
        self.assertEqual(self.activity(), [])

    def test_model_cannot_replace_a_reserved_host_suggestion_and_bad_version_is_not_recorded(self):
        task = self.create_system()
        suggestion = self.suggestion(task)
        row = dict(id=suggestion['id'], title='伪造替换', explanation='不应写入。', impact='不应写入。',
                   options=[dict(id='tool', label='工具', kind='tool', tool_id='browser_cache'),
                            dict(id='keep', label='保留', kind='answer')])
        with self.assertRaises(AppError) as caught:
            self.harness.tool(task['id'], 'record_specialist_suggestions', {'items': [row]}, cancel=threading.Event())
        self.assertEqual(caught.exception.code, 'host_suggestion_reserved')
        with self.assertRaises(AppError) as stale:
            self.answer(task, suggestion, version=suggestion['version'] + 1)
        self.assertEqual((stale.exception.status, stale.exception.code), (409, 'specialist_stale'))
        self.assertEqual(self.suggestion(self.harness.public(task['id']))['title'], suggestion['title'])
        self.assertEqual(self.activity(), [])

    def test_global_cancel_and_completed_receipts_are_recorded_once_without_raw_arguments(self):
        private_profile = 'private-profile-' + str(self.scope)
        source = {'return_context': {'view': 'cleanup', 'query': 'private-query-' + str(self.scope)}}
        cancelled = self.prepare('browser_cache', parameters={'browser': 'edge', 'profile': private_profile}, source=source)
        for _ in range(2):
            self.app.post('/api/cleanup-tools/cancel', dict(plan_id=cancelled['id'], version=cancelled['version']))
        self.assertEqual(len(self.activity('cancelled')), 1)
        plan = self.prepare('browser_cache', parameters={'browser': 'edge', 'profile': private_profile}, source=source)
        first = self.app.post('/api/cleanup-tools/execute', dict(plan_id=plan['id'], version=plan['version']))
        second = self.app.post('/api/cleanup-tools/execute', dict(plan_id=plan['id'], version=plan['version']))
        self.assertEqual(first, second)
        self.assertEqual(first['actual_reclaimed_bytes'], 0)
        self.assertEqual(len(self.activity('completed')), 1)
        self.assertEqual(self.app.state.db.execute('SELECT COUNT(*) FROM cleanup_tool_receipts').fetchone()[0], 1)
        serialized = json.dumps(self.activity(), ensure_ascii=False)
        for private in (private_profile, str(self.scope), 'private-query', 'parameters', 'return_context'):
            self.assertNotIn(private, serialized)
        self.assertTrue(all(not row['applied'] and row['actual_reclaimed_bytes'] == 0 for row in self.activity()))

    def test_mocked_global_open_is_an_open_event_never_applied_or_completed(self):
        private_profile = 'private-profile-' + str(self.scope)
        with patch('diskpilot.server.open_entry', return_value=dict(status='opened', applied=False,
                target='private-target-' + str(self.scope))) as opened:
            response = self.app.post('/api/cleanup-tools/open-settings', dict(tool_id='browser_cache',
                parameters={'browser': 'edge', 'profile': private_profile}))
        opened.assert_called_once()
        self.assertEqual(response['status'], 'opened')
        self.assertFalse(response['applied'])
        rows = self.activity()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'opened')
        self.assertEqual(rows[0]['backend'], 'host')
        self.assertFalse(rows[0]['applied'])
        self.assertEqual(self.activity('completed'), [])
        self.assertNotIn(str(self.scope), json.dumps(rows))
        self.assertNotIn('private-profile', json.dumps(rows))
        self.assertEqual(self.app.cleanup_tools.history(), [])

    def test_later_tasks_receive_compact_decline_and_completed_activity_without_a_model(self):
        task = self.create_system()
        self.answer(task, self.suggestion(task))
        plan = self.prepare('browser_cache')
        self.app.post('/api/cleanup-tools/execute', dict(plan_id=plan['id'], version=plan['version']))
        other = self.harness.create('查看新目录，参考之前的工具选择。', str(self.scope))
        context = self.harness._context(self.harness._get(other['id']))
        rows = context['recent_tool_activity']
        self.assertEqual({row['status'] for row in rows}, {'declined', 'completed'})
        self.assertEqual({row['tool_id'] for row in rows}, {'windows_temp', 'browser_cache'})
        self.assertTrue(all(row['applied'] is False and row['actual_reclaimed_bytes'] == 0 for row in rows))
        self.assertNotIn(str(self.scope), json.dumps(rows))
        self.assertEqual(self.harness.public(other['id'])['usage']['model_requests'], 0)


if __name__ == '__main__':
    unittest.main()
