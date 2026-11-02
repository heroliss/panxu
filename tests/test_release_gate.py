from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from verification.finalize import release_files, source_model_evidence, sha, MODEL_SOURCE_FILES


class ReleasePrivacyTests(unittest.TestCase):
    def test_real_model_source_receipt_cannot_claim_final_executable_or_changed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verify = root / 'verification'
            verify.mkdir()
            hashes = {}
            for name in MODEL_SOURCE_FILES:
                target = root / name
                target.parent.mkdir(exist_ok=True)
                target.write_text('owned fixture source', encoding='utf-8')
                hashes[name] = sha(target)
            report = dict(passed=True, real_provider_used=True, real_fixture_unchanged=True,
                actual_reclaimed_bytes=0, scope='source_harness', final_exe_attribution=False, source_sha256=hashes)
            path = verify / 'real-model.json'
            def write(): path.write_text(json.dumps(report), encoding='utf-8')
            with patch('verification.finalize.ROOT', root), patch('verification.finalize.VERIFY', verify):
                write()
                self.assertFalse(source_model_evidence(path)['final_exe_attribution'])
                report['final_exe_attribution'] = True
                write()
                with self.assertRaisesRegex(RuntimeError, 'scoped real-model acceptance'):
                    source_model_evidence(path)
                report['final_exe_attribution'] = False
                write()
                (root / 'diskpilot/tasks.py').write_text('changed source', encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'stale'):
                    source_model_evidence(path)

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
