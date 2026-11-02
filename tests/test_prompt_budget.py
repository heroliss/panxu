"""Prompt compaction checks use disposable profiles and independent fake peers."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.llm import ModelAssistant, _http_transport, MAX_REQUEST_BYTES
from tests.prompt_fixture import parse_host_data


TOOLS = [{'type': 'function', 'function': {'name': 'inspect_items', 'description': 'Inspect known IDs',
    'parameters': {'type': 'object', 'properties': {'ids': {'type': 'array', 'items': {'type': 'string'}}},
                   'required': ['ids'], 'additionalProperties': False}}}]


def reply(protocol):
    if protocol == 'responses':
        return {'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '已核对。'}]}]}
    if protocol == 'anthropic':
        return {'content': [{'type': 'text', 'text': '已核对。'}]}
    if protocol == 'gemini':
        return {'candidates': [{'content': {'role': 'model', 'parts': [{'text': '已核对。'}]}}]}
    return {'choices': [{'message': {'role': 'assistant', 'content': '已核对。'}}]}


class PromptBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-prompt-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = []

    def assistant(self, protocol='chat_completions', preferences=''):
        def transport(url, payload, headers, timeout):
            self.requests.append(copy.deepcopy(payload))
            return reply(protocol)
        value = ModelAssistant(self.root / protocol, transport=transport)
        value._windows = False
        value.save_settings(dict(provider='custom', protocol=protocol, base_url='http://127.0.0.1:11434/v1',
                                 model='prompt-fixture', stream=False, custom_instructions=preferences))
        return value

    @staticmethod
    def context():
        return dict(goal='请只分析这些文件', simulation_only=True, available_items=2,
            preferences='保留我创建的内容', user_choices=[{'id': 'kept-file', 'choice': 'keep'}],
            coverage={'complete': False, 'unknown_count': None},
            initial_objects=[dict(id='a', cleanup_rating=None, size_bytes=0,
                timestamps={'created_at': None, 'modified_at': '2026-01-01T00:00:00+00:00', 'scope': 'entry_only'})])

    def test_host_context_and_tool_results_shrink_without_losing_facts_or_roles(self):
        value = self.assistant()
        context = self.context()
        fact = dict(items=[{'id': 'a', 'name': '名称保留  内部空格\n和换行', 'rating': None,
                           'blocked_reasons': [], 'can_prepare': False}], has_more=False, next_cursor=None)
        context_text, tool_text = (json.dumps(item, ensure_ascii=False, indent=2) for item in (context, fact))
        args = json.dumps({'ids': ['a']}, indent=2)
        messages = [{'role': 'system', 'content': '仅使用已有范围。'}, {'role': 'user', 'content': context_text},
                    {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'lookup', 'type': 'function',
                        'function': {'name': 'inspect_items', 'arguments': args}}]},
                    {'role': 'tool', 'tool_call_id': 'lookup', 'content': tool_text}]
        before = copy.deepcopy(messages)
        value.agent_turn(messages, TOOLS, stream=False)
        sent = self.requests[-1]
        self.assertEqual([m['role'] for m in sent['messages']], [m['role'] for m in messages])
        self.assertEqual(sent['tools'], TOOLS)
        self.assertEqual(parse_host_data(sent['messages'][1]['content']), context)
        self.assertEqual(parse_host_data(sent['messages'][3]['content']), fact)
        self.assertEqual(sent['messages'][3]['tool_call_id'], 'lookup')
        self.assertEqual(json.loads(sent['messages'][2]['tool_calls'][0]['function']['arguments']), {'ids': ['a']})
        self.assertLess(len(sent['messages'][1]['content']), len(context_text))
        self.assertLess(len(sent['messages'][3]['content']), len(tool_text))
        self.assertEqual(messages, before)

    def test_arbitrary_public_json_text_keeps_original_formatting(self):
        value = self.assistant()
        raw = '{\n    "说明" : "  原文  保留  ",\n    "unknown" : null\n}'
        messages = [{'role': 'user', 'content': raw}, {'role': 'assistant', 'content': raw}]
        value.agent_turn(messages, [], stream=False)
        self.assertEqual([m['content'] for m in self.requests[-1]['messages']], [raw, raw])

    def test_privacy_and_saved_preference_budgets_still_apply_before_compaction(self):
        preferences = '偏好' * 1998 + '保留照片'
        value = self.assistant(preferences=preferences)
        value._api_key = 'only-a-fixture-secret'
        context = self.context()
        context.update(preferences=preferences, nested={'preferences': preferences},
                       secret='only-a-fixture-secret', path=r'C:\Users\fixture-person\private.txt')
        value.agent_turn([{'role': 'user', 'content': json.dumps(context, ensure_ascii=False, indent=2)}], [], stream=False)
        sent_text = self.requests[-1]['messages'][0]['content']
        sent = parse_host_data(sent_text)
        self.assertEqual(sent['preferences'], preferences)
        self.assertEqual(len(sent['nested']['preferences']), 2000)
        self.assertNotIn('only-a-fixture-secret', sent_text)
        self.assertNotIn('fixture-person', sent_text)
        self.assertIsNone(sent['coverage']['unknown_count'])

    def test_public_json_sensitive_fields_are_still_scrubbed(self):
        value = self.assistant()
        raw = json.dumps({'description': '甲' * 2100, 'path': r'C:\private\project.txt'}, indent=4)
        value.agent_turn([{'role': 'assistant', 'content': raw}], [], stream=False)
        sent = json.loads(self.requests[-1]['messages'][0]['content'])
        self.assertEqual(len(sent['description']), 2000)
        self.assertNotIn('private', sent['path'])

    def test_four_native_protocols_keep_context_and_complete_tool_result_meaning(self):
        context, fact = self.context(), {'ids': ['a'], 'complete': False, 'unknown': None}
        for protocol in ('chat_completions', 'responses', 'anthropic', 'gemini'):
            with self.subTest(protocol=protocol):
                value = self.assistant(protocol)
                messages = [{'role': 'system', 'content': '只读。'},
                    {'role': 'user', 'content': json.dumps(context, ensure_ascii=False, indent=2)},
                    {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'lookup', 'type': 'function',
                        'function': {'name': 'inspect_items', 'arguments': '{ "ids" : [ "a" ] }'}}]},
                    {'role': 'tool', 'tool_call_id': 'lookup', 'content': json.dumps(fact, indent=2)}]
                result = value.agent_turn(messages, TOOLS, stream=False)
                self.assertEqual(result['content'], '已核对。')
                sent = self.requests[-1]
                if protocol == 'responses':
                    sent_context = sent['input'][1]['content']
                    sent_fact = parse_host_data(sent['input'][-1]['output'])
                    self.assertEqual(sent['input'][-1]['call_id'], 'lookup')
                elif protocol == 'anthropic':
                    sent_context = sent['messages'][0]['content'][0]['text']
                    sent_fact = parse_host_data(sent['messages'][-1]['content'][0]['content'])
                    self.assertEqual(sent['messages'][-1]['content'][0]['tool_use_id'], 'lookup')
                elif protocol == 'gemini':
                    sent_context = sent['contents'][0]['parts'][0]['text']
                    response = sent['contents'][-1]['parts'][0]['functionResponse']
                    sent_fact = parse_host_data(response['response'])
                    self.assertEqual(response['name'], 'inspect_items')
                else:
                    sent_context = sent['messages'][1]['content']
                    sent_fact = parse_host_data(sent['messages'][-1]['content'])
                self.assertEqual(parse_host_data(sent_context), context)
                self.assertEqual(sent_fact, fact)

    def test_http_encoding_preserves_values_and_enforces_actual_request_byte_limit(self):
        captured = []
        class Response(io.BytesIO):
            status, headers = 200, {'Content-Type': 'application/json'}
        class Opener:
            def open(self, request, timeout):
                captured.append(request.data)
                return Response(b'{"ok":true}')
        body = {'messages': [{'role': 'user', 'content': '这是  原始文本'}], 'unknown': None, 'disabled': False}
        with patch('diskpilot.llm.build_opener', return_value=Opener()):
            self.assertEqual(_http_transport('http://127.0.0.1:11434/v1', body, {}, 1), {'ok': True})
            self.assertEqual(json.loads(captured[-1]), body)
            self.assertLess(len(captured[-1]), len(json.dumps(body, ensure_ascii=False).encode()))
            with self.assertRaisesRegex(ValueError, 'request_budget'):
                _http_transport('http://127.0.0.1:11434/v1', {'large': 'a' * MAX_REQUEST_BYTES}, {}, 1)
        self.assertEqual(len(captured), 1)

    def test_public_truncation_flag_is_host_owned_and_does_not_guess_from_exact_limit(self):
        for text, remote_flag, expected in (('短回复', True, False), ('字' * 8000, True, False), ('字' * 8001, False, True)):
            with self.subTest(length=len(text)):
                value = self.assistant()
                value._transport = lambda *_args: {'choices': [{'message': {'role': 'assistant', 'content': text,
                    '_public_text_truncated': remote_flag}}]}
                result = value.agent_turn([{'role': 'user', 'content': '只读测试'}], [], stream=False)
                self.assertEqual(result['_public_text_truncated'], expected)
                self.assertEqual(result['content'], text[:8000])


if __name__ == '__main__':
    unittest.main()
