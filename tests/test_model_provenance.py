import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant
from diskpilot.recommendations import build_recommendations
from diskpilot.server import Application
from diskpilot.simulation import AppError


def item(ident='file-1'):
    return dict(id=ident, name='fixture.bin', kind='file', path=r'E:\fixture\fixture.bin',
                risk='review', category='personal', actions=['delete'], details={}, size_bytes=10, simulated=False)


def suggestion(ident='file-1'):
    return dict(id=ident, recommend=True, suggested_action='delete', reason='请按留存需求决定。', impact='删除后需重新取得副本。')


def reply(content):
    return {'choices': [{'message': {'role': 'assistant', 'content': content}}]}


def provenance(model='actual-model', source='llm'):
    return dict(source=source, provider='custom' if source == 'llm' else None,
                model=model if source == 'llm' else None, protocol='chat_completions' if source == 'llm' else None,
                generated_at='2026-11-02T03:00:00+00:00')


class ModelProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def assistant(self, transport):
        value = ModelAssistant(self.root, transport=transport)
        value._windows = False
        value.save_settings(dict(provider='custom', base_url='http://127.0.0.1:12345/v1',
                                 model='request-model', protocol='chat_completions', api_key='fixture-private-token'))
        return value

    def assert_stamp(self, value, source):
        self.assertEqual(set(value), {'source', 'provider', 'model', 'protocol', 'generated_at'})
        self.assertEqual(value['source'], source)
        parsed = datetime.fromisoformat(value['generated_at'])
        self.assertEqual(parsed.utcoffset(), timezone.utc.utcoffset(parsed))
        self.assertNotIn('127.0.0.1', json.dumps(value))
        self.assertNotIn('fixture-private-token', json.dumps(value))

    def test_agent_turn_attribution_uses_request_snapshot_despite_inflight_settings_change(self):
        calls = []
        def peer(url, payload, headers, timeout):
            calls.append(copy.deepcopy(payload))
            value.save_settings({'model': 'newly-selected-model'})
            return dict(reply('只读说明。'), provenance=provenance('provider-invented-model'))
        value = self.assistant(peer)
        first = value.agent_turn([{'role': 'user', 'content': '查看用途'}], [])
        self.assert_stamp(first['_provenance'], 'llm')
        self.assertEqual(first['_provenance']['model'], 'request-model')
        original = copy.deepcopy(first['_provenance'])
        # A changed model must rebuild its public transcript, not replay the
        # previous connection's opaque provider continuation.
        public_first = {key: child for key, child in first.items() if key != '_continuation'}
        value.agent_turn([{'role': 'user', 'content': '查看用途'}, public_first], [])
        self.assertEqual(first['_provenance'], original)
        self.assertNotIn('_provenance', json.dumps(calls))
        self.assertNotIn('generated_at', json.dumps(calls))

    def test_legacy_analyze_and_structured_results_use_host_snapshot(self):
        for structured in (False, True):
            with self.subTest(structured=structured):
                def peer(*args):
                    value.save_settings({'model': 'current-settings-model'})
                    content = json.dumps({'recommendations': [suggestion()]}) if structured else '只读说明。'
                    return dict(reply(content), provenance=provenance('untrusted-response-label'))
                value = self.assistant(peer)
                result = value.recommend([item()]) if structured else value.analyze('查看用途', [item()])
                self.assertEqual(result['mode'], 'llm')
                self.assert_stamp(result['provenance'], 'llm')
                self.assertEqual(result['provenance']['model'], 'request-model')

    def test_invalid_structured_output_and_provider_failures_get_local_not_llm_attribution(self):
        for response in (reply(json.dumps({'recommendations': [suggestion()], 'provenance': provenance('spoofed')})),
                         ValueError('provider_auth')):
            def peer(*args):
                if isinstance(response, Exception):
                    raise response
                return copy.deepcopy(response)
            value = self.assistant(peer)
            result = value.recommend([item()])
            self.assertEqual(result['mode'], 'local')
            self.assert_stamp(result['provenance'], 'local')
            self.assertIsNone(result['provenance']['model'])
            self.assertIsNone(result['provenance']['provider'])
            self.assertIsNone(result['provenance']['protocol'])

    def test_each_advice_keeps_its_own_host_generation_and_extra_metadata_is_not_copied(self):
        class Stub:
            def public_settings(self):
                return {'model': 'wrong-current-model', 'protect_patterns': []}
            def recommend(self, _):
                return dict(mode='llm', recommendations=[suggestion()],
                            provenance=dict(provenance('actual-request-model'), base_url='private-endpoint', api_key='private-secret'))
        result = build_recommendations([item(), item('not-analyzed')], Stub(), use_model=True)
        model, local = [row['advice']['provenance'] for row in result['items']]
        self.assertEqual(model, provenance('actual-request-model'))
        self.assert_stamp(local, 'local')
        self.assertNotIn('wrong-current-model', json.dumps(result))
        self.assertNotIn('private-secret', json.dumps(result))
        self.assertNotIn('private-endpoint', json.dumps(result))

    def test_missing_or_malformed_historical_attribution_stays_unknown(self):
        for metadata in (None, dict(provenance(), generated_at='not-a-timestamp'), dict(provenance(), model='')):
            class Stub:
                def public_settings(self):
                    return {'model': 'current-must-not-fill-history', 'protect_patterns': []}
                def recommend(self, _):
                    return dict(mode='llm', recommendations=[suggestion()], provenance=metadata)
            result = build_recommendations([item()], Stub(), use_model=True)
            value = result['items'][0]['advice']['provenance']
            self.assertEqual(value, dict(source='unknown', provider=None, model=None, protocol=None, generated_at=None))
            self.assertNotIn('current-must-not-fill-history', json.dumps(result))


class RecommendationCacheProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Application(Path(self.temp.name))
        self.addCleanup(self.app.close)
        self.app.state.publish([item()], 'unit-fixture')
        self.app.assistant._windows = False
        self.app.post('/api/settings', dict(base_url='http://127.0.0.1:12345/v1', model='old-model'))

    def generate(self, model='old-model'):
        self.app.post('/api/settings', {'model': model})
        self.app.assistant._transport = lambda *args: reply(json.dumps({'recommendations': [suggestion()]}))
        return self.app.recommendations(use_model=True)

    def test_cached_response_and_failed_reanalysis_keep_original_source_and_time(self):
        original = self.generate()
        before = copy.deepcopy(original['items'][0]['advice']['provenance'])
        self.assertEqual(self.app.recommendations()['items'][0]['advice']['provenance'], before)
        self.app.assistant._transport = lambda *args: (_ for _ in ()).throw(ValueError('provider_auth'))
        retained = self.app.recommendations(use_model=True)
        self.assertEqual(retained['items'], original['items'])
        self.assertEqual(retained['analysis_notice']['status'], 'retained_previous')
        self.assertFalse(retained['analysis_notice']['new_advice_accepted'])
        self.assertEqual(retained['items'][0]['advice']['provenance'], before)
        self.assertNotIn('analysis_notice', self.app.recommendations())

    def test_successful_reanalysis_replaces_source_without_relabelling_prior_result(self):
        first = self.generate('old-model')
        old = copy.deepcopy(first)
        second = self.generate('new-model')
        self.assertEqual(second['items'][0]['advice']['provenance']['model'], 'new-model')
        self.assertEqual(self.app.recommendations()['items'][0]['advice']['provenance']['model'], 'new-model')
        self.assertEqual(first, old)

    def test_failed_reanalysis_never_reuses_cache_after_settings_or_catalog_change(self):
        self.generate()
        self.app.post('/api/settings', {'model': 'new-model'})
        self.app.assistant._transport = lambda *args: (_ for _ in ()).throw(ValueError('provider_auth'))
        changed = self.app.recommendations(use_model=True)
        self.assertEqual(changed['items'][0]['advice']['provenance']['source'], 'local')
        self.assertNotIn('analysis_notice', changed)
        self.generate()
        self.app.state.publish([item()], 'unit-fixture')
        self.app.assistant._transport = lambda *args: (_ for _ in ()).throw(ValueError('provider_auth'))
        changed = self.app.recommendations(use_model=True)
        self.assertEqual(changed['items'][0]['advice']['provenance']['source'], 'local')
        self.assertNotIn('analysis_notice', changed)

    def test_late_same_profile_analysis_cannot_overwrite_a_newer_accepted_result(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        first = dict(items=[dict(item(), advice=dict(origin='model', provenance=provenance('older-response')))], summary={'model_mode':'llm'})
        newest = dict(items=[dict(item(), advice=dict(origin='model', provenance=provenance('newest-response')))], summary={'model_mode':'llm'})
        def build(*args, **kwargs):
            if threading.current_thread().name == 'older-recommendation':
                entered.set()
                release.wait(2)
                return copy.deepcopy(first)
            return copy.deepcopy(newest)
        def older():
            try:
                self.app.recommendations(use_model=True)
            except AppError as exc:
                errors.append(exc.code)
        with patch('diskpilot.server.build_recommendations', side_effect=build):
            thread = threading.Thread(target=older, name='older-recommendation')
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.app.recommendations(use_model=True)
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ['stale_recommendations'])
        self.assertEqual(self.app.recommendations()['items'][0]['advice']['provenance']['model'], 'newest-response')

    def test_inflight_local_get_does_not_replace_an_accepted_model_cache(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        model = dict(items=[dict(item(), advice=dict(origin='model', provenance=provenance('accepted-model')))], summary={'model_mode':'llm'})
        local = dict(items=[dict(item(), advice=dict(origin='rule', provenance=provenance(source='local')))], summary={'model_mode':'not_requested'})
        errors = []
        def build(*args, **kwargs):
            if kwargs.get('use_model'):
                entered.set()
                release.wait(2)
                return copy.deepcopy(model)
            release.set()
            if not finished.wait(2):
                raise AssertionError('model fixture did not finish')
            return copy.deepcopy(local)
        def worker():
            try:
                self.app.recommendations(use_model=True)
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                finished.set()
        with patch('diskpilot.server.build_recommendations', side_effect=build):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                result = self.app.recommendations()
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result['items'][0]['advice']['provenance']['model'], 'accepted-model')
        self.assertEqual(self.app.recommendations()['items'][0]['advice']['origin'], 'model')


if __name__ == '__main__':
    unittest.main()
