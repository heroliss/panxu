"""Read-only bounded inspections use disposable metadata fixtures."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.cleanup_tools import CleanupToolkit, REGISTRY, REGISTRY_VERSION
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tool_inspection import candidate_paths, inspect_metadata, measure_paths, memory_presentation, volume_presentation


class ToolInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_metadata_scan_has_real_sizes_without_reading_file_contents(self):
        cache = self.root / 'cache'; cache.mkdir()
        first = cache / 'one.bin'; first.write_bytes(b'abc')
        nested = cache / 'nested'; nested.mkdir(); (nested / 'two.bin').write_bytes(b'12345')
        expected = hashlib.sha256(first.read_bytes()).hexdigest()
        with patch.object(Path, 'open', side_effect=AssertionError('inspection cannot read content')):
            report = measure_paths([cache])
        self.assertEqual(report['observed_bytes'], 8)
        self.assertEqual(report['observed_files'], 2)
        self.assertTrue(report['complete'])
        self.assertFalse(report['reads_file_contents'])
        self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), expected)

    def test_scan_budget_is_a_partial_lower_bound_not_fake_full_size(self):
        cache = self.root / 'many'; cache.mkdir()
        for index in range(10): (cache / str(index)).write_bytes(b'ab')
        report = measure_paths([cache], max_entries=4)
        self.assertEqual(report['status'], 'partial')
        self.assertFalse(report['complete'])
        self.assertLessEqual(report['visited_entries'], 4)
        self.assertGreater(report['observed_bytes'], 0)
        self.assertLess(report['observed_bytes'], 20)

    def test_unavailable_root_does_not_claim_empty_cache(self):
        with patch('diskpilot.tool_inspection._local_root', return_value=False):
            report = measure_paths([self.root])
        self.assertEqual(report['status'], 'unknown')
        with patch('diskpilot.tool_inspection.candidate_paths', return_value=([self.root], 'default_candidates')), \
             patch('diskpilot.tool_inspection._local_root', return_value=False):
            _, presentation = inspect_metadata('gpu_cache', {})
        self.assertEqual(presentation['metrics'], [])
        self.assertFalse(presentation['complete'])

    def test_absent_location_does_not_emit_zero_size_metric(self):
        with patch('diskpilot.tool_inspection.candidate_paths', return_value=([self.root / 'absent'], 'default_candidates')):
            evidence, presentation = inspect_metadata('uv_cache', {})
        self.assertEqual(evidence['status'], 'not_found')
        self.assertEqual(presentation['metrics'], [])
        self.assertFalse(presentation['complete'])

    def test_thumbnail_scan_only_counts_thumbnail_files(self):
        (self.root / 'thumbcache_32.db').write_bytes(b'123')
        (self.root / 'iconcache_32.db').write_bytes(b'1234567')
        with patch('diskpilot.tool_inspection.candidate_paths', return_value=([self.root], 'default_candidates')):
            report, _ = inspect_metadata('thumbnail_cache', {})
        self.assertEqual(report['observed_bytes'], 3)
        self.assertEqual(report['observed_files'], 1)

    def test_symlink_and_redirected_ancestors_are_not_followed(self):
        destination = self.root / 'destination'; destination.mkdir(); (destination / 'model.bin').write_bytes(b'private')
        link = self.root / 'link'
        try: link.symlink_to(destination, target_is_directory=True)
        except OSError: self.skipTest('symlinks require developer mode or privilege')
        for path in (link, link / 'model.bin'):
            report = measure_paths([path])
            self.assertFalse(report['complete'])
            self.assertEqual(report['observed_bytes'], 0)

    def test_recycle_meter_is_current_user_selected_volume_and_total_volume_denominator(self):
        evidence = dict(capacity=dict(total_bytes=1000, free_bytes=400, available_to_current_user_bytes=350),
                        recycle_bin=dict(query_succeeded=True, size_bytes=25, item_count=3))
        report = volume_presentation('recycle_bin', {'volume': 'E:'}, evidence)
        self.assertEqual(report['recycle'], dict(bytes=25, count=3, volume_total_bytes=1000, volume_free_bytes=350, volume='E:'))
        self.assertEqual(report['metrics'][0]['denominator'], 1000)
        self.assertEqual(report['metrics'][0]['denominator_label'], '占此卷总容量')
        self.assertTrue(report['complete'])

    def test_recycle_unavailable_omits_unknown_size_instead_of_zero(self):
        report = volume_presentation('recycle_bin', {'volume': 'C:'}, dict(recycle_bin=dict(status='unknown')))
        self.assertEqual(report['status'], 'unknown')
        self.assertIsNone(report['recycle']['bytes'])
        self.assertFalse(any(row['key'] == 'recycle_bytes' for row in report['metrics']))

    def test_browser_model_scope_and_volume_filter_are_respected(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root), 'TEMP': str(self.root / 'Temp'), 'WINDIR': str(self.root / 'Windows')}):
            chrome, _ = candidate_paths('browser_models', {'browser': 'chrome'})
            edge, _ = candidate_paths('browser_models', {'browser': 'edge'})
            self.assertTrue(all('Chrome' in str(p) and 'Edge' not in str(p) for p in chrome))
            self.assertTrue(all('Edge' in str(p) and 'Chrome' not in str(p) for p in edge))
            self.assertEqual(candidate_paths('windows_temp', {'volume': 'Z:'})[0], [])

    def test_env_cache_override_is_used_but_network_path_is_not_walked(self):
        with patch.dict(os.environ, {'UV_CACHE_DIR': str(self.root / 'relocated')}):
            paths, source = candidate_paths('uv_cache', {})
        self.assertEqual(paths, [self.root / 'relocated']); self.assertEqual(source, 'environment')
        with patch.dict(os.environ, {'UV_CACHE_DIR': r'\\server\share\cache', 'LOCALAPPDATA': str(self.root)}):
            paths, source = candidate_paths('uv_cache', {})
        self.assertNotIn('server', str(paths)); self.assertEqual(source, 'default_candidates')

    def test_memory_projection_keeps_physical_and_commit_quantities_separate(self):
        evidence = dict(memory=dict(status='observed', physical_total_bytes=32, physical_available_bytes=12,
                        commit_current_bytes=25, commit_limit_bytes=64),
                        active_pagefiles=dict(items=[dict(volume='E:', allocated_bytes=32)]))
        report = memory_presentation('pagefile', evidence)
        self.assertEqual(report['metrics'][2]['denominator'], 64)
        self.assertEqual(report['items'][0], dict(label='E:', value=32, unit='bytes'))
        self.assertTrue(report['complete'])

    def test_browser_privacy_does_not_read_private_bodies(self):
        with patch('diskpilot.tool_inspection.find_program', return_value='fixture.exe'), \
             patch.object(Path, 'open', side_effect=AssertionError('no content')):
            evidence, report = inspect_metadata('browser_privacy', {'browser': 'chrome'})
        self.assertFalse(evidence['reads_private_data'])
        self.assertEqual(report['kind'], 'application')


class ToolInspectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.state = SimulationState(Path(self.temp.name) / 'data'); self.addCleanup(self.state.close)
        self.tools = CleanupToolkit(self.state)

    def test_missing_required_location_needs_input_without_scanning_or_plan(self):
        with patch('diskpilot.cleanup_tools.inspect_metadata', side_effect=AssertionError('no scan')):
            report = self.tools.inspect('cargo_cache')
        self.assertEqual(report['presentation']['status'], 'needs_input')
        self.assertEqual(self.tools.history(), [])
        with self.assertRaises(AppError): self.tools.prepare('cargo_cache')

    def test_launch_reference_uses_host_entry_and_normalized_parameters(self):
        with patch('diskpilot.cleanup_tools.resolve_entry', side_effect=lambda ident, parameters=None: dict(entry_id=ident, parameters=parameters or {})):
            entry = self.tools.entrypoint('windows_temp', 0, {'volume': 'e:'})
            self.assertEqual(entry, dict(entry_id='disk_cleanup', parameters={'volume': 'E:'}))
            self.assertEqual(self.tools.entrypoint('recycle_bin')['entry_id'], 'recycle_bin')
        for index in (True, -1, 999, '0'):
            with self.subTest(index=index), self.assertRaises(AppError): self.tools.entrypoint('windows_temp', index)

    def test_old_version_one_plan_stays_valid_when_only_readers_and_entrypoints_change(self):
        plan = self.tools.prepare('windows_temp', {'volume': 'E:'})
        self.assertEqual(REGISTRY_VERSION, 1)
        with patch('diskpilot.cleanup_tools.resolve_entry', return_value=dict(available=False)):
            self.assertEqual(self.tools.get_plan(plan['id'])['entries'], plan['entries'])
        self.assertEqual(self.tools.execute(plan['id'], 1)['actual_reclaimed_bytes'], 0)

    def test_browser_models_and_project_targets_are_separate_batch_identities(self):
        plan = self.tools.prepare_many([dict(tool_id='browser_models', parameters=dict(browser=browser)) for browser in ('edge', 'chrome')])
        self.assertEqual(len(plan['entries']), 2)

    def test_every_registered_tool_uses_only_virtual_action_runtime(self):
        fixture = Path(self.temp.name) / 'untouched.txt'; fixture.write_bytes(b'fixture remains intact')
        before = fixture.read_bytes()
        with patch('diskpilot.tool_launch.subprocess.Popen', side_effect=AssertionError('execution cannot launch')), \
             patch('diskpilot.tool_launch.os.startfile', create=True, side_effect=AssertionError('execution cannot open settings')):
            for tool_id, row in REGISTRY.items():
                params = {field['name']: (r'E:\owned-fixture' if field.get('format') == 'path' else 'owned-fixture')
                          for field in row['parameters'] if field.get('required') and 'default' not in field}
                with self.subTest(tool_id=tool_id):
                    plan = self.tools.prepare(tool_id, params)
                    result = self.tools.execute(plan['id'], plan['version'])
                    self.assertEqual(result['status'], 'succeeded')
                    self.assertTrue(result['simulation_only'])
                    self.assertEqual(result['actual_reclaimed_bytes'], 0)
        self.assertEqual(fixture.read_bytes(), before)
        plan = self.tools.prepare_many([dict(tool_id='cargo_cache', parameters=dict(project_path=path)) for path in (r'E:\projectA', r'E:\projectB')])
        self.assertEqual(len(plan['entries']), 2)


if __name__ == '__main__':
    unittest.main()
