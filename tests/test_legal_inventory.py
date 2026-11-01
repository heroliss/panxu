import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from verification import finalize


class LegalInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
            (self.root / name).write_text('disposable license fixture', encoding='utf-8')
        directory = self.root / 'licenses/third-party'
        directory.mkdir(parents=True)
        self.notice = directory / 'fixture-license.txt'
        self.notice.write_bytes(b'fixture terms')
        self.entry = dict(path='licenses/third-party/fixture-license.txt', sha256=hashlib.sha256(self.notice.read_bytes()).hexdigest())
        self.manifest = directory / 'manifest.json'
        self.write([self.entry])
        self.patcher = patch.object(finalize, 'ROOT', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write(self, entries):
        self.manifest.write_text(json.dumps(dict(files=entries)), encoding='utf-8')

    def test_known_notices_and_manifest_are_included_without_collecting_other_files(self):
        (self.root / 'private.json').write_text('never bundle this')
        result = finalize.legal_sources()
        self.assertEqual(len(result), 5)
        self.assertNotIn(self.root / 'private.json', result)

    def test_escape_paths_and_duplicate_paths_are_rejected(self):
        for path in ('licenses/third-party/../../private.json', '/outside.txt', r'licenses\third-party\fixture.txt', 'licenses/third-party/a/../fixture-license.txt', 'licenses/third-party//fixture-license.txt'):
            self.write([dict(self.entry, path=path)])
            with self.assertRaises(RuntimeError):
                finalize.legal_sources()
        self.write([self.entry, self.entry])
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            finalize.legal_sources()

    def test_changed_terms_and_missing_owner_notice_block_packaging(self):
        self.notice.write_bytes(b'changed terms')
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            finalize.legal_sources()
        self.notice.write_bytes(b'fixture terms')
        (self.root / 'NOTICE').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Missing application'):
            finalize.legal_sources()
