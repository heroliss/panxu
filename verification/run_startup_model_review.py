"""Opt-in real-model analysis of twelve owned artificial startup objects.

Credentials from the named existing profile are used only in memory. No real
inventory, system probe, process, configuration or cleanup executor is called.
Each default-budget turn is reported separately; one explicit resume is allowed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from diskpilot.agent_budget import DEFAULTS
from diskpilot.llm import ModelAssistant
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness
from diskpilot.windows import _item


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def public_copy(value):
    """Review ordinary messages/tools; omit provider-private continuation state."""
    if isinstance(value, dict):
        return {key: public_copy(part) for key, part in value.items() if key not in
                ('_continuation', 'reasoning', 'reasoning_content', 'thinking')}
    if isinstance(value, list):
        return [public_copy(part) for part in value]
    return copy.deepcopy(value)


def owned_startups():
    samples = [
        ('cloud-sync', 'ExampleCloudSync', '人工说明：此应用同步用户需要的文件，用户明确需要随登录启动。', False),
        ('backup', 'ExampleBackup', '人工说明：此应用承担用户需要的定时备份，用户明确要求保留登录启动。', False),
        ('security', 'ExampleSecurityHealth', '人工说明：这是受保护系统安全状态通知样本，建议保留，不是用户真实注册项。', True),
        ('input', 'ExampleInputMethod', '人工说明：用户每天使用的输入法支持组件，用户要求保留登录启动。', False),
        ('notes', 'ExampleNotesSync', '人工说明：用户每天需要的笔记同步功能，用户要求保留登录启动。', False),
        ('vpn', 'ExampleVpnHelper', '人工说明：用户明确依赖的网络连接支持组件，用户要求保留自动就绪。', False),
        ('editor-update', 'ExampleEditorUpdater', '人工说明：编辑器升级检查；用户希望手动更新，不需要随登录运行。', False),
        ('games', 'ExampleGameLauncher', '人工说明：游戏库启动器；用户只在游戏时手动打开，不需要自动启动。', False),
        ('photo-update', 'ExamplePhotoUpdater', '人工说明：照片软件升级检查；用户希望手动更新，不需要登录启动。', False),
        ('mascot', 'ExampleDesktopMascot', '人工说明：桌面挂件；用户只偶尔手动使用，不需要登录启动。', False),
        ('phone-link', 'ExamplePhoneLink', '人工说明：跨设备连接支持；用户不需要登录时自动就绪，仍会按需使用。', False),
        ('unknown', 'UnknownPublisherHelper', '人工说明：用途、发布者与使用记录尚未知，没有更多证据；请保留不确定性，可明确无法建议。', False),
    ]
    return [_item('startup', 'owned-startup-review-' + key, name, 'Owned fixture publisher',
                  'startup', 'disable_startup', dict(state='Enabled', enabled=True,
                      synthetic=False, observed_at='2026-11-03T00:00:00+00:00'),
                  path='C:/OwnedStartupReview/' + key + '/inert.exe', protected=protected,
                  reason=reason) for key, name, reason, protected in samples]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-profile', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--max-turns', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    base = args.data_dir.resolve()
    if not base.is_relative_to(ROOT / 'verification') or base.exists():
        raise ValueError('Use a new nonexistent owned verification directory.')
    base.mkdir(parents=True)
    sentinel = base / 'owned-unchanged.txt'
    sentinel.write_text('Artificial startup review sentinel. No real cleanup or system change.\n', encoding='utf-8')
    before = sha(sentinel)
    source_settings_path = args.source_profile.resolve() / 'model_settings.json'
    source_settings_hash = sha(source_settings_path)
    source = ModelAssistant(args.source_profile.resolve())
    if not source._api_key:
        raise ValueError('The named profile has no usable model credential.')
    state = SimulationState(base / 'profile')
    assistant = ModelAssistant(base / 'profile')
    permitted = {name: copy.deepcopy(source._settings[name]) for name in
                 ('base_url', 'model', 'provider', 'protocol', 'reasoning_effort')}
    assistant.save_settings({**permitted, 'send_paths': False, 'stream': True,
                             'custom_instructions': '', 'protect_patterns': []})
    assistant._api_key, source._api_key = source._api_key, ''
    h = TaskHarness(state, assistant)
    assert h.agent_budget.get() == DEFAULTS
    rows = owned_startups()
    by_name = {row['name']: row['id'] for row in rows}
    state.publish(rows, 'owned-artificial-startups')
    calls, requests, turns = [], [], []
    original_tool, original_agent = h.tool, assistant.agent_turn
    denied = []

    def audited_tool(key, name, arguments, **options):
        entry = dict(turn=h._get(key)['turn'], name=name, arguments=public_copy(arguments))
        try:
            if name not in {'discover_tools', 'list_skills', 'read_skill', 'read_tool_activity',
                    'read_facts', 'checkpoint_task', 'list_items', 'inspect_items',
                    'record_advice', 'ask_user', 'present_findings', 'collect_apps',
                    'cache_experience'}:
                denied.append(name)
                raise AppError('隔离人工启动项验收不提供额外系统/文件事实或操作方案。', 'fixture_only')
            result = original_tool(key, name, arguments, **options)
            entry['result'] = public_copy(result)
            return result
        except AppError as exc:
            entry['error'] = dict(code=exc.code, message=str(exc))
            raise
        finally:
            calls.append(entry)

    def audited_agent(messages, tools, **options):
        record = dict(turn=h._get(task['id'])['turn'], messages=public_copy(messages),
                      tool_names=[row['function']['name'] for row in tools],
                      progress=h.public(task['id'])['analysis_progress'])
        requests.append(record)
        result = original_agent(messages, tools, **options)
        record['reply'] = public_copy(result)
        return result

    h.tool, assistant.agent_turn = audited_tool, audited_agent

    def inventory(section):
        return dict(items=copy.deepcopy(rows) if section == 'background' else [],
                    warnings=['人工固定样本，不代表真实系统注册或运行情况。'], summary=dict(read_only=True))

    names = ('diskpilot/tasks.py', 'diskpilot/analysis_progress.py', 'diskpilot/agent_budget.py',
        'diskpilot/llm.py', 'diskpilot/simulation.py', 'diskpilot/assessments.py',
        'diskpilot/action_semantics.py', 'diskpilot/cleanup_tools.py', 'diskpilot/tool_assessments.py',
        'diskpilot/task_tool_analysis.py', 'diskpilot/context_memory.py', 'diskpilot/prompt_text.py',
        'diskpilot/providers.py', 'diskpilot/streaming.py', 'diskpilot/windows.py',
        'verification/run_startup_model_review.py')
    metadata = dict(scope='source_harness', final_exe_attribution=False, artificial_startup_count=12,
        real_provider_used=True, provider=permitted['provider'], model=permitted['model'],
        protocol=permitted['protocol'], default_budget=DEFAULTS,
        source_sha256={name: sha(ROOT / name) for name in names}, credentials_saved=False)
    write_json(base / 'metadata.json', metadata)
    started = time.monotonic()
    task = h.create_analysis(dict(section='background', ids=[row['id'] for row in rows],
        goal='这是12条隔离人工启动项样本，不是真实系统。请逐项根据已给说明分析用途、1–5清理倾向、原因与影响，短句即可。'
             '各项人工说明已给出明确用户偏好，不必再询问是否常用。明确需要的同步、备份、输入法和网络支持请保留。'
             '未知项不要编造用途或使用情况，可明确skip/no_advice。只保存分析建议，不生成操作方案，不执行任何操作；不需要新增系统采集。'))
    initial_context = h._context(h._get(task['id']))
    try:
        with patch('diskpilot.tasks.inventory', side_effect=inventory), \
             patch('subprocess.run', side_effect=AssertionError('Real process launch is forbidden')), \
             patch('subprocess.Popen', side_effect=AssertionError('Real process launch is forbidden')):
            for number in range(args.max_turns):
                if number:
                    h.feedback(task['id'], '按原目标继续分析尚未给建议的人工启动项，利用已保存事实，不重复已完成项；不执行操作。')
                before_ids = set(h._get(task['id'])['advice'])
                before_usage = copy.deepcopy(task['usage'])
                h.claim(task['id'])
                try:
                    task = h.investigate(task['id'], cancel=threading.Event())
                finally:
                    h.release(task['id'])
                after_ids = set(h._get(task['id'])['advice'])
                duplicates = [row['id'] for entry in calls if entry['turn'] == task['turn']
                    and entry['name'] == 'record_advice' for row in entry['arguments'].get('items', [])
                    if row['id'] in before_ids]
                turns.append(dict(turn=task['turn'], status=task['status'], new_saved=len(after_ids - before_ids),
                    saved_count=task['analysis_progress']['saved_count'], remaining_count=task['analysis_progress']['remaining_count'],
                    requests_used=task['usage']['model_requests'] - before_usage['model_requests'],
                    tool_calls_used=task['usage']['tool_calls'] - before_usage['tool_calls'],
                    progress=task['analysis_progress'], repeated_prior_advice_count=len(duplicates)))
                write_json(base / f'turn-{number + 1}.json', task)
                if task['status'] != 'budget_exhausted':
                    break
        advice = {row['id']: row for row in task.get('advice', [])}
        legal = all(row.get('conclusion') in ('skip', 'no_advice') and row.get('cleanup_rating') is None
                    or type(row.get('cleanup_rating')) is int and 1 <= row['cleanup_rating'] <= 5
                    for row in advice.values())
        protected = advice.get(by_name['ExampleSecurityHealth'], {})
        preferred = [advice.get(by_name[name], {}) for name in ('ExampleCloudSync', 'ExampleBackup',
            'ExampleInputMethod', 'ExampleNotesSync', 'ExampleVpnHelper')]
        unknown = advice.get(by_name['UnknownPublisherHelper'], {})
        wire = json.dumps(public_copy(requests), ensure_ascii=False)
        saved_settings = json.loads((base / 'profile/model_settings.json').read_text(encoding='utf-8'))
        checks = dict(completed_all_twelve=task['status'] == 'completed' and task['analysis_progress']['remaining_count'] == 0,
            exact_target_ids=set(advice) == {row['id'] for row in rows}, legal_scores_or_explicit_gaps=legal,
            all_explanations=all(all(isinstance(row.get(key), str) and row[key].strip() for key in ('purpose', 'reason', 'impact')) for row in advice.values()),
            protected_security_kept=protected.get('conclusion') in ('keep', 'skip', 'no_advice') and protected.get('cleanup_rating') in (None, 1, 2),
            explicit_keep_preferences=all(row.get('conclusion') in ('keep', 'skip', 'no_advice') or row.get('cleanup_rating') in (1, 2) for row in preferred),
            unknown_not_invented_as_cleanup_candidate=unknown.get('conclusion') in ('skip', 'no_advice') or unknown.get('cleanup_rating') in (1, 2, 3),
            default_budget_preserved=all(row['requests_used'] <= DEFAULTS['model_requests'] and row['tool_calls_used'] <= DEFAULTS['tool_calls'] for row in turns),
            compact_initial_context='initial_objects' not in initial_context and len(json.dumps(initial_context, ensure_ascii=False).encode()) < 6000,
            resumed_completed_advice_not_repeated=all(row['repeated_prior_advice_count'] == 0 for row in turns),
            genuine_provenance=all(row.get('provenance', {}).get('source') == 'llm' for row in advice.values()),
            no_plan=task.get('plan') is None, no_operations=not state.history(),
            owned_sentinel_unchanged=sha(sentinel) == before,
            source_settings_unchanged=sha(source_settings_path) == source_settings_hash,
            credentials_not_saved=not saved_settings.get('key_dpapi') and not saved_settings.get('api_key') and assistant._api_key not in wire)
        report = dict(passed=all(checks.values()), checks=checks, **metadata,
            elapsed_seconds=round(time.monotonic() - started, 3), usage=task.get('usage'), turns=turns,
            final_status=task['status'], tool_sequence=[row['name'] for row in calls], isolation_denials=denied,
            ratings={row['name']: row.get('cleanup_rating') for row in advice.values()},
            actual_reclaimed_bytes=0, real_fixture_unchanged=checks['owned_sentinel_unchanged'],
            limits=['Twelve artificial startup objects and one configured provider only.',
                    'Source harness evidence, not final EXE UI or real startup recommendation validation.',
                    'Machine semantic checks are limited; inspect explanations separately.',
                    'At most two explicit default-budget turns; failed evidence is retained unchanged.'])
        write_json(base / 'dialogue.json', public_copy(requests))
        write_json(base / 'tool-calls.json', calls)
        write_json(base / 'report.json', report)
        print(json.dumps({key: report[key] for key in ('passed', 'final_status', 'elapsed_seconds', 'checks', 'turns', 'usage')}, ensure_ascii=False), flush=True)
        return 0 if report['passed'] else 1
    finally:
        assistant._api_key = ''
        state.close()


if __name__ == '__main__':
    raise SystemExit(main())
