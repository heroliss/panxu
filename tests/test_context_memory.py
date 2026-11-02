"""Working evidence is bounded durable data, not a fabricated task conclusion."""
import copy
import json
import unittest

from diskpilot.context_memory import build_working_evidence, model_visible_bytes
from diskpilot.prompt_text import render_host_text


def fact(ident='fact-owned', tool='inspect_items', *, at='2026-11-02T10:00:00+00:00', rows=None, result=None):
    scope = dict(task_id='owned-task', root_ids=['owned-root'], target_count=0, depth='auto')
    if result is None:
        result = dict(items=rows if rows is not None else [dict(id='owned-object', name='Example app',
                      kind='startup', available=False, count=0, last_used=None)], _scope=copy.deepcopy(scope))
    return dict(id=ident, tool=tool, at=at, source='tool_observation', scope=scope,
                freshness='historical_observation', requires_recheck_for_action=True,
                object_ids=['owned-object'], summary=json.dumps(result, ensure_ascii=False)[:240], result=result)


def size(value):
    return len(render_host_text(value).encode('utf-8'))


class WorkingEvidenceTests(unittest.TestCase):
    def test_actual_names_ids_and_decision_fields_survive_with_provenance(self):
        source = fact()
        before = copy.deepcopy(source)
        value = build_working_evidence([source])
        self.assertEqual(value['object_count'], 1)
        entry = value['items'][0]
        self.assertEqual(entry['fact_id'], source['id'])
        self.assertEqual(entry['result']['items'], source['result']['items'])
        self.assertEqual(entry['scope'], source['scope'])
        self.assertEqual(entry['at'], source['at'])
        self.assertEqual(entry['source'], 'tool_observation')
        self.assertEqual(entry['freshness'], 'historical_observation')
        self.assertTrue(entry['requires_recheck_for_action'])
        self.assertNotIn('summary', entry)
        self.assertNotIn('_scope', entry['result'])
        self.assertEqual(value['omitted_count'], 0)
        self.assertFalse(value['partial'])
        self.assertEqual(source, before)

    def test_unknown_zero_false_and_non_array_items_are_not_pruned(self):
        for items in (None, 0, False, {}):
            with self.subTest(items=items):
                source = fact(result=dict(items=items, unknown=None, count=0, ready=False))
                entry = build_working_evidence([source])['items'][0]
                self.assertEqual(entry['result'], source['result'])
                self.assertEqual(type(entry['result']['items']), type(items))

    def test_just_read_old_fact_takes_priority_over_newer_metadata(self):
        sources = [fact('fact-new', at='2026-11-02T12:00:00+00:00'),
                   fact('fact-preferred', tool='cache_experience', at='2020-01-01T00:00:00+00:00')]
        value = build_working_evidence(sources, preferred_ids=('fact-preferred',), max_records=1)
        self.assertEqual(value['items'][0]['fact_id'], 'fact-preferred')
        self.assertEqual(value['omitted_count'], 1)
        self.assertTrue(value['partial'])

    def test_object_observations_precede_bookkeeping_then_newest_first(self):
        sources = [fact('fact-old-items', at='2026-11-01T10:00:00+00:00'),
                   fact('fact-new-summary', tool='checkpoint_task', at='2026-11-03T10:00:00+00:00'),
                   fact('fact-new-items', tool='read_scan', at='2026-11-02T10:00:00+00:00')]
        value = build_working_evidence(sources)
        self.assertEqual([row['fact_id'] for row in value['items']],
                         ['fact-new-items', 'fact-old-items', 'fact-new-summary'])

    def test_rows_are_trimmed_whole_with_counts_and_original_pagination(self):
        rows = [dict(id=f'owned-{index}', name=f'App {index}', purpose='缓存用途' * 80,
                     available=False, count=0, last_used=None) for index in range(20)]
        source = fact(rows=rows)
        source['result'].update(total=656, has_more=True, next_cursor='opaque-next', warnings=['Only one page.'])
        value = build_working_evidence([source], budget_bytes=2800)
        self.assertLessEqual(size(value), 2800)
        entry = value['items'][0]
        retained = entry['result']['items']
        self.assertTrue(0 < len(retained) < len(rows))
        self.assertEqual(retained, rows[:len(retained)])
        self.assertEqual(entry['projection'], dict(shown=len(retained), total=20, omitted=20-len(retained),
                         partial=True, scope='this_observation'))
        self.assertEqual(entry['result']['total'], 656)
        self.assertEqual(entry['result']['next_cursor'], 'opaque-next')
        self.assertTrue(entry['result']['has_more'])
        self.assertEqual(entry['result']['warnings'], ['Only one page.'])
        self.assertTrue(value['partial'])

    def test_source_partial_is_not_mistaken_for_complete_when_all_page_rows_fit(self):
        for status in (dict(has_more=True), dict(complete=False), dict(status='partial'),
                       dict(summary={'status': 'incomplete'})):
            with self.subTest(status=status):
                source = fact()
                source['result'].update(status)
                value = build_working_evidence([source])
                self.assertTrue(value['partial'])
                self.assertFalse(value['items'][0]['projection']['partial'])

    def test_shared_object_row_budget_is_not_restarted_for_each_fact(self):
        rows = [dict(id=f'owned-{index}', name=f'Item {index}') for index in range(10)]
        value = build_working_evidence([fact('fact-a', rows=rows), fact('fact-b', rows=rows)], max_objects=3)
        self.assertEqual(value['object_count'], 3)
        self.assertEqual(len(value['items'][0]['result']['items']), 3)
        self.assertNotIn('result', value['items'][1])
        self.assertEqual(value['omitted_count'], 1)

    def test_oversized_row_keeps_complete_readable_fact_reference_not_broken_content(self):
        source = fact(rows=[dict(id='owned-large', purpose='不得截断这条内容' * 10000)])
        value = build_working_evidence([source], budget_bytes=1200)
        self.assertLessEqual(size(value), 1200)
        self.assertEqual(len(value['items']), 1)
        entry = value['items'][0]
        self.assertEqual(entry['fact_id'], 'fact-owned')
        self.assertEqual(entry['tool'], 'inspect_items')
        self.assertNotIn('result', entry)
        self.assertIn('read_facts', entry['reason'])
        self.assertTrue(entry['result_omitted'])
        self.assertEqual(value['omitted_count'], 1)
        self.assertEqual(value['object_count'], 0)

    def test_unusable_large_scope_does_not_force_an_over_budget_reference(self):
        source = fact(rows=[dict(id='owned-large', purpose='Huge' * 10000)])
        source['scope']['root_ids'] = ['opaque-root-' + str(index) for index in range(1000)]
        value = build_working_evidence([source], budget_bytes=1200)
        self.assertLessEqual(size(value), 1200)
        entry = value['items'][0]
        self.assertTrue(entry['scope_omitted'])
        self.assertNotIn('scope', entry)
        self.assertNotIn('result', entry)

    def test_truncated_json_preview_does_not_become_reconstructed_evidence(self):
        source = fact(result=dict(truncated=True, original_bytes=800000,
                                  preview='{"items":[{"id":"not-complete', summary='Only a preview.'))
        value = build_working_evidence([source])
        entry = value['items'][0]
        self.assertNotIn('result', entry)
        self.assertNotIn('not-complete', render_host_text(value))
        self.assertIn('截断预览', entry['reason'])

    def test_conflicting_source_scopes_are_preserved_without_merging_authority(self):
        source = fact()
        source['result']['_scope']['root_ids'] = ['different-observed-scope']
        entry = build_working_evidence([source])['items'][0]
        self.assertEqual(entry['scope']['root_ids'], ['owned-root'])
        self.assertEqual(entry['result_scope']['root_ids'], ['different-observed-scope'])
        self.assertNotIn('_scope', entry['result'])

    def test_empty_observation_remains_distinct_from_missing_observation(self):
        empty = build_working_evidence([fact(rows=[])])
        self.assertEqual(empty['items'][0]['result']['items'], [])
        self.assertEqual(empty['omitted_count'], 0)
        missing = fact()
        missing.pop('result')
        value = build_working_evidence([missing])
        self.assertNotIn('result', value['items'][0])
        self.assertEqual(value['omitted_count'], 1)

    def test_no_records_can_be_requested_without_inventing_an_empty_inventory(self):
        value = build_working_evidence([fact()], max_records=0)
        self.assertEqual(value['items'], [])
        self.assertEqual(value['omitted_count'], 1)
        self.assertTrue(value['partial'])
        self.assertEqual(value['read_more'], 'read_facts')

    def test_invalid_budgets_are_controlled_and_extreme_provenance_is_omitted_atomically(self):
        for kwargs in (dict(budget_bytes=True), dict(budget_bytes=1), dict(max_objects=-1),
                       dict(max_records=21), dict(preferred_ids='fact-owned')):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_working_evidence([fact()], **kwargs)
        source = fact('fact-' + 'a' * 5000)
        value = build_working_evidence([source], budget_bytes=500)
        self.assertEqual(value['items'], [])
        self.assertEqual(value['omitted_count'], 1)
        self.assertLessEqual(size(value), 500)

    def test_covered_observation_rows_are_referenced_without_losing_source_or_progress(self):
        row = dict(id='owned-app', name='Owned app', observed_at='2026-10-01T00:00:00+00:00',
                   signature='same-entity', usage=dict(count=0, last_launch=None), allowed=False,
                   evidence=['One observed registration'])
        rich = fact('fact-rich', rows=[dict(row, purpose='Additional factual detail')])
        skim = fact('fact-skim', at='2026-11-01T10:00:00+00:00', rows=[row])
        before = copy.deepcopy([rich, skim])
        value = build_working_evidence([rich, skim])
        self.assertEqual(value['object_count'], 1)
        source = value['items'][1]
        self.assertEqual(source['at'], skim['at'])
        self.assertEqual(source['scope'], skim['scope'])
        self.assertEqual(source['result']['items'], [])
        self.assertEqual(source['row_references'], [dict(item_id='owned-app', fact_id='fact-rich', observed_at=row['observed_at'])])
        self.assertEqual(source['projection'], dict(shown=0, total=1, omitted=1, partial=True,
            scope='this_observation', deduplicated=1, unrepresented=0))
        self.assertEqual(value['omitted_count'], 0)
        self.assertTrue(value['partial'])
        self.assertEqual([rich, skim], before)

    def test_different_versions_times_evidence_and_unknown_values_are_never_merged(self):
        row = dict(id='owned-app', observed_at='2026-10-01T00:00:00+00:00', signature='version-1',
                   evidence=['registration'], active=False)
        variants = [dict(row, observed_at='2026-10-02T00:00:00+00:00'), dict(row, signature='version-2'),
                    dict(row, signature=None), dict(row, evidence=['registration', 'additional observation']),
                    dict(row, active=0), dict(row, active=None), dict(row, extra_evidence='New, unshared evidence')]
        for different in variants:
            with self.subTest(different=different):
                value = build_working_evidence([fact('fact-original', rows=[row]),
                    fact('fact-different', at='2026-11-01T10:00:00+00:00', rows=[different])])
                self.assertEqual(value['object_count'], 2)
                self.assertNotIn('row_references', value['items'][1])
                self.assertEqual(value['items'][1]['result']['items'], [different])

    def test_missing_observation_time_never_proves_two_rows_are_the_same_observation(self):
        for stamp in (None, ''):
            with self.subTest(stamp=stamp):
                row = dict(id='owned-app', name='Owned app', observed_at=stamp)
                value = build_working_evidence([fact('fact-a', rows=[row]), fact('fact-b', rows=[row])])
                self.assertEqual(value['object_count'], 2)
                self.assertNotIn('row_references', value['items'][1])

    def test_duplicate_rows_do_not_consume_slots_for_new_objects_in_the_next_fact(self):
        common = [dict(id=f'owned-{index}', observed_at='2026-10-01T00:00:00+00:00', name=f'Owned {index}') for index in range(2)]
        newer = [*common, dict(id='owned-new', observed_at='2026-10-01T00:00:00+00:00', name='New visible object')]
        value = build_working_evidence([fact('fact-rich', rows=common), fact('fact-other', rows=newer)], max_objects=3)
        self.assertEqual(value['object_count'], 3)
        entry = value['items'][1]
        self.assertEqual([row['id'] for row in entry['result']['items']], ['owned-new'])
        self.assertEqual(entry['projection']['deduplicated'], 2)
        self.assertEqual(entry['projection']['unrepresented'], 0)

    def test_deduplicated_references_still_obey_the_measured_working_budget(self):
        row = dict(id='owned-app', name='Owned app', purpose='Repeated useful evidence ' * 80,
                   observed_at='2026-10-01T00:00:00+00:00')
        sources = [fact('fact-a', rows=[row]), fact('fact-b', rows=[row])]
        for budget in (1000, 2500, 5000):
            with self.subTest(budget=budget):
                value = build_working_evidence(sources, budget_bytes=budget)
                self.assertLessEqual(size(value), budget)
                for entry in value['items']:
                    for reference in entry.get('row_references', []):
                        target = next(record for record in value['items'] if record['fact_id'] == reference['fact_id'])
                        self.assertTrue(any(item['id'] == reference['item_id'] for item in target['result']['items']))


