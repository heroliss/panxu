"""Disposable v0.6 UI fixture: guidance, a real question and host actions.

Uses only a local protocol peer and inherits unchanged-file shutdown checks.
"""
import json

import serve_v050_fixture as fixture


base_answer = fixture.answer


def answer(payload):
    definitions = {item['function']['name'] for item in payload.get('tools', [])}
    if 'connection_probe' in definitions:
        return base_answer(payload)
    messages = payload['messages']
    context = json.loads(messages[1]['content'])
    if context.get('last_result'):
        return fixture.tool('present_findings', {'summary': '已收到你直接选择处理的记录，并核对1项预演结果；实际文件未改变。', 'needs_input': False})
    answered = any(isinstance(row, dict) and row.get('answer') for row in context.get('user_feedback', []))
    if len(messages) == 2:
        return fixture.tool('list_items', {'limit': 20}) if answered else fixture.tool('list_skills', {'query': '缓存', 'limit': 3})
    previous = messages[-2]['tool_calls'][0]['function']['name']
    result = json.loads(messages[-1]['content'])
    if previous == 'list_skills':
        return fixture.tool('read_skill', {'skill_id': 'application-caches'})
    if previous == 'read_skill':
        return fixture.tool('list_items', {'limit': 20})
    if previous == 'list_items':
        return fixture.tool('inspect_items', {'ids': [row['id'] for row in result.get('items', [])[:20]]})
    observed = {}
    for message in messages:
        if message['role'] == 'tool':
            value = json.loads(message['content'])
            for row in value.get('items', []) if isinstance(value, dict) else []:
                if isinstance(row, dict) and 'kind' in row:
                    observed[row['id']] = row
    if previous == 'inspect_items':
        return fixture.tool('record_advice', {'items': [dict(id=row['id'], conclusion='review',
            purpose='你下载的测试安装包副本。', reason='已参考缓存指南；下载文件仍由你决定去留。',
            impact='可以先保留；测试保护下处理只记录预演。') for row in observed.values()]})
    if previous == 'record_advice' and not answered:
        return fixture.tool('ask_user', {'question': '这两份测试安装包，你更希望先怎么处理？',
            'options': [dict(id='keep', label='先保留，继续看其他内容', description='保留这两份，助手继续找其他线索。'),
                        dict(id='review', label='先查看这两份的处理建议', description='先看建议和影响，不会立即处理。')], 'allow_text': True})
    return fixture.tool('present_findings', {'summary': '已按你的回答保留逐项建议。展开用途分析，可查看指导或自行选择处理；无需助手重新提出删除命令。', 'needs_input': False})


if __name__ == '__main__':
    fixture.answer = answer
    fixture.main()
