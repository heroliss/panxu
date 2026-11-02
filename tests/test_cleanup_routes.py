"""Task-scoped selection and execution controls use disposable real files only."""
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


class CleanupRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'downloads'
        self.folder = self.scope / 'group'
        self.folder.mkdir(parents=True)
        for name in ('clean.bin', 'keep.txt', 'unknown.bin'):
            (self.folder / name).write_bytes(('owned-' + name).encode())
        self.before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()}
        self.app = Application(self.root / 'data')
        self.closed = False
        self.addCleanup(self.close)
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'owned-files')
        self.task = self.app.harness.create_analysis({'section': 'files', 'scope': str(self.scope)})
        self.items = {i['name']: i for i in self.app.state.snapshot([])['items']}
        self.app.harness.tool(self.task['id'], 'record_advice', {'items': [
            dict(id=self.items['group']['id'], conclusion='recommend', cleanup_rating=5, inheritance='subtree',
                 purpose='测试目录', reason='演示目录倾向', impact='模拟记录'),
            dict(id=self.items['clean.bin']['id'], conclusion='review', cleanup_rating=4,
                 purpose='测试副本', reason='演示可清理项', impact='模拟记录'),
            dict(id=self.items['keep.txt']['id'], conclusion='keep', cleanup_rating=1,
                 purpose='保留测试资料', reason='演示明确保留', impact='模拟记录'),
        ]}, cancel=threading.Event())

    def close(self):
        if not self.closed:
            self.app.close()
            self.closed = True

    def page(self):
        return self.app.get('/api/files', {'task_id': [self.task['id']], 'limit': ['100']})

    def logical(self, selected, excluded=()):
        page = self.page()
        ids = list(selected) + list(excluded)
        current = {i['id']: i for i in self.app.state.snapshot([])['items']}
        return dict(task_id=self.task['id'], selected_ids=list(selected), excluded_ids=list(excluded),
                    snapshots={i: current[i]['signature'] for i in ids}, revision=page['revision'],
                    selection_revision=page['selection_revision'])

    def assert_files_unchanged(self):
        self.assertTrue(all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == digest
                            for p, digest in self.before.items()))

    def test_task_scoped_category_selection_and_directory_exception_compile(self):
        page = self.page()
        high = self.app.post('/api/assessments/select', dict(task_id=self.task['id'], category='4',
            mode='manual', revision=page['revision'], selection_revision=page['selection_revision']))
        self.assertEqual(high['ids'], [self.items['clean.bin']['id']])
        payload = self.logical([self.items['group']['id']], [self.items['keep.txt']['id']])
        compiled = self.app.post('/api/assessments/compile', payload)
        self.assertNotIn(self.items['group']['id'], compiled['ids'])
        self.assertNotIn(self.items['keep.txt']['id'], compiled['ids'])
        self.assertIn(self.items['clean.bin']['id'], compiled['ids'])
        with patch.object(self.app.assistant, 'agent_turn', side_effect=AssertionError('selection must not call LLM')):
            prepared = self.app.post('/api/assessments/plan', payload)
        self.assertEqual(prepared['task']['cleanup_source_task_id'], self.task['id'])
        self.assertNotIn(self.items['keep.txt']['id'], prepared['picked_ids'])
        self.assertEqual(prepared['task']['status'], 'awaiting_review')
        self.assertFalse(self.app.state.history())
        self.assert_files_unchanged()

    def test_logical_negative_snapshots_and_stale_versions_are_rejected(self):
        payload = self.logical([self.items['group']['id']], [self.items['keep.txt']['id']])
        payload['snapshots'][self.items['keep.txt']['id']] = 'stale-negative-reference'
        with self.assertRaises(AppError):
            self.app.post('/api/assessments/plan', payload)
        payload = self.logical([self.items['clean.bin']['id']])
        payload['selection_revision'] = 'old'
        with self.assertRaises(AppError):
            self.app.post('/api/assessments/compile', payload)
        self.assertFalse(self.app.state.history())
        self.assert_files_unchanged()

    def test_auto_four_preference_is_separate_persistent_boolean(self):
        self.assertTrue(self.app.get('/api/ui-preferences', {})['preferences']['cleanup_auto_select_four'])
        changed = self.app.post('/api/ui-preferences', {'cleanup_auto_select_four': False})
        self.assertFalse(changed['preferences']['cleanup_auto_select_four'])
        self.assertFalse(self.app.get('/api/ui-preferences', {})['preferences']['cleanup_auto_select_four'])
        self.assertNotIn('cleanup_auto_select_four', self.app.assistant.public_settings())
        with self.assertRaises(AppError):
            self.app.post('/api/ui-preferences', {'cleanup_auto_select_four': 1})

    def test_pause_then_cancel_is_real_control_without_commit_or_model_request(self):
        prepared = self.app.post('/api/assessments/plan', self.logical([self.items['clean.bin']['id']]))
        key, version = prepared['task']['id'], prepared['plan']['version']
        entered, release = threading.Event(), threading.Event()

        def gate(entry, phase):
            if phase == 'before':
                entered.set()
                release.wait(3)
            return False

        self.app.harness.runtime.fault = gate
        self.addCleanup(release.set)
        with patch.object(self.app.assistant, 'agent_turn', side_effect=AssertionError('cleanup must not call LLM')):
            started = self.app.post('/api/tasks/' + key + '/approve', {'version': version, 'ids': prepared['picked_ids']})
            self.assertTrue(entered.wait(1))
            paused = self.app.post('/api/tasks/' + key + '/execution/pause', {'version': version})['execution']
            self.assertIn(paused['status'], ('pausing', 'paused'))
            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                state = self.app.get('/api/tasks/' + key + '/execution', {})['execution']
                if state['status'] == 'paused':
                    break
                threading.Event().wait(.01)
            self.assertEqual(state['status'], 'paused')
            self.assertFalse(self.app.state.history())
            self.app.post('/api/tasks/' + key + '/execution/cancel', {'version': version})
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = self.app.get('/api/jobs/' + started['job_id'], {})
                if job['status'] != 'running':
                    break
                threading.Event().wait(.01)
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(job['execution']['succeeded'], 0)
        self.assertEqual(job['execution']['cancelled'], 1)
        self.assertEqual(job['result']['task']['status'], 'paused')
        self.assertFalse(self.app.state.history())
        self.assert_files_unchanged()
        with self.assertRaises(AppError):
            self.app.post('/api/tasks/' + key + '/execution/resume', {'version': version + 1})

    def test_resume_then_cancel_preserves_already_committed_result(self):
        chosen = [self.items['clean.bin']['id'], self.items['unknown.bin']['id']]
        prepared = self.app.post('/api/assessments/plan', self.logical(chosen))
        key, version = prepared['task']['id'], prepared['plan']['version']
        first, second, release_first, release_second = (threading.Event() for _ in range(4))
        order = []

        def gate(entry, phase):
            if phase == 'before':
                order.append(entry['id'])
                (first if len(order) == 1 else second).set()
                (release_first if len(order) == 1 else release_second).wait(3)
            return False

        self.addCleanup(release_first.set)
        self.addCleanup(release_second.set)
        self.app.harness.runtime.fault = gate
        started = self.app.post('/api/tasks/' + key + '/approve', {'version': version, 'ids': prepared['picked_ids']})
        self.assertTrue(first.wait(1))
        self.app.post('/api/tasks/' + key + '/execution/pause', {'version': version})
        release_first.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = self.app.harness.execution_status(key)
            if status['status'] == 'paused':
                break
            threading.Event().wait(.01)
        self.assertEqual(status['status'], 'paused')
        self.app.post('/api/tasks/' + key + '/execution/resume', {'version': version})
        self.assertTrue(second.wait(1))
        self.assertEqual(self.app.harness.execution_status(key)['succeeded'], 1)
        self.app.post('/api/tasks/' + key + '/execution/cancel', {'version': version})
        release_second.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = self.app.job(started['job_id'])
            if job['status'] != 'running':
                break
            threading.Event().wait(.01)
        self.assertEqual(job['execution']['succeeded'], 1)
        self.assertEqual(job['execution']['cancelled'], 1)
        self.assertEqual(len(self.app.state.history()), 1)
        self.assertEqual(job['execution']['actual_reclaimed_bytes'], 0)
        self.assert_files_unchanged()

    def test_close_wakes_paused_execution_without_holding_application_locks(self):
        prepared = self.app.post('/api/assessments/plan', self.logical([self.items['clean.bin']['id']]))
        key, version = prepared['task']['id'], prepared['plan']['version']
        entered, release = threading.Event(), threading.Event()

        def gate(entry, phase):
            if phase == 'before':
                entered.set()
                release.wait(3)
            return False

        self.addCleanup(release.set)
        self.app.harness.runtime.fault = gate
        self.app.post('/api/tasks/' + key + '/approve', {'version': version, 'ids': prepared['picked_ids']})
        self.assertTrue(entered.wait(1))
        self.app.post('/api/tasks/' + key + '/execution/pause', {'version': version})
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self.app.harness.execution_status(key)['status'] != 'paused':
            threading.Event().wait(.01)
        worker = threading.Thread(target=self.app.close)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive(), 'closing must wake paused cleanup')
        self.closed = True
        self.assert_files_unchanged()


if __name__ == '__main__':
    unittest.main()
