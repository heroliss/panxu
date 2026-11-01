import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from diskpilot.llm import ModelAssistant, _http_transport
from diskpilot import providers


TOOLS = [{'type': 'function', 'function': {'name': 'inspect_items', 'description': 'Inspect known IDs',
          'parameters': {'type': 'object', 'properties': {'ids': {'type': 'array', 'items': {'type': 'string'}}},
                         'required': ['ids'], 'additionalProperties': False}}}]


class ProtocolFixture:
    """An independent native-format peer, never an external service."""
    def __init__(self, protocol):
        self.protocol, self.requests = protocol, []

    def __call__(self, url, payload, headers, timeout):
        self.requests.append(copy.deepcopy(dict(url=url, payload=payload, headers=headers, timeout=timeout)))
        first = len(self.requests) == 1
        p = self.protocol
        if first:
            if p == 'chat_completions':
                nonce = payload['tools'][0]['function']['parameters']['properties']['nonce']['enum'][0]
                return {'choices': [{'message': {'role': 'assistant', 'content': None,
                    'reasoning_content': 'private protocol reasoning sentinel', 'tool_calls': [dict(id='probe-1', type='function',
                    function=dict(name='connection_probe', arguments=json.dumps({'nonce': nonce}))) ]}}]}
            if p == 'responses':
                nonce = payload['tools'][0]['parameters']['properties']['nonce']['enum'][0]
                return {'output': [{'type': 'reasoning', 'id': 'rs-1', 'encrypted_content': 'opaque-private-sentinel', 'summary': []},
                    {'type': 'function_call', 'id': 'fc-1', 'call_id': 'probe-1', 'name': 'connection_probe', 'arguments': json.dumps({'nonce': nonce})}]}
            if p == 'anthropic':
                nonce = payload['tools'][0]['input_schema']['properties']['nonce']['enum'][0]
                return {'content': [{'type': 'thinking', 'thinking': 'private protocol reasoning sentinel', 'signature': 'opaque-private-sentinel'},
                    {'type': 'tool_use', 'id': 'probe-1', 'name': 'connection_probe', 'input': {'nonce': nonce}}]}
            nonce = payload['tools'][0]['functionDeclarations'][0]['parameters']['properties']['nonce']['enum'][0]
            return {'candidates': [{'content': {'role': 'model', 'parts': [
                {'thought': True, 'text': 'private protocol reasoning sentinel'},
                {'functionCall': {'id': 'probe-1', 'name': 'connection_probe', 'args': {'nonce': nonce}}, 'thoughtSignature': 'opaque-private-sentinel'}]}}]}
        if p == 'chat_completions':
            assert payload['messages'][-2]['reasoning_content'] == 'private protocol reasoning sentinel'
            nonce = json.loads(payload['messages'][-1]['content'])['nonce']
            return {'choices': [{'message': {'role': 'assistant', 'content': nonce}}]}
        if p == 'responses':
            assert any(i.get('encrypted_content') == 'opaque-private-sentinel' for i in payload['input'])
            nonce = json.loads(payload['input'][-1]['output'])['nonce']
            return {'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': nonce}]}]}
        if p == 'anthropic':
            assert payload['messages'][-2]['content'][0]['signature'] == 'opaque-private-sentinel'
            nonce = json.loads(payload['messages'][-1]['content'][0]['content'])['nonce']
            return {'content': [{'type': 'text', 'text': nonce}]}
        assert payload['contents'][-2]['parts'][1]['thoughtSignature'] == 'opaque-private-sentinel'
        result = payload['contents'][-1]['parts'][0]['functionResponse']
        assert result['id'] == 'probe-1' and result['name'] == 'connection_probe'
        nonce = result['response']['nonce']
        return {'candidates': [{'content': {'role': 'model', 'parts': [{'text': nonce}]}}]}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def assistant(self, protocol, transport, provider='custom'):
        assistant = ModelAssistant(self.root / (protocol + '-' + provider), transport=transport)
        assistant._windows = False
        assistant.save_settings(dict(provider=provider, protocol=protocol, base_url='http://127.0.0.1:12345/v1', model='fixture-model', api_key='fixture-api-secret'))
        return assistant

    def test_four_native_protocols_complete_tool_roundtrip(self):
        for protocol in providers.PROTOCOLS:
            with self.subTest(protocol=protocol):
                peer = ProtocolFixture(protocol)
                assistant = self.assistant(protocol, peer)
                result = assistant.test_connection()
                self.assertTrue(result['passed'], result)
                self.assertEqual(result['requests'], 2)
                self.assertEqual(len(peer.requests), 2)
                for request in peer.requests:
                    self.assertNotIn('private-user', json.dumps(request['payload']))
                if protocol == 'responses':
                    self.assertTrue(peer.requests[0]['url'].endswith('/responses'))
                    self.assertFalse(peer.requests[0]['payload']['store'])
                    self.assertNotIn('messages', peer.requests[0]['payload'])
                elif protocol == 'anthropic':
                    self.assertTrue(peer.requests[0]['url'].endswith('/v1/messages'))
                    self.assertIn('anthropic-version', peer.requests[0]['headers'])
                    self.assertEqual(peer.requests[0]['payload']['tool_choice']['type'], 'any')
                elif protocol == 'gemini':
                    self.assertTrue(peer.requests[0]['url'].endswith('/models/fixture-model:generateContent'))
                    self.assertNotIn('Authorization', peer.requests[0]['headers'])
                    self.assertEqual(peer.requests[0]['headers']['x-goog-api-key'], 'fixture-api-secret')
                    self.assertEqual(peer.requests[0]['payload']['toolConfig']['functionCallingConfig']['mode'], 'ANY')

    def test_thinking_chat_preserves_exact_native_response_not_public_reasoning(self):
        response = {'choices': [{'message': {'role': 'assistant', 'content': '查看文件', 'reasoning_content': 'opaque-thinking',
                    'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {'name': 'inspect_items', 'arguments': '{"ids":["a"]}'}}]}}]}
        requests = []
        def transport(url, payload, headers, timeout):
            requests.append(copy.deepcopy(payload))
            return copy.deepcopy(response)
        assistant = self.assistant('chat_completions', transport, 'deepseek')
        first = assistant.agent_turn([{'role': 'user', 'content': '调查'}], TOOLS)
        self.assertNotIn('reasoning_content', first)
        assistant.agent_turn([{'role': 'user', 'content': '调查'}, first,
                             {'role': 'tool', 'tool_call_id': 'call-1', 'content': '{}'}], TOOLS)
        self.assertEqual(requests[-1]['messages'][1], response['choices'][0]['message'])

    def test_trace_contains_messages_and_tools_but_omits_secrets_and_continuation(self):
        for protocol in providers.PROTOCOLS:
            with self.subTest(protocol=protocol):
                assistant = self.assistant(protocol, ProtocolFixture(protocol))
                entries = []
                assistant.trace_hook = entries.append
                self.assertTrue(assistant.test_connection()['passed'])
                self.assertEqual([e['kind'] for e in entries], ['request', 'response', 'request', 'response'])
                data = json.dumps(entries, ensure_ascii=False)
                self.assertNotIn('fixture-api-secret', data)
                self.assertNotIn('private protocol reasoning sentinel', data)
                self.assertNotIn('opaque-private-sentinel', data)
                self.assertIn('connection_probe', data)
                self.assertIn('nonce', data)

    def test_trace_keeps_innocuous_thinking_configuration(self):
        assistant = self.assistant('chat_completions', ProtocolFixture('chat_completions'), 'glm')
        assistant.save_settings({'model': 'glm-5.3'})
        entries = []
        assistant.trace_hook = entries.append
        self.assertTrue(assistant.test_connection()['passed'])
        self.assertEqual(entries[0]['payload']['thinking'], {'type': 'enabled', 'clear_thinking': False})

    def test_debug_hook_failure_does_not_fail_model_operation(self):
        assistant = self.assistant('responses', ProtocolFixture('responses'))
        assistant.trace_hook = lambda event: (_ for _ in ()).throw(RuntimeError('trace disk failed'))
        self.assertTrue(assistant.test_connection()['passed'])

    def test_inflight_key_change_does_not_leak_previous_key_to_trace(self):
        entries = []
        assistant = self.assistant('chat_completions', None)
        previous = 'previous-inflight-private-token'
        assistant.save_settings({'api_key': previous})
        def transport(*args):
            assistant.save_settings({'api_key': 'new-inflight-private-token'})
            return {'choices': [{'message': {'role': 'assistant', 'content': previous}}]}
        assistant._transport = transport
        assistant.trace_hook = entries.append
        assistant.agent_turn([{'role': 'user', 'content': 'test'}], TOOLS)
        self.assertNotIn(previous, json.dumps(entries))
        self.assertNotIn('new-inflight-private-token', json.dumps(entries))

    def test_continuation_cannot_cross_model_or_connection(self):
        peer = ProtocolFixture('responses')
        assistant = self.assistant('responses', peer)
        tool = [{'type': 'function', 'function': {'name': 'connection_probe', 'parameters': {'type': 'object',
                'properties': {'nonce': {'type': 'string', 'enum': ['123']}}, 'required': ['nonce']}}}]
        first = assistant.agent_turn([{'role': 'user', 'content': 'probe'}], tool)
        assistant.save_settings({'model': 'other-model'})
        with self.assertRaisesRegex(ValueError, 'provider_configuration_changed'):
            assistant.agent_turn([first], tool)
        self.assertEqual(len(peer.requests), 1)

    def test_select_preset_fills_defaults_but_explicit_overrides_survive(self):
        assistant = ModelAssistant(self.root)
        state = assistant.save_settings({'provider': 'openai'})
        self.assertEqual(state['protocol'], 'responses')
        self.assertEqual(state['model'], 'gpt-6.1-sol')
        self.assertEqual(state['base_url'], 'https://api.openai.com/v1')
        overridden = assistant.save_settings({'provider': 'kimi_cn', 'model': 'custom-id', 'base_url': 'https://proxy.example/v1'})
        self.assertEqual(overridden['model'], 'custom-id')
        self.assertEqual(overridden['base_url'], 'https://proxy.example/v1')
        self.assertEqual(ModelAssistant(self.root).public_settings()['provider'], 'kimi_cn')

    def test_old_settings_load_as_custom_chat(self):
        self.root.mkdir(exist_ok=True)
        (self.root / 'model_settings.json').write_text(json.dumps({'base_url': 'https://old.example/v1', 'model': 'old-id'}))
        state = ModelAssistant(self.root).public_settings()
        self.assertEqual(state['provider'], 'custom')
        self.assertEqual(state['protocol'], 'chat_completions')
        self.assertEqual(state['model'], 'old-id')

    def test_provider_switch_does_not_silently_forward_saved_key(self):
        assistant = self.assistant('chat_completions', ProtocolFixture('chat_completions'))
        with self.assertRaisesRegex(ValueError, '来源已改变'):
            assistant.save_settings({'provider': 'openai'})
        self.assertEqual(assistant.public_settings()['provider'], 'custom')

    def test_parameter_mapping_uses_specific_provider_and_model(self):
        for provider, model in [('glm', 'glm-5.3'), ('deepseek', 'deepseek-flash'), ('kimi_cn', 'kimi-k3.1-pro')]:
            with self.subTest(provider=provider):
                settings = dict(provider=provider, protocol='chat_completions', base_url='https://example.com/v1', model=model, reasoning_effort='medium')
                _, payload, _ = providers.prepare(settings, dict(model=model, messages=[{'role': 'user', 'content': 'test'}],
                    tools=TOOLS, max_tokens=8192, stream=False), '')
                self.assertEqual(payload['reasoning_effort'], 'high')
                self.assertNotIn('temperature', payload)
                if provider.startswith('kimi'):
                    self.assertEqual(payload['max_completion_tokens'], 8192)
                    self.assertNotIn('max_tokens', payload)
                else:
                    self.assertEqual(payload['thinking']['type'], 'enabled')

    def test_current_anthropic_version_and_no_sampling_parameters(self):
        settings = dict(provider='anthropic', protocol='anthropic', base_url='https://api.anthropic.com/v1', model='claude-sonnet-5-5')
        url, payload, headers = providers.prepare(settings, dict(messages=[{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'u'}], tools=TOOLS), 'fixture')
        self.assertEqual(url, 'https://api.anthropic.com/v1/messages')
        self.assertEqual(headers['anthropic-version'], '2026-10-21')
        self.assertNotIn('temperature', payload)
        self.assertEqual(payload['system'], 's')

    def test_gemini_effort_is_native_and_unsupported_max_rejected_before_send(self):
        assistant = self.assistant('gemini', ProtocolFixture('gemini'), 'gemini')
        assistant.save_settings({'model': 'gemini-3.8-flash', 'reasoning_effort': 'medium'})
        settings = assistant.public_settings()
        _, payload, _ = providers.prepare(settings, {'messages': [{'role': 'user', 'content': 'x'}]}, '')
        self.assertEqual(payload['generationConfig']['thinkingConfig']['thinkingLevel'], 'medium')
        with self.assertRaisesRegex(ValueError, '自动'):
            assistant.save_settings({'reasoning_effort': 'max'})

    def test_unverified_provider_effort_is_not_silently_ignored(self):
        assistant = ModelAssistant(self.root)
        with self.assertRaisesRegex(ValueError, '自动'):
            assistant.save_settings({'provider': 'qwen_cn', 'reasoning_effort': 'high'})
        self.assertEqual(assistant.public_settings()['provider'], 'custom')
        saved = assistant.save_settings({'provider': 'deepseek', 'reasoning_effort': 'medium'})
        self.assertEqual(saved['reasoning_effort'], 'high')

    def test_gemini_synthesized_call_ids_are_unique_across_responses(self):
        settings = dict(provider='gemini', protocol='gemini', model='gemini-3.8-flash')
        raw = {'candidates': [{'content': {'role': 'model', 'parts': [{'functionCall': {'name': 'a', 'args': {}}}]}}]}
        ids = [providers.normalize(settings, raw)['choices'][0]['message']['tool_calls'][0]['id'] for _ in range(2)]
        self.assertNotEqual(ids[0], ids[1])

    def test_failed_tool_probe_does_not_report_chat_only_success(self):
        assistant = self.assistant('chat_completions', lambda *args: {'choices': [{'message': {'content': 'hello'}}]})
        result = assistant.test_connection()
        self.assertFalse(result['passed'])
        self.assertEqual(result['code'], 'connection_tool_failed')

    def test_deepseek_thinking_probe_does_not_send_unsupported_required_choice(self):
        """Independent peer rejects the combination documented as HTTP 400."""
        native = ProtocolFixture('chat_completions')
        def deepseek_peer(url, payload, headers, timeout):
            if payload.get('thinking', {}).get('type') == 'enabled' and payload.get('tool_choice') not in ('auto', 'none'):
                raise ValueError('provider_parameters')
            return native(url, payload, headers, timeout)
        assistant = self.assistant('chat_completions', deepseek_peer, 'deepseek')
        assistant.save_settings({'model': 'deepseek-flash'})
        result = assistant.test_connection()
        self.assertTrue(result['passed'], result)
        self.assertEqual(len(native.requests), 2)
        first, final = [request['payload'] for request in native.requests]
        self.assertEqual(first['tool_choice'], 'auto')
        self.assertEqual(final['tool_choice'], 'none')
        for payload in (first, final):
            self.assertEqual(payload['thinking'], {'type': 'enabled'})
            self.assertEqual(payload['reasoning_effort'], 'high')
            self.assertEqual(payload['model'], 'deepseek-flash')
        self.assertEqual(final['messages'][-2]['reasoning_content'], 'private protocol reasoning sentinel')

    def test_deepseek_auto_probe_still_requires_correct_tool_and_nonce(self):
        for response in ({'role': 'assistant', 'content': 'connected'},
                         {'role': 'assistant', 'content': None, 'tool_calls': [dict(id='wrong', type='function',
                           function=dict(name='connection_probe', arguments='{"nonce":"wrong"}'))]}):
            with self.subTest(response=response):
                assistant = self.assistant('chat_completions', lambda *args: {'choices': [{'message': response}]}, 'deepseek')
                result = assistant.test_connection()
                self.assertFalse(result['passed'])
                self.assertEqual(result['code'], 'connection_tool_failed')

    def test_probe_choice_policy_does_not_change_other_protocols_or_force_disable_thinking(self):
        for protocol in providers.PROTOCOLS:
            for provider in ('custom', 'openai', 'glm', 'anthropic', 'gemini', 'deepseek'):
                with self.subTest(protocol=protocol, provider=provider):
                    expected = 'auto' if protocol == 'chat_completions' and provider == 'deepseek' else 'required'
                    self.assertEqual(providers.connection_probe_tool_choice({'provider': provider, 'protocol': protocol}), expected)
        self.assertEqual(providers.connection_probe_tool_choice({'provider':'deepseek','model':'deepseek-chat'}), 'auto')

    def test_connection_errors_are_safe_and_actionable(self):
        for code in ('provider_auth', 'provider_model', 'provider_rate_limit', 'provider_parameters', 'provider_unavailable'):
            with self.subTest(code=code):
                assistant = self.assistant('chat_completions', lambda *args: (_ for _ in ()).throw(ValueError(code)))
                result = assistant.test_connection()
                self.assertFalse(result['passed'])
                self.assertEqual(result['code'], code)
        assistant._transport = lambda *args: (_ for _ in ()).throw(RuntimeError('fixture-api-secret provider private body'))
        self.assertNotIn('fixture-api-secret', json.dumps(assistant.test_connection()))

    def test_http_status_classification_never_exposes_body(self):
        for status, code in [(400, 'provider_parameters'), (401, 'provider_auth'), (403, 'provider_auth'), (404, 'provider_model'), (429, 'provider_rate_limit'), (503, 'provider_unavailable')]:
            class Opener:
                def open(self, request, timeout):
                    raise HTTPError(request.full_url, status, 'secret', {}, io.BytesIO(b'private body'))
            with self.subTest(status=status), patch('diskpilot.llm.build_opener', return_value=Opener()):
                with self.assertRaisesRegex(ValueError, '^' + code + '$'):
                    _http_transport('http://127.0.0.1:1234/v1', {}, {}, 1)

    def test_model_path_injection_is_rejected_for_gemini(self):
        assistant = self.assistant('gemini', ProtocolFixture('gemini'))
        assistant.save_settings({'model': 'x/../../other?key=oops'})
        with self.assertRaisesRegex(ValueError, 'invalid_model_id'):
            assistant.agent_turn([{'role': 'user', 'content': 'test'}], TOOLS)

    def test_presets_are_independent_documented_snapshots_not_live_claims(self):
        values = providers.presets()
        self.assertGreaterEqual(len(values['presets']), 15)
        ids = [p['id'] for p in values['presets']]
        self.assertEqual(len(ids), len(set(ids)))
        for value in values['presets']:
            self.assertEqual(value['checked_at'], '2026-11-02')
            self.assertFalse(value['live_provider_tested'])
            self.assertTrue(all(s.startswith('https://') for s in value['sources']))
        values['presets'].clear()
        self.assertGreater(len(providers.presets()['presets']), 0)
        self.assertEqual(providers.get_preset('kimi_cn')['default_model'], 'kimi-k3.1-pro')
        self.assertEqual(providers.get_preset('qianfan')['default_model'], '')


if __name__ == '__main__':
    unittest.main()
