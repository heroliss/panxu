"""Disposable v0.9 peer: genuine SSE, fragmented tools and controlled endings.

--hold-marker is optional. While the file exists, the middle public-text stage
waits for its removal, at most twenty seconds. No user file is changed by it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time

import serve_v050_fixture as fixture
from serve_v080_fixture import answer as base_answer, create_with_icons


FINAL_TEXT = '正在核对所选测试范围中的信息。公开说明会逐步到达，工具参数完整结束前不会执行。此轮仅取得只读证据，实际文件和系统设置均未改变。'
PRIVATE_CANARY = 'fixture-private-thinking-must-not-be-visible'
HOLD_MARKER = None
_hold_lock, _hold_identity, _hold_deadline = threading.Lock(), None, 0
original_writer = fixture.write_response


def context(payload):
    messages = payload.get('messages', [])
    try:
        value = json.loads(messages[1]['content'])
        return value if isinstance(value, dict) else {}
    except (ValueError, KeyError, IndexError, TypeError):
        return {}


def answer(payload):
    goal = context(payload).get('goal', '')
    if not goal.startswith('验证流式回复'):
        return base_answer(payload)
    if len(payload['messages']) == 2:
        return fixture.tool('collect_system_configuration', {'section': 'memory_power'})
    return fixture.tool('present_findings', {'summary': FINAL_TEXT, 'needs_input': False})


def _send(handler, value):
    handler.wfile.write(('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode())
    handler.wfile.flush()


def _delta(handler, value):
    _send(handler, {'choices': [{'index': 0, 'delta': value}]})


def _hold_for_capture():
    global _hold_identity, _hold_deadline
    if HOLD_MARKER is None:
        return
    try:
        info = HOLD_MARKER.stat()
    except OSError:
        return
    identity = info.st_dev, info.st_ino, info.st_mtime_ns
    with _hold_lock:
        if identity != _hold_identity:
            _hold_identity, _hold_deadline = identity, time.monotonic() + 20
        deadline = _hold_deadline
    # One marker instance has one twenty-second window across model turns.
    while HOLD_MARKER.is_file() and time.monotonic() < deadline:
        time.sleep(.1)


def write_response(handler, value, payload, status=200):
    if status != 200 or payload.get('stream') is not True:
        return original_writer(handler, value, payload, status)
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('Connection', 'close')
    handler.end_headers()
    handler.close_connection = True
    goal = str(context(payload).get('goal', ''))
    verification = goal.startswith('验证流式回复')
    message = value['choices'][0]['message']
    public = FINAL_TEXT if verification else (message.get('content') or '正在根据你的目标核对信息，稍后会说明用途、建议和影响。')
    count, interval = (12, .31) if verification else (4, .07)
    pieces = [public[len(public) * i // count:len(public) * (i + 1) // count] for i in range(count)]
    try:
        _delta(handler, {'role': 'assistant', 'reasoning_content': PRIVATE_CANARY})
        for index, piece in enumerate(pieces):
            _delta(handler, {'content': piece})
            if index == count // 2 and HOLD_MARKER is not None:
                _hold_for_capture()
            if index + 1 < count:
                time.sleep(interval)
        for index, call in enumerate(message.get('tool_calls') or []):
            function = call['function']
            name, arguments = function['name'], function['arguments']
            middle = max(1, len(name) // 2)
            _delta(handler, {'tool_calls': [{'index': index, 'id': call['id'], 'type': 'function',
                'function': {'name': name[:middle], 'arguments': ''}}]})
            time.sleep(.18 if verification else .02)
            _delta(handler, {'tool_calls': [{'index': index, 'function': {'name': name[middle:], 'arguments': ''}}]})
            slices = 3
            for part in range(slices):
                chunk = arguments[len(arguments) * part // slices:len(arguments) * (part + 1) // slices]
                _delta(handler, {'tool_calls': [{'index': index, 'function': {'arguments': chunk}}]})
                time.sleep(.2 if verification else .02)
        if verification:
            # A complete JSON argument buffer is still not a completed model
            # turn. The checker samples durable facts during this gap.
            time.sleep(1.0)
        if goal.startswith('验证流式回复-断连'):
            return  # deliberate EOF with no finish_reason / [DONE]
        if goal.startswith('验证流式回复-取消'):
            time.sleep(2)
        _send(handler, {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}]})
        _send(handler, {'choices': [], 'usage': {'prompt_tokens': 42, 'completion_tokens': len(public)}})
        handler.wfile.write(b'data: [DONE]\n\n')
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        pass  # expected cancellation; no response replay


def main():
    global HOLD_MARKER
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--hold-marker', type=Path)
    args, remaining = parser.parse_known_args()
    HOLD_MARKER = args.hold_marker.absolute() if args.hold_marker else None
    fixture.create_server = create_with_icons
    fixture.answer = answer
    fixture.write_response = write_response
    sys.argv = [sys.argv[0], *remaining]
    fixture.main()


if __name__ == '__main__':
    main()
