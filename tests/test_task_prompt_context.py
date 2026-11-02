"""On-demand contexts use owned metadata and local fixtures, never a user model."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant
from diskpilot.prompt_text import render_host_text
from diskpilot.scanner import scan_directory
from diskpilot.simulation import SimulationState
from diskpilot.tasks import TaskHarness
from tests.prompt_fixture import parse_host_data


def previous_context(harness, task):
    """Reproduce the pre-v0.16 projection using the same facts/metadata values.

    The renderer's generic scalar/list grammar is unchanged. Restoring the old
    log, fact-preview and four-object projections isolates context compaction
    from unrelated collector/schema changes. No provider token count is claimed.
    """
    context = harness._context(task)
    context.pop('_prompt_projection', None)
    context.pop('scope_boundary', None)
    if task['observations']:
        context['observations'] = copy.deepcopy(task['observations'][-3:])
    facts = harness.facts_page(task['id'], limit=5, detail=False)
    context['facts'] = {field: facts[field] for field in ('total', 'has_more')}
    if facts['items']:
        context['facts']['items'] = facts['items']
    if task.get('analysis'):
        by_id = {row['id']: row for row in harness._items(task)}
        targets = [by_id[ident] for ident in task['target_ids'] if ident in by_id]
        if targets:
            context['initial_objects'] = [harness.assistant.task_metadata(row) for row in targets[:4]]
            context['initial_objects_omitted'] = max(0, len(targets) - 4)
    return context


class TaskPromptContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='panxu-context-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scope = self.root / 'selected'
        self.scope.mkdir()
        for index in range(6):
            (self.scope / f'owned-{index}.txt').write_text('disposable fixture', encoding='utf-8')
        self.state = SimulationState(self.root / 'data')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(self.scope))['items'], 'owned-fixture')
        self.assistant = ModelAssistant(self.root / 'data', transport=lambda *_args: self.fail('no model request'))
        self.assistant._windows = False
        self.assistant.save_settings(dict(base_url='http://127.0.0.1:12345/v1', model='fixture-a'))
        self.harness = TaskHarness(self.state, self.assistant)
        self.files = [row for row in self.state.snapshot([])['items'] if row['kind'] == 'file']
        self.key = self.harness.create_analysis({'ids': [row['id'] for row in self.files]})['id']

    def context(self):
        return self.harness._context(self.harness._get(self.key))

    def wire(self):
        return render_host_text(self.context())

    def facts(self):
        refs = []
        for state in ('Running', 'Stopped'):
            refs.append(self.harness._record_fact(self.key, 'collect_apps', dict(
                items=[dict(id='same-service', name='Owned service', kind='service',
                            program_facts=dict(state=state, last_used=None, count=0, enabled=False))],
                coverage=dict(complete=False, unknown_count=None), warnings=['仅有界历史观察'])))
        return refs

    def add_log(self):
        refs = self.facts()
        task = self.harness._get(self.key)
        read = self.harness.facts_page(self.key, ids=refs, detail=True)
        task['observations'] += [dict(tool='collect_apps', at='2026-01-01T00:00:00+00:00',
                                     result=dict(fact_id=refs[-1], **read['items'][0]['result'])),
                                 dict(tool='record_advice', at='2026-01-02T00:00:00+00:00',
                                      result=dict(recorded_count=6, total_advice_count=6)),
                                 dict(tool='read_facts', at='2026-01-03T00:00:00+00:00', result=read)]
        self.harness._save(task)
        return refs

    def test_first_packet_is_scope_only_and_known_zero_is_explained(self):
        self.key = self.harness.create(preset='background')['id']
        self.harness.feedback(self.key, '保留同步功能。')
        before = self.context()
        text = self.wire()
        sent = parse_host_data(text)
        for field in ('scope_kind', 'scopes', 'scope_count', 'simulation_only', 'preset',
                      'available_items', '_prompt_projection', 'initial_objects'):
            self.assertNotIn(field, sent)
        self.assertIn('启动项、服务与进程', sent['scope_boundary'])
        self.assertIn('不代表位置为空', sent['inventory_note'])
        self.assertIn('暂无', sent['facts'])
        self.assertEqual(sent['user_feedback'], [{'content': '保留同步功能。'}])
        self.assertEqual(self.context(), before)  # Rendering does not mutate tasks.

    def test_context_never_samples_or_adopts_inventory_for_small_or_large_tasks(self):
        for index in range(6, 60):
            (self.scope / f'owned-{index}.txt').touch()
        self.state.publish(scan_directory(str(self.scope))['items'], 'owned-fixture')
        self.files = [row for row in self.state.snapshot([])['items'] if row['kind'] == 'file']
        for count in (1, 6, 60):
            self.key = self.harness.create_analysis({'ids': [row['id'] for row in self.files[:count]]})['id']
            with patch('diskpilot.tasks.scan_directory', side_effect=AssertionError('no scan')), \
                 patch('diskpilot.tasks._checked_root', side_effect=AssertionError('no collection')), \
                 patch('os.scandir', side_effect=AssertionError('no enumeration')):
                context = self.context()
            self.assertNotIn('initial_objects', context)
            self.assertEqual(context['analysis']['target_count'], count)
            listed = self.harness.tool(self.key, 'list_items', {}, cancel=threading.Event())
            self.assertEqual(listed['total'], count)
            self.assertEqual(len(listed['items']), min(count, 20))
            self.assertIn('timestamps', listed['items'][0])
            self.assertIn('user_protected', listed['items'][0])
        new = self.harness.create('同一位置的新任务。', str(self.scope))
        self.assertEqual(self.harness._get(new['id'])['known_ids'], [])
        self.assertNotIn('saved_observations', self.harness._get(new['id']))

    def test_compact_fact_index_has_no_echoes_and_conflicting_facts_remain_readable(self):
        refs = self.add_log()
        before = self.harness.facts_page(self.key, ids=refs, detail=True)
        context = parse_host_data(self.wire())
        self.assertNotIn('observations', context)
        self.assertEqual({row['id'] for row in context['facts']['items']}, set(refs))
        self.assertTrue(all(set(row) == {'id', 'tool'} for row in context['facts']['items']))
        self.assertIn('不代表当前状态', context['facts']['note'])
        reread = self.harness.tool(self.key, 'read_facts', {'ids': refs}, cancel=threading.Event())
        sent = parse_host_data(render_host_text(reread))
        self.assertEqual([row['result']['items'][0]['program_facts']['state'] for row in sent['items']],
                         ['Stopped', 'Running'])
        for row in sent['items']:
            self.assertNotIn('summary', row)
            self.assertNotIn('object_ids', row)
            self.assertNotIn('_scope', row['result'])
            self.assertEqual(row['scope'], before['items'][0]['scope'])
            self.assertIn('at', row)
            self.assertIsNone(row['result']['coverage']['unknown_count'])
            self.assertFalse(row['result']['coverage']['complete'])
        self.assertEqual(self.harness.facts_page(self.key, ids=refs, detail=True), before)

    def test_reconstruction_and_model_change_keep_choices_references_and_coverage(self):
        refs = self.add_log()
        self.harness.feedback(self.key, '只有我最后勾选才处理。', [self.files[0]['id']])
        task = self.harness._get(self.key)
        task['feedback'].append(dict(role='user', content='离线副本保留。', answer=dict(
            option_id='offline', text='保留离线副本', skipped=False, execution_authorized=False)))
        task['connection_changed'] = True
        task['previous_connection'] = dict(model='fixture-a', connection_id='old-private-fingerprint')
        task['last_connection'] = dict(model='fixture-b', connection_id='new-private-fingerprint')
        task['handoff'] = dict(summary='已有两份状态不同的历史观察，不能据此断言当前状态。',
            next_steps=['按需核对历史来源'], unresolved=['实际是否需要常驻尚未知'],
            updated_at='not-a-useful-input', reason='model_changed', origin='model', model=task['last_connection'])
        self.harness._save(task)
        restored = TaskHarness(self.state, self.assistant)
        sent = parse_host_data(render_host_text(restored._context(restored._get(self.key))))
        self.assertTrue(sent['connection_changed'])
        self.assertEqual(sent['connection']['model'], 'fixture-b')
        self.assertNotIn('connection_id', sent['previous_connection'])
        self.assertNotIn('updated_at', sent['handoff'])
        self.assertIn('不同', sent['handoff']['summary'])
        self.assertEqual({row['id'] for row in sent['facts']['items']}, set(refs))
        self.assertEqual(sent['user_choices'][0]['choice'], 'keep')
        self.assertFalse(sent['user_feedback'][-1]['answer']['execution_authorized'])
        self.assertFalse(sent['user_feedback'][-1]['answer']['skipped'])
        self.assertGreater(sent['analysis']['remaining_count'], 0)
        self.assertFalse(self.state.history())

    def test_legacy_unindexed_observations_keep_unknown_zero_false_and_partial(self):
        task = self.harness._get(self.key)
        facts = dict(fact_id='fact-no-longer-retained', complete=False, count=0, unknown=None, observations=[],
                     program_facts=dict(usage='unknown', last_used=None, enabled=False))
        task['observations'].append(dict(tool='legacy_custom_observation', at='original-time', result=facts))
        task['scans']['existing-scan'] = dict(scan_id='existing-scan', status='partial',
            coverage=dict(complete=False, pending_metadata=0, unseen_count=None), aggregate=dict(logical_bytes=0))
        self.harness._save(task)
        sent = parse_host_data(self.wire())
        self.assertEqual(sent['observations'][0]['result'], facts)
        self.assertEqual(sent['scans'][0]['coverage'], task['scans']['existing-scan']['coverage'])

    def test_read_fact_cache_survives_when_original_row_is_no_longer_retained(self):
        task = self.harness._get(self.key)
        cached = dict(items=[dict(id='fact-expired', tool='collect_apps', at='original-time',
            result=dict(state='Stopped', unknown=None, count=0, complete=False))], total=1, has_more=False)
        task['observations'].append(dict(tool='read_facts', result=cached))
        self.harness._save(task)
        self.assertEqual(parse_host_data(self.wire())['observations'][0]['result'], cached)

    def test_representative_context_characters_fall_without_http_or_token_claims(self):
        self.add_log()
        task = self.harness._get(self.key)
        old = render_host_text(previous_context(self.harness, task))
        new = render_host_text(self.harness._context(task))
        self.assertLess(len(new), len(old) * .45)
        self.assertEqual(set(self.harness._get(self.key)['target_ids']), {row['id'] for row in self.files})


if __name__ == '__main__':
    unittest.main()
