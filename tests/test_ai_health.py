import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from helpers import TempDirTest
from panxu.ai_health import AIHealth, NOTICE_SECONDS
from panxu.ai_client import AIError
from panxu.settings import Settings
from panxu.api import Backend, Bridge
from panxu import sandbox


class HealthTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.settings = Settings(self.tmp)
        self.calls = []
        self.now = 1000
        self.health = AIHealth(self.settings, lambda ai, key: self.calls.append((ai, key)) or {'model': ai['model'], 'reply': {'ok': True}},
                               clock=lambda: self.now)

    def key(self):
        self.settings.set_secret('ai_key:deepseek', 'test-secret')

    def finish(self):
        end = time.time() + 5
        while self.health._running and time.time() < end:
            time.sleep(.01)
        self.assertFalse(self.health._running)
        return self.health.status()

    def test_missing_config_never_probes_and_local_model_does_not_need_a_key(self):
        state = self.health.check()
        self.assertEqual(state['state'], 'unconfigured')
        self.assertIn('API Key', state['message'])
        self.assertEqual(self.calls, [])
        self.settings.update({'ai': {'preset': 'ollama', 'base_url': 'http://127.0.0.1:11434/v1', 'model': 'local'}})
        self.health.check()
        self.assertEqual(self.finish()['state'], 'ready')
        self.assertEqual(len(self.calls), 1)

    def test_model_key_and_endpoint_changes_invalidate_success_but_unrelated_options_do_not(self):
        self.key()
        self.health.check()
        self.assertEqual(self.finish()['state'], 'ready')
        self.settings.update({'ai': {'max_requests': 16}, 'theme': 'dark'})
        self.assertEqual(self.health.status()['state'], 'ready')
        for changes in ({'model': 'another'}, {'base_url': 'https://test.invalid/v1'}, {'json_mode': False}):
            self.settings.update({'ai': changes})
            self.assertEqual(self.health.status()['state'], 'untested')
            self.health.test()
        self.settings.set_secret('ai_key:deepseek', 'other-key')
        self.assertEqual(self.health.status()['state'], 'untested')

    def test_probe_is_async_coalesced_and_stale_result_does_not_certify_new_config(self):
        self.key()
        entered, release = threading.Event(), threading.Event()
        def probe(ai, key):
            self.calls.append(ai['model'])
            entered.set()
            release.wait(3)
        self.health.probe = probe
        self.health.check()
        self.assertTrue(entered.wait(2))
        for _ in range(5):
            self.assertEqual(self.health.check(force=True)['state'], 'checking')
        self.settings.update({'ai': {'model': 'new'}})
        self.assertEqual(self.health.status()['state'], 'untested')
        release.set()
        self.assertEqual(self.finish()['state'], 'untested')
        self.assertEqual(len(self.calls), 1)

    def test_new_probe_queued_during_old_config_is_checked_without_parallel_requests(self):
        self.key()
        entered, release = threading.Event(), threading.Event()
        def probe(ai, key):
            self.calls.append(ai['model'])
            entered.set()
            release.wait(3)
        self.health.probe = probe
        self.health.check()
        self.assertTrue(entered.wait(2))
        self.settings.update({'ai': {'model': 'new'}})
        self.health.check()
        release.set()
        self.assertEqual(self.finish()['model'], 'new')
        self.assertEqual(self.calls[-1], 'new')
        self.assertEqual(len(self.calls), 2)

    def test_reminder_ack_persists_for_one_day_and_never_exposes_key(self):
        state = self.health.status()
        self.assertTrue(state['notice_due'])
        self.health.acknowledge(state['signature'])
        self.assertFalse(AIHealth(Settings(self.tmp), self.health.probe, clock=lambda: self.now).status()['notice_due'])
        self.now += NOTICE_SECONDS + 1
        self.assertTrue(self.health.status()['notice_due'])
        self.key()
        exc = AIError('server echoed test-secret')
        exc.status = 401
        self.health.probe = lambda *args: (_ for _ in ()).throw(exc)
        self.health.check()
        state = self.finish()
        self.assertTrue(state['notice_due'])
        self.assertEqual(state['state'], 'error')
        self.assertNotIn('test-secret', str(state))
        self.assertIn('无效', state['message'])
        self.health.acknowledge(state['signature'])
        self.assertFalse(self.health.status()['notice_due'])
        exc.status = 402
        self.health.check(force=True)
        self.assertTrue(self.finish()['notice_due'], 'a different failure needs fresh guidance')
        self.assertEqual(self.health.error_text(TimeoutError())[0], '模型连接超时')

    def test_actual_model_requests_update_health_and_stale_client_is_ignored(self):
        self.key()
        client = SimpleNamespace(complete=lambda *a: 'ok', model='m', usage=SimpleNamespace(requests=0))
        observed = self.health.observe(client)
        self.assertEqual(observed.complete('a'), 'ok')
        self.assertEqual(self.health.status()['state'], 'ready')
        client.complete = lambda *a: (_ for _ in ()).throw(AIError('offline'))
        with self.assertRaises(AIError): observed.complete('a')
        self.assertEqual(self.health.status()['state'], 'error')
        self.settings.update({'ai': {'model': 'different'}})
        client.complete = lambda *a: 'ok'
        observed.complete('a')
        self.assertEqual(self.health.status()['state'], 'untested')


