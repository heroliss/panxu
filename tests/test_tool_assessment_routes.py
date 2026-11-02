"""HTTP dispatch integration for checked tools; fixtures remain unchanged."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.server import Application
from diskpilot.simulation import AppError


def observation():
    return dict(at='2026-11-03T00:00:00+00:00',
        evidence=dict(status='observed', complete=True, observed_bytes=1024,
            observed_files=2, coverage_paths=[r'C:\owned-cache'], coverage_complete=True),
        presentation=dict(kind='cache', status='observed', complete=True,
            metrics=[dict(key='observed_bytes', value=1024), dict(key='observed_files', value=2)],
            items=[], warnings=[]))


class ToolAssessmentRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = Application(Path(temporary.name) / 'data')
        self.addCleanup(self.app.close)

    def checked(self):
        with patch.object(self.app.cleanup_tools, 'inspect', return_value=observation()):
            return self.app.post('/api/cleanup-tools/assess', {'tool_id': 'gpu_cache', 'parameters': {}})

    @staticmethod
    def ref(row):
        return dict(row['tool_ref'], inspection_id=row['inspection_id'])

    def test_catalog_is_immediate_and_does_not_probe_native_entries_or_call_model(self):
        with patch.object(self.app.cleanup_tools, 'inspect', side_effect=AssertionError('unexpected scan')), \
             patch('diskpilot.cleanup_tools.resolve_entry', side_effect=AssertionError('native probe')), \
             patch.object(self.app.assistant, 'agent_turn', side_effect=AssertionError('model call')):
            result = self.app.get('/api/cleanup-tools/assessments', {})
        self.assertEqual(result['total'], 48)
        self.assertTrue(all(row['inspection']['status'] in ('uninspected', 'needs_input') for row in result['items']))

    def test_checked_summary_and_prepared_execution_share_snapshot_and_invalidate_after_receipt(self):
        row = self.checked()
        request = {'items': [self.ref(row)]}
        with patch.object(self.app.cleanup_tools, 'inspect', side_effect=AssertionError('unexpected rescan')):
            summary = self.app.post('/api/cleanup-tools/assessments/summary', request)
            plan = self.app.post('/api/cleanup-tools/prepare-selection', request)
        self.assertEqual(summary['estimated_reclaimable_bytes'], 1024)
        self.assertEqual(plan['entries'][0]['parameters'], row['tool_ref']['parameters'])
        receipt = self.app.post('/api/cleanup-tools/execute', {'plan_id': plan['id'], 'version': plan['version']})
        self.assertEqual(receipt['actual_reclaimed_bytes'], 0)
        refreshed = self.app.get('/api/cleanup-tools/assessments', {'query': ['gpu_cache']})['items'][0]
        self.assertFalse(refreshed['can_execute'])
        with self.assertRaises(AppError):
            self.app.post('/api/cleanup-tools/prepare-selection', request)

    def test_tool_analysis_uses_existing_job_dispatch_without_file_catalog_insertion(self):
        row = self.checked()
        before = self.app.catalog.token()
        with patch.object(self.app, 'start_job', return_value={'job_id': 'owned-job'}) as jobs:
            response = self.app.post('/api/tasks/tool-analysis', {'items': [self.ref(row)]})
        task = response['task']
        jobs.assert_called_once_with('agent', {'task_id': task['id']})
        self.assertEqual(task['scope_kind'], 'cleanup_tools')
        self.assertEqual(task['analysis']['section'], 'cleanup_tools')
        self.assertEqual(task['cleanup_tool_targets'][0]['tool_ref']['tool_id'], 'gpu_cache')
        self.assertEqual(self.app.catalog.token(), before)
        self.assertEqual(self.app.cleanup_tools.history(), [])

    def test_malformed_routes_and_stale_or_foreign_targets_do_not_prepare_operations(self):
        row = self.checked()
        requests = [
            ('/api/cleanup-tools/assess', {'tool_id': 'gpu_cache', 'refresh': 'yes'}),
            ('/api/cleanup-tools/assess', {'tool_id': 'gpu_cache', 'command': 'anything'}),
            ('/api/cleanup-tools/assessments/summary', {'items': [], 'execute': True}),
            ('/api/cleanup-tools/prepare-selection', {'items': [row['tool_ref']]}),
            ('/api/cleanup-tools/prepare-selection', {'items': [self.ref(row)], 'source': {'task_id': 'foreign'}}),
            ('/api/tasks/tool-analysis', {'items': [dict(self.ref(row), inspection_id='foreign')]}),
        ]
        for route, body in requests:
            with self.subTest(route=route, body=body), self.assertRaises(AppError):
                self.app.post(route, body)
        self.assertEqual(self.app.cleanup_tools.history(), [])


if __name__ == '__main__':
    unittest.main()
