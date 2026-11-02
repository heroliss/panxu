"""Measure model-visible prompt text with owned files and an offline transport.

The optional before JSON is a local capture made before the change, not user
settings. No tokenizer is installed or downloaded; character estimates are
explicit heuristics, not provider usage or billing counts.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diskpilot.llm import ModelAssistant, _SYSTEM
from diskpilot.scanner import scan_directory
from diskpilot.simulation import SimulationState
from diskpilot.tasks import TaskHarness, SYSTEM, tool_specs
from tests.prompt_fixture import parse_host_data


def text_stats(payload):
    parts = {}
    for message in payload['messages']:
        text = message.get('content') or ''
        entry = parts.setdefault(message['role'], dict(chars=0, utf8_bytes=0))
        entry['chars'] += len(text)
        entry['utf8_bytes'] += len(text.encode('utf-8'))
    schema = json.dumps(payload['tools'], ensure_ascii=False, separators=(',', ':'))
    parts['tool_schemas'] = dict(chars=len(schema), utf8_bytes=len(schema.encode('utf-8')))
    parts['visible_chars_including_schema'] = sum(value['chars'] for value in parts.values())
    parts['message_content_chars'] = sum(value['chars'] for key, value in parts.items()
                                       if isinstance(value, dict) and key != 'tool_schemas')
    return parts


def rough_tokens(payload):
    text = ''.join(message.get('content') or '' for message in payload['messages'])
    text += json.dumps(payload['tools'], ensure_ascii=False, separators=(',', ':'))
    ascii_count = sum(ord(char) < 128 for char in text)
    non_ascii = len(text) - ascii_count
    return dict(heuristic_low=math.ceil(ascii_count / 4) + non_ascii,
                heuristic_high=math.ceil(ascii_count / 3) + 2 * non_ascii,
                note='ASCII characters /4..3 + nonASCII *1..2; not tokenizer counts or guaranteed bounds; excludes protocol framing and billing normalization.')


def compare_json_messages(before, after):
    if before['tools'] != after['tools'] or len(before['messages']) != len(after['messages']):
        return False
    for first, second in zip(before['messages'], after['messages']):
        if first['role'] != second['role'] or first.get('tool_call_id') != second.get('tool_call_id'):
            return False
        a, b = first.get('content'), second.get('content')
        try:
            equal = parse_host_data(a) == parse_host_data(b) if isinstance(a, str) and isinstance(b, str) else a == b
        except (ValueError, TypeError):
            equal = a == b
        if not equal:
            return False
        a, b = first.get('tool_calls', []), second.get('tool_calls', [])
        if len(a) != len(b):
            return False
        for left, right in zip(a, b):
            if (left['id'], left['function']['name']) != (right['id'], right['function']['name']):
                return False
            if json.loads(left['function']['arguments']) != json.loads(right['function']['arguments']):
                return False
    return True


def run(baseline=None):
    report = dict(fixture='12 owned disposable installer files; real TaskHarness context/list_items result',
                  provider_token_usage=None, external_connections=0, real_credentials_used=False)
    with tempfile.TemporaryDirectory(prefix='panxu-prompt-budget-') as directory:
        root = Path(directory)
        captured = []
        def offline(_url, payload, _headers, _timeout):
            captured.append(copy.deepcopy(payload))
            return {'choices': [{'message': {'role': 'assistant', 'content': '只测提示词，不执行操作。'}}]}
        assistant = ModelAssistant(root / 'profile', transport=offline)
        assistant._windows = False
        assistant.save_settings(dict(base_url='http://127.0.0.1:11434/v1', model='prompt-budget-fixture', stream=False))
        if baseline:
            for payload in baseline['payloads']:
                assistant.agent_turn(payload['messages'], payload['tools'], stream=False)
            replay = copy.deepcopy(captured)
            equal = all(compare_json_messages(old, new) for old, new in zip(baseline['payloads'], replay))
            if len(replay) != len(baseline['payloads']) or not equal:
                raise RuntimeError('Isolated compaction changed prompt meaning')
            report['before_stats'] = [text_stats(payload) for payload in baseline['payloads']]
            report['before_rough_token_estimates'] = [rough_tokens(payload) for payload in baseline['payloads']]
            report['isolated_llm_replay'] = dict(ast_equal=True, stats=[text_stats(payload) for payload in replay])
            captured.clear()
        scope = root / 'downloads'
        scope.mkdir()
        for number in range(12):
            (scope / f'installer-{number:02d}.zip').write_bytes(b'fixture')
        state = SimulationState(root / 'state')
        try:
            state.publish(scan_directory(str(scope))['items'], 'owned-fixture')
            harness = TaskHarness(state, assistant)
            ids = [item['id'] for item in state.snapshot([])['items'] if item['kind'] == 'file']
            task = harness.create_analysis(dict(section='files', ids=ids, goal='分析这些安装包并给出清理倾向，个人文件由我选择。'))
            current = harness._get(task['id'])
            context = harness._context(current)
            messages = [{'role': 'system', 'content': SYSTEM},
                        {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}]
            result = harness.tool(task['id'], 'list_items', {}, cancel=threading.Event())
            call = dict(id='budget-lookup', type='function', function=dict(name='list_items', arguments='{}'))
            continued = messages + [dict(role='assistant', content=None, tool_calls=[call]),
                dict(role='tool', tool_call_id=call['id'], content=json.dumps(result, ensure_ascii=False))]
            assistant.agent_turn(messages, tool_specs(), stream=False)
            assistant.agent_turn(continued, tool_specs(), stream=False)
            if set(harness._get(task['id'])['target_ids']) != set(ids) or state.history():
                raise RuntimeError('Prompt test changed targets or created an operation')
            report['integrated_current'] = dict(stats=[text_stats(payload) for payload in captured],
                rough_token_estimates=[rough_tokens(payload) for payload in captured], payloads=captured,
                frozen_target_count=len(ids), initial_sample_count=len(context['initial_objects']),
                initial_sample_omitted=context.get('initial_objects_omitted'), listed_count=len(result['items']))
        finally:
            state.close()
    report['legacy_system_chars'] = dict(before=baseline.get('baseline_legacy_system_chars') if baseline else None, after=len(_SYSTEM))
    report['source_sha256'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ('diskpilot/llm.py', 'diskpilot/tasks.py', 'diskpilot/prompt_text.py')}
    report['limits'] = 'Readable host content strings and compact native schema JSON characters are measured. Structured facts remain host-owned; no original JSON is appended to the wire text. Outer HTTP whitespace reduction counts only as transport bytes. Character heuristics are not model/provider usage.'
    report['passed'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/prompt-budget-final.json')
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding='utf-8')) if args.baseline else None
    report = run(baseline)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in ('integrated_current',)} | {
        'integrated_stats': report['integrated_current']['stats']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
