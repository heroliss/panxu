"""Fixed read-only evidence: units, unknowns, scope and outbound privacy."""
import ctypes
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.system_evidence import NativeEvidence, MAX_RESULT_BYTES, _Performance, _Pagefile, _configured_pagefile, system_configuration, volume_snapshot
from diskpilot.tasks import TaskHarness, tool_specs


class Backend:
    def memory(self):
        return dict(page_size_bytes=4096, commit_current_bytes=40000, commit_limit_bytes=100000,
                    commit_peak_since_boot_bytes=80000, physical_total_bytes=65536)

    def pagefiles(self, page_size):
        return dict(items=[dict(volume='C:', file='custom_system_file', path=r'C:\Users\PrivateCanary\custom-memory.bin',
            allocated_bytes=10000, used_bytes=1000, peak_used_bytes=2000)], complete=True)

    def power(self):
        return dict(hibernation_supported=True, hibernation_file_present=True)

    def registry_configuration(self):
        return dict(values={'paging_files': dict(status='observed', value=[dict(volume='C:', file='pagefile.sys',
            initial_mb=1024, maximum_mb=2048)]), 'temporary_pagefile_flag': dict(status='unknown', reason='value absent')})

    def volume(self, path):
        return dict(root='C:\\', drive_type='fixed', capacity=dict(status='observed', free_bytes=5000,
            available_to_current_user_bytes=4000, total_bytes=10000), recycle_bin=dict(status='observed',
            query_succeeded=True, hresult='0x00000000', size_bytes=100, item_count=1))


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class SystemEvidenceTests(unittest.TestCase):
    def test_config_distinguishes_active_usage_and_requested_settings_with_unknowns(self):
        report = system_configuration(backend=Backend(), platform='nt')
        self.assertEqual(report['active_pagefiles']['items'][0]['used_bytes'], 1000)
        self.assertEqual(report['configuration']['values']['paging_files']['value'][0]['initial_mb'], 1024)
        self.assertEqual(report['configuration']['values']['temporary_pagefile_flag']['status'], 'unknown')
        self.assertTrue(report['partial'])
        self.assertEqual(report['actions'], [])
        self.assertTrue(report['read_only'])
        self.assertTrue(report['collected_at'].endswith('+00:00'))
        self.assertTrue(report['completed_at'])
        self.assertNotIn('max_native_calls', report['budget'])
        self.assertEqual(report['budget']['max_core_snapshot_calls'], 3)
        self.assertLess(len(json.dumps(report).encode()), MAX_RESULT_BYTES)

    def test_collection_errors_are_unknown_not_disabled_or_zero(self):
        backend = Backend()
        backend.memory = lambda: (_ for _ in ()).throw(PermissionError('private error detail'))
        backend.power = lambda: (_ for _ in ()).throw(OSError('no capabilities'))
        report = system_configuration(backend=backend, platform='nt')
        self.assertEqual(report['memory']['status'], 'unknown')
        self.assertEqual(report['power_capabilities']['status'], 'unknown')
        self.assertNotIn('physical_total_bytes', report['memory'])
        self.assertNotIn('private error detail', json.dumps(report))
        self.assertTrue(report['partial'])

    def test_unsupported_platform_does_not_load_native_libraries(self):
        with patch.object(NativeEvidence, '_dll', side_effect=AssertionError('loaded native DLL')):
            self.assertEqual(system_configuration(platform='posix')['status'], 'unsupported')
            self.assertEqual(volume_snapshot('C:\\safe', platform='posix')['status'], 'unsupported')

    def test_volume_capacity_quota_and_recycle_query_are_not_restore_guarantees(self):
        report = volume_snapshot('C:\\selected', backend=Backend(), platform='nt')
        self.assertEqual(report['capacity']['free_bytes'], 5000)
        self.assertEqual(report['capacity']['available_to_current_user_bytes'], 4000)
        self.assertTrue(report['recycle_bin']['query_succeeded'])
        self.assertIn('不证明', report['warnings'][0])
        self.assertEqual(report['actions'], [])
        backend = Backend()
        backend.volume = lambda path: dict(capacity=dict(status='observed'), recycle_bin=dict(query_succeeded=False, hresult='0x80004005'))
        failed = volume_snapshot('C:\\selected', backend=backend, platform='nt')
        self.assertTrue(failed['partial'])
        self.assertNotIn('size_bytes', failed['recycle_bin'])

    def test_arbitrary_network_relative_device_or_command_paths_are_rejected(self):
        for path in (r'\\server\share\data', r'\\?\C:\data', r'\\.\PhysicalDrive0', 'C:relative',
                     '../data', 'https://example.com', 'C:\\data' + chr(0)):
            with self.subTest(path=path), self.assertRaises(ValueError):
                volume_snapshot(path, backend=Backend(), platform='nt')
        with self.assertRaises(ValueError):
            system_configuration('powershell')

    def test_pagefile_configuration_keeps_numeric_limits_without_private_paths(self):
        parsed = _configured_pagefile(r'C:\Users\PrivateCanary\custom name.bin 1024 8192')
        self.assertEqual(parsed['volume'], 'C:')
        self.assertEqual(parsed['file'], 'custom_system_file')
        self.assertEqual((parsed['initial_mb'], parsed['maximum_mb']), (1024, 8192))
        self.assertNotIn('PrivateCanary', json.dumps(parsed))
        self.assertEqual(_configured_pagefile(r'E:\pagefile.sys 0 0')['mode'], 'system_managed_requested')
        self.assertFalse(_configured_pagefile('unrecognized sensitive text')['parsed'])

    def test_native_memory_counts_pages_as_bytes_without_guessing_best_size(self):
        class Library:
            pass
        library = Library()

        def collect(pointer, size):
            value = ctypes.cast(pointer, ctypes.POINTER(_Performance)).contents
            self.assertEqual(size, ctypes.sizeof(_Performance))
            value.PageSize, value.CommitTotal, value.CommitLimit = 4096, 100, 1000
            value.CommitPeak, value.PhysicalTotal, value.PhysicalAvailable = 200, 500, 123
            return 1

        library.GetPerformanceInfo = Function(collect)
        with patch.object(NativeEvidence, '_dll', return_value=library):
            report = NativeEvidence().memory()
        self.assertEqual(report['commit_current_bytes'], 409600)
        self.assertEqual(report['commit_peak_since_boot_bytes'], 819200)
        self.assertEqual(report['physical_available_bytes'], 503808)
        self.assertNotIn('recommended_pagefile_size', report)

    @unittest.skipUnless(hasattr(ctypes, 'WINFUNCTYPE'), 'Win32 callback ABI test')
    def test_native_pagefile_callback_stops_at_budget_and_reports_partial(self):
        class Library:
            pass
        library = Library()
        seen = []

        def enumerate(callback, context):
            for index in range(100):
                value = _Pagefile()
                value.TotalSize, value.TotalInUse, value.PeakUsage = 100, 2, 9
                seen.append(index)
                if not callback(None, ctypes.pointer(value), f'C:\\pagefile-{index}.sys'):
                    return 0
            return 1

        library.EnumPageFilesW = Function(enumerate)
        with patch.object(NativeEvidence, '_dll', return_value=library):
            report = NativeEvidence().pagefiles(4096, limit=3)
        self.assertEqual(len(report['items']), 3)
        self.assertEqual(len(seen), 4)
        self.assertFalse(report['complete'])
        self.assertTrue(report['limit_reached'])
        self.assertEqual(report['items'][0]['used_bytes'], 8192)

    def test_native_remote_drive_does_not_call_filesystem_capacity_or_shell(self):
        class Library:
            GetDriveTypeW = Function(lambda path: 4)
        with patch.object(NativeEvidence, '_dll', return_value=Library()) as load:
            report = NativeEvidence().volume('Z:\\selected')
        self.assertEqual(report['drive_type'], 'remote')
        self.assertEqual(report['capacity']['status'], 'unknown')
        self.assertEqual(load.call_count, 1)

    def test_oversize_is_explicit_partial_and_not_silently_presented_as_complete(self):
        backend = Backend()
        backend.registry_configuration = lambda: dict(values={}, fixture='large' * MAX_RESULT_BYTES)
        report = system_configuration(backend=backend, platform='nt')
        self.assertTrue(report['output_truncated'])
        self.assertTrue(report['partial'])
        self.assertEqual(report['configuration']['status'], 'unknown')
        self.assertLess(len(json.dumps(report).encode()), MAX_RESULT_BYTES)


class HarnessSystemEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'selected'
        self.scope.mkdir()
        (self.scope / 'data.txt').write_text('fixture')
        self.state = SimulationState(self.root / 'store')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(self.scope))['items'], 'fixture')
        self.requests, self.queue = [], []

        def transport(url, payload, headers, timeout):
            self.requests.append(payload)
            return self.queue.pop(0)

        self.assistant = ModelAssistant(self.root / 'store', transport=transport)
        self.harness = TaskHarness(self.state, self.assistant)
        file = next(i for i in self.state.view()['items'] if i['kind'] == 'file')
        self.task = self.harness.create_analysis({'ids': [file['id']]})

    def tool(self, name, args):
        return self.harness.tool(self.task['id'], name, args, cancel=threading.Event())

    def test_volume_uses_existing_scope_and_never_expands_file_only_action_scope(self):
        before = self.harness._get(self.task['id'])
        self.assertEqual(self.tool('list_scopes', {})['total'], 0)
        references = self.tool('list_scopes', {'purpose': 'volume'})
        self.assertEqual(references['total'], 1)
        self.assertFalse(references['scopes'][0]['can_traverse'])
        self.assertEqual(self.harness._context(before)['volume_scopes'][0]['scope_id'], before['roots'][0]['id'])
        with (patch('diskpilot.tasks.volume_snapshot', return_value=volume_snapshot('C:\\safe', backend=Backend(), platform='nt')) as collect,
              patch('diskpilot.tasks.validate_volume_path')):
            report = self.tool('inspect_volume', {'scope_id': before['roots'][0]['id']})
            self.assertEqual(collect.call_args.args, (before['roots'][0]['path'],))
            self.assertEqual(report['scope_id'], before['roots'][0]['id'])
        after = self.harness._get(self.task['id'])
        self.assertEqual(after['known_ids'], before['known_ids'])
        self.assertEqual(after['roots'], before['roots'])
        self.assertEqual(after['action_directories'], [])
        with self.assertRaises(AppError):
            self.tool('inspect_volume', {'scope_id': 'different-volume'})
        with self.assertRaises(AppError):
            self.tool('inspect_volume', {'path': 'D:\\'})
        with self.assertRaises(AppError):
            self.tool('survey_scope', {'scope_id': before['roots'][0]['id']})

    def test_system_fact_is_persisted_with_source_and_outbound_path_privacy(self):
        self.assistant.save_settings({'base_url': 'http://127.0.0.1:12345/v1', 'model': 'fixture', 'send_paths': False})

        def call(name, args):
            return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'call-' + name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}}]}

        self.queue = [call('collect_system_configuration', {}), call('present_findings', {'summary': '已取得有限配置。', 'needs_input': True})]
        report = system_configuration(backend=Backend(), platform='nt')
        with patch('diskpilot.tasks.system_configuration', return_value=report):
            self.harness.claim(self.task['id'])
            result = self.harness.investigate(self.task['id'])
        self.assertEqual(result['facts']['total'], 1)
        fact = self.harness.facts_page(self.task['id'])['items'][0]
        self.assertEqual(fact['tool'], 'collect_system_configuration')
        self.assertEqual(fact['source'], 'tool_observation')
        self.assertEqual(fact['result']['memory']['source'], 'GetPerformanceInfo')
        sent = json.dumps(self.requests, ensure_ascii=False)
        self.assertNotIn('PrivateCanary', sent)
        self.assertIn('initial_mb', sent)
        self.assertIn('1024', sent)
        self.assertEqual(self.state.history(), [])
        specs = {i['function']['name']: i['function']['parameters'] for i in tool_specs()}
        self.assertEqual(specs['inspect_volume']['required'], ['scope_id'])
        self.assertNotIn('command', specs['collect_system_configuration']['properties'])


if __name__ == '__main__':
    unittest.main()
