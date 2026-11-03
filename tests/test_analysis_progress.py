"""Analysis progress uses saved current results and bounded resource counters."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.agent_budget import DEFAULTS, finalization_due
from diskpilot.analysis_progress import summarize_analysis
from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness
from diskpilot.windows import _item


def call(name, args, ident='fixture-call'):
    return dict(id=ident, type='function', function=dict(name=name,
        arguments=json.dumps(args, ensure_ascii=False)))


def response(*calls):
    return dict(choices=[dict(message=dict(role='assistant', content=None, tool_calls=list(calls)))])


class AnalysisProgressProjectionTests(unittest.TestCase):
    def fixture(self, count=70):
        current = {str(index): dict(id=str(index), signature='signature-' + str(index)) for index in range(count)}
        advice = {ident: dict(id=ident, signature=item['signature'], conclusion='review',
                            cleanup_rating=int(ident) % 5 + 1) for ident, item in current.items()}
        return dict(status='investigating', analysis=dict(section='background'),
                    target_ids=list(current), advice=advice), current

    def test_full_advice_distribution_is_not_limited_to_public_page_of_fifty(self):
        task, current = self.fixture()
        result = summarize_analysis(task, current, DEFAULTS)
        self.assertEqual(result['saved_count'], 70)
        self.assertEqual(result['target_count'], 70)
        self.assertEqual(result['remaining_count'], 0)
        self.assertEqual(result['rating_counts'], {str(i): 14 for i in range(1, 6)})
        self.assertEqual(result['rated_count'], 70)

    def test_stale_missing_skipped_unrated_and_explicit_no_advice_are_distinct(self):
        task, current = self.fixture(8)
        task['advice']['0'].update(conclusion='skip', cleanup_rating=None)
        task['advice']['1'].update(conclusion='no_advice', cleanup_rating=None)
        task['advice']['2']['signature'] = 'old'
        current.pop('3')
        task['advice']['4']['cleanup_rating'] = True
        result = summarize_analysis(task, current, DEFAULTS, stale_ids=['5'])
        self.assertEqual(result['saved_count'], 5)
        self.assertEqual(result['rated_count'], 2)
        self.assertEqual(result['skipped_count'], 1)
        self.assertEqual(result['no_advice_count'], 1)
        self.assertEqual(result['remaining_count'], 3)

    def test_open_inventory_has_no_invented_target_or_percent(self):
        task, current = self.fixture(12)
        task.pop('analysis')
        task['known_ids'] = list(current) + ['unread'] * 1000
        result = summarize_analysis(task, current, DEFAULTS)
        self.assertIsNone(result['target_count'])
        self.assertIsNone(result['remaining_count'])
        self.assertEqual(result['saved_count'], 12)
        self.assertNotIn('percent', result)

    def test_legacy_cumulative_resources_are_not_presented_as_current_turn_usage(self):
        task, current = self.fixture(3)
        task['usage'] = dict(model_requests=90, tool_calls=150)
        result = summarize_analysis(task, current, DEFAULTS)
        self.assertIsNone(result['budget']['model_requests']['used'])
        self.assertIsNone(result['budget']['tool_calls']['used'])

    def test_saved_turn_limits_and_reason_survive_current_setting_change(self):
        task, current = self.fixture(3)
        task.update(status='budget_exhausted', turn_budget=dict(limits=DEFAULTS,
            used=dict(model_requests=6, tool_calls=9)), investigation_notice=dict(code='round_budget'))
        result = summarize_analysis(task, current, dict(model_requests=30, tool_calls=100))
        self.assertEqual(result['phase'], 'limited')
        self.assertEqual(result['budget']['model_requests'], dict(used=6, limit=6))
        self.assertEqual(result['budget']['tool_calls'], dict(used=9, limit=12))
        self.assertEqual(result['budget']['reason'], 'round_budget')

    def test_two_replies_or_two_calls_remaining_prompt_timely_scoring_without_new_allowance(self):
        self.assertFalse(finalization_due(DEFAULTS, model_requests_used=3, tool_calls_used=9))
        self.assertTrue(finalization_due(DEFAULTS, model_requests_used=4))
        self.assertTrue(finalization_due(DEFAULTS, tool_calls_used=10))
        self.assertEqual(DEFAULTS, dict(model_requests=6, tool_calls=12))


class AnalysisProgressHarnessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scope = self.root / 'owned'
        self.scope.mkdir()
        for index in range(12):
            (self.scope / f'sample-{index:02}.tmp').write_text('Owned artificial metadata sample.')
        self.state = SimulationState(self.root / 'profile')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(self.scope))['items'], 'owned-progress-fixtures')
        self.steps, self.requests = [], []

        def transport(_url, payload, _headers, _timeout):
            self.requests.append(copy.deepcopy(payload))
            if not self.steps:
                raise AssertionError('unexpected model call')
            step = self.steps.pop(0)
            return step(payload) if callable(step) else step

        self.assistant = ModelAssistant(self.root / 'profile', transport=transport)
        self.assistant.save_settings(dict(base_url='http://127.0.0.1:12345/v1', model='offline-fixture'))
        self.harness = TaskHarness(self.state, self.assistant)
        self.files = [row for row in self.state.snapshot()['items'] if row['kind'] == 'file']
        self.task = self.harness.create_analysis(dict(ids=[row['id'] for row in self.files], section='files'))

    def run_turn(self):
        key = self.task['id']
        self.harness.claim(key)
        try:
            return self.harness.investigate(key)
        finally:
            self.harness.release(key)

    def advice(self, rows):
        return [dict(id=row['id'], conclusion='review', cleanup_rating=3, purpose='人工临时文件样本。',
                     reason='已核对选择范围，是否清理由用户决定。', impact='本构建仅模拟记录。') for row in rows]

    def test_twelve_selected_items_can_save_two_batches_and_finish_inside_same_six_requests(self):
        self.steps = [response(call('inspect_items', dict(ids=[row['id'] for row in self.files[index:index + 3]]), str(index)))
                      for index in range(0, 12, 3)]

        def scoring(payload):
            names = {tool['function']['name'] for tool in payload['tools']}
            self.assertIn('record_advice', names)
            self.assertIn('collect_apps', names)
            self.assertIn('按信息量自行选批次', payload['messages'][-1]['content'])
            progress = self.harness.public(self.task['id'])['analysis_progress']
            self.assertEqual(progress['phase'], 'analyzing')
            self.assertEqual(progress['budget']['model_requests']['used'], 5)
            return response(call('record_advice', dict(items=self.advice(self.files[:6])), 'score-a'),
                            call('record_advice', dict(items=self.advice(self.files[6:])), 'score-b'))

        def finish(payload):
            names = {tool['function']['name'] for tool in payload['tools']}
            self.assertIn('record_advice', names)
            self.assertIn('present_findings', names)
            before = self.harness.public(self.task['id'])['analysis_progress']
            self.assertEqual(before['saved_count'], 12)
            self.assertEqual(before['remaining_count'], 0)
            return response(call('present_findings', dict(summary='12个所选项目建议已保存，没有执行操作。', needs_input=False)))

        self.steps += [scoring, finish]
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['analysis_progress']['saved_count'], 12)
        self.assertEqual(result['analysis_progress']['phase'], 'completed')
        self.assertEqual(result['analysis_progress']['budget']['model_requests'], dict(used=6, limit=6))
        self.assertEqual(result['analysis_progress']['budget']['tool_calls'], dict(used=7, limit=12))
        self.assertEqual(self.state.history(), [])
        self.assertEqual(len(list(self.scope.iterdir())), 12)

    def test_resource_exhaustion_names_resource_and_never_claims_item_limit_or_success(self):
        self.harness.agent_budget.save_session({'model_requests': 6})
        self.steps = [response(call('list_items', {})) for _ in range(6)]
        result = self.run_turn()
        progress = result['analysis_progress']
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(progress['phase'], 'limited')
        self.assertEqual(progress['budget']['reason'], 'session_requests')
        self.assertEqual(progress['remaining_count'], 12)
        self.assertEqual(progress['saved_count'], 0)
        self.assertIn('分析请求', result['summary'])
        self.assertNotIn('12项上限', result['summary'])
        self.assertEqual(result['usage']['model_requests'], 6)
        self.assertEqual(self.state.history(), [])

    def test_current_turn_counter_resets_on_resume_but_cumulative_usage_does_not(self):
        self.steps = [response(call('ask_user', dict(question='人工测试：是否保留这些样本？', options=[
            dict(id='keep', label='保留'), dict(id='skip', label='稍后决定')])))]
        first = self.run_turn()
        self.assertEqual(first['analysis_progress']['budget']['model_requests']['used'], 1)
        self.harness.answer(self.task['id'], dict(version=first['question']['version'], skip=True))
        self.steps = [response(call('present_findings', dict(summary='已有范围不变，建议稍后按需分析。', needs_input=False)))]
        second = self.run_turn()
        self.assertEqual(second['usage']['model_requests'], 2)
        self.assertEqual(second['analysis_progress']['budget']['model_requests']['used'], 1)
        restored = TaskHarness(self.state, self.assistant).public(self.task['id'])
        self.assertEqual(restored['analysis_progress']['budget'], second['analysis_progress']['budget'])

    def test_public_progress_counts_all_sixty_results_and_same_count_reanalysis_has_revision(self):
        for index in range(12, 60):
            (self.scope / f'sample-{index:02}.tmp').write_text('Owned artificial metadata sample.')
        self.state.publish(scan_directory(str(self.scope))['items'], 'owned-progress-fixtures')
        files = [row for row in self.state.snapshot()['items'] if row['kind'] == 'file']
        task = self.harness.create_analysis(dict(ids=[row['id'] for row in files], section='files'))
        for start in range(0, 60, 6):
            self.harness.tool(task['id'], 'record_advice', dict(items=self.advice(files[start:start + 6])),
                              cancel=threading.Event())
        result = self.harness.public(task['id'])
        self.assertEqual(len(result['advice']), 50)
        self.assertEqual(result['analysis_progress']['saved_count'], 60)
        self.assertEqual(result['analysis_progress']['rating_counts']['3'], 60)
        self.assertEqual(result['analysis_progress']['remaining_count'], 0)
        revision = result['advice_revision']
        revised = self.advice(files[:1])
        revised[0]['cleanup_rating'] = 4
        self.harness.tool(task['id'], 'record_advice', dict(items=revised), cancel=threading.Event())
        updated = self.harness.public(task['id'])
        self.assertGreater(updated['advice_revision'], revision)
        self.assertEqual(updated['analysis_progress']['saved_count'], 60)
        self.assertEqual(updated['analysis_progress']['rating_counts']['4'], 1)
        (self.scope / files[0]['name']).write_text('A changed owned test sample.')
        self.state.publish(scan_directory(str(self.scope))['items'], 'owned-progress-fixtures')
        stale = self.harness.public(task['id'])['analysis_progress']
        self.assertEqual(stale['saved_count'], 59)
        self.assertEqual(stale['remaining_count'], 1)

    def startup_selection(self):
        rows = [_item('startup', f'owned-startup-{index}', f'Owned Example {index}', 'Owned fixture',
                      'startup', 'disable_startup', dict(state='Enabled', enabled=True, synthetic=False),
                      path=str(self.scope / f'inert-{index}.exe'), reason='Artificial test fixture.')
                for index in range(3)]
        self.state.publish(rows, 'owned-startup-fixtures')
        task = self.harness.create_analysis(dict(section='background', ids=[row['id'] for row in rows]))
        return task, rows

    def test_selected_startups_cannot_finish_early_and_resume_lists_only_unresolved_objects(self):
        task, rows = self.startup_selection()
        self.harness.tool(task['id'], 'record_advice', dict(items=self.advice(rows[:1])), cancel=threading.Event())
        with self.assertRaises(AppError) as caught:
            self.harness.tool(task['id'], 'present_findings', dict(summary='不能把未覆盖标为完成。', needs_input=False),
                              cancel=threading.Event())
        self.assertEqual(caught.exception.code, 'coverage_incomplete')
        remaining = self.harness.tool(task['id'], 'list_items', dict(unanalysed=True), cancel=threading.Event())
        self.assertEqual({row['id'] for row in remaining['items']}, {row['id'] for row in rows[1:]})
        self.assertEqual(self.harness.public(task['id'])['analysis_progress']['remaining_count'], 2)
        undecided = self.advice(rows[1:])
        undecided[0].update(conclusion='skip', cleanup_rating=None)
        undecided[1].update(conclusion='no_advice', cleanup_rating=None)
        self.harness.tool(task['id'], 'record_advice', dict(items=undecided), cancel=threading.Event())
        self.harness.tool(task['id'], 'present_findings', dict(summary='三项均已有建议或明确说明。', needs_input=False),
                          cancel=threading.Event())
        result = self.harness.public(task['id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['analysis']['completion_requirement'], 'each_selected_object')
        self.assertEqual(result['analysis_progress']['saved_count'], 3)
        self.assertEqual(result['analysis_progress']['rated_count'], 1)
        self.assertEqual(result['analysis_progress']['skipped_count'], 1)
        self.assertEqual(result['analysis_progress']['no_advice_count'], 1)
        self.assertEqual(result['analysis_progress']['remaining_count'], 0)

    def test_open_background_exploration_does_not_promise_to_score_every_installed_object(self):
        _selected, rows = self.startup_selection()
        task = self.harness.create(preset='background')
        stored = self.harness._get(task['id'])
        stored.update(known_ids=[row['id'] for row in rows],
                      observations=[dict(tool='owned_fixture_observation', result=dict(total=3))])
        self.harness._save(stored)
        self.harness.tool(task['id'], 'record_advice', dict(items=self.advice(rows[:1])), cancel=threading.Event())
        self.harness.tool(task['id'], 'present_findings', dict(summary='已完成当前有限探索，未遍历其他软件。', needs_input=False),
                          cancel=threading.Event())
        result = self.harness.public(task['id'])
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(result['analysis_progress']['target_count'])
        self.assertIsNone(result['analysis_progress']['remaining_count'])
        self.assertEqual(result['analysis_progress']['saved_count'], 1)

    def test_filtered_startup_list_is_frozen_and_requires_each_result_like_checkbox_selection(self):
        self.startup_selection()
        task = self.harness.create_analysis(dict(section='background', kind='startup'))
        self.assertEqual(task['analysis']['selection'], 'filtered')
        self.assertEqual(task['analysis_progress']['target_count'], 3)
        self.assertEqual(task['analysis']['completion_requirement'], 'each_selected_object')
        with self.assertRaises(AppError) as caught:
            self.harness.tool(task['id'], 'present_findings', dict(summary='筛选列表不能空结束。', needs_input=False),
                              cancel=threading.Event())
        self.assertEqual(caught.exception.code, 'coverage_incomplete')

    def test_selected_tool_objects_also_require_explicit_coverage_before_completion(self):
        observation = dict(evidence=dict(status='unknown'),
                           presentation=dict(kind='status', status='unknown', complete=False))
        with patch.object(self.harness.cleanup_tools, 'inspect', return_value=observation):
            rows = [self.harness.tool_assessments.inspect(name) for name in ('gpu_cache', 'thumbnail_cache')]
        task = self.harness.create_tool_analysis(dict(items=[dict(row['tool_ref'], inspection_id=row['inspection_id']) for row in rows]))
        with self.assertRaises(AppError) as caught:
            self.harness.tool(task['id'], 'present_findings', dict(summary='不能忽略两个所选工具。', needs_input=False),
                              cancel=threading.Event())
        self.assertEqual(caught.exception.code, 'coverage_incomplete')
        advice = [dict(id=row['id'], conclusion=conclusion, cleanup_rating=None,
                       purpose='人工工具未知快照。', reason='本轮明确说明缺口，避免伪造评分。', impact='不执行任何操作。')
                  for row, conclusion in zip(rows, ('skip', 'no_advice'))]
        self.harness.tool(task['id'], 'record_advice', dict(items=advice), cancel=threading.Event())
        self.harness.tool(task['id'], 'present_findings', dict(summary='已明确说明两项工具的缺口。', needs_input=False),
                          cancel=threading.Event())
        result = self.harness.public(task['id'])['analysis_progress']
        self.assertEqual(result['saved_count'], 2)
        self.assertEqual(result['rated_count'], 0)
        self.assertEqual(result['skipped_count'], 1)
        self.assertEqual(result['no_advice_count'], 1)
        self.assertEqual(result['remaining_count'], 0)

    def test_frozen_tool_analysis_stays_complete_after_cache_expiry_invalidation_and_restart(self):
        tick = [100.0]
        self.harness.tool_assessments.clock = lambda: tick[0]
        observation = dict(evidence=dict(status='observed', complete=True, observed_bytes=64,
            observed_files=1, coverage_paths=['C:/OwnedToolProgressCache'], coverage_complete=True),
            presentation=dict(kind='cache', status='observed', complete=True,
                metrics=[dict(key='observed_bytes', value=64), dict(key='observed_files', value=1)]))
        with patch.object(self.harness.cleanup_tools, 'inspect', return_value=observation):
            rows = [self.harness.tool_assessments.inspect(name) for name in
                    ('gpu_cache', 'thumbnail_cache', 'steam_cache')]
        refs = [dict(row['tool_ref'], inspection_id=row['inspection_id']) for row in rows]
        task = self.harness.create_tool_analysis(dict(items=refs))
        self.harness.tool(task['id'], 'inspect_items', dict(ids=[row['id'] for row in rows]),
                          cancel=threading.Event())
        self.harness.tool(task['id'], 'record_advice', dict(items=self.advice(rows)),
                          cancel=threading.Event(), provenance=dict(source='llm', provider='custom',
                              model='offline-tool-progress-fixture', protocol='chat_completions',
                              generated_at='2026-11-03T00:00:00+00:00'))
        self.harness.tool(task['id'], 'present_findings', dict(summary='人工工具快照的三项建议已完成。', needs_input=False),
                          cancel=threading.Event())
        fresh = self.harness.public(task['id'])
        self.assertEqual(fresh['analysis_progress']['saved_count'], 3)
        self.assertEqual(fresh['analysis_progress']['stale_count'], 0)
        tick[0] += 61
        def assert_historical_snapshot(result):
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['analysis_progress']['saved_count'], 3)
            self.assertEqual(result['analysis_progress']['rated_count'], 3)
            self.assertEqual(result['analysis_progress']['rating_counts']['3'], 3)
            self.assertEqual(result['analysis_progress']['remaining_count'], 0)
            self.assertEqual(result['analysis_progress']['stale_count'], 3)
            self.assertTrue(all(row['stale'] for row in result['advice']))

        assert_historical_snapshot(self.harness.public(task['id']))
        with self.assertRaises(AppError):
            self.harness.tool_assessments.prepare_selection(refs)
        self.harness.tool_assessments.invalidate()
        assert_historical_snapshot(self.harness.public(task['id']))
        reopened_state = SimulationState(self.root / 'profile')
        self.addCleanup(reopened_state.close)
        restored = TaskHarness(reopened_state, self.assistant)
        assert_historical_snapshot(restored.public(task['id']))
        with self.assertRaises(AppError):
            restored.tool_assessments.prepare_selection(refs)
        self.assertEqual(self.state.history(), [])
        self.assertEqual(self.requests, [])

    def test_late_tool_remains_available_and_conclusion_can_fit_without_expanding_limits(self):
        _selected, rows = self.startup_selection()
        self.task = self.harness.create(preset='background')
        task = self.harness._get(self.task['id'])
        task['known_ids'] = [row['id'] for row in rows]
        self.harness._save(task)
        self.harness.agent_budget.save(dict(model_requests=32, tool_calls=6))
        self.steps = [response(*[call('list_items', {}, f'read-{index}') for index in range(5)])]

        def finish(payload):
            tools = {row['function']['name'] for row in payload['tools']}
            self.assertTrue({'present_findings', 'ask_user', 'collect_apps'} <= tools)
            return response(call('present_findings', dict(summary='仅概览已有范围，未评分或执行；更多调查可另行继续。', needs_input=False)))

        self.steps.append(finish)
        result = self.run_turn()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['tool_calls'], 6)
        self.assertEqual(result['usage']['model_requests'], 2)
        self.assertEqual(result['analysis_progress']['budget']['tool_calls'], dict(used=6, limit=6))
        self.assertEqual(result['analysis_progress']['budget']['model_requests'], dict(used=2, limit=32))


if __name__ == '__main__':
    unittest.main()
