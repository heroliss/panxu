from pathlib import Path
import tempfile
import unittest

from verification.finalize import release_files


class ReleasePrivacyTests(unittest.TestCase):
    def test_new_task_or_developer_databases_cannot_enter_portable_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'PanXu.exe').write_bytes(b'fixture')
            for name in ('developer_trace.sqlite3', 'scan-index.sqlite3', 'unfamiliar-task.db', 'state.sqlite3-wal'):
                target = root / name
                target.write_bytes(b'private fixture')
                try:
                    with self.assertRaisesRegex(RuntimeError, 'Private or test data'):
                        release_files(root)
                finally:
                    target.unlink()
            self.assertEqual([p.name for p in release_files(root)], ['PanXu.exe'])

    def test_any_userdata_subdirectory_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / '_internal' / 'userdata'
            private.mkdir(parents=True)
            (private / 'observations.json').write_text('fixture')
            with self.assertRaisesRegex(RuntimeError, 'Private or test data'):
                release_files(root)
