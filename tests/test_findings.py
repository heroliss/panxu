"""Modular public findings use owned files, local transport and no execution."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness, tool_specs


class FindingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'downloads'
        self.scope.mkdir()
        for index in range(12):
            (self.scope / f'installer-{index:02d}.zip').write_bytes(b'disposable-fixture')
        self.state = SimulationState(self.root / 'data')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(self.scope))['items'], 'fixture')

        def no_model(*_args):
            raise AssertionError('These host checks must not call a model.')

        self.assistant = ModelAssistant(self.root / 'data', transport=no_model)
        self.assistant.save_settings(dict(provider='custom', model='fixture-a', base_url='http://127.0.0.1:12345/v1'))
        self.harness = TaskHarness(self.state, self.assistant)
        self.files = [row for row in self.state.snapshot([])['items'] if row['kind'] == 'file']
        self.key = self.harness.create_analysis({'ids': [row['id'] for row in self.files]})['id']
        self.source = dict(source='llm', provider='custom', protocol='chat_completions', model='fixture-a',
                           generated_at='2026-11-02T01:00:00+00:00')
        self.modules = dict(highlights=['安装介质可重新获取。'], cautions=['离线重装需求由你决定。'], coverage=['只核对当前选择的文件。'])

    def tool(self, name, args, *, source=None):
        return self.harness.tool(self.key, name, args, cancel=threading.Event(), provenance=source)

    def present(self, **changes):
        args = dict(summary='请先查看这些安装包的去留建议。', needs_input=True, briefing=copy.deepcopy(self.modules))
        args.update(changes)
        self.tool('present_findings', args, source=self.source)
        return self.harness.public(self.key)

    def proposal(self, **changes):
        args = dict(summary='先看这份副本。', briefing=copy.deepcopy(self.modules), items=[dict(
            id=self.files[0]['id'], action='delete', reason='由你明确决定是否保留。', impact='这里只记录模拟。')])
        args.update(changes)
        self.tool('propose_plan', args, source=self.source)
        return self.harness.public(self.key)

    def test_optional_modules_are_persisted_with_original_host_source(self):
        first = self.present()
        self.assertEqual(first['briefing'], self.modules)
        self.assertEqual(first['briefing_provenance'], self.source)
        self.assistant.save_settings({'model': 'fixture-b'})
        recovered = TaskHarness(self.state, self.assistant)
        self.assertEqual(recovered.public(self.key)['briefing'], self.modules)
        self.assertEqual(recovered.public(self.key)['briefing_provenance'], self.source)
        first['briefing']['highlights'].clear()
        self.assertEqual(recovered.public(self.key)['briefing'], self.modules)
        self.assertFalse(self.state.history())

    def test_plan_modules_share_version_and_host_source(self):
        first = self.proposal()
        self.assertEqual(first['briefing'], first['plan']['briefing'])
        self.assertEqual(first['briefing_provenance'], first['plan']['briefing_provenance'])
        self.assertEqual(first['plan']['briefing_provenance'], self.source)
        version = first['plan']['version']
        second = self.proposal(briefing={'highlights': ['本轮只关注另一项取舍。']})
        self.assertEqual(second['plan']['version'], version + 1)
        self.assertNotEqual(second['briefing'], first['briefing'])
        self.assertEqual(first['plan']['briefing'], self.modules)
        self.assertFalse(second['plan']['items'][0]['default_selected'])

    def test_legacy_full_summary_is_preserved_without_invented_modules(self):
        text = '原始结论与完整依据。' * 95
        self.tool('present_findings', dict(summary=text, needs_input=True))
        restored = self.harness.public(self.key)
        self.assertEqual(restored['summary'], text)
        self.assertEqual(restored['briefing'], {})
        self.assertEqual(restored['briefing_provenance']['source'], 'unknown')
        value = self.harness._get(self.key)
        for field in ('briefing', 'briefing_provenance', 'briefing_binding'):
            value.pop(field, None)
        with self.state.db:
            self.state.db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(value), self.key))
        self.assertEqual(self.harness.public(self.key)['summary'], text)
        self.assertEqual(self.harness.public(self.key)['briefing'], {})

    def test_legacy_plan_has_empty_modules_and_does_not_gain_current_source(self):
        self.tool('propose_plan', dict(summary='未分模块的旧方案', items=[dict(id=self.files[0]['id'], action='delete', reason='用户选择', impact='仅模拟')]))
        value = self.harness._get(self.key)
        value['plan'].pop('briefing', None)
        value['plan'].pop('briefing_provenance', None)
        self.harness._save(value)
        public = self.harness.public(self.key)
        self.assertEqual(public['plan']['briefing'], {})
        self.assertEqual(public['plan']['briefing_provenance']['source'], 'unknown')

    def test_new_investigation_clears_old_modules_without_relabeling_old_plan(self):
        old = self.proposal()
        self.harness.claim(self.key)
        try:
            current = self.harness.public(self.key)
            self.assertEqual(current['briefing'], {})
            self.assertEqual(current['briefing_provenance']['source'], 'unknown')
            self.assertEqual(current['plan']['briefing'], old['plan']['briefing'])
            self.assertEqual(current['plan']['briefing_provenance'], self.source)
        finally:
            self.harness.release(self.key)

    def test_same_text_question_replaces_modules_by_question_version(self):
        self.present(summary='需要你确认')
        self.tool('ask_user', dict(question='需要你确认', options=[dict(id='keep', label='保留'), dict(id='later', label='稍后')]))
        current = self.harness.public(self.key)
        self.assertEqual(current['summary'], '需要你确认')
        self.assertEqual(current['briefing'], {})
        self.assertIsNotNone(current['question'])

    def test_feedback_clears_current_modules_before_continuing(self):
        self.present()
        updated = self.harness.feedback(self.key, '请继续核对。')
        self.assertEqual(updated['briefing'], {})
        self.assertEqual(updated['status'], 'ready')

    def test_user_direct_replacement_does_not_borrow_model_modules(self):
        self.proposal()
        result = self.harness.direct_plan(self.key, dict(ids=[self.files[1]['id']], action='delete'))
        self.assertEqual(result['task']['briefing'], {})
        self.assertEqual(result['plan']['briefing'], {})
        self.assertEqual(result['plan']['origin'], 'user_direct')
        self.assertEqual(result['plan']['briefing_provenance']['source'], 'unknown')
        self.assertFalse(self.state.history())

    def test_short_modules_reject_invalid_or_duplicated_content_atomically(self):
        before = self.present()
        for value in (None, [], {'other': ['未知类']}, {'highlights': []}, {'highlights': ['  ']},
                      {'highlights': ['长' * 161]}, {'highlights': ['a', 'b', 'c', 'd']},
                      {'cautions': ['a', 'b', 'c']}, {'coverage': [True]},
                      {'highlights': ['重复'], 'cautions': ['重复']}):
            with self.subTest(value=value), self.assertRaises(AppError):
                self.present(briefing=value)
            self.assertEqual(self.harness.public(self.key)['briefing'], before['briefing'])
            self.assertEqual(self.harness.public(self.key)['briefing_provenance'], before['briefing_provenance'])

    def test_summary_limits_match_schema_and_modules_do_not_add_a_hidden_limit(self):
        definitions = {row['function']['name']: row['function']['parameters'] for row in tool_specs()}
        for name, host_limit, apply in (('present_findings', 1000, self.present), ('propose_plan', 600, self.proposal)):
            with self.subTest(tool=name):
                self.assertEqual(definitions[name]['properties']['summary']['maxLength'], host_limit)
                text = '完整结论' * (host_limit // 4)
                modules = {'coverage': ['完整保留实际短句']}
                result = apply(summary=text, briefing=modules)
                self.assertEqual(result['summary'], text)
                self.assertEqual(result['briefing'], modules)
                self.assertEqual(result['briefing_provenance'], self.source)
                if name == 'propose_plan':
                    self.assertEqual(result['plan']['summary'], text)
                with self.assertRaises(AppError):
                    apply(summary='长' * (host_limit + 1), briefing=modules)
                self.assertEqual(self.harness.public(self.key)['summary'], text)

    def test_long_valid_summary_does_not_bypass_invalid_module_validation(self):
        before = self.present(summary='长' * 500)
        with self.assertRaises(AppError):
            self.present(summary='长' * 500, briefing={'coverage': ['长' * 161]})
        self.assertEqual(self.harness.public(self.key)['summary'], before['summary'])
        self.assertEqual(self.harness.public(self.key)['briefing'], before['briefing'])

    def test_model_cannot_supply_briefing_identity_or_operation(self):
        for extra in ({'briefing_provenance': self.source}, {'briefing_binding': {}}, {'action': 'delete'}):
            with self.subTest(extra=extra), self.assertRaises(AppError):
                self.present(**extra)
        self.assertFalse(self.state.history())

    def test_modules_do_not_bypass_completion_or_coverage_gate(self):
        self.key = self.harness.create_analysis({'ids': [row['id'] for row in self.files], 'depth': 'detailed'})['id']
        with self.assertRaises(AppError):
            self.present(needs_input=False, briefing={'coverage': ['全部完成']})
        self.assertEqual(self.harness.public(self.key)['status'], 'ready')

    def test_on_demand_context_preserves_frozen_targets_preferences_and_choices(self):
        preferences = '请保留项目与个人资料。' * 80
        self.assistant.save_settings({'custom_instructions': preferences})
        self.harness.feedback(self.key, keep_ids=[self.files[10]['id']])
        context = self.harness._context(self.harness._get(self.key))
        self.assertNotIn('initial_objects', context)
        self.assertNotIn('initial_objects_omitted', context)
        self.assertEqual(context['available_items'], 12)
        self.assertEqual(context['preferences'], preferences)
        self.assertEqual(context['user_choices'][0]['id'], self.files[10]['id'])
        self.assertNotIn('information_guide', context)
        listed = self.tool('list_items', {'limit': 40})
        self.assertEqual({item['id'] for item in listed['items']}, {item['id'] for item in self.files if item['id'] != self.files[10]['id']})
        self.assertEqual(set(self.harness._get(self.key)['target_ids']), {item['id'] for item in self.files})
        self.assertEqual(len(tool_specs()), 38)
        self.assertTrue(all((self.scope / row['name']).is_file() for row in self.files))

    def test_empty_optional_sections_are_absent_but_scope_and_known_zero_remain(self):
        key = self.harness.create('只分析所选目录。', str(self.scope))['id']
        context = self.harness._context(self.harness._get(key))
        for field in ('initial_objects', 'analysis', 'preferences', 'user_feedback', 'user_choices',
                      'observations', 'last_result', 'usage', 'loaded_skills', 'skills_note',
                      'scans', 'connection', 'previous_connection', 'handoff', 'last_user_action'):
            self.assertNotIn(field, context)
        self.assertEqual(context['available_items'], 0)
        self.assertEqual(context['facts']['total'], 0)
        self.assertIs(context['facts']['has_more'], False)
        self.assertEqual(context['scope_count'], 1)
        self.assertEqual(len(context['scopes']), 1)
        self.assertNotIn('volume_scopes', context)  # Same root need not be repeated.
        self.assertIs(context['simulation_only'], True)
        public = self.harness.public(key)
        self.assertEqual(public['usage']['model_requests'], 0)
        self.assertIsNone(public['usage']['input_tokens'])

    def test_on_demand_context_does_not_sample_targets_or_collect_background(self):
        related = copy.deepcopy(self.files[0])
        related.update(id='related-software', name='Background software', kind='software',
                       path='installed-app/fixture', actions=['uninstall'], signature='software-fixture')
        self.state.publish([related], 'related-software')
        task = self.harness._get(self.key)
        chosen = [self.files[8]['id'], self.files[2]['id']]
        task['target_ids'] = chosen
        task['analysis']['target_count'] = 2
        task['known_ids'].append(related['id'])
        self.harness._save(task)
        with patch('diskpilot.tasks.scan_directory', side_effect=AssertionError('context must not scan')), \
             patch('diskpilot.tasks._checked_root', side_effect=AssertionError('context must not collect')), \
             patch('os.scandir', side_effect=AssertionError('context must not enumerate')):
            context = self.harness._context(self.harness._get(self.key))
        self.assertNotIn('initial_objects', context)
        self.assertNotIn('initial_objects_omitted', context)
        self.assertEqual(context['analysis']['target_count'], 2)
        self.assertEqual(context['scopes'], [])  # Selected files do not authorize parent traversal.
        self.assertEqual(context['volume_scope_count'], 1)
        self.assertEqual([row['id'] for row in self.tool('inspect_items', {'ids': chosen})['items']], chosen)

    def test_projection_preserves_nested_unknown_and_partial_metadata(self):
        item = copy.deepcopy(self.files[0])
        item['details']['timestamps']['created_at'] = None
        item['details']['subtree_complete'] = False
        self.state.publish([item], 'unknown-time')
        self.key = self.harness.create_analysis({'ids': [item['id']]})['id']
        context = self.harness._context(self.harness._get(self.key))
        self.assertNotIn('initial_objects', context)
        sample = self.tool('inspect_items', {'ids': [item['id']]})['items'][0]
        self.assertIn('created_at', sample['timestamps'])
        self.assertIsNone(sample['timestamps']['created_at'])
        self.assertIs(sample['complete'], False)
        self.assertIs(sample['user_protected'], False)
        self.assertIs(sample['personal_choice_required'], True)


class PublicReplyTests(unittest.TestCase):
    setUp = FindingsTests.setUp

    def reply(self, text='完整公开回复，仅供阅读。', *, source=None, **changes):
        value = dict(role='assistant', content=text, tool_calls=[dict(id='present', type='function', function=dict(
            name='present_findings', arguments=json.dumps(dict(summary='已保存简短结论。', needs_input=True))))],
            _provenance=copy.deepcopy(source or self.source), _usage={})
        value.update(changes)
        return value

    def run_reply(self, value, *, cancel=None):
        with patch.object(self.assistant, 'agent_turn', side_effect=value if callable(value) else [value]):
            self.harness.claim(self.key)
            return self.harness.investigate(self.key, cancel=cancel)

    def test_complete_public_text_survives_reopen_with_original_source(self):
        value = self.run_reply(self.reply('完整公开正文，不是摘要。'))
        record = value['last_public_reply']
        self.assertEqual(record['text'], '完整公开正文，不是摘要。')
        self.assertNotEqual(record['text'], value['summary'])
        self.assertEqual(record['provenance'], self.source)
        self.assertEqual(record['completed_at'], self.source['generated_at'])
        self.assertEqual(record['protocol'], 'chat_completions')
        self.assertIs(record['truncated'], False)
        self.assistant.save_settings({'model': 'fixture-b'})
        restored = TaskHarness(self.state, self.assistant)
        self.assertEqual(restored.public(self.key)['last_public_reply'], record)
        record['text'] = '外部修改'
        self.assertEqual(restored.public(self.key)['last_public_reply']['text'], '完整公开正文，不是摘要。')

    def test_public_reply_is_per_task_and_legacy_summary_is_not_a_reply(self):
        value = self.run_reply(self.reply('仅属于任务A的公开正文'))
        self.assertIsNotNone(value['last_public_reply'])
        other = self.harness.create_analysis({'ids': [self.files[0]['id']]})
        self.assertIsNone(other['last_public_reply'])
        task = self.harness._get(self.key)
        task.pop('last_public_reply')
        with self.state.db:
            self.state.db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(task), self.key))
        self.assertIsNone(self.harness.public(self.key)['last_public_reply'])
        self.assertTrue(self.harness.public(self.key)['summary'])

    def test_failed_or_cancelled_turn_does_not_replace_accepted_text(self):
        old = self.run_reply(self.reply('上次完整公开回复'))['last_public_reply']

        def failure(*_args, **_kwargs):
            raise ValueError('fixture transport failed')

        self.assertEqual(self.run_reply(failure)['last_public_reply'], old)
        cancel = threading.Event()

        def cancelled(*_args, **_kwargs):
            cancel.set()
            return self.reply('取消后不可当作完整回复保存')

        cancelled_value = self.run_reply(cancelled, cancel=cancel)
        self.assertEqual(cancelled_value['status'], 'paused')
        self.assertEqual(cancelled_value['last_public_reply'], old)

    def test_empty_tool_only_response_does_not_erase_last_public_reply(self):
        old = self.run_reply(self.reply('上次完整公开回复'))['last_public_reply']
        for text in (None, '', '  \n'):
            with self.subTest(text=text):
                current = self.run_reply(self.reply(text))
                self.assertEqual(current['last_public_reply'], old)

    def test_public_reply_is_sanitized_bounded_and_never_contains_private_state(self):
        self.assistant.save_settings({'api_key': 'synthetic-visible-key'})
        value = self.run_reply(self.reply('公开文字 synthetic-visible-key',
            _continuation={'native': 'private-continuation-canary'}, reasoning_content='private-thinking-canary'))
        record = value['last_public_reply']
        self.assertNotIn('synthetic-visible-key', record['text'])
        self.assertEqual(set(record), {'text', 'completed_at', 'protocol', 'provenance', 'truncated'})
        raw = self.state.db.execute('SELECT payload FROM tasks WHERE id=?', (self.key,)).fetchone()[0]
        self.assertNotIn('private-continuation-canary', raw)
        self.assertNotIn('private-thinking-canary', raw)
        task = self.harness._get(self.key)
        self.harness._remember_public_reply(task, '字' * 8001, self.source)
        self.harness._save(task)
        clipped = self.harness.public(self.key)['last_public_reply']
        self.assertEqual(len(clipped['text']), 8000)
        self.assertIs(clipped['truncated'], True)

    def test_upstream_truncation_flag_is_host_metadata_not_transcript_text(self):
        value = self.run_reply(self.reply('已经由协议层限长的公开文字', _public_text_truncated=True))
        self.assertIs(value['last_public_reply']['truncated'], True)
        raw = self.state.db.execute('SELECT payload FROM tasks WHERE id=?', (self.key,)).fetchone()[0]
        self.assertNotIn('_public_text_truncated', raw)

    def test_public_reply_never_enters_model_context(self):
        marker = 'last-public-reply-token-canary'
        self.run_reply(self.reply(marker))
        context = self.harness._context(self.harness._get(self.key))
        self.assertNotIn('last_public_reply', context)
        self.assertNotIn(marker, json.dumps(context, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
