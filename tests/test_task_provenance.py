import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest

from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness


def response(*commands):
    return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': f'call-{index}', 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}
        for index, (name, args) in enumerate(commands)]}}]}


class TaskProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'downloads'
        self.scope.mkdir()
        (self.scope / 'a.zip').write_bytes(b'disposable provenance fixture')
        (self.scope / 'b.zip').write_bytes(b'disposable provenance fixture')
        self.state = SimulationState(self.root / 'state')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(self.scope))['items'], 'fixture')
        self.steps, self.requests = [], []

        def transport(_url, payload, _headers, _timeout):
            self.requests.append(copy.deepcopy(payload))
            step = self.steps.pop(0)
            return step(payload) if callable(step) else step

        self.assistant = ModelAssistant(self.root / 'state', transport=transport)
        self.assistant.save_settings({'provider': 'custom', 'protocol': 'chat_completions',
            'base_url': 'http://127.0.0.1:12345/v1', 'model': 'model-a',
            'api_key': 'synthetic-provenance-secret'})
        self.harness = TaskHarness(self.state, self.assistant)
        self.item = next(row for row in self.state.view()['items'] if row['name'] == 'a.zip')
        self.task = self.harness.create_analysis({'ids': [self.item['id']]})

    def advice(self, **changes):
        value = dict(id=self.item['id'], conclusion='review', purpose='用户下载的测试副本。',
                     reason='是否保留需要用户选择。', impact='模拟不会改变文件。')
        value.update(changes)
        return value

    def proposal(self):
        return {'summary': '请查看这项操作和影响。', 'items': [dict(id=self.item['id'], action='delete',
            reason='是否保留需要用户选择。', impact='模拟不会改变文件。')]}

    def run_turn(self):
        self.harness.claim(self.task['id'])
        return self.harness.investigate(self.task['id'])

    def advice_turn(self):
        return response(('record_advice', {'items': [self.advice()]}),
                        ('present_findings', {'summary': '已保存本次建议。', 'needs_input': True}))

    def assert_llm(self, value, model, provider='custom'):
        self.assertEqual(set(value), {'source', 'provider', 'model', 'protocol', 'generated_at'})
        self.assertEqual(value['source'], 'llm')
        self.assertEqual(value['provider'], provider)
        self.assertEqual(value['model'], model)
        self.assertEqual(value['protocol'], 'chat_completions')
        self.assertEqual(datetime.fromisoformat(value['generated_at']).utcoffset().total_seconds(), 0)
        self.assertNotIn('synthetic-provenance-secret', json.dumps(value))

    def test_inflight_settings_change_does_not_relabel_advice_or_old_source_in_new_plan(self):
        def first(payload):
            self.assertEqual(payload['model'], 'model-a')
            errors = []
            def change():
                try:
                    self.assistant.save_settings({'provider': 'ollama', 'model': 'model-b', 'clear_key': True})
                except Exception as exc:
                    errors.append(type(exc).__name__)
            changed = threading.Thread(target=change)
            changed.start()
            changed.join(timeout=5)
            self.assertFalse(changed.is_alive())
            self.assertEqual(errors, [])
            return response(('record_advice', {'items': [self.advice()]}))

        self.steps = [first, response(('propose_plan', self.proposal()))]
        public = self.run_turn()
        advice_source = public['advice'][0]['provenance']
        self.assert_llm(advice_source, 'model-a')
        self.assert_llm(public['plan']['provenance'], 'model-b', 'ollama')
        entry = public['plan']['items'][0]
        self.assertEqual(entry['provenance'], public['plan']['provenance'])
        self.assertEqual(entry['advice_provenance'], advice_source)
        self.assertEqual([r['model'] for r in self.requests], ['model-a', 'model-b'])
        self.assistant.save_settings({'model': 'model-c'})
        recovered = TaskHarness(self.state, self.assistant)
        self.assertEqual(recovered.public(self.task['id'])['advice'][0]['provenance'], advice_source)
        self.assertEqual(recovered.advice_page(self.task['id'], ids=[self.item['id']])['items'][0]['provenance'], advice_source)
        self.assertEqual(recovered.public(self.task['id'])['plan']['items'][0]['advice_provenance'], advice_source)

    def test_one_response_has_one_source_for_advice_and_plan(self):
        self.steps = [response(('record_advice', {'items': [self.advice()]}), ('propose_plan', self.proposal()))]
        public = self.run_turn()
        source = public['advice'][0]['provenance']
        self.assert_llm(source, 'model-a')
        self.assertEqual(public['plan']['provenance'], source)
        self.assertEqual(public['plan']['items'][0]['provenance'], source)
        stored = self.state.db.execute('SELECT payload FROM tasks WHERE id=?', (self.task['id'],)).fetchone()[0]
        self.assertNotIn('"_provenance"', stored)
        self.assertNotIn('"base_url"', json.dumps(source))

    def test_successful_reanalysis_replaces_source_and_time_failure_keeps_previous(self):
        self.steps = [self.advice_turn()]
        old = self.run_turn()['advice'][0]
        self.assistant.save_settings({'model': 'model-b'})
        self.assertEqual(self.harness.public(self.task['id'])['advice'][0], old)

        def failed(_payload):
            raise ValueError('provider_unavailable')

        self.steps = [failed]
        self.assertEqual(self.run_turn()['status'], 'error')
        self.assertEqual(self.harness.advice_page(self.task['id'])['items'][0]['provenance'], old['provenance'])
        self.steps = [self.advice_turn()]
        new = self.run_turn()['advice'][0]
        self.assert_llm(new['provenance'], 'model-b')
        self.assertGreater(datetime.fromisoformat(new['provenance']['generated_at']), datetime.fromisoformat(old['provenance']['generated_at']))
        self.assertEqual(new['signature'], old['signature'])

    def test_invalid_batch_cannot_overwrite_existing_advice_or_accept_model_self_attribution(self):
        self.steps = [self.advice_turn()]
        old = self.run_turn()['advice'][0]
        forged = dict(source='llm', provider='forged', model='forged', protocol='forged', generated_at='2030-01-01T00:00:00Z')
        invalid = self.advice(provenance=forged)
        with self.assertRaises(AppError):
            self.harness.tool(self.task['id'], 'record_advice', {'items': [self.advice(reason='新理由。'), invalid]},
                              cancel=threading.Event(), provenance=forged)
        current = self.harness.public(self.task['id'])['advice'][0]
        self.assertEqual(current, old)
        with self.assertRaises(AppError):
            self.harness.tool(self.task['id'], 'propose_plan', dict(self.proposal(), provenance=forged), cancel=threading.Event())

    def test_response_cannot_supply_its_own_provenance(self):
        untrusted = self.advice_turn()
        untrusted['choices'][0]['message']['_provenance'] = dict(source='llm', provider='forged', model='forged')
        untrusted['_provenance'] = untrusted['choices'][0]['message']['_provenance']
        self.steps = [untrusted]
        source = self.run_turn()['advice'][0]['provenance']
        self.assert_llm(source, 'model-a')

    def test_legacy_advice_and_plan_remain_unknown_despite_current_settings_or_handoff(self):
        self.harness.tool(self.task['id'], 'record_advice', {'items': [self.advice()]}, cancel=threading.Event())
        self.harness.tool(self.task['id'], 'propose_plan', self.proposal(), cancel=threading.Event())
        task = self.harness._get(self.task['id'])
        task['advice'][self.item['id']].pop('provenance')
        task['plan'].pop('provenance')
        task['plan']['items'][0].pop('provenance')
        task['plan']['items'][0].pop('advice_provenance')
        self.harness._save(task)
        self.assistant.save_settings({'model': 'model-c'})
        public = self.harness.public(self.task['id'])
        unknown = {'source': 'unknown', 'provider': None, 'model': None, 'protocol': None, 'generated_at': None}
        self.assertEqual(public['advice'][0]['provenance'], unknown)
        self.assertEqual(public['plan']['provenance'], unknown)
        self.assertEqual(public['plan']['items'][0]['provenance'], unknown)
        self.assertEqual(self.harness.advice_page(self.task['id'])['items'][0]['provenance'], unknown)
        raw = self.harness._get(self.task['id'])
        self.assertNotIn('provenance', raw['advice'][self.item['id']])

    def test_direct_user_plan_is_local_and_preserves_related_advice_source(self):
        self.steps = [self.advice_turn()]
        advice_source = self.run_turn()['advice'][0]['provenance']
        self.assistant.save_settings({'model': 'model-b'})
        prepared = self.harness.direct_plan(self.task['id'], {'ids': [self.item['id']], 'action': 'delete',
            'snapshots': {self.item['id']: self.item['signature']}})
        plan = prepared['plan']
        self.assertEqual(plan['origin'], 'user_direct')
        self.assertEqual(plan['provenance']['source'], 'local')
        self.assertIsNone(plan['provenance']['model'])
        self.assertEqual(plan['items'][0]['advice_provenance'], advice_source)
        self.assertEqual(plan['items'][0]['provenance'], plan['provenance'])
        self.assertEqual(self.state.history(), [])
        self.assertTrue((self.scope / 'a.zip').exists())

    def test_paged_advice_beyond_preview_retains_exact_whitelisted_metadata(self):
        for index in range(60):
            (self.scope / f'extra-{index}.txt').write_text('disposable')
        self.state.publish(scan_directory(str(self.scope))['items'], 'fixture')
        task = self.harness.create_analysis({'kind': 'file'})
        rows = self.harness._items(self.harness._get(task['id']))
        host = dict(source='llm', provider='custom', model='saved-model', protocol='chat_completions',
                    generated_at=datetime.now(timezone.utc).isoformat(), base_url='https://must-not-persist.invalid', api_key='must-not-persist')
        for start in range(0, len(rows), 30):
            batch = [dict(self.advice(), id=row['id']) for row in rows[start:start + 30]]
            self.harness.tool(task['id'], 'record_advice', {'items': batch}, cancel=threading.Event(), provenance=host)
        public = self.harness.public(task['id'])
        self.assertEqual(len(public['advice']), 50)
        wanted = rows[-1]['id']
        page = self.harness.advice_page(task['id'], ids=[wanted])
        self.assertEqual(page['items'][0]['id'], wanted)
        self.assert_llm(page['items'][0]['provenance'], 'saved-model')
        self.assertNotIn('must-not-persist', json.dumps(page))


if __name__ == '__main__':
    unittest.main()
