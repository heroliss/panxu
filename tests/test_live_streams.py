"""Volatile public previews remain bounded and independent of task actions."""
import threading
import unittest
from unittest.mock import patch

from diskpilot.live_streams import LiveStreams


class LiveStreamsTests(unittest.TestCase):
    def streams(self, **limits):
        value = LiveStreams(**limits)
        self.addCleanup(value.close)
        return value

    @staticmethod
    def event(kind='turn_start', turn='turn-one', **values):
        return dict(kind=kind, turn_id=turn, protocol='chat_completions', **values)

    def test_tasks_have_independent_snapshots_and_global_order_without_aliasing(self):
        value = self.streams()
        value.record('task-a', self.event(text='甲'))
        a = value.snapshot('task-a')
        value.record('task-b', self.event(text='乙'))
        b = value.snapshot('task-b')
        self.assertEqual(a['epoch'], b['epoch'])
        self.assertGreater(b['sequence'], a['sequence'])
        b['text'] = 'consumer mutation'
        self.assertEqual(value.snapshot('task-a')['text'], '甲')
        self.assertEqual(value.snapshot('task-b')['text'], '乙')
        self.assertIsNone(value.snapshot('unknown-task'))
        self.assertNotEqual(self.streams().epoch, value.epoch)

    def test_new_turn_resets_preview_and_superseded_frames_are_ignored(self):
        value = self.streams()
        value.record('task', self.event(text='旧轮', tool_pending=True))
        value.record('task', self.event(turn='new-turn'))
        new = value.snapshot('task')
        self.assertEqual(new['text'], '')
        self.assertFalse(new['tool_pending'])
        self.assertEqual(new['status'], 'streaming')
        for kind in ('stream_delta', 'turn_end', 'turn_start'):
            value.record('task', self.event(kind, text='旧帧', status='completed'))
            self.assertEqual(value.snapshot('task'), new)
        value.record('task', self.event('stream_delta', 'new-turn', text='新轮'))
        self.assertEqual(value.snapshot('task')['text'], '新轮')

    def test_terminal_snapshots_cannot_be_rewritten_or_restarted(self):
        for status in ('completed', 'error', 'cancelled'):
            with self.subTest(status=status):
                value = self.streams()
                value.record('task', self.event(text='已收到'))
                value.record('task', self.event('turn_end', status=status))
                terminal = value.snapshot('task')
                self.assertEqual(terminal['text'], '已收到')
                for kind in ('stream_delta', 'turn_end', 'turn_start'):
                    value.record('task', self.event(kind, text='迟到的错误覆盖', status='completed'))
                    self.assertEqual(value.snapshot('task'), terminal)

    def test_last_complete_survives_next_partial_error_cancel_and_empty_tool_turn(self):
        value = self.streams()
        value.record('a', self.event(text='上次完整公开回复'))
        value.record('a', self.event('turn_end', status='completed'))
        saved = value.snapshot('a')['last_complete']
        self.assertEqual(saved['text'], '上次完整公开回复')
        for turn, status, text in (('partial-error', 'error', '未完成'),
                                   ('cancelled', 'cancelled', '已取消片段'),
                                   ('tool-only', 'completed', '')):
            value.record('a', self.event(turn=turn))
            self.assertEqual(value.snapshot('a')['last_complete'], saved)
            value.record('a', self.event('stream_delta', turn=turn, text=text))
            self.assertEqual(value.snapshot('a')['last_complete'], saved)
            value.record('a', self.event('turn_end', turn=turn, status=status))
            self.assertEqual(value.snapshot('a')['last_complete'], saved)
        value.record('a', self.event(turn='fresh', text='下一条完整回复'))
        value.record('a', self.event('turn_end', turn='fresh', status='completed'))
        self.assertEqual(value.snapshot('a')['last_complete']['text'], '下一条完整回复')
        self.assertEqual(value.snapshot('a')['last_complete']['turn_id'], 'fresh')
        value.record('b', self.event(text='另一任务'))
        self.assertIsNone(value.snapshot('b')['last_complete'])

    def test_last_complete_is_host_owned_clipped_copied_and_counted_in_retention(self):
        value = self.streams(max_text=3, max_bytes=40)
        value.record('a', self.event(text='甲乙丙丁', last_complete={'text': 'forged'}))
        self.assertIsNone(value.snapshot('a')['last_complete'])
        value.record('a', self.event('turn_end', status='completed'))
        saved = value.snapshot('a')
        self.assertEqual(saved['last_complete']['text'], '甲乙丙')
        self.assertTrue(saved['last_complete']['truncated'])
        saved['last_complete']['text'] = 'consumer mutation'
        self.assertEqual(value.snapshot('a')['last_complete']['text'], '甲乙丙')
        for key in ('b', 'c', 'd'):
            value.record(key, self.event(turn=key, text='一二三'))
            value.record(key, self.event('turn_end', turn=key, status='completed'))
        self.assertLessEqual(sum(value._text_bytes(row[1]) for row in value.rows.values()), 40)

    def test_only_public_whitelisted_fields_are_copied_and_bad_status_is_error(self):
        value = self.streams()
        value.record('task', self.event(text='公开文字', arguments='private-argument',
                     reasoning_content='private-reasoning', api_key='private-key', actions=['delete']))
        value.record('task', self.event('stream_delta', tool_pending=True, delivery='sse'))
        row = value.snapshot('task')
        self.assertTrue(row['tool_pending'])
        self.assertEqual(row['text'], '公开文字')
        self.assertEqual(row['delivery'], 'sse')
        self.assertFalse(set(row) & {'arguments', 'reasoning_content', 'api_key', 'actions'})
        value.record('task', self.event('turn_end', status='task-cleanup-succeeded', code='x' * 100))
        self.assertEqual(value.snapshot('task')['status'], 'error')
        self.assertEqual(len(value.snapshot('task')['code']), 80)

    def test_invalid_events_do_not_create_rows_or_consume_valid_turn_identity(self):
        value = self.streams()
        for task, event in ((None, self.event()), ('', self.event()), ('x' * 129, self.event()),
                            ('task', None), ('task', {}), ('task', self.event(kind='unknown')),
                            ('task', self.event(turn='')), ('task', self.event(text=123))):
            value.record(task, event)
        self.assertEqual(value.sequence, 0)
        self.assertEqual(len(value.rows), 0)
        value.record('task', self.event(text='合法重试'))
        self.assertIsNotNone(value.snapshot('task'), 'a rejected event must not poison its turn ID')

    def test_preview_character_limit_preserves_unicode_and_explicit_truncation(self):
        value = self.streams(max_text=3)
        value.record('task', self.event(text='甲😀乙丙'))
        row = value.snapshot('task')
        self.assertEqual(row['text'], '甲😀乙')
        self.assertTrue(row['truncated'])
        value.record('task', self.event('stream_delta', text='更正'))
        self.assertEqual(value.snapshot('task')['text'], '更正')
        self.assertFalse(value.snapshot('task')['truncated'])

    def test_task_and_utf8_byte_retention_are_independently_bounded(self):
        value = self.streams(max_tasks=2, max_bytes=12)
        value.record('a', self.event(text='甲甲'))
        value.record('b', self.event(text='乙乙'))
        value.record('a', self.event('stream_delta', text='甲甲'))
        value.record('c', self.event(text='丙丙'))
        self.assertIsNone(value.snapshot('b'), 'least recently updated task is evicted')
        self.assertEqual(set(value.rows), {'a', 'c'})
        self.assertLessEqual(sum(len(row[1]['text'].encode()) for row in value.rows.values()), 12)
        value.record('large', self.event(text='大' * 10))
        self.assertLessEqual(sum(len(row[1]['text'].encode()) for row in value.rows.values()), 12)
        self.assertLessEqual(len(value.rows), 2)

    def test_recent_turn_id_history_is_bounded(self):
        value = self.streams(max_tasks=2)
        for number in range(100):
            value.record('task', self.event(turn=f'turn-{number}'))
        self.assertLessEqual(len(value.rows), 2)
        self.assertLessEqual(len(value.recent_turns), 16)

    def test_ttl_expiry_uses_observation_time_without_sleeping(self):
        clock = [100.0]
        with patch('diskpilot.live_streams.time.monotonic', side_effect=lambda: clock[0]):
            value = self.streams(ttl=10)
            value.record('older', self.event(text='较早'))
            clock[0] = 105.0
            value.record('newer', self.event(text='较晚'))
            clock[0] = 111.0
            self.assertIsNone(value.snapshot('older'))
            self.assertEqual(value.snapshot('newer')['text'], '较晚')
            clock[0] = 116.0
            self.assertIsNone(value.wait('newer', timeout=0))

    def test_wait_is_notified_by_a_new_snapshot_and_has_a_hard_wait_limit(self):
        value = self.streams()
        value.record('task', self.event(text='之前'))
        sequence = value.snapshot('task')['sequence']
        ready, result = threading.Event(), []

        def reader():
            with value.condition:
                ready.set()
                result.append(value.wait('task', sequence, timeout=2))

        worker = threading.Thread(target=reader)
        worker.start()
        self.assertTrue(ready.wait(1))
        value.record('task', self.event('stream_delta', text='之后'))
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]['text'], '之后')
        with patch.object(value.condition, 'wait', return_value=True) as wait:
            value.wait('missing', timeout=100000)
            wait.assert_called_once_with(2)

    def test_close_wakes_waiters_clears_buffers_and_rejects_future_frames(self):
        value = self.streams()
        value.record('task', self.event(text='内存内容'))
        sequence = value.snapshot('task')['sequence']
        ready, result = threading.Event(), []

        def reader():
            with value.condition:
                ready.set()
                result.append(value.wait('task', sequence, timeout=2))

        worker = threading.Thread(target=reader)
        worker.start()
        self.assertTrue(ready.wait(1))
        value.close()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [None])
        self.assertTrue(value.closed)
        self.assertFalse(value.rows)
        self.assertFalse(value.recent_turns)
        value.record('task', self.event(turn='new-turn', text='不得复活'))
        self.assertIsNone(value.snapshot('task'))

    def test_invalid_constructor_limits_are_rejected(self):
        for limits in ({'max_tasks': 0}, {'max_text': -1}, {'max_bytes': False},
                       {'max_tasks': 1.5}, {'ttl': 0}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                LiveStreams(**limits)


if __name__ == '__main__':
    unittest.main()
