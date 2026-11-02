import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness
from tests.prompt_fixture import parse_host_data


def call(name, arguments, key='call_1'):
    return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': key, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}}]}


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'downloads'
        self.scope.mkdir()
        (self.scope / 'a.zip').write_bytes(b'test-only-duplicate')
        (self.scope / 'b.zip').write_bytes(b'test-only-duplicate')
        self.state = SimulationState(self.root / 'state')
        self.requests = []
        self.steps = []

        def transport(url, payload, headers, timeout):
            self.requests.append(payload)
            if not self.steps:
                raise AssertionError('Unexpected model request')
            step = self.steps.pop(0)
            return step(payload) if callable(step) else step
        self.assistant = ModelAssistant(self.root / 'state', transport=transport)
        self.assistant.save_settings({'base_url': 'http://127.0.0.1:12345/v1', 'model': 'fixture-model'})
        self.harness = TaskHarness(self.state, self.assistant)
        self.task_id = self.harness.create('整理这些下载，个人文件由我选择。', str(self.scope))['id']

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def run_turn(self, execute=False, cancel=None):
        self.harness.claim(self.task_id, execute=execute)
        return self.harness.investigate(self.task_id, execute=execute, cancel=cancel)

    def tool(self, name, args):
        return self.harness.tool(self.task_id, name, args, cancel=threading.Event())

    def observed_files(self):
        self.tool('survey_scope', {'scope_id': 'root'})
        task = self.harness._get(self.task_id)
        return [i for i in self.harness._items(task) if i['kind'] == 'file']

    @staticmethod
    def proposal(items):
        return {'summary': '这些副本可以整理，原文件是否保留由你选择。', 'items': [
            {'id': i['id'], 'action': 'delete', 'reason': '已经核对内容与用途线索。',
             'impact': '去掉这份副本后仍需保留需要的原件。'} for i in items]}

    def test_model_drives_scan_comparison_plan_and_feedback_after_execution(self):
        self.steps = [call('survey_scope', {'scope_id': 'root'})]
        def compare(payload):
            result = parse_host_data(payload['messages'][-1]['content'])
            ids = [i['id'] for i in result['items'] if i['kind'] == 'file']
            self.assertEqual(len(ids), 2)
            return call('compare_files', {'ids': ids}, 'compare')
        def propose(payload):
            result = parse_host_data(payload['messages'][-1]['content'])
            self.assertTrue(result['groups'][0]['verified'])
            entry = {'id': result['groups'][0]['ids'][1]}
            return call('propose_plan', self.proposal([entry]), 'plan')
        self.steps.extend([compare, propose])
        reviewed = self.run_turn()
        self.assertEqual(reviewed['status'], 'awaiting_review')
        self.assertEqual(len(self.requests), 3)
        entry = reviewed['plan']['items'][0]
        self.assertTrue(entry['personal_choice_required'])
        self.assertFalse(entry['default_selected'])
        self.harness.validate_approval(self.task_id, reviewed['plan']['version'], [entry['id']])
        def verify_context(payload):
            context = parse_host_data(payload['messages'][1]['content'])
            self.assertEqual(context['last_result']['succeeded'], 1)
            self.assertEqual(context['last_result']['actual_reclaimed_bytes'], 0)
            return call('list_items', {'kind': 'file'}, 'remaining')
        def finish(payload):
            result = parse_host_data(payload['messages'][-1]['content'])
            self.assertNotIn(entry['id'], [i['id'] for i in result['items']])
            return call('present_findings', {'summary': '已经核对本轮结果，剩下的原件建议保留。', 'needs_input': False}, 'done')
        self.steps = [verify_context, finish]
        finished = self.run_turn(execute=True)
        self.assertEqual(finished['status'], 'completed')
        self.assertTrue(all(p.exists() for p in (self.scope / 'a.zip', self.scope / 'b.zip')))
        self.assertEqual(finished['last_result']['backend'], 'simulation')
        self.assertEqual(len(self.requests), 3)  # Execution finalization needs no model round trip.
        self.assertEqual(len(self.steps), 2)
        continued = self.run_turn()  # The user explicitly asks for further investigation.
        self.assertEqual(continued['status'], 'completed')
        self.assertEqual(self.steps, [])
        with self.assertRaises(AppError):
            self.harness.validate_approval(self.task_id, reviewed['plan']['version'], [entry['id']])

    def test_user_kept_file_cannot_be_reintroduced_through_parent_directory(self):
        files = self.observed_files()
        self.harness.feedback(self.task_id, keep_ids=[files[0]['id']])
        # Complete the root without replacing the scope or losing the choice.
        root = next(i for i in self.harness._items(self.harness._get(self.task_id)) if i['kind'] == 'directory')
        self.tool('inspect_directory', {'id': root['id']})
        with self.assertRaisesRegex(AppError, '保留'):
            self.tool('propose_plan', self.proposal([root]))
        with self.assertRaisesRegex(AppError, '保留'):
            self.tool('propose_plan', self.proposal([files[0]]))
        self.tool('propose_plan', self.proposal([files[1]]))
        self.assertEqual(len(self.harness.public(self.task_id)['plan']['items']), 1)

    def test_feedback_invalidates_old_approval_and_survives_restart(self):
        files = self.observed_files()
        self.tool('propose_plan', self.proposal(files))
        version = self.harness.public(self.task_id)['plan']['version']
        self.harness.feedback(self.task_id, 'a.zip需要保留离线版。', [files[0]['id']])
        with self.assertRaises(AppError):
            self.harness.validate_approval(self.task_id, version, [files[0]['id']])
        reloaded = TaskHarness(self.state, self.assistant)
        context = reloaded._context(reloaded._get(self.task_id))
        self.assertEqual(context['user_choices'][0]['choice'], 'keep')
        self.assertIn('离线', context['user_feedback'][-1]['content'])

    def test_unknown_and_cross_task_targets_cannot_be_queried_or_planned(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'private.txt').write_text('outside-sentinel')
        from diskpilot.scanner import scan_directory
        observed = scan_directory(str(outside))
        self.state.publish(observed['items'], 'other-task')
        foreign = next(i for i in observed['items'] if i['kind'] == 'file')
        for name, args in [('inspect_items', {'ids': [foreign['id']]}),
                           ('inspect_directory', {'id': observed['items'][0]['id']}),
                           ('propose_plan', self.proposal([foreign]))]:
            with self.assertRaises(AppError):
                self.tool(name, args)
        self.assertEqual((outside / 'private.txt').read_text(), 'outside-sentinel')

    def test_scope_replaced_by_junction_or_other_directory_is_rejected(self):
        # Replacing the directory changes the observed filesystem identity.
        moved = self.root / 'original-scope'
        self.scope.rename(moved)
        self.scope.mkdir()
        with self.assertRaisesRegex(AppError, '替换'):
            self.tool('survey_scope', {'scope_id': 'root'})

    def test_busy_and_stale_or_empty_approval_cannot_change_a_task(self):
        files = self.observed_files()
        self.tool('propose_plan', self.proposal(files))
        for version, ids in [(999, [files[0]['id']]), (1, []), (1, ['foreign'])]:
            with self.assertRaises(AppError):
                self.harness.validate_approval(self.task_id, version, ids)
        self.harness.claim(self.task_id)
        with self.assertRaises(AppError):
            self.harness.claim(self.task_id)
        with self.assertRaises(AppError):
            self.harness.feedback(self.task_id, 'new requirement')
        self.harness.release(self.task_id)

    def test_cancel_after_request_returns_does_not_apply_its_tool_call(self):
        cancellation = threading.Event()
        def provider(payload):
            cancellation.set()
            return call('survey_scope', {'scope_id': 'root'})
        self.steps = [provider]
        result = self.run_turn(cancel=cancellation)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.state.view()['items'], [])

    def test_unknown_tool_is_reported_to_model_and_can_be_repaired(self):
        self.steps = [call('shell', {'command': 'delete something'})]
        def recover(payload):
            self.assertIn('error', parse_host_data(payload['messages'][-1]['content']))
            return call('survey_scope', {'scope_id': 'root'}, 'scan')
        self.steps += [recover, call('ask_user', {'question': '是否需要保留离线安装包？', 'options': [
            {'id': 'keep', 'label': '保留离线安装包'}, {'id': 'unused', 'label': '已经不需要'}]}, 'question')]
        result = self.run_turn()
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(len(self.requests), 3)

    def test_budget_and_provider_failure_are_not_reported_as_success(self):
        self.steps = [call('list_items', {}) for _ in range(6)]
        result = self.run_turn()
        self.assertEqual(result['status'], 'budget_exhausted')
        self.steps = [lambda _: (_ for _ in ()).throw(OSError('Bearer not-a-real-provider-secret'))]
        result = self.run_turn()
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('provider-secret', json.dumps(result))

    def test_restart_marks_interrupted_task_paused_and_keeps_evidence(self):
        self.observed_files()
        self.harness.claim(self.task_id)
        recovered = TaskHarness(self.state, self.assistant)
        self.assertEqual(recovered.public(self.task_id)['status'], 'paused')
        self.assertTrue(recovered._get(self.task_id)['known_ids'])

    def test_unconfigured_model_does_not_fabricate_an_agent_result(self):
        self.assistant.save_settings({'base_url': '', 'model': ''})
        self.assertEqual(self.run_turn()['status'], 'needs_model')
        self.assertEqual(self.requests, [])

    def test_incomplete_directory_and_user_protection_remain_explicit_facts(self):
        folder = self.scope / 'nested'
        folder.mkdir()
        (folder / 'work.txt').write_text('private fixture')
        self.observed_files()
        root = next(i for i in self.harness._items(self.harness._get(self.task_id))
                    if i['kind'] == 'directory' and i['path'] == str(self.scope))
        with self.assertRaisesRegex(AppError, '完整'):
            self.tool('propose_plan', self.proposal([root]))
        file = next(i for i in self.harness._items(self.harness._get(self.task_id)) if i['kind'] == 'file')
        self.assistant.save_settings({'protect_patterns': [file['name']]})
        with self.assertRaisesRegex(AppError, '保护'):
            self.tool('propose_plan', self.proposal([file]))

    def test_application_decision_can_use_model_judgment_without_recipe_allow_flag(self):
        from diskpilot.windows import _item
        app = _item('software', 'fixture-only-app', '测试用编辑器', 'Test Publisher', '已安装软件', 'uninstall', {'synthetic': False})
        with patch('diskpilot.tasks.inventory', return_value={'items': [app], 'warnings': []}):
            self.tool('collect_apps', {'section': 'software'})
        # Software collected to explain a file task remains context. Make the
        # intended software analysis explicit before proposing its uninstall.
        self.task_id = self.harness.create_analysis({'section': 'software', 'ids': [app['id']]})['id']
        self.tool('propose_plan', {'summary': '结合需求提出软件整理方案。', 'items': [{
            'id': app['id'], 'action': 'uninstall', 'reason': '这项仅是待用户确认的软件建议。',
            'impact': '卸载可能移除配置，实际处理前需要确认。'}]})
        item = self.harness.public(self.task_id)['plan']['items'][0]
        self.assertFalse(item['personal_choice_required'])
        self.assertEqual(item['action'], 'uninstall')

    def test_undo_is_visible_to_the_following_model_turn(self):
        files = self.observed_files()
        self.tool('propose_plan', self.proposal(files[:1]))
        self.harness.validate_approval(self.task_id, 1, [files[0]['id']])
        self.steps = [call('present_findings', {'summary': '本轮核对完成。', 'needs_input': False})]
        finished = self.run_turn(execute=True)
        self.state.undo(finished['last_result']['items'][0]['batch_id'])
        context = self.harness._context(self.harness._get(self.task_id))
        self.assertEqual(context['last_result']['items'][0]['current_state'], 'undone')
        self.assertIn(files[0]['id'], [i['id'] for i in self.harness._items(self.harness._get(self.task_id))])

    def test_keys_and_full_paths_absent_from_model_requests_and_task_text(self):
        key = 'fixture-secret-only-for-this-local-test'
        self.assistant.save_settings({'api_key': key})
        self.harness.feedback(self.task_id, f'这项请保留，路径 {self.scope}，Bearer {key}')
        self.steps = [call('survey_scope', {'scope_id': 'root'}),
                      call('present_findings', {'summary': '已查看，具体项目由你勾选。', 'needs_input': False}, 'end')]
        self.run_turn()
        sent = json.dumps(self.requests, ensure_ascii=False)
        self.assertNotIn(str(self.scope), sent)
        self.assertNotIn(str(self.scope).replace('\\', '\\\\'), sent)
        self.assertNotIn(key, sent)
        self.assertNotIn(key, self.state.db.execute('SELECT payload FROM tasks').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
