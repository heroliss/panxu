"""Shared diagnosis uses owned metadata and explicit unknown/partial states."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.cleanup_tools import CleanupToolkit, REGISTRY
from diskpilot.diagnostics import DiagnosticsStore, _applicability, MAX_DETAIL_PAGE
from diskpilot.simulation import AppError, SimulationState


MEMORY = dict(memory=dict(status='observed', physical_total_bytes=4096, physical_available_bytes=2048,
    commit_current_bytes=1000, commit_limit_bytes=8192),
    power_capabilities=dict(status='observed', hibernation_supported=True, hibernation_file_present=True),
    active_pagefiles=dict(status='observed', items=[]))
VOLUME = dict(capacity=dict(status='observed', total_bytes=10000, available_to_current_user_bytes=5000),
              recycle_bin=dict(query_succeeded=True, size_bytes=0, item_count=0))


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-diagnostics-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'scope'; self.scope.mkdir()
        self.cache = self.scope / 'cache'; self.cache.mkdir()
        (self.cache / 'sample.tmp').write_bytes(b'owned bytes')
        self.state = SimulationState(self.root / 'profile')
        self.addCleanup(self.state.close)
        self.toolkit = CleanupToolkit(self.state)
        self.store = DiagnosticsStore(self.state, self.toolkit)
        self.system = patch('diskpilot.diagnostics.system_configuration', return_value=copy.deepcopy(MEMORY))
        self.volume = patch('diskpilot.diagnostics.volume_snapshot', return_value=copy.deepcopy(VOLUME))
        self.memory_probe = self.system.start(); self.volume_probe = self.volume.start()
        self.addCleanup(self.system.stop); self.addCleanup(self.volume.stop)

    def scan(self, candidates=None, **kwargs):
        with patch('diskpilot.diagnostics.candidate_paths', side_effect=candidates or (lambda *_: ([self.cache], 'owned'))):
            return self.store.scan([str(self.scope)], task_id='owned-task', **kwargs)

    def row(self, snapshot, tool_id):
        return next(row for row in snapshot['items'] if row['tool_id'] == tool_id)

    def test_describe_exposes_field_names_types_defaults_enums_without_program_probe(self):
        with patch('diskpilot.cleanup_tools.resolve_entry', side_effect=AssertionError('describe is metadata only')):
            result = self.toolkit.describe('unity_cache')
        schema = result['input_schema']
        self.assertEqual(schema['required'], ['project_path'])
        self.assertEqual(schema['properties']['project_path']['type'], 'string')
        self.assertEqual(schema['properties']['project_path']['format'], 'path')
        self.assertFalse(schema['additionalProperties'])
        browser = self.toolkit.describe('browser_cache')['input_schema']['properties']['browser']
        self.assertEqual(browser['default'], 'edge')
        self.assertEqual(browser['enum'], ['edge', 'chrome', 'firefox'])
        result['parameters'][0]['name'] = 'modified'
        self.assertEqual(REGISTRY['unity_cache']['parameters'][0]['name'], 'project_path')
        self.assertEqual(self.state.history(), [])

    def test_inspect_missing_parameter_names_and_schema_are_machine_readable(self):
        with patch('diskpilot.cleanup_tools.inspect_metadata', side_effect=AssertionError('no scan without target')):
            result = self.toolkit.inspect('unity_cache')
        self.assertEqual(result['missing_parameters'], ['project_path'])
        self.assertEqual(result['parameter_schema']['required'], ['project_path'])
        self.assertEqual(result['presentation']['status'], 'needs_input')
        self.assertTrue(result['read_only'])

    def test_shared_system_probes_and_repeated_cache_measurement_are_not_repeated_per_tool(self):
        from diskpilot.tool_inspection import measure_paths
        with patch('diskpilot.diagnostics.measure_paths', wraps=measure_paths) as measure:
            result = self.scan()
        self.assertEqual(self.memory_probe.call_count, 1)
        self.assertEqual(self.volume_probe.call_count, 1 if ':' in str(self.scope) else 0)
        self.assertEqual(measure.call_count, 2)  # Ordinary cache and thumbnail prefix variants.
        self.assertEqual(len(result['items']), 48)
        self.assertEqual(len({row['tool_id'] for row in result['items']}), 48)
        self.assertTrue(result['coverage']['registry_complete'])
        self.assertFalse(result['coverage']['all_tools_fully_diagnosed'])
        self.assertFalse(result['coverage']['reads_file_contents'])
        self.assertTrue(self.row(result, 'uv_cache')['candidate'])
        self.assertEqual(self.row(result, 'unity_cache')['applicability'], 'needs_input')
        self.assertEqual(self.row(result, 'docker_cache')['status'], 'unknown')
        self.assertEqual(self.state.history(), [])

    def test_summary_omits_paths_private_details_and_duplicates_of_system_facts(self):
        result = self.scan()
        self.assertNotIn(str(self.scope), json.dumps(result, ensure_ascii=False))
        self.assertNotIn('_sources', result)
        page = self.store.read(result['snapshot_id'], task_id='owned-task', tool_ids=['pagefile', 'hibernation'])
        self.assertEqual(len(page['items']), 2)
        self.assertEqual(list(page['sources']), ['memory_power'])
        self.assertEqual(page['sources']['memory_power'], MEMORY)
        self.assertTrue(all(row['details'][0]['evidence'] == {} for row in page['items']))
        self.assertEqual(page['total'], 2)

    def test_snapshot_is_durable_and_cannot_be_read_under_another_task(self):
        result = self.scan()
        second = DiagnosticsStore(self.state, self.toolkit)
        self.assertEqual(second.summary(result['snapshot_id'], task_id='owned-task'), result)
        with self.assertRaises(AppError) as error:
            second.read(result['snapshot_id'], task_id='other-task')
        self.assertEqual(error.exception.code, 'outside_scope')
        self.assertEqual(second.read(result['snapshot_id'], offset=20, limit=7)['offset'], 20)
        self.assertTrue(second.read(result['snapshot_id'], offset=20, limit=7)['has_more'])

    def test_absent_or_outside_locations_are_not_zero_or_not_installed(self):
        absent = self.scope / 'missing'
        result = self.scan(lambda tool, _: ([absent] if tool == 'uv_cache' else [self.root / 'outside'], 'owned'))
        missing, outside = self.row(result, 'uv_cache'), self.row(result, 'pip_cache')
        self.assertEqual(missing['status'], 'not_found')
        self.assertEqual(missing['applicability'], 'unknown')
        self.assertIsNone(missing['candidate'])
        self.assertFalse(missing['complete'])
        self.assertEqual(missing['variants'][0]['metrics'], [])
        self.assertEqual(outside['status'], 'not_applicable')
        self.assertIsNone(outside['candidate'])
        self.assertEqual(outside['variants'][0]['scope_status'], 'outside_scope')

    def test_complete_empty_cache_is_the_only_negative_candidate(self):
        empty = self.scope / 'empty'; empty.mkdir()
        result = self.scan(lambda *_: ([empty], 'owned'))
        row = self.row(result, 'uv_cache')
        self.assertFalse(row['candidate'])
        self.assertTrue(row['complete'])
        self.assertEqual(self.row(result, 'recycle_bin')['candidate'], False if ':' in str(self.scope) else None)
        self.assertIsNone(self.row(result, 'system_restore')['candidate'])

    def test_partial_metadata_is_a_positive_lower_bound_not_complete_empty(self):
        observed = dict(status='partial', complete=False, observed_bytes=100, observed_files=1,
                        roots_found=1, visited_entries=200, reads_file_contents=False)
        with patch('diskpilot.diagnostics.measure_paths', return_value=observed):
            result = self.scan()
        row = self.row(result, 'uv_cache')
        self.assertTrue(row['candidate'])
        self.assertFalse(row['complete'])
        self.assertEqual(row['status'], 'partial')
        self.assertLessEqual(result['coverage']['budget']['visited_entries'], 1200)

    def test_metadata_budget_and_cancellation_do_not_invoke_full_specialist_inspect(self):
        cancelled = threading.Event(); cancelled.set()
        with (patch.object(self.toolkit, 'inspect', side_effect=AssertionError('no48 full scans')),
              patch('diskpilot.diagnostics.measure_paths', side_effect=AssertionError('cancelled'))):
            result = self.scan(cancel=cancelled)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(len(result['items']), 48)
        self.assertEqual(result['coverage']['shared_probe_count'], 0)
        self.assertTrue(all(row['candidate'] is None for row in result['items']))
        self.assertGreater(result['coverage']['not_checked'], 0)

    def test_files_are_not_opened_and_scope_never_expands_to_other_cache_roots(self):
        with (patch.object(Path, 'open', side_effect=AssertionError('no bodies')),
              patch('diskpilot.diagnostics.measure_paths', side_effect=AssertionError('outside roots'))):
            result = self.scan(lambda *_: ([self.root / 'outside'], 'owned'))
        self.assertIsNone(self.row(result, 'uv_cache')['candidate'])
        self.assertEqual(result['coverage']['budget']['visited_entries'], 0)
        self.assertEqual((self.cache / 'sample.tmp').read_bytes(), b'owned bytes')

    def test_browser_profile_discovery_does_not_enumerate_outside_the_selected_scope(self):
        def candidates(tool, params):
            if tool == 'browser_cache':
                raise AssertionError('profile enumeration must be scoped before resolver')
            return [self.cache], 'owned'
        with patch.dict('os.environ', {'LOCALAPPDATA': str(self.root / 'outside-profile')}):
            result = self.scan(candidates)
        self.assertEqual(self.row(result, 'browser_cache')['applicability'], 'not_applicable')

    def test_installation_absence_requires_verified_adapter_evidence(self):
        self.assertEqual(_applicability(dict(status='not_found'), 'in_scope', {}), 'unknown')
        self.assertEqual(_applicability(dict(status='not_installed'), 'in_scope', {}), 'unknown')
        self.assertEqual(_applicability(dict(status='not_installed'), 'in_scope', {'installation_verified': True}), 'not_installed')

    def test_invalid_inputs_are_rejected_without_probes_or_scan(self):
        for paths in ([], ['relative'], ['\\\\server\\share'], ['C:\\a\x00b'], ['C:relative'], ['C:\\'] * 9):
            with self.subTest(paths=paths), self.assertRaises(AppError):
                self.store.scan(paths)
        self.memory_probe.assert_not_called()
        result = self.scan()
        for arguments in (dict(limit=MAX_DETAIL_PAGE+1), dict(offset=-1), dict(tool_ids=['wrong']), dict(tool_ids=[{}])):
            with self.subTest(arguments=arguments), self.assertRaises(AppError):
                self.store.read(result['snapshot_id'], **arguments)


if __name__ == '__main__':
    unittest.main()
