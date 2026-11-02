"""Read-only directory investigation must not inherit mutation prohibitions."""
from pathlib import Path
import tempfile
import threading
import unittest

from diskpilot.scanner import scan_directory
from diskpilot.server import Application
from diskpilot.simulation import AppError
from diskpilot.tasks import TaskHarness


class DirectoryInvestigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-directory-investigation-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'owned-project'
        (self.project / '.git').mkdir(parents=True)
        (self.project / 'Source').mkdir()
        self.source = self.project / 'Source' / 'keep.txt'
        self.source.write_bytes(b'owned source: unchanged')
        (self.project / '.ssh').mkdir()
        (self.project / '.ssh' / 'owned-key-name').write_bytes(b'not a real credential')
        (self.project / 'keep-dir').mkdir()
        (self.project / 'keep-dir' / 'keep.txt').write_bytes(b'owned protected sample')
        self.app = Application(self.root / 'profile')
        self.addCleanup(self.app.close)
        self.harness = self.app.harness
        self.task_id = self.harness.create('只读分析这个测试项目；保留所有源码。', str(self.project))['id']

    def tool(self, name, arguments):
        return self.harness.tool(self.task_id, name, arguments, cancel=threading.Event())

    def directory(self, name):
        self.tool('survey_scope', {'scope_id': 'root'})
        return next(item for item in self.harness._items(self.harness._get(self.task_id))
                    if item['name'] == name and item['kind'] == 'directory')

    def test_project_protection_keeps_actions_denied_but_allows_metadata_investigation(self):
        directory = self.directory('Source')
        self.assertEqual(directory['risk'], 'protected')
        report = self.tool('inspect_directory', {'id': directory['id']})
        child = next(item for item in report['items'] if item['name'] == 'keep.txt')
        self.assertEqual(child['risk'], 'protected')
        self.assertEqual(child['allowed_actions'], [])
        with self.assertRaises(AppError):
            self.tool('validate_directory', {'id': directory['id']})
        with self.assertRaises(AppError):
            self.harness.prepare_assessment_plan([child['id']], source_task_id=self.task_id)
        self.assertEqual(self.source.read_bytes(), b'owned source: unchanged')
        self.assertEqual(self.app.state.history(), [])

    def test_credential_directory_is_not_deeply_enumerated(self):
        directory = self.directory('.ssh')
        self.assertEqual(directory['category'], 'credentials')
        with self.assertRaises(AppError) as raised:
            self.tool('inspect_directory', {'id': directory['id']})
        self.assertEqual(raised.exception.code, 'directory_private')
        self.assertNotIn('owned-key-name', [item['name'] for item in
            self.harness._items(self.harness._get(self.task_id))])

    def test_user_protected_directory_is_not_deeply_enumerated(self):
        self.app.assistant.save_settings({'protect_patterns': ['*keep-dir*']})
        directory = self.directory('keep-dir')
        self.assertTrue(self.harness._metadata(directory)['user_protected'])
        with self.assertRaises(AppError) as raised:
            self.tool('inspect_directory', {'id': directory['id']})
        self.assertEqual(raised.exception.code, 'directory_private')

    def test_observed_foreign_directory_does_not_expand_task_scope(self):
        outside = self.root / 'outside-task'
        outside.mkdir()
        (outside / 'outside.txt').write_bytes(b'owned outside-scope sample')
        report = scan_directory(str(outside))
        self.app.state.publish(report['items'], 'owned-outside-observation')
        directory = next(item for item in report['items'] if item['kind'] == 'directory')
        with self.assertRaises(AppError) as raised:
            self.tool('inspect_directory', {'id': directory['id']})
        self.assertIn(raised.exception.code, ('invalid_request', 'outside_scope'))

    def test_bad_advice_identifies_row_without_saving_partial_batch(self):
        directory = self.directory('Source')
        entry = dict(id=directory['id'], conclusion='keep', purpose='源码目录',
                     reason='人工样本必须保留', impact='保留资料不变', cleanup_rating=1)
        with self.assertRaises(AppError) as raised:
            self.tool('record_advice', {'items': [entry, {**entry, 'id': 'invented-container'}]})
        self.assertEqual(raised.exception.code, 'unknown_advice_item')
        self.assertIn('第2项', str(raised.exception))
        self.assertIn('list_items', str(raised.exception))
        self.assertEqual(self.harness.public(self.task_id)['advice'], [])

    def test_child_investigation_keeps_root_id_but_invalidates_old_aggregate(self):
        ordinary = self.root / 'ordinary'
        child = ordinary / 'child'
        child.mkdir(parents=True)
        file = child / 'sample.txt'
        file.write_bytes(b'owned ordinary file')
        key = self.harness.create('只读分析人工普通目录', str(ordinary))['id']
        invoke = lambda name, args: self.harness.tool(key, name, args, cancel=threading.Event())
        report = invoke('survey_scope', {'scope_id': 'root'})
        root_row = next(row for row in report['items'] if row['name'] == 'ordinary')
        child_row = next(row for row in report['items'] if row['name'] == 'child')
        invoke('inspect_directory', {'id': child_row['id']})
        current = next(row for row in self.harness._items(self.harness._get(key)) if row['id'] == root_row['id'])
        self.assertIsNone(current['size_bytes'])
        self.assertTrue(current['details']['aggregate_stale'])
        self.assertFalse(current['details']['subtree_complete'])
        self.assertEqual(current['actions'], [])
        self.assertNotIn('file_count', current['details'])
        self.assertNotIn('hardlink_entities', current['details'])
        self.assertIsNone(self.harness._metadata(current)['size_bytes'])
        # A root ID the model already received still accepts an explanation.
        invoke('record_advice', {'items': [dict(id=root_row['id'], conclusion='keep', cleanup_rating=1,
            purpose='人工目录容器', reason='只对具体文件讨论清理', impact='保留目录不影响文件')]})
        invoke('list_items', {'unanalysed': False})  # Unknown sizes remain sortable.
        self.assertEqual(self.app.state.snapshot()['summary']['logical_bytes'], len(b'owned ordinary file'))
        with self.assertRaises(AppError):
            self.app.state.prepare([root_row['id']])
        invoke('validate_directory', {'id': root_row['id']})
        prepared = self.app.state.prepare([root_row['id']])
        self.assertEqual(prepared['items'][0]['id'], root_row['id'])
        self.assertEqual(file.read_bytes(), b'owned ordinary file')

    def test_legacy_receipt_with_missing_task_does_not_turn_committed_undo_into_error(self):
        ordinary = self.root / 'legacy'
        ordinary.mkdir()
        file = ordinary / 'sample.txt'
        file.write_bytes(b'owned legacy file')
        observed = scan_directory(str(ordinary))['items']
        self.app.state.publish(observed, 'owned-legacy')
        entry = dict(next(row for row in observed if row['kind'] == 'file'), action='delete')
        result = self.harness.runtime.run('missing-legacy-task', 1, [entry])
        self.assertEqual(result['succeeded'], 1)
        batch = result['items'][0]['batch_id']
        undone = self.app.post('/api/undo', {'batch_id': batch})
        self.assertEqual(next(row for row in undone['history'] if row['id'] == batch)['status'], 'undone')
        self.assertEqual(self.harness.runtime.result('missing-legacy-task', 1)['items'][0]['current_state'], 'undone')
        self.assertEqual(file.read_bytes(), b'owned legacy file')


class ModelActionContractTests(unittest.TestCase):
    @staticmethod
    def actions(task):
        spec = next(row['function'] for row in TaskHarness._task_tool_specs(task)
                    if row['function']['name'] == 'propose_plan')
        return spec['parameters']['properties']['items']['items']['properties']['action']['enum']

    def test_action_codes_match_task_capabilities_instead_of_free_text(self):
        self.assertEqual(self.actions({'scope_kind': 'filesystem'}), ['delete'])
        self.assertEqual(self.actions({'scope_kind': 'applications', 'preset': 'background'}),
                         ['disable_startup', 'stop_service', 'stop_process'])
        self.assertEqual(self.actions({'scope_kind': 'applications', 'analysis': {'section': 'software'}}),
                         ['uninstall'])
        # Each request builds an independent schema; a previous software task
        # must not remove delete from a later filesystem task.
        self.assertEqual(self.actions({'scope_kind': 'filesystem'}), ['delete'])


if __name__ == '__main__':
    unittest.main()
