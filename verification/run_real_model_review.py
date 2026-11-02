"""Opt-in real-provider review against owned files and synthetic app facts.

Credentials are read in memory from a named existing profile and never printed,
copied, or saved. Only this helper's artificial data is offered to the model.
The normal host, provider adapters, durable task loop and simulation runtime are
used; OS collection/validation boundaries are explicitly injected test doubles.
No real file deletion, startup/service/process change, or native launch occurs.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.server import create_server
from diskpilot.simulation import AppError
from diskpilot.windows import _item


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fixture_files(base):
    scope = base / 'owned-files'
    samples = {
        'DownloadCache/expired-session.tmp': b'owned disposable cache sample\n' * 32,
        'DownloadCache/old-preview.tmp': b'owned disposable preview sample\n' * 16,
        'PersonalNotes/keep-family-notes.txt': b'owned artificial personal note, not real user content\n',
        'Installers/ExampleEditorSetup-2023.exe': b'owned inert installer placeholder, never executable\n',
        'Installers/ExampleEditorSetup-2023-copy.exe': b'owned inert installer placeholder, never executable\n',
        'UnknownProject/project-source.txt': b'owned artificial source project, preserve unless understood\n',
    }
    for relative, content in samples.items():
        target = scope / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return scope, {name: sha(scope / name) for name in samples}


def fixture_apps(scope):
    rows = []
    samples = [
        ('startup', 'owned-updater', 'ExampleEditor updater', 'disable_startup',
         dict(state='Enabled', enabled=True, synthetic=False)),
        ('startup', 'owned-sync', 'ExampleNotes sync', 'disable_startup',
         dict(state='Enabled', enabled=True, synthetic=False)),
        ('service', 'owned-service', 'ExampleEditor update service', 'stop_service',
         dict(state='Running', start_mode='Auto', synthetic=False)),
        ('process', 'owned-process', 'ExamplePreview helper', 'stop_process',
         dict(state='Running', pid=987654, working_set_bytes=48 * 1024 * 1024,
              synthetic=False, process_created_at='2026-10-01T10:00:00+00:00')),
        ('software', 'owned-software', 'ExampleEditor 2023', 'uninstall',
         dict(has_uninstaller=True, publisher='Owned Fixture Publisher',
              version='2023.1', synthetic=False)),
    ]
    for kind, key, name, action, details in samples:
        rows.append(_item(kind, key, name, 'Owned artificial fixture', kind, action,
            details, path=str(scope / 'ExampleEditor' / 'program.exe'),
            reason='明确标识的隔离人工测试数据，不是真实安装或运行状态。'))
    return rows


def run_turn(harness, task_id, output, *, execute=False):
    harness.claim(task_id, execute=execute)
    try:
        result = harness.investigate(task_id, execute=execute)
    finally:
        harness.release(task_id)
    write_json(output, result)
    return result


def semantic_checks(case, task):
    """Check the user's intent without requiring per-file model analysis.

    Explicit subtree keep advice may preserve a directory's children. This is
    not a claim that unobserved children were individually inspected or scored.
    """
    advice = {row['name']: row for row in task.get('advice', [])}

    def kept(name, parent):
        row = advice.get(name)
        if row is not None:
            return row.get('cleanup_rating') in (1, 2) and not row.get('stale')
        row = advice.get(parent, {})
        return (row.get('cleanup_rating') in (1, 2) and row.get('inheritance') == 'subtree'
                and not row.get('stale'))

    if case == 'files':
        return dict(personal_notes_retained=kept('keep-family-notes.txt', 'PersonalNotes'),
            source_project_retained=kept('project-source.txt', 'UnknownProject'),
            unused_cache_recommended=advice.get('expired-session.tmp', {}).get('cleanup_rating') in (4, 5))
    return dict(sync_retained=advice.get('ExampleNotes sync', {}).get('cleanup_rating') in (1, 2),
        unused_updater_recommended=advice.get('ExampleEditor updater', {}).get('cleanup_rating') in (4, 5))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--source-profile', type=Path, required=True)
    parser.add_argument('--case', choices=('files', 'background', 'both'), default='both')
    parser.add_argument('--max-turns', type=int, choices=(1, 2), default=1)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--resume-from', type=Path,
                        help='Copy and continue a previous owned review profile; preserve the original evidence.')
    args = parser.parse_args()
    base = args.data_dir.resolve()
    if not base.is_relative_to(ROOT / 'verification') or base.exists():
        raise ValueError('Use a new, nonexistent directory inside verification.')
    base.mkdir(parents=True)
    previous_meta = None
    profile = base / 'profile'
    if args.resume_from:
        prior = args.resume_from.resolve()
        if not prior.is_relative_to(ROOT / 'verification'):
            raise ValueError('Only an owned prior verification profile may be resumed.')
        previous_meta = json.loads((prior / 'metadata.json').read_text(encoding='utf-8'))
        scope, originals = Path(previous_meta['scope']), previous_meta['fixture_hashes']
        if scope.name != 'owned-files' or not scope.parent.name.startswith('panxu-real-model-owned-'):
            raise ValueError('The prior profile does not identify this helper\'s owned fixture scope.')
        if not all(sha(scope / name) == digest for name, digest in originals.items()):
            raise ValueError('Owned fixture files changed since the prior review.')
        profile.mkdir()
        for name in ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3'):
            with sqlite3.connect((prior / 'profile' / name).as_uri() + '?mode=ro', uri=True) as source_db:
                with sqlite3.connect(profile / name) as target_db:
                    source_db.backup(target_db)
        shutil.copy2(prior / 'profile/model_settings.json', profile / 'model_settings.json')
    else:
        scope, originals = fixture_files(Path(tempfile.mkdtemp(prefix='panxu-real-model-owned-')))
    source = ModelAssistant(args.source_profile.resolve())
    if not source._api_key:
        raise ValueError('The explicitly selected profile has no usable credential.')
    server = create_server(profile, args.port)
    app, h = server.app, server.app.harness
    permitted_settings = {name: copy.deepcopy(source._settings[name]) for name in (
        'base_url', 'model', 'provider', 'protocol', 'reasoning_effort')}
    app.assistant.save_settings({**permitted_settings, 'send_paths': False,
        'custom_instructions': '', 'protect_patterns': [], 'stream': True})
    app.assistant._api_key = source._api_key
    source._api_key = ''
    app.trace.update(enabled=True)
    h.agent_budget.save(dict(model_requests=10, tool_calls=24))
    rows = fixture_apps(scope)
    live = {row['id']: row for row in rows}
    if previous_meta is None:
        app.state.publish(scan_directory(str(scope))['items'], 'owned-real-model-review')
        app.state.publish(rows, 'owned-artificial-apps')
    original_tool = h.tool
    isolation_denials = []

    def bounded_tool(key, name, arguments, **options):
        if name in {'collect_system_configuration', 'inspect_volume',
                    'inspect_cleanup_tool', 'run_cleanup_diagnostics'}:
            isolation_denials.append(name)
            raise AppError('隔离验收没有真实系统事实；请仅分析已提供的人工文件/后台样本。',
                           'fixture_system_facts_unavailable')
        return original_tool(key, name, arguments, **options)

    def inventory(section):
        return dict(items=copy.deepcopy([row for row in rows if
            (row['kind'] == 'software') == (section == 'software')]),
            warnings=['隔离人工库存；不代表本机真实安装、启用或使用情况。'],
            summary={'read_only': True})

    def validate(items):
        for item in items:
            if item['kind'] in ('file', 'directory'):
                if not Path(item['path']).is_relative_to(scope):
                    raise AppError('Fixture action outside owned scope.', 'outside_scope')
            elif item['id'] not in live or live[item['id']]['signature'] != item['signature']:
                raise AppError('Fixture identity changed.', 'target_changed')

    h.tool = bounded_tool
    h.runtime.backend.validator = validate
    threading.Thread(target=server.serve_forever, daemon=True).start()
    meta = dict(app_url=f'http://127.0.0.1:{server.server_port}', scope=str(scope),
        fixture_hashes=originals, provider=permitted_settings['provider'],
        model=permitted_settings['model'], protocol=permitted_settings['protocol'],
        send_paths=False, real_provider_used=True, credentials_saved=False,
        os_collection='injected artificial facts only', tasks=copy.deepcopy(previous_meta['tasks']) if previous_meta else {},
        source_sha256={name: sha(ROOT / name) for name in (
            'diskpilot/tasks.py', 'diskpilot/simulation.py', 'diskpilot/llm.py',
            'diskpilot/duplicates.py', 'diskpilot/activity.py', 'diskpilot/cleanup_tools.py',
            'diskpilot/server.py', 'verification/run_real_model_review.py')})
    if previous_meta:
        meta.update(resume_from=str(args.resume_from.resolve().relative_to(ROOT)),
            previous_source_sha256=previous_meta['source_sha256'],
            prior_report_sha256=sha(args.resume_from.resolve() / 'report.json'))
    write_json(base / 'metadata.json', meta)
    print(json.dumps({k: meta[k] for k in ('app_url', 'provider', 'model', 'protocol')},
                     ensure_ascii=False), flush=True)
    outcomes = []
    started = time.monotonic()
    with patch('diskpilot.tasks.inventory', side_effect=inventory), patch('diskpilot.server.inventory', side_effect=inventory):
        try:
            for case in ('files', 'background') if args.case == 'both' else (args.case,):
                if previous_meta:
                    task = h.public(meta['tasks'][case])
                    previous_usage = copy.deepcopy(task['usage'])
                    h.feedback(task['id'], '继续这次人工验收，原目标和保留要求不变；请利用已有事实、评分和模拟操作记录继续，不重复采集。')
                elif case == 'files':
                    task = h.create('整理此隔离验收目录。全部是人工测试文件：DownloadCache中的临时文件已无用；'
                        'PersonalNotes和UnknownProject必须保留；安装包已经不用了，请核对是否重复。'
                        '按证据给简短用途、1–5清理倾向、原因和影响；不执行操作。', str(scope))
                else:
                    task = h.create_analysis(dict(section='background', ids=[row['id'] for row in rows if row['kind'] != 'software'],
                        goal='这是隔离人工库存：用户不需要ExampleEditor的自动更新和ExamplePreview常驻，但要保留ExampleNotes同步。'
                             '分析每项用途、清理倾向和影响；不需要再询问是否常用，不要声称真实系统已改变。'))
                key = task['id']
                meta['tasks'][case] = key
                write_json(base / 'metadata.json', meta)
                for turn in range(args.max_turns):
                    task = run_turn(h, key, base / f'{case}-turn-{turn + 1}.json')
                    print(json.dumps(dict(case=case, turn=turn + 1, status=task['status'],
                        advice_count=len(task.get('advice', [])), usage=task.get('usage')), ensure_ascii=False), flush=True)
                    if task['status'] != 'budget_exhausted':
                        break
                # Explicit fixture selection exercises the exact same host path
                # as the user, independent of model wording or automatic score.
                selected = [row for row in h._items(h._get(key)) if
                    (case == 'files' and row['kind'] == 'file' and row['name'] == 'expired-session.tmp') or
                    (case == 'background' and row['name'] == 'ExampleEditor updater')]
                operation = None
                if previous_meta and meta['tasks'].get(case + '_operation'):
                    operation = h.public(meta['tasks'][case + '_operation'])
                    write_json(base / f'{case}-simulation.json', operation)
                elif selected:
                    prepared = h.prepare_assessment_plan([selected[0]['id']], source_task_id=key,
                        allow_global_applications=True)
                    operation_id = prepared['task']['id']
                    h.validate_approval(operation_id, prepared['plan']['version'], prepared['picked_ids'])
                    operation = run_turn(h, operation_id, base / f'{case}-simulation.json', execute=True)
                    meta['tasks'][case + '_operation'] = operation_id
                    write_json(base / 'metadata.json', meta)
                checks = semantic_checks(case, task)
                with app.trace.lock:
                    tool_results = [json.loads(row[0]).get('payload', {}) for row in app.trace.db.execute(
                        "SELECT payload FROM entries WHERE task_id=? AND kind='tool_result' ORDER BY seq", (key,))]
                action_errors = sum(row.get('result', {}).get('code') == 'unsupported_action' for row in tool_results)
                duplicates = [row['result'] for row in tool_results if row.get('name') == 'compare_files']
                outcomes.append(dict(case=case, task_id=key, status=task['status'],
                    advice_count=len(task.get('advice', [])), question=bool(task.get('question')),
                    usage=task.get('usage'), summary=task.get('summary'),
                    semantic_checks=checks, unsupported_action_errors=action_errors,
                    duplicate_bytes_read=sum(row.get('bytes_read', 0) for row in duplicates),
                    verified_duplicate_groups=sum(group.get('verified') is True for row in duplicates for group in row.get('groups', [])),
                    simulation_succeeded=(operation or {}).get('last_result', {}).get('succeeded', 0)))
                if previous_meta:
                    outcomes[-1].update(previous_usage=previous_usage, simulation_from_prior_profile=True,
                        new_model_requests=task['usage']['model_requests'] - previous_usage['model_requests'])
            unchanged = all(sha(scope / name) == digest for name, digest in originals.items())
            report = dict(passed=unchanged and all(row['usage']['model_requests'] > 0 and
                row['advice_count'] > 0 and row['status'] in ('completed', 'awaiting_review')
                and row['simulation_succeeded'] == 1 and all(row['semantic_checks'].values())
                for row in outcomes),
                elapsed_seconds=round(time.monotonic() - started, 3), real_provider_used=True,
                actual_reclaimed_bytes=0, real_fixture_unchanged=unchanged,
                isolation_denials=isolation_denials, scenarios=outcomes,
                limits=['Artificial owned metadata only; not a real-disk cleanup recommendation quality benchmark.',
                        'A single configured provider; no cross-provider claim.',
                        'No real executor; only SQLite simulation receipts.',
                        'OS inventory/system facts replaced or denied at explicit test boundary.'])
            write_json(base / 'report.json', report)
            print(json.dumps(report, ensure_ascii=False), flush=True)
            while args.serve and not (base / 'stop-fixture').exists():
                threading.Event().wait(1)
        finally:
            server.shutdown()
            server.server_close()
            app.close()


if __name__ == '__main__':
    main()
