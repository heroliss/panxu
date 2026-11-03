"""Response budgets accept bounded real-protocol overhead, without cloud access."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from diskpilot import streaming
from diskpilot.llm import ModelAssistant
from diskpilot.tasks import tool_specs


def event(value):
    return b'data: ' + json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode() + b'\n\n'


def completion(delta, finish=None):
    # Fixed, artificial metadata with the fields shown by the official SSE
    # example. None of these values come from a real profile or response.
    return dict(id='00000000-0000-0000-0000-000000000000', object='chat.completion.chunk',
        created=0, model='deepseek-flash', system_fingerprint='fixture-000000000000000000000000',
        choices=[dict(index=0, delta=delta, logprobs=None, finish_reason=finish)], usage=None)


class InvestigationBudgetTests(unittest.TestCase):
    def assistant(self, transport, *, protocol='chat_completions', provider='deepseek'):
        directory = tempfile.TemporaryDirectory(prefix='panxu-budget-regression-')
        self.addCleanup(directory.cleanup)
        assistant = ModelAssistant(Path(directory.name), transport=transport)
        assistant._windows = False
        assistant.save_settings(dict(provider=provider, protocol=protocol,
            base_url='http://127.0.0.1:11434/v1', model='deepseek-flash', stream=True))
        return assistant

    def test_official_sse_envelopes_do_not_exhaust_budget_for_two_thousand_tiny_deltas(self):
        chunks = [event(completion({'reasoning_content': 'x'}))] * 2000
        chunks += [event(completion({'content': 'ok'}, 'stop')), b'data: [DONE]\n\n']
        self.assertGreater(sum(map(len, chunks)), 512 * 1024)
        previews = []
        result = streaming.assemble('chat_completions', chunks,
            on_preview=lambda text, pending: previews.append(text))
        self.assertEqual(result['choices'][0]['message']['content'], 'ok')
        self.assertEqual(result['choices'][0]['message']['reasoning_content'], 'x' * 2000)
        self.assertNotIn('x', ''.join(previews))

    def test_over_four_thousand_small_frames_can_complete_without_large_semantic_response(self):
        delta = event({'choices': [{'delta': {'reasoning_content': 'x'}}]})
        chunks = [delta] * 5000 + [event(completion({'content': 'ok'}, 'stop')), b'data: [DONE]\n\n']
        self.assertLess(sum(map(len, chunks)), 512 * 1024)
        result = streaming.assemble('chat_completions', chunks)
        self.assertEqual(len(result['choices'][0]['message']['reasoning_content']), 5000)
        self.assertEqual(result['choices'][0]['message']['content'], 'ok')

    def test_normalized_chinese_and_native_continuation_are_not_ascii_budgeted_twice(self):
        public, private = '甲' * 4000, '乙' * 40000
        response = dict(choices=[dict(message=dict(role='assistant', content=public,
            reasoning_content=private), finish_reason='stop')])
        self.assertLess(len(json.dumps(response, ensure_ascii=False).encode()), 256 * 1024)
        requests = []

        def offline(_url, payload, _headers, _timeout):
            requests.append(copy.deepcopy(payload))
            return copy.deepcopy(response)

        assistant = self.assistant(offline)
        result = assistant.agent_turn([dict(role='user', content='Fixed offline fixture.')], [], stream=False)
        self.assertEqual(result['content'], public)
        self.assertEqual(result['_continuation']['value']['reasoning_content'], private)
        self.assertNotIn('reasoning_content', result)
        self.assertEqual(len(requests), 1)

    def assert_schema(self, value, schema):
        """Check this fixture against the actual advertised JSON-schema subset."""
        expected = schema.get('type')
        if expected == 'object':
            self.assertIsInstance(value, dict)
            self.assertTrue(set(schema.get('required', [])) <= set(value))
            properties = schema.get('properties', {})
            if schema.get('additionalProperties') is False:
                self.assertFalse(set(value) - set(properties))
            for name, child in value.items():
                self.assert_schema(child, properties[name])
        elif expected == 'array':
            self.assertIsInstance(value, list)
            self.assertGreaterEqual(len(value), schema.get('minItems', 0))
            self.assertLessEqual(len(value), schema.get('maxItems', len(value)))
            for child in value:
                self.assert_schema(child, schema['items'])
        elif expected == 'string':
            self.assertIsInstance(value, str)
            self.assertLessEqual(len(value), schema.get('maxLength', len(value)))
        elif expected == ['integer', 'null']:
            self.assertTrue(value is None or type(value) is int)
            if value is not None:
                self.assertLessEqual(value, schema['maximum'])
                self.assertGreaterEqual(value, schema['minimum'])
        if 'enum' in schema:
            self.assertIn(value, schema['enum'])

    def maximum_arguments(self, name, *, astral=False):
        spec = next(row for row in tool_specs() if row['function']['name'] == name)
        schema = spec['function']['parameters']
        items = schema['properties']['items']
        self.assertEqual(items['maxItems'], 30)
        fields = items['items']['properties']
        rows = []
        for index in range(items['maxItems']):
            character = '😀' if astral else '标'
            ident = str(index) + character * (fields['id']['maxLength'] - len(str(index)))
            row = dict(id=ident, reason=('😀' if astral else '因') * fields['reason']['maxLength'],
                impact=('😀' if astral else '果') * fields['impact']['maxLength'])
            evidence = fields['evidence_ids']
            row['evidence_ids'] = [str(number) + ('😀' if astral else '证') * (evidence['items']['maxLength'] - len(str(number)))
                                   for number in range(evidence['maxItems'])]
            if name == 'record_advice':
                row.update(purpose=('😀' if astral else '用') * fields['purpose']['maxLength'], conclusion='review',
                           cleanup_rating=3, inheritance='none')
            else:
                row['action'] = 'delete'
            rows.append(row)
        result = dict(items=rows)
        if name == 'propose_plan':
            # Presentation preferences cannot add a hidden summary limit;
            # exercise the full schema limit together with all module fields.
            result.update(summary=('😀' if astral else '总') * schema['properties']['summary']['maxLength'])
            briefing = schema['properties']['briefing']['properties']
            result['briefing'] = {key: [chr(0x4e00 + module * 8 + number)
                                       + ('😀' if astral else chr(0x4e00 + module * 8 + number))
                                       * (definition['items']['maxLength'] - 1)
                                       for number in range(definition['maxItems'])]
                                  for module, (key, definition) in enumerate(briefing.items())}
        self.assert_schema(result, schema)
        return spec, result

    def test_thirty_schema_bounded_escaped_advice_and_plan_items_survive_stream_and_agent_validation(self):
        self.assertGreaterEqual(streaming.MAX_ARGUMENTS, 128000)
        for name, astral, escaped in ((name, astral, escaped) for name in ('record_advice', 'propose_plan')
                                      for astral in (False, True) for escaped in (False, True)):
            with self.subTest(tool=name, astral=astral, escaped=escaped):
                spec, body = self.maximum_arguments(name, astral=astral)
                arguments = json.dumps(body, ensure_ascii=escaped, separators=(',', ':'))
                self.assertGreater(len(arguments), 16000)
                semantic = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
                self.assertLessEqual(len(semantic), streaming.MAX_ARGUMENTS)
                self.assertLessEqual(len(arguments), streaming.MAX_ARGUMENTS * 12)
                if astral and escaped:
                    self.assertGreater(len(arguments), 128000)
                chunks = [event(completion(dict(tool_calls=[dict(index=0, id='fixture-call', type='function',
                    function=dict(name=name, arguments=''))])))]
                for offset in range(0, len(arguments), 1000):
                    chunks.append(event(completion(dict(tool_calls=[dict(index=0,
                        function=dict(arguments=arguments[offset:offset + 1000]))]))))
                chunks += [event(completion({}, 'tool_calls')), b'data: [DONE]\n\n']
                self.assertTrue(all(len(chunk) < 128 * 1024 for chunk in chunks))
                assistant = self.assistant(lambda *_args: iter(chunks))
                result = assistant.agent_turn([dict(role='user', content='Fixed tool fixture.')], [spec])
                self.assertEqual(len(result['tool_calls']), 1)
                self.assertEqual(result['tool_calls'][0]['function']['name'], name)
                self.assertEqual(json.loads(result['tool_calls'][0]['function']['arguments']), body)

    def test_argument_semantic_budget_remains_bounded_after_unicode_normalization(self):
        arguments = json.dumps(dict(value='x' * streaming.MAX_ARGUMENTS), separators=(',', ':'))
        with self.assertRaises(ValueError):
            streaming._arguments(arguments)

    def test_explicit_provider_token_limits_are_budget_failures_for_all_protocols_and_transports(self):
        for protocol in ('chat_completions', 'responses', 'anthropic', 'gemini'):
            for streamed in (False, True):
                for has_usage in (False, True):
                    with self.subTest(protocol=protocol, streamed=streamed, usage=has_usage):
                        usage = 77 if has_usage else None
                        tool = dict(id='partial-call', type='function',
                                    function=dict(name='inspect_items', arguments='{'))
                        if protocol == 'chat_completions':
                            signal = 'length'
                            native = dict(choices=[dict(message=dict(role='assistant', content=None,
                                tool_calls=[tool]), finish_reason=signal)])
                            if has_usage:
                                native['usage'] = dict(completion_tokens=usage)
                            final = completion({}, signal)
                            final.update({key: native[key] for key in ('usage',) if key in native})
                            chunks = [event(completion(dict(tool_calls=[dict(tool, index=0)]))),
                                      event(final), b'data: [DONE]\n\n']
                        elif protocol == 'responses':
                            signal = 'max_output_tokens'
                            call = dict(type='function_call', id='partial-call', call_id='partial-call',
                                        name='inspect_items', arguments='{')
                            native = dict(status='incomplete', incomplete_details=dict(reason=signal), output=[call])
                            if has_usage:
                                native['usage'] = dict(output_tokens=usage)
                            chunks = [event(dict(type='response.output_item.added', output_index=0, item=call)),
                                      event(dict(type='response.incomplete', response=native))]
                        elif protocol == 'anthropic':
                            signal = 'max_tokens'
                            block = dict(type='tool_use', id='partial-call', name='inspect_items', input={})
                            native = dict(role='assistant', stop_reason=signal, content=[block])
                            if has_usage:
                                native['usage'] = dict(output_tokens=usage)
                            final = dict(type='message_delta', delta=dict(stop_reason=signal))
                            if has_usage:
                                final['usage'] = native['usage']
                            # Even a parseable tool block cannot become an
                            # accepted call when the provider terminal is cut.
                            chunks = [event(dict(type='message_start', message=dict(role='assistant', content=[]))),
                                event(dict(type='content_block_start', index=0, content_block=block)),
                                event(dict(type='content_block_stop', index=0)), event(final),
                                event(dict(type='message_stop'))]
                        else:
                            signal = 'MAX_TOKENS'
                            content = dict(role='model', parts=[dict(functionCall=dict(id='partial-call',
                                name='inspect_items', args={}))])
                            native = dict(candidates=[dict(content=content, finishReason=signal)])
                            if has_usage:
                                native['usageMetadata'] = dict(candidatesTokenCount=usage)
                            final = dict(candidates=[dict(finishReason=signal)])
                            if has_usage:
                                final['usageMetadata'] = native['usageMetadata']
                            chunks = [event(dict(candidates=[dict(content=content)])), event(final)]
                        requests = []

                        def offline(_url, payload, _headers, _timeout):
                            requests.append(copy.deepcopy(payload))
                            return iter(chunks) if streamed else copy.deepcopy(native)

                        assistant = self.assistant(offline, protocol=protocol, provider='custom')
                        with self.assertRaisesRegex(ValueError, '^response_budget$') as caught:
                            assistant.agent_turn([dict(role='user', content='Fixed truncation fixture.')], [],
                                                 stream=streamed)
                        self.assertEqual(caught.exception.budget,
                            dict(boundary='generation_tokens', observed=usage, limit=streaming.MAX_GENERATION_TOKENS, signal=signal))
                        self.assertEqual(len(requests), 1)

    def test_disconnect_without_explicit_limit_remains_incomplete(self):
        chunks = [event(completion(dict(tool_calls=[dict(index=0, id='partial-call', type='function',
            function=dict(name='inspect_items', arguments='{'))])))]
        with self.assertRaisesRegex(ValueError, '^stream_incomplete$'):
            streaming.assemble('chat_completions', chunks, max_output_tokens=3500)


if __name__ == '__main__':
    unittest.main()
