"""Task-owned read-only orchestration, with disposable files and no cloud model."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.orchestration import orchestration_definitions
from diskpilot.scanner import scan_directory
from diskpilot.server import Application
from diskpilot.simulation import AppError


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='panxu-orchestration-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'owned'; self.scope.mkdir()
        (self.scope / 'first.bin').write_bytes(b'owned duplicate fixture')
        (self.scope / 'second.bin').write_bytes(b'owned duplicate fixture')
        (self.scope / 'unique.bin').write_bytes(b'different')
        self.app = Application(self.root / 'profile'); self.addCleanup(self.app.close)
        self.harness = self.app.harness
        self.task = self.harness.create('分析自有测试目录', str(self.scope))

    def tool(self, name, args=None, task=None, **kwargs):
        return self.harness.tool((task or self.task)['id'], name, args or {},
                                 cancel=threading.Event(), **kwargs)

    def finish(self, work, task=None):
        key = (task or self.task)['id']
        self.harness.task_jobs.wait(key, work['id'])
        return self.tool('read_task_work', {'job_id': work['id']}, task)

    def error(self, code, callback):
        with self.assertRaises(AppError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def analysis_directory(self):
        report = scan_directory(str(self.scope))
        directory = next(row for row in report['items'] if row['kind'] == 'directory')
        self.app.state.publish([directory], 'owned-test')
        return self.harness.create_analysis({'ids': [directory['id']]}), directory

    def draft(self, task=None):
        return self.tool('draft_user_tool', {'type': 'files', 'title': '旧临时文件',
            'rules': {'includes': ['*.bin'], 'min_age_days': 1}}, task,
            provenance={'source': 'model', 'provider': 'owned', 'model': 'fixture'})

    def test_discovery_and_description_expose_exact_parameters_without_inspecting(self):
        names = {row[0] for row in orchestration_definitions()}
        self.assertTrue({'describe_cleanup_tool', 'start_duplicate_scan', 'draft_user_tool'} <= names)
        with patch.object(self.harness.cleanup_tools, 'inspect', side_effect=AssertionError('metadata only')):
            result = self.tool('describe_cleanup_tool', {'tool_id': 'unity_cache'})
        schema = result['input_schema']
        self.assertEqual(schema['required'], ['project_path'])
        self.assertEqual(schema['properties']['project_path']['anyOf'][0]['format'], 'path')
        self.assertEqual(schema['properties']['project_path']['anyOf'][1]['required'], ['scope_id'])
        self.assertIn('scope_id', result['path_binding']['value'])
        self.assertFalse(schema['additionalProperties'])
        browser = self.tool('describe_cleanup_tool', {'tool_id': 'browser_cache'})
        self.assertEqual(browser['input_schema']['properties']['browser']['default'], 'edge')
        self.assertIn('chrome', browser['input_schema']['properties']['browser']['enum'])
        self.assertEqual(self.app.state.history(), [])

    def test_system_task_diagnoses_once_before_model_configuration_and_records_fact(self):
        spec = dict(goal='清理系统盘', title='owned', kind='filesystem', paths=[(str(self.scope), 'owned')])
        with patch('diskpilot.tasks.resolve_preset', return_value=spec):
            task = self.harness.create(preset='system_cleanup')
        original = self.harness.diagnostics.scan
        calls = []
        def owned_scan(paths, **kwargs):
            self.assertEqual(paths, [str(self.scope)])
            self.assertEqual(kwargs['task_id'], task['id'])
            calls.append(paths)
            return original(paths, **kwargs)
        with (patch.object(self.harness.diagnostics, 'scan', side_effect=owned_scan),
              patch('diskpilot.diagnostics.system_configuration', return_value={}),
              patch('diskpilot.diagnostics.volume_snapshot', return_value={}),
              patch('diskpilot.diagnostics.candidate_paths', return_value=([self.scope], 'owned')),
              patch.object(self.harness.cleanup_tools, 'inspect', side_effect=AssertionError('no48 deep scans')),
              patch.object(self.app.assistant, 'agent_turn', side_effect=AssertionError('no cloud'))):
            first = self.harness.investigate(task['id'])
            second = self.harness.investigate(task['id'])
        self.assertEqual(first['status'], 'needs_model')
        self.assertEqual(second['status'], 'needs_model')
        self.assertEqual(len(calls), 1)
        saved = self.harness._get(task['id'])
        snapshot = self.harness.diagnostics.summary(saved['diagnostic_snapshot_id'], task_id=task['id'])
        self.assertEqual(len(snapshot['items']), 48)
        self.assertFalse(snapshot['coverage']['all_tools_fully_diagnosed'])
        facts = self.harness.facts_page(task['id'])
        self.assertEqual(sum(row['tool'] == 'diagnostics_work' for row in facts['items']), 1)
        read = self.tool('read_cleanup_diagnostics', {'snapshot_id': snapshot['snapshot_id'],
            'tool_ids': ['hibernation', 'pagefile']}, task)
        self.assertEqual({row['tool_id'] for row in read['items']}, {'hibernation', 'pagefile'})

    def test_duplicate_worker_adopts_new_members_and_fact_exactly_once(self):
        self.assertEqual(self.harness._get(self.task['id'])['known_ids'], [])
        work = self.tool('start_duplicate_scan')
        done = self.finish(work)
        self.assertEqual(done['status'], 'completed')
        self.assertTrue(done['fact_id'])
        groups = self.tool('read_duplicate_groups', {'job_id': work['id'], 'limit': 1})
        self.assertEqual(groups['summary']['content_groups'], 1)
        self.assertEqual(len(groups['groups']), 1)
        group = groups['groups'][0]
        first = self.tool('read_duplicate_members', {'job_id': work['id'], 'group_id': group['group_id'], 'limit': 1})
        second = self.tool('read_duplicate_members', {'job_id': work['id'], 'group_id': group['group_id'], 'offset': 1})
        self.assertTrue(first['has_more']); self.assertFalse(second['has_more'])
        members = first['items'] + second['items']
        self.assertEqual({row['name'] for row in members}, {'first.bin', 'second.bin'})
        task = self.harness._get(self.task['id'])
        ids = {row['id'] for row in members}
        self.assertTrue(ids <= set(task['known_ids']))
        self.assertTrue(all(self.harness._action_target(task, row) for row in self.app.state.view()['items'] if row['id'] in ids))
        self.assertEqual(task['usage']['hash_bytes'], 2 * len(b'owned duplicate fixture'))
        again = self.tool('read_task_work', {'job_id': work['id']})
        self.assertEqual(again['fact_id'], done['fact_id'])
        self.assertEqual(self.harness._get(self.task['id'])['usage']['hash_bytes'], task['usage']['hash_bytes'])
        facts = self.harness.facts_page(task['id'])
        self.assertEqual(sum(row['tool'] == 'duplicates_work' for row in facts['items']), 1)
        self.assertEqual(self.app.state.history(), [])

    def test_analysis_directory_can_adopt_new_descendants_without_sibling_expansion(self):
        task, directory = self.analysis_directory()
        work = self.tool('start_duplicate_scan', {'directory_ids': [directory['id']]}, task)
        self.finish(work, task)
        saved = self.harness._get(task['id'])
        self.assertEqual(len(saved['target_ids']), 3)
        self.assertTrue(set(saved['target_ids']) <= set(saved['known_ids']))
        self.assertEqual(saved['action_directories'], [str(self.scope)])
        outside = self.root / 'outside'; outside.mkdir()
        (outside / 'first.bin').write_bytes(b'owned duplicate fixture')
        (outside / 'second.bin').write_bytes(b'owned duplicate fixture')
        self.error('outside_scope', lambda: self.tool('start_duplicate_scan', {'scope_ids': ['not-owned']}, task))
        self.assertFalse(any(row['path'].startswith(str(outside)) for row in self.app.state.view()['items']))

    def test_file_only_analysis_cannot_turn_observed_sibling_context_into_scan_scope(self):
        context = self.scope / 'sibling'; context.mkdir()
        report = scan_directory(str(self.scope))
        item = next(row for row in report['items'] if row['name'] == 'first.bin')
        sibling = next(row for row in report['items'] if row['path'] == str(context))
        self.app.state.publish([item, sibling], 'owned-context')
        task = self.harness.create_analysis({'ids': [item['id']]})
        saved = self.harness._get(task['id']); saved['known_ids'].append(sibling['id']); self.harness._save(saved)
        self.error('work_scope_required', lambda: self.tool('start_duplicate_scan', task=task))
        self.error('outside_scope', lambda: self.tool('start_duplicate_scan', {'directory_ids': [sibling['id']]}, task))
        self.assertEqual(self.harness.task_jobs.recent(task['id']), [])

    def test_duplicate_scope_validation_uses_saved_request_before_publishing(self):
        small = self.scope / 'small'; small.mkdir()
        report = scan_directory(str(small))
        directory = next(row for row in report['items'] if row['kind'] == 'directory')
        self.app.state.publish([directory], 'owned-child')
        saved = self.harness._get(self.task['id']); saved['known_ids'].append(directory['id']); self.harness._save(saved)
        from diskpilot.dedup import run_duplicate_scan
        wrong = run_duplicate_scan({'paths': [str(self.scope)]})
        with patch('diskpilot.orchestration.run_duplicate_scan', return_value=wrong):
            work = self.tool('start_duplicate_scan', {'directory_ids': [directory['id']]})
            self.harness.task_jobs.wait(self.task['id'], work['id'])
        self.error('outside_scope', lambda: self.tool('read_task_work', {'job_id': work['id']}))
        self.assertEqual(self.harness._get(self.task['id'])['known_ids'], [directory['id']])

    def test_result_reuse_refresh_and_cross_task_boundaries(self):
        work = self.tool('start_duplicate_scan'); self.finish(work)
        again = self.tool('start_duplicate_scan')
        self.assertTrue(again['reused']); self.assertEqual(work['id'], again['id'])
        fresh = self.tool('start_duplicate_scan', {'refresh': True}); self.finish(fresh)
        self.assertNotEqual(work['id'], fresh['id'])
        other = self.harness.create('另一任务', str(self.scope))
        for name in ('read_task_work', 'cancel_task_work', 'read_duplicate_groups'):
            self.error('outside_scope', lambda name=name: self.tool(name, {'job_id': work['id']}, other))
        self.error('outside_scope', lambda: self.tool('read_duplicate_members', {'job_id': work['id'], 'group_id': 'other-group'}))
        with self.assertRaises(AppError):
            self.tool('read_duplicate_groups', {'job_id': work['id'], 'limit': True})

    def test_cancellation_preserves_only_partial_readonly_facts(self):
        entered = threading.Event()
        def slow(request, *, cancel, progress, **kwargs):
            progress({'message': 'owned metadata', 'scanned_entries': 1}); entered.set(); cancel.wait(2)
            return dict(source='local_duplicate_scan', status='cancelled', partial=True,
                        summary={'scanned_entries': 1, 'total_groups': 0}, groups=[], bytes_read=0)
        with patch('diskpilot.orchestration.run_duplicate_scan', side_effect=slow):
            work = self.tool('start_duplicate_scan'); self.assertTrue(entered.wait(1))
            cancelled = self.tool('cancel_task_work', {'job_id': work['id']})
            self.assertEqual(cancelled['status'], 'cancelling')
            done = self.finish(work)
        self.assertEqual(done['status'], 'cancelled'); self.assertTrue(done['partial']); self.assertTrue(done['fact_id'])
        groups = self.tool('read_duplicate_groups', {'job_id': work['id']})
        self.assertTrue(groups['partial']); self.assertEqual(groups['groups'], [])
        self.assertEqual(self.app.state.history(), [])

    def test_drafts_use_task_scopes_and_model_projection_never_exposes_roots(self):
        draft = self.draft()
        self.assertEqual(draft['status'], 'draft'); self.assertEqual(draft['root_count'], 1)
        self.assertNotIn('roots', draft['rules']); self.assertNotIn('source', draft)
        actual = self.harness.user_tools._get_draft(draft['draft_id'])
        self.assertEqual(actual['definition']['rules']['roots'], [str(self.scope)])
        self.assertEqual(actual['source'], {'task_id': self.task['id']})
        self.assertEqual(actual['provenance']['model'], 'fixture')
        saved = self.harness.user_tools.save(draft['draft_id'])
        description = self.tool('describe_user_tool', {'tool_id': saved['id'], 'version': saved['version']})
        self.assertNotIn('roots', description['rules'])
        matches = self.tool('inspect_user_tool', {'tool_id': saved['id'], 'version': saved['version']})
        self.assertIn('run_id', matches); self.assertNotIn('items', matches)
        self.assertNotIn('definition', matches)
        outside = self.root / 'outside'; outside.mkdir()
        other = self.harness.create('其他目录', str(outside))
        with patch.object(self.harness.user_tools, 'inspect', side_effect=AssertionError('outside scope must not inspect')):
            need = self.tool('inspect_user_tool', {'tool_id': saved['id'], 'version': saved['version']}, other)
        self.assertEqual(need['status'], 'needs_user_scope'); self.assertNotIn('roots', need['tool']['rules'])

    def test_draft_discovery_inspection_and_suggestion_require_current_task_ownership(self):
        draft = self.draft()
        other = self.harness.create('另一个任务', str(self.scope))
        self.assertIn(draft['draft_id'], [row.get('draft_id') for row in self.tool('discover_user_tools')['items']])
        self.assertNotIn(draft['draft_id'], [row.get('draft_id') for row in self.tool('discover_user_tools', task=other)['items']])
        self.error('outside_scope', lambda: self.tool('inspect_user_tool', {'draft_id': draft['draft_id']}, other))
        suggestion = dict(items=[dict(id='keep-rule', title='保存日常规则', explanation='反复需求可保存为工具',
            impact='先审阅，不会执行', options=[dict(id='open', label='查看草案', kind='user_tool', draft_id=draft['draft_id']),
                                              dict(id='skip', label='暂不保存', kind='answer')])])
        self.error('outside_scope', lambda: self.tool('record_specialist_suggestions', suggestion, other))
        result = self.tool('record_specialist_suggestions', suggestion)
        self.assertFalse(result['execution_authorized'])
        answer = self.harness.answer_specialist(self.task['id'], 'keep-rule', 'open', version=1)
        self.assertEqual(answer['user_tool'], {'draft_id': draft['draft_id']})
        self.assertFalse(answer['execution_authorized'])
        self.assertEqual(self.app.state.history(), [])

    def test_path_parameter_scope_binding_resolves_locally_and_rejects_foreign_refs(self):
        task = self.harness._get(self.task['id'])
        parameters = {'project_path': {'scope_id': 'root'}}
        resolved = self.harness._check_tool_read_scope(task, 'unity_cache', parameters)
        self.assertEqual(parameters, {'project_path': {'scope_id': 'root'}})
        self.assertEqual(resolved, {'tool_id': 'unity_cache', 'parameters': {'project_path': str(self.scope)}})
        self.error('outside_scope', lambda: self.harness._check_tool_read_scope(task, 'unity_cache',
            {'project_path': {'scope_id': 'another-task-root'}}))
        self.error('invalid_work_scope', lambda: self.harness._check_tool_read_scope(task, 'unity_cache',
            {'project_path': {'scope_id': 'root', 'path': str(self.scope)}}))
        self.error('invalid_work_scope', lambda: self.harness._check_tool_read_scope(task, 'browser_cache',
            {'browser': {'scope_id': 'root'}}))
        self.error('outside_scope', lambda: self.harness._check_tool_read_scope(task, 'unity_cache',
            {'project_path': str(self.root)}))
        description = self.tool('describe_cleanup_tool', {'tool_id': 'unity_cache'})
        self.assertNotIn(str(self.scope), str(description))
        report = scan_directory(str(self.scope))
        item = next(row for row in report['items'] if row['kind'] == 'file')
        self.app.state.publish([item], 'owned-file')
        narrow = self.harness.create_analysis({'ids': [item['id']]})
        self.error('outside_scope', lambda: self.harness._check_tool_read_scope(self.harness._get(narrow['id']),
            'unity_cache', {'project_path': {'scope_id': 'root'}}))

    def test_model_preset_draft_binds_paths_and_filters_private_connection_provenance(self):
        draft = self.tool('draft_user_tool', {'type': 'preset', 'title': '项目缓存',
            'builtin': {'tool_id': 'unity_cache', 'parameters': {'project_path': {'scope_id': 'root'}}}},
            provenance={'provider': 'owned', 'model': 'fixture', 'protocol': 'openai',
                        'generated_at': '2026-11-02', 'connection_id': 'must-not-persist'})
        self.assertEqual(draft['builtin'], {'tool_id': 'unity_cache', 'parameters': {}})
        self.assertNotIn('connection_id', draft['provenance'])
        actual = self.harness.user_tools._get_draft(draft['draft_id'])
        self.assertEqual(actual['definition']['builtin']['parameters']['project_path'], str(self.scope))
        self.assertNotIn('connection_id', actual['provenance'])
        self.assertEqual(actual['provenance']['model'], 'fixture')
        inspection = dict(read_only=True, presentation={'status': 'unknown', 'summary': 'owned'})
        with patch.object(self.harness.cleanup_tools, 'inspect', return_value=inspection) as inspect:
            result = self.tool('inspect_user_tool', {'draft_id': draft['draft_id']})
        inspect.assert_called_once_with(tool_id='unity_cache', parameters={'project_path': str(self.scope)})
        self.assertNotIn(str(self.scope), str(result))
        self.assertEqual(result['draft_id'], draft['draft_id'])
        self.assertEqual(self.app.state.history(), [])

    def test_saved_file_tool_keeps_origin_for_feedback_without_original_scope_guard(self):
        draft = self.tool('draft_user_tool', {'type': 'files', 'title': '文件规则',
            'rules': {'includes': ['*.bin']}})
        saved = self.harness.user_tools.save(draft['draft_id'])
        run = self.harness.user_tools.inspect(tool_id=saved['id'], version=saved['version'])
        self.assertEqual(run['source'], {'task_id': self.task['id']})
        prepared = self.harness.user_tools.prepare(run['run_id'])
        task = self.harness._get(prepared['task']['id'])
        self.assertEqual(task['user_tool_source_task_id'], self.task['id'])
        self.assertIsNone(task.get('cleanup_source_task_id'))
        self.assertEqual(task['plan']['user_tool']['run_id'], run['run_id'])
        self.assertEqual(self.app.state.history(), [])

    def test_saved_builtin_preset_freezes_origin_task_for_feedback(self):
        draft = self.tool('draft_user_tool', {'type': 'preset', 'title': 'uv旧缓存',
            'builtin': {'tool_id': 'uv_cache', 'parameters': {'operation': 'prune'}}})
        saved = self.harness.user_tools.save(draft['draft_id'])
        with patch.object(self.harness.cleanup_tools, 'inspect', return_value={
                'presentation': {'status': 'unknown', 'summary': 'owned'}}):
            run = self.harness.user_tools.inspect(tool_id=saved['id'], version=saved['version'])
        prepared = self.harness.user_tools.prepare(run['run_id'])
        plan = prepared['specialist_plan']
        self.assertEqual(plan['source'], {'task_id': self.task['id']})
        self.assertEqual(self.harness.cleanup_tools.get_plan(plan['id'])['source'], plan['source'])
        self.assertEqual(plan['user_tool']['run_id'], run['run_id'])
        self.assertEqual(self.app.state.history(), [])


if __name__ == '__main__':
    unittest.main()
