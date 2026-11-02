import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from diskpilot.scanner import scan_directory
from diskpilot.server import Application
from diskpilot.simulation import AppError


class AssessmentCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'downloads'
        self.scope.mkdir()
        for folder in ('cache', 'mixed'):
            (self.scope / folder).mkdir()
            for name in ('a.bin', 'b.bin', 'c.bin'):
                (self.scope / folder / name).write_bytes(b'fixture-only')
        self.app = Application(self.root / 'data')
        self.addCleanup(self.app.close)
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'test')
        self.items = {str(Path(i['path']).relative_to(self.scope)): i for i in self.app.state.snapshot([])['items']}

    def advice(self, relative, score, conclusion='recommend', inheritance='none'):
        item = self.items[relative]
        record = dict(id=item['id'], signature=item['signature'], path=item['path'], kind=item['kind'],
                      cleanup_rating=score, conclusion=conclusion, inheritance=inheritance,
                      purpose='夹具', reason='仅用于评分验收', impact='只记录模拟')
        task = dict(id='catalog-fixture', target_ids=[item['id']], advice_revision=int(self.app.harness.assessments.revision()) + 1)
        with self.app.state.lock, self.app.state.db:
            self.app.harness.assessments.publish(task, {item['id']: record})

    def test_sparse_direct_list_and_observed_tree_have_different_membership(self):
        self.advice('cache', 5, inheritance='subtree')
        self.advice('mixed', 5, inheritance='subtree')
        self.advice(str(Path('mixed') / 'a.bin'), 1, 'keep')
        self.advice(str(Path('mixed') / 'b.bin'), None, 'skip')
        direct = self.app.catalog.page(view='suggestions')
        self.assertEqual(direct['total'], 4)
        self.assertTrue(all(i['assessment']['state'] != 'inherited' for i in direct['items']))
        child = self.app.catalog.page(view='tree', parent_id=self.items['cache']['id'])
        self.assertEqual(child['total'], 3)
        self.assertTrue(all(i['assessment']['rating'] == 5 and i['assessment']['state'] == 'inherited' for i in child['items']))
        self.assertTrue(all(i['assessment']['inherited_from_name'] == 'cache' for i in child['items']))
        self.assertTrue(child['coverage']['observed_only'])
        self.assertEqual(child['breadcrumbs'][-1]['id'], self.items['cache']['id'])
        mixed = {i['name']: i for i in self.app.catalog.page(view='tree', parent_id=self.items['mixed']['id'])['items']}
        self.assertEqual(mixed['a.bin']['assessment']['rating'], 1)
        self.assertEqual(mixed['b.bin']['assessment']['state'], 'skipped')
        self.assertEqual(mixed['c.bin']['assessment']['rating'], 5)
        parent = self.app.catalog.page(query='mixed')['items'][0]
        self.assertFalse(parent['can_prepare'])

    def test_neutral_unrated_and_skipped_are_distinct(self):
        self.advice(str(Path('cache') / 'a.bin'), 3, 'review')
        self.advice(str(Path('cache') / 'b.bin'), None, 'skip')
        self.advice(str(Path('cache') / 'c.bin'), None, 'no_advice')
        self.assertEqual(self.app.catalog.page(rating='neutral')['total'], 1)
        self.assertEqual(self.app.catalog.page(rating='skipped')['total'], 1)
        self.assertEqual(self.app.catalog.page(rating='no_advice')['total'], 1)
        self.assertEqual(self.app.catalog.page(rating='unrated')['total'], len(self.items) - 3)

    def test_rating_filter_keeps_ancestor_navigation_without_scanning(self):
        self.advice(str(Path('cache') / 'a.bin'), 4, 'review')
        with patch('os.scandir', side_effect=AssertionError('tree must not scan')):
            roots = self.app.catalog.page(view='tree', rating='high')
            self.assertEqual([i['id'] for i in roots['items']], [self.items['.']['id']])
            folders = self.app.catalog.page(view='tree', parent_id=self.items['.']['id'], rating='high')
            self.assertEqual([i['name'] for i in folders['items']], ['cache'])

    def test_new_assessment_invalidates_frozen_page_and_selection(self):
        before = self.app.catalog.page()
        self.advice('cache', 4, 'review', 'subtree')
        with self.assertRaises(AppError) as caught:
            self.app.catalog.page(expected=before['revision'])
        self.assertEqual(caught.exception.code, 'catalog_changed')
        item = self.items[str(Path('cache') / 'a.bin')]
        payload = dict(ids=[item['id']], snapshots={item['id']: item['signature']}, revision=before['revision'], action='delete')
        with self.assertRaises(AppError):
            self.app.post('/api/assessments/plan', payload)

    def test_selection_route_requires_exact_signatures_and_no_auto_execution(self):
        item = self.items[str(Path('cache') / 'a.bin')]
        payload = dict(ids=[item['id']], snapshots={item['id']: item['signature']}, revision=self.app.catalog.token(), action='delete')
        with patch.object(self.app.harness, 'prepare_assessment_plan', create=True, return_value={'task': {}, 'plan': {}, 'picked_ids': [item['id']]}) as prepare:
            result = self.app.post('/api/assessments/plan', payload)
            self.assertEqual(result['picked_ids'], [item['id']])
            prepare.assert_called_once()
            self.assertFalse(self.app.state.history())
            self.assertTrue(Path(item['path']).is_file())
            payload['snapshots'][item['id']] = 'changed'
            with self.assertRaises(AppError) as caught:
                self.app.post('/api/assessments/plan', payload)
            self.assertEqual(caught.exception.code, 'selection_changed')
        for change in ({'action': 'uninstall'}, {'ids': []}, {'revision': None}, {'extra': True}):
            with self.subTest(change=change), self.assertRaises(AppError):
                self.app.post('/api/assessments/plan', dict(payload, **change))

    def test_unknown_tree_node_and_filters_are_rejected(self):
        for options in ({'view': 'physical'}, {'view': 'tree', 'parent_id': 'missing'}, {'rating': 'six'}):
            with self.subTest(options=options), self.assertRaises(AppError):
                self.app.catalog.page(**options)

    def test_missing_intermediate_observation_is_navigation_only(self):
        leaf = dict(self.items[str(Path('cache') / 'a.bin')])
        data = self.root / 'sparse-data'
        app = Application(data)
        self.addCleanup(app.close)
        app.state.publish([leaf], 'sparse')
        with patch('os.scandir', side_effect=AssertionError('navigation must not scan')):
            roots = app.catalog.page(view='tree')
            self.assertEqual(roots['total'], 1)
            nav = roots['items'][0]
            self.assertTrue(nav['navigation_only'])
            self.assertFalse(nav['can_select'])
            self.assertFalse(nav['can_prepare'])
            self.assertIsNone(nav['signature'])
            self.assertIsNone(nav['assessment']['rating'])
            nested = app.catalog.page(view='tree', parent_id=nav['id'])
            self.assertTrue(nested['items'][0]['navigation_only'])

    def test_actual_host_plan_deduplicates_without_model_or_approval(self):
        self.advice('cache', 5, inheritance='subtree')
        parent, child = self.items['cache'], self.items[str(Path('cache') / 'a.bin')]
        with patch.object(self.app.assistant, 'agent_turn', side_effect=AssertionError('host plan must not call LLM')):
            result = self.app.post('/api/assessments/plan', dict(ids=[parent['id'], child['id']],
                snapshots={i['id']: i['signature'] for i in (parent, child)}, revision=self.app.catalog.token(), action='delete'))
        self.assertEqual(result['picked_ids'], [parent['id']])
        self.assertEqual(result['task']['status'], 'awaiting_review')
        self.assertEqual(result['plan']['items'][0]['assessment']['rating'], 5)
        self.assertFalse(result['plan']['items'][0]['default_selected'])
        self.assertFalse(self.app.state.history())
        self.assertTrue(Path(child['path']).is_file())


if __name__ == '__main__':
    unittest.main()