class HealthApiTest(TempDirTest):
    def test_probe_with_real_transport_for_openai_and_anthropic_against_fake_server(self):
        from http.server import HTTPServer
        from test_ai import FakeHandler, chat
        server = HTTPServer(('127.0.0.1', 0), FakeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        backend = Backend(os.path.join(self.tmp, 'data'), env=self.posix_env())
        try:
            for protocol in ('openai', 'anthropic'):
                backend.settings.update({'ai': {'preset': 'custom', 'model': 'fake', 'protocol': protocol,
                    'base_url': f'http://127.0.0.1:{server.server_port}/v1'}})
                backend.settings.set_secret('ai_key:custom', 'fake-key')
                FakeHandler.seen = []
                FakeHandler.replies = [chat('{"ok":true}')] if protocol == 'openai' else [
                    (200, {'content': [{'type': 'text', 'text': '{"ok":true}'}], 'usage': {}})]
                self.assertEqual(backend.m_ai_test()['reply'], {'ok': True})
                self.assertEqual(backend.ai_health.status()['state'], 'ready')
                req = FakeHandler.seen[0]
                self.assertEqual(req['path'], '/v1/chat/completions' if protocol == 'openai' else '/v1/messages')
                self.assertEqual(req['body']['max_tokens'], 64)
                self.assertEqual(req['body']['messages'][-1]['content'], '请回复 {"ok": true}')
                self.assertNotIn('USERPROFILE', str(req['body']))
        finally:
            server.shutdown()
            thread.join(2)
            server.server_close()

    def test_sandbox_probe_cannot_reach_network_and_never_blocks_file_jobs(self):
        sb = sandbox.Sandbox(os.path.join(self.tmp, 'sb'))
        backend = Backend(os.path.join(self.tmp, 'data'), sandbox=sb)
        self.addCleanup(sandbox.deactivate)
        self.addCleanup(backend.jobs.stop)
        backend.settings.set_secret('ai_key:deepseek', 'fake')
        bridge = Bridge(backend)
        job = backend.jobs.start('checkup', lambda j: j.cancel.wait(5))
        with patch('panxu.ai_client.client_from_settings', side_effect=AssertionError('no network')):
            self.assertTrue(bridge.call('ai_health_check')['ok'])
            self.assertEqual(bridge.call('ai_test')['data']['reply'], {'ok': True})
        self.assertEqual(bridge.call('activity')['data']['jobs'][0]['id'], job)
        self.assertEqual(bridge.call('ai_health')['data']['state'], 'ready')

    def test_real_probe_uses_only_fixed_message_and_short_budget(self):
        backend = Backend(os.path.join(self.tmp, 'data'), env=self.posix_env())
        seen = []
        fake = SimpleNamespace(model='m', max_tokens=999, usage=SimpleNamespace(as_dict=lambda: {'requests': 1}),
                               complete=lambda *a, **kw: seen.append((a, kw)) or '{"ok": true}')
        with patch('panxu.ai_client.client_from_settings', return_value=fake) as factory:
            backend._probe_ai({'timeout': 120}, 'fake')
        self.assertEqual(factory.call_args.args[0]['timeout'], 12)
        self.assertEqual(fake.max_tokens, 64)
        self.assertEqual(seen[0][0], ('你是连接测试助手。只输出 JSON。', '请回复 {"ok": true}'))
        fake.complete = lambda *a, **k: '{"ok": false}'
        with patch('panxu.ai_client.client_from_settings', return_value=fake), self.assertRaises(AIError):
            backend._probe_ai({'timeout': 120}, 'fake')
