import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness


class ListAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'Downloads'
        self.scope.mkdir()
        for index in range(125):
            (self.scope / f'file-{index:03d}.txt').write_text('disposable fixture')
        self.state = SimulationState(self.root / 'store')
        self.addCleanup(self.state.close)
        self.assistant = ModelAssistant(self.root / 'store')
        self.harness = TaskHarness(self.state, self.assistant)
        self.publish()

    def publish(self):
        self.state.publish(scan_directory(str(self.scope))['items'], 'selected')

    def tool(self, task, name, args):
        return self.harness.tool(task['id'], name, args, cancel=threading.Event())

    def file(self, name):
        return next(i for i in self.state.view()['items'] if i['name'] == name)

    def advice(self, item, conclusion='review'):
        return dict(id=item['id'], conclusion=conclusion, purpose='这是一份用户资料。', reason='需要你确认是否保留。', impact='处理后会少一份文件。')

    def test_whole_filtered_list_freezes_more_than_one_page(self):
        task = self.harness.create_analysis({'section': 'files', 'kind': 'file'})
        self.assertEqual(task['analysis']['target_count'], 125)
        self.assertEqual(task['analysis']['selection'], 'filtered')
        (self.scope / 'new.txt').write_text('new data')
        self.publish()
        self.assertEqual(self.tool(task, 'list_items', {})['total'], 125)

    def test_selected_file_cannot_scan_siblings(self):
        item = self.file('file-000.txt')
        task = self.harness.create_analysis({'section': 'files', 'ids': [item['id']]})
        self.assertEqual(self.tool(task, 'list_items', {})['total'], 1)
        with self.assertRaises(AppError) as caught:
            self.tool(task, 'survey_scope', {'scope_id': 'root'})
        self.assertEqual(caught.exception.code, 'outside_scope')
        self.assertEqual(self.harness._context(self.harness._get(task['id']))['scopes'], [])

    def test_contextual_software_lookup_does_not_expand_actions(self):
        item = self.file('file-000.txt')
        task = self.harness.create_analysis({'section': 'files', 'ids': [item['id']]})
        software = dict(id='context-app', kind='software', name='Fixture', path='fixture-context', signature='context-v1',
                        risk='review', actions=['uninstall'], details={}, size_bytes=0)
        with patch('diskpilot.tasks.inventory', return_value={'items': [software]}):
            self.tool(task, 'collect_apps', {'section': 'software'})
        with self.assertRaises(AppError) as caught:
            self.tool(task, 'propose_plan', {'summary': '不应扩大范围', 'items': [dict(id=software['id'], action='uninstall', reason='fixture', impact='fixture')]})
        self.assertEqual(caught.exception.code, 'outside_scope')
        with self.assertRaises(AppError):
            self.tool(task, 'record_advice', {'items': [self.advice(software)]})

    def test_selected_directory_can_expand_but_overview_cannot(self):
        directory = next(i for i in self.state.view()['items'] if i['kind'] == 'directory')
        task = self.harness.create_analysis({'section': 'files', 'ids': [directory['id']]})
        result = self.tool(task, 'inspect_directory', {'id': directory['id']})
        self.assertGreater(result['observed_children'], 0)
        overview = self.harness.create_analysis({'section': 'files', 'ids': [directory['id']], 'depth': 'overview'})
        with self.assertRaises(AppError):
            self.tool(overview, 'inspect_directory', {'id': directory['id']})

    def test_protected_metadata_can_be_analysed_but_not_proposed(self):
        self.assistant.save_settings({'protect_patterns': ['*file-000*']})
        item = self.file('file-000.txt')
        task = self.harness.create_analysis({'section': 'files', 'ids': [item['id']]})
        self.tool(task, 'record_advice', {'items': [self.advice(item, 'keep')]})
        public = self.harness.public(task['id'])
        self.assertEqual(public['advice'][0]['conclusion'], 'keep')
        self.assertEqual(public['analysis']['advised_count'], 1)
        self.assertIsNone(public['plan'])
        with self.assertRaises(AppError):
            self.tool(task, 'propose_plan', {'summary': '不可处理保护内容', 'items': [dict(id=item['id'], action='delete', reason='fixture', impact='fixture')]})
        with self.assertRaises(AppError):
            self.tool(task, 'record_advice', {'items': [self.advice(item, 'recommend')]})

    def test_advice_is_persistent_but_stale_after_new_snapshot(self):
        item = self.file('file-000.txt')
        task = self.harness.create_analysis({'ids': [item['id']]})
        self.tool(task, 'record_advice', {'items': [self.advice(item)]})
        self.assertEqual(self.tool(task, 'list_items', {'unanalysed': True})['total'], 0)
        (self.scope / item['name']).write_text('changed data and size')
        self.publish()
        result = self.harness.public(task['id'])
        self.assertTrue(result['advice'][0]['stale'])
        self.assertEqual(result['analysis']['remaining_count'], 1)
        self.assertEqual(self.tool(task, 'list_items', {'unanalysed': True})['total'], 1)

    def test_advice_is_not_authorization_or_implicit_personal_selection(self):
        item = self.file('file-000.txt')
        task = self.harness.create_analysis({'ids': [item['id']]})
        self.tool(task, 'record_advice', {'items': [self.advice(item, 'recommend')]})
        self.assertEqual(self.state.history(), [])
        with self.assertRaises(AppError):
            self.harness.validate_approval(task['id'], 1, [item['id']])
        self.tool(task, 'propose_plan', {'summary': '仍需用户选择', 'items': [dict(id=item['id'], action='delete', reason='fixture', impact='fixture')]})
        entry = self.harness.public(task['id'])['plan']['items'][0]
        self.assertTrue(entry['personal_choice_required'])
        self.assertFalse(entry['default_selected'])
        self.assertTrue((self.scope / item['name']).is_file())

    def test_tool_discovery_is_machine_readable_and_rejects_unknown_commands(self):
        task = self.harness.create_analysis({'kind': 'file'})
        help = self.tool(task, 'discover_tools', {'name': 'inspect_items'})
        self.assertEqual(help['tools'][0]['function']['name'], 'inspect_items')
        self.assertIn('parameters', help['tools'][0]['function'])
        with self.assertRaises(AppError):
            self.tool(task, 'discover_tools', {'name': 'powershell'})

    def test_detailed_directory_requires_discovery_and_each_observed_object(self):
        directory = next(i for i in self.state.view()['items'] if i['kind'] == 'directory')
        task = self.harness.create_analysis({'ids': [directory['id']], 'depth': 'detailed'})
        self.tool(task, 'record_advice', {'items': [self.advice(directory)]})
        # A directory-level opinion does not satisfy an explicit per-file request.
        with self.assertRaises(AppError) as caught:
            self.tool(task, 'present_findings', {'summary': '不能假装完成', 'needs_input': False})
        self.assertEqual(caught.exception.code, 'coverage_incomplete')
        self.tool(task, 'inspect_directory', {'id': directory['id']})
        result = self.harness.public(task['id'])
        self.assertEqual(result['analysis']['target_count'], 126)
        self.assertTrue(result['analysis']['directory_coverage_complete'])
        self.assertEqual(self.tool(task, 'list_items', {})['total'], 125)
        with self.assertRaises(AppError):
            self.tool(task, 'present_findings', {'summary': '还有文件未记录建议', 'needs_input': False})

    def test_detailed_partial_scan_never_claims_complete(self):
        directory = next(i for i in self.state.view()['items'] if i['kind'] == 'directory')
        task = self.harness.create_analysis({'ids': [directory['id']], 'depth': 'detailed'})
        report = scan_directory(str(self.scope))
        report['summary']['output_truncated'] = True
        report['summary']['omitted_items'] = 300
        with patch('diskpilot.tasks.scan_directory', return_value=report):
            self.tool(task, 'inspect_directory', {'id': directory['id']})
        self.assertFalse(self.harness.public(task['id'])['analysis']['directory_coverage_complete'])

    def test_advice_preview_and_pages_are_bounded(self):
        task = self.harness.create_analysis({'kind': 'file'})
        rows = self.harness._items(self.harness._get(task['id']))
        for offset in range(0, len(rows), 30):
            self.tool(task, 'record_advice', {'items': [self.advice(i) for i in rows[offset:offset + 30]]})
        public = self.harness.public(task['id'])
        self.assertEqual(len(public['advice']), 50)
        self.assertEqual(public['advice_total'], 125)
        self.assertTrue(public['advice_has_more'])
        self.assertEqual(public['analysis']['advised_count'], 125)
        first, second = self.harness.advice_page(task['id']), self.harness.advice_page(task['id'], offset=20)
        self.assertEqual(first['total'], 125)
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in second['items']})

    def test_scope_catalog_is_bounded_and_has_real_paging(self):
        for index in range(45):
            (self.scope / f'dir-{index}').mkdir()
        self.publish()
        task = self.harness.create_analysis({'kind': 'directory'})
        context = self.harness._context(self.harness._get(task['id']))
        self.assertEqual(len(context['scopes']), 40)
        self.assertEqual(context['scope_count'], 46)
        result = self.tool(task, 'list_scopes', {'offset': 40})
        self.assertEqual(len(result['scopes']), 6)

    def test_analysis_invalid_requests_fail_without_task_creation(self):
        for payload in ({'section': []}, {'depth': 'unlimited'}, {'ids': []}, {'ids': ['not-known']},
                        {'kind': 'service'}, {'query': 'x' * 201}, {'risk': 'unsafe'}):
            with self.subTest(payload=payload), self.assertRaises(AppError):
                self.harness.create_analysis(payload)
        self.assertEqual(self.harness._all(), [])


if __name__ == '__main__':
    unittest.main()
