"""State intent must not become the opposite cleanup action or an invented fact."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest

from diskpilot.recommendations import build_recommendations
from diskpilot.action_semantics import primary_action, unavailable_reason
from diskpilot.server import Application
from diskpilot.simulation import AppError
from diskpilot.state_advice import BLOCKED_REASON, current_state, normalize, operation_allowed, present
from diskpilot.windows import _item


def service(*, state='Running', start_mode='Auto'):
    return _item('service', 'owned-service', 'Owned service', 'Owned fixture', 'service', 'stop_service',
                 dict(state=state, start_mode=start_mode, synthetic=False), path='registration/owned-service')


def startup(state='Enabled', **details):
    return _item('startup', 'owned-startup', 'Owned startup', 'Owned fixture', 'startup', 'disable_startup',
                 dict(state=state, synthetic=False, **details), path='registration/owned-startup')


def intent(recommendation, scope='runtime'):
    return dict(state_recommendation=recommendation, state_scope=scope)


class StateAdviceTests(unittest.TestCase):
    def test_stopped_does_not_mean_disabled_or_manual_means_running(self):
        row = service(state='Stopped', start_mode='Auto')
        self.assertEqual(current_state(row, 'runtime')['state'], 'stopped')
        self.assertEqual(current_state(row, 'service_startup')['state'], 'automatic')
        self.assertEqual(present(row, intent('keep_disabled'))['label'], '保持停止')
        self.assertEqual(present(row, intent('keep_enabled', 'service_startup'))['label'], '保持自动启动')
        row = service(state='Running', start_mode='Disabled')
        self.assertEqual(current_state(row, 'runtime')['state'], 'running')
        self.assertEqual(current_state(row, 'service_startup')['state'], 'disabled')
        self.assertEqual(present(row, intent('keep_disabled', 'service_startup'))['label'], '保持禁用服务启动')
        self.assertEqual(present(service(start_mode='Manual'), intent('keep_enabled', 'service_startup'))['label'], '保持手动启动')

    def test_legacy_advice_and_registration_only_never_invent_intent_or_enabled_state(self):
        for row in (service(state=None, start_mode=None), startup('启动项注册信息'), startup('位于启动文件夹')):
            self.assertEqual(normalize(row, {}), dict(state_recommendation=None, state_scope=None))
            self.assertIsNone(present(row, {'cleanup_rating': 1, 'conclusion': 'keep'}))
            self.assertTrue(operation_allowed(row, {}))
        self.assertEqual(current_state(startup('已注册'), 'login')['state'], 'unknown')
        self.assertEqual(current_state(startup('已注册', startup_enabled=False), 'login')['state'], 'disabled')

    def test_unknown_current_state_does_not_claim_a_keep_direction_is_already_satisfied(self):
        for recommendation in ('keep_disabled', 'keep_enabled'):
            view = present(startup('启动项注册信息'), intent(recommendation, 'login'))
            self.assertFalse(view['current_state_known'])
            self.assertEqual(view['current_state'], 'unknown')
            self.assertIn('状态待核实', view['label'])
            self.assertIn('不能据此认定', view['reason'])
            self.assertNotIn('无需清理', view['reason'])
            self.assertFalse(view['actionable'])

    def test_service_requires_scope_and_unknown_or_mismatched_fields_are_rejected(self):
        for row, value in ((service(), {'state_recommendation': 'enable'}),
                           (service(), intent('enable', 'login')), (startup(), intent('disable', 'runtime')),
                           (service(), intent('delete')), (service(), {'state_scope': 'runtime'}),
                           ({'kind': 'file'}, intent('enable'))):
            with self.subTest(row=row, value=value), self.assertRaises(ValueError):
                normalize(row, value)
            self.assertFalse(operation_allowed(row, value))
        self.assertEqual(normalize(startup(), {'state_recommendation': 'enable'})['state_scope'], 'login')

    def test_keep_enable_and_unknown_intents_never_become_stop_even_with_high_cleanup_score(self):
        for recommendation in ('enable', 'keep_current', 'keep_enabled', 'keep_disabled', 'investigate'):
            for row, scope, action in ((service(), 'runtime', 'stop_service'),
                                        (startup(), 'login', 'disable_startup')):
                value = dict(intent(recommendation, scope), cleanup_rating=5)
                with self.subTest(value=value):
                    self.assertFalse(operation_allowed(row, value, action))
                    self.assertFalse(present(row, value)['actionable'])

    def test_concrete_supported_closing_state_differs_from_startup_policy(self):
        self.assertTrue(operation_allowed(service(), intent('disable'), 'stop_service'))
        self.assertFalse(operation_allowed(service(state='Stopped'), intent('disable'), 'stop_service'))
        self.assertFalse(operation_allowed(service(), intent('disable', 'service_startup'), 'stop_service'))
        self.assertFalse(operation_allowed(service(), intent('disable'), 'disable_startup'))
        self.assertFalse(operation_allowed(startup('Disabled'), intent('disable', 'login'), 'disable_startup'))
        self.assertTrue(operation_allowed(startup(), intent('disable', 'login'), 'disable_startup'))

    def test_known_closed_startup_has_no_redundant_action_while_unknown_registration_remains_unknown(self):
        for row in (startup('Disabled'), startup('已关闭'), startup('注册信息', startup_enabled=False)):
            self.assertIsNone(primary_action(row))
            self.assertIn('无需再次关闭', unavailable_reason(row))
        row = startup('注册信息')
        self.assertEqual(primary_action(row), 'disable_startup')
        self.assertEqual(current_state(row, 'login')['state'], 'unknown')

    def test_legacy_process_identity_supports_exact_action_without_inventing_current_state(self):
        row = _item('process', 'owned-process', 'Owned process', 'Owned fixture', 'process', 'stop_process',
                    dict(pid=4242, create_time=123.5, synthetic=False), path='owned-program.exe')
        self.assertTrue(operation_allowed(row, intent('enable'), 'stop_process', explicit_user_override=True))
        self.assertEqual(current_state(row, 'runtime')['state'], 'unknown')
        changed = copy.deepcopy(row)
        changed['details']['state'] = 'Stopped'
        self.assertFalse(operation_allowed(changed, intent('enable'), 'stop_process', explicit_user_override=True))
        changed = copy.deepcopy(row)
        changed['details'].pop('create_time')
        self.assertFalse(operation_allowed(changed, intent('enable'), 'stop_process', explicit_user_override=True))

    def test_exact_user_override_still_requires_the_actual_supported_action_and_state(self):
        self.assertTrue(operation_allowed(service(), intent('enable'), 'stop_service', explicit_user_override=True))
        self.assertFalse(operation_allowed(service(), intent('enable'), explicit_user_override=True))
        self.assertFalse(operation_allowed(service(), intent('enable'), 'uninstall', explicit_user_override=True))
        self.assertFalse(operation_allowed(service(state='Stopped'), intent('enable'), 'stop_service', explicit_user_override=True))
        self.assertFalse(operation_allowed(dict(service(), risk='protected'), intent('enable'), 'stop_service', explicit_user_override=True))
        self.assertFalse(present(dict(service(), simulated=True), intent('disable'))['actionable'])

    def test_changed_or_contradictory_state_is_explicit_and_never_actionable(self):
        view = present(service(), intent('keep_disabled'))
        self.assertTrue(view['state_conflict'])
        self.assertEqual(view['current_label'], '正在运行')
        self.assertFalse(view['actionable'])
        view = present(service(), intent('disable'), stale=True)
        self.assertEqual(view['recommendation'], 'disable')
        self.assertTrue(view['stale'])
        self.assertFalse(view['actionable'])
        self.assertIn('检查已变化', view['reason'])

    def test_legacy_recommendation_pipeline_preserves_explicit_enable_but_removes_stop_default(self):
        row = service()
        row['details'].update(optional=True, dependency_check_complete=True, has_dependents=False)
        response = dict(id=row['id'], recommend=True, suggested_action='stop_service', reason='样本用途需要此服务。',
                        impact='先核对功能需要。', cleanup_rating=5, **intent('enable'))

        class Peer:
            def public_settings(self): return {}
            def recommend(self, items): return dict(mode='llm', recommendations=[response])

        result = build_recommendations([row], Peer(), use_model=True)['items'][0]['advice']
        self.assertEqual(result['state_advice']['label'], '建议启动服务')
        self.assertEqual(result['cleanup_rating'], 5)
        self.assertFalse(result['auto_select'])
        self.assertIsNone(result['suggested_action'])


class StateAdviceAssessmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='panxu-state-advice-')
        self.addCleanup(temp.cleanup)
        self.app = Application(Path(temp.name) / 'profile')
        self.addCleanup(self.app.close)
        self.row = service()
        self.app.state.publish([self.row], 'background')
        self.task = self.app.harness.create_analysis({'ids': [self.row['id']], 'section': 'background'})

    def record(self, recommendation, scope='runtime'):
        record = dict(id=self.row['id'], signature=self.row['signature'], path=self.row['path'], kind='service',
                      cleanup_rating=5, conclusion='review', inheritance='none', purpose='人工样本',
                      reason='仅用于状态建议验证', impact='不会执行操作', **intent(recommendation, scope))
        task = self.app.harness._get(self.task['id'])
        task['advice_revision'] = task.get('advice_revision', 0) + 1
        with self.app.state.lock, self.app.state.db:
            self.app.harness.assessments.publish(task, {self.row['id']: record})
        return task

    def test_persisted_enable_advice_blocks_automatic_cleanup_without_removing_manual_review(self):
        self.record('enable')
        page = self.app.get('/api/files', {'task_id': [self.task['id']], 'view': ['all']})
        row = next(row for row in page['items'] if row['id'] == self.row['id'])
        self.assertEqual(row['assessment']['state_recommendation'], 'enable')
        self.assertEqual(row['assessment']['state_scope'], 'runtime')
        self.assertEqual(row['assessment']['state_advice']['label'], '建议启动服务')
        self.assertIn(BLOCKED_REASON, row['assessment']['blocked_reasons'])
        self.assertFalse(row['can_auto_select'])
        self.assertFalse(row['can_prepare'])
        self.assertTrue(row['can_logical_select'])
        self.assertEqual(self.app.state.history(), [])

    def test_stale_record_keeps_explanation_and_intent_without_reusing_operation_eligibility(self):
        self.record('disable')
        changed = copy.deepcopy(self.row)
        changed['signature'] = 'changed-owned-signature'
        changed['details'].update(state='Stopped', start_mode='Auto')
        value = self.app.harness.assessments.resolve([changed])[changed['id']]
        self.assertTrue(value['stale'])
        self.assertIsNone(value['rating'])
        self.assertEqual(value['state_advice']['recommendation'], 'disable')
        self.assertEqual(value['state_advice']['current_state'], 'stopped')
        self.assertFalse(value['state_advice']['actionable'])
        self.assertEqual(value['reason'], '仅用于状态建议验证')

    def test_current_state_is_not_copied_from_a_different_scope_or_low_cleanup_score(self):
        self.record('keep_disabled', 'service_startup')
        value = self.app.harness.assessments.resolve([self.row])[self.row['id']]
        self.assertEqual(value['state_advice']['current_state'], 'automatic')
        self.assertTrue(value['state_advice']['state_conflict'])
        self.assertEqual(value['state_advice']['scope_label'], '服务启动方式')
        self.assertIn(BLOCKED_REASON, value['blocked_reasons'])

    def record_through_tool(self, recommendation, scope='runtime'):
        record = dict(id=self.row['id'], conclusion='review', purpose='人工服务样本',
                      reason='需要相关功能时建议开启。', impact='开启后相关功能会运行。', impact_rating=2,
                      cleanup_rating=5, state_recommendation=recommendation)
        if scope is not None:
            record['state_scope'] = scope
        return self.app.harness.tool(self.task['id'], 'record_advice', {'items': [record]}, cancel=threading.Event())

    def test_tool_persists_direction_and_public_task_includes_real_state(self):
        self.record_through_tool('enable')
        advice = self.app.harness.public(self.task['id'])['advice'][0]
        self.assertEqual(advice['state_recommendation'], 'enable')
        self.assertEqual(advice['state_scope'], 'runtime')
        self.assertEqual(advice['state_advice']['label'], '建议启动服务')
        self.assertEqual(advice['state_advice']['current_state'], 'running')
        self.assertFalse(advice['state_advice']['actionable'])
        self.assertEqual(self.app.state.history(), [])

    def test_invalid_service_scope_is_atomic_and_does_not_publish_an_ambiguous_record(self):
        before = self.app.harness.assessments.revision()
        with self.assertRaises(AppError):
            self.record_through_tool('enable', scope=None)
        self.assertEqual(self.app.harness.assessments.revision(), before)
        self.assertEqual(self.app.harness.public(self.task['id'])['advice_total'], 0)

    def test_implicit_checkbox_plan_and_model_proposal_cannot_translate_enable_into_stop(self):
        self.record_through_tool('enable')
        with self.assertRaises(AppError) as caught:
            self.app.harness.prepare_assessment_plan([self.row['id']], source_task_id=self.task['id'])
        self.assertEqual(caught.exception.code, BLOCKED_REASON)
        with self.assertRaises(AppError) as caught:
            self.app.harness.tool(self.task['id'], 'propose_plan', {'summary': '人工模拟方案', 'items': [dict(
                id=self.row['id'], action='stop_service', reason='不应转换成此操作', impact='停止会影响功能')]},
                cancel=threading.Event())
        self.assertEqual(caught.exception.code, BLOCKED_REASON)
        self.assertIsNone(self.app.harness.public(self.task['id'])['plan'])
        self.assertEqual(self.app.state.history(), [])

    def test_explicit_user_action_can_override_recommendation_with_actual_action_explanation(self):
        self.record_through_tool('enable')
        self.app.harness.runtime.backend.validator = lambda items: None  # Owned fixture only.
        prepared = self.app.harness.direct_plan(self.task['id'], {'ids': [self.row['id']],
            'actions': {self.row['id']: 'stop_service'}, 'snapshots': {self.row['id']: self.row['signature']}})
        entry = prepared['plan']['items'][0]
        self.assertEqual(entry['action'], 'stop_service')
        self.assertIn('停止', entry['action_label'])
        self.assertIn('依赖功能', entry['impact'])
        self.assertNotIn('开启后', entry['impact'])
        self.assertIsNone(entry['impact_rating'])
        self.assertIn('建议启动服务', entry['reason'])
        self.assertIn('不同', entry['reason'])
        self.assertEqual(self.app.state.history(), [])

    def test_direct_clean_intent_allows_reviewable_stop_without_reinterpreting_model_enable(self):
        self.record_through_tool('enable')
        self.app.harness.assessments.dispositions.set(self.row, 'direct_clean', self.row['signature'])
        self.app.harness.runtime.backend.validator = lambda items: None  # Owned fixture only.
        page = self.app.get('/api/files', {'task_id': [self.task['id']], 'view': ['all']})
        row = next(value for value in page['items'] if value['id'] == self.row['id'])
        self.assertNotIn(BLOCKED_REASON, row['assessment']['blocked_reasons'])
        self.assertTrue(row['assessment']['state_recommendation_overridden'])
        self.assertEqual(row['assessment']['state_advice']['recommendation'], 'enable')
        self.assertTrue(row['can_prepare'])
        prepared = self.app.harness.prepare_assessment_plan([self.row['id']], source_task_id=self.task['id'])
        entry = prepared['plan']['items'][0]
        self.assertEqual(entry['action'], 'stop_service')
        self.assertIn('依赖功能', entry['impact'])
        self.assertNotIn('开启后', entry['impact'])
        self.assertIsNone(entry['impact_rating'])
        self.assertIn('建议启动服务', entry['reason'])
        self.assertEqual(self.app.state.history(), [])

    def test_stale_direct_clean_does_not_remove_model_direction_guard(self):
        self.record_through_tool('enable')
        old = dict(self.row, signature='old-owned-signature')
        self.app.harness.assessments.dispositions.set(old, 'direct_clean', old['signature'])
        value = self.app.harness.assessments.resolve([self.row])[self.row['id']]
        self.assertIn(BLOCKED_REASON, value['blocked_reasons'])
        with self.assertRaises(AppError) as caught:
            self.app.harness.prepare_assessment_plan([self.row['id']], source_task_id=self.task['id'])
        self.assertEqual(caught.exception.code, BLOCKED_REASON)

    def test_known_disabled_startup_projection_preserves_state_and_disables_cleanup_selection(self):
        row = startup('注册信息', startup_enabled=False)
        self.app.state.publish([row], 'owned-disabled-startup')
        task = self.app.harness.create_analysis({'ids': [row['id']], 'section': 'background'})
        page = self.app.get('/api/files', {'task_id': [task['id']], 'view': ['all']})
        observed = next(value for value in page['items'] if value['id'] == row['id'])
        self.assertIs(observed['details']['startup_enabled'], False)
        self.assertIsNone(observed['action'])
        self.assertFalse(observed['can_logical_select'])
        self.assertFalse(observed['can_prepare'])
        self.assertIn('无需再次关闭', observed['action_unavailable_reason'])
        with self.assertRaises(AppError) as caught:
            self.app.harness.prepare_assessment_plan([row['id']], source_task_id=task['id'])
        self.assertEqual(caught.exception.code, 'selection_changed')
        self.assertEqual(self.app.state.history(), [])


if __name__ == '__main__':
    unittest.main()
