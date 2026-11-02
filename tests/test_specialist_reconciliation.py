"""Reopening reconciles durable outcomes without making old actions new."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.server import Application


class SpecialistReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-specialist-reconcile-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'owned'
        self.scope.mkdir()
        (self.scope / 'sample.txt').write_bytes(b'owned unchanged sample')
        self.profile = self.root / 'profile'
        self.app = Application(self.profile)
        self.addCleanup(lambda: self.app.close())
        self.task_id = self.app.harness.create('人工专项结果往返测试', str(self.scope))['id']

    def plan(self, date, *, cancelled=False, feedback=True):
        with patch('diskpilot.cleanup_tools.now', return_value=date):
            plan = self.app.cleanup_tools.prepare('thumbnail_cache', {},
                source={'task_id': self.task_id})
            if cancelled:
                self.app.cleanup_tools.cancel_plan(plan['id'], plan['version'])
            else:
                self.app.cleanup_tools.execute(plan['id'], plan['version'])
        if feedback:
            self.app.harness.record_specialist_result(plan)
        return self.app.cleanup_tools.get_plan(plan['id'])

    def snapshot(self):
        h = self.app.harness
        return dict(task=copy.deepcopy(h._get(self.task_id)),
            facts=self.app.state.db.execute("SELECT COUNT(*) FROM task_facts WHERE tool='specialist_result'").fetchone()[0],
            events=self.app.state.db.execute('SELECT COUNT(*) FROM task_events').fetchone()[0],
            activities=self.app.state.db.execute('SELECT COUNT(*) FROM tool_activity').fetchone()[0])

    def reopen(self):
        self.app.close()
        with patch('diskpilot.llm.ModelAssistant.agent_turn', side_effect=AssertionError('reopen must not call model')):
            self.app = Application(self.profile)

    def test_older_unreported_cancellation_does_not_replace_later_completed_feedback(self):
        old = self.plan('2026-01-01T00:00:00+00:00', cancelled=True, feedback=False)
        latest = self.plan('2026-01-02T00:00:00+00:00')
        before = self.snapshot()
        self.reopen()
        after = self.snapshot()
        self.assertEqual(after['task']['last_specialist_result']['plan_id'], latest['id'])
        self.assertEqual(after['task']['last_specialist_result']['at'], latest['completed_at'])
        self.assertEqual(after['task']['updated_at'], before['task']['updated_at'])
        self.assertEqual(after['events'], before['events'])
        self.assertEqual(after['facts'], before['facts'] + 1)
        activity = self.app.harness.tool_activity.recent()
        self.assertEqual(activity[0]['source']['plan_id'], latest['id'])
        self.assertEqual(activity[1]['source']['plan_id'], old['id'])
        self.assertEqual(activity[1]['time'], old['cancelled_at'])
        self.reopen()
        repeated = self.snapshot()
        self.assertEqual(repeated, after)

    def test_newer_missing_feedback_is_reconciled_once_at_original_event_time(self):
        self.plan('2026-01-01T00:00:00+00:00')
        latest = self.plan('2026-01-02T00:00:00+00:00', feedback=False)
        before = self.snapshot()
        self.reopen()
        after = self.snapshot()
        result = after['task']['last_specialist_result']
        self.assertEqual(result['plan_id'], latest['id'])
        self.assertEqual(result['at'], latest['completed_at'])
        self.assertEqual(after['task']['updated_at'], before['task']['updated_at'])
        self.assertEqual(after['events'], before['events'])
        self.assertEqual(after['facts'], before['facts'] + 1)
        fact = next(row for row in self.app.harness.facts_page(self.task_id)['items']
                    if row['tool'] == 'specialist_result' and row['result']['plan_id'] == latest['id'])
        self.assertEqual(fact['at'], latest['completed_at'])
        self.reopen()
        self.assertEqual(self.snapshot(), after)

    def test_already_corrupted_latest_projection_is_repaired_without_duplicate_facts(self):
        old = self.plan('2026-01-01T00:00:00+00:00', cancelled=True)
        old_result = self.app.harness.public(self.task_id)['last_specialist_result']
        latest = self.plan('2026-01-02T00:00:00+00:00')
        task = self.app.harness._get(self.task_id)
        task['last_specialist_result'] = dict(old_result, at='2099-01-01T00:00:00+00:00')
        self.app.harness._save(task)
        before = self.snapshot()
        self.reopen()
        after = self.snapshot()
        self.assertEqual(after['task']['last_specialist_result']['plan_id'], latest['id'])
        self.assertEqual(after['task']['last_specialist_result']['at'], latest['completed_at'])
        self.assertEqual(after['facts'], before['facts'])
        self.assertEqual(after['events'], before['events'])
        self.assertEqual(after['task']['updated_at'], before['task']['updated_at'])
        self.assertNotEqual(old['id'], latest['id'])

    def test_legacy_unknown_transition_time_stays_unknown_and_does_not_win_latest(self):
        old = self.plan('2026-01-01T00:00:00+00:00', feedback=False)
        self.app.cleanup_tools.undo(old['id'])
        legacy = self.app.cleanup_tools.get_plan(old['id'])
        legacy.pop('undone_at')
        self.app.cleanup_tools._save(legacy)
        latest = self.plan('2026-01-02T00:00:00+00:00')
        before = self.snapshot()
        self.reopen()
        after = self.snapshot()
        self.assertEqual(after['task']['last_specialist_result']['plan_id'], latest['id'])
        self.assertEqual(after['task']['updated_at'], before['task']['updated_at'])
        self.assertEqual(after['events'], before['events'])
        self.assertEqual(after['activities'], before['activities'])
        fact = next(row for row in self.app.harness.facts_page(self.task_id)['items']
                    if row['tool'] == 'specialist_result' and row['result']['plan_id'] == old['id'])
        self.assertIsNone(fact['at'])
        self.assertIsNone(fact['result']['at'])

    def test_repeated_undo_keeps_its_original_transition_time(self):
        plan = self.plan('2026-01-01T00:00:00+00:00')
        with patch('diskpilot.cleanup_tools.now', return_value='2026-01-03T00:00:00+00:00'):
            undone = self.app.cleanup_tools.undo(plan['id'])
        self.app.harness.record_specialist_result(undone)
        first = self.snapshot()
        with patch('diskpilot.cleanup_tools.now', side_effect=AssertionError('repeated undo is not a new transition')):
            repeated = self.app.cleanup_tools.undo(plan['id'])
        self.app.harness.record_specialist_result(repeated)
        self.assertEqual(repeated['undone_at'], '2026-01-03T00:00:00+00:00')
        self.assertEqual(self.snapshot(), first)


if __name__ == '__main__':
    unittest.main()
