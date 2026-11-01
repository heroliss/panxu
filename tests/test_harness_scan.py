import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant
from diskpilot.scan_index import ScanIndex
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness


class HarnessScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'fixture'
        self.scope.mkdir()
        for index in range(220):
            (self.scope / f'data-{index:03d}.bin').write_bytes(b'disposable')
        self.state = SimulationState(self.root / 'store')
        self.addCleanup(self.state.close)
        self.index = ScanIndex(self.root / 'store' / 'scans.sqlite3')
        self.addCleanup(self.index.close)
        self.assistant = ModelAssistant(self.root / 'store')
        self.harness = TaskHarness(self.state, self.assistant, scan_index=self.index)
        self.report = scan_directory(str(self.scope))
        self.state.publish(self.report['items'], 'fixture')
        self.directory = next(i for i in self.report['items'] if i['kind'] == 'directory')
        self.task = self.harness.create_analysis({'ids': [self.directory['id']], 'depth': 'detailed'})

    def tool(self, name, args):
        return self.harness.tool(self.task['id'], name, args, cancel=threading.Event())

    def begin_index(self):
        partial = copy.deepcopy(self.report)
        partial['summary']['output_truncated'] = True
        partial['items'] = partial['items'][:15]
        with patch('diskpilot.tasks.scan_directory', return_value=partial):
            return self.tool('inspect_directory', {'id': self.directory['id']})

    def test_multiple_acquisition_pages_publish_without_losing_previous_objects(self):
        result = self.begin_index()
        scan_id = result['scan_id']
        first = set(self.harness._get(self.task['id'])['known_ids'])
        for _ in range(20):
            result = self.tool('continue_scan', {'scan_id': scan_id, 'max_entries': 128})
            if result['coverage']['complete'] and result['published_count'] == 221:
                break
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(result['published_count'], 221)
        task = self.harness._get(self.task['id'])
        self.assertTrue(first <= set(task['known_ids']))
        self.assertEqual(len(task['known_ids']), 221)
        self.assertEqual(len(self.state.view()['items']), 221)
        self.assertTrue(self.harness.public(task['id'])['analysis']['directory_coverage_complete'])
        root = next(i for i in self.state.view()['items'] if i['kind'] == 'directory')
        self.assertEqual(root['actions'], [])

    def test_scans_resume_after_reopen_without_repeating_legacy_prefix(self):
        result = self.begin_index()
        scan_id = result['scan_id']
        known = set(self.harness._get(self.task['id'])['known_ids'])
        self.index.close()
        reopened = ScanIndex(self.root / 'store' / 'scans.sqlite3')
        self.addCleanup(reopened.close)
        self.harness = TaskHarness(self.state, self.assistant, scan_index=reopened)
        with patch('diskpilot.tasks.scan_directory', side_effect=AssertionError('must not restart prefix')):
            result = self.tool('inspect_directory', {'id': self.directory['id']})
        self.assertEqual(result['scan_id'], scan_id)
        self.assertTrue(known <= set(self.harness._get(self.task['id'])['known_ids']))

    def test_observation_index_cannot_enable_whole_directory_without_validation(self):
        result = self.begin_index()
        self.tool('validate_directory', {'id': self.directory['id']})
        item = next(i for i in self.state.view()['items'] if i['id'] == self.directory['id'])
        self.assertIn('delete', item['actions'])
        self.assertTrue(item['signature'].startswith('subtree-sha256:'))

    def test_scan_ids_cannot_be_used_by_other_tasks_or_file_sibling_scopes(self):
        result = self.begin_index()
        file = next(i for i in self.report['items'] if i['kind'] == 'file')
        other = self.harness.create_analysis({'ids': [file['id']]})
        for name, args in (('continue_scan', {'scan_id': result['scan_id']}), ('read_scan', {'scan_id': result['scan_id']})):
            with self.assertRaises(AppError):
                self.harness.tool(other['id'], name, args, cancel=threading.Event())


if __name__ == '__main__':
    unittest.main()
