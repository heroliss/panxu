"""State projections use owned fixtures, never real cleanup or model requests."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from diskpilot.cleanup_tools import CleanupToolkit
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tool_assessments import ToolAssessments


def observed(size=100, count=2, path=r'C:\cache', *, complete=True, status=None):
    status = status or ('observed' if complete else 'partial')
    return dict(at='2026-11-03T00:00:00+00:00', evidence=dict(status=status, complete=complete,
        observed_bytes=size, observed_files=count, roots_found=1,
        coverage_paths=[path], coverage_complete=True),
        presentation=dict(kind='cache', status=status, complete=complete,
            metrics=[dict(key='observed_bytes', value=size), dict(key='observed_files', value=count)],
            items=[], warnings=[]))


def snapshot(row):
    return dict(row['tool_ref'], inspection_id=row['inspection_id'])


class ToolAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = SimulationState(Path(self.temp.name) / 'data')
        self.addCleanup(self.state.close)
        self.tools = CleanupToolkit(self.state)
        self.tick = 100.0
        self.service = ToolAssessments(self.tools, clock=lambda: self.tick)
        self.probe = patch.object(self.tools, 'inspect', return_value=observed()).start()
        self.addCleanup(patch.stopall)

    def assertCode(self, code, call):
        with self.assertRaises(AppError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_catalog_has_all_48_fixed_explanations_without_probe_or_native_resolution(self):
        with patch('diskpilot.cleanup_tools.resolve_entry', side_effect=AssertionError('unexpected program probe')):
            rows = self.service.catalog()['items']
        self.probe.assert_not_called()
        self.assertEqual(len(rows), 48)
        self.assertEqual(len({row['id'] for row in rows}), 48)
        for row in rows:
            rec = row['recommendation']
            self.assertTrue(all(rec[key] for key in ('purpose', 'reason', 'advice', 'impact', 'recovery')))
            self.assertIsNone(rec['rating'])
            self.assertIsNone(row['benefit']['estimated_reclaimable_bytes'])
            self.assertFalse(row['can_execute'])
            self.assertTrue(row['selectable'])
            self.assertEqual(rec['source'], 'builtin')

    def test_complete_observation_is_only_logical_upper_bound(self):
        row = self.service.inspect('gpu_cache')
        self.assertEqual(row['benefit']['observed_bytes'], 100)
        self.assertEqual(row['benefit']['estimated_reclaimable_bytes'], 100)
        self.assertEqual(row['benefit']['estimate_kind'], 'logical_upper_bound')
        self.assertEqual(row['recommendation']['rating'], 4)
        self.assertTrue(row['recommendation']['eligible_auto_select'])
        self.assertTrue(row['can_execute'])
        self.assertNotIn('actual_reclaimed_bytes', row['benefit'])

    def test_fixed_advice_is_short_action_direction_separate_from_diagnostic_reason(self):
        row = self.service.inspect('gpu_cache')
        self.assertEqual(row['recommendation']['advice'], '可按需清理')
        self.assertNotEqual(row['recommendation']['advice'], row['recommendation']['reason'])
        self.probe.return_value = observed(0, 0)
        empty = self.service.inspect('gpu_cache', refresh=True)
        self.assertEqual(empty['recommendation']['advice'], '当前无需清理')
        self.probe.return_value = observed(complete=False)
        partial = self.service.inspect('gpu_cache', refresh=True)
        self.assertEqual(partial['recommendation']['advice'], '先核对状态')
        missing = self.service.inspect('unity_cache')
        self.assertEqual(missing['recommendation']['advice'], '先补充选项')

    def test_partial_is_lower_bound_and_not_executable_or_autoselected(self):
        self.probe.return_value = observed(500, 7, complete=False)
        row = self.service.inspect('gpu_cache')
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(row['benefit']['observed_bytes'], 500)
        self.assertIsNone(row['benefit']['estimated_reclaimable_bytes'])
        self.assertFalse(row['can_execute'])
        self.assertFalse(row['recommendation']['eligible_auto_select'])
        self.assertIsNone(row['recommendation']['rating'])

    def test_unknown_or_absent_is_not_zero(self):
        for status in ('unknown', 'not_found'):
            self.probe.return_value = observed(0, 0, status=status)
            row = self.service.inspect('gpu_cache', refresh=True)
            self.assertIsNone(row['benefit']['observed_bytes'])
            self.assertIsNone(row['benefit']['count'])
            self.assertFalse(row['can_execute'])
            self.assertIsNone(row['recommendation']['rating'])

    def test_known_empty_is_not_cleanup_candidate(self):
        self.probe.return_value = observed(0, 0)
        row = self.service.inspect('gpu_cache')
        self.assertEqual(row['benefit']['estimated_reclaimable_bytes'], 0)
        self.assertEqual(row['recommendation']['rating'], 1)
        self.assertFalse(row['can_execute'])

    def test_missing_project_parameter_is_explicit(self):
        row = self.service.inspect('unity_cache')
        self.assertEqual(row['status'], 'needs_input')
        self.assertEqual(row['missing_parameters'], ['project_path'])
        self.assertTrue(row['requires_parameters'])
        self.assertFalse(row['can_execute'])
        self.assertIsNone(row['benefit']['observed_bytes'])

    def test_models_private_data_and_configuration_do_not_autoselect(self):
        for tool_id, params in [('browser_models', {}), ('hf_cache', {}),
                                ('browser_privacy', {}), ('pagefile', {'mode': 'system', 'volume': 'E:'})]:
            row = self.service.inspect(tool_id, params)
            self.assertFalse(row['recommendation']['eligible_auto_select'], tool_id)
        self.assertFalse(row['can_execute'])

    def test_configuration_requires_explicit_choice_even_with_registry_defaults(self):
        row = self.service.inspect('hibernation')
        self.assertTrue(row['requires_parameters'])
        self.assertIn('mode', row['missing_parameters'])
        self.assertFalse(row['can_execute'])
        self.assertFalse(self.service.inspect('hibernation', {'mode': 'full'})['requires_parameters'])

    def test_subset_observation_cannot_confirm_cleanup_candidates(self):
        for ident, params in [('uv_cache', {'operation': 'prune'}), ('pip_cache', {'operation': 'wheels'}),
                              ('browser_cache', {'time_range': 'last_hour'})]:
            row = self.service.inspect(ident, params)
            self.assertFalse(row['can_execute'], ident)
            self.assertFalse(row['recommendation']['eligible_auto_select'], ident)

    def test_unsupported_hibernation_is_not_applicable(self):
        self.probe.return_value = dict(evidence=dict(power_capabilities=dict(
            status='observed', hibernation_supported=False)), presentation=dict(status='observed', complete=True))
        row = self.service.inspect('hibernation', {'mode': 'full'})
        self.assertEqual(row['status'], 'not_applicable')
        self.assertEqual(row['recommendation']['rating'], 1)
        self.assertFalse(row['can_execute'])

    def test_diagnostic_uses_no_disk_savings_claim(self):
        row = self.service.inspect('npm_cache', {'operation': 'verify'})
        self.assertEqual(row['benefit']['kind'], 'diagnostic')
        self.assertEqual(row['benefit']['estimate_kind'], 'not_applicable')
        self.assertIsNone(row['benefit']['estimated_reclaimable_bytes'])
        self.assertFalse(row['can_execute'])

    def test_cache_scope_timestamp_and_strict_expiration(self):
        first = self.service.inspect('windows_temp', {'volume': 'C:'})
        again = self.service.inspect('windows_temp', {'volume': 'C:'})
        self.assertEqual(self.probe.call_count, 1)
        self.assertEqual(first['inspection_id'], again['inspection_id'])
        self.assertEqual(first['inspection']['at'], again['inspection']['at'])
        self.assertTrue(again['inspection']['cached'])
        other = self.service.inspect('windows_temp', {'volume': 'E:'})
        self.assertNotEqual(first['id'], other['id'])
        self.tick += 60
        stale = self.service.catalog()['items'][0]
        self.assertEqual(stale['tool_id'], 'windows_temp')
        self.assertTrue(stale['inspection']['stale'])
        self.assertEqual(stale['inspection']['at'], first['inspection']['at'])
        self.assertIsNone(stale['benefit']['estimated_reclaimable_bytes'])
        self.assertFalse(self.service.is_current(first['tool_ref'], first['inspection_id']))
        self.assertCode('tool_inspection_changed', lambda: self.service.prepare_selection([snapshot(first)]))

    def test_refresh_replaces_snapshot_but_identical_evidence_keeps_signature(self):
        first = self.service.inspect('gpu_cache')
        refreshed = self.service.inspect('gpu_cache', refresh=True)
        self.assertEqual(first['id'], refreshed['id'])
        self.assertEqual(first['signature'], refreshed['signature'])
        self.assertNotEqual(first['inspection_id'], refreshed['inspection_id'])
        self.assertCode('tool_inspection_changed', lambda: self.service.freeze_targets([snapshot(first)]))

    def test_long_running_inspection_is_not_relabelled_fresh_on_completion(self):
        def probe(*args):
            self.tick += 61
            return observed()
        self.probe.side_effect = probe
        row = self.service.inspect('gpu_cache')
        self.assertTrue(row['inspection']['stale'])
        self.assertFalse(row['can_execute'])
        self.assertFalse(self.service.is_current(row['tool_ref'], row['inspection_id']))

    def test_cache_and_frozen_targets_cannot_mutate_cached_facts(self):
        first = self.service.inspect('gpu_cache')
        frozen = self.service.freeze_targets([snapshot(first)])[0]
        frozen['assessment']['benefit']['observed_bytes'] = 99
        frozen['diagnostic']['evidence']['observed_bytes'] = 98
        first['tool_ref']['parameters']['bad'] = True
        row = self.service.inspect('gpu_cache')
        self.assertEqual(row['benefit']['observed_bytes'], 100)
        self.assertNotIn('bad', row['tool_ref']['parameters'])

    def test_exact_scope_and_current_snapshot_needed_for_preparation(self):
        row = self.service.inspect('gpu_cache')
        self.assertCode('tool_inspection_changed', lambda: self.service.prepare_selection([row['tool_ref']]))
        self.assertCode('tool_inspection_changed', lambda: self.service.prepare_selection([
            dict(tool_id='recycle_bin', parameters={'volume': 'E:'}, inspection_id=row['inspection_id'])]))
        plan = self.service.prepare_selection([snapshot(row)], source={'task_id': 'fixture'})
        self.assertTrue(plan['simulation_only'])
        self.assertEqual(plan['entries'][0]['tool_id'], 'gpu_cache')
        self.assertNotIn('inspection_id', plan['entries'][0]['parameters'])
        self.assertEqual(plan['source'], {'task_id': 'fixture'})
        self.assertEqual(plan['actual_reclaimed_bytes'], 0)

    def test_unknown_cannot_be_mistaken_for_manual_authority(self):
        self.probe.return_value = observed(status='unknown')
        row = self.service.inspect('gpu_cache')
        self.assertTrue(self.service.freeze_targets([snapshot(row)]))
        self.assertCode('tool_not_actionable', lambda: self.service.prepare_selection([snapshot(row)]))

    def test_overlap_not_added_or_prepared(self):
        first = self.service.inspect('gpu_cache')
        self.probe.return_value = observed(300, 3, r'C:\cache\nested')
        second = self.service.inspect('thumbnail_cache')
        summary = self.service.summarize([snapshot(first), snapshot(second)])
        self.assertEqual(summary['overlap_count'], 1)
        self.assertIsNone(summary['estimated_reclaimable_bytes'])
        self.assertFalse(summary['estimate_complete'])
        self.assertCode('tool_selection_overlap', lambda: self.service.prepare_selection([snapshot(first), snapshot(second)]))

    def test_disjoint_upper_bounds_can_be_summed_without_claiming_actual_savings(self):
        first = self.service.inspect('gpu_cache')
        self.probe.return_value = observed(300, 3, r'E:\other')
        second = self.service.inspect('thumbnail_cache')
        summary = self.service.summarize([snapshot(first), snapshot(first), snapshot(second)])
        self.assertEqual(summary['selected_count'], 2)
        self.assertEqual(summary['estimated_reclaimable_bytes'], 400)
        self.assertTrue(summary['estimate_complete'])
        self.assertEqual(summary['actual_reclaimed_bytes'], 0)

    def test_unknown_coverage_or_partial_not_in_estimate_sum(self):
        first = self.service.inspect('gpu_cache')
        self.probe.return_value = observed(900, 5, r'E:\other', complete=False)
        second = self.service.inspect('thumbnail_cache')
        summary = self.service.summarize([snapshot(first), snapshot(second)])
        self.assertEqual(summary['estimated_reclaimable_bytes'], 100)
        self.assertEqual(summary['unknown_count'], 1)
        self.assertFalse(summary['estimate_complete'])

    def test_file_aliases_across_disjoint_roots_are_not_double_counted(self):
        result = observed()
        result['evidence']['observed_file_keys'] = ['same-filesystem-identity']
        self.probe.return_value = result
        first = self.service.inspect('gpu_cache')
        result = observed(path=r'C:\different')
        result['evidence']['observed_file_keys'] = ['same-filesystem-identity']
        self.probe.return_value = result
        second = self.service.inspect('thumbnail_cache')
        summary = self.service.summarize([snapshot(first), snapshot(second)])
        self.assertEqual(summary['overlap_count'], 1)
        self.assertIsNone(summary['estimated_reclaimable_bytes'])

    def test_bounded_cache_and_invalidation(self):
        service = ToolAssessments(self.tools, max_entries=2, clock=lambda: self.tick)
        rows = [service.inspect(ident) for ident in ('gpu_cache', 'thumbnail_cache', 'steam_cache')]
        self.assertEqual(len(service._cache), 2)
        self.assertFalse(service.is_current(rows[0]['tool_ref'], rows[0]['inspection_id']))
        service.invalidate(['thumbnail_cache'])
        self.assertFalse(service.is_current(rows[1]['tool_ref'], rows[1]['inspection_id']))
        self.assertTrue(service.is_current(rows[2]['tool_ref'], rows[2]['inspection_id']))
        service.invalidate()
        self.assertEqual(len(service._cache), 0)

    def test_invalidation_during_probe_cannot_publish_old_facts(self):
        started, release = threading.Event(), threading.Event()
        def probe(*args):
            started.set()
            self.assertTrue(release.wait(3))
            return observed()
        self.probe.side_effect = probe
        result = []
        worker = threading.Thread(target=lambda: result.append(self.service.inspect('gpu_cache')))
        worker.start()
        self.assertTrue(started.wait(3))
        self.service.invalidate()
        release.set(); worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result[0]['inspection']['stale'])
        self.assertEqual(len(self.service._cache), 0)

    def test_late_older_same_scope_probe_does_not_replace_newer_probe(self):
        started, release = threading.Event(), threading.Event()
        def probe(*args):
            if not started.is_set():
                started.set()
                self.assertTrue(release.wait(3))
                return observed(111)
            return observed(222)
        self.probe.side_effect = probe
        result = []
        worker = threading.Thread(target=lambda: result.append(self.service.inspect('gpu_cache')))
        worker.start(); self.assertTrue(started.wait(3))
        new = self.service.inspect('gpu_cache', refresh=True)
        release.set(); worker.join(3)
        self.assertTrue(result[0]['inspection']['stale'])
        self.assertEqual(self.service.inspect('gpu_cache')['inspection_id'], new['inspection_id'])
        self.assertEqual(self.service.inspect('gpu_cache')['benefit']['observed_bytes'], 222)

    def test_ai_advice_provenance_scoped_to_current_evidence_and_never_grants_execution(self):
        row = self.service.inspect('browser_privacy')
        advice = dict(cleanup_rating=5, reason='fixture', purpose='privacy', impact='impact',
            conclusion='optional', at='2026-11-03', signature=row['signature'],
            provenance=dict(source='llm', model='fixture-model'))
        ai = self.service.publish_ai(row['tool_ref'], row['inspection_id'], advice)
        self.assertEqual(ai['source'], 'llm')
        self.assertEqual(ai['recommendation']['rating'], 5)
        self.assertFalse(ai['can_execute'])
        self.assertFalse(ai['recommendation']['eligible_auto_select'])
        self.probe.return_value = observed(200)
        changed = self.service.inspect('browser_privacy', refresh=True)
        self.assertEqual(changed['source'], 'builtin')
        self.assertCode('tool_inspection_changed', lambda: self.service.publish_ai(
            changed['tool_ref'], changed['inspection_id'], advice))

    def test_unknown_or_local_advice_never_relabelled_llm(self):
        row = self.service.inspect('gpu_cache')
        for source in ('unknown', 'local', 'builtin'):
            self.assertCode('invalid_tool_advice', lambda: self.service.publish_ai(
                row['tool_ref'], row['inspection_id'], dict(provenance=dict(source=source))))

    def test_expired_model_advice_remains_readable_without_current_authority(self):
        row = self.service.inspect('gpu_cache')
        advice = dict(cleanup_rating=5, reason='历史清理理由', conclusion='历史建议',
            signature=row['signature'], provenance=dict(source='llm', model='fixture-model'))
        self.service.publish_ai(row['tool_ref'], row['inspection_id'], advice)
        self.tick += 61
        expired = next(row for row in self.service.catalog()['items'] if row['tool_id'] == 'gpu_cache')
        self.assertTrue(expired['inspection']['stale'])
        self.assertEqual(expired['source'], 'llm')
        self.assertEqual(expired['recommendation']['reason'], '历史清理理由')
        self.assertEqual(expired['recommendation']['rating'], 5)
        self.assertTrue(expired['recommendation']['stale'])
        self.assertFalse(expired['recommendation']['eligible_auto_select'])
        self.assertFalse(expired['can_execute'])

    def test_long_model_analysis_can_publish_history_after_ttl(self):
        row = self.service.inspect('gpu_cache')
        self.tick += 61
        expired = self.service.publish_ai(row['tool_ref'], row['inspection_id'], dict(
            cleanup_rating=4, reason='long analysis', conclusion='recommend',
            signature=row['signature'], provenance=dict(source='llm', model='fixture')))
        self.assertEqual(expired['recommendation']['advice'], '建议清理')
        self.assertTrue(expired['inspection']['stale'])
        self.assertFalse(expired['can_execute'])

    def test_all_48_can_be_frozen_for_analysis_but_execution_limit_stays_40(self):
        rows = [self.service.inspect(row['tool_id'], row['tool_ref']['parameters'])
                for row in self.service.catalog()['items']]
        refs = [snapshot(row) for row in rows]
        self.assertEqual(len(self.service.freeze_targets(refs)), 48)
        self.assertEqual(self.service.summarize(refs)['selected_count'], 48)
        self.assertCode('invalid_tool_batch', lambda: self.service.prepare_selection(refs))

    def test_provider_restores_only_genuine_matching_advice(self):
        row = self.service.inspect('gpu_cache')
        advice = dict(tool_ref=row['tool_ref'], signature=row['signature'], cleanup_rating=2,
            provenance=dict(source='llm', model='fixture-model'), reason='keep', at='2026-11-03')
        self.service.advice_provider = Mock(return_value=advice)
        restored = self.service.inspect('gpu_cache')
        self.assertEqual(restored['recommendation']['rating'], 2)
        self.assertEqual(restored['source'], 'llm')
        self.assertFalse(restored['recommendation']['eligible_auto_select'])
        advice['signature'] = 'other'
        self.assertEqual(self.service.inspect('gpu_cache')['source'], 'builtin')

    def test_compact_projection_omits_full_registry_and_raw_evidence(self):
        row = self.service.inspect('gpu_cache')
        projected = self.service.compact(row)
        self.assertNotIn('definition', projected)
        self.assertNotIn('evidence', projected)
        self.assertNotIn('coverage_keys', projected['benefit'])


if __name__ == '__main__':
    unittest.main()
