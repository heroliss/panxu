"""Assessment facts use disposable fixtures and never contact a model service."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from diskpilot.knowledge import BUNDLED_SKILL_FILES, SKILL_VERSION, SkillRegistry
from diskpilot.llm import ModelAssistant
from diskpilot.recommendations import _validated_suggestions, build_recommendations
from diskpilot.scanner import _metadata_signature, _utc_timestamp_ns, metadata_item, scan_directory
from diskpilot.windows import _item
from tests.test_recommendations import StubAssistant, cache, item, suggestion


class AssessmentMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-assessment-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assistant = ModelAssistant(self.root / 'model')

    def stat_info(self, **changes):
        data = dict(st_mode=stat.S_IFREG | 0o600, st_size=8, st_ino=7, st_dev=1, st_nlink=1,
                    st_mtime_ns=1_700_000_000_123_456_789, st_ctime_ns=1_750_000_000_999_999_999,
                    st_atime_ns=1_710_000_000_000_000_000, st_birthtime_ns=1_600_000_000_987_654_321)
        data.update(changes)
        return SimpleNamespace(**data)

    def make_metadata(self, info, *, directory=False):
        return metadata_item(str(self.root / 'example'), info, root=str(self.root), depth=1,
                             parent_id='parent', project=None, is_dir=directory)

    def test_birthtime_is_creation_and_ctime_remains_legacy_signature_input(self):
        info = self.stat_info()
        before = _metadata_signature(str(self.root / 'example'), info)
        row = self.make_metadata(info)
        times = row['details']['timestamps']
        self.assertEqual(times['created_at'], '2020-09-13T12:26:40.987654+00:00')
        self.assertEqual(times['modified_at'], '2023-11-14T22:13:20.123456+00:00')
        self.assertNotEqual(times['created_at'], _utc_timestamp_ns(info.st_ctime_ns))
        self.assertEqual(times['scope'], 'entry_only')
        self.assertIsNotNone(datetime.fromisoformat(times['observed_at']).tzinfo)
        self.assertEqual(row['details']['ctime_ns'], info.st_ctime_ns)
        self.assertEqual(before, _metadata_signature(str(self.root / 'example'), info))

    def test_missing_birthtime_is_unknown_even_when_ctime_exists(self):
        info = self.stat_info()
        del info.st_birthtime_ns
        self.assertIsNone(self.make_metadata(info)['details']['timestamps']['created_at'])
        for invalid in (True, '1700000000000000000', 1.7e18, 10 ** 100):
            with self.subTest(invalid=invalid):
                self.assertIsNone(_utc_timestamp_ns(invalid))

    def test_model_projection_uses_iso_and_keeps_access_time_in_detailed_evidence(self):
        row = self.make_metadata(self.stat_info())
        settings = self.assistant.public_settings()
        basic = self.assistant._metadata(row, settings, '', detailed=False)
        detailed = self.assistant.task_metadata(row)
        self.assertNotIn('accessed_at', basic['timestamps'])
        self.assertIn('accessed_at', detailed['timestamps'])
        self.assertNotIn('mtime_ns', json.dumps(detailed))
        self.assertNotIn('ctime_ns', json.dumps(detailed))
        self.assertEqual(detailed['timestamps']['observed_at'], row['details']['timestamps']['observed_at'])
        self.assertNotIn('last_used', json.dumps(detailed))

    def test_legacy_metadata_does_not_invent_creation_or_observation_time(self):
        row = item(details={'mtime_ns': 1_700_000_000_000_000_000,
                            'ctime_ns': 1_600_000_000_000_000_000})
        times = self.assistant.task_metadata(row)['timestamps']
        self.assertEqual(times['modified_at'], '2023-11-14T22:13:20.000000+00:00')
        self.assertIsNone(times['created_at'])
        self.assertIsNone(times['observed_at'])
        self.assertIsNone(times['accessed_at'])

    def test_directory_time_is_entry_only_even_with_recent_descendant_and_partial_coverage(self):
        folder = self.root / 'data'
        child = folder / 'nested'
        child.mkdir(parents=True)
        (child / 'active.txt').write_text('fixture', encoding='utf-8')
        os.utime(folder, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        with patch('builtins.open', side_effect=AssertionError('scanner must not read contents')):
            report = scan_directory(str(folder), max_depth=0)
        row = next(value for value in report['items'] if value['path'] == str(folder))
        self.assertFalse(row['details']['subtree_complete'])
        self.assertEqual(row['details']['timestamps']['scope'], 'entry_only')
        self.assertEqual(row['details']['timestamps']['modified_at'], '2001-09-09T01:46:40.000000+00:00')
        self.assertFalse(row['details']['can_simulate_delete'])

    def test_program_facts_are_whitelisted_and_usage_stays_unknown(self):
        details = dict(state='Running', start_mode='Auto', memory_bytes=12345,
                       create_time=1_700_000_000, has_uninstaller=True, estimated_install_bytes=789,
                       observed_at='2026-11-02T04:05:06+00:00', usage='used yesterday',
                       last_used='invented', InstallDate='20000101', command_line='SECRET-COMMAND',
                       registry_location=r'C:\private\registry', arbitrary='SECRET-OTHER')
        for kind in ('process', 'service', 'startup', 'software'):
            with self.subTest(kind=kind):
                row = item(kind=kind, details=copy.deepcopy(details))
                before = copy.deepcopy(row)
                result = self.assistant.task_metadata(row)
                facts = result['program_facts']
                wire = json.dumps(result)
                self.assertNotIn('SECRET-', wire)
                self.assertNotIn('private', wire)
                self.assertNotIn('last_used', wire)
                self.assertNotIn('InstallDate', wire)
                self.assertEqual(result['observed_at'], details['observed_at'])
                if kind == 'process':
                    self.assertEqual(facts['process_started_at'], '2023-11-14T22:13:20+00:00')
                    self.assertEqual(facts['memory_bytes'], 12345)
                    self.assertNotIn('has_uninstaller', facts)
                elif kind == 'software':
                    self.assertEqual(facts['usage'], 'unknown')
                    self.assertTrue(facts['has_uninstaller'])
                    self.assertEqual(facts['estimated_install_bytes'], 789)
                    self.assertNotIn('process_started_at', facts)
                elif kind == 'service':
                    self.assertEqual(facts['start_mode'], 'Auto')
                self.assertEqual(row, before)

    def test_bad_timestamps_and_program_scalars_do_not_become_facts(self):
        row = item(kind='process', details={'state': r'Running C:\private\work', 'memory_bytes': True,
                                            'create_time': float('nan'), 'observed_at': 'SECRET-TEXT'})
        facts = self.assistant.task_metadata(row)
        self.assertIsNone(facts['observed_at'])
        self.assertIsNone(facts['program_facts']['process_started_at'])
        self.assertNotIn('memory_bytes', facts['program_facts'])
        self.assertNotIn('private', json.dumps(facts))
        row = item(details={'timestamps': {'created_at': '2024-01-01',
                                           'modified_at': 'SECRET', 'observed_at': True}})
        self.assertTrue(all(value is None for name, value in self.assistant.task_metadata(row)['timestamps'].items()
                            if name != 'scope'))

    def test_windows_inventory_observation_time_is_timezone_qualified(self):
        row = _item('startup', 'fixture', 'Fixture', 'Test', 'test', 'disable_startup', {'synthetic': True})
        self.assertEqual(datetime.fromisoformat(row['details']['observed_at']).utcoffset(), timezone.utc.utcoffset(None))


class CleanupRatingTests(unittest.TestCase):
    def test_absent_score_is_unknown_for_legacy_model_and_local_rules(self):
        for assistant, use_model in ((None, False), (StubAssistant([suggestion('a')]), True)):
            result = build_recommendations([cache()], assistant, use_model=use_model)
            self.assertIsNone(result['items'][0]['advice']['cleanup_rating'])

    def test_three_is_valid_analysed_neutral_not_missing_and_personal_high_needs_choice(self):
        neutral = dict(suggestion('a'), cleanup_rating=3)
        high = dict(suggestion('b'), cleanup_rating=5)
        result = build_recommendations([cache(), item('b')], StubAssistant([neutral, high]), use_model=True)
        self.assertEqual(result['items'][0]['advice']['cleanup_rating'], 3)
        advice = result['items'][1]['advice']
        self.assertEqual(advice['cleanup_rating'], 5)
        self.assertEqual(advice['group'], 'personal')
        self.assertTrue(advice['needs_user_choice'])
        self.assertFalse(advice['auto_select'])

    def test_protected_items_do_not_gain_model_rating_or_action(self):
        protected = cache(risk='protected')
        result = build_recommendations([protected], StubAssistant([dict(suggestion('a'), cleanup_rating=5)]), use_model=True)
        self.assertIsNone(result['items'][0]['advice']['cleanup_rating'])
        self.assertFalse(result['items'][0]['advice']['auto_select'])

    def test_invalid_scores_or_obvious_contradictions_reject_entire_batch(self):
        for score in (True, False, 0, 6, 3.0, '4', [], {}):
            with self.subTest(score=score), self.assertRaises(ValueError):
                _validated_suggestions([dict(suggestion('a'), cleanup_rating=score)], [cache()])
        for score in (1, 2):
            with self.subTest(score=score), self.assertRaises(ValueError):
                _validated_suggestions([dict(suggestion('a'), cleanup_rating=score)], [cache()])
        rows = _validated_suggestions([dict(suggestion('a', recommend=False), cleanup_rating=1)], [cache()])
        self.assertEqual(rows[0]['cleanup_rating'], 1)

    def test_new_guide_is_fixed_versioned_and_bundled(self):
        self.assertEqual(SKILL_VERSION, '1.2.0')
        self.assertIn('cleanup-assessment.md', BUNDLED_SKILL_FILES)
        registry = SkillRegistry()
        page = registry.load('cleanup-assessment')
        content = page['content']
        while page['has_more']:
            page = registry.load('cleanup-assessment', cursor=page['next_cursor'])
            content += page['content']
        self.assertEqual(page['version'], '1.2.0')
        self.assertIn('不是概率、置信度、安全等级或操作授权', content)
        self.assertIn('不能用 3 假装已分析', content)
        self.assertIn('inheritance=subtree', content)
        self.assertIn('操作权限不能继承', content)
        self.assertEqual(len(page['sources']), 5)


if __name__ == '__main__':
    unittest.main()
