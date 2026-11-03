"""Real loopback routes for local user tools and task-owned read-only work."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from diskpilot.server import Application, create_server


class UserToolHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-user-http-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'owned'; self.scope.mkdir()
        for name in ('first.tmp', 'second.tmp', 'keep.txt'):
            path = self.scope / name; path.write_bytes(('owned fixture ' + name).encode())
            stamp = time.time() - 14 * 86400; os.utime(path, (stamp, stamp))
        self.before = self.hashes()
        self.server = create_server(self.root / 'profile')
        self.app = self.server.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.csrf = self.request('/api/bootstrap?compact=1')['csrf']
        self.task = self.app.harness.create(goal='仅检查自有样本', path=str(self.scope))

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.app.close(); self.thread.join(3)

    def hashes(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.scope.iterdir()}

    def request(self, path, body=None, *, expected=200, csrf=None, raw=False):
        headers, data = {}, None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers = {'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf if csrf is None else csrf}
        request = urllib.request.Request(self.url + path, data=data, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            self.assertEqual(response.status, expected)
            content = response.read()
            return content if raw else json.loads(content)

    def definition(self, title='我的临时文件'):
        return dict(type='files', title=title, description='仅清理此处旧tmp样本',
                    rules=dict(roots=[str(self.scope)], includes=['*.tmp'], min_age_days=7))

    def saved(self):
        draft = self.request('/api/user-tools/draft', dict(definition=self.definition(), source={'task_id': self.task['id']}))
        saved = self.request('/api/user-tools/save', {'draft_id': draft['draft_id']})
        return draft, saved

    def wait(self, key):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.request('/api/jobs/' + key)
            if job['status'] != 'running':
                return job
            time.sleep(.01)
        self.fail('Owned simulation job did not complete')

    def test_shared_catalog_and_local_draft_saved_inspection_plan_execution_keep_files_unchanged(self):
        self.assertIs(self.app.catalog, self.app.harness.catalog)
        self.assertIs(self.app.catalog, self.app.user_tools.catalog)
        self.assertEqual(self.request('/api/user-tools/schema')['types'], ['files', 'preset'])
        with patch.object(self.app.assistant, 'agent_turn', side_effect=AssertionError('No model for UI operation')):
            draft, saved = self.saved()
            self.assertEqual(draft['provenance']['source'], 'user')
            self.assertNotIn('model', draft['provenance'])
            self.assertTrue(saved['source_feedback']['recorded'])
            listing = self.request('/api/user-tools?query=' + urllib.parse.quote('临时'))
            self.assertEqual([row['id'] for row in listing['items']], [saved['id']])
            run = self.request('/api/user-tools/inspect', {'tool_id': saved['id'], 'version': saved['version']})
            self.assertEqual(run['matched_count'], 2)
            self.assertTrue(run['read_only'])
            page = self.request('/api/user-tool-runs/' + run['id'] + '?offset=0&limit=1')
            self.assertEqual(len(page['items']), 1); self.assertTrue(page['has_more'])
            prepared = self.request('/api/user-tools/prepare', {'run_id': run['id'], 'ids': [page['items'][0]['id']]})
            self.assertEqual(len(prepared['picked_ids']), 1)
            self.assertEqual(self.app.state.history(), [], 'Preparing is not execution')
            facts = self.app.harness.facts_page(self.task['id'], limit=20)['items']
            actions = [row for row in facts if row['tool'] == 'user_tool_user_action']
            self.assertTrue(actions)
            self.assertFalse(actions[-1]['result']['applied'])
            job = self.request('/api/tasks/' + prepared['task']['id'] + '/approve',
                {'version': prepared['plan']['version'], 'ids': prepared['picked_ids']})
            final = self.wait(job['job_id'])
            self.assertEqual(final['status'], 'completed')
            self.assertEqual(final['result']['task']['last_result']['actual_reclaimed_bytes'], 0)
            self.assertEqual(final['result']['task']['last_result']['succeeded'], 1)
        self.assertEqual(self.hashes(), self.before)

    def test_explicit_version_reads_survive_edits_but_old_version_cannot_be_run(self):
        _, saved = self.saved()
        draft = self.request('/api/user-tools/draft', dict(definition=self.definition('已修改'),
            recipe_id=saved['id'], base_version=1))
        updated = self.request('/api/user-tools/save', {'draft_id': draft['id']})
        self.assertEqual(updated['version'], 2)
        old = self.request('/api/user-tools/' + saved['id'] + '?version=1')
        self.assertEqual(old['version'], 1); self.assertEqual(old['current_version'], 2)
        self.assertEqual(old['definition']['title'], '我的临时文件')
        self.request('/api/user-tools/inspect', {'tool_id': saved['id'], 'version': 1}, expected=409)
        self.request('/api/user-tools/' + saved['id'] + '?version=bad', expected=400)
        result = self.request('/api/user-tools/archive', {'tool_id': saved['id'], 'version': 2})
        self.assertTrue(result['archived']); self.assertEqual(self.request('/api/user-tools')['items'], [])

    def test_strict_shapes_provenance_csrf_and_queries_prevent_implicit_execution(self):
        self.request('/api/user-tools/draft', {'definition': self.definition()}, expected=403, csrf='wrong')
        for path, body in [('/api/user-tools/draft', {'definition': self.definition(), 'provenance': {'source': 'model'}}),
                           ('/api/user-tools/save', {'draft_id': 'x', 'execute': True}),
                           ('/api/user-tools/inspect', {'tool_id': 'x'}),
                           ('/api/user-tools/inspect', {'tool_id': 'x', 'version': None}),
                           ('/api/user-tools/inspect', {'draft_id': 'x', 'tool_id': 'x', 'version': 1}),
                           ('/api/user-tools/prepare', {'run_id': 'x', 'command': 'del *'}),
                           ('/api/user-tools/archive', {'tool_id': 'x'}),
                           ('/api/user-tools/draft', {'definition': {'type': 'script', 'title': 'x'}})]:
            with self.subTest(path=path, body=body):
                self.request(path, body, expected=400)
        for path in ('/api/user-tools?query=a&query=b', '/api/user-tools/schema?version=1',
                     '/api/user-tools/unknown?version=1&version=2', '/api/user-tool-runs/x?limit=bad'):
            with self.subTest(path=path):
                self.request(path, expected=400)
        self.assertEqual(self.app.state.history(), [])
        self.assertEqual(self.hashes(), self.before)

    def test_budget_routes_are_flat_atomic_and_persist_without_model_or_connection_overrides(self):
        self.assertEqual(self.request('/api/agent-budget'), dict(model_requests=6, tool_calls=12))
        result = self.request('/api/agent-budget', {'tool_calls': 50})
        self.assertEqual(result, dict(model_requests=6, tool_calls=50))
        for body in ({'model_requests': True}, {'model_requests': 33}, {'custom_instructions': 'x'},
                     {'model_requests': 12, 'tool_calls': -1}):
            self.request('/api/agent-budget', body, expected=400)
            self.assertEqual(self.request('/api/agent-budget'), result)
        self.request('/api/agent-budget?extra=1', expected=400)

    def test_new_local_modules_are_served_by_the_static_allowlist(self):
        for name in ('user-tools.js', 'user-tools.css', 'orchestration-ui.js', 'orchestration-ui.css'):
            with self.subTest(asset=name):
                body = self.request('/' + name, raw=True)
                self.assertEqual(body, (self.app.web_root / name).read_bytes())
                self.assertTrue(body)
        self.request('/user-tools.py', expected=404)

    def test_analysis_session_budget_is_separate_atomic_and_query_checked(self):
        value = self.request('/api/analysis-budget')
        self.assertEqual(value['model_requests'], 96)
        self.assertEqual(value['elapsed_minutes'], 30)
        saved = self.request('/api/analysis-budget', {'elapsed_minutes': 12, 'total_tokens': 50000})
        self.assertEqual(saved, dict(value, elapsed_minutes=12, total_tokens=50000))
        self.assertEqual(self.request('/api/agent-budget'), dict(model_requests=6, tool_calls=12))
        for body in ({'elapsed_minutes': True}, {'total_tokens': 0}, {'model_requests': 1025},
                     {'elapsed_minutes': 4, 'command': 'anything'}):
            self.request('/api/analysis-budget', body, expected=400)
            self.assertEqual(self.request('/api/analysis-budget'), saved)
        self.request('/api/analysis-budget', {'elapsed_minutes': 10}, expected=403, csrf='wrong')
        self.request('/api/analysis-budget?extra=1', expected=400)
        self.assertEqual(self.app.state.history(), [])

    def test_task_diagnostic_snapshot_reads_are_owned_and_paginated(self):
        with (patch('diskpilot.diagnostics.system_configuration', return_value={}),
              patch('diskpilot.diagnostics.volume_snapshot', return_value={}),
              patch('diskpilot.diagnostics.candidate_paths', return_value=([], 'owned'))):
            snapshot = self.app.diagnostics.scan([str(self.scope)], task_id=self.task['id'])
        base = '/api/tasks/' + self.task['id'] + '/diagnostics/' + snapshot['snapshot_id']
        page = self.request(base + '?offset=0&limit=2')
        self.assertEqual(page['task_id'], self.task['id']); self.assertEqual(len(page['items']), 2)
        self.assertTrue(page['has_more'])
        filtered = self.request(base + '?tool_ids=uv_cache,pip_cache')
        self.assertEqual({row['tool_id'] for row in filtered['items']}, {'uv_cache', 'pip_cache'})
        other = self.app.harness.create(goal='其他样本任务', path=str(self.scope))
        self.request('/api/tasks/' + other['id'] + '/diagnostics/' + snapshot['snapshot_id'], expected=409)
        self.request(base + '?limit=0', expected=400)
        self.request(base + '?offset=0&offset=1', expected=400)
        self.request(base + '?raw=1', expected=400)

    def test_task_work_read_and_cancel_never_allow_other_task_ids(self):
        entered = threading.Event()
        def worker(cancel, progress):
            entered.set(); cancel.wait(3)
            return dict(status='cancelled' if cancel.is_set() else 'completed', summary={'owned': True})
        work = self.app.harness.task_jobs.start(self.task['id'], 'owned_test', {}, worker)
        self.assertTrue(entered.wait(1))
        path = '/api/tasks/' + self.task['id'] + '/work/' + work['id']
        self.assertEqual(self.request(path)['id'], work['id'])
        other = self.app.harness.create(goal='其他工作', path=str(self.scope))
        wrong = '/api/tasks/' + other['id'] + '/work/' + work['id']
        self.request(wrong, expected=404)
        self.request(wrong + '/cancel', {}, expected=404)
        self.request(path + '/cancel', {'execute': True}, expected=400)
        cancelled = self.request(path + '/cancel', {})
        self.assertIn(cancelled['status'], ('cancelling', 'cancelled'))
        final = self.app.harness.task_jobs.wait(self.task['id'], work['id'])
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(self.hashes(), self.before)


class UserToolShutdownTests(unittest.TestCase):
    def test_application_waits_for_child_workers_before_closing_the_database(self):
        with tempfile.TemporaryDirectory(prefix='panxu-close-work-') as temp:
            app = Application(Path(temp) / 'profile')
            entered, finished = threading.Event(), threading.Event()
            def worker(cancel, progress):
                entered.set(); cancel.wait(3); finished.set()
                return dict(status='cancelled')
            work = app.harness.task_jobs.start('owned-task', 'owned', {}, worker)
            self.assertTrue(entered.wait(1))
            close = app.state.close
            def checked_close():
                self.assertTrue(finished.is_set())
                close()
            with patch.object(app.state, 'close', side_effect=checked_close):
                app.close()
            db = sqlite3.connect(Path(temp) / 'profile/simulation.sqlite3')
            try:
                row = db.execute('SELECT payload FROM task_work WHERE id=?', (work['id'],)).fetchone()
            finally:
                db.close()
            self.assertEqual(json.loads(row[0])['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
