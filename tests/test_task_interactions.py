"""Host intent, optional model guidance and preference questions share durable tasks."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.server import Application, create_server
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness, tool_specs


def model_call(name, args):
    return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'call-' + name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}}]}


QUESTION = dict(question='这些下载主要如何使用？', options=[
    dict(id='offline', label='保留离线版本'), dict(id='online', label='可以重新下载', description='优先调查旧安装包。')])


class TaskInteractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'Downloads'
        self.folder.mkdir()
        (self.folder / 'chosen.zip').write_bytes(b'personal fixture')
        (self.folder / 'sibling.zip').write_bytes(b'another personal fixture')
        self.state = SimulationState(self.root / 'state')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(self.folder))['items'], 'fixture')
        self.queue, self.requests = [], []

        def transport(url, payload, headers, timeout):
            self.requests.append(payload)
            step = self.queue.pop(0)
            return step(payload) if callable(step) else step

        self.assistant = ModelAssistant(self.root / 'state', transport=transport)
        self.harness = TaskHarness(self.state, self.assistant)
        self.file = next(i for i in self.state.view()['items'] if i['name'] == 'chosen.zip')
        self.other = next(i for i in self.state.view()['items'] if i['name'] == 'sibling.zip')
        self.task = self.harness.create_analysis({'ids': [self.file['id']]})

    def tool(self, name, args):
        return self.harness.tool(self.task['id'], name, args, cancel=threading.Event())

    def direct(self, item=None, **overrides):
        item = item or self.file
        return self.harness.direct_plan(self.task['id'], dict(ids=[item['id']], action='delete',
            snapshots={item['id']: item['signature']}, **overrides))

    def question(self):
        self.tool('ask_user', QUESTION)
        return self.harness.public(self.task['id'])['question']

    def test_question_survives_restart_and_answer_is_not_execution_authorization(self):
        question = self.question()
        self.assertEqual(question['options'][0]['id'], 'offline')
        self.assertTrue(question['allow_text'])
        restored = TaskHarness(self.state, self.assistant)
        self.assertEqual(restored.public(self.task['id'])['question'], question)
        with self.assertRaises(AppError) as blocked:
            restored.claim(self.task['id'])
        self.assertEqual(blocked.exception.code, 'answer_required')
        result = restored.answer(self.task['id'], dict(version=question['version'], question_id=question['id'],
            option_id='online', text='只有我勾选的才清理。'))
        self.assertIsNone(result['question'])
        self.assertGreater(result['question_version'], question['version'])
        self.assertIsNone(result['plan'])
        answer = result['feedback'][-1]['answer']
        self.assertEqual(answer['option_id'], 'online')
        self.assertFalse(answer['execution_authorized'])
        self.assertEqual(self.state.history(), [])
        with self.assertRaises(AppError):
            restored.validate_approval(self.task['id'], 1, [self.file['id']])
        self.assertIn('只有我勾选', restored._context(restored._get(self.task['id']))['user_feedback'][-1]['answer']['text'])

    def test_stale_question_and_invalid_or_ambiguous_answers_do_not_consume_question(self):
        old = self.question()
        current = self.question()
        for payload in [dict(version=old['version'], option_id='offline'),
                        dict(version=current['version'], question_id=old['id'], option_id='offline'),
                        dict(version=current['version'], option_id='unknown'), dict(version=True, text='yes'),
                        dict(version=current['version']), dict(version=current['version'], text='x' * 2001),
                        dict(version=current['version'], skip=True, option_id='online')]:
            with self.assertRaises(AppError):
                self.harness.answer(self.task['id'], payload)
            self.assertEqual(self.harness.public(self.task['id'])['question']['id'], current['id'])
        self.harness.answer(self.task['id'], dict(version=current['version'], skip=True))
        with self.assertRaises(AppError) as replay:
            self.harness.answer(self.task['id'], dict(version=current['version'], skip=True))
        self.assertEqual(replay.exception.code, 'stale_question')
        self.assertTrue(self.harness.public(self.task['id'])['feedback'][-1]['answer']['skipped'])

    def test_free_text_is_valid_and_feedback_invalidates_question(self):
        question = self.question()
        result = self.harness.answer(self.task['id'], dict(version=question['version'], text='优先看安装包，资料保留。'))
        self.assertIsNone(result['feedback'][-1]['answer']['option_id'])
        question = self.question()
        self.harness.feedback(self.task['id'], '改查重复文件。')
        with self.assertRaises(AppError):
            self.harness.answer(self.task['id'], dict(version=question['version'], option_id='online'))

    def test_question_validates_stable_options_and_never_disables_free_text(self):
        for malformed in [dict(QUESTION, options=[QUESTION['options'][0]]),
                          dict(QUESTION, options=[QUESTION['options'][0]] * 2),
                          dict(QUESTION, options=[dict(id='../unsafe', label='X'), QUESTION['options'][1]]),
                          dict(QUESTION, allow_text=False)]:
            with self.assertRaises(AppError):
                self.tool('ask_user', malformed)
        self.assertIsNone(self.harness.public(self.task['id'])['question'])

    def test_model_question_yields_and_uses_structured_answer_on_next_turn(self):
        self.assistant.save_settings({'base_url': 'http://127.0.0.1:12345/v1', 'model': 'fixture'})
        self.queue = [model_call('ask_user', QUESTION)]
        self.harness.claim(self.task['id'])
        question = self.harness.investigate(self.task['id'])['question']
        self.assertEqual(len(self.requests), 1)
        self.harness.answer(self.task['id'], dict(version=question['version'], option_id='offline'))

        def next_turn(payload):
            context = json.loads(payload['messages'][1]['content'])
            self.assertEqual(context['user_feedback'][-1]['answer']['option_id'], 'offline')
            self.assertFalse(context['user_feedback'][-1]['answer']['execution_authorized'])
            return model_call('present_findings', dict(summary='先保留离线版本。', needs_input=True))

        self.queue = [next_turn]
        self.harness.claim(self.task['id'])
        self.harness.investigate(self.task['id'])
        self.assertEqual(self.state.history(), [])

    def test_user_direct_plan_executes_without_model_command_and_keeps_source(self):
        question = self.question()
        value = self.direct()
        self.assertEqual(value['plan']['origin'], 'user_direct')
        self.assertEqual([i['id'] for i in value['plan']['items']], [self.file['id']])
        self.assertTrue(value['plan']['items'][0]['personal_choice_required'])
        self.assertIsNone(value['task']['question'])
        self.assertEqual(self.state.history(), [])
        with self.assertRaises(AppError):
            self.harness.answer(self.task['id'], dict(version=question['version'], option_id='online'))
        self.harness.validate_approval(self.task['id'], value['plan']['version'], [self.file['id']])
        self.harness.claim(self.task['id'], execute=True)
        result = self.harness.investigate(self.task['id'], execute=True)
        self.assertEqual(result['status'], 'needs_model')
        self.assertEqual(result['last_result']['succeeded'], 1)
        self.assertEqual(result['last_result']['actual_reclaimed_bytes'], 0)
        self.assertTrue((self.folder / 'chosen.zip').exists())
        restored = TaskHarness(self.state, self.assistant)
        action = restored._context(restored._get(self.task['id']))['last_user_action']
        self.assertEqual(action['source'], 'user_direct')
        self.assertEqual(action['user_selected_ids'], [self.file['id']])
        self.assertEqual(action['result']['items'][0]['status'], 'succeeded')

    def test_direct_plan_scope_protection_keep_and_stale_preflight_are_enforced(self):
        with self.assertRaises(AppError) as scope:
            self.direct(self.other)
        self.assertEqual(scope.exception.code, 'outside_scope')
        self.assistant.save_settings({'protect_patterns': ['*chosen.zip']})
        with self.assertRaises(AppError):
            self.direct()
        self.assistant.save_settings({'protect_patterns': []})
        (self.folder / 'chosen.zip').write_bytes(b'changed since observation')
        with self.assertRaises(AppError) as stale:
            self.direct()
        self.assertEqual(stale.exception.code, 'stale_snapshot')
        self.assertIsNone(self.harness.public(self.task['id'])['plan'])
        self.state.publish(scan_directory(str(self.folder))['items'], 'fixture')
        self.file = next(i for i in self.state.view()['items'] if i['name'] == 'chosen.zip')
        self.harness.feedback(self.task['id'], keep_ids=[self.file['id']])
        with self.assertRaisesRegex(AppError, '保留'):
            self.direct()
        self.assertEqual(self.state.plans, {})

    def test_context_apps_do_not_expand_host_action_scope(self):
        software = dict(id='context-app', kind='software', name='Context', path='context', signature='v1',
                        risk='review', actions=['uninstall'], details={}, size_bytes=0)
        with patch('diskpilot.tasks.inventory', return_value={'items': [software]}):
            self.tool('collect_apps', {'section': 'software'})
        with self.assertRaises(AppError) as caught:
            self.harness.direct_plan(self.task['id'], dict(ids=[software['id']], action='uninstall'))
        self.assertEqual(caught.exception.code, 'outside_scope')

    def test_busy_or_invalid_direct_request_keeps_existing_plan_unchanged(self):
        first = self.direct()['plan']
        with self.assertRaises(AppError) as stale:
            self.harness.direct_plan(self.task['id'], dict(ids=[self.file['id']], action='delete',
                snapshots={self.file['id']: 'old-signature'}))
        self.assertEqual(stale.exception.code, 'selection_changed')
        self.assertEqual(self.harness.public(self.task['id'])['plan'], first)
        self.harness.claim(self.task['id'])
        with self.assertRaises(AppError) as busy:
            self.direct()
        self.assertEqual(busy.exception.code, 'task_busy')
        self.harness.release(self.task['id'])

    def test_direct_failure_is_recorded_and_feedback_does_not_claim_success(self):
        plan = self.direct()['plan']
        (self.folder / 'chosen.zip').write_bytes(b'changed after approval preview')
        self.harness.validate_approval(self.task['id'], plan['version'], [self.file['id']])
        self.harness.claim(self.task['id'], execute=True)
        result = self.harness.investigate(self.task['id'], execute=True)
        self.assertEqual(result['last_result']['status'], 'failed')
        self.assertEqual(result['last_result']['succeeded'], 0)
        action = self.harness._context(self.harness._get(self.task['id']))['last_user_action']
        self.assertEqual(action['result']['items'][0]['status'], 'failed')
        self.assertEqual(action['result']['actual_reclaimed_bytes'], 0)
        self.assertEqual(self.state.history(), [])

    def test_manual_history_awareness_includes_only_task_known_intersection(self):
        self.state.execute(self.state.prepare([self.file['id'], self.other['id']])['id'])
        context = self.harness._context(self.harness._get(self.task['id']))
        action = context['recent_host_actions'][0]
        self.assertEqual([i['id'] for i in action['items']], [self.file['id']])
        self.assertTrue(action['only_for_awareness'])
        self.assertNotIn('sibling.zip', json.dumps(context, ensure_ascii=False))

    def test_skill_guidance_has_versions_in_context_and_distinct_persisted_source(self):
        self.assistant.save_settings({'base_url': 'http://127.0.0.1:12345/v1', 'model': 'fixture'})
        self.queue = [model_call('read_skill', dict(skill_id='application-caches')),
                      model_call('present_findings', dict(summary='需要继续核对本机位置。', needs_input=True))]
        self.harness.claim(self.task['id'])
        value = self.harness.investigate(self.task['id'])
        loaded = value['loaded_skills'][0]
        self.assertEqual(loaded['id'], 'application-caches')
        self.assertTrue(loaded['sha256'])
        facts = self.harness.facts_page(self.task['id'])['items']
        guide = next(f for f in facts if f['tool'] == 'read_skill')
        self.assertEqual(guide['source'], 'skill_guidance')
        self.assertEqual(guide['freshness'], 'versioned_guidance')
        self.assertTrue(guide['result']['guidance_only'])
        self.assertIn('content', guide['result'])
        restored = TaskHarness(self.state, self.assistant)
        self.assertEqual(restored._context(restored._get(self.task['id']))['loaded_skills'][0]['sha256'], loaded['sha256'])
        self.assertEqual(self.state.history(), [])
        tools = {s['function']['name'] for s in tool_specs()}
        self.assertTrue({'list_skills', 'read_skill', 'ask_user'} <= tools)
        self.assertNotIn('direct_plan', tools)


class TaskInteractionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'files'
        self.folder.mkdir()
        (self.folder / 'chosen.zip').write_bytes(b'personal fixture')
        self.app = Application(self.root / 'store')
        self.addCleanup(self.app.close)
        self.app.state.publish(scan_directory(str(self.folder))['items'], 'fixture')
        self.item = next(i for i in self.app.view()['items'] if i['kind'] == 'file')
        self.task = self.app.harness.create_analysis({'ids': [self.item['id']]})

    def wait_job(self, result):
        for _ in range(200):
            job = self.app.job(result['job_id'])
            if job['status'] != 'running':
                return job
            time.sleep(.01)
        self.fail('Local job did not finish')

    def test_answer_job_and_host_direct_api_use_same_runtime_without_model(self):
        self.app.harness.tool(self.task['id'], 'ask_user', QUESTION, cancel=threading.Event())
        question = self.app.harness.public(self.task['id'])['question']
        result = self.app.post('/api/tasks/' + self.task['id'] + '/answer', dict(version=question['version'], skip=True))
        self.assertEqual(self.wait_job(result)['result']['task']['status'], 'needs_model')
        plan = self.app.post('/api/tasks/' + self.task['id'] + '/direct-plan', dict(ids=[self.item['id']], action='delete'))['plan']
        result = self.app.post('/api/tasks/' + self.task['id'] + '/approve', dict(version=plan['version'], ids=[self.item['id']]))
        task = self.wait_job(result)['result']['task']
        self.assertEqual(task['last_result']['succeeded'], 1)
        self.assertTrue((self.folder / 'chosen.zip').is_file())

    def test_ui_preferences_persist_in_separate_store_and_validate_allowlist(self):
        defaults = self.app.get('/api/ui-preferences', {})['preferences']
        self.assertEqual(defaults['theme'], 'system')
        changed = self.app.post('/api/ui-preferences', {'theme': 'light', 'developer_live': True})['preferences']
        self.assertTrue(changed['developer_conversation'])
        self.assertTrue(changed['developer_live'])
        self.assertFalse(self.app.trace.page()['enabled'])
        self.assertEqual(self.app.get('/api/ui-preferences', {})['preferences'], changed)
        self.assertNotIn('developer_live', self.app.assistant.public_settings())
        for malformed in ({'theme': '<script>'}, {'developer_live': 1}, {'key': 'private'}):
            with self.assertRaises(AppError):
                self.app.post('/api/ui-preferences', malformed)

    def test_skills_api_is_bounded_readonly_and_rejects_paths(self):
        catalog = self.app.get('/api/skills', {'limit': ['10']})
        self.assertTrue(catalog['guidance_only'])
        self.assertLessEqual(catalog['budget']['used'], 3000)
        self.assertTrue(catalog['items'])
        guide = self.app.get('/api/skills/application-caches', {})
        self.assertEqual(guide['id'], 'application-caches')
        self.assertLessEqual(guide['budget']['used'], 4000)
        for path in ('/api/skills/../../settings', '/api/skills/unknown'):
            with self.assertRaises(AppError):
                self.app.get(path, {})

    def test_theme_survives_new_port_and_static_html_keeps_csp(self):
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                store = self.root / ('theme-http-' + theme)
                for index in range(2):
                    server = create_server(store)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        if not index:
                            self.assertEqual(server.app.ui_preferences()['preferences']['theme'], 'system')
                            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/', timeout=5) as response:
                                initial_body = response.read().decode('utf-8')
                            self.assertIn('data-theme-preference="system"', initial_body)
                            self.assertLess(initial_body.index('<script src="/app.js">'), initial_body.index('<link rel="stylesheet"'))
                            server.app.post('/api/ui-preferences', {'theme': theme, 'developer_live': True})
                        else:
                            self.assertEqual(server.app.get('/api/ui-preferences', {})['preferences']['theme'], theme)
                        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/', timeout=5) as response:
                            self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
                            body = response.read().decode('utf-8')
                        self.assertIn('data-theme-preference="' + theme + '"', body)
                        self.assertNotIn('data-theme-preference="system"', body)
                        self.assertFalse(server.app.trace.page()['enabled'])
                    finally:
                        server.shutdown()
                        server.server_close()
                        server.app.close()
                        thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
