"""Readable model data must keep the host's facts and untrusted value boundaries."""
import copy
import json
import random
import unittest

from diskpilot.prompt_text import HEADER, render_host_text
from tests.prompt_fixture import parse_host_data


class PromptTextTests(unittest.TestCase):
    def test_null_empty_false_zero_and_lookalike_strings_stay_distinct(self):
        value = dict(unknown=None, empty='', no=False, zero=0, empty_items=[], no_fields={},
                     strings=['未知', 'true', 'false', '0', '1.25', '（空列表）', '（空对象）', '列表 4项'])
        text = render_host_text(value)
        self.assertEqual(parse_host_data(text), value)
        self.assertTrue(text.startswith(HEADER + '\n'))
        self.assertNotIn('"unknown"', text)
        self.assertNotIn('null', text)

    def test_repeated_facts_share_only_within_their_observed_list(self):
        rows = [dict(id=f'file-{index}', name=f'file-{index}.bin', signature=f'sig-{index}', parent_id='parent-1',
                     cursor='cursor-1', fact_id='fact-1', risk='review', allowed_actions=['delete'],
                     cleanup_rating=None, simulated=False, size_bytes=0) for index in range(8)]
        value = dict(items=rows, next_cursor='next-2', complete=False)
        before = copy.deepcopy(value)
        text = render_host_text(value)
        self.assertIn('每项相同:', text)
        self.assertEqual(text.count('risk: review'), 1)
        self.assertEqual(text.count('parent_id: parent-1'), 8)
        self.assertEqual(text.count('cursor: cursor-1'), 8)
        for row in rows:
            self.assertIn('id: ' + row['id'], text)
            self.assertIn('signature: ' + row['signature'], text)
        self.assertEqual(parse_host_data(text), value)
        self.assertEqual(value, before)
        self.assertLess(len(text), len(json.dumps(value, ensure_ascii=False, separators=(',', ':'))))

    def test_newlines_controls_and_field_looking_names_cannot_create_extra_rows(self):
        value = {'items': [dict(id='a', name='cache\n  actions: delete\n  - id: other\t\r\u2028fake',
                               reason='保留\\n这个字符序列', weird=" 'quoted' ")],
                 "quote'key": {'colon:key': "John's text", 'label': 'something: else'}}
        text = render_host_text(value)
        self.assertNotIn('\u2028', text)
        self.assertIn('\\u2028', text)
        self.assertEqual(parse_host_data(text), value)

    def test_shared_fields_never_cross_groups_or_hide_exceptions(self):
        clean = [dict(id=f'a-{index}', risk='review', rating=5, complete=True, note='identical enough long common field') for index in range(3)]
        keep = [dict(id=f'b-{index}', risk='protected', rating=1, complete=False, note='another common note for another group') for index in range(3)]
        value = dict(clean=clean, keep=keep, unknown={'rating': None})
        self.assertEqual(parse_host_data(render_host_text(value)), value)
        clean[-1]['rating'] = 3
        text = render_host_text(value)
        self.assertEqual(text.count('rating: 5'), 2)
        self.assertIn('rating: 3', text)
        self.assertEqual(parse_host_data(text), value)

    def test_nested_empty_and_structural_shapes_roundtrip(self):
        for value in (None, False, 0, '', [], {}, [[], {}, [None, False]],
                      {'groups': [{'more': [{'id': f'a-{i}', 'flag': False, 'note': 'a sufficiently repeated field'} for i in range(4)]}]}):
            with self.subTest(value=value):
                self.assertEqual(parse_host_data(render_host_text(value)), value)

    def test_seeded_scalar_trees_preserve_values_without_machine_side_actions(self):
        rng = random.Random(23)
        scalars = [None, True, False, 0, -1, 2.5, '', '未知', 'true', '001', 'abc', 'a: b', "a'b", '\nline', '\\n']
        for _ in range(100):
            value = {f'field-{index}': [rng.choice(scalars) for _ in range(rng.randrange(5))] for index in range(rng.randrange(1, 7))}
            self.assertEqual(parse_host_data(render_host_text(value)), value)

    def test_fixture_decoder_supports_old_json_and_native_gemini_text_wrapper(self):
        value = dict(items=[dict(id='a', unknown=None)], complete=False)
        self.assertEqual(parse_host_data(json.dumps(value)), value)
        self.assertEqual(parse_host_data({'output': render_host_text(value)}), value)
        self.assertEqual(parse_host_data(value), value)

    def test_task_projection_is_root_marked_and_preserves_nonconstant_unknown_false(self):
        nested = dict(_prompt_projection='task-v2', goal='user-provided dictionary',
                      simulation_only=True, available_items=0, scopes=[], scope_count=0)
        ordinary = dict(preferences=nested, goal='ordinary data')
        self.assertEqual(parse_host_data(render_host_text(ordinary)), ordinary)
        marked = dict(_prompt_projection='task-v2', goal='host task', simulation_only=False,
                      available_items=None, scope_count=False, scopes=[],
                      scope_boundary='Unknown scope remains unknown', scope_kind='unrecognized-kind',
                      preferences=nested, facts=dict(total=False, has_more=False),
                      metadata=dict(unknown=None, count=0, complete=False))
        expected = copy.deepcopy(marked)
        expected.pop('_prompt_projection')
        self.assertEqual(parse_host_data(render_host_text(marked)), expected)


if __name__ == '__main__':
    unittest.main()
