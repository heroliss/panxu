"""Owned source/native profiles for the persistent workspace header.

Long artificial lists exercise navigation, filters and scrolling. Records and
their saved explanations are layout examples, not actual Windows inventory or
model judgements. This helper never configures a provider or runs an executor.
Native verification uses the ordinary executable with the external snapshot.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot import __version__
from diskpilot.server import create_server
from diskpilot.windows import _item
from run_file_layout_fixture import DATABASES, hashes, seed as seed_files, snapshot_profile
from serve_v0231_fixture import new_profile

MODEL = '人工页头排版夹具（未调用模型）'
NAMES = {'startup': '开机启动', 'process': '后台应用', 'service': '后台服务', 'software': '已安装软件'}
ACTIONS = {'startup': 'disable_startup', 'process': 'stop_process', 'service': 'stop_service', 'software': 'uninstall'}


def seed_inventory(app, scope):
    """Publish inert fixture records, then save explicit artificial advice."""
    rows, tasks = [], {}
    provenance = dict(source='llm', provider='custom', protocol='chat_completions',
                      model=MODEL, generated_at=datetime.now(timezone.utc).isoformat())
    for kind, label in NAMES.items():
        executable = scope / ('inert-' + kind + '.exe')
        executable.write_bytes(b'Owned inert header-layout fixture; never execute.\n')
        group = []
        for index in range(60):
            details = dict(fixture=MODEL, usage_evidence={'status': 'unknown'})
            if kind == 'startup':
                details.update(state='Enabled' if index % 6 else 'Disabled', enabled=bool(index % 6))
            elif kind == 'service':
                details.update(service_name=f'PanXuOwnedHeaderService{index:03}',
                               state='Running' if index % 3 else 'Stopped', start_mode='Manual', process_id=0)
            elif kind == 'process':
                details.update(pid=987000000 + index, rss_bytes=(index + 1) * 1024 * 1024)
            else:
                details.update(version=f'1.0.{index}', scope='人工验收登记', has_uninstaller=True,
                               install_date='20200101')
            name = f'排版验收 · {label} {index + 1:02}'
            if index == 2:
                name += ' · 较长名称与说明用于核对标题区域及窄窗口换行'
            row = _item(kind, f'PanXuOwnedHeader-{kind}-{index:03}', name,
                        '人工验收发布者', label, ACTIONS[kind], details, path=str(executable),
                        reason='人工界面样本，不是真实系统登记；只测试列表显示，不执行。')
            group.append(row)
        rows.extend(group)
        app.state.publish(group, 'owned-header-' + kind)
        task = app.harness.create_analysis(dict(section='software' if kind == 'software' else 'background',
            kind=kind, ids=[row['id'] for row in group[:30]], reanalyze=True,
            goal='[页头排版验收] ' + label + '的人工建议，未调用模型，不执行操作。'))
        advice = []
        for index, row in enumerate(group[:30]):
            score = index % 5 + 1
            value = dict(id=row['id'], conclusion='keep' if score <= 2 else 'recommend' if score >= 4 else 'review',
                cleanup_rating=score, impact_rating=2,
                purpose='人工排版示例：验证固定标题、分类、视图、筛选和列表之间的层级。',
                reason='固定保存的界面样本，未调用大模型，不代表对实际电脑的清理判断。',
                impact='仅供只读查看与界面验收，不关闭程序，不修改启动项或服务，不卸载。')
            if kind == 'startup':
                value.update(state_scope='login', state_recommendation='keep_disabled' if index % 6 == 0 else 'keep_enabled')
            elif kind == 'service':
                value.update(state_scope='runtime', state_recommendation='keep_current')
            advice.append(value)
        app.harness.tool(task['id'], 'record_advice', {'items': advice},
                         cancel=threading.Event(), provenance=provenance)
        stored = app.harness._get(task['id'])
        stored.update(status='completed', summary='30条人工排版建议已保存；没有模型请求和操作。')
        app.harness._save(stored)
        tasks[kind] = task['id']
    assert not app.state.history()
    assert not app.assistant.public_settings()['has_key']
    return dict(items=rows, tasks=tasks, provenance=provenance,
                counts={kind: 60 for kind in NAMES}, reviewed_counts={kind: 30 for kind in NAMES})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'verification/v0290-source-header-fixture')
    parser.add_argument('--native-profile', type=Path, default=ROOT / 'verification/v0290-native-header-fixture')
    parser.add_argument('--port', type=int, default=14789)
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    source, native = new_profile(args.data_dir), new_profile(args.native_profile)
    if source.is_relative_to(native) or native.is_relative_to(source):
        raise ValueError('Source and native profiles must be separate.')
    with tempfile.TemporaryDirectory(prefix='panxu-owned-v0290-header-') as temporary:
        owned = Path(temporary).resolve()
        files, applications = owned / 'files', owned / 'applications'
        files.mkdir(); applications.mkdir()
        server = create_server(source, port=args.port)
        metadata = None
        serving = False
        try:
            file_info = seed_files(server.app, files)
            app_info = seed_inventory(server.app, applications)
            snapshot_profile(source, native)
            owned_rows = app_info.pop('items')

            def inventory(section):
                kinds = ('software',) if section == 'software' else ('startup', 'process', 'service')
                return dict(items=[row for row in owned_rows if row['kind'] in kinds],
                            warnings=['人工排版数据，未采集真实系统。'], summary={'status': 'completed'})

            source_names = [path.relative_to(ROOT).as_posix() for path in sorted((ROOT / 'web').iterdir())
                            if path.suffix in ('.js', '.css', '.html')]
            metadata = dict(version=__version__, app_url=f'http://127.0.0.1:{server.server_port}',
                source_profile=str(source), native_profile=str(native), owned_scope=str(owned),
                workspace_path=str(files), fixture_hashes=hashes(owned), files=file_info, inventory=app_info,
                source_hashes={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_names},
                native_initial_hashes={name: hashlib.sha256((native / name).read_bytes()).hexdigest()
                                      for name in (*DATABASES, 'model_settings.json')},
                real_credentials_used=False, external_provider_used=False, model_calls=0, actual_reclaimed_bytes=0,
                limits=['Artificial layout data; saved explanations and provenance do not come from a model.',
                        'Native actions are limited to navigation, scrolling, filtering and selection; do not rescan or start analysis.',
                        'Source refresh inventory is replaced with the same artificial records.',
                        'Tool registry is real program data; no tool execution or real cleanup is part of this case.'])
            (source / 'fixture-meta.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            with patch('diskpilot.server.inventory', side_effect=inventory), patch('diskpilot.tasks.inventory', side_effect=inventory):
                threading.Thread(target=server.serve_forever, daemon=True).start()
                serving = True
                print(json.dumps(dict(metadata=str(source / 'fixture-meta.json'), app_url=metadata['app_url'],
                    workspace_path=str(files), native_profile=str(native), counts=app_info['counts']), ensure_ascii=False), flush=True)
                while args.serve and not (source / 'stop-fixture').exists():
                    threading.Event().wait(.3)
                if hashes(owned) != metadata['fixture_hashes']:
                    raise AssertionError('Owned fixture bytes changed.')
        finally:
            if metadata:
                report = dict(passed=hashes(owned) == metadata['fixture_hashes'], model_calls=0,
                              actual_reclaimed_bytes=0, owned_files_unchanged=hashes(owned) == metadata['fixture_hashes'])
                (source / 'fixture-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            if serving:
                server.shutdown()
            server.server_close()
            server.app.close()


if __name__ == '__main__':
    main()
