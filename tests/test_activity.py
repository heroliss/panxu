import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from helpers import TempDirTest
from panxu import sandbox
from panxu.api import Backend, Bridge, SERIAL_METHODS
from panxu.jobs import UserError
from panxu.windowing import WindowControls, restore_size_needed


class ActivityTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.sb = sandbox.Sandbox(self.tmp + '/sb')
        self.backend = Backend(self.tmp + '/data', sandbox=self.sb)
        self.bridge = Bridge(self.backend)
        self.addCleanup(sandbox.deactivate)
        self.addCleanup(self.backend.jobs.stop)

    def test_checkup_blocks_writers_before_reading_inputs_but_keeps_results_readable(self):
        rows = [{'id': 'user_temp', 'size': 123}]
        self.backend.junk_rows = rows
        job = self.backend.jobs.start('checkup', lambda j: j.cancel.wait(5), group='checkup')
        with patch.object(self.backend, '_client', side_effect=AssertionError('must not start AI')):
            for method in SERIAL_METHODS:
                answer = self.bridge.call(method, {})
                self.assertFalse(answer['ok'], method)
                self.assertEqual(answer['busy']['id'], job, method)
        self.assertEqual(self.bridge.call('junk_result')['data'], rows)
        self.assertEqual(self.bridge.call('activity')['data']['jobs'][0]['id'], job)
        self.assertEqual(len(self.backend.jobs.running()), 1)
        self.backend.jobs.stop()
        with patch.object(self.backend.junk, 'scan', return_value=rows):
            answer = self.bridge.call('junk_scan')
            self.assertTrue(answer['ok'])

    def test_simultaneous_scans_register_only_one_job_and_reverse_conflict_is_blocked(self):
        barrier = threading.Barrier(2)
        def scan(cancel, progress):
            cancel.wait(5)
            return []
        def submit():
            barrier.wait()
            return self.bridge.call('junk_scan')
        with patch.object(self.backend.junk, 'scan', side_effect=scan), ThreadPoolExecutor(2) as pool:
            results = [f.result() for f in [pool.submit(submit), pool.submit(submit)]]
            self.assertEqual(sum(r['ok'] for r in results), 1)
            answer = self.bridge.call('checkup')
            self.assertFalse(answer['ok'])
            self.assertEqual(answer['busy']['kind'], 'junk_scan')

    def test_read_only_adviser_does_not_lock_result_writers(self):
        first = self.backend.jobs.start('ai_ask', lambda j: j.cancel.wait(5))
        second = self.backend.jobs.start('checkup', lambda j: j.cancel.wait(5), group='checkup')
        self.assertNotEqual(first, second)
        self.assertEqual([j['id'] for j in self.bridge.call('activity')['data']['jobs']], [second])


class WindowControlsTest(TempDirTest):
    def test_restore_compensates_only_for_the_native_border_not_snap_or_dpi_changes(self):
        self.assertTrue(restore_size_needed((1320, 860), (1306, 846), (14, 14)))
        self.assertFalse(restore_size_needed((1320, 860), (1320, 860), (14, 14)))
        self.assertFalse(restore_size_needed((1320, 860), (960, 1040), (14, 14)))
        self.assertFalse(restore_size_needed((1320, 860), (1640, 1065), (18, 18)))
        self.assertFalse(restore_size_needed((1320, 860), (1320, 860), (0, 0)))

    def test_buttons_and_restore_keep_close_confirmation_in_window_destroy(self):
        class FakeWindow:
            def __init__(self):
                self.calls = []
            def minimize(self): self.calls.append('minimize')
            def maximize(self): self.calls.append('maximize')
            def restore(self): self.calls.append('restore')
            def destroy(self): self.calls.append('closing-event')
        window = FakeWindow()
        controls = WindowControls(lambda: window)
        with patch.object(controls, '_prepare_frame'):
            self.assertTrue(controls.perform('toggle_maximize')['maximized'])
            self.assertFalse(controls.perform('toggle_maximize')['maximized'])
        controls.perform('minimize')
        controls.perform('close')
        self.assertEqual(window.calls, ['maximize', 'restore', 'minimize', 'closing-event'])
        with self.assertRaises(UserError): controls.perform('bad-command')
        with self.assertRaises(UserError): WindowControls().perform('close')

    def test_maximize_event_can_arrive_before_action_returns(self):
        controls = WindowControls()
        window = type('Window', (), {'maximize': lambda _: controls._maximized(True)})()
        controls.provider = lambda: window
        with patch.object(controls, '_prepare_frame'):
            self.assertTrue(controls.perform('toggle_maximize')['maximized'])
