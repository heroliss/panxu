"""Owned filesystem profiles for the compact address bar and sortable file UI.

All content is disposable and inert. Production scanning and advice contracts
seed a few clearly labelled layout examples. No model, credentials, executor,
Windows registration or user data is involved. Native verification uses an
external snapshot with an unmodified executable.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot import __version__
from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.server import create_server
from serve_v0231_fixture import new_profile

DATABASES = ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3')
PROVENANCE_MODEL = '人工排版夹具（未调用模型）'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(scope):
    return {path.relative_to(scope).as_posix(): digest(path)
            for path in sorted(scope.rglob('*')) if path.is_file()}


def create_samples(scope):
    """Only writes beneath the explicitly owned, empty fixture directory."""
    scope = scope.resolve()
    if not scope.is_dir() or any(scope.iterdir()) or scope.is_symlink():
        raise ValueError('Use an empty owned fixture directory.')
    origin = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp())
    extensions = ('.txt', '.tmp', '.log', '.pdf', '.json', '.png', '.zip')
    named = {0: '空文件.tmp', 1: '个人资料说明.txt', 2: '旧缓存示例.tmp',
             3: ('这是一份较长的中文文件名用于检查紧凑排版和地址输入_' * 3)[:100] + '.txt',
             4: '旅行照片与年度备份说明 2020.pdf'}
    for index in range(121):
        name = named.get(index, f'文件-{index:03}{extensions[index % len(extensions)]}')
        path = scope / name
        size = 0 if index == 0 else 31 + index * 73
        content = (f'Owned inert UI sample {index:03}; not a real {path.suffix} document.\n'.encode() * 200)
        path.write_bytes(content[:size])
        stamp = origin + index * 11 * 86400
        os.utime(path, (stamp, stamp))
    known, unknown = scope / '已分析的缓存目录', scope / '尚未展开的目录'
    for directory in (known, unknown):
        directory.mkdir()
        for index in range(3):
            path = directory / f'目录内容-{index + 1}.tmp'
            path.write_bytes((b'Owned inert nested sample.\n' * (index + 1)))
            stamp = origin + (140 + index) * 86400
            os.utime(path, (stamp, stamp))
    for directory, year in ((known, 2023), (unknown, 2024)):
        stamp = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
        os.utime(directory, (stamp, stamp))
    return dict(known_directory=str(known), unknown_directory=str(unknown), root_file_count=121)


def seed(app, scope):
    samples = create_samples(scope)
    # One layer leaves both directories honestly incomplete. A second explicit
    # scan supplies only one directory's total; the other remains unknown.
    layer = scan_directory(str(scope), max_entries=1000, max_depth=1)
    app.state.publish(layer['items'], 'owned-v0280-layout-layer')
    known = scan_directory(samples['known_directory'], max_entries=100, max_depth=2)
    app.state.publish(known['items'], 'owned-v0280-layout-known-directory')
    items = app.state.snapshot()['items']
    wanted = {'个人资料说明.txt': (1, 'keep'), '旧缓存示例.tmp': (5, 'recommend'),
              '旅行照片与年度备份说明 2020.pdf': (2, 'keep'), '空文件.tmp': (3, 'review'),
              '已分析的缓存目录': (4, 'review')}
    chosen = [row for row in items if row['name'] in wanted]
    assert len(chosen) == len(wanted)
    task = app.harness.create_analysis(dict(section='files', ids=[row['id'] for row in chosen],
        reanalyze=True, goal='[盘序排版验收] 人工文件与目录建议示例，只展示，不调用模型或执行。'))
    provenance = dict(source='llm', provider='custom', protocol='chat_completions',
        model=PROVENANCE_MODEL, generated_at=datetime.now(timezone.utc).isoformat())
    rows = [dict(id=row['id'], conclusion=wanted[row['name']][1], cleanup_rating=wanted[row['name']][0],
        inheritance='subtree' if row['kind'] == 'directory' else 'none', impact_rating=2,
        purpose='人工排版示例，用于核对文件属性、评分和建议布局。',
        reason='这是固定的界面验收建议，未调用模型，不代表真实清理判断。',
        impact='仅展示和模拟记录；本次不删除或修改任何文件。') for row in chosen]
    app.harness.tool(task['id'], 'record_advice', {'items': rows}, cancel=threading.Event(),
                     provenance=provenance)
    stored = app.harness._get(task['id'])
    stored.update(status='completed', summary='五项人工文件／目录建议已保存；其余内容保持未分析。')
    app.harness._save(stored)
    app.assistant.save_settings(dict(provider='custom', protocol='chat_completions',
        base_url='', model='', api_key='', send_paths=False))
    assert not app.assistant.public_settings()['has_key']
    assert not app.state.history()
    return dict(**samples, task_id=task['id'], reviewed_ids=[row['id'] for row in chosen],
        provenance=provenance, scanned_items=len(items), reviewed_count=len(rows))


def snapshot_profile(source, destination):
    for name in DATABASES:
        with closing(sqlite3.connect((source / name).as_uri() + '?mode=ro', uri=True)) as origin:
            with closing(sqlite3.connect(destination / name)) as target:
                origin.backup(target)
    ModelAssistant(destination).save_settings(dict(provider='custom', protocol='chat_completions',
        base_url='', model='', api_key='', send_paths=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'verification/v0280-source-file-fixture')
    parser.add_argument('--native-profile', type=Path, default=ROOT / 'verification/v0280-native-file-fixture')
    parser.add_argument('--port', type=int, default=14788)
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    source, native = new_profile(args.data_dir), new_profile(args.native_profile)
    if source.is_relative_to(native) or native.is_relative_to(source):
        raise ValueError('Source and native profiles must be separate.')
    scope = Path(tempfile.mkdtemp(prefix='panxu-owned-v0280-files-'))
    server = create_server(source, port=args.port)
    serving = False
    metadata = None
    try:
        seeded = seed(server.app, scope)
        snapshot_profile(source, native)
        source_files = ('diskpilot/file_browser.py', 'diskpilot/catalog.py', 'diskpilot/scanner.py',
                        'diskpilot/scan_index.py', 'web/file-workspace.js', 'web/file-workspace.css',
                        'web/files.js', 'web/hierarchy.css')
        metadata = dict(version=__version__, app_url=f'http://127.0.0.1:{server.server_port}',
            source_profile=str(source), native_profile=str(native), scope=str(scope), workspace_path=str(scope),
            fixture_hashes=hashes(scope), source_hashes={name: digest(ROOT / name) for name in source_files},
            native_initial_hashes={name: digest(native / name) for name in (*DATABASES, 'model_settings.json')},
            external_provider_used=False, real_credentials_used=False, model_calls=0,
            actual_reclaimed_bytes=0, **seeded,
            limits=['All samples and saved judgement/provenance are artificial layout fixtures.',
                    'File creation times come from filesystem birthtime; only modification/access times are varied.',
                    'Source and native use unconfigured model profiles; there is no SSE/cloud peer.',
                    'One directory has production-scanned totals; the other was not expanded.',
                    'No cleanup, approval, system registration or user-data modification is required.'])
        target = source / 'fixture-meta.json'
        target.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        serving = True
        print(json.dumps(dict(metadata=str(target), app_url=metadata['app_url'], workspace_path=str(scope),
            native_profile=str(native), task_id=seeded['task_id']), ensure_ascii=False), flush=True)
        while args.serve and not (source / 'stop-fixture').exists():
            threading.Event().wait(.3)
        if hashes(scope) != metadata['fixture_hashes']:
            raise AssertionError('Owned fixture bytes changed.')
    finally:
        if metadata:
            (source / 'fixture-report.json').write_text(json.dumps(dict(passed=hashes(scope) == metadata['fixture_hashes'],
                model_calls=0, actual_reclaimed_bytes=0, owned_files_unchanged=hashes(scope) == metadata['fixture_hashes']),
                ensure_ascii=False, indent=2), encoding='utf-8')
        if serving:
            server.shutdown()
        server.server_close()
        server.app.close()


if __name__ == '__main__':
    main()
