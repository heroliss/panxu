"""Isolated UI peer with read-only icon inventory and system-evidence tools.

All writes stay in the chosen test profile or the owned TemporaryDirectory.
No cloud provider, real cleanup, shell command or system change is involved.
"""
import json
from pathlib import Path
import sys

import serve_v050_fixture as fixture
from serve_v060_fixture import answer as question_answer


original_create = fixture.create_server


def create_with_icons(*args, **kwargs):
    from diskpilot.windows import startup_items, software_items, _item
    server = original_create(*args, **kwargs)
    for name, collect in (('startup', startup_items), ('software', software_items)):
        items, _ = collect()
        server.app.state.publish(items, 'verification-readonly-' + name)
    # Existing executable resource only; it is neither copied nor launched.
    sample = _item('process', 'verification-icon-process', '图标读取测试', '只读图标夹具',
                   '应用图标', 'stop_process', {'synthetic': False},
                   path=str(Path(sys.executable)), protected=True)
    server.app.state.publish([sample], 'verification-icon-process')
    return server


def answer(payload):
    if any(t.get('function', {}).get('name') == 'connection_probe' for t in payload.get('tools', [])):
        return question_answer(payload)
    messages = payload.get('messages', [])
    context = json.loads(messages[1]['content']) if len(messages) > 1 else {}
    if not str(context.get('goal', '')).startswith('验证系统证据'):
        return question_answer(payload)
    if len(messages) == 2:
        return fixture.tool('collect_system_configuration', {'section': 'memory_power'})
    previous = messages[-2]['tool_calls'][0]['function']['name']
    if previous == 'collect_system_configuration' and context.get('scopes'):
        return fixture.tool('inspect_volume', {'scope_id': context['scopes'][0]['id']})
    return fixture.tool('present_findings', {'summary': '已取得只读系统配置与本次范围所在卷的证据；未知字段仍需核实，没有改变系统或扩大清理范围。', 'needs_input': False})


if __name__ == '__main__':
    fixture.create_server = create_with_icons
    fixture.answer = answer
    fixture.main()
