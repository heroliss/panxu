"""Compact tool choices/receipts persist without raw arguments or execution."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from diskpilot.activity import ToolActivity
from diskpilot.cleanup_tools import REGISTRY
from diskpilot.simulation import AppError, SimulationState


class ToolActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = SimulationState(self.temp.name)
        self.addCleanup(self.state.close)
        self.activity = ToolActivity(self.state)

    def record(self, key='event-1', status='accepted', **kwargs):
        return self.activity.record(key, 'windows_temp', 'Windows临时文件', status, **kwargs)

    def test_same_event_is_idempotent_and_public_result_is_consistent(self):
        first = self.record(source={'task_id': 'task-a', 'suggestion_id': 'host-system-windows_temp', 'option_id': 'tool'})
        second = self.record(source={'task_id': 'task-a', 'suggestion_id': 'host-system-windows_temp', 'option_id': 'tool'})
        self.assertEqual(first, second)
        self.assertEqual(self.activity.recent(), [first])
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM tool_activity').fetchone()[0], 1)
        with self.assertRaises(AppError) as caught:
            self.record(status='declined')
        self.assertEqual(caught.exception.code, 'activity_event_conflict')

    def test_context_uses_registry_labels_and_drops_private_text_paths_and_keys(self):
        self.activity.record('user supplied path C:\\private\\sk-key', 'browser_privacy',
            'secret-title-cannot-become-context', 'opened',
            source={'task_id': 'task-a', 'item_id': 'scan-sample', 'suggestion_id': 'options',
                    'path': 'C:\\private', 'api_key': 'sk-private-source',
                    'return_context': {'path': 'C:\\private'}},
            parameters={'path': 'C:\\private', 'password': 'private-password'},
            result={'message': 'private response body', 'api_key': 'sk-private-result', 'applied': True,
                    'actual_reclaimed_bytes': 999999999, 'items': [{'name': 'private-file-name'}]},
            option_label='private-choice')
        result = self.activity.recent()[0]
        self.assertEqual(result['title'], REGISTRY['browser_privacy']['title'])
        self.assertEqual(result['status'], 'opened')
        self.assertFalse(result['applied'])
        self.assertEqual(result['actual_reclaimed_bytes'], 0)
        self.assertEqual(result['backend'], 'host')
        self.assertEqual(result['source'], {'task_id': 'task-a', 'item_id': 'scan-sample', 'suggestion_id': 'options'})
        raw = json.dumps([dict(row) for row in self.state.db.execute('SELECT * FROM tool_activity')])
        for secret in ('private', 'sk-key', 'password', 'return_context', 'actual_moved'):
            self.assertNotIn(secret, raw)

    def test_counts_preserve_zero_and_unknown_without_promoting_success(self):
        entry = self.record(status='interrupted', result={'total': 4, 'succeeded': 0, 'failed': 1,
            'pending': 3, 'unverified': True, 'cancelled': -1, 'undone': 1_000_000_001})
        self.assertEqual(entry['counts'], {'total': 4, 'succeeded': 0, 'failed': 1, 'pending': 3})
        self.assertEqual(entry['backend'], 'simulation')
        self.assertTrue(entry['simulation_only'])
        self.assertEqual(entry['actual_reclaimed_bytes'], 0)
        other = self.record('event-2', result={'item_count': 2})
        self.assertEqual(other['counts'], {'total': 2})

    def test_decline_answer_and_open_do_not_execute_or_approve_anything(self):
        for status in ('declined', 'answered', 'opened', 'cancelled'):
            entry = self.record(status, status=status, option_label='暂不清理')
            self.assertEqual(entry['backend'], 'host')
            self.assertFalse(entry['applied'])
        self.assertEqual(self.state.history(), [])
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM overlay').fetchone()[0], 0)
        self.assertEqual(self.state.recovery_store.summary()['retained_count'], 0)

    def test_recent_is_bounded_newest_first_and_filters_both_exact_sources(self):
        self.record('old', source={'task_id': 'task-a', 'suggestion_id': 'suggestion-a'})
        wanted = self.record('wanted', status='declined', source={'task_id': 'task-a', 'suggestion_id': 'suggestion-a'})
        self.record('other-task', source={'task_id': 'task-b', 'suggestion_id': 'suggestion-a'})
        self.record('other-suggestion', source={'task_id': 'task-a', 'suggestion_id': 'suggestion-b'})
        filtered = self.activity.recent(1, task_id='task-a', suggestion_id='suggestion-a')
        self.assertEqual(filtered, [wanted])
        self.assertEqual(len(self.activity.recent(2)), 2)
        self.assertEqual(self.activity.recent(task_id='missing'), [])

    def test_historical_backfill_keeps_original_time_and_does_not_become_latest(self):
        newer = self.record('newer', status='completed', occurred_at='2026-01-02T08:00:00+08:00')
        older = self.record('older', status='cancelled', occurred_at='2026-01-01T00:00:00+00:00')
        self.assertEqual(newer['time'], '2026-01-02T00:00:00+00:00')
        self.assertEqual(self.activity.recent(), [newer, older])
        self.assertEqual(self.record('older', status='cancelled', occurred_at='2099-01-01T00:00:00+00:00'), older)
        with self.assertRaises(AppError) as raised:
            self.record('invalid', occurred_at='unknown')
        self.assertEqual(raised.exception.code, 'invalid_activity_time')

    def test_events_survive_reopening_and_do_not_create_files_outside_the_store(self):
        before = set(Path(self.temp.name).iterdir())
        entry = self.record(status='completed', result={'succeeded': 2, 'failed': 0})
        self.assertEqual(set(Path(self.temp.name).iterdir()), before)
        with tempfile.TemporaryDirectory() as other:
            # A second Python object shares the original transaction/lock; it
            # must not duplicate or reset already recorded events.
            second = ToolActivity(self.state)
            self.assertEqual(second.recent(), [entry])
        self.state.db.commit()
        import sqlite3
        db = sqlite3.connect(Path(self.temp.name) / 'simulation.sqlite3')
        try:
            self.assertEqual(json.loads(db.execute('SELECT payload FROM tool_activity').fetchone()[0]), entry)
        finally:
            db.close()

    def test_latest_decision_remains_retrievable_after_many_later_open_events(self):
        source = {'task_id': 'task-a', 'suggestion_id': 'host-system-windows_temp', 'option_id': 'skip'}
        choice = self.record('choice', status='declined', source=source)
        for number in range(30):
            self.record('opened-' + str(number), status='opened', source=source)
        self.assertNotIn(choice['id'], [entry['id'] for entry in self.activity.recent(20)])
        selected = self.activity.recent(1, task_id='task-a', suggestion_id='host-system-windows_temp',
                                        statuses=('accepted', 'declined'))
        self.assertEqual(selected, [choice])

    def test_concurrent_same_event_writes_one_receipt(self):
        barrier = threading.Barrier(4)
        results, errors = [], []
        def run():
            try:
                barrier.wait(timeout=5)
                results.append(self.record())
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(errors)
        self.assertEqual(len(results), 4)
        self.assertEqual(len({result['id'] for result in results}), 1)
        self.assertEqual(len(self.activity.recent()), 1)

    def test_invalid_inputs_are_controlled_without_an_event(self):
        for changes in ({'event_key': ''}, {'event_key': None}, {'tool_id': 'missing'},
                        {'title': ''}, {'status': 'executed'}, {'status': []}, {'source': []}):
            values = dict(event_key='event', tool_id='windows_temp', title='临时文件', status='opened')
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(AppError):
                self.activity.record(**values)
        for limit in (0, 21, True, '6'):
            with self.subTest(limit=limit), self.assertRaises(AppError):
                self.activity.recent(limit)
        for kwargs in ({'task_id': []}, {'suggestion_id': 'C:\\private'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AppError):
                self.activity.recent(**kwargs)
        for statuses in ([], ['missing'], ['opened', 'opened'], 'opened', [None], [{}]):
            with self.subTest(statuses=statuses), self.assertRaises(AppError):
                self.activity.recent(statuses=statuses)
        self.assertEqual(self.activity.recent(), [])


if __name__ == '__main__':
    unittest.main()
