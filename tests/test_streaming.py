import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from diskpilot import streaming
from diskpilot.llm import ModelAssistant, _http_transport, _HTTPStream
from diskpilot.trace import DeveloperTrace


TOOLS = [{'type': 'function', 'function': {'name': 'inspect_items', 'parameters': {'type': 'object',
    'properties': {'ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['ids']}}}]
PRIVATE = 'never-public-thinking'
SIGNATURE = 'opaque-continuation-canary'


def event(value, name=None):
    return ((('event: ' + name + '\n') if name else '') + 'data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode()


def protocol_wire(protocol):
    arguments = '{"ids":["fixture"]}'
    if protocol == 'chat_completions':
        chunks = [event({'choices': [{'index': 0, 'delta': {'role': 'assistant', 'reasoning_content': PRIVATE}}]}),
            event({'choices': [{'index': 0, 'delta': {'content': '公开'}}]}),
            event({'choices': [{'index': 0, 'delta': {'content': '说明。', 'tool_calls': [{'index': 0, 'id': 'call-1', 'type': 'function', 'function': {'name': 'inspect_items', 'arguments': '{"ids":'}}]}}]}),
            event({'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '["fixture"]}'}}]}, 'finish_reason': 'tool_calls'}]}),
            event({'choices': [], 'usage': {'prompt_tokens': 12, 'completion_tokens': 5}}), b'data: [DONE]\n\n']
    elif protocol == 'responses':
        output = [{'type': 'reasoning', 'encrypted_content': SIGNATURE},
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '公开说明。'}]},
            {'type': 'function_call', 'id': 'fc-1', 'call_id': 'call-1', 'name': 'inspect_items', 'arguments': arguments}]
        chunks = [event({'type': 'response.output_item.added', 'output_index': 0, 'item': output[0]}),
            event({'type': 'response.output_text.delta', 'output_index': 1, 'content_index': 0, 'delta': '公开说明。'}),
            event({'type': 'response.output_item.added', 'output_index': 2, 'item': dict(output[2], arguments='')}),
            event({'type': 'response.function_call_arguments.delta', 'output_index': 2, 'delta': arguments}),
            event({'type': 'response.completed', 'response': {'status': 'completed', 'output': output, 'usage': {'input_tokens': 12, 'output_tokens': 5}}})]
    elif protocol == 'anthropic':
        chunks = [event({'type': 'message_start', 'message': {'role': 'assistant', 'content': [], 'usage': {'input_tokens': 12}}}),
            event({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': ''}}),
            event({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': PRIVATE}}),
            event({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'signature_delta', 'signature': SIGNATURE}}),
            event({'type': 'content_block_stop', 'index': 0}),
            event({'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}}),
            event({'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': '公开说明。'}}),
            event({'type': 'content_block_stop', 'index': 1}),
            event({'type': 'content_block_start', 'index': 2, 'content_block': {'type': 'tool_use', 'id': 'call-1', 'name': 'inspect_items', 'input': {}}}),
            event({'type': 'content_block_delta', 'index': 2, 'delta': {'type': 'input_json_delta', 'partial_json': '{"ids":'}}),
            event({'type': 'content_block_delta', 'index': 2, 'delta': {'type': 'input_json_delta', 'partial_json': '["fixture"]}'}}),
            event({'type': 'content_block_stop', 'index': 2}),
            event({'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 5}}),
            event({'type': 'message_stop'})]
    else:
        chunks = [event({'candidates': [{'content': {'parts': [{'thought': True, 'text': PRIVATE}]}}]}),
            event({'candidates': [{'content': {'parts': [{'text': '公开'}]}}]}),
            event({'candidates': [{'content': {'parts': [{'text': '说明。'}]}}]}),
            event({'candidates': [{'content': {'parts': [{'functionCall': {'id': 'call-1', 'name': 'inspect_items', 'args': {'ids': ['fixture']}}, 'thoughtSignature': SIGNATURE}]}}]}),
            event({'candidates': [{'finishReason': 'STOP'}], 'usageMetadata': {'promptTokenCount': 12, 'candidatesTokenCount': 5}})]
    return b''.join(chunks)


class StreamingTests(unittest.TestCase):
    def test_four_protocols_handle_byte_split_utf8_and_preserve_only_private_continuation(self):
        for protocol in ('chat_completions', 'responses', 'anthropic', 'gemini'):
            with self.subTest(protocol=protocol):
                previews = []
                wire = protocol_wire(protocol)
                raw = streaming.assemble(protocol, (wire[i:i + 1] for i in range(len(wire))),
                    on_preview=lambda text, pending: previews.append((text, pending)))
                self.assertIn('公开说明。', previews[-1][0])
                self.assertTrue(previews[-1][1])
                public = json.dumps(previews, ensure_ascii=False)
                self.assertNotIn(PRIVATE, public)
                self.assertNotIn(SIGNATURE, public)
                self.assertIn(PRIVATE if protocol in ('chat_completions', 'anthropic', 'gemini') else SIGNATURE, json.dumps(raw))

    def test_sse_comments_crlf_multiline_and_cr_only_terminal(self):
        wire = b': keepalive\r\nevent: example\r\ndata: {"a":\r\ndata: 1}\r\n\r\n'
        self.assertEqual(list(streaming.frames(wire[i:i + 1] for i in range(len(wire)))), [('example', '{"a":\n1}')])
        self.assertEqual(list(streaming.frames([b'data: [DONE]\r\r'])), [('', '[DONE]')])
        self.assertEqual(list(streaming.frames([b'\xef', b'\xbb\xbfdata: {}\n\n'])), [('', '{}')])

    def test_disconnect_or_incomplete_arguments_never_returns_a_tool(self):
        for wire in (protocol_wire('chat_completions').replace(b'data: [DONE]\n\n', b''),
                     protocol_wire('chat_completions').replace(b'[\\"fixture\\"]}', b'['),
                     b'data: {"choices":[]}', b'data: {"choices":[]}\n\n\xe4'):
            with self.subTest(wire=wire[-30:]), self.assertRaises(ValueError):
                streaming.assemble('chat_completions', [wire])
        cut = event({'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call', 'function': {'name': 'inspect_items', 'arguments': '{'}}]}, 'finish_reason': 'tool_calls'}]}) + b'data: [DONE]\n\n'
        with self.assertRaisesRegex(ValueError, 'invalid_tool_arguments'):
            streaming.assemble('chat_completions', [cut])

    def test_failure_terminal_limits_and_cancellation_are_explicit(self):
        for protocol, wire in [('responses', event({'type': 'response.incomplete'})),
                               ('anthropic', event({'type': 'error', 'error': {'message': PRIVATE}})),
                               ('gemini', event({'candidates': [{'finishReason': 'MAX_TOKENS'}]}))]:
            with self.subTest(protocol=protocol), self.assertRaises(ValueError):
                streaming.assemble(protocol, [wire])
        with patch('diskpilot.streaming.MAX_WIRE_BYTES', 20), self.assertRaisesRegex(ValueError, 'response_budget'):
            streaming.assemble('chat_completions', [protocol_wire('chat_completions')])
        with patch('diskpilot.streaming.MAX_FRAMES', 1), self.assertRaisesRegex(ValueError, 'response_budget'):
            streaming.assemble('chat_completions', [protocol_wire('chat_completions')])
        cancel, closed = threading.Event(), []
        def chunks():
            try:
                yield event({'choices': [{'delta': {'content': '公开'}}]})
                cancel.set()
                yield event({'choices': [{'delta': {'content': '不应读取'}}]})
            finally:
                closed.append(True)
        with self.assertRaisesRegex(ValueError, 'stream_cancelled'):
            streaming.assemble('chat_completions', chunks(), cancel=cancel)
        self.assertEqual(closed, [True])

    def test_ambiguous_gemini_argument_fragments_are_rejected(self):
        frames = [event({'candidates': [{'content': {'parts': [{'functionCall': {'id': 'x', 'name': 'inspect_items', 'args': {'ids': ['a']}}}]}}]}),
                  event({'candidates': [{'content': {'parts': [{'functionCall': {'id': 'x', 'args': {'ids': ['b']}}}]}, 'finishReason': 'STOP'}]})]
        with self.assertRaisesRegex(ValueError, 'invalid_tool_arguments'):
            streaming.assemble('gemini', frames)

    def test_gemini_complete_calls_without_ids_are_distinct_across_frames(self):
        calls = [{'name': 'first_tool', 'args': {'id': 'A'}},
                 {'name': 'second_tool', 'args': {'id': 'B'}}]
        for same_name in (False, True):
            with self.subTest(same_name=same_name):
                expected = copy.deepcopy(calls)
                if same_name:
                    expected[1] = copy.deepcopy(expected[0])
                wire = [event({'candidates':[{'content':{'parts':[{'functionCall': call}]}}]}) for call in expected]
                wire.append(event({'candidates':[{'finishReason':'STOP'}]}))
                raw = streaming.assemble('gemini', wire)
                self.assertEqual(raw['candidates'][0]['content']['parts'], [{'functionCall': call} for call in expected])
                self.assertTrue(all('id' not in part['functionCall'] for part in raw['candidates'][0]['content']['parts']))

    def test_gemini_identified_fragments_still_merge_but_anonymous_partial_json_does_not(self):
        wire = [event({'candidates':[{'content':{'parts':[{'functionCall':{'id':'call-1', 'name':'inspect_items', 'args':'{"ids":'}}]}}]}),
                event({'candidates':[{'content':{'parts':[{'functionCall':{'id':'call-1', 'args':'["a"]}'}}]}, 'finishReason':'STOP'}]})]
        raw = streaming.assemble('gemini', wire)
        self.assertEqual(raw['candidates'][0]['content']['parts'],
                         [{'functionCall': {'id':'call-1', 'name':'inspect_items', 'args': {'ids':['a']}}}])
        for call in ({'name':'inspect_items', 'args':'{"ids":'}, {'args': {'ids':['a']}}):
            with self.subTest(call=call), self.assertRaisesRegex(ValueError, 'invalid_tool_arguments'):
                streaming.assemble('gemini', [event({'candidates':[{'content':{'parts':[{'functionCall':call}]}, 'finishReason':'STOP'}]})])

    def test_gemini_signatures_preserve_text_and_empty_terminal_part_boundaries(self):
        parts = [{'text':'first', 'thoughtSignature':'signature-A'},
                 {'text':'second', 'thoughtSignature':'signature-B'},
                 {'text':'unsigned'}, {'text':'', 'thoughtSignature':'signature-terminal'}]
        wire = [event({'candidates':[{'content':{'parts':[part]}}]}) for part in parts]
        wire.append(event({'candidates':[{'finishReason':'STOP'}]}))
        previews = []
        raw = streaming.assemble('gemini', wire, on_preview=lambda text,pending: previews.append(text))
        self.assertEqual(raw['candidates'][0]['content']['parts'], parts)
        self.assertEqual(previews[-1], 'firstsecondunsigned')
        self.assertFalse(any('signature-' in text for text in previews))

    def test_gemini_repeated_native_call_cannot_overwrite_an_existing_signature(self):
        wire = [event({'candidates':[{'content':{'parts':[{'functionCall':{'id':'call-1', 'name':'inspect_items', 'args':{}},
                                                              'thoughtSignature': signature}]}}]})
                for signature in ('signature-A', 'signature-B')]
        wire.append(event({'candidates':[{'finishReason':'STOP'}]}))
        with self.assertRaisesRegex(ValueError, 'invalid_stream'):
            streaming.assemble('gemini', wire)

    def test_gemini_anonymous_calls_keep_global_call_budget(self):
        wire = [event({'candidates':[{'content':{'parts':[{'functionCall':{'name':'inspect_items', 'args':{}}}]}}]})
                for _ in range(streaming.MAX_CALLS + 1)]
        wire.append(event({'candidates':[{'finishReason':'STOP'}]}))
        with self.assertRaisesRegex(ValueError, 'response_budget'):
            streaming.assemble('gemini', wire)


class AssistantStreamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def assistant(self, protocol, transport):
        value = ModelAssistant(self.folder / protocol, transport=transport)
        value._windows = False
        value.save_settings({'provider': 'custom', 'protocol': protocol, 'base_url': 'http://127.0.0.1:12345/v1', 'model': 'fixture', 'api_key': 'abca'})
        return value

    def test_all_four_streams_normalize_tools_usage_and_private_native_state(self):
        for protocol in ('chat_completions', 'responses', 'anthropic', 'gemini'):
            with self.subTest(protocol=protocol):
                value = self.assistant(protocol, lambda *args: iter([protocol_wire(protocol)]))
                events = []
                value.stream_hook = events.append
                response = value.agent_turn([{'role': 'user', 'content': '调查'}], TOOLS)
                self.assertEqual(response['content'], '公开说明。')
                self.assertEqual(json.loads(response['tool_calls'][0]['function']['arguments']), {'ids': ['fixture']})
                self.assertTrue(response['_usage'])
                self.assertIn('_continuation', response)
                self.assertNotIn(PRIVATE, json.dumps(events))
                self.assertNotIn(SIGNATURE, json.dumps(events))
                self.assertEqual(events[0]['kind'], 'turn_start')
                self.assertEqual(events[-1]['status'], 'completed')

    def test_gemini_anonymous_calls_roundtrip_without_invented_native_ids_or_lost_signatures(self):
        expected = [{'functionCall':{'name':'inspect_items', 'args':{'ids':[ident]}},
                     'thoughtSignature': 'opaque-' + ident} for ident in ('A', 'B')]
        sent = []
        def peer(url, payload, headers, timeout):
            sent.append(copy.deepcopy(payload))
            if len(sent) == 1:
                return iter([event({'candidates':[{'content':{'parts':[part]}}]}) for part in expected]
                            + [event({'candidates':[{'finishReason':'STOP'}]})])
            self.assertEqual(payload['contents'][1]['parts'], expected)
            responses = payload['contents'][2]['parts']
            self.assertEqual(len(responses), 2)
            self.assertTrue(all('id' not in part['functionResponse'] for part in responses))
            return {'candidates':[{'content':{'role':'model', 'parts':[{'text':'完成。'}]}, 'finishReason':'STOP'}]}
        value = self.assistant('gemini', peer)
        messages = [{'role':'user', 'content':'调查'}]
        first = value.agent_turn(messages, TOOLS)
        calls = first['tool_calls']
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]['id'], calls[1]['id'])
        messages += [first] + [{'role':'tool', 'tool_call_id':call['id'], 'content':'{"ok":true}'} for call in calls]
        self.assertEqual(value.agent_turn(messages, TOOLS)['content'], '完成。')

    def test_split_key_is_never_previewed_even_when_key_last_character_matches_prefix(self):
        def chunks():
            for content in ('公开说明：', 'ab', 'ca', '。'):
                yield event({'choices': [{'delta': {'content': content}}]})
            yield event({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})
            yield b'data: [DONE]\n\n'
        value = self.assistant('chat_completions', lambda *args: chunks())
        events = []
        value.stream_hook = events.append
        with patch('diskpilot.llm.time.monotonic', side_effect=range(1, 100)):
            response = value.agent_turn([{'role': 'user', 'content': '调查'}], TOOLS)
        self.assertNotIn('abca', json.dumps(events))
        self.assertFalse(any(row.get('text', '').endswith('ab') for row in events))
        self.assertIn('[REDACTED]', response['content'])

    def test_json_fallback_is_explicit_and_deltas_are_not_fabricated(self):
        value = self.assistant('chat_completions', lambda *args: {'choices': [{'message': {'role': 'assistant', 'content': '完整JSON'}}]})
        events = []
        value.stream_hook = events.append
        value.agent_turn([{'role': 'user', 'content': '调查'}], TOOLS)
        self.assertEqual(events[-1]['delivery'], 'json_fallback')
        self.assertFalse(any(row['kind'] == 'stream_delta' and row.get('text') for row in events))

    def test_save_then_failed_probe_does_not_rollback_or_clear_key(self):
        value = self.assistant('chat_completions', lambda *args: (_ for _ in ()).throw(ValueError('provider_auth')))
        value.save_settings({'model': 'saved-fixture', 'stream': False})
        before_file = value._file.read_bytes()
        result = value.test_connection()
        self.assertFalse(result['passed'])
        self.assertEqual(value.public_settings()['model'], 'saved-fixture')
        self.assertFalse(value.public_settings()['stream'])
        self.assertTrue(value.public_settings()['has_key'])
        self.assertEqual(value._file.read_bytes(), before_file)
        self.assertEqual(value._api_key, 'abca')
        with self.assertRaises(ValueError):
            value.save_settings({'base_url': 'https://different.example/v1'})

    def test_delta_is_transient_while_trace_retains_bounded_final_response(self):
        value = self.assistant('chat_completions', lambda *args: iter([protocol_wire('chat_completions')]))
        trace = DeveloperTrace(self.folder, lambda text: value.clean_text(text, limit=1024 * 1024))
        self.addCleanup(trace.close)
        trace.update(enabled=True)
        value.trace_hook = trace.record
        task_ids = []
        value.stream_hook = lambda event: task_ids.append(trace.current_task_id())
        with trace.context('task-stream'):
            value.agent_turn([{'role': 'user', 'content': '调查'}], TOOLS)
        page = trace.page()
        self.assertLessEqual(page['total'], 3)
        self.assertFalse(any(row['kind'] == 'stream_delta' for row in page['entries']))
        self.assertEqual(set(task_ids), {'task-stream'})
        self.assertNotIn(PRIVATE, json.dumps(page))

    def test_generic_reasoning_fields_are_private_but_reasoning_config_is_visible(self):
        wire = event({'choices': [{'delta': {'reasoning': PRIVATE, 'reasoning_details': [{'signature': SIGNATURE}], 'content': '公开内容。'}}]})
        wire += event({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}) + b'data: [DONE]\n\n'
        value = self.assistant('chat_completions', lambda *args: [wire])
        captured, previews = [], []
        value.trace_hook, value.stream_hook = captured.append, previews.append
        result = value.agent_turn([{'role': 'user', 'content': '调查'}], TOOLS)
        self.assertIn(PRIVATE, json.dumps(result['_continuation']))
        self.assertNotIn(PRIVATE, json.dumps(captured + previews))
        self.assertNotIn(SIGNATURE, json.dumps(captured + previews))

    def test_probe_forces_nonstream_and_failed_test_keeps_persisted_ciphertext(self):
        requests = []
        def fail(url, payload, headers, timeout):
            requests.append(payload)
            raise ValueError('provider_auth')
        value = self.assistant('chat_completions', fail)
        value._windows = True
        with patch('diskpilot.llm._dpapi', return_value=b'fixture-ciphertext-only'):
            value.save_settings({'stream': True, 'model': 'persisted-fixture'})
        before = value._file.read_bytes()
        self.assertFalse(value.test_connection()['passed'])
        self.assertFalse(requests[0]['stream'])
        self.assertTrue(value.public_settings()['stream'])
        self.assertTrue(value.public_settings()['has_key'])
        self.assertIn('key_dpapi', json.loads(before))
        self.assertEqual(value._file.read_bytes(), before)
        self.assertNotIn('abca', before.decode())


class HttpStreamingTests(unittest.TestCase):
    def test_all_four_real_http_routes_deliver_preview_before_terminal(self):
        current = {}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                current['request'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                current['path'] = self.path
                wire = protocol_wire(current['protocol'])
                split = wire.rfind(b'\n\n', 0, len(wire) - 2) + 2
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(wire[:split])
                self.wfile.flush()
                current['release'].wait(2)
                current['terminal_sent'] = True
                self.wfile.write(wire[split:])
                self.wfile.flush()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                for protocol in ('chat_completions', 'responses', 'anthropic', 'gemini'):
                    with self.subTest(protocol=protocol):
                        current.clear()
                        current.update(protocol=protocol, release=threading.Event(), terminal_sent=False, preview_early=False)
                        value = ModelAssistant(Path(temporary) / protocol)
                        value.save_settings({'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'fixture', 'protocol': protocol})
                        def hook(event):
                            if event['kind'] == 'stream_delta' and event.get('text'):
                                current['preview_early'] |= not current['terminal_sent']
                                current['release'].set()
                        value.stream_hook = hook
                        result = value.agent_turn([{'role': 'user', 'content': '只读'}], TOOLS)
                        self.assertTrue(current['preview_early'])
                        self.assertEqual(result['content'], '公开说明。')
                        self.assertEqual(json.loads(result['tool_calls'][0]['function']['arguments']), {'ids': ['fixture']})
                        if protocol == 'gemini':
                            self.assertTrue(current['path'].endswith(':streamGenerateContent?alt=sse'))
                            self.assertNotIn('stream', current['request'])
                        else:
                            self.assertTrue(current['request']['stream'])
        finally:
            current.get('release', threading.Event()).set()
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    def test_keepalive_stream_has_wall_clock_budget_not_only_byte_budget(self):
        class Response:
            fp = None
            closed = False
            def read1(self, size):
                time.sleep(.01)
                return b': ping\n\n'
            def close(self):
                self.closed = True
        response = Response()
        with patch('diskpilot.llm.MAX_STREAM_SECONDS', .03):
            stream = _HTTPStream(response)
        with self.assertRaisesRegex(ValueError, 'stream_timeout'):
            streaming.assemble('chat_completions', stream)
        self.assertTrue(response.closed)

    def test_actual_http_delivers_preview_before_completion_and_cancel_interrupts_read(self):
        first_frame, release, requested = threading.Event(), threading.Event(), []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                requested.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                try:
                    self.wfile.write(event({'choices': [{'delta': {'content': '正在只读调查。'}}]}))
                    self.wfile.flush()
                    first_frame.set()
                    release.wait(3)
                    self.wfile.write(event({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}) + b'data: [DONE]\n\n')
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                value = ModelAssistant(Path(temporary))
                value.save_settings({'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'fixture'})
                cancel, previewed, finished = threading.Event(), threading.Event(), threading.Event()
                events, errors = [], []
                def hook(event):
                    events.append(event)
                    if event['kind'] == 'stream_delta' and event.get('text'):
                        previewed.set()
                value.stream_hook = hook
                def run():
                    try:
                        value.agent_turn([{'role': 'user', 'content': '只读'}], TOOLS, cancel=cancel)
                    except ValueError as exc:
                        errors.append(str(exc))
                    finally:
                        finished.set()
                worker = threading.Thread(target=run)
                worker.start()
                self.assertTrue(first_frame.wait(2))
                self.assertTrue(previewed.wait(2))
                self.assertFalse(finished.is_set())
                self.assertTrue(requested[0]['stream'])
                cancel.set()
                self.assertTrue(finished.wait(2))
                self.assertEqual(errors, ['stream_cancelled'])
                self.assertEqual(events[-1]['status'], 'cancelled')
                worker.join(timeout=2)
        finally:
            release.set()
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == '__main__':
    unittest.main()
