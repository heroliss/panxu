"""Specialist tools use owned SQLite fixtures and the shared virtual runtime."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.actions import ActionRuntime
from diskpilot.cleanup_tools import CleanupToolkit, REGISTRY
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tool_launch import entry_definition


class Interrupted(BaseException):
    pass


class CleanupToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = SimulationState(Path(self.temp.name) / 'data')
        self.addCleanup(self.state.close)
        self.tools = CleanupToolkit(self.state)
        resolver = patch('diskpilot.cleanup_tools.resolve_entry', side_effect=lambda entry_id, parameters=None:
            dict(entry_definition(entry_id), parameters=parameters or {}, available=False, reason='测试不启动本机程序'))
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_registry_is_common_to_direct_and_model_clients(self):
        listing = self.tools.list()
        self.assertEqual(listing['total'], 48)
        self.assertTrue(listing['simulation_only'])
        for tool in listing['items']:
            detail = self.tools.detail(tool['id'])
            self.assertFalse(detail['capabilities']['apply'])
            self.assertTrue(detail['capabilities']['prepare'])
            self.assertTrue(detail['sources'])
            self.assertTrue(all(source['url'].startswith('https://') for source in detail['sources']))
        self.assertEqual(self.tools.list(query='UV')['items'][0]['id'], 'uv_cache')
        self.assertTrue(all(x['category'] == 'privacy' for x in self.tools.list(category='privacy')['items']))

    def test_detail_cannot_mutate_global_registry(self):
        detail = self.tools.detail('windows_temp')
        detail['parameters'][0]['default'] = 'Z:'
        self.assertEqual(self.tools.detail('windows_temp')['parameters'][0]['default'], 'C:')

    def test_reference_normalizes_only_known_fields(self):
        self.assertEqual(self.tools.validate_reference({'tool_id': 'windows_temp', 'parameters': {'volume': 'e:'}}),
                         {'tool_id': 'windows_temp', 'parameters': {'volume': 'E:'}})
        for reference in ({'tool_id': 'nope'}, {'tool_id': 'windows_temp', 'command': 'malicious'},
                          {'tool_id': 'windows_temp', 'parameters': []},
                          {'tool_id': 'windows_temp', 'parameters': {'script': 'Remove-Item'}},
                          {'tool_id': 'windows_temp', 'parameters': {'volume': r'\\server\share'}}):
            with self.assertRaises(AppError):
                self.tools.validate_reference(reference)

    def test_required_path_and_numeric_parameters_are_checked(self):
        for parameters in ({}, {'target_path': 'relative'}, {'target_path': r'\\server\share'},
                           {'target_path': 'C:\\x\x00bad'}):
            with self.assertRaises(AppError):
                self.tools.prepare('known_folder_move', parameters)
        for params in ({'initial_mb': True}, {'initial_mb': -1},
                       {'mode': 'custom', 'initial_mb': 4096, 'maximum_mb': 2048}):
            with self.assertRaises(AppError):
                self.tools.prepare('pagefile', params)
        plan = self.tools.prepare('known_folder_move', {'target_path': r'E:\Documents'})
        self.assertEqual(plan['parameters']['target_path'], r'E:\Documents')

    def test_empty_wrong_parameters_are_not_silently_accepted(self):
        with self.assertRaises(AppError):
            self.tools.prepare('windows_temp', [])
        with self.assertRaises(AppError):
            self.tools.inspect('windows_temp', [])

    def test_source_is_bounded_navigation_not_code_or_authorization(self):
        source = dict(task_id='task-a', item_id='item-a', signature='sig-a', advice_version=2,
                      return_context=dict(view='files', query='cache', limit=50, scroll_y=100))
        plan = self.tools.prepare('windows_temp', source=source)
        self.assertEqual(plan['source'], source)
        self.assertEqual(self.tools.get_origin(task_id='task-a')[0]['id'], plan['id'])
        self.assertEqual(self.tools.get_origin(item_id='item-a')[0]['id'], plan['id'])
        for bad in ({'command': 'x'}, {'return_context': {'html': '<script>'}},
                    {'task_id': 'x' * 201}, {'return_context': {'scroll_y': float('nan')}},
                    {'return_context': {'query': {'api_key': 'private'}}}):
            with self.assertRaises(AppError):
                self.tools.prepare('windows_temp', source=bad)

    def test_inspect_only_uses_fixed_evidence_and_unknown_is_not_zero(self):
        with patch('diskpilot.cleanup_tools.volume_snapshot', return_value={'status': 'observed', 'free_bytes': 123}) as read:
            report = self.tools.inspect('windows_temp', {'volume': 'E:'})
        read.assert_called_once_with('E:\\')
        self.assertEqual(report['evidence']['free_bytes'], 123)
        self.assertTrue(report['read_only'])
        with patch('diskpilot.tool_inspection.find_program', return_value=None):
            self.assertEqual(self.tools.inspect('browser_privacy')['evidence']['status'], 'unknown')
        with patch('diskpilot.cleanup_tools.system_configuration', return_value={'status': 'partial'}) as config:
            self.assertEqual(self.tools.inspect('pagefile')['evidence']['status'], 'partial')
        config.assert_called_once_with()

    def test_settings_are_definitions_not_a_side_effect(self):
        settings = self.tools.detail('windows_temp')['settings']
        self.assertEqual(settings[0]['entry_id'], 'disk_cleanup')
        self.assertEqual(self.tools.history(), [])
        names = [row['label'] for row in self.tools.detail('browser_cache')['settings']]
        self.assertTrue(any('Firefox' in label for label in names))
        self.assertTrue(self.tools.detail('browser_cache')['capabilities']['open_settings'])
        self.assertTrue(all(row['kind'] != 'official_method' for row in self.tools.detail('browser_cache')['settings']))

    def test_prepare_is_frozen_and_execute_uses_same_runtime(self):
        plan = self.tools.prepare('windows_temp')
        self.assertEqual(plan['status'], 'awaiting_review')
        self.assertEqual(self.tools.runtime.result(plan['id'], plan['version'])['total'], 0)
        result = self.tools.execute(plan['id'], plan['version'])
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(result['items'][0]['verified'])
        self.assertEqual(result['actual_reclaimed_bytes'], 0)
        self.assertEqual(result['actions'], {'specialist_cleanup': 1})
        self.assertEqual(self.tools.get_plan(plan['id'])['status'], 'completed')
        self.assertEqual(self.tools.get_plan(plan['id'])['result'], result)
        with self.state.lock:
            self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM overlay').fetchone()[0], 0)

    def test_execute_is_idempotent_and_never_replays_after_undo(self):
        plan = self.tools.prepare('windows_temp')
        first = self.tools.execute(plan['id'], plan['version'])
        self.assertEqual(self.tools.execute(plan['id'], plan['version']), first)
        undone = self.tools.undo(plan['id'])
        self.assertEqual(undone['result']['undone'], 1)
        self.assertEqual(self.tools.execute(plan['id'], plan['version'])['undone'], 1)
        with self.state.lock:
            self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM cleanup_tool_receipts').fetchone()[0], 1)

    def test_wrong_version_and_modified_persisted_plan_cannot_execute(self):
        plan = self.tools.prepare('windows_temp')
        for version in (False, 2, '1'):
            with self.assertRaises(AppError):
                self.tools.execute(plan['id'], version)
        changed = copy.deepcopy(plan)
        changed['entries'][0]['parameters']['volume'] = 'D:'
        with self.state.lock, self.state.db:
            self.state.db.execute('UPDATE cleanup_tool_plans SET payload=? WHERE id=?', (json.dumps(changed), plan['id']))
        with self.assertRaises(AppError):
            self.tools.execute(plan['id'], 1)

    def test_changed_registry_invalidates_frozen_entry(self):
        plan = self.tools.prepare('windows_temp')
        with patch.dict(REGISTRY['windows_temp'], version=2):
            with self.assertRaises(AppError):
                self.tools.get_plan(plan['id'])

    def test_batch_deduplicates_same_action_and_rejects_alternatives(self):
        plan = self.tools.prepare_many([{'tool_id': 'windows_temp'}, {'tool_id': 'windows_temp', 'parameters': {'volume': 'C:'}}])
        self.assertEqual(len(plan['entries']), 1)
        with self.assertRaises(AppError):
            self.tools.prepare_many([{'tool_id': 'hibernation', 'parameters': {'mode': 'off'}},
                                     {'tool_id': 'hibernation', 'parameters': {'mode': 'full'}}])
        with self.assertRaises(AppError):
            self.tools.prepare_many([{'tool_id': 'known_folder_move', 'parameters': {'target_path': r'E:\A'}},
                                     {'tool_id': 'known_folder_move', 'parameters': {'target_path': r'E:\B'}}])
        privacy = self.tools.prepare_many([
            {'tool_id': 'browser_privacy', 'parameters': {'data_type': 'history'}},
            {'tool_id': 'browser_privacy', 'parameters': {'data_type': 'cookies'}}])
        self.assertEqual(len(privacy['entries']), 2)

    def test_failure_and_partial_result_are_not_forced_success(self):
        plan = self.tools.prepare_many([{'tool_id': 'windows_temp'}, {'tool_id': 'browser_cache'}])
        self.tools.runtime.fault = lambda entry, phase: entry['tool_id'] == 'windows_temp' and phase == 'before'
        result = self.tools.execute(plan['id'], 1)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['succeeded'], 1)

    def test_cancel_before_commit_records_no_virtual_change(self):
        plan = self.tools.prepare('windows_temp')
        cancel = threading.Event()
        cancel.set()
        result = self.tools.execute(plan['id'], 1, cancel=cancel)
        self.assertEqual(result['status'], 'cancelled')
        with self.state.lock:
            self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM cleanup_tool_receipts').fetchone()[0], 0)

    def test_interruption_recovery_uses_receipt_not_reapply(self):
        plan = self.tools.prepare('windows_temp')
        def fault(entry, phase):
            if phase == 'after':
                raise Interrupted()
        self.tools.runtime.fault = fault
        with self.assertRaises(Interrupted):
            self.tools.execute(plan['id'], 1)
        ActionRuntime(self.state).recover()  # Default backend must leave specialist attempts alone.
        self.assertEqual(self.tools.runtime.result(plan['id'], 1)['unverified'], 1)
        reopened = CleanupToolkit(self.state)
        self.assertEqual(reopened.runtime.result(plan['id'], 1)['succeeded'], 1)
        with self.state.lock:
            self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM cleanup_tool_receipts').fetchone()[0], 1)

    def test_cancel_review_does_not_execute(self):
        plan = self.tools.prepare('windows_temp')
        self.assertEqual(self.tools.cancel_plan(plan['id'], 1)['status'], 'cancelled')
        self.assertEqual(self.tools.execute(plan['id'], 1)['total'], 0)

    def test_persistent_plan_is_restored_without_replay(self):
        plan = self.tools.prepare('windows_temp')
        reopened = CleanupToolkit(self.state)
        self.assertEqual(reopened.get_plan(plan['id'])['frozen_hash'], plan['frozen_hash'])
        self.assertEqual(reopened.history()[0]['status'], 'awaiting_review')

    def test_second_facade_does_not_recover_work_still_running(self):
        plan = self.tools.prepare('windows_temp')
        entered, release = threading.Event(), threading.Event()
        errors = []
        def gate(entry, phase):
            if phase == 'before':
                entered.set()
                release.wait(5)
        self.tools.runtime.fault = gate
        def run():
            try:
                self.tools.execute(plan['id'], 1)
            except BaseException as exc:
                errors.append(exc)
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            other = CleanupToolkit(self.state)
            self.assertEqual(other.get_plan(plan['id'])['status'], 'executing')
            with self.assertRaises(AppError):
                other.execute(plan['id'], 1)
            self.assertIs(other._controls, self.tools._controls)
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
