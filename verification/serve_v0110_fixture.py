"""Local-only rating hierarchy, modular findings and long-legacy UI fixture.

Reuses the disposable real-file fixture and local SSE peer. No real provider,
user key, execution or Windows control is used. See serve_v0100_fixture CLI.
Use a task goal containing 旧长摘要兼容验收 to exercise a legacy long summary.
"""
from __future__ import annotations

import json

import serve_v0100_fixture as fixture


_original_answer = fixture.answer
LEGACY_MARKER = '旧长摘要兼容验收'
LEGACY_SUMMARY = ('这是旧版保存的完整调查说明，包含用途判断、保留条件和检查范围；'
                  '文件保持原样，个人资料由用户决定。') * 7
LONG_PUBLIC = ('正在核对所选范围中的文件用途、清理倾向及保留例外。'
               '这段公开回复用于检查较长内容的收起与展开，内容应完整保留。\n') * 12
BRIEFING = {
    'highlights': ['旧安装包 4/5，优先确认是否还需离线副本。', '重要资料 1/5，继续保留。'],
    'cautions': ['父目录含保留项，请选择具体文件。'],
    'coverage': ['本轮仅核对所选目录，其他位置尚未检查。'],
}


def answer(payload):
    value = _original_answer(payload)
    context = fixture.stream_peer.context(payload)
    legacy = LEGACY_MARKER in str(context.get('goal', ''))
    message = value['choices'][0]['message']
    for call in message.get('tool_calls') or []:
        function = call['function']
        if function['name'] not in ('propose_plan', 'present_findings'):
            continue
        args = json.loads(function['arguments'])
        if function['name'] == 'propose_plan':
            seen = {row['id'] for row in args['items']}
            for row in fixture._observed(payload):
                if row['id'] in seen or not row.get('name', '').startswith('缓存副本-'):
                    continue
                args['items'].append(dict(id=row['id'], action='delete',
                    reason='沿用目录对可再生成副本的倾向，按文件分别处理以保留例外。',
                    impact='需要时会重新生成；本次只记录模拟结果。'))
                seen.add(row['id'])
                if len(args['items']) == 5:
                    break
        if legacy:
            args['summary'] = LEGACY_SUMMARY
            args.pop('briefing', None)
        else:
            args['summary'] = '优先核对旧安装包；重要资料继续保留。'
            args['briefing'] = BRIEFING
        function['arguments'] = json.dumps(args, ensure_ascii=False)
        # Intentionally verbose public text tests the UI boundary. This does
        # not teach real models to produce long prose or fake token savings.
        message['content'] = LONG_PUBLIC
    return value


if __name__ == '__main__':
    fixture.answer = answer
    fixture.SEED_GOAL = '验证评分优先与分模块建议；所有隔离文件保留，等待用户审阅。'
    fixture.main()
