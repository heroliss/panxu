"""Owned local fixture for compact context, impact ratings and calm surfaces.

Inherits the disposable file tree, local SSE peer and CLI from v0.13. Only the
fake's declared advice is changed; no user model, key or system action is used.
Create <data-dir>/stop-fixture to finish and verify the owned file hashes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import serve_v0130_fixture as previous


fixture = previous.fixture
_original_advice = fixture._advice
_original_answer = previous.answer
_audit_path = None
_requests = []
IMPACTS = {
    fixture.PARENT: (1, '需要时重新生成缓存，可能多等片刻。'),
    fixture.HIGH: (2, '以后需要安装时，要重新下载这份安装包。'),
    fixture.NEUTRAL: (3, '离线使用前可能需要重新准备副本。'),
    fixture.LOW: (4, '现有工作流程可能依赖这份副本，需要先核对替代位置。'),
    fixture.KEEP: (5, '这份资料可能唯一；删除会失去内容，恢复尚未验证。'),
}


def advice(row):
    value = _original_advice(row)
    if value is not None:
        score, text = IMPACTS.get(row['name'], (None, '现有线索不足，具体影响仍待确认。'))
        value.update(impact_rating=score, impact=text)
    return value


def answer(payload):
    context = fixture.stream_peer.context(payload)
    content = next((message.get('content', '') for message in payload.get('messages', [])
                    if message.get('role') == 'user'), '')
    refs = context.get('facts', {})
    refs = refs.get('items', []) if isinstance(refs, dict) else []
    _requests.append(dict(context_characters=len(content), context_keys=sorted(context),
        no_initial_objects='initial_objects' not in context,
        no_internal_projection_marker='_prompt_projection' not in context,
        fact_reference_keys=[sorted(row) for row in refs],
        simulation_constant_repeated='simulation_only' in context))
    if _audit_path is not None:
        _audit_path.write_text(json.dumps(dict(local_fixture=True, external_provider_used=False,
            requests=_requests), ensure_ascii=False, indent=2), encoding='utf-8')
    feedback = context.get('user_feedback', [])
    latest = feedback[-1] if isinstance(feedback, list) and feedback else None
    # v0.16 intentionally omits the redundant per-row role="user" shell. The
    # inherited v0.13 fake must not require that removed implementation detail.
    if isinstance(latest, dict) and previous.LONG_FINDINGS_MARKER in str(latest.get('content', '')):
        value = fixture.basic.tool('present_findings', dict(summary=previous.LONG_FINDINGS_SUMMARY,
            briefing=dict(highlights=['既有评分及独立影响保留。'], cautions=['个人资料和保留例外仍由用户决定。'],
                          coverage=['只使用已保存观察，没有新增扫描或实际处理。']), needs_input=False))
        value['choices'][0]['message']['content'] = previous.LONG_FINDINGS_SUMMARY
        return value
    return _original_answer(payload)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--data-dir', type=Path, required=True)
    args, _ = parser.parse_known_args()
    _audit_path = args.data_dir.absolute() / 'request-audit.json'
    fixture._advice = advice
    previous.answer = answer
    previous.main()
