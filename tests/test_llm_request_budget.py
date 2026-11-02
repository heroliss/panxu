"""Request budgets apply to one native wire request, not its in-memory copies."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from diskpilot import llm, providers
from diskpilot.llm import ModelAssistant


TOOLS = [{'type': 'function', 'function': {'name': 'sample', 'description': 'Owned offline fixture.',
          'parameters': {'type': 'object', 'properties': {'items': {'type': 'array', 'items': {'type': 'object'}}}}}}]


def response(protocol):
    return {
        'chat_completions': {'choices': [{'message': {'role': 'assistant', 'content': 'Done.'}, 'finish_reason': 'stop'}]},
        'responses': {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Done.'}]}]},
        'anthropic': {'content': [{'type': 'text', 'text': 'Done.'}], 'stop_reason': 'end_turn'},
        'gemini': {'candidates': [{'content': {'parts': [{'text': 'Done.'}]}, 'finishReason': 'STOP'}]},
    }[protocol]


def transcript(settings, count):
    arguments = {'items': [dict(id=f'owned-{index}', purpose='缓存' * 200,
                               reason='证据' * 200, impact='后果' * 200) for index in range(count)]}
    raw = json.dumps(arguments, ensure_ascii=False)
    call = dict(id='owned-call', type='function', function=dict(name='sample', arguments=raw))
    protocol = settings['protocol']
    native = {
        'chat_completions': dict(role='assistant', content=None, tool_calls=[call]),
        'responses': [dict(type='function_call', call_id='owned-call', name='sample', arguments=raw)],
        'anthropic': [dict(type='tool_use', id='owned-call', name='sample', input=arguments)],
        'gemini': dict(role='model', parts=[dict(functionCall=dict(id='owned-call', name='sample', args=arguments))]),
    }[protocol]
    return [dict(role='user', content='Continue the owned fixture.'),
            dict(role='assistant', content=None, tool_calls=[call],
                 _continuation=dict(connection=providers.connection_identity(settings), value=native)),
            dict(role='tool', tool_call_id='owned-call', content='{"saved":true}')]


class NativeRequestBudgetTests(unittest.TestCase):
    def test_native_continuation_does_not_count_tool_arguments_twice(self):
        for protocol in providers.PROTOCOLS:
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                requests = []
                def transport(url, payload, headers, timeout):
                    requests.append(copy.deepcopy(payload))
                    return response(protocol)
                assistant = ModelAssistant(Path(directory), transport=transport)
                assistant.save_settings(dict(base_url='http://127.0.0.1:1234/v1', model='owned-model', protocol=protocol))
                settings = assistant.public_settings()
                messages = transcript(settings, 30)
                canonical = dict(messages=messages, tools=TOOLS, tool_choice='auto', max_tokens=3500, stream=False)
                _, wire, _ = providers.prepare(settings, canonical, '')
                self.assertGreater(len(llm._json_text(canonical).encode()), llm.MAX_REQUEST_BYTES)
                self.assertLess(len(llm._json_text(wire).encode()), llm.MAX_REQUEST_BYTES)
                result = assistant.agent_turn(messages, TOOLS, stream=False)
                self.assertEqual(result['content'], 'Done.')
                self.assertEqual(len(requests), 1)
                self.assertLess(len(llm._json_text(requests[0]).encode()), llm.MAX_REQUEST_BYTES)

    def test_oversized_native_wire_is_still_rejected_before_transport(self):
        for protocol in providers.PROTOCOLS:
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                def no_transport(*args):
                    raise AssertionError('Oversized requests must stay local.')
                assistant = ModelAssistant(Path(directory), transport=no_transport)
                assistant.save_settings(dict(base_url='http://127.0.0.1:1234/v1', model='owned-model', protocol=protocol))
                settings = assistant.public_settings()
                messages = transcript(settings, 80)
                _, wire, _ = providers.prepare(settings, dict(messages=messages, tools=TOOLS, max_tokens=3500), '')
                self.assertGreater(len(llm._json_text(wire).encode()), llm.MAX_REQUEST_BYTES)
                with self.assertRaisesRegex(ValueError, '^request_budget$'):
                    assistant.agent_turn(messages, TOOLS, stream=False)


if __name__ == '__main__':
    unittest.main()
