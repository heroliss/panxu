"""Bounded SSE decoding and native response assembly. No partial tool execution.

Private reasoning/signatures survive only in the returned native object; the
preview callback receives public text and a tool-pending flag, never raw events.
"""
from __future__ import annotations

import codecs
import copy
import json


MAX_WIRE_BYTES = 512 * 1024
MAX_EVENT_BYTES = 128 * 1024
MAX_FRAMES = 4096
MAX_ASSEMBLED_BYTES = 256 * 1024
MAX_TEXT = 64000
MAX_ARGUMENTS = 16000
MAX_CALLS = 8


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise ValueError('stream_cancelled')


def frames(chunks, *, cancel=None):
    """Incremental UTF-8, CR/LF/CRLF and multi-line SSE data, with hard budgets."""
    decoder = codecs.getincrementaldecoder('utf-8-sig')('strict')
    buffer, lines, event, count, wire, event_bytes = '', [], '', 0, 0, 0
    try:
        for chunk in chunks:
            _check(cancel)
            if not isinstance(chunk, bytes):
                raise ValueError('invalid_stream')
            wire += len(chunk)
            if wire > MAX_WIRE_BYTES:
                raise ValueError('response_budget')
            buffer += decoder.decode(chunk)
            while True:
                positions = [i for i in (buffer.find('\r'), buffer.find('\n')) if i >= 0]
                if not positions:
                    break
                end = min(positions)
                if buffer[end] == '\r' and end + 1 == len(buffer):
                    break
                skip = 2 if buffer[end:end + 2] == '\r\n' else 1
                line, buffer = buffer[:end], buffer[end + skip:]
                event_bytes += len(line.encode()) + 1
                if event_bytes > MAX_EVENT_BYTES:
                    raise ValueError('response_budget')
                if not line:
                    if lines:
                        count += 1
                        if count > MAX_FRAMES:
                            raise ValueError('response_budget')
                        yield event, '\n'.join(lines)
                    lines, event, event_bytes = [], '', 0
                elif line.startswith('data:'):
                    lines.append(line[5:].removeprefix(' '))
                elif line.startswith('event:'):
                    event = line[6:].removeprefix(' ')
            if len(buffer.encode()) + event_bytes > MAX_EVENT_BYTES:
                raise ValueError('response_budget')
        _check(cancel)
        decoder.decode(b'', final=True)
        if buffer == '\r' and lines:
            count += 1
            if count > MAX_FRAMES:
                raise ValueError('response_budget')
            yield event, '\n'.join(lines)
            buffer, lines = '', []
        # No dispatch of an unterminated event: a disconnect is not completion.
        if buffer.strip() or lines:
            raise ValueError('stream_incomplete')
    except UnicodeError:
        raise ValueError('invalid_stream') from None


def _index(value, maximum=32):
    if type(value) is not int or not 0 <= value < maximum:
        raise ValueError('invalid_stream')
    return value


def _append(value, addition, limit=MAX_TEXT):
    if addition is None:
        return value
    if not isinstance(addition, str) or len(value) + len(addition) > limit:
        raise ValueError('response_budget' if isinstance(addition, str) else 'invalid_stream')
    return value + addition


def _arguments(value):
    if not isinstance(value, str) or len(value) > MAX_ARGUMENTS:
        raise ValueError('invalid_tool_arguments')
    try:
        parsed = json.loads(value)
    except ValueError:
        raise ValueError('invalid_tool_arguments') from None
    if not isinstance(parsed, dict):
        raise ValueError('invalid_tool_arguments')
    return parsed


def _merge_object(target, addition):
    """Gemini structured argument chunks: merge only unambiguous values."""
    if not isinstance(addition, dict):
        raise ValueError('invalid_tool_arguments')
    for key, value in addition.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _merge_object(target[key], value)
        elif key not in target or target[key] == value:
            target[key] = copy.deepcopy(value)
        else:
            raise ValueError('invalid_tool_arguments')


