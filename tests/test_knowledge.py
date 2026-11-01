import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from diskpilot import knowledge
from diskpilot.knowledge import SkillRegistry


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry()

    def assert_budget(self, value, budget):
        measured = len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        self.assertEqual(value['budget']['used'], measured)
        self.assertEqual(value['budget']['token_budget'], budget)
        self.assertLessEqual(measured, budget)

    def catalog(self, *, budget=3000, limit=2):
        cursor = None
        records = []
        for _ in range(30):
            page = self.registry.discover(cursor=cursor, limit=limit, token_budget=budget)
            self.assert_budget(page, budget)
            self.assertLessEqual(len(page['items']), limit)
            records.extend(page['items'])
            if not page['has_more']:
                self.assertIsNone(page['next_cursor'])
                return records
            self.assertTrue(page['items'])
            self.assertNotEqual(page['next_cursor'], cursor)
            cursor = page['next_cursor']
        self.fail('catalog failed to advance')

    def test_discovery_is_stable_paginated_public_metadata_without_content_reads(self):
        with patch('diskpilot.knowledge.os.open', side_effect=AssertionError('discovery read content')):
            first = self.catalog()
            second = self.catalog()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 17)
        self.assertEqual(len({item['id'] for item in first}), 17)
        self.assertEqual({item['id'] + '.md' for item in first}, set(knowledge.BUNDLED_SKILL_FILES))
        for item in first:
            self.assertTrue({'id', 'name', 'summary', 'applies_when', 'version', 'sources',
                             'checked_at', 'risk', 'readonly_support', 'capabilities'}.issubset(item))
            self.assertNotIn('content', item)
            self.assertTrue(item['capabilities']['readonly'])
            self.assertFalse(item['capabilities']['real_execution'])
            self.assertFalse(item['capabilities']['automatic_recommendation'])

    def test_all_guides_have_complete_content_hash_and_primary_source_snapshot(self):
        allowed = {'learn.microsoft.com', 'support.microsoft.com', 'docs.astral.sh', 'pip.pypa.io', 'docs.npmjs.com',
                   'helpx.adobe.com', 'www.asus.com', 'docs.docker.com', 'docs.unity.com', 'help.steampowered.com',
                   'support-en.sandisk.com', 'www.dell.com', 'support.google.com', 'chromeenterprise.google'}
        for item in self.catalog():
            with self.subTest(item=item['id']):
                read = self.registry.load(item['id'])
                self.assert_budget(read, 4000)
                self.assertTrue(read['guidance_only'])
                self.assertEqual(read['execution_support'], 'guidance_only')
                self.assertTrue(read['read_at'].endswith('+00:00'))
                raw = (knowledge._ASSET_DIR / (item['id'] + '.md')).read_bytes()
                self.assertEqual(read['sha256'], hashlib.sha256(raw).hexdigest())
                self.assertEqual(read['content_fingerprint'], read['sha256'])
                self.assertEqual(read['version'], item['version'])
                content = read['content']
                while read['has_more']:
                    read = self.registry.load(item['id'], cursor=read['next_cursor'])
                    self.assert_budget(read, 4000)
                    content += read['content']
                self.assertEqual(content, raw.decode('utf-8'))
                self.assertTrue(read['sources'])
                for source in read['sources']:
                    url = urlsplit(source['url'])
                    self.assertEqual(url.scheme, 'https')
                    self.assertIn(url.hostname, allowed)
                    self.assertEqual(source['checked_at'], knowledge.CHECKED_AT)
                    self.assertTrue(source['version'])

    def test_small_read_budget_reassembles_unicode_content_without_loss(self):
        ident = 'application-caches'
        cursor = None
        chunks, fingerprints, offsets = [], [], []
        for _ in range(50):
            page = self.registry.load(ident, cursor=cursor, token_budget=2300)
            self.assert_budget(page, 2300)
            self.assertTrue(page['content'])
            self.assertNotIn('\ufffd', page['content'])
            chunks.append(page['content'])
            fingerprints.append(page['content_fingerprint'])
            offsets.append(page['content_offset'])
            if not page['has_more']:
                break
            self.assertNotEqual(page['next_cursor'], cursor)
            cursor = page['next_cursor']
        else:
            self.fail('read pagination failed to advance')
        self.assertGreater(len(chunks), 1)
        original = (knowledge._ASSET_DIR / (ident + '.md')).read_text(encoding='utf-8')
        self.assertEqual(''.join(chunks), original)
        self.assertEqual(len(set(fingerprints)), 1)
        self.assertEqual(offsets, sorted(set(offsets)))

    def test_cursors_bind_query_skill_registry_and_full_content(self):
        first = self.registry.discover(limit=1)
        with self.assertRaises(ValueError):
            self.registry.discover(query='分页', cursor=first['next_cursor'])
        body = self.registry.load('application-caches', token_budget=2300)
        with self.assertRaises(ValueError):
            self.registry.load('temporary-files', cursor=body['next_cursor'])
        with self.assertRaises(ValueError):
            self.registry.discover(cursor=body['next_cursor'])
        with patch('diskpilot.knowledge.REGISTRY_VERSION', 'new-registry'):
            with self.assertRaises(ValueError):
                self.registry.discover(cursor=first['next_cursor'])
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            file = assets / 'application-caches.md'
            file.write_text('标题\n' + '这是固定指南的一段内容。' * 300, encoding='utf-8')
            with patch('diskpilot.knowledge._ASSET_DIR', assets):
                before = self.registry.load('application-caches', token_budget=2300)
                self.assertTrue(before['has_more'])
                file.write_text('已修订\n' + '这是更新后的内容。' * 300, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.registry.load('application-caches', cursor=before['next_cursor'], token_budget=2300)

    def test_fixed_allowlist_rejects_paths_urls_and_extra_resources_before_open(self):
        with patch('diskpilot.knowledge.os.open', side_effect=AssertionError('attempted arbitrary read')):
            for ident in ('../secret', 'C:\\Users\\someone\\private.txt', '/etc/passwd',
                          'file:///etc/passwd', 'https://example.com/guide.md', 'application-caches.md',
                          'application-caches/../../secret', '', None, 1, 'unlisted'):
                with self.subTest(ident=ident), self.assertRaises(ValueError):
                    self.registry.load(ident)
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            (assets / 'unlisted.md').write_text('not a bundled guide', encoding='utf-8')
            with patch('diskpilot.knowledge._ASSET_DIR', assets):
                self.assertNotIn('unlisted', {item['id'] for item in self.catalog()})
                with self.assertRaises(ValueError):
                    self.registry.load('unlisted')

    def test_resource_symlinks_and_oversize_are_rejected_without_reading(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary) / 'skills'
            assets.mkdir()
            file = assets / 'application-caches.md'
            file.write_bytes(b'x' * (knowledge.MAX_RESOURCE_BYTES + 1))
            with patch('diskpilot.knowledge._ASSET_DIR', assets), patch('diskpilot.knowledge.os.open', side_effect=AssertionError('read oversized resource')):
                with self.assertRaises(ValueError):
                    self.registry.load('application-caches')
            file.unlink()
            target = Path(temporary) / 'private.txt'
            target.write_text('fixture-only private data', encoding='utf-8')
            try:
                os.symlink(target, file)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f'environment cannot create symlinks: {exc}')
            with patch('diskpilot.knowledge._ASSET_DIR', assets), patch('diskpilot.knowledge.os.open', side_effect=AssertionError('followed symlink')):
                with self.assertRaises(ValueError):
                    self.registry.load('application-caches')

    def test_budget_validation_never_silently_emits_oversize_output(self):
        for budget in (0, True, 8001, '4000'):
            with self.assertRaises(ValueError):
                self.registry.load('pagefile-memory', token_budget=budget)
            with self.assertRaises(ValueError):
                self.registry.discover(token_budget=budget)
        with self.assertRaises(ValueError):
            self.registry.load('application-caches', token_budget=512)
        with self.assertRaises(ValueError):
            self.registry.discover(token_budget=512)
        for limit in (0, True, 11):
            with self.assertRaises(ValueError):
                self.registry.discover(limit=limit)
        for cursor in ('!', 'a' * 513, 3):
            with self.assertRaises(ValueError):
                self.registry.discover(cursor=cursor)

    def test_search_empty_results_and_returned_metadata_are_independent(self):
        result = self.registry.discover(query='分页')
        self.assertEqual([item['id'] for item in result['items']], ['pagefile-memory'])
        result['items'][0]['sources'][0]['url'] = 'https://example.com/changed'
        self.assertNotEqual(self.registry.load('pagefile-memory')['sources'][0]['url'], 'https://example.com/changed')
        empty = self.registry.discover(query='no_such_skill_qx9')
        self.assertEqual(empty['items'], [])
        self.assertFalse(empty['has_more'])
        self.assertIsNone(empty['next_cursor'])
        self.assert_budget(empty, 3000)
        self.assertEqual(self.registry.catalog(query='休眠'), self.registry.discover(query='休眠'))


if __name__ == '__main__':
    unittest.main()
