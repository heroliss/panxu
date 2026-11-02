"""Real cooperative controls over an owned simulation fixture, without an LLM."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.actions import ActionRuntime
from diskpilot.execution_control import ExecutionControl
from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness


class Interrupted(BaseException):
    pass


class ExecutionControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'owned-files'
        self.scope.mkdir()
        for name in ('a.bin', 'b.bin', 'c.bin'):
            (self.scope / name).write_bytes(b'unchanged-real-fixture')
        self.state = SimulationState(self.root / 'data')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(self.scope))['items'], 'fixture')
        self.files = sorted([i for i in self.state.snapshot([])['items'] if i['kind'] == 'file'], key=lambda i: i['name'])
        self.assistant = ModelAssistant(self.root / 'data')
        self.assistant.save_settings({'provider': 'custom', 'base_url': 'http://127.0.0.1:12345/v1', 'model': 'not-called'})
        self.harness = TaskHarness(self.state, self.assistant)
        self.task = self.harness.create_analysis({'ids': [i['id'] for i in self.files]})
        self.key = self.task['id']
        self.worker = None
        self.release_fault = threading.Event()
        self.addCleanup(self.cleanup_worker)

    def cleanup_worker(self):
        if self.worker and self.worker.is_alive():
            try:
                self.harness.control_execution(self.key, 'cancel')
            except AppError:
                pass
            self.release_fault.set()
            self.worker.join(3)
            if self.worker.is_alive():
                raise AssertionError('Test worker did not leave its pause/fault gate.')

    def approve(self, entries=None):
        selected = self.files if entries is None else entries
        result = self.harness.direct_plan(self.key, {'ids': [i['id'] for i in selected], 'action': 'delete'})
        self.version = result['plan']['version']
        self.harness.validate_approval(self.key, self.version, [i['id'] for i in selected])
        self.harness.claim(self.key, execute=True)
        return result

    def start(self, *, fault=None, progress=None, cancel=None):
        self.harness.runtime.fault = fault
        self.result, self.errors = [], []

        def run():
            try:
                with patch.object(self.assistant, 'agent_turn', side_effect=AssertionError('Execution must not call an LLM')):
                    self.result.append(self.harness.investigate(self.key, execute=True, progress=progress, cancel=cancel))
            except BaseException as exc:
                self.errors.append(exc)

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def finish(self, timeout=3):
        self.worker.join(timeout)
        self.assertFalse(self.worker.is_alive())
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.result), 1)
        self.assertTrue(all(p.read_bytes() == b'unchanged-real-fixture' for p in self.scope.iterdir()))
        self.assertEqual(self.result[0]['last_result']['actual_reclaimed_bytes'], 0)
        return self.result[0]

    def gate(self, phase='before', index=0):
        entered, paused = threading.Event(), threading.Event()
        ident = self.files[index]['id']

        def fault(entry, stage):
            if entry['id'] == ident and stage == phase:
                entered.set()
                if not self.release_fault.wait(3):
                    raise AssertionError('Owned test gate timed out.')

        def progress(value):
            if value.get('execution', {}).get('status') == 'paused':
                paused.set()

        self.start(fault=fault, progress=progress)
        self.assertTrue(entered.wait(2))
        return paused

    def test_pause_before_commit_releases_state_and_settings_until_resume(self):
        self.approve()
        paused = self.gate()
        requested = self.harness.control_execution(self.key, 'pause', version=self.version)
        self.assertEqual(requested['status'], 'pausing')
        self.release_fault.set()
        self.assertTrue(paused.wait(2))
        current = self.harness.execution_status(self.key)
        self.assertEqual(current['status'], 'paused')
        self.assertEqual(current['pending'], 3)
        self.assertTrue(current['can_resume'])
        self.assertTrue(self.state.lock.acquire(timeout=.5))
        self.state.lock.release()
        self.assertTrue(self.assistant._lock.acquire(timeout=.5))
        self.assistant._lock.release()
        self.assertEqual(self.state.history(), [])
        with self.assertRaises(AppError):
            self.harness.control_execution(self.key, 'resume', version=self.version - 1)
        self.assertEqual(self.harness.execution_status(self.key)['status'], 'paused')
        self.harness.control_execution(self.key, 'resume', version=self.version)
        final = self.finish()
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['execution']['status'], 'completed')
        self.assertEqual(final['execution']['succeeded'], 3)
        self.assertEqual(final['execution']['processed'], 3)

    def test_current_commit_can_finish_before_pause_acknowledges(self):
        self.approve()
        paused = self.gate('after')
        self.harness.control_execution(self.key, 'pause', version=self.version)
        self.release_fault.set()
        self.assertTrue(paused.wait(2))
        current = self.harness.execution_status(self.key)
        self.assertEqual(current['succeeded'], 1)
        self.assertEqual(current['pending'], 2)
        self.assertEqual(len(self.state.history()), 1)
        self.harness.control_execution(self.key, 'resume', version=self.version)
        self.assertEqual(self.finish()['last_result']['succeeded'], 3)

    def test_cancel_while_paused_cancels_all_uncommitted_entries(self):
        self.approve()
        paused = self.gate()
        self.harness.control_execution(self.key, 'pause', version=self.version)
        self.release_fault.set()
        self.assertTrue(paused.wait(2))
        self.harness.control_execution(self.key, 'cancel', version=self.version)
        final = self.finish()
        self.assertEqual(final['status'], 'paused')
        self.assertEqual(final['execution']['status'], 'cancelled')
        self.assertEqual(final['last_result']['cancelled'], 3)
        self.assertEqual(final['last_result']['succeeded'], 0)
        self.assertEqual(self.state.history(), [])
        self.assertFalse(final['execution']['can_resume'])

    def test_cancel_keeps_completed_receipts_and_does_not_resume_cancelled_batch(self):
        self.approve()
        self.gate('after')
        self.harness.control_execution(self.key, 'pause', version=self.version)
        self.harness.control_execution(self.key, 'cancel', version=self.version)
        with self.assertRaises(AppError):
            self.harness.control_execution(self.key, 'resume', version=self.version)
        self.release_fault.set()
        final = self.finish()
        self.assertEqual(final['last_result']['succeeded'], 1)
        self.assertEqual(final['last_result']['cancelled'], 2)
        self.assertEqual(final['execution']['processed'], 3)
        self.assertEqual(final['execution']['pending'], 0)
        self.assertEqual(len(self.state.history()), 1)
        self.assertIn('已取消', final['summary'])

    def test_plain_cancel_event_also_wakes_a_paused_worker(self):
        self.approve()
        cancel, entered, paused = threading.Event(), threading.Event(), threading.Event()

        def fault(entry, phase):
            if entry['id'] == self.files[0]['id'] and phase == 'before':
                entered.set()
                self.release_fault.wait(3)

        self.start(fault=fault, cancel=cancel, progress=lambda event: paused.set()
                   if event.get('execution', {}).get('status') == 'paused' else None)
        self.assertTrue(entered.wait(2))
        self.harness.control_execution(self.key, 'pause', version=self.version)
        self.release_fault.set()
        self.assertTrue(paused.wait(2))
        cancel.set()
        self.assertEqual(self.finish()['execution']['status'], 'cancelled')

    def test_controls_do_not_wait_for_an_inflight_state_settings_commit_lock(self):
        self.approve(self.files[:2])
        original = self.harness.runtime.backend.apply
        entered, requested, paused = threading.Event(), threading.Event(), threading.Event()

        def slow_commit(entry, operation_id, patterns):
            if entry['id'] == self.files[0]['id']:
                entered.set()
                self.release_fault.wait(3)
            return original(entry, operation_id, patterns)

        with patch.object(self.harness.runtime.backend, 'apply', side_effect=slow_commit):
            self.start(progress=lambda event: paused.set() if event.get('execution', {}).get('status') == 'paused' else None)
            self.assertTrue(entered.wait(2))

            def request():
                self.harness.control_execution(self.key, 'pause', version=self.version)
                requested.set()

            requester = threading.Thread(target=request, daemon=True)
            requester.start()
            self.assertTrue(requested.wait(.5), 'Control endpoint must not acquire the commit locks.')
            self.release_fault.set()
            self.assertTrue(paused.wait(2))
            self.harness.control_execution(self.key, 'cancel', version=self.version)
            final = self.finish()
            requester.join(1)
        self.assertEqual(final['last_result']['succeeded'], 1)
        self.assertEqual(final['last_result']['cancelled'], 1)

    def test_stale_version_cannot_control_a_later_execution(self):
        self.approve(self.files[:1])
        self.start()
        self.finish()
        old_version = self.version
        self.approve(self.files[1:2])
        with self.assertRaises(AppError) as caught:
            self.harness.control_execution(self.key, 'pause', version=old_version)
        self.assertEqual(caught.exception.code, 'stale_execution')
        with self.assertRaises(AppError):
            self.harness.control_execution(self.key, 'pause', version=True)
        self.assertEqual(self.harness.execution_status(self.key)['status'], 'running')
        self.harness.control_execution(self.key, 'cancel', version=self.version)
        self.start()
        self.assertEqual(self.finish()['last_result']['cancelled'], 1)
        self.assertEqual(len(self.state.history()), 1)

    def test_partial_failures_finish_without_llm_or_false_success(self):
        self.approve()
        self.start(fault=lambda entry, phase: entry['id'] == self.files[1]['id'] and phase == 'before')
        final = self.finish()
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['execution']['failed'], 1)
        self.assertEqual(final['execution']['succeeded'], 2)
        self.assertEqual(final['last_result']['status'], 'partial')
        self.assertIn('失败', final['summary'])
        with self.assertRaises(AppError):
            self.harness.control_execution(self.key, 'pause', version=self.version)

    def test_restart_preserves_receipt_and_marks_execution_interrupted(self):
        self.approve()

        def crash(entry, phase):
            if phase == 'after':
                raise Interrupted()

        self.harness.runtime.fault = crash
        with self.assertRaises(Interrupted):
            self.harness.investigate(self.key, execute=True)
        self.assertEqual(len(self.state.history()), 1)
        restored = TaskHarness(self.state, self.assistant)
        result = restored.public(self.key)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(result['execution']['status'], 'interrupted')
        self.assertEqual(result['execution']['succeeded'], 1)
        self.assertEqual(result['execution']['pending'], 2)
        self.assertFalse(result['execution']['can_resume'])
        with self.assertRaises(AppError):
            restored.control_execution(self.key, 'resume', version=self.version)
        self.assertEqual(len(self.state.history()), 1)

    def test_stored_paused_phase_without_worker_is_never_resumable_after_restart(self):
        self.approve()
        current = self.harness._get(self.key)
        current['status'] = 'paused'
        current['execution'] = self.harness.execution_status(self.key)
        current['execution'].update(status='paused', can_resume=True)
        self.harness._save(current)
        restored = TaskHarness(self.state, self.assistant)
        status = restored.execution_status(self.key)
        self.assertEqual(status['status'], 'interrupted')
        self.assertEqual(status['pending'], 3)
        self.assertFalse(status['can_resume'])
        self.assertEqual(self.state.history(), [])

    def test_manual_low_or_skipped_advice_and_temporary_hold_can_be_overridden(self):
        self.harness.tool(self.key, 'record_advice', {'items': [dict(id=self.files[0]['id'], conclusion='skip',
            purpose='暂未判断', reason='待用户决定', impact='仅模拟')]}, cancel=threading.Event())
        self.harness.feedback(self.key, keep_ids=[self.files[0]['id']])
        self.approve(self.files[:1])
        self.start()
        self.assertEqual(self.finish()['last_result']['succeeded'], 1)
        self.assertEqual(len(self.state.history()), 1)

    def test_manual_empty_directory_advice_override_still_obeys_hard_protection(self):
        empty = self.scope / 'empty'
        empty.mkdir()
        self.state.publish(scan_directory(str(empty))['items'], 'empty-dir')
        directory = next(i for i in self.state.snapshot([])['items'] if i['path'] == str(empty))
        task = self.harness.create_analysis({'ids': [directory['id']]})
        self.harness.tool(task['id'], 'record_advice', {'items': [dict(id=directory['id'], conclusion='keep', cleanup_rating=1,
            purpose='空目录', reason='原建议保留', impact='尚未修改')]}, cancel=threading.Event())
        value = self.harness.prepare_assessment_plan([directory['id']])
        self.assertEqual(value['picked_ids'], [directory['id']])
        self.assertEqual(value['plan']['origin'], 'user_direct')
        self.assistant.save_settings({'protect_patterns': ['*empty']})
        with self.assertRaises(AppError):
            self.harness.prepare_assessment_plan([directory['id']])
        self.assertTrue(empty.is_dir())

    def test_snapshot_objects_cannot_mutate_control_state(self):
        entries = [dict(i, action='delete') for i in self.files]
        control = ExecutionControl('owned', 1, entries)
        data = control.snapshot()
        data['items'][0]['status'] = 'succeeded'
        self.assertEqual(control.snapshot()['succeeded'], 0)
        with self.assertRaises(AppError):
            control.request('unsupported')

    def test_host_cleanup_preserves_source_link_without_mutating_original_choices(self):
        self.harness.feedback(self.key, keep_ids=[self.files[0]['id']])
        before = copy.deepcopy(self.harness._get(self.key))
        drafted = self.harness.prepare_assessment_plan([self.files[0]['id']], source_task_id=self.key)
        self.assertEqual(drafted['task']['cleanup_source_task_id'], self.key)
        self.assertEqual(self.harness._get(self.key), before)
        self.assertEqual(drafted['picked_ids'], [self.files[0]['id']])
        current = self.harness._get(drafted['task']['id'])
        self.assertNotIn('cleanup_source_task_id', self.harness._context(current))
        restored = TaskHarness(self.state, self.assistant)
        self.assertEqual(restored.public(drafted['task']['id'])['cleanup_source_task_id'], self.key)

    def test_host_cleanup_source_scope_is_checked_including_non_analysis_roots(self):
        only_a = self.harness.create_analysis({'ids': [self.files[0]['id']]})
        with self.assertRaises(AppError) as outside:
            self.harness.prepare_assessment_plan([self.files[1]['id']], source_task_id=only_a['id'])
        self.assertEqual(outside.exception.code, 'outside_scope')
        nested = self.scope / 'nested'
        nested.mkdir()
        source = self.harness.create('只看这个子目录。', str(nested))
        with self.assertRaises(AppError) as outside:
            self.harness.prepare_assessment_plan([self.files[0]['id']], source_task_id=source['id'])
        self.assertEqual(outside.exception.code, 'outside_scope')
        for bad in (False, [], '', 'missing-task'):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                self.harness.prepare_assessment_plan([self.files[0]['id']], source_task_id=bad)
        with self.assertRaises(AppError):
            self.harness.tool(self.key, 'propose_plan', dict(summary='不能由模型指定宿主来源',
                items=[dict(id=self.files[0]['id'], action='delete', reason='测试', impact='只模拟')],
                cleanup_source_task_id=only_a['id']), cancel=threading.Event())

    def record_rating(self, task_id, score):
        self.harness.tool(task_id, 'record_advice', {'items': [dict(id=self.files[0]['id'],
            conclusion='keep' if score <= 2 else 'review', cleanup_rating=score,
            purpose='测试文件', reason='测试倾向', impact='不改真实文件')]}, cancel=threading.Event())

    def resolved_rating(self):
        return self.harness.assessments.resolve(self.state.snapshot([])['items'])[self.files[0]['id']]['rating']

    def test_cleanup_draft_does_not_block_source_reanalysis_or_lift_late_old_response(self):
        self.record_rating(self.key, 4)
        drafted = self.harness.prepare_assessment_plan([self.files[0]['id']], source_task_id=self.key)
        raw = self.harness._get(drafted['task']['id'])
        self.assertTrue(raw['non_analysis_intent'])
        self.harness.claim(self.key)  # Explicit continuation of the original source investigation.
        try:
            self.record_rating(self.key, 1)
        finally:
            self.harness.release(self.key)
        self.assertEqual(self.resolved_rating(), 1)
        newer = self.harness.create_analysis({'ids': [self.files[0]['id']]})
        self.record_rating(newer['id'], 2)
        self.record_rating(self.key, 5)
        self.assertEqual(self.resolved_rating(), 2)  # Genuine newer investigation still wins.

    def test_host_draft_becomes_real_analysis_intent_only_on_explicit_investigation(self):
        self.record_rating(self.key, 4)
        drafted = self.harness.prepare_assessment_plan([self.files[0]['id']], source_task_id=self.key)
        host_id = drafted['task']['id']
        self.harness.claim(host_id)
        try:
            self.assertFalse(self.harness._get(host_id)['non_analysis_intent'])
            self.record_rating(host_id, 2)
            self.record_rating(self.key, 5)
            self.assertEqual(self.resolved_rating(), 2)
        finally:
            self.harness.release(host_id)
        external = self.harness.create_analysis({'ids': [self.files[0]['id']],
            'non_analysis_intent': True, 'reserve_intent': False})
        self.assertFalse(self.harness._get(external['id'])['non_analysis_intent'])
        self.record_rating(external['id'], 1)
        self.record_rating(host_id, 5)
        self.assertEqual(self.resolved_rating(), 1)

    def stage_from_source(self, entries=None):
        selected = self.files if entries is None else entries
        source_id = self.key
        drafted = self.harness.prepare_assessment_plan([i['id'] for i in selected], source_task_id=source_id)
        self.key, self.version = drafted['task']['id'], drafted['plan']['version']
        self.harness.validate_approval(self.key, self.version, drafted['picked_ids'])
        self.harness.claim(self.key, execute=True)
        return source_id

    def test_source_gets_compact_actual_result_without_automatic_model_followup(self):
        source_id = self.stage_from_source()
        stale_source = self.harness._get(source_id)
        self.start()
        self.finish()
        source = self.harness.public(source_id)
        receipt = source['last_cleanup_result']
        self.assertEqual(receipt['execution_task_id'], self.key)
        self.assertEqual(receipt['plan_version'], self.version)
        self.assertEqual(receipt['total'], 3)
        self.assertEqual(receipt['succeeded'], 3)
        self.assertEqual(receipt['actual_reclaimed_bytes'], 0)
        self.assertNotIn('items', receipt)
        self.assertEqual(self.harness._context(self.harness._get(source_id))['last_cleanup_result'], receipt)
        fact = self.harness.facts_page(source_id, ids=[receipt['fact_id']])['items'][0]
        self.assertEqual(fact['tool'], 'host_cleanup_result')
        self.assertEqual(fact['result']['succeeded'], 3)
        facts_before = self.harness.facts_page(source_id)['total']
        self.harness._save(stale_source)  # Concurrent source investigation cannot overwrite the cross-task receipt.
        self.assertEqual(self.harness.public(source_id)['last_cleanup_result'], receipt)
        restored = TaskHarness(self.state, self.assistant)
        self.assertEqual(restored.public(source_id)['last_cleanup_result'], receipt)
        self.assertEqual(restored.facts_page(source_id)['total'], facts_before)

    def test_cancel_summary_notifies_source_of_finished_and_unexecuted_counts(self):
        source_id = self.stage_from_source()
        self.gate('after')
        self.harness.control_execution(self.key, 'cancel', version=self.version)
        self.release_fault.set()
        self.finish()
        receipt = self.harness.public(source_id)['last_cleanup_result']
        self.assertEqual(receipt['status'], 'cancelled')
        self.assertEqual(receipt['total'], 3)
        self.assertEqual(receipt['succeeded'], 1)
        self.assertEqual(receipt['cancelled'], 2)
        self.assertNotIn('items', receipt)

    def test_restart_links_recovered_receipt_and_pending_count_back_to_source(self):
        source_id = self.stage_from_source()

        def crash(entry, phase):
            if phase == 'after':
                raise Interrupted()

        self.harness.runtime.fault = crash
        with self.assertRaises(Interrupted):
            self.harness.investigate(self.key, execute=True)
        restored = TaskHarness(self.state, self.assistant)
        receipt = restored.public(source_id)['last_cleanup_result']
        self.assertEqual(receipt['status'], 'interrupted')
        self.assertEqual(receipt['total'], 3)
        self.assertEqual(receipt['succeeded'], 1)
        self.assertEqual(receipt['pending'], 2)
        self.assertEqual(len(self.state.history()), 1)
        self.assertEqual(restored.execution_status(self.key)['status'], 'interrupted')

    def test_full_batch_count_reaches_source_without_copying_500_item_results(self):
        for index in range(500):
            (self.scope / f'owned-{index:03d}.bin').write_bytes(b'unchanged-real-fixture')
        self.state.publish(scan_directory(str(self.scope))['items'], 'batch-fixture')
        selected = [i for i in self.state.snapshot([])['items'] if i['kind'] == 'file' and i['name'].startswith('owned-')]
        self.assertEqual(len(selected), 500)
        self.key = self.harness.create_analysis({'ids': [i['id'] for i in selected]})['id']
        source_id = self.stage_from_source(selected)
        self.start()
        final = self.finish(timeout=30)
        self.assertEqual(final['last_result']['succeeded'], 500)
        source_context = self.harness._context(self.harness._get(source_id))
        receipt = source_context['last_cleanup_result']
        self.assertEqual(receipt['total'], 500)
        self.assertEqual(receipt['succeeded'], 500)
        self.assertNotIn('items', receipt)
        self.assertLess(len(str(receipt)), 800)
        self.assertLess(len(str(source_context)), 6000)


if __name__ == '__main__':
    unittest.main()