def assemble(protocol, chunks, *, on_preview=None, cancel=None):
    raw, public, pending, complete = {}, '', False, False
    chat = {'role': 'assistant', 'content': ''}
    calls, outputs, blocks, argument_parts, open_blocks = {}, {}, {}, {}, set()
    gemini_parts, gemini_calls = [], {}
    usage = {}

    def preview(text=None, tool=False):
        nonlocal public, pending
        if text is not None:
            public = _append(public, text)
        pending |= tool
        if on_preview:
            on_preview(public, pending)

    try:
        for event, data in frames(chunks, cancel=cancel):
            _check(cancel)
            if data == '[DONE]':
                if protocol != 'chat_completions' or raw.get('finish_reason') not in ('stop', 'tool_calls'):
                    raise ValueError('stream_incomplete')
                complete = True
                break
            try:
                value = json.loads(data)
            except (ValueError, UnicodeError):
                raise ValueError('invalid_stream') from None
            if not isinstance(value, dict) or 'error' in value:
                raise ValueError('provider_unavailable')
            kind = value.get('type') or event
            if protocol == 'chat_completions':
                if isinstance(value.get('usage'), dict):
                    usage.update(value['usage'])
                choices = value.get('choices', [])
                if not isinstance(choices, list):
                    raise ValueError('invalid_stream')
                for choice in choices:
                    if not isinstance(choice, dict) or choice.get('index', 0) != 0:
                        raise ValueError('invalid_stream')
                    delta = choice.get('delta') or {}
                    if not isinstance(delta, dict):
                        raise ValueError('invalid_stream')
                    for key in ('content', 'reasoning_content', 'reasoning'):
                        if key in delta:
                            chat[key] = _append(chat.get(key, ''), delta[key])
                            if key == 'content' and delta[key]:
                                preview(delta[key])
                    for key in ('signature', 'thought_signature', 'reasoning_details'):
                        if key in delta:
                            if isinstance(delta[key], str):
                                chat[key] = _append(chat.get(key, ''), delta[key])
                            elif isinstance(delta[key], list):
                                chat.setdefault(key, []).extend(copy.deepcopy(delta[key]))
                            else:
                                raise ValueError('invalid_stream')
                    for call in delta.get('tool_calls') or []:
                        index = _index(call.get('index'), MAX_CALLS)
                        entry = calls.setdefault(index, dict(id='', type='function', function=dict(name='', arguments='')))
                        for key in ('id',):
                            if call.get(key) and entry[key] != call[key]:
                                entry[key] = _append(entry[key], call[key], 100)
                        function = call.get('function') or {}
                        if function.get('name') and entry['function']['name'] != function['name']:
                            entry['function']['name'] = _append(entry['function']['name'], function['name'], 80)
                        if 'arguments' in function:
                            entry['function']['arguments'] = _append(entry['function']['arguments'], function['arguments'], MAX_ARGUMENTS)
                        preview(tool=True)
                    if choice.get('finish_reason') is not None:
                        raw['finish_reason'] = choice['finish_reason']
                        if raw['finish_reason'] not in ('stop', 'tool_calls'):
                            raise ValueError('stream_incomplete')
            elif protocol == 'responses':
                nested = value.get('response')
                if isinstance(nested, dict) and isinstance(nested.get('usage'), dict):
                    usage.update(nested['usage'])
                if kind in ('error', 'response.failed', 'response.incomplete'):
                    raise ValueError('stream_incomplete')
                if kind == 'response.output_item.added' or kind == 'response.output_item.done':
                    outputs[_index(value.get('output_index'))] = copy.deepcopy(value['item'])
                    if value['item'].get('type') == 'function_call':
                        preview(tool=True)
                elif kind == 'response.content_part.added':
                    index, part_index = _index(value.get('output_index')), _index(value.get('content_index'))
                    item = outputs.setdefault(index, dict(type='message', role='assistant', content=[]))
                    while len(item['content']) <= part_index:
                        item['content'].append(dict(type='output_text', text=''))
                    item['content'][part_index] = copy.deepcopy(value['part'])
                elif kind == 'response.output_text.delta':
                    index, part_index = _index(value.get('output_index')), _index(value.get('content_index'))
                    item = outputs.setdefault(index, dict(type='message', role='assistant', content=[]))
                    while len(item['content']) <= part_index:
                        item['content'].append(dict(type='output_text', text=''))
                    part = item['content'][part_index]
                    part['text'] = _append(part.get('text', ''), value.get('delta'))
                    preview(value.get('delta'))
                elif kind == 'response.function_call_arguments.delta':
                    item = outputs.setdefault(_index(value.get('output_index')), dict(type='function_call', arguments=''))
                    item['arguments'] = _append(item.get('arguments', ''), value.get('delta'), MAX_ARGUMENTS)
                    preview(tool=True)
                elif kind == 'response.function_call_arguments.done':
                    item = outputs.setdefault(_index(value.get('output_index')), dict(type='function_call'))
                    item['arguments'] = value.get('arguments', item.get('arguments', ''))
                elif kind == 'response.completed':
                    raw = copy.deepcopy(value.get('response', {}))
                    if raw.get('status', 'completed') != 'completed':
                        raise ValueError('stream_incomplete')
                    raw.setdefault('output', [outputs[i] for i in sorted(outputs)])
                    if usage:
                        raw['usage'] = copy.deepcopy(usage)
                    complete = True
                    break
            elif protocol == 'anthropic':
                if kind == 'message_start':
                    raw = copy.deepcopy(value['message'])
                    usage.update(raw.get('usage') or {})
                elif kind == 'content_block_start':
                    index = _index(value.get('index'))
                    if index in blocks:
                        raise ValueError('invalid_stream')
                    blocks[index] = copy.deepcopy(value['content_block'])
                    open_blocks.add(index)
                    if blocks[index].get('type') == 'tool_use':
                        preview(tool=True)
                elif kind == 'content_block_delta':
                    index = _index(value.get('index'))
                    if index not in open_blocks:
                        raise ValueError('invalid_stream')
                    block, delta = blocks[index], value['delta']
                    field = {'text_delta': 'text', 'thinking_delta': 'thinking', 'signature_delta': 'signature'}.get(delta.get('type'))
                    if field:
                        block[field] = _append(block.get(field, ''), delta.get(field))
                        if field == 'text' and block.get('type') == 'text':
                            preview(delta.get(field))
                    elif delta.get('type') == 'input_json_delta':
                        argument_parts[index] = _append(argument_parts.get(index, ''), delta.get('partial_json'), MAX_ARGUMENTS)
                        preview(tool=True)
                    else:
                        raise ValueError('invalid_stream')
                elif kind == 'content_block_stop':
                    index = _index(value.get('index'))
                    if index not in open_blocks:
                        raise ValueError('invalid_stream')
                    open_blocks.remove(index)
                    if index in argument_parts:
                        blocks[index]['input'] = _arguments(argument_parts[index])
                elif kind == 'message_delta':
                    raw.update(value.get('delta') or {})
                    usage.update(value.get('usage') or {})
                elif kind == 'message_stop':
                    if open_blocks or raw.get('stop_reason') not in ('end_turn', 'tool_use', 'stop_sequence'):
                        raise ValueError('stream_incomplete')
                    raw.update(content=[blocks[i] for i in sorted(blocks)], usage=usage)
                    complete = True
                    break
                elif kind == 'error':
                    raise ValueError('provider_unavailable')
            elif protocol == 'gemini':
                if isinstance(value.get('usageMetadata'), dict):
                    usage.update(value['usageMetadata'])
                for candidate in value.get('candidates', []):
                    if candidate.get('index', 0) != 0:
                        raise ValueError('invalid_stream')
                    for part in candidate.get('content', {}).get('parts', []):
                        if 'text' in part:
                            thought = part.get('thought') is True
                            # A signature belongs to its original part. Never
                            # move it onto concatenated text or overwrite it
                            # with the signature of a later part, including an
                            # empty terminal text part carrying only a signature.
                            previous = gemini_parts[-1] if gemini_parts else {}
                            if ('text' in previous and set(previous) <= {'text', 'thought'}
                                    and set(part) <= {'text', 'thought'}
                                    and (previous.get('thought') is True) == thought):
                                target = gemini_parts[-1]
                                target['text'] = _append(target['text'], part['text'])
                            else:
                                target = copy.deepcopy(part)
                                gemini_parts.append(target)
                            if not thought:
                                preview(part['text'])
                        elif 'functionCall' in part:
                            call = part['functionCall']
                            native_id = call.get('id')
                            if native_id is not None and (not isinstance(native_id, str) or len(native_id) > 100):
                                raise ValueError('invalid_tool_arguments')
                            if native_id:
                                ident = ('native', native_id)
                            else:
                                # Without a native ID there is no cross-chunk
                                # fragment identity. Each complete function part
                                # is a new call, even when name/args are equal.
                                # Internal grouping keys must never be replayed
                                # as IDs invented by the provider adapter.
                                if not isinstance(call.get('name'), str) or not call['name']:
                                    raise ValueError('invalid_tool_arguments')
                                ident = ('anonymous', len(gemini_calls))
                            if len(gemini_calls) >= MAX_CALLS and ident not in gemini_calls:
                                raise ValueError('response_budget')
                            if ident not in gemini_calls:
                                gemini_calls[ident] = dict(functionCall=dict(name=call.get('name', ''), args={}))
                                gemini_parts.append(gemini_calls[ident])
                            target = gemini_calls[ident]
                            function = target['functionCall']
                            if native_id:
                                function['id'] = native_id
                            if call.get('name'):
                                if function['name'] and function['name'] != call['name']:
                                    raise ValueError('invalid_tool_arguments')
                                function['name'] = call['name']
                            if isinstance(call.get('args', {}), str):
                                if native_id:
                                    argument_parts[ident] = _append(argument_parts.get(ident, ''), call['args'], MAX_ARGUMENTS)
                                else:
                                    # A complete JSON object can be accepted as
                                    # one compatibility part. Guessing which
                                    # anonymous call owns partial JSON cannot.
                                    function['args'] = _arguments(call['args'])
                            else:
                                _merge_object(function['args'], call.get('args', {}))
                            if 'thoughtSignature' in part:
                                if ('thoughtSignature' in target and target['thoughtSignature'] != part['thoughtSignature']):
                                    raise ValueError('invalid_stream')
                                target['thoughtSignature'] = part['thoughtSignature']
                            preview(tool=True)
                        else:
                            raise ValueError('invalid_stream')
                    if candidate.get('finishReason') is not None:
                        if candidate['finishReason'] != 'STOP':
                            raise ValueError('stream_incomplete')
                        raw['finishReason'] = candidate['finishReason']
                        complete = True
                if complete:
                    for ident, text in argument_parts.items():
                        gemini_calls[ident]['functionCall']['args'] = _arguments(text)
                    raw = dict(candidates=[dict(content=dict(role='model', parts=gemini_parts), finishReason='STOP')], usageMetadata=usage)
                    break
            else:
                raise ValueError('unsupported_protocol')
        _check(cancel)
        if not complete:
            raise ValueError('stream_incomplete')
        if protocol == 'chat_completions':
            chat['content'] = chat['content'] or None
            if calls:
                chat['tool_calls'] = [calls[i] for i in sorted(calls)]
                for call in chat['tool_calls']:
                    _arguments(call['function']['arguments'])
            raw = dict(choices=[dict(message=chat, finish_reason=raw.get('finish_reason'))], usage=usage)
        if len(json.dumps(raw, ensure_ascii=False).encode()) > MAX_ASSEMBLED_BYTES:
            raise ValueError('response_budget')
        return raw
    except (KeyError, TypeError, AttributeError, IndexError):
        raise ValueError('invalid_stream') from None
    finally:
        close = getattr(chunks, 'close', None)
        if callable(close):
            try:
                close()
            except OSError:
                pass
