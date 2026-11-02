"""Owned local v0.13 UI fixture with many small SSE frames.

The CLI and lifecycle are inherited from serve_v0100_fixture. Both source and
native profiles must be new/empty. Only a loopback peer is used, with no key;
creating <data-dir>/stop-fixture stops it and checks the disposable file tree.
wire-fixture.json contains counts only, never model/private text or arguments.
After seeding, submit feedback containing 验证长摘要结论 for one findings-only
round with a long summary and valid modules, reusing the existing observations.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import threading
import time

import serve_v0120_fixture as previous


fixture = previous.fixture
_original_answer = previous.previous.answer
_original_delta = fixture.stream_peer._delta
_lock = threading.Lock()
_stats_path = None
_stats = dict(local_fixture=True, external_provider_used=False,
              real_credentials_used=False, responses=0, injected_frames=0,
              injected_wire_bytes=0, advice_batch_sizes=[], response_counts=[])
FRAME_COUNT = 2100
REASONING_CHARACTERS = 2100
LONG_FINDINGS_MARKER = '验证长摘要结论'
LONG_FINDINGS_SUMMARY = (
    '本轮重新核对了既有观察与九条清理建议，没有重新扫描磁盘，也没有对实际文件执行任何删除。'
    '旧安装包仍倾向清理，但需要先确认是否还要保留离线副本；重要资料继续保留，不随上级目录一并处理。'
    '已跳过和用途不明的内容各自保留原状态，评分只帮助比较去留倾向，并不是安全保证或删除授权。'
    '目录中的保留例外仍然有效，下一步应回到清单检查勾选，再查看具体操作汇总；当前没有批准任何清理动作。'
    '这段超过一百六十字的结论和合法模块应同时完整保存，可在分析详情里阅读最后一句：长摘要结论验收完成。'
)


def _save_stats():
    if _stats_path is None:
        return
    # The inherited main has already admitted and created this owned profile.
    # A replacement avoids readers observing a half-written JSON document.
    temporary = _stats_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(_stats, indent=2), encoding='utf-8')
    temporary.replace(_stats_path)


def _legacy_view(payload):
    """Adapt the old fake's phase detector, without changing the real request.

    A native parallel assistant has two trailing tool messages. The previous
    fake assumes messages[-2] is the assistant. Reorder only this private copy
    so every tool result is still available to its observation collector and
    its tail is assistant + final result. This is never sent over the wire.
    """
    view = copy.deepcopy(payload)
    messages = view.get('messages', [])
    boundary = len(messages)
    while boundary and messages[boundary - 1].get('role') == 'tool':
        boundary -= 1
    if len(messages) - boundary > 1 and boundary and messages[boundary - 1].get('role') == 'assistant':
        assistant = messages[boundary - 1]
        view['messages'] = messages[:boundary - 1] + messages[boundary:-1] + [assistant, messages[-1]]
    return view


def answer(payload):
    context = fixture.stream_peer.context(payload)
    feedback = context.get('user_feedback', [])
    latest = feedback[-1] if isinstance(feedback, list) and feedback else None
    if (isinstance(latest, dict) and latest.get('role') == 'user'
            and LONG_FINDINGS_MARKER in str(latest.get('content', ''))):
        value = fixture.basic.tool('present_findings', dict(summary=LONG_FINDINGS_SUMMARY,
            briefing=copy.deepcopy(previous.previous.BRIEFING), needs_input=False))
        value['choices'][0]['message']['content'] = LONG_FINDINGS_SUMMARY
        return value
    value = _original_answer(_legacy_view(payload))
    message = value['choices'][0]['message']
    calls = []
    for call in message.get('tool_calls') or []:
        if call.get('function', {}).get('name') != 'record_advice':
            calls.append(call)
            continue
        args = json.loads(call['function']['arguments'])
        items = args.get('items', [])
        for start in range(0, len(items), 6):
            part = copy.deepcopy(call)
            part['id'] = call['id'] + '_part_' + str(start // 6 + 1)
            part['function']['arguments'] = json.dumps(dict(args, items=items[start:start + 6]), ensure_ascii=False)
            calls.append(part)
        with _lock:
            _stats['advice_batch_sizes'].append([len(items[start:start + 6]) for start in range(0, len(items), 6)])
            _stats['advice_batch_sizes'] = _stats['advice_batch_sizes'][-100:]
            _save_stats()
    if message.get('tool_calls'):
        message['tool_calls'] = calls
    return value


def _delta(handler, value):
    """Inject once at the first role delta; keep the old writer unchanged."""
    if value.get('role') == 'assistant' and not getattr(handler, '_v0130_injected', False):
        handler._v0130_injected = True
        with _lock:
            response_id = _stats['responses'] + 1
            _stats['responses'] = response_id
        # Keep the original role/canary delta intact. Empty tiny deltas pad the
        # frame count so total semantic reasoning, including it, is 2100 chars.
        original_characters = len(value.get('reasoning_content') or '')
        additional = max(0, REASONING_CHARACTERS - original_characters)
        wire_bytes = 0
        for index in range(FRAME_COUNT):
            envelope = dict(id=f'chatcmpl-owned-wire-fixture-{response_id:06d}',
                object='chat.completion.chunk', created=int(time.time()),
                model='local-acceptance-fixture',
                system_fingerprint='fp_owned_official_shape_wire_budget_fixture', service_tier='default',
                choices=[dict(index=0, delta=dict(reasoning_content='x' if index < additional else ''),
                              finish_reason=None)])
            raw = ('data: ' + json.dumps(envelope, ensure_ascii=False) + '\n\n').encode('utf-8')
            handler.wfile.write(raw)
            wire_bytes += len(raw)
        handler.wfile.flush()
        with _lock:
            _stats['injected_frames'] += FRAME_COUNT
            _stats['injected_wire_bytes'] += wire_bytes
            _stats['response_counts'].append(dict(response=response_id, frames=FRAME_COUNT,
                injected_wire_bytes=wire_bytes,
                semantic_reasoning_characters=original_characters + additional,
                exceeds_legacy_512_kib=wire_bytes > 512 * 1024))
            _stats['response_counts'] = _stats['response_counts'][-100:]
            _save_stats()
    return _original_delta(handler, value)


def main():
    global _stats_path
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--data-dir', type=Path, required=True)
    args, _ = parser.parse_known_args()
    _stats_path = args.data_dir.absolute() / 'wire-fixture.json'
    fixture.create_tree = previous.create_tree
    fixture.answer = answer
    fixture.stream_peer._delta = _delta
    fixture.SEED_GOAL = '验证0.13公开状态留存、9项倾向与本地重复查询；所有隔离文件保留，仅允许模拟。'
    fixture.main()


if __name__ == '__main__':
    main()
