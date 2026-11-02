"""Opt-in real-model review using only artificial cleanup-tool diagnostics.

Read the explicitly named existing model credential in memory. Never copy or
persist it. No real system collectors or cleanup executors run in this review.
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

from diskpilot.llm import ModelAssistant
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def artificial(tool_id, parameters=None):
    sizes = {'gpu_cache': 128 * 1024 ** 2, 'thumbnail_cache': 0, 'browser_cache': 64 * 1024 ** 2}
    if tool_id not in sizes:
        raise AppError('本验收只提供三项人工工具快照，没有本机系统事实。', 'fixture_only')
    size = sizes[tool_id]
    return dict(at='2026-11-03T00:00:00+00:00',
        evidence=dict(status='observed', complete=True, observed_bytes=size,
            observed_files=4 if size else 0, roots_found=1, coverage_complete=True,
            coverage_paths=['C:/OwnedArtificialToolReview/' + tool_id],
            observed_file_keys=['fixture-file-proof-' + tool_id],
            note='隔离人工诊断，不代表本机真实占用。'),
        presentation=dict(kind='cache', status='observed', complete=True,
            metrics=[dict(key='observed_bytes', value=size), dict(key='observed_files', value=4 if size else 0)],
            items=[], warnings=['人工验收快照；不能据此声称真实磁盘已清理。']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-profile', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--prior-report', type=Path,
                        help='Retain a prior failed review as evidence when correcting its acceptance criterion.')
    args = parser.parse_args()
    base = args.data_dir.resolve()
    if not base.is_relative_to(ROOT / 'verification') or base.exists():
        raise ValueError('Use a new, nonexistent directory inside verification.')
    base.mkdir(parents=True)
    sentinel = base / 'owned-unchanged.txt'
    sentinel.write_text('Artificial sentinel. No deletion or system operation is authorized.\n', encoding='utf-8')
    before = sha(sentinel)
    source = ModelAssistant(args.source_profile.resolve())
    if not source._api_key:
        raise ValueError('The explicitly selected profile has no usable credential.')
    state = SimulationState(base / 'profile')
    assistant = ModelAssistant(base / 'profile')
    permitted = {name: copy.deepcopy(source._settings[name]) for name in
                 ('base_url', 'model', 'provider', 'protocol', 'reasoning_effort')}
    assistant.save_settings({**permitted, 'send_paths': False, 'custom_instructions': '',
                             'protect_patterns': [], 'stream': True})
    assistant._api_key, source._api_key = source._api_key, ''
    h = TaskHarness(state, assistant)
    h.agent_budget.save(dict(model_requests=10, tool_calls=24))
    # A slow provider must not turn this evidence-quality scenario into a TTL
    # test. Ordinary cache expiry is covered separately by strict unit tests.
    h.tool_assessments.ttl = 900
    calls, requests = [], []
    original_tool, original_agent = h.tool, assistant.agent_turn

    def audited_tool(key, name, arguments, **options):
        entry = dict(name=name, arguments=copy.deepcopy(arguments))
        try:
            result = original_tool(key, name, arguments, **options)
            entry['result'] = copy.deepcopy(result)
            return result
        except AppError as exc:
            entry['error'] = dict(code=exc.code, message=str(exc))
            raise
        finally:
            calls.append(entry)

    def audited_agent(messages, tools, **options):
        requests.append(dict(messages=copy.deepcopy(messages),
            tool_names=[spec['function']['name'] for spec in tools]))
        result = original_agent(messages, tools, **options)
        requests[-1]['reply'] = copy.deepcopy(result)
        return result

    h.tool, assistant.agent_turn = audited_tool, audited_agent
    names = ('diskpilot/tasks.py', 'diskpilot/task_tool_analysis.py', 'diskpilot/tool_assessments.py',
             'diskpilot/server.py', 'diskpilot/cleanup_tools.py', 'diskpilot/tool_inspection.py',
             'diskpilot/llm.py', 'diskpilot/simulation.py', 'diskpilot/assessments.py',
             'diskpilot/orchestration.py', 'diskpilot/diagnostics.py', 'diskpilot/context_memory.py',
             'diskpilot/prompt_text.py', 'diskpilot/providers.py', 'verification/run_tool_model_review.py')
    metadata = dict(provider=permitted['provider'], model=permitted['model'], protocol=permitted['protocol'],
        real_provider_used=True, credentials_saved=False, artificial_diagnostics_only=True,
        send_paths=False, source_sha256={name: sha(ROOT / name) for name in names})
    criteria = dict(version='reasoned-score-v2',
        cache_score='Any legal 1–5 independent judgment with purpose, evidence-based reason and impact; do not force the built-in rating.',
        strict_preferences='Known-empty thumbnail cache and explicit browser keep preference remain low scored.',
        review_limit='Machine checks validate readback, values and obvious contradictions; a separate semantic review remains necessary.')
    if args.prior_report:
        prior_path = args.prior_report.resolve()
        if not prior_path.is_relative_to(ROOT / 'verification'):
            raise ValueError('The prior report must be owned verification evidence.')
        prior = json.loads(prior_path.read_text(encoding='utf-8'))
        criteria['prior_failure'] = dict(report=str(prior_path.relative_to(ROOT)), sha256=sha(prior_path),
            passed=prior.get('passed'), failed_checks=[key for key, value in prior.get('checks', {}).items() if value is False],
            gpu_cache_rating=prior.get('ratings', {}).get('gpu_cache'),
            correction='The former 4–5-only GPU assertion was too strong: 128 MiB savings may reasonably be neutral when shader recompilation has a cost. Preserve that failed report; rerun the unchanged core.')
    metadata['acceptance_criteria'] = criteria
    write_json(base / 'metadata.json', metadata)
    started = time.monotonic()
    try:
        with patch.object(h.cleanup_tools, 'inspect', side_effect=artificial) as probe:
            rows = [h.tool_assessments.inspect('gpu_cache'), h.tool_assessments.inspect('thumbnail_cache'),
                    h.tool_assessments.inspect('browser_cache', dict(browser='chrome', profile='current'))]
            inspected_count = probe.call_count
            task = h.create_tool_analysis(dict(items=[dict(row['tool_ref'], inspection_id=row['inspection_id']) for row in rows],
                goal='本次全部是人工验收诊断，不是真实磁盘：请分析这三项工具并各给1–5分、用途、原因及影响。'
                     '我明确希望保留浏览器缓存；其余按已保存的真实值判断，没有内容则不建议清理。'
                     '没有需要询问的个人偏好，只分析建议，不执行操作，不需要新的采集。'))
            h.claim(task['id'])
            try:
                task = h.investigate(task['id'], cancel=threading.Event())
            finally:
                h.release(task['id'])
            no_new_collectors = probe.call_count == inspected_count
        write_json(base / 'task.json', task)
        write_json(base / 'tool-calls.json', calls)
        # This is only artificial conversation plus privacy-projected facts.
        # Never include source settings, auth headers, endpoints or credentials.
        write_json(base / 'dialogue.json', requests)
        advice = {row['tool_ref']['tool_id']: row for row in task.get('advice', [])}
        initial = h._context(h._get(task['id']))
        compact_index = all(set(row) == {'id', 'name'} for row in initial['selected_tools'])
        inspected_ids = {ident for call in calls if call['name'] == 'inspect_items'
                         for ident in call['arguments'].get('ids', [])}
        targets = {row['id'] for row in rows}
        model_wire = json.dumps(requests, ensure_ascii=False)
        saved_settings = json.loads((base / 'profile' / 'model_settings.json').read_text(encoding='utf-8'))
        gpu = advice.get('gpu_cache', {})
        gpu_rating = gpu.get('cleanup_rating')
        gpu_reason = str(gpu.get('reason', '')).replace(' ', '')
        gpu_snapshot = next((item for call in calls if call['name'] == 'inspect_items'
            for item in call.get('result', {}).get('items', [])
            if item.get('tool_id') == 'gpu_cache'), {})
        false_empty_claims = ('无可清理', '没有可清理', '没有缓存', '无缓存', '未找到缓存',
                              '未发现缓存', '占用未知', '占用为0', '占用0字节')
        checks = dict(completed=task['status'] == 'completed', exact_three_ratings=len(advice) == 3,
            gpu_cache_valid_independent_rating=type(gpu_rating) is int and 1 <= gpu_rating <= 5,
            gpu_cache_explanation=all(isinstance(gpu.get(key), str) and gpu[key].strip()
                for key in ('purpose', 'reason', 'impact')),
            gpu_cache_basis_read=gpu_snapshot.get('diagnostic', {}).get('benefit', {}).get('observed_bytes') == 128 * 1024 ** 2,
            gpu_cache_not_claimed_empty_or_unknown=not any(claim in gpu_reason for claim in false_empty_claims),
            empty_thumbnail_retained=advice.get('thumbnail_cache', {}).get('cleanup_rating') in (1, 2),
            user_browser_preference_retained=advice.get('browser_cache', {}).get('cleanup_rating') in (1, 2),
            diagnostics_read=targets <= inspected_ids, compact_initial_index=compact_index,
            no_internal_proof_tokens=not any(token in model_wire for token in
                ('fixture-file-proof-', 'coverage_keys', 'observed_file_keys', 'OwnedArtificialToolReview')),
            no_unexpected_tool_calls=not any(call.get('error') for call in calls),
            no_new_collectors=no_new_collectors, no_plan=task.get('plan') is None,
            no_actions=not state.history(), owned_sentinel_unchanged=sha(sentinel) == before,
            credentials_not_saved=not saved_settings.get('key_dpapi') and not saved_settings.get('api_key'),
            genuine_provenance=all(row.get('provenance', {}).get('source') == 'llm' for row in advice.values()))
        report = dict(passed=all(checks.values()), status=task['status'], checks=checks,
            elapsed_seconds=round(time.monotonic() - started, 3), usage=task.get('usage'),
            real_provider_used=True, artificial_diagnostics_only=True, actual_reclaimed_bytes=0,
            provider=permitted['provider'], model=permitted['model'], protocol=permitted['protocol'],
            source_sha256=metadata['source_sha256'],
            acceptance_criteria=criteria,
            tool_sequence=[call['name'] for call in calls], ratings={key: row.get('cleanup_rating') for key, row in advice.items()},
            limits=['One configured provider and three artificial snapshots only.',
                    'Source harness evidence, not final executable or native UI acceptance.',
                    'No real collectors or cleanup operations.'])
        write_json(base / 'report.json', report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0 if report['passed'] else 1
    finally:
        assistant._api_key = ''
        state.close()


if __name__ == '__main__':
    raise SystemExit(main())
