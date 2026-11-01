"""Product defaults and novice selection boundaries, using local fixtures only."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from diskpilot.scanner import scan_directory
from diskpilot.server import Application, common_cache_paths
from diskpilot.simulation import AppError, SimulationState


class ProductFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = Application(self.root / 'state')

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def wait_job(self, key):
        for _ in range(200):
            job = self.app.job(key)
            if job['status'] != 'running':
                return job
            time.sleep(.01)
        self.fail('Local fixture job did not finish')

    def test_fresh_start_contains_no_development_fixtures_or_public_demo_api(self):
        result = self.app.get('/api/bootstrap', {})
        self.assertEqual(result['items'], [])
        self.assertEqual(result['source'], 'empty')
        self.assertEqual(result['history'], [])
        self.assertEqual(self.app.get('/api/recommendations', {})['items'], [])
        with self.assertRaises(AppError) as ctx:
            self.app.post('/api/demo', {})
        self.assertEqual(ctx.exception.status, 404)

    def test_upgrade_discards_old_synthetic_catalog_and_journal(self):
        folder = self.root / 'old-store'
        state = SimulationState(folder)
        try:
            state.replace_demo()
            state.execute(state.prepare(['demo-startup'])['id'])
        finally:
            state.close()
        upgraded = Application(folder)
        try:
            self.assertEqual(upgraded.view()['items'], [])
            self.assertEqual(upgraded.state.history(), [])
        finally:
            upgraded.close()

    def test_upgrade_preserves_real_catalog_and_operation_history(self):
        folder = self.root / 'observed'
        folder.mkdir()
        file = folder / 'my-document.txt'
        file.write_text('personal document')
        result = scan_directory(str(folder))
        self.app.state.publish(result['items'], 'observed')
        item = next(x for x in result['items'] if x['kind'] == 'file')
        self.app.state.execute(self.app.state.prepare([item['id']])['id'])
        self.app.state.discard_development_fixtures()
        self.assertTrue(self.app.state.history())
        self.assertEqual(len(self.app.view()['items']), len(result['items']))
        self.assertEqual(file.read_text(), 'personal document')

    def test_personal_files_never_join_recommended_batch_but_manual_choice_works(self):
        folder = self.root / 'Downloads'
        folder.mkdir()
        file = folder / 'old-installer.exe'
        file.write_bytes(b'user-downloaded-data')
        result = scan_directory(str(folder))
        self.app.state.publish(result['items'], 'downloads')
        item = next(x for x in result['items'] if x['kind'] == 'file')
        advice = next(x['advice'] for x in self.app.recommendations()['items'] if x['id'] == item['id'])
        self.assertFalse(advice['auto_select'])
        self.assertTrue(advice['needs_user_choice'])
        with self.assertRaises(AppError) as ctx:
            self.app.post('/api/plan', {'ids': [item['id']], 'selection_mode': 'recommended'})
        self.assertEqual(ctx.exception.code, 'user_choice_required')
        plan = self.app.post('/api/plan', {'ids': [item['id']], 'selection_mode': 'manual'})
        self.app.post('/api/execute', {'plan_id': plan['id']})
        self.assertEqual(file.read_bytes(), b'user-downloaded-data')

    def test_check_is_bounded_and_reads_only_known_roots_and_inventory(self):
        local = self.root / 'profile' / 'AppData' / 'Local'
        cache = local / 'D3DSCache'
        cache.mkdir(parents=True)
        file = cache / 'shader.bin'
        file.write_bytes(b'cache-fixture')
        (local / 'UnrelatedApp').mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(local)}), \
             patch('diskpilot.server.inventory', return_value={'items': [], 'summary': {}}) as collect:
            self.assertEqual(common_cache_paths(), [str(cache)])
            job = self.wait_job(self.app.post('/api/check', {})['job_id'])
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(job['result']['summary']['cache_locations_checked'], 1)
        paths = {x['path'] for x in self.app.view()['items']}
        self.assertIn(str(file), paths)
        self.assertFalse(any('UnrelatedApp' in p for p in paths))
        self.assertEqual(file.read_bytes(), b'cache-fixture')

    def test_cancelling_check_does_not_publish_partially_collected_data(self):
        entered, release = threading.Event(), threading.Event()

        def collect(section):
            entered.set()
            release.wait(2)
            return {'items': [], 'summary': {}}

        with patch('diskpilot.server.common_cache_paths', return_value=[]), \
             patch('diskpilot.server.inventory', side_effect=collect):
            key = self.app.post('/api/check', {})['job_id']
            self.assertTrue(entered.wait(2))
            self.app.post('/api/jobs/' + key + '/cancel', {})
            release.set()
            job = self.wait_job(key)
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(self.app.view()['source'], 'empty')

    def test_model_result_from_an_old_catalog_cannot_be_applied(self):
        def stale(*args, **kwargs):
            self.app.state.publish([], 'new-data')
            return {'items': [], 'groups': [], 'summary': {}}
        with patch('diskpilot.server.build_recommendations', side_effect=stale):
            with self.assertRaises(AppError) as ctx:
                self.app.recommendations(use_model=True)
        self.assertEqual(ctx.exception.code, 'stale_recommendations')


if __name__ == '__main__':
    unittest.main()
