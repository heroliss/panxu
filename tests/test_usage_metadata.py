"""Model input uses compact, privacy-filtered, already-collected evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant
from diskpilot.usage_evidence import enrich_usage_evidence
from tests.test_usage_evidence import Reader, item, NOW, LAST, record


class UsageMetadataTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='panxu-usage-metadata-')
        self.addCleanup(temp.cleanup)
        self.assistant = ModelAssistant(Path(temp.name))

    def observed(self, kind='process', **fields):
        row, reader = item(kind), Reader()
        reader.rows = [reader.entry(data=record(17, LAST))]
        enrich_usage_evidence([row], reader=reader, observed=NOW)
        row['details'].update(fields)
        return row

    def test_snapshot_projection_has_distinct_current_process_and_history_facts(self):
        row = self.observed(pid=42, create_time=1_700_000_000, memory_bytes=1234)
        before = copy.deepcopy(row)
        with patch('diskpilot.usage_evidence.NativeUserAssistReader.entries', side_effect=AssertionError('must use snapshot')):
            result = self.assistant.task_metadata(row)
        facts = result['program_facts']
        self.assertEqual(facts['pid'], 42)
        self.assertEqual(facts['memory_bytes'], 1234)
        self.assertEqual(facts['process_started_at'], '2023-11-14T22:13:20+00:00')
        self.assertEqual(facts['usage_evidence']['last_recorded_launch_at'], LAST.isoformat())
        self.assertEqual(facts['usage_evidence']['cumulative_launch_count'], 17)
        self.assertEqual(facts['usage_evidence']['match_type'], 'process_executable_reference')
        self.assertEqual(row, before)

    def test_compact_usage_contract_drops_raw_and_repetitive_local_data(self):
        row = self.observed()
        local = row['details']['usage_evidence']
        local.update(command='SECRET-COMMAND', path=r'C:\private\fixture.exe',
                     raw_value='SECRET-BINARY', digest='SECRET-DIGEST', frequency_per_30_days=999,
                     reason='timestamp_unknown', limitations='SECRET-LONG-LIMITATIONS')
        for send_paths in (False, True):
            settings = self.assistant.public_settings()
            settings['send_paths'] = send_paths
            facts = self.assistant._metadata(row, settings, '', detailed=True)['program_facts']['usage_evidence']
            self.assertEqual(set(facts), {'source', 'status', 'coverage', 'last_recorded_launch_at',
                                         'cumulative_launch_count', 'match_type', 'reason'})
            wire = json.dumps(facts)
            for forbidden in ('SECRET', 'private', 'frequency', 'window_', 'reset', 'observed_at', 'matched_record_count'):
                self.assertNotIn(forbidden, wire)

    def test_unknown_preserves_null_time_and_count_without_calling_it_unused(self):
        for reason in ('no_matching_record', 'source_tracking_disabled', 'shared_registration_reference'):
            row = self.observed()
            row['details']['usage_evidence'].update(status='unknown', reason=reason,
                cumulative_launch_count=9999, last_recorded_launch_at=LAST.isoformat())
            facts = self.assistant.task_metadata(row)['program_facts']['usage_evidence']
            self.assertEqual(facts['status'], 'unknown')
            self.assertEqual(facts['reason'], reason)
            self.assertIsNone(facts['last_recorded_launch_at'])
            self.assertIsNone(facts['cumulative_launch_count'])
            self.assertNotIn('match_type', facts)

    def test_no_unknown_fields_can_forge_statistics_or_matching_source(self):
        for fields in ({'status': 'complete'}, {'match_type': 'similar_name'},
                       {'cumulative_launch_count': True}, {'cumulative_launch_count': -1},
                       {'cumulative_launch_count': 2 ** 32}, {'last_recorded_launch_at': 'PRIVATE-TEXT'},
                       {'reason': 'SECRET-REASON'}):
            with self.subTest(fields=fields):
                row = self.observed()
                row['details']['usage_evidence'].update(fields)
                facts = self.assistant.task_metadata(row)['program_facts']['usage_evidence']
                self.assertNotIn('SECRET', json.dumps(facts))
                if 'status' in fields or 'match_type' in fields:
                    self.assertEqual(facts['status'], 'unknown')
                    self.assertIsNone(facts['cumulative_launch_count'])
                if 'cumulative_launch_count' in fields:
                    self.assertIsNone(facts['cumulative_launch_count'])
                if 'last_recorded_launch_at' in fields:
                    self.assertIsNone(facts['last_recorded_launch_at'])
        for fields in ({'source': 'SECRET-SOURCE'}, {'coverage': 'machine_wide'}):
            row = self.observed()
            row['details']['usage_evidence'].update(fields)
            self.assertNotIn('usage_evidence', self.assistant.task_metadata(row)['program_facts'])

    def test_software_registration_version_publisher_scope_are_bounded_but_no_install_date(self):
        row = self.observed('software', version='2.4.0', scope='当前用户', has_uninstaller=True,
                            InstallDate='20010101', registry_location=r'C:\private\registration')
        row['owner'] = 'Fixture publisher'
        facts = self.assistant.task_metadata(row)['program_facts']
        self.assertEqual(facts['version'], '2.4.0')
        self.assertEqual(facts['publisher'], 'Fixture publisher')
        self.assertEqual(facts['scope'], '当前用户')
        self.assertEqual(facts['usage'], 'unknown')
        self.assertEqual(facts['usage_evidence']['match_type'], 'registered_executable_reference')
        self.assertNotIn('InstallDate', json.dumps(facts))
        self.assertNotIn('private', json.dumps(facts))
        row['owner'] = 'x' * 1000
        row['details']['version'] = 'y' * 1000
        self.assertLessEqual(len(self.assistant.task_metadata(row)['program_facts']['version']), 120)
        self.assertLessEqual(len(self.assistant.task_metadata(row)['program_facts']['publisher']), 120)

    def test_zero_counter_is_preserved_without_adding_frequency_or_general_last_used(self):
        row = self.observed()
        row['details']['usage_evidence']['cumulative_launch_count'] = 0
        facts = self.assistant.task_metadata(row)['program_facts']['usage_evidence']
        self.assertEqual(facts['cumulative_launch_count'], 0)
        self.assertNotIn('frequency', json.dumps(facts))
        self.assertNotIn('last_used', json.dumps(facts))

    def test_basic_list_and_detailed_inspect_both_receive_existing_key_facts(self):
        row = self.observed('software', version='2.4', scope='整机')
        settings = self.assistant.public_settings()
        with patch('diskpilot.usage_evidence.NativeUserAssistReader.entries', side_effect=AssertionError('no repeat read')):
            listing = self.assistant._tool('list_items', {}, [row], settings, '')
            inspection = self.assistant._tool('inspect_items', {'ids': [row['id']]}, [row], settings, '')
        self.assertEqual(listing['items'][0]['program_facts'], inspection['items'][0]['program_facts'])
        self.assertEqual(listing['items'][0]['program_facts']['version'], '2.4')


if __name__ == '__main__':
    unittest.main()
