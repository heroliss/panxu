"""The common catalog/plan/runtime handles background IDs without real actions."""
import copy
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from diskpilot.scanner import scan_directory
from diskpilot.server import Application
from diskpilot.simulation import AppError
from diskpilot.windows import _item


class ApplicationSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-objects-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'owned'
        self.scope.mkdir()
        self.file = self.scope / 'retained.bin'
        self.file.write_bytes(b'owned source is unchanged')
        self.before = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.app = Application(self.root / 'profile')
        self.addCleanup(self.app.close)
        self.rows = [self.item('startup', 'start-a'), self.item('startup', 'start-b'),
            self.item('service', 'service-running'), self.item('service', 'service-stopped', state='Stopped'),
            self.item('process', 'process-a', pid=101), self.item('process', 'process-b', pid=102),
            self.item('startup', 'protected-start', protected=True)]
        self.related = self.item('software', 'related-software')
        self.foreign = self.item('process', 'foreign-process', pid=103)
        self.live = {row['id']: copy.deepcopy(row) for row in [*self.rows, self.related, self.foreign]}
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'owned-files')
        self.app.state.publish([self.foreign], 'outside-task')
        self.source = self.app.harness.create(preset='background')
        for section, rows in (('background', self.rows), ('software', [self.related])):
            with patch('diskpilot.tasks.inventory', return_value={'items': rows, 'warnings': [], 'summary': {'read_only': True}}):
                self.app.harness.tool(self.source['id'], 'collect_apps', {'section': section}, cancel=threading.Event())
        self.validator_calls = 0

        def validate(items):
            self.validator_calls += 1
            if any(self.live.get(item['id'], {}).get('signature') != item['signature'] for item in items):
                raise AppError('Owned fixture changed.', 'target_changed', 409)

        self.app.harness.runtime.backend.validator = validate

    def item(self, kind, key, *, state='Running', pid=101, protected=False):
        action = {'startup': 'disable_startup', 'service': 'stop_service', 'process': 'stop_process', 'software': 'uninstall'}[kind]
        details = dict(synthetic=False, state=state, start_mode='Auto', pid=pid, has_uninstaller=True)
        row = _item(kind, key, key, 'Owned fixture', kind, action, details,
            path=str(self.root / 'same-program.exe') if kind == 'process' else 'registration/' + key, protected=protected)
        return row

    def row(self, key):
        return next(row for row in self.rows if row['name'] == key)

    def page(self, **query):
        return self.app.get('/api/files', {key: [str(value)] for key, value in dict(task_id=self.source['id'], limit=50, **query).items()})

    def select(self, **extra):
        page = self.page()
        payload = dict(task_id=self.source['id'], category='all', mode='manual', revision=page['revision'],
                       selection_revision=page['selection_revision'])
        payload.update(extra)
        return self.app.post('/api/assessments/select', payload)

    def logical(self, selected, excluded=()):
        page = self.page()
        current = {row['id']: row for row in self.app.state.snapshot([])['items']}
        return dict(task_id=self.source['id'], selected_ids=list(selected), excluded_ids=list(excluded),
            snapshots={ident: current[ident]['signature'] for ident in [*selected, *excluded]},
            revision=page['revision'], selection_revision=page['selection_revision'])

    def score(self, name, score, conclusion='review'):
        self.app.harness.tool(self.source['id'], 'record_advice', {'items': [dict(id=self.row(name)['id'],
            conclusion=conclusion, cleanup_rating=score, purpose='样本用途', reason='样本依据', impact='可能影响功能就绪')]},
            cancel=threading.Event())

    def wait_job(self, key):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            job = self.app.job(key)
            if job['status'] != 'running':
                return job
            time.sleep(.005)
        self.fail('Owned simulation job did not finish')

    def execute(self, ids):
        prepared = self.app.post('/api/assessments/plan', self.logical(ids))
        task_id = prepared['task']['id']
        job = self.app.post(f'/api/tasks/{task_id}/approve', dict(version=prepared['plan']['version'], ids=prepared['picked_ids']))
        return prepared, self.wait_job(job['job_id'])

    def unchanged(self):
        self.assertEqual(hashlib.sha256(self.file.read_bytes()).hexdigest(), self.before)
        self.assertTrue(all(row['signature'] == self.live[row['id']]['signature'] for row in self.rows))

    def test_all_is_observed_task_scope_and_suggestions_are_actual_records(self):
        all_rows = self.page()
        self.assertEqual(all_rows['total'], len(self.rows))
        self.assertFalse(self.page(view='suggestions')['items'])
        self.assertEqual({row['kind'] for row in all_rows['items']}, {'startup', 'service', 'process'})
        self.assertNotIn(self.related['id'], [row['id'] for row in all_rows['items']])
        self.assertNotIn(self.foreign['id'], [row['id'] for row in all_rows['items']])
        self.assertTrue(all(row['kind'] in ('file', 'directory') for row in self.app.get('/api/files', {})['items']))
        self.score('start-a', 5)
        self.score('service-running', 4)
        self.score('service-stopped', 5)
        self.score('process-a', None)
        self.score('process-b', None, 'skip')
        self.score('protected-start', 1, 'keep')
        suggestions = self.page(view='suggestions')
        self.assertEqual(suggestions['total'], 6)
        by_name = {row['name']: row for row in suggestions['items']}
        self.assertIsNone(by_name['process-a']['assessment']['rating'])
        self.assertEqual(by_name['process-b']['assessment']['state'], 'skipped')
        self.assertFalse(by_name['service-stopped']['can_logical_select'])
        self.assertIn('已停止', by_name['service-stopped']['action_unavailable_reason'])
        self.assertEqual(by_name['service-running']['action'], 'stop_service')
        self.assertIn('不会禁用', by_name['service-running']['action_effect'])
        high = self.select(category='high', mode='automatic')
        self.assertEqual(set(high['ids']), {self.row('start-a')['id'], self.row('service-running')['id']})
        with self.assertRaises(AppError):
            self.page(view='tree')

    def test_same_executable_processes_are_independent_ids_not_path_rules(self):
        first, second = self.row('process-a'), self.row('process-b')
        self.assertEqual(first['path'], second['path'])
        self.assertNotEqual(first['id'], second['id'])
        result = self.app.post('/api/assessments/compile', self.logical([first['id']], [second['id']]))
        self.assertEqual(result['ids'], [first['id']])
        self.assertEqual(result['items'][0]['action'], 'stop_process')
        self.assertEqual(result['action_groups'], [dict(action='stop_process', label='结束进程', total=1, returned=1)])
        processes = self.page(kind='process')
        self.assertEqual(processes['total'], 2)
        self.assertEqual({row['details']['pid'] for row in processes['items']}, {101, 102})

    def test_kind_filter_applies_to_full_selection_not_the_visible_page(self):
        bulk = [self.item(('startup', 'service', 'process')[index % 3], f'Bulk {index:04}', pid=1000 + index) for index in range(620)]
        self.live.update({row['id']: copy.deepcopy(row) for row in bulk})
        self.app.state.publish(bulk, 'bulk-owned-metadata')
        self.source = self.app.harness.create_analysis({'section': 'background', 'query': 'Bulk '})
        page = self.page()
        self.assertEqual(len(page['items']), 50)
        self.assertEqual(page['total'], 620)
        first = self.select()
        second = self.select(cursor=first['next_cursor'])
        self.assertEqual(len(first['ids']), 500)
        self.assertEqual(first['counts']['total_targets'], 620)
        self.assertEqual(len(second['ids']), 120)
        self.assertFalse(set(first['ids']) & set(second['ids']))
        self.assertEqual(sum(group['total'] for group in first['action_groups']), 620)
        self.assertEqual(sum(group['returned'] for group in first['action_groups']), 500)
        for kind in ('startup', 'service', 'process'):
            chosen = self.select(kind=kind)
            self.assertEqual(set(chosen['ids']), {row['id'] for row in bulk if row['kind'] == kind})
        with self.assertRaises(AppError):
            self.select(kind='service', cursor=first['next_cursor'])
        compiled = self.app.post('/api/assessments/compile', self.logical([row['id'] for row in bulk]))
        self.assertEqual(compiled['counts']['total_targets'], 620)
        prepared = self.app.post('/api/assessments/plan', self.logical([row['id'] for row in bulk]))
        self.assertEqual(len(prepared['plan']['items']), 500)
        self.assertEqual(sum(group['total'] for group in prepared['plan']['action_groups']), 500)
        self.assertEqual(prepared['selection']['counts']['total_targets'], 620)
        self.assertEqual({row['action'] for row in prepared['plan']['items']}, {'disable_startup', 'stop_service', 'stop_process'})
        self.assertFalse(self.app.state.history())

    def test_protection_unavailable_state_and_foreign_context_cannot_enter_plan(self):
        selected = self.select()
        self.assertNotIn(self.row('protected-start')['id'], selected['ids'])
        self.assertNotIn(self.row('service-stopped')['id'], selected['ids'])
        for row in (self.related, self.foreign):
            with self.subTest(name=row['name']), self.assertRaises(AppError):
                self.app.post('/api/assessments/compile', self.logical([row['id']]))
        with self.assertRaises(AppError):
            self.app.harness.direct_plan(self.source['id'], {'ids': [self.related['id']], 'action': 'uninstall'})
        with self.assertRaises(AppError):
            self.app.harness.prepare_assessment_plan([self.row('service-stopped')['id']], source_task_id=self.source['id'])
        with self.assertRaises(AppError):
            self.app.harness.prepare_assessment_plan([self.row('process-a')['id']])

    def test_signature_and_available_action_changes_invalidate_frozen_selection(self):
        row = self.row('process-a')
        payload = self.logical([row['id']])
        changed = copy.deepcopy(row)
        changed['signature'] = 'new-owned-signature'
        self.app.state.publish([changed], 'changed-owned-process')
        with self.assertRaises(AppError):
            self.app.post('/api/assessments/compile', payload)
        self.app.state.publish([row], 'changed-owned-process')
        payload = self.logical([row['id']])
        changed = copy.deepcopy(row)
        changed['actions'] = ['uninstall']  # Same ID/signature, unsupported meaning.
        self.app.state.publish([changed], 'changed-owned-process')
        with self.assertRaises(AppError):
            self.app.post('/api/assessments/compile', payload)
        refreshed = self.page(kind='process')
        self.assertFalse(next(item for item in refreshed['items'] if item['id'] == row['id'])['can_logical_select'])

    def test_mixed_plan_runs_same_runtime_not_model_and_undo_reaches_source_beyond_recent_three(self):
        ids = [row['id'] for row in self.rows if row['name'] not in ('protected-start', 'service-stopped')]
        with patch.object(self.app.assistant, 'agent_turn', side_effect=AssertionError('Manual operations cannot request a model')):
            prepared, job = self.execute(ids)
        self.assertEqual(job['status'], 'completed')
        result = job['result']['task']['last_result']
        self.assertEqual(result['succeeded'], 5)
        self.assertEqual(result['actual_reclaimed_bytes'], 0)
        self.assertEqual(result['actions'], dict(disable_startup=2, stop_service=1, stop_process=2))
        self.assertEqual(prepared['task']['cleanup_source_task_id'], self.source['id'])
        oldest = result['items'][0]
        self.assertNotIn(oldest['id'], [row['id'] for group in self.app.state.history()[:3] for row in group['items']])
        self.app.post('/api/undo', {'batch_id': oldest['batch_id']})
        host = self.app.harness.public(prepared['task']['id'])
        self.assertEqual(host['last_result']['succeeded'], 5)  # Historical result remains.
        self.assertEqual(host['last_result']['undone'], 1)
        self.assertEqual(next(row for row in host['last_result']['items'] if row['id'] == oldest['id'])['current_state'], 'undone')
        source = self.app.harness._get(self.source['id'])
        receipt = self.app.harness._context(source)['last_cleanup_result']
        self.assertEqual(receipt['succeeded'], 5)
        self.assertEqual(receipt['undone'], 1)
        self.assertEqual(receipt['undone_actions'], {oldest['action']: 1})
        self.assertEqual(receipt['action_results'][oldest['action']]['undone'], 1)
        self.assertIn(oldest['id'], [row['id'] for row in self.page()['items']])
        self.unchanged()

    def test_runtime_rechecks_frozen_action_and_pid_after_confirmation(self):
        row = self.row('process-a')
        prepared = self.app.post('/api/assessments/plan', self.logical([row['id']]))
        entry, = prepared['plan']['items']
        self.assertEqual(entry['details']['pid'], 101)
        self.assertEqual(entry['action'], 'stop_process')

        def changed(_entry, phase):
            if phase == 'before':
                current = copy.deepcopy(row)
                current['actions'] = ['uninstall']
                self.app.state.publish([current], 'changed-before-owned-runtime')

        self.app.harness.runtime.fault = changed
        key, version = prepared['task']['id'], prepared['plan']['version']
        job = self.app.post(f'/api/tasks/{key}/approve', dict(version=version, ids=prepared['picked_ids']))
        done = self.wait_job(job['job_id'])
        result = done['result']['task']['last_result']
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['succeeded'], 0)
        self.assertFalse(self.app.state.history())
        self.assertEqual(self.app.harness.public(self.source['id'])['last_cleanup_result']['failed'], 1)
        self.unchanged()

    def test_mixed_execution_pauses_resumes_then_cancels_only_uncommitted_items(self):
        ids = [self.row(name)['id'] for name in ('start-a', 'service-running', 'process-a', 'process-b')]
        entered, release, committed, release_commit = (threading.Event() for _ in range(4))

        def fault(entry, phase):
            if phase == 'before' and not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise AssertionError('Owned gate not released')
            elif phase == 'after' and not committed.is_set():
                committed.set()
                if not release_commit.wait(5):
                    raise AssertionError('Owned commit gate not released')

        self.app.harness.runtime.fault = fault
        prepared = self.app.post('/api/assessments/plan', self.logical(ids))
        key, version = prepared['task']['id'], prepared['plan']['version']
        job = self.app.post(f'/api/tasks/{key}/approve', dict(version=version, ids=prepared['picked_ids']))
        try:
            self.assertTrue(entered.wait(2))
            self.app.post(f'/api/tasks/{key}/execution/pause', {'version': version})
            release.set()
            end = time.monotonic() + 3
            while time.monotonic() < end and self.app.harness.execution_status(key)['status'] != 'paused':
                time.sleep(.005)
            paused = self.app.harness.execution_status(key)
            self.assertEqual(paused['status'], 'paused')
            self.assertEqual(paused['succeeded'], 0)
            self.assertFalse(self.app.state.history())
            self.app.post(f'/api/tasks/{key}/execution/resume', {'version': version})
            self.assertTrue(committed.wait(2))
            self.app.post(f'/api/tasks/{key}/execution/cancel', {'version': version})
            release_commit.set()
            finished = self.wait_job(job['job_id'])
            result = finished['result']['task']['last_result']
            self.assertEqual(result['total'], 4)
            self.assertEqual(result['succeeded'] + result['cancelled'], 4)
            self.assertEqual(result['succeeded'], 1)
            self.assertEqual(result['cancelled'], 3)
            receipt = self.app.harness.public(self.source['id'])['last_cleanup_result']
            self.assertEqual(receipt['total'], 4)
            self.assertEqual(receipt['cancelled'], result['cancelled'])
            self.assertEqual(receipt['actual_reclaimed_bytes'], 0)
        finally:
            release.set()
            release_commit.set()
        self.unchanged()

    def test_software_uses_same_scoped_list_and_uninstall_action(self):
        self.source = self.app.harness.create_analysis({'section': 'software', 'ids': [self.related['id']]})
        self.assertEqual(self.page()['items'][0]['action'], 'uninstall')
        prepared, job = self.execute([self.related['id']])
        self.assertEqual(prepared['plan']['items'][0]['action'], 'uninstall')
        self.assertEqual(job['result']['task']['last_result']['succeeded'], 1)
        self.assertEqual(job['result']['task']['last_result']['actual_reclaimed_bytes'], 0)
        self.unchanged()

    def test_older_receipt_never_borrows_new_plan_execution_counts(self):
        prepared, job = self.execute([self.row('start-a')['id'], self.row('process-a')['id']])
        task = self.app.harness._get(prepared['task']['id'])
        result = job['result']['task']['last_result']
        self.app.harness._record_cleanup_result(task, result,
            dict(plan_version=result['plan_version'] + 1, total=900, pending=800, status='running'))
        receipt = self.app.harness.public(self.source['id'])['last_cleanup_result']
        self.assertEqual(receipt['plan_version'], result['plan_version'])
        self.assertEqual(receipt['total'], 2)
        self.assertEqual(receipt['pending'], 0)
        self.assertEqual(receipt['succeeded'], 2)
        self.assertNotEqual(receipt['status'], 'running')


if __name__ == '__main__':
    unittest.main()
