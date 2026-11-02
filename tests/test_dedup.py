"""Independent duplicate search reads only disposable, owned local fixtures."""
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from diskpilot import dedup
from diskpilot.duplicates import _entity, verify_duplicates
from diskpilot.scanner import scan_directory
from diskpilot.server import Application
from diskpilot.simulation import AppError


class DuplicateScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-dedup-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'owned'
        self.scope.mkdir()

    def write(self, name, data=b'owned identical bytes'):
        path = self.scope / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_scan(self, **kwargs):
        return dedup.run_duplicate_scan({'paths': [str(self.scope)], **kwargs})

    def selected(self):
        return [i for i in scan_directory(str(self.scope))['items'] if i['kind'] == 'file']

    def test_two_stages_reject_same_size_different_bytes_without_llm_or_writes(self):
        a, b = self.write('a.bin'), self.write('nested/b.other')
        self.write('c.bin', b'x' * a.stat().st_size)
        self.write('unique-size.bin', b'unique length')
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.scope.rglob('*') if p.is_file()}
        events = []
        result = dedup.run_duplicate_scan({'paths': [str(self.scope)]}, progress=events.append)
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(result['partial'])
        self.assertFalse(result['llm_used'])
        self.assertTrue(result['read_only'])
        self.assertEqual(result['actual_reclaimed_bytes'], 0)
        self.assertEqual(result['summary']['files_seen'], 4)
        self.assertEqual(result['summary']['candidate_files'], 3)
        self.assertEqual(result['summary']['hashed_files'], 3)
        self.assertEqual(result['bytes_read'], a.stat().st_size * 3)
        group, = result['groups']
        self.assertEqual(group['type'], 'content')
        self.assertEqual({x['path'] for x in group['items']}, {str(a), str(b)})
        self.assertEqual(group['sha256'], hashlib.sha256(a.read_bytes()).hexdigest())
        self.assertEqual(group['verification'], 'sha256')
        self.assertEqual(group['items_total'], 2)
        self.assertIsNone(group['reclaimable_bytes'])
        self.assertTrue(all(i['signature'] and i['details']['file_identity']
            and i['details']['timestamps']['observed_at'] and i['details']['source'] == 'local_duplicate_scan'
            for i in group['items']))
        self.assertEqual({e['phase'] for e in events}, {'scanning', 'hashing', 'finished'})
        self.assertTrue(all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == value for p, value in before.items()))
        repeated = self.run_scan()
        self.assertEqual(repeated['groups'][0]['group_id'], group['group_id'])

    def test_unique_sizes_never_open_content_and_empty_files_are_valid_duplicates(self):
        self.write('unique-1', b'a')
        self.write('unique-2', b'bb')
        with patch('diskpilot.duplicates.Path.open', side_effect=AssertionError('Unique sizes must not read bytes')):
            result = self.run_scan()
        self.assertEqual(result['bytes_read'], 0)
        self.assertEqual(result['summary']['candidate_files'], 0)
        self.write('empty-a', b'')
        self.write('empty-b', b'')
        result = self.run_scan()
        self.assertEqual(result['summary']['content_groups'], 1)
        self.assertEqual(result['groups'][0]['size_bytes'], 0)
        self.assertEqual(result['bytes_read'], 0)

    def test_hardlinks_are_separate_evidence_and_hash_shared_entity_once(self):
        original = self.write('original.bin')
        try:
            os.link(original, self.scope / 'alias.bin')
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        self.write('independent.bin')
        result = self.run_scan()
        self.assertEqual(result['summary']['content_groups'], 1)
        self.assertEqual(result['summary']['hardlink_groups'], 1)
        self.assertEqual(result['summary']['hashed_files'], 2)
        self.assertEqual(result['summary']['checked_files'], 3)
        self.assertEqual(result['bytes_read'], original.stat().st_size * 2)
        content = next(g for g in result['groups'] if g['type'] == 'content')
        link = next(g for g in result['groups'] if g['type'] == 'hardlink')
        self.assertEqual(content['physical_file_count'], 2)
        self.assertEqual(link['verification'], 'file_identity')
        self.assertIsNone(link['reclaimable_bytes'])

    def test_unknown_inode_never_merges_unrelated_entities(self):
        info = SimpleNamespace(st_dev=1, st_ino=0)
        self.assertNotEqual(_entity(info, self.scope / 'a'), _entity(info, self.scope / 'b'))

    def test_overlapping_roots_are_deduplicated_without_reading_outside_scope(self):
        first = self.write('sub/a')
        self.write('sub/b')
        outside = self.root / 'outside.bin'
        outside.write_bytes(first.read_bytes())
        result = dedup.run_duplicate_scan({'paths': [str(self.scope / 'sub'), str(self.scope), str(self.scope)]})
        self.assertEqual(result['roots'], [str(self.scope)])
        self.assertEqual(result['summary']['files_seen'], 2)
        self.assertEqual(len(result['groups'][0]['items']), 2)
        self.assertTrue(all(i['path'].startswith(str(self.scope)) for i in result['groups'][0]['items']))

    def test_multi_root_search_can_find_cross_directory_copy(self):
        self.write('a')
        other = self.root / 'another'
        other.mkdir()
        (other / 'b').write_bytes(b'owned identical bytes')
        result = dedup.run_duplicate_scan({'paths': [str(self.scope), str(other)]})
        self.assertEqual(result['summary']['content_groups'], 1)
        self.assertEqual(len(result['roots']), 2)

    def test_protected_patterns_credentials_and_project_subtrees_are_not_read(self):
        self.write('public-a')
        self.write('public-b')
        self.write('keep/private.bin')
        self.write('.env', b'owned identical bytes')
        self.write('project/.git/HEAD', b'fixture')
        self.write('project/source.bin')
        result = dedup.run_duplicate_scan({'paths': [str(self.scope)]}, patterns=['*keep*'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['summary']['content_groups'], 1)
        self.assertEqual({i['name'] for i in result['groups'][0]['items']}, {'public-a', 'public-b'})
        self.assertGreaterEqual(result['summary']['skipped']['protected'], 3)
        self.assertEqual(result['bytes_read'], len(b'owned identical bytes') * 2)

    def test_new_protection_during_job_is_rechecked_before_hashing(self):
        self.write('a')
        self.write('b')
        patterns = []

        def progress(event):
            if event['phase'] == 'hashing':
                patterns[:] = [str(self.scope)]

        result = dedup.run_duplicate_scan({'paths': [str(self.scope)]},
            patterns=lambda: patterns, progress=progress)
        self.assertFalse(result['groups'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['bytes_read'], 0)
        self.assertGreater(result['summary']['skipped']['protected'], 0)

    def test_links_are_not_followed_and_all_invalid_root_is_explicit_failure(self):
        other = self.root / 'external'
        other.mkdir()
        (other / 'secret').write_bytes(b'owned identical bytes')
        try:
            os.symlink(other, self.scope / 'link', target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        self.write('a')
        result = self.run_scan()
        self.assertFalse(result['groups'])
        self.assertEqual(result['summary']['skipped']['reparse_point'], 1)
        self.assertEqual(result['bytes_read'], 0)
        with self.assertRaises(AppError):
            dedup.run_duplicate_scan({'paths': [str(self.scope / 'link' / '..' / 'safe')]})

    def test_unavailable_root_does_not_hide_partial_scope(self):
        self.write('a')
        self.write('b')
        result = dedup.run_duplicate_scan({'paths': [str(self.scope), str(self.root / 'missing')]})
        self.assertEqual(result['summary']['content_groups'], 1)
        self.assertTrue(result['partial'])
        self.assertEqual(result['summary']['skipped']['unavailable'], 1)
        with self.assertRaises(AppError):
            dedup.run_duplicate_scan({'paths': [str(self.root / 'missing')]})

    def test_entry_depth_and_read_budgets_have_honest_partial_results(self):
        for name in ('a', 'b', 'nested/c'):
            self.write(name)
        entries = self.run_scan(max_entries=1)
        self.assertTrue(entries['partial'])
        self.assertEqual(entries['summary']['scanned_entries'], 1)
        self.assertTrue(entries['summary']['entry_limit_reached'])
        depth = self.run_scan(max_depth=1)
        self.assertTrue(depth['partial'])
        self.assertEqual(depth['summary']['skipped']['depth_limit'], 1)
        budget = self.run_scan(max_bytes=1)
        self.assertTrue(budget['partial'])
        self.assertEqual(budget['bytes_read'], 0)
        self.assertEqual(budget['summary']['skipped']['byte_budget'], 3)
        self.assertFalse(budget['groups'])

    def test_cancel_during_enumeration_is_observed_without_hashing(self):
        self.write('a')
        self.write('b')
        cancel = threading.Event()

        def progress(event):
            if event['phase'] == 'scanning':
                cancel.set()

        with patch('diskpilot.duplicates.Path.open', side_effect=AssertionError('Cancelled bytes must not open')):
            result = dedup.run_duplicate_scan({'paths': [str(self.scope)]}, cancel=cancel, progress=progress)
        self.assertEqual(result['status'], 'cancelled')
        self.assertTrue(result['partial'])
        self.assertEqual(result['bytes_read'], 0)

    def test_cancel_at_hash_phase_keeps_partial_status_without_opening_contents(self):
        self.write('a')
        self.write('b')
        cancel = threading.Event()

        def progress(event):
            if event['phase'] == 'hashing':
                cancel.set()

        with patch('diskpilot.duplicates.Path.open', side_effect=AssertionError('Cancelled bytes must not open')):
            result = dedup.run_duplicate_scan({'paths': [str(self.scope)]}, cancel=cancel, progress=progress)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['bytes_read'], 0)

    def test_deadline_produces_partial_without_claiming_no_duplicates(self):
        self.write('a')
        self.write('b')
        with patch.object(dedup, 'MAX_SECONDS', 0):
            result = self.run_scan()
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['summary']['timed_out'])
        self.assertEqual(result['bytes_read'], 0)

    def test_result_group_limit_reports_true_total_and_no_automatic_choice(self):
        for number in range(7):
            self.write(f'{number}-a', bytes([number]) * (number + 1))
            self.write(f'{number}-b', bytes([number]) * (number + 1))
        with patch.object(dedup, 'MAX_GROUPS', 3):
            result = self.run_scan()
        self.assertEqual(len(result['groups']), 3)
        self.assertEqual(result['summary']['total_groups'], 7)
        self.assertEqual(result['summary']['omitted_groups'], 4)
        self.assertEqual(result['summary']['omitted_group_items'], 8)
        self.assertTrue(result['result_truncated'])
        self.assertTrue(result['partial'])
        self.assertNotIn('keep_id', result['groups'][0])
        self.assertNotIn('delete_ids', result['groups'][0])

    def test_result_member_limit_retains_group_total_and_omitted_count(self):
        for number in range(7):
            self.write(f'{number}.bin')
        with patch.object(dedup, 'MAX_RESULT_ITEMS', 3):
            result = self.run_scan()
        group, = result['groups']
        self.assertEqual(len(group['items']), 3)
        self.assertEqual(group['items_total'], 7)
        self.assertEqual(group['omitted_items'], 4)
        self.assertEqual(result['summary']['omitted_group_items'], 4)
        self.assertTrue(result['result_truncated'])

    def test_real_two_thousand_path_result_cap_keeps_full_count(self):
        for number in range(2002):
            self.write(f'{number:04}.empty', b'')
        result = self.run_scan()
        group, = result['groups']
        self.assertEqual(result['summary']['files_seen'], 2002)
        self.assertEqual(result['summary']['checked_files'], 2002)
        self.assertEqual(result['summary']['returned_files'], 2000)
        self.assertEqual(result['summary']['omitted_group_items'], 2)
        self.assertEqual(group['items_total'], 2002)
        self.assertEqual(group['omitted_items'], 2)
        self.assertEqual(len(group['items']), 2000)
        self.assertEqual(result['bytes_read'], 0)
        self.assertTrue(result['result_truncated'])

    def test_validation_is_strict_bounded_and_does_not_probe_disk(self):
        with patch('os.lstat', side_effect=AssertionError('Validation must not probe disk')):
            valid = dedup.validate_duplicate_request({'paths': [str(self.scope)]})
        self.assertEqual(valid['max_entries'], 25000)
        self.assertEqual(valid['max_depth'], 16)
        self.assertEqual(valid['max_bytes'], 2 * 1024 ** 3)
        self.assertEqual(dedup.MAX_GROUPS, 200)
        self.assertEqual(dedup.MAX_RESULT_ITEMS, 2000)
        invalid = [{}, {'paths': []}, {'paths': ['relative']}, {'paths': ['\\\\server\\share']},
            {'paths': [str(self.scope)] * 9}, {'paths': [str(self.scope)], 'algorithm': 'md5'},
            {'paths': [str(self.scope)], 'max_entries': True}, {'paths': [str(self.scope)], 'max_depth': 33},
            {'paths': [str(self.scope)], 'max_bytes': 8 * 1024 ** 3 + 1}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(AppError):
                dedup.validate_duplicate_request(payload)

    def test_scan_job_does_not_publish_catalog_call_model_or_create_task(self):
        self.write('a')
        self.write('b')
        app = Application(self.root / 'profile')
        self.addCleanup(app.close)
        revision = app.state.revision
        with patch.object(app.assistant, 'agent_turn', side_effect=AssertionError('Independent search cannot call LLM')), \
                patch.object(app.state, 'publish', side_effect=AssertionError('Independent search must preserve catalog')):
            job_id = app.post('/api/duplicates/scan', {'paths': [str(self.scope)]})['job_id']
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = app.job(job_id)
                if job['status'] != 'running':
                    break
                time.sleep(.01)
        self.assertEqual(job['status'], 'completed', job.get('error'))
        self.assertEqual(job['result']['summary']['content_groups'], 1)
        self.assertEqual(app.state.revision, revision)
        self.assertEqual(app.state.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)


class SelectedFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-hash-')
        self.addCleanup(self.temp.cleanup)
        self.scope = Path(self.temp.name)
        for name in ('a.bin', 'b.bin'):
            (self.scope / name).write_bytes(b'owned duplicate bytes')
        self.items = [i for i in scan_directory(str(self.scope))['items'] if i['kind'] == 'file']

    def test_old_selected_interface_still_bounds_hundred_files(self):
        with self.assertRaises(AppError):
            verify_duplicates(self.items * 51)

    def test_installer_suffixes_hash_without_false_path_handle_mode_changes(self):
        content = b'owned inert installer placeholder, never executable'
        for name in ('setup.exe', 'setup-copy.exe', 'setup.bat'):
            (self.scope / name).write_bytes(content)
        items = [i for i in scan_directory(str(self.scope))['items']
                 if i['kind'] == 'file' and i['name'].startswith('setup')]
        result = verify_duplicates(items)
        self.assertFalse(result['partial'])
        self.assertEqual(result['bytes_read'], len(content) * 3)
        self.assertEqual(result['hashed_files'], 3)
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual(len(result['groups'][0]['items']), 3)

    def test_changed_handle_file_type_still_rejects_before_content_read(self):
        import stat
        original = os.fstat

        def handle_stat(fd):
            info = original(fd)
            fields = {key: getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_size',
                'st_mtime_ns', 'st_ctime_ns', 'st_nlink')}
            fields['st_file_attributes'] = getattr(info, 'st_file_attributes', 0)
            fields['st_mode'] = stat.S_IFDIR | (info.st_mode & 0o777)
            return SimpleNamespace(**fields)

        with patch('diskpilot.duplicates.os.fstat', side_effect=handle_stat):
            result = verify_duplicates(self.items)
        self.assertFalse(result['groups'])
        self.assertEqual(result['bytes_read'], 0)
        self.assertEqual(result['skipped']['changed'], 2)

    def test_stale_scan_signature_is_rejected_before_reading_file(self):
        target = self.scope / 'a.bin'
        before = target.stat()
        target.write_bytes(b'newly changed content')
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
        result = verify_duplicates(self.items)
        self.assertFalse(result['groups'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['bytes_read'], 0)
        self.assertEqual(result['skipped']['changed'], 1)

    def test_cloud_placeholder_is_never_opened(self):
        original = Path.lstat
        target = self.scope / 'a.bin'

        def cloud(path):
            value = original(path)
            if path != target:
                return value
            return SimpleNamespace(st_file_attributes=0x1000, st_mode=value.st_mode)

        with patch.object(Path, 'lstat', cloud), patch.object(Path, 'open', side_effect=AssertionError('Cloud content must not open')):
            result = verify_duplicates(self.items)
        self.assertEqual(result['skipped']['cloud_placeholder'], 1)
        self.assertEqual(result['bytes_read'], 0)
        self.assertFalse(result['groups'])

    @unittest.skipUnless(os.name == 'nt', 'Windows path/handle ctime compatibility')
    def test_windows_handle_ctime_can_differ_from_path_ctime_without_false_change(self):
        original = os.fstat

        def handle_stat(fd):
            info = original(fd)
            fields = {key: getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_size',
                'st_mtime_ns', 'st_ctime_ns', 'st_nlink', 'st_file_attributes')}
            fields['st_ctime_ns'] = 12345  # Stable handle time, different path time.
            return SimpleNamespace(**fields)

        with patch('diskpilot.duplicates.os.fstat', side_effect=handle_stat):
            result = verify_duplicates(self.items)
        self.assertFalse(result['partial'])
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual(result['bytes_read'], len(b'owned duplicate bytes') * 2)

    @unittest.skipUnless(os.name == 'nt', 'Windows path/handle ctime compatibility')
    def test_handle_time_change_during_hash_still_invalidates_result(self):
        original = os.fstat
        calls = 0

        def handle_stat(fd):
            nonlocal calls
            calls += 1
            info = original(fd)
            fields = {key: getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_size',
                'st_mtime_ns', 'st_ctime_ns', 'st_nlink', 'st_file_attributes')}
            fields['st_ctime_ns'] = calls  # Different opened/after on same API.
            return SimpleNamespace(**fields)

        with patch('diskpilot.duplicates.os.fstat', side_effect=handle_stat):
            result = verify_duplicates(self.items)
        self.assertTrue(result['partial'])
        self.assertFalse(result['groups'])
        self.assertEqual(result['skipped']['changed'], 2)

    def test_changed_content_during_read_is_not_returned_as_verified(self):
        original = Path.open
        target = self.scope / 'a.bin'
        changed = False

        class ChangingStream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self, size):
                nonlocal changed
                data = self.stream.read(size)
                if not changed:
                    changed = True
                    target.write_bytes(b'changed while hash is running')
                return data

        def opening(path, *args, **kwargs):
            stream = original(path, *args, **kwargs)
            return ChangingStream(stream) if path == target and args and args[0] == 'rb' else stream

        with patch.object(Path, 'open', opening):
            result = verify_duplicates(self.items)
        self.assertTrue(changed)
        self.assertFalse(result['groups'])
        self.assertTrue(result['partial'])
        self.assertGreater(result['bytes_read'], 0)
        self.assertGreater(result['skipped']['changed'], 0)

    def test_cancellation_between_hash_chunks_stops_at_one_mebibyte(self):
        for path in self.scope.iterdir():
            path.write_bytes(b'owned-chunk-data' * (2 * 1024 * 1024 // len(b'owned-chunk-data') + 1))
        items = [i for i in scan_directory(str(self.scope))['items'] if i['kind'] == 'file']
        original = Path.open
        cancel = threading.Event()

        class StoppingStream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self, size):
                data = self.stream.read(size)
                cancel.set()
                return data

        def opening(path, *args, **kwargs):
            return StoppingStream(original(path, *args, **kwargs))

        with patch.object(Path, 'open', opening):
            result = verify_duplicates(items, cancel=cancel)
        self.assertTrue(result['cancelled'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['bytes_read'], 1024 * 1024)
        self.assertEqual(result['hashed_files'], 0)
        self.assertFalse(result['groups'])

    def test_failed_open_is_not_counted_as_read_bytes(self):
        original = Path.open
        failed = self.scope / 'a.bin'

        def opening(path, *args, **kwargs):
            if path == failed:
                raise PermissionError('owned test: inaccessible')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'open', opening):
            result = verify_duplicates(self.items)
        self.assertTrue(result['partial'])
        self.assertEqual(result['bytes_read'], len(b'owned duplicate bytes'))
        self.assertEqual(result['skipped']['unavailable'], 1)
        self.assertEqual(result['hashed_files'], 1)


if __name__ == '__main__':
    unittest.main()