class VisibleMessageEstimateTests(unittest.TestCase):
    def test_private_continuation_and_local_bookkeeping_are_not_counted_twice(self):
        message = dict(role='assistant', content='An unchanged public reply.', tool_calls=[dict(id='call-a',
                       type='function', function=dict(name='sample', arguments='{"items":[]}'))])
        internal = dict(message, _continuation={'private': 'opaque duplicate ' * 100000},
                        _usage={'token_count': 9999}, _provenance={'model': 'local bookkeeping'})
        self.assertEqual(model_visible_bytes([message]), model_visible_bytes([internal]))

    def test_host_json_and_tool_json_share_the_actual_readable_projection(self):
        host = dict(_prompt_projection='task-v2', goal='Owned goal', simulation_only=True,
                    available_items=0, scope_kind='filesystem', scopes=[], scope_count=0,
                    scope_boundary='Only the selected root.')
        result = dict(items=[dict(id='owned', available=False, count=0, size=None)])
        source = [dict(role='user', content=json.dumps(host)), dict(role='tool', tool_call_id='call-a',
                   content=json.dumps(result))]
        expected = [dict(role='user', content=render_host_text(host)), dict(role='tool', tool_call_id='call-a',
                    content=render_host_text(result))]
        self.assertEqual(model_visible_bytes(source), model_visible_bytes(expected))

    def test_ordinary_public_prose_and_json_keep_the_original_whitespace(self):
        for role in ('assistant', 'user', 'system'):
            for text in ('A paragraph.\n\n  Keep indentation.', '{ "ordinary": "public JSON", "zero": 0 }'):
                source = [dict(role=role, content=text)]
                expected = len(json.dumps(source, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
                self.assertEqual(model_visible_bytes(source), expected)

    def test_opaque_ids_and_tool_arguments_contribute_to_the_estimate(self):
        small = dict(role='assistant', content=None, tool_calls=[dict(id='call-a', type='function',
                     function=dict(name='sample', arguments='{"id":"short"}'))])
        bigger = copy.deepcopy(small)
        bigger['tool_calls'][0]['function']['arguments'] = '{"id":"longer-opaque-id"}'
        self.assertGreater(model_visible_bytes([bigger]), model_visible_bytes([small]))


if __name__ == '__main__':
    unittest.main()
