import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness, tool_specs


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'downloads'
        self.scope.mkdir()
        (self.scope / 'a.txt').write_text('disposable fixture')
        (self.scope / 'b.txt').write_text('disposable fixture')
        (self.scope / 'nested').mkdir()
        (self.scope / 'nested' / 'c.txt').write_text('disposable fixture')
        self.state = SimulationState(self.root / 'data')
        self.addCleanup(self.state.close)
        self.assistant = ModelAssistant(self.root / 'data')
        self.harness = TaskHarness(self.state, self.assistant)
        self.publish()
        self.parent = self.item(self.scope)
        self.child = self.item(self.scope / 'a.txt')
        self.sibling = self.item(self.scope / 'b.txt')
        self.nested = self.item(self.scope / 'nested')
        self.leaf = self.item(self.scope / 'nested' / 'c.txt')
        self.host = dict(source='llm', provider='custom', model='fixture-a', protocol='chat_completions', generated_at='2026-11-02T00:00:00+00:00')

    def publish(self):
        self.state.publish(scan_directory(str(self.scope))['items'], 'fixture')

    def item(self, path):
        return next(item for item in self.state.view()['items'] if item['path'] == str(path))

    def task(self, *items):
        return self.harness.create_analysis({'ids': [item['id'] for item in items]})

    def record(self, task, item, *, value=5, conclusion='review', inheritance='none', include_rating=True):
        advice = dict(id=item['id'], conclusion=conclusion, purpose='测试资料。', reason='按用途判断清理倾向。', impact='只记录模拟，不改动文件。', inheritance=inheritance)
        if include_rating:
            advice['cleanup_rating'] = value
        return self.harness.tool(task['id'], 'record_advice', {'items': [advice]}, cancel=threading.Event(), provenance=self.host)

    def resolved(self, task=None):
        return self.harness.assessments.resolve(self.state.snapshot(self.harness.assistant.public_settings()['protect_patterns'])['items'], task=task)

    def propose(self, task, *items):
        return self.harness.tool(task['id'], 'propose_plan', {'summary': '先审阅再决定。', 'items': [dict(id=item['id'], action='delete', reason='按用户需要判断。', impact='模拟不会改动文件。') for item in items]}, cancel=threading.Event(), provenance=self.host)

    def positive_parent(self):
        task = self.task(self.parent)
        self.harness.tool(task['id'], 'inspect_directory', {'id': self.parent['id']}, cancel=threading.Event())
        self.record(task, self.parent, inheritance='subtree')
        return task

    def test_sparse_subtree_inheritance_preserves_real_source_without_actions(self):
        task = self.positive_parent()
        direct = self.harness.public(task['id'])['advice'][0]
        values = self.resolved()
        self.assertEqual(values[self.parent['id']]['state'], 'explicit')
        inherited = values[self.leaf['id']]
        self.assertEqual(inherited['state'], 'inherited')
        self.assertEqual(inherited['rating'], 5)
        self.assertEqual(inherited['provenance'], direct['provenance'])
        self.assertEqual(inherited['inherited_from']['item_id'], self.parent['id'])
        self.assertNotIn('actions', inherited)
        self.assertNotIn('default_selected', inherited)
        self.assertEqual(self.harness.public(task['id'])['advice_total'], 1)
        self.assertEqual(self.state.history(), [])

    def test_explicit_child_wins_even_when_parent_is_regenerated_later(self):
        parent = self.positive_parent()
        child_task = self.task(self.child)
        self.record(child_task, self.child, value=1, conclusion='keep')
        newer = self.task(self.parent)
        self.record(newer, self.parent, inheritance='subtree')
        values = self.resolved()
        self.assertEqual(values[self.child['id']]['state'], 'explicit')
        self.assertEqual(values[self.child['id']]['rating'], 1)
        self.assertEqual(values[self.sibling['id']]['state'], 'inherited')
        self.assertIn('explicit_keep', values[self.parent['id']]['blocked_reasons'])
        with self.assertRaises(AppError) as caught:
            self.propose(newer, self.parent)
        self.assertEqual(caught.exception.code, 'directory_conflict')
        self.assertEqual(self.harness.public(parent['id'])['advice'][0]['cleanup_rating'], 5)

    def test_skip_no_advice_unscored_and_neutral_block_whole_directory(self):
        for conclusion, value, expected in [('skip', None, 'skipped'), ('no_advice', None, 'no_advice'), ('review', None, 'explicit'), ('review', 3, 'explicit')]:
            with self.subTest(conclusion=conclusion, value=value):
                parent = self.positive_parent()
                child_task = self.task(self.child)
                self.record(child_task, self.child, value=value, conclusion=conclusion)
                value_dto = self.resolved()[self.child['id']]
                self.assertEqual(value_dto['state'], expected)
                self.assertEqual(value_dto['rating'], value)
                with self.assertRaises(AppError):
                    self.propose(parent, self.parent)

    def test_missing_legacy_score_is_null_not_three(self):
        task = self.task(self.child)
        self.record(task, self.child, include_rating=False)
        self.assertIsNone(self.harness.public(task['id'])['advice'][0]['cleanup_rating'])
        self.assertIsNone(self.resolved()[self.child['id']]['rating'])
        self.assertEqual(self.resolved()[self.sibling['id']]['state'], 'unanalysed')

    def test_non_subtree_directory_is_a_boundary_to_more_general_ancestor(self):
        self.positive_parent()
        narrow = self.task(self.nested)
        self.record(narrow, self.nested, value=4)
        values = self.resolved()
        self.assertEqual(values[self.nested['id']]['rating'], 4)
        self.assertEqual(values[self.leaf['id']]['state'], 'unanalysed')
        self.assertIsNone(values[self.leaf['id']]['rating'])
        self.assertIn('ancestor_barrier', values[self.leaf['id']]['blocked_reasons'])
        self.assertIn('self_only_directory', values[self.parent['id']]['blocked_reasons'])

    def test_changed_directory_identity_or_snapshot_stops_inheritance(self):
        self.positive_parent()
        old = self.resolved()[self.sibling['id']]['provenance']
        (self.scope / 'new.txt').write_text('new disposable file')
        self.publish()
        current = self.resolved()
        self.assertTrue(current[self.parent['id']]['stale'])
        self.assertIsNone(current[self.sibling['id']]['rating'])
        self.assertEqual(current[self.sibling['id']]['state'], 'unanalysed')
        self.assertEqual(old['model'], 'fixture-a')

    def test_stale_child_is_a_barrier_not_fallback_to_parent_rating(self):
        self.positive_parent()
        child_task = self.task(self.child)
        self.record(child_task, self.child, value=4)
        (self.scope / 'a.txt').write_text('changed disposable content and size')
        self.publish()
        values = self.resolved()
        self.assertTrue(values[self.child['id']]['stale'])
        self.assertIsNone(values[self.child['id']]['rating'])
        self.assertIn('stale_or_unknown_descendant', values[self.parent['id']]['blocked_reasons'])

    def test_newer_intent_prevents_late_older_reply_and_failed_new_task_keeps_old_record(self):
        old = self.task(self.child)
        self.record(old, self.child, value=1, conclusion='keep')
        original = copy.deepcopy(self.resolved()[self.child['id']])
        new = self.task(self.child)
        self.record(old, self.child, value=5)
        self.assertEqual(self.resolved()[self.child['id']], original)
        self.assertEqual(self.harness.public(old['id'])['advice'][0]['cleanup_rating'], 5)
        self.record(new, self.child, value=4)
        self.assertEqual(self.resolved()[self.child['id']]['rating'], 4)
        self.assertNotEqual(self.resolved()[self.child['id']]['source_ref'], original['source_ref'])

    def test_new_directory_intent_also_blocks_old_descendant_response(self):
        old = self.task(self.child)
        self.record(old, self.child, value=1, conclusion='keep')
        original = copy.deepcopy(self.resolved()[self.child['id']])
        self.task(self.parent)
        self.record(old, self.child, value=5)
        self.assertEqual(self.resolved()[self.child['id']], original)

    def test_index_survives_reopen_without_relabeling_old_task(self):
        task = self.positive_parent()
        before = self.resolved()
        original = self.harness.public(task['id'])['advice']
        self.assistant.save_settings({'model': 'a-different-current-model'})
        harness = TaskHarness(self.state, self.assistant)
        self.assertEqual(harness.assessments.resolve(self.state.view()['items']), before)
        self.assertEqual(harness.public(task['id'])['advice'], original)

    def test_current_task_keep_is_not_silently_globalized(self):
        original = self.positive_parent()
        self.record(original, self.child, value=4)
        self.harness.feedback(original['id'], keep_ids=[self.child['id']])
        kept = self.harness._get(original['id'])
        self.assertIn('user_kept', self.resolved(kept)[self.child['id']]['blocked_reasons'])
        self.assertIn('user_kept_descendant', self.resolved(kept)[self.parent['id']]['blocked_reasons'])
        fresh = self.harness._get(self.task(self.parent)['id'])
        self.assertNotIn('user_kept_descendant', self.resolved(fresh)[self.parent['id']]['blocked_reasons'])

    def test_global_protection_beats_even_high_inherited_rating(self):
        self.positive_parent()
        self.assistant.save_settings({'protect_patterns': ['*a.txt']})
        values = self.resolved()
        self.assertIn('protected', values[self.child['id']]['blocked_reasons'])
        self.assertIn('protected_descendant', values[self.parent['id']]['blocked_reasons'])

    def test_ratings_and_inheritance_are_strict_but_personal_review_can_be_high(self):
        task = self.task(self.parent, self.child)
        for kwargs in [dict(value=True), dict(value=3.0), dict(value=0), dict(value=6), dict(value=5, conclusion='keep'),
                       dict(value=1, conclusion='recommend'), dict(value=4, conclusion='skip'), dict(value=3, conclusion='no_advice'),
                       dict(value=4, inheritance='subtree')]:
            with self.subTest(kwargs=kwargs), self.assertRaises(AppError):
                self.record(task, self.child, **kwargs)
        self.record(task, self.child, value=5, conclusion='review')
        self.assertEqual(self.resolved()[self.child['id']]['rating'], 5)
        self.assertEqual(len(tool_specs()), 25)

    def test_approval_rechecks_global_exception_after_proposal(self):
        task = self.positive_parent()
        self.propose(task, self.parent)
        child = self.task(self.child)
        self.record(child, self.child, value=None, conclusion='skip')
        with self.assertRaises(AppError) as caught:
            self.harness.validate_approval(task['id'], 1, [self.parent['id']])
        self.assertEqual(caught.exception.code, 'directory_conflict')
        self.assertEqual(self.state.history(), [])

    def test_runtime_rechecks_exception_after_approval_without_touching_files(self):
        task = self.positive_parent()
        self.propose(task, self.parent)
        self.harness.validate_approval(task['id'], 1, [self.parent['id']])
        child = self.task(self.child)
        self.record(child, self.child, value=1, conclusion='keep')
        self.harness.claim(task['id'], execute=True)
        public = self.harness.investigate(task['id'], execute=True)
        self.assertEqual(public['last_result']['succeeded'], 0)
        self.assertEqual(public['last_result']['items'][0]['status'], 'failed')
        self.assertEqual(self.state.history(), [])
        self.assertTrue((self.scope / 'a.txt').exists())

    def test_partial_coverage_is_not_fixed_by_high_score(self):
        task = self.positive_parent()
        rows = copy.deepcopy(self.state.view()['items'])
        next(item for item in rows if item['id'] == self.parent['id'])['details']['subtree_complete'] = False
        self.state.publish(rows, 'fixture')
        self.assertIn('coverage_incomplete', self.resolved()[self.parent['id']]['blocked_reasons'])
        with self.assertRaises(AppError):
            self.propose(task, self.parent)

    def test_host_plan_is_reviewable_exact_user_selection_without_model_or_approval(self):
        self.positive_parent()
        with patch.object(self.assistant, 'agent_turn', side_effect=AssertionError('No model permitted')):
            result = self.harness.prepare_assessment_plan([self.parent['id'], self.child['id']], expected_revision=self.harness.assessments.revision())
        self.assertEqual(result['picked_ids'], [self.parent['id']])
        self.assertEqual(result['selection_origin'], 'user')
        self.assertEqual(result['task']['status'], 'awaiting_review')
        self.assertEqual(result['plan']['origin'], 'user_direct')
        self.assertFalse(result['plan']['items'][0]['default_selected'])
        self.assertEqual(self.state.history(), [])
        self.assertIsNone(result['task']['last_result'])

    def test_host_selection_rejects_stale_revision_unknown_protected_and_bad_ids(self):
        task = self.positive_parent()
        revision = self.harness.assessments.revision()
        self.record(task, self.child, value=4)
        with self.assertRaises(AppError):
            self.harness.prepare_assessment_plan([self.child['id']], expected_revision=revision)
        for ids in ([], ['missing'], [self.child['id'], self.child['id']], [{}], ['x'] * 501):
            with self.subTest(ids=str(ids)[:80]), self.assertRaises(AppError):
                self.harness.prepare_assessment_plan(ids)
        self.assistant.save_settings({'protect_patterns': ['*a.txt']})
        with self.assertRaises(AppError):
            self.harness.prepare_assessment_plan([self.child['id']])

    def test_host_plan_supports_500_exact_leaf_targets_without_preselection_by_model(self):
        for index in range(500):
            (self.scope / f'batch-{index:03d}.txt').write_text('disposable')
        self.publish()
        ids = [item['id'] for item in self.state.view()['items'] if item['name'].startswith('batch-')]
        result = self.harness.prepare_assessment_plan(ids)
        self.assertEqual(len(result['plan']['items']), 500)
        self.assertEqual(set(result['picked_ids']), set(ids))
        self.assertTrue(all(not item['default_selected'] for item in result['plan']['items']))
        self.assertEqual(self.state.history(), [])

    def test_failed_batch_preserves_task_and_global_index_atomically(self):
        task = self.task(self.child, self.sibling)
        self.record(task, self.child, value=4)
        before, revision = self.harness.public(task['id']), self.harness.assessments.revision()
        good = dict(id=self.child['id'], conclusion='review', cleanup_rating=5, purpose='测试', reason='更新', impact='无实际变更')
        bad = dict(good, id=self.sibling['id'], cleanup_rating=True)
        with self.assertRaises(AppError):
            self.harness.tool(task['id'], 'record_advice', {'items': [good, bad]}, cancel=threading.Event())
        self.assertEqual(self.harness.assessments.revision(), revision)
        self.assertEqual(self.harness.public(task['id'])['advice'], before['advice'])
        self.assertEqual(self.resolved()[self.child['id']]['rating'], 4)

    def test_older_response_revision_in_same_task_cannot_replace_new_record(self):
        task = self.task(self.child)
        self.record(task, self.child, value=1, conclusion='keep')
        old = self.harness._get(task['id'])
        old_advice = copy.deepcopy(old['advice'])
        self.record(task, self.child, value=4)
        latest = copy.deepcopy(self.resolved()[self.child['id']])
        with self.state.lock, self.state.db:
            self.harness.assessments.publish(old, old_advice)
        self.assertEqual(self.resolved()[self.child['id']], latest)

    def test_read_projection_does_not_scan_or_expand_file_only_task_authority(self):
        self.positive_parent()
        task = self.task(self.child)
        with patch('os.scandir', side_effect=AssertionError('Projection must not enumerate')):
            self.assertEqual(self.resolved()[self.sibling['id']]['state'], 'inherited')
        with self.assertRaises(AppError):
            self.propose(task, self.sibling)

    def test_legacy_backfill_keeps_unknown_barrier_after_catalog_child_was_omitted(self):
        task = self.task(self.parent)
        self.harness.tool(task['id'], 'inspect_directory', {'id': self.parent['id']}, cancel=threading.Event())
        self.record(task, self.child, value=None)
        raw = self.harness._get(task['id'])
        for field in ('path', 'kind', 'cleanup_rating', 'advice_id', 'record_revision'):
            raw['advice'][self.child['id']].pop(field, None)
        self.harness._save(raw)
        self.state.publish([self.parent], 'fixture')
        with self.state.lock, self.state.db:
            self.state.db.execute('DELETE FROM catalog WHERE id=?', (self.child['id'],))
            self.state.revision += 1
            for table in ('assessment_records', 'assessment_intents', 'assessment_scopes', 'assessment_generations'):
                self.state.db.execute('DELETE FROM ' + table)
            self.state.db.execute('UPDATE assessment_state SET revision=0')
            self.state.db.execute("DELETE FROM meta WHERE key='assessment_index_v1'")
        restored = TaskHarness(self.state, self.assistant)
        values = restored.assessments.resolve(self.state.view()['items'])
        self.assertIn('legacy_unknown_descendant', values[self.parent['id']]['blocked_reasons'])
        self.assertIsNone(restored.public(task['id'])['advice'][0]['cleanup_rating'])

    def test_legacy_backfill_uses_creation_order_instead_of_late_task_save_time(self):
        old = self.task(self.child)
        self.record(old, self.child, value=1, conclusion='keep')
        new = self.task(self.child)
        self.record(new, self.child, value=4)
        # INSERT OR REPLACE gives the old task a newer SQLite rowid; it must
        # not become the latest analysis generation during migration.
        self.harness._save(self.harness._get(old['id']))
        with self.state.lock, self.state.db:
            for table in ('assessment_records', 'assessment_intents', 'assessment_scopes', 'assessment_generations'):
                self.state.db.execute('DELETE FROM ' + table)
            self.state.db.execute('UPDATE assessment_state SET revision=0')
            self.state.db.execute("DELETE FROM meta WHERE key='assessment_index_v1'")
        restored = TaskHarness(self.state, self.assistant)
        value = restored.assessments.resolve(self.state.view()['items'])[self.child['id']]
        self.assertEqual(value['rating'], 4)
        self.assertEqual(value['source_ref']['task_id'], new['id'])

    def test_program_inventory_at_same_path_is_not_a_directory_identity(self):
        self.positive_parent()
        self.state.publish([dict(id='installed-fixture', kind='software', name='测试程序', path=str(self.scope),
            signature='registration-signature', risk='review', actions=['uninstall'], details={}, size_bytes=0)], 'installed-fixture')
        self.assertEqual(self.resolved()[self.child['id']]['state'], 'inherited')
        self.assertEqual(self.resolved()[self.child['id']]['rating'], 5)

    def test_explicit_skip_counts_as_skipped_not_independently_analysed_or_scored(self):
        task = self.harness.create_analysis({'ids': [self.parent['id']], 'depth': 'detailed'})
        self.record(task, self.parent, value=None, conclusion='skip')
        coverage = self.harness.public(task['id'])['analysis']
        self.assertEqual(coverage['advised_count'], 0)
        self.assertEqual(coverage['skipped_count'], 1)
        self.assertEqual(coverage['directories_checked'], 0)
        self.assertEqual(coverage['directories_skipped'], 1)
        self.assertIsNone(self.resolved()[self.parent['id']]['rating'])

    def test_pruned_created_event_and_late_save_do_not_promote_old_generation(self):
        old = self.task(self.child)
        self.record(old, self.child, value=5)
        new = self.task(self.child)
        self.record(new, self.child, value=1, conclusion='keep')
        for _ in range(205):
            self.harness._event(self.harness._get(old['id']), 'tool', '只读测试观察')
        self.harness._save(self.harness._get(old['id']))
        with self.state.lock, self.state.db:
            for table in ('assessment_records', 'assessment_intents', 'assessment_scopes', 'assessment_generations'):
                self.state.db.execute('DELETE FROM ' + table)
            self.state.db.execute('UPDATE assessment_state SET revision=0')
            self.state.db.execute("DELETE FROM meta WHERE key='assessment_index_v1'")
        restored = TaskHarness(self.state, self.assistant)
        value = restored.assessments.resolve(self.state.view()['items'])[self.child['id']]
        self.assertEqual(value['rating'], 1)
        self.assertEqual(value['source_ref']['task_id'], new['id'])

    def test_unprovable_legacy_order_stays_unknown_until_new_analysis(self):
        old = self.task(self.child)
        self.record(old, self.child, value=5)
        new = self.task(self.child)
        self.record(new, self.child, value=1, conclusion='keep')
        for task in (old, new):
            raw = self.harness._get(task['id'])
            raw.pop('analysis_generation', None)
            raw.pop('created_at', None)
            # Set up pre-index legacy JSON without accidentally allocating a
            # replacement host generation through the normal save method.
            with self.state.lock, self.state.db:
                self.state.db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(raw), task['id']))
                self.state.db.execute("DELETE FROM task_events WHERE task_id=? AND kind='created'", (task['id'],))
        with self.state.lock, self.state.db:
            for table in ('assessment_records', 'assessment_intents', 'assessment_scopes', 'assessment_generations'):
                self.state.db.execute('DELETE FROM ' + table)
            self.state.db.execute('UPDATE assessment_state SET revision=0')
            self.state.db.execute("DELETE FROM meta WHERE key='assessment_index_v1'")
        self.harness = TaskHarness(self.state, self.assistant)
        value = self.resolved()[self.child['id']]
        self.assertIsNone(value['rating'])
        self.assertEqual(value['state'], 'no_advice')
        self.assertEqual(value['provenance']['source'], 'unknown')
        self.record(old, self.child, value=5)
        self.assertIsNone(self.resolved()[self.child['id']]['rating'])
        fresh = self.task(self.child)
        self.record(fresh, self.child, value=4)
        self.assertEqual(self.resolved()[self.child['id']]['rating'], 4)

    def test_keep_published_inside_runtime_before_hook_is_caught_at_apply(self):
        task = self.positive_parent()
        self.propose(task, self.parent)
        self.harness.validate_approval(task['id'], 1, [self.parent['id']])
        def before(entry, phase):
            if phase == 'before':
                child = self.task(self.child)
                self.record(child, self.child, value=1, conclusion='keep')
            return False
        self.harness.runtime.fault = before
        self.harness.claim(task['id'], execute=True)
        public = self.harness.investigate(task['id'], execute=True)
        self.assertEqual(public['last_result']['succeeded'], 0)
        self.assertEqual(self.state.history(), [])

    def test_protection_changed_inside_runtime_before_hook_blocks_leaf_apply(self):
        task = self.task(self.child)
        self.propose(task, self.child)
        self.harness.validate_approval(task['id'], 1, [self.child['id']])
        def before(entry, phase):
            if phase == 'before':
                self.assistant.save_settings({'protect_patterns': ['*a.txt']})
            return False
        self.harness.runtime.fault = before
        self.harness.claim(task['id'], execute=True)
        public = self.harness.investigate(task['id'], execute=True)
        self.assertEqual(public['last_result']['succeeded'], 0)
        self.assertEqual(self.state.history(), [])
        self.assertTrue((self.scope / 'a.txt').exists())


    def test_impact_rating_is_independent_and_durable_without_changing_eligibility(self):
        task = self.task(self.child)
        baseline = None
        for score in range(1, 6):
            row = dict(id=self.child['id'], conclusion='review', cleanup_rating=4,
                       impact_rating=score, purpose='资料副本', reason='按实际用途判断', impact='操作后需要重新取得副本')
            self.harness.tool(task['id'], 'record_advice', {'items': [row]}, cancel=threading.Event(), provenance=self.host)
            value = self.resolved()[self.child['id']]
            self.assertEqual(value['impact_rating'], score)
            decision = (value['rating'], value['state'], value['blocked_reasons'], value.get('eligible_direct'))
            baseline = decision if baseline is None else baseline
            self.assertEqual(decision, baseline)
            reopened = TaskHarness(self.state, self.assistant)
            self.assertEqual(reopened.public(task['id'])['advice'][0]['impact_rating'], score)

    def test_invalid_impact_ratings_reject_whole_advice_batch(self):
        task = self.task(self.child, self.sibling)
        base = dict(conclusion='review', cleanup_rating=4, purpose='资料', reason='用途已看', impact='影响待核对')
        revision = self.harness.assessments.revision()
        for invalid in (True, False, 0, 6, 1.5, '4', [], {}):
            with self.subTest(value=invalid), self.assertRaises(AppError):
                self.harness.tool(task['id'], 'record_advice', {'items': [
                    dict(base, id=self.child['id'], impact_rating=1),
                    dict(base, id=self.sibling['id'], impact_rating=invalid)]}, cancel=threading.Event())
            self.assertEqual(self.harness.public(task['id'])['advice_total'], 0)
            self.assertEqual(self.harness.assessments.revision(), revision)

    def test_impact_unknown_legacy_inheritance_and_stale_are_not_fabricated(self):
        task = self.task(self.parent)
        self.harness.tool(task['id'], 'inspect_directory', {'id': self.parent['id']}, cancel=threading.Event())
        self.record(task, self.parent, value=5, inheritance='subtree')
        self.assertIsNone(self.resolved()[self.child['id']]['impact_rating'])
        row = dict(id=self.parent['id'], conclusion='review', cleanup_rating=5, impact_rating=2,
                   inheritance='subtree', purpose='缓存', reason='可以重建', impact='下次使用需重新生成')
        self.harness.tool(task['id'], 'record_advice', {'items': [row]}, cancel=threading.Event())
        inherited = self.resolved()[self.child['id']]
        self.assertEqual((inherited['state'], inherited['impact_rating']), ('inherited', 2))
        changed = copy.deepcopy(self.parent)
        changed['signature'] = 'new-identity'
        stale = self.harness.assessments.resolve([changed])[changed['id']]
        self.assertTrue(stale['stale'])
        self.assertIsNone(stale['impact_rating'])
        self.assertEqual(self.harness.public(task['id'])['advice'][0]['impact_rating'], 2)

    def test_frozen_plan_keeps_impact_score_and_legacy_plans_stay_unknown(self):
        task = self.task(self.child)
        row = dict(id=self.child['id'], action='delete', reason='用户选择', impact='会失去副本', impact_rating=4)
        self.harness.tool(task['id'], 'propose_plan', {'summary': '待审阅', 'items': [row]}, cancel=threading.Event())
        self.assertEqual(self.harness.public(task['id'])['plan']['items'][0]['impact_rating'], 4)
        row.pop('impact_rating')
        self.harness.tool(task['id'], 'propose_plan', {'summary': '仍待审阅', 'items': [row]}, cancel=threading.Event())
        self.assertIsNone(self.harness.public(task['id'])['plan']['items'][0]['impact_rating'])
        row['impact_rating'] = True
        before = self.harness.public(task['id'])['plan']
        with self.assertRaises(AppError):
            self.harness.tool(task['id'], 'propose_plan', {'summary': '不能接受', 'items': [row]}, cancel=threading.Event())
        self.assertEqual(self.harness.public(task['id'])['plan'], before)

    def test_impact_rating_schema_is_optional_independent_ordinal(self):
        specs = {entry['function']['name']: entry['function']['parameters'] for entry in tool_specs()}
        for name in ('record_advice', 'propose_plan'):
            item = specs[name]['properties']['items']['items']
            self.assertEqual(item['properties']['impact_rating'], {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 5})
            self.assertNotIn('impact_rating', item['required'])

    def test_user_selected_plan_preserves_same_source_impact_text_and_rating(self):
        task = self.task(self.child)
        self.harness.tool(task['id'], 'record_advice', {'items': [dict(id=self.child['id'],
            conclusion='review', cleanup_rating=4, impact_rating=2,
            purpose='副本', reason='来源可以重新取得', impact='需要重新下载这份副本')]}, cancel=threading.Event())
        result = self.harness.prepare_assessment_plan([self.child['id']], source_task_id=task['id'])
        entry = result['plan']['items'][0]
        self.assertEqual((entry['impact'], entry['impact_rating']), ('需要重新下载这份副本', 2))
        self.assertEqual(entry['assessment']['impact_rating'], 2)
        self.assertEqual(self.harness.public(task['id'])['advice_total'], 1)
        self.assertIsNone(self.harness.public(task['id'])['last_result'])


if __name__ == '__main__':
    unittest.main()
