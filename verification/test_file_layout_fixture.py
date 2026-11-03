"""Local-only behaviour checks for the owned file layout seeder."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from run_file_layout_fixture import ROOT, DATABASES, create_samples, hashes, seed, snapshot_profile
from diskpilot.server import Application


class LayoutFixtureTests(unittest.TestCase):
    def test_samples_have_varied_real_metadata_and_inert_content(self):
        with tempfile.TemporaryDirectory(prefix='panxu-layout-unit-') as directory:
            scope = Path(directory)
            data = create_samples(scope)
            self.assertEqual(len(list(scope.iterdir())), 123)
            self.assertEqual(len(hashes(scope)), 127)
            self.assertEqual(len([p for p in scope.iterdir() if p.is_file()]), 121)
            self.assertTrue(Path(data['known_directory']).is_dir())
            self.assertTrue(Path(data['unknown_directory']).is_dir())
            self.assertEqual((scope / '空文件.tmp').stat().st_size, 0)
            self.assertGreater(len({p.stat().st_mtime_ns for p in scope.iterdir() if p.is_file()}), 100)
            self.assertGreater(len({p.stat().st_size for p in scope.iterdir() if p.is_file()}), 100)

    def test_seed_and_external_native_snapshot_preserve_state_and_never_configure_a_model(self):
        with tempfile.TemporaryDirectory(prefix='panxu-layout-unit-') as directory:
            base = Path(directory)
            scope, source, native = base / 'files', base / 'source', base / 'native'
            scope.mkdir(); source.mkdir(); native.mkdir()
            app = Application(source)
            try:
                metadata = seed(app, scope)
                before = hashes(scope)
                snapshot_profile(source.resolve(), native.resolve())
                self.assertEqual(metadata['reviewed_count'], 5)
                self.assertEqual(app.harness.public(metadata['task_id'])['advice_total'], 5)
                self.assertEqual(app.state.history(), [])
                self.assertEqual(app.assistant.public_settings()['model'], '')
                self.assertFalse(app.assistant.public_settings()['has_key'])
                unknown = app.file_browser.page(metadata['unknown_directory'], limit=50)
                self.assertEqual(len(unknown['items']), 3)
                for name in DATABASES:
                    with closing(sqlite3.connect((source / name).resolve().as_uri() + '?mode=ro', uri=True)) as a:
                        with closing(sqlite3.connect((native / name).resolve().as_uri() + '?mode=ro', uri=True)) as b:
                            self.assertEqual(a.execute('SELECT COUNT(*) FROM sqlite_master').fetchone(),
                                             b.execute('SELECT COUNT(*) FROM sqlite_master').fetchone())
                settings = json.loads((native / 'model_settings.json').read_text(encoding='utf-8'))
                self.assertEqual(settings['model'], '')
                self.assertFalse(settings.get('key_dpapi'))
                self.assertFalse(settings.get('api_key'))
                self.assertEqual(hashes(scope), before)
            finally:
                app.close()


if __name__ == '__main__':
    unittest.main()
