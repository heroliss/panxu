"""Owned tool-assessment UI profiles; all observations/advice are offline fixtures.

Only the source server substitutes read-only inspection results. The native
profile contains ordinary persisted tasks and can be opened by an unmodified
EXE; its frozen diagnostic/advice history stays clearly marked as fixture data.
No real cleanup, model request, user profile or system setting is touched.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot.scanner import scan_directory
from diskpilot.server import create_server
from diskpilot.tasks import now
from serve_v0231_fixture import new_profile


def cache_result(path, size, count, *, complete=True):
    status = 'observed' if complete else 'partial'
    return dict(at='2026-11-03T00:00:00+00:00', read_only=True, simulation_only=True,
        evidence=dict(status=status, complete=complete, observed_bytes=size, observed_files=count,
            roots_found=1, coverage_paths=[str(path)], coverage_complete=True,
            reads_file_contents=False, fixture='offline-v0250-synthetic-metadata'),
        presentation=dict(kind='cache', status=status, complete=complete,
            summary='离线验收夹具：完整缓存' if complete else '离线验收夹具：部分检查',
            metrics=[dict(key='observed_bytes', label='已统计占用', value=size, unit='bytes'),
                     dict(key='observed_files', label='已统计文件', value=count, unit='count')],
            items=[], warnings=['离线夹具，不是本机真实软件占用。']))


def seed(app, scope):
    h, stop = app.harness, threading.Event()
    for name, value in (('test-cache.tmp', b'Owned temporary file sample.'),
                        ('keep-notes.txt', b'Owned personal notes sample.')):
        (scope / name).write_bytes(value)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in scope.iterdir() if p.is_file()}
    app.state.publish(scan_directory(str(scope))['items'], 'v0250-owned-files')
    files = [row for row in app.state.snapshot()['items'] if row['kind'] == 'file']
    provenance = dict(source='llm', provider='custom', model='offline-v0250-fixture',
                      protocol='chat_completions', generated_at=now())
    file_task = h.create_analysis(dict(section='files', ids=[row['id'] for row in files],
        reanalyze=True, goal='测试文件清单：离线夹具建议，不执行真实清理。'))
    h.tool(file_task['id'], 'record_advice', {'items': [dict(id=row['id'],
        conclusion='keep' if row['name'].endswith('.txt') else 'recommend',
        cleanup_rating=1 if row['name'].endswith('.txt') else 5,
        purpose='离线夹具：个人笔记。' if row['name'].endswith('.txt') else '离线夹具：已结束工作的临时文件。',
        reason='保留个人创作内容。' if row['name'].endswith('.txt') else '仅用于可丢弃文件的模拟验收。',
        impact='删除会丢失笔记。' if row['name'].endswith('.txt') else '本构建只记录模拟结果。',
        impact_rating=4 if row['name'].endswith('.txt') else 1) for row in files]}, cancel=stop, provenance=provenance)
    h.tool(file_task['id'], 'present_findings', dict(summary='离线夹具建议已完成：保留笔记，可选择临时文件。',
        needs_input=False), cancel=stop, provenance=provenance)

    fixture_root = scope / 'synthetic-cache-roots'
    results = {
        'gpu_cache': cache_result(fixture_root / 'shared', 128 * 1024**2, 12),
        'thumbnail_cache': cache_result(fixture_root / 'shared' / 'thumbs', 40 * 1024**2, 4),
        'steam_cache': cache_result(fixture_root / 'independent', 64 * 1024**2, 6),
        'adobe_cache': cache_result(fixture_root / 'partial', 96 * 1024**2, 8, complete=False),
        'browser_cache': cache_result(fixture_root / 'empty', 0, 0),
        'browser_models': cache_result(fixture_root / 'models', 512 * 1024**2, 1),
    }

    def inspection(tool_id, parameters=None):
        ref = app.cleanup_tools.validate_reference(dict(tool_id=tool_id, parameters=parameters or {}), allow_missing=True)
        missing = [field['name'] for field in app.cleanup_tools.describe(tool_id)['parameters']
                   if field.get('required') and field['name'] not in ref['parameters']]
        result = results.get(tool_id)
        if missing:
            return dict(tool_id=tool_id, at=now(), parameters=ref['parameters'], missing_parameters=missing,
                evidence=dict(status='needs_input'), presentation=dict(status='needs_input', complete=False,
                    summary='离线夹具：尚未选择目标。', metrics=[], items=[], warnings=[]))
        if tool_id == 'hibernation':
            return dict(tool_id=tool_id, at=now(), parameters=ref['parameters'],
                evidence=dict(power_capabilities=dict(status='observed', hibernation_supported=True,
                    hibernation_file_present=True), fixture='offline-v0250-synthetic-metadata'),
                presentation=dict(kind='power', status='observed', complete=True,
                    summary='离线夹具：休眠配置需要明确选择。', metrics=[], items=[], warnings=[]))
        if result is None:
            return dict(tool_id=tool_id, at=now(), parameters=ref['parameters'],
                evidence=dict(status='unknown', fixture='offline-v0250-synthetic-metadata'),
                presentation=dict(kind='status', status='unknown', complete=False,
                    summary='离线夹具：尚无完整状态采集。', metrics=[], items=[], warnings=[]))
        return dict(result, tool_id=tool_id, parameters=ref['parameters'])

    patcher = patch.object(app.cleanup_tools, 'inspect', side_effect=inspection)
    patcher.start()
    try:
        rows = [app.tool_assessments.inspect(row['tool_id'], row['tool_ref']['parameters'])
                for row in app.tool_assessments.catalog()['items']]
        chosen = [row for row in rows if row['tool_id'] in ('gpu_cache', 'steam_cache', 'adobe_cache',
                                                          'pip_cache', 'browser_models', 'hibernation')]
        tool_task = h.create_tool_analysis(dict(items=[dict(row['tool_ref'], inspection_id=row['inspection_id'])
                                                       for row in chosen],
            reanalyze=True, goal='测试专项工具建议：所有事实和建议均为离线夹具。'))
        h.tool(tool_task['id'], 'inspect_items', dict(ids=[row['id'] for row in chosen]),
               cancel=stop, provenance=provenance)
        h.tool(tool_task['id'], 'record_advice', {'items': [dict(id=row['id'],
            conclusion='recommend' if row['can_execute'] and row['tool_id'] != 'browser_models' else 'review',
            cleanup_rating=4 if row['can_execute'] and row['tool_id'] != 'browser_models' else 3,
            purpose='离线验收夹具：' + row['recommendation']['purpose'][:120],
            reason='离线固定建议，用于核对来源、选择和恢复流程；不代表真实模型推理。',
            impact=row['recommendation']['impact'][:300],
            impact_rating=row['recommendation']['impact_rating']) for row in chosen]},
            cancel=stop, provenance=provenance)
        h.tool(tool_task['id'], 'present_findings', dict(summary='离线夹具工具建议已保存；不执行清理，未知项仍需核对。',
            needs_input=False), cancel=stop, provenance=provenance)
    except BaseException:
        patcher.stop()
        raise
    return dict(tasks=dict(files=file_task['id'], tools=tool_task['id']),
        cases={row['tool_id']: dict(id=row['id'], tool_ref=row['tool_ref'], inspection_id=row['inspection_id'],
            status=row['status'], can_execute=row['can_execute']) for row in rows},
        scope=str(scope), fixture_hashes=before), patcher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--native-profile', type=Path, required=True)
    parser.add_argument('--port', type=int, default=14785)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--serve', action='store_true')
    modes.add_argument('--seed-only', action='store_true')
    args = parser.parse_args()
    profile, native = new_profile(args.data_dir), new_profile(args.native_profile)
    if profile.is_relative_to(native) or native.is_relative_to(profile):
        raise ValueError('Use separate owned source and native profiles.')
    # Project checkouts are intentionally protected file targets. Use a newly
    # allocated disposable scope, exactly as the previous native fixtures do.
    scope = Path(tempfile.mkdtemp(prefix='panxu-owned-v0250-'))
    server = create_server(profile, port=args.port)
    patcher, serving = None, False
    try:
        seeded, patcher = seed(server.app, scope)
        for name in ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3'):
            with closing(sqlite3.connect((profile / name).as_uri() + '?mode=ro', uri=True)) as origin:
                with closing(sqlite3.connect(native / name)) as target:
                    origin.backup(target)
        metadata = dict(app_url=f'http://127.0.0.1:{server.server_port}',
            source_profile=str(profile), native_profile=str(native), **seeded,
            external_provider_used=False, real_credentials_used=False,
            limits=['Source inspection values are offline synthetic metadata, not real system measurements.',
                    'Persisted suggestions identify offline-v0250-fixture; no model inference was performed.',
                    'Unmodified final EXE restores frozen task history, not the in-memory inspection cache.',
                    'All execution remains simulated, actual reclaimed bytes are zero.'])
        destination = profile / 'fixture-meta.json'
        destination.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(metadata=str(destination), app_url=metadata['app_url'],
            native_profile=str(native), tasks=seeded['tasks'], scope=str(scope),
            cases=len(seeded['cases']), external_provider_used=False, real_credentials_used=False),
            ensure_ascii=False), flush=True)
        if args.serve:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            serving = True
            while not (profile / 'stop-fixture').exists():
                threading.Event().wait(.3)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in scope.iterdir() if p.is_file()}
        if after != seeded['fixture_hashes']:
            raise AssertionError('Owned file samples changed.')
        print(json.dumps(dict(stopped=True, real_fixture_files_unchanged=True)), flush=True)
    finally:
        if serving:
            server.shutdown()
        if patcher:
            patcher.stop()
        server.server_close(); server.app.close()


if __name__ == '__main__':
    main()
