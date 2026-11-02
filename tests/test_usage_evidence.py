"""Synthetic UserAssist records only; never read the test machine's history."""
import codecs
import copy
import ctypes
from datetime import datetime, timezone
import json
import struct
import types
import unittest
from unittest.mock import patch

from diskpilot import usage_evidence as usage, windows


NOW = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
LAST = datetime(2026, 10, 30, 3, tzinfo=timezone.utc)
EXE = r'D:\Fixture\Editor\editor.exe'


def record(count=42, last=LAST):
    data = bytearray(72)
    ticks = int((last - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10_000_000) if last else 0
    struct.pack_into('<I', data, 4, count)
    struct.pack_into('<Q', data, 60, ticks)
    return bytes(data)


class Reader:
    def __init__(self, rows=(), *, status='available', partial=False):
        self.rows, self.status, self.partial = rows, status, partial
        self.calls = self.reads = self.visited = 0
        self.closed = False

    def entry(self, path=EXE, *, data=None, group='one', **changes):
        def read():
            self.reads += 1
            if isinstance(data, Exception):
                raise data
            return record() if data is None else data
        result = dict(version=5, name=codecs.encode(path, 'rot_13'), size=72, type=3, read=read, group=group)
        result.update(changes)
        return result

    def entries(self, groups):
        self.calls += 1
        assert groups == 16
        try:
            for row in self.rows:
                self.visited += 1
                yield row
        finally:
            self.closed = True


def item(kind='process', ident='a', path=EXE):
    row = windows._item(kind, ident, 'Fixture editor', 'Fixture publisher', 'Fixture',
                        {'process': 'stop_process', 'startup': 'disable_startup', 'service': 'stop_service',
                         'software': 'uninstall'}[kind], {'synthetic': False})
    row['details'].update(_usage_executable_digest=usage.executable_digest(path),
        _usage_match_type={'process': 'process_executable_reference', 'startup': 'startup_executable_reference',
                          'software': 'registered_executable_reference', 'service': 'process_executable_reference'}[kind])
    return row


class UsageEvidenceTests(unittest.TestCase):
    def enrich(self, rows=None, reader=None, **limits):
        rows = [item()] if rows is None else rows
        reader = Reader() if reader is None else reader
        summary = usage.enrich_usage_evidence(rows, reader=reader, observed=NOW, **limits)
        return rows, summary

    def test_exact_path_case_normalization_but_no_basename_or_fuzzy_match(self):
        reader = Reader()
        reader.rows = [reader.entry(path=r'd:/fixture/editor/EDITOR.EXE')]
        rows, summary = self.enrich(reader=reader)
        facts = rows[0]['details']['usage_evidence']
        self.assertEqual(facts['status'], 'matched')
        self.assertEqual(facts['last_recorded_launch_at'], LAST.isoformat())
        self.assertEqual(facts['cumulative_launch_count'], 42)
        self.assertIsNone(facts['frequency_per_30_days'])
        self.assertIsNone(facts['window_start_at'])
        self.assertFalse(summary['partial'])
        self.assertTrue(reader.closed)
        for other in (r'C:\Another\editor.exe', r'D:\Fixture\Editor\editor2.exe', 'editor.exe'):
            reader = Reader()
            reader.rows = [reader.entry(path=other)]
            rows, _ = self.enrich(reader=reader)
            self.assertEqual(rows[0]['details']['usage_evidence']['status'], 'unknown')
            self.assertEqual(reader.reads, 0)

    def test_local_reference_parsers_reject_ambiguous_or_shared_commands(self):
        digest = usage.executable_digest(EXE)
        self.assertEqual(usage.command_executable_digest('"' + EXE + '" --start'), digest)
        self.assertEqual(usage.command_executable_digest(EXE + ' --start'), digest)
        self.assertEqual(usage.registered_executable_digest('"' + EXE + '",-3'), digest)
        self.assertEqual(usage.registered_executable_digest(EXE + ',0'), digest)
        for value in (r'C:\Program Files\app.exe --run', 'editor.exe', '"' + EXE + '"junk',
                      r'C:\Windows\System32\cmd.exe /c ' + EXE,
                      r'C:\Windows\System32\rundll32.exe shell.dll,Run',
                      r'D:\Fixture\unins000.exe', r'D:\Fixture\setup.exe'):
            self.assertIsNone(usage.command_executable_digest(value), value)
        for value in (r'D:\Fixture\icon.dll,0', r'D:\Fixture\uninstall.exe', r'@{package!app}',
                      'editor', r'D:\Fixture\app.lnk', r'\\server\share\editor.exe',
                      r'D:\Fixture\..\editor.exe', r'D:\Fixture\editor.exe:stream', r'D:\Fixture\launcher.exe'):
            self.assertIsNone(usage.registered_executable_digest(value), value)
        self.assertEqual(usage.executable_digest(r'%ROOT%\editor.exe', environment={'root': r'D:\Fixture\Editor'}), digest)
        self.assertIsNone(usage.executable_digest(r'%UNKNOWN%\editor.exe', environment={}))

    def test_zero_count_is_observed_cumulative_not_recent_frequency_and_zero_time_unknown(self):
        reader = Reader()
        reader.rows = [reader.entry(data=record(0, None))]
        rows, _ = self.enrich(reader=reader)
        facts = rows[0]['details']['usage_evidence']
        self.assertEqual(facts['cumulative_launch_count'], 0)
        self.assertIsNone(facts['last_recorded_launch_at'])
        self.assertIsNone(facts['frequency_per_30_days'])
        self.assertEqual(facts['reason'], 'timestamp_unknown')
        self.assertTrue(facts['counter_reset_possible'])

    def test_unavailable_disabled_missing_and_absent_are_unknown_not_zero(self):
        for status, reason in (('unavailable', 'source_unavailable'), ('missing', 'source_missing'),
                               ('not_supported', 'source_not_supported'), ('tracking_disabled', 'source_tracking_disabled'),
                               ('available', 'no_matching_record')):
            with self.subTest(status=status):
                rows, _ = self.enrich(reader=Reader(status=status))
                facts = rows[0]['details']['usage_evidence']
                self.assertEqual(facts['status'], 'unknown')
                self.assertEqual(facts['reason'], reason)
                self.assertIsNone(facts['last_recorded_launch_at'])
                self.assertIsNone(facts['cumulative_launch_count'])

    def test_invalid_unsupported_changed_and_large_values_never_become_usage(self):
        for changes in ({'version': 3}, {'size': 1_000_000_000}, {'type': 1},
                        {'data': b'short'}, {'data': OSError('changed')}, {'name': None}, {'name': 'x' * 1025}):
            with self.subTest(changes=changes):
                reader = Reader()
                reader.rows = [reader.entry(**changes)]
                rows, summary = self.enrich(reader=reader)
                facts = rows[0]['details']['usage_evidence']
                self.assertEqual(facts['status'], 'unknown')
                self.assertIsNone(facts['cumulative_launch_count'])
                self.assertTrue(summary['partial'])
                self.assertTrue(reader.closed)
                if changes.get('size') or changes.get('type') or changes.get('version'):
                    self.assertEqual(reader.reads, 0)

    def test_invalid_or_future_filetime_is_unknown_not_invented(self):
        for ticks in (1, 2 ** 64 - 1):
            with self.subTest(ticks=ticks):
                data = bytearray(record())
                struct.pack_into('<Q', data, 60, ticks)
                parsed = usage._record(bytes(data), NOW)
                self.assertIsNone(parsed['last_recorded_launch_at'])
                self.assertEqual(parsed['cumulative_launch_count'], 42)

    def test_multiple_counters_are_not_summed_and_duplicates_are_not_counted_twice(self):
        reader = Reader()
        reader.rows = [reader.entry(data=record(2, LAST)), reader.entry(data=record(2, LAST)),
                       reader.entry(group='two', data=record(9, NOW))]
        rows, _ = self.enrich(reader=reader)
        facts = rows[0]['details']['usage_evidence']
        self.assertEqual(reader.reads, 2)
        self.assertEqual(facts['status'], 'partial')
        self.assertEqual(facts['matched_record_count'], 2)
        self.assertIsNone(facts['cumulative_launch_count'])
        self.assertEqual(facts['last_recorded_launch_at'], NOW.isoformat())
        self.assertEqual(facts['reason'], 'multiple_counters')

    def test_shared_registered_executable_does_not_become_app_wide_usage(self):
        reader = Reader()
        reader.rows = [reader.entry()]
        rows, _ = self.enrich([item('software', 'a'), item('software', 'b'), item()], reader)
        for row in rows[:2]:
            facts = row['details']['usage_evidence']
            self.assertEqual(facts['status'], 'unknown')
            self.assertEqual(facts['reason'], 'shared_registration_reference')
        self.assertEqual(rows[2]['details']['usage_evidence']['match_type'], 'process_executable_reference')

    def test_services_unmatched_shortcuts_and_unrelated_files_do_not_trigger_history_read(self):
        rows = [item('service'), item('startup', path=r'D:\Fixture\editor.lnk'),
                dict(id='file', kind='file', details={})]
        reader = Reader()
        rows, summary = self.enrich(rows, reader)
        self.assertEqual(reader.calls, 0)
        self.assertEqual(summary['source_status'], 'not_queried')
        self.assertEqual(rows[0]['details']['usage_evidence']['reason'], 'not_a_gui_usage_source')
        self.assertEqual(rows[1]['details']['usage_evidence']['reason'], 'no_exact_executable')
        self.assertNotIn('usage_evidence', rows[2]['details'])

    def test_unresolved_known_folder_shortcut_and_package_records_mark_source_partial(self):
        for reference in (r'{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}\Editor\editor.exe',
                          r'D:\Fixture\editor.lnk', 'Vendor.Package_abcd!Editor',
                          r'UEME_RUNPIDL:D:\Fixture\editor.lnk'):
            with self.subTest(reference=reference):
                reader = Reader()
                reader.rows = [reader.entry(path=reference)]
                rows, summary = self.enrich(reader=reader)
                self.assertTrue(summary['partial'])
                self.assertEqual(summary['unsupported_references'], 1)
                self.assertEqual(rows[0]['details']['usage_evidence']['reason'], 'source_scan_limited')
                self.assertIsNone(rows[0]['details']['usage_evidence']['cumulative_launch_count'])
                self.assertEqual(reader.reads, 0)
        reader = Reader()
        reader.rows = [reader.entry(), reader.entry(path='Vendor.Package_abcd!Editor')]
        rows, summary = self.enrich(reader=reader)
        self.assertEqual(rows[0]['details']['usage_evidence']['status'], 'partial')
        self.assertEqual(rows[0]['details']['usage_evidence']['cumulative_launch_count'], 42)

    def test_value_byte_and_deadline_limits_are_honest_and_close_iterator(self):
        for limits in ({'max_values': 1}, {'max_bytes': 1}, {'max_seconds': 0}):
            with self.subTest(limits=limits):
                reader = Reader()
                reader.rows = [reader.entry(), reader.entry(group='two')]
                _, summary = self.enrich(reader=reader, **limits)
                self.assertTrue(summary['partial'])
                self.assertTrue(summary['limit_reached'])
                self.assertLessEqual(reader.visited, 1)
                if reader.visited:
                    self.assertTrue(reader.closed)
        reader = Reader()
        reader.rows = [reader.entry(path=r'D:\Other\app.exe'), reader.entry()]
        _, summary = self.enrich(reader=reader, max_values=1)
        self.assertEqual(reader.reads, 0)
        self.assertEqual(summary['values_checked'], 1)

    def test_output_is_private_and_acquisition_does_not_change_action_signature(self):
        reader = Reader()
        reader.rows = [reader.entry(path='UEME_RUNPATH:' + EXE)]
        rows = [item()]
        before = copy.deepcopy(rows[0])
        rows, summary = self.enrich(rows, reader)
        self.assertEqual(rows[0]['signature'], before['signature'])
        text = json.dumps([rows[0]['details']['usage_evidence'], summary])
        self.assertNotIn(EXE, text)
        self.assertNotIn('editor.exe', text)
        self.assertNotIn(usage.executable_digest(EXE), text)
        self.assertEqual(reader.reads, 1)

    def test_inventory_enriches_once_but_execution_validation_never_reads_history(self):
        reader = Reader()
        reader.rows = [reader.entry()]
        process = item()
        startup = item('startup')
        service = item('service')
        with patch.object(windows, 'startup_items', return_value=([startup], [])), \
                patch.object(windows, 'service_items', return_value=([service], [])), \
                patch.object(windows, 'process_items', return_value=([process], [])):
            report = windows.inventory('background', usage_reader=reader)
            self.assertEqual(reader.calls, 1)
            self.assertEqual(reader.reads, 1)
            self.assertEqual(report['summary']['usage_evidence']['source_status'], 'available')
            with patch.object(windows, 'enrich_usage_evidence', side_effect=AssertionError('history must not repeat')):
                windows.validate_system_items(report['items'])
        with patch.object(windows, 'software_items', return_value=([item('software')], [])):
            reader = Reader(status='tracking_disabled')
            report = windows.inventory('software', usage_reader=reader)
            self.assertEqual(reader.calls, 1)
            self.assertTrue(report['warnings'])

    def test_reachability_probe_only_opens_fixed_key_without_enumerating_history(self):
        class Handle:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return None
        registry = types.SimpleNamespace(HKEY_CURRENT_USER=1, KEY_READ=2,
            OpenKey=lambda *args: Handle(), EnumKey=lambda *args: self.fail('history enumeration forbidden'),
            QueryValueEx=lambda *args: self.fail('history payload forbidden'))
        with patch.object(usage.os, 'name', 'nt'), patch.dict('sys.modules', winreg=registry):
            self.assertEqual(usage.probe_userassist_source()['status'], 'available')

    def native_reader(self, *, tracking=1, version=5):
        """Inject all Win32 registry calls; never open the real user hive."""
        events = []
        names = [codecs.encode(r'D:\Other\editor.exe', 'rot_13'), codecs.encode(EXE, 'rot_13')]
        class Handle:
            def __init__(self, value):
                self.value = value
            def __int__(self):
                return self.value
            def __enter__(self):
                return self
            def __exit__(self, *args):
                events.append(('close', self.value))
        def open_key(parent, path, *args):
            events.append(('open', path))
            return Handle({usage.TRACKING_KEY: 1, usage.USERASSIST_KEY: 2, 'group': 3, 'Count': 4}[path])
        def query(handle, name, reserved, kind, data, size):
            events.append(('query', name, size._obj.value))
            if name in ('Start_TrackProgs', 'Version'):
                self.assertEqual(size._obj.value, 4)
                kind._obj.value, size._obj.value = 4, 4
                data._obj.value = tracking if name == 'Start_TrackProgs' else version
                return 0
            self.assertEqual(name, names[1], 'unmatched registry payload must never be read')
            self.assertEqual(size._obj.value, 72)
            kind._obj.value, size._obj.value = 3, 72
            ctypes.memmove(data, record(), 72)
            return 0
        def enum(handle, index, name, length, reserved, kind, data, size):
            self.assertIsNone(data, 'enumeration must read only metadata, not history payloads')
            events.append(('enum', index))
            if index >= len(names):
                return 259
            name.value, length._obj.value, kind._obj.value = names[index], len(names[index]), 3
            size._obj.value = 999_999 if index == 0 else 72
            return 0
        class Function:
            def __init__(self, callback):
                self.callback = callback
            def __call__(self, *args):
                return self.callback(*args)
        registry = types.SimpleNamespace(HKEY_CURRENT_USER=0, KEY_READ=1, REG_DWORD=4, REG_BINARY=3,
            OpenKey=open_key, QueryInfoKey=lambda handle: (1, 0, 0), EnumKey=lambda handle, index: 'group')
        advapi = types.SimpleNamespace(RegEnumValueW=Function(enum), RegQueryValueExW=Function(query))
        with patch.object(usage.os, 'name', 'nt'), patch.dict('sys.modules', winreg=registry), \
                patch.object(ctypes, 'WinDLL', return_value=advapi, create=True):
            reader = usage.NativeUserAssistReader()
            rows, summary = self.enrich(reader=reader)
        return rows, summary, events

    def test_native_reader_uses_bounded_type_size_probes_and_only_reads_matched_record(self):
        rows, summary, events = self.native_reader()
        self.assertEqual(rows[0]['details']['usage_evidence']['cumulative_launch_count'], 42)
        self.assertFalse(summary['partial'])
        self.assertEqual([event[2] for event in events if event[0] == 'query'], [4, 4, 72])
        self.assertEqual(sum(event[0] == 'close' for event in events), 4)

    def test_native_explicit_tracking_disabled_never_reads_stale_counters(self):
        rows, summary, events = self.native_reader(tracking=0)
        self.assertEqual(summary['source_status'], 'tracking_disabled')
        self.assertEqual(rows[0]['details']['usage_evidence']['reason'], 'source_tracking_disabled')
        self.assertIsNone(rows[0]['details']['usage_evidence']['cumulative_launch_count'])
        self.assertFalse(any(event[0] == 'enum' for event in events))
        self.assertNotIn(('open', usage.USERASSIST_KEY), events)

    def test_native_unsupported_version_does_not_apply_old_counter_offset_rules(self):
        rows, summary, events = self.native_reader(version=3)
        self.assertTrue(summary['partial'])
        self.assertEqual(summary['unsupported_formats'], 1)
        self.assertEqual(rows[0]['details']['usage_evidence']['status'], 'unknown')
        self.assertFalse(any(event[0] == 'enum' for event in events))

    def test_nonwindows_native_source_is_explicitly_unknown(self):
        with patch.object(usage.os, 'name', 'posix'):
            rows, summary = self.enrich(reader=usage.NativeUserAssistReader())
        self.assertEqual(summary['source_status'], 'not_supported')
        self.assertIsNone(rows[0]['details']['usage_evidence']['cumulative_launch_count'])


if __name__ == '__main__':
    unittest.main()
