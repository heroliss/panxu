"""GET-only checks for the owned v0.29 persistent-header layout profile.

This checks the running version, current resources and artificial catalogues.
Actual sticky positioning, focus, scroll and layout are separately observed in
the browser/native window. No model, inventory refresh or operation is started.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from run_tool_http_acceptance import Client

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.29.0'
ASSETS = ('workspace-header.js', 'workspace-header.css', 'app.js', 'files.js',
          'file-workspace.js', 'file-workspace.css', 'background-workspace.js',
          'background-workspace.css', 'cleanup-tools.js', 'cleanup-tools.css',
          'hierarchy.css', 'style.css', 'index.html')
KINDS = ('startup', 'process', 'service', 'software')


class GetOnlyClient(Client):
    def response(self, path, body=None):
        if body is not None:
            raise ValueError('This helper permits GET requests only.')
        return super().response(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def query(endpoint, **values):
    return endpoint + '?' + urlencode(values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', '--url', dest='url', default='http://127.0.0.1:3567')
    parser.add_argument('--fixture-meta', type=Path, required=True)
    parser.add_argument('--report', type=Path, default=ROOT / 'verification/agent-ui-acceptance.json')
    parser.add_argument('--target-kind', choices=('source-fixture', 'unmodified-exe'), default='unmodified-exe')
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--expected-exe-sha256')
    args = parser.parse_args()
    meta_path, destination = args.fixture_meta.resolve(), args.report.resolve()
    if not all(path.is_relative_to(ROOT / 'verification') for path in (meta_path, destination)):
        raise ValueError('Metadata and report must remain under owned verification data.')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    owned = Path(meta['owned_scope']).resolve()
    if (not owned.is_dir() or not owned.name.startswith('panxu-owned-v0290-header-')
            or meta.get('external_provider_used') is not False or meta.get('real_credentials_used') is not False):
        raise ValueError('Only the explicitly owned offline header fixture is accepted.')
    samples = {name: (owned / name).resolve() for name in meta['fixture_hashes']}
    if not samples or not all(path.is_relative_to(owned) and path.is_file() for path in samples.values()):
        raise ValueError('All hashes must identify files within the owned fixture.')
    workspace = Path(meta['workspace_path']).resolve()
    if not workspace.is_relative_to(owned):
        raise ValueError('Only the owned file layer can be browsed.')
    checks = []
    report = dict(passed=False, version=VERSION, target_kind=args.target_kind,
        at=datetime.now(timezone.utc).isoformat(), checks=checks, asset_hashes={},
        actual_reclaimed_bytes=0, real_fixture_files_unchanged=False, requests_get_only=True,
        native_gui_assertion=False, real_provider_used=False, real_credentials_used=False,
        limits=['HTTP/static checks do not prove sticky pixels, control reachability, focus or scroll.',
                'Only artificial records and the owned file layer are read; browsing may publish metadata observations.',
                'No inventory refresh, model, selection, plan, approval, tool action or cleanup is started.',
                'Executable path/hash is checked separately from native process identity and interaction evidence.'])

    def require(name, passed):
        checks.append(dict(name=name, passed=bool(passed)))
        if not passed:
            raise AssertionError(name)

    def unchanged():
        return all(sha(samples[name]) == expected for name, expected in meta['fixture_hashes'].items())

    try:
        client = GetOnlyClient(args.url)
        health = client.request('/api/health')
        require('current_version_and_simulation', health.get('version') == VERSION
                and health.get('mode') == 'simulation' and health.get('live_executor_available') is False)
        require('owned_fixture_hashes_initially_unchanged', unchanged())
        settings = client.request('/api/bootstrap?compact=1')['settings']
        require('provider_is_unconfigured_without_key_or_path_sending', not settings.get('has_key')
                and settings.get('model') == '' and settings.get('base_url') == ''
                and settings.get('send_paths') is False)
        if args.target_kind == 'unmodified-exe':
            require('native_executable_is_explicitly_supplied', args.exe is not None)
        if args.exe:
            exe = args.exe.resolve()
            require('executable_inside_owned_dist', exe.is_relative_to(ROOT / 'dist')
                    and exe.name == 'PanXu.exe' and exe.is_file())
            report['exe_sha256'] = sha(exe)
            if args.expected_exe_sha256:
                require('executable_matches_expected_artifact', report['exe_sha256'] == args.expected_exe_sha256)
        text = {}
        for name in ASSETS:
            status, raw = client.response('/' if name == 'index.html' else '/' + name)
            digest = hashlib.sha256(raw).hexdigest()
            require('current_asset_' + name, status == 200 and digest == sha(ROOT / 'web' / name))
            report['asset_hashes'][name] = digest
            text[name] = raw.decode('utf-8')
        require('shared_header_module_loaded_before_workspace_modules',
                text['index.html'].index('/workspace-header.js') < text['index.html'].index('/file-workspace.js')
                and '/workspace-header.css' in text['index.html'])
        require('header_moves_live_controls_into_named_slots', all(name in text['workspace-header.js']
                for name in ('navigation', 'controls', 'actions', 'replaceChildren')))

        tasks_before = client.request('/api/tasks')['tasks']
        expected_tasks = {meta['files']['task_id'], *meta['inventory']['tasks'].values()}
        require('exact_artificial_tasks_are_present', {row['id'] for row in tasks_before} == expected_tasks)
        require('fixture_has_zero_model_requests', sum((row.get('usage') or {}).get('model_requests', 0)
                                                      for row in tasks_before) == 0)
        background = client.request('/api/applications?section=background')['items']
        software = client.request('/api/applications?section=software')['items']
        require('background_and_software_sections_stay_distinct', len(background) == 180 and len(software) == 60
                and all(row['kind'] in KINDS[:3] for row in background)
                and all(row['kind'] == 'software' for row in software))
        for kind in KINDS:
            rows = [row for row in background + software if row['kind'] == kind]
            require(kind + '_sixty_owned_items', len(rows) == 60 and len({row['id'] for row in rows}) == 60
                    and all('排版验收' in row['name'] and Path(row['path']).resolve().is_relative_to(owned) for row in rows))
            assessed = [row for row in rows if row.get('assessment', {}).get('state') == 'explicit']
            require(kind + '_thirty_saved_and_thirty_unanalysed', len(assessed) == 30
                    and sum(row.get('assessment', {}).get('state') == 'unanalysed' for row in rows) == 30)
            require(kind + '_all_score_bands_preserved', all(sum(row['assessment'].get('rating') == score
                    for row in assessed) == 6 for score in range(1, 6)))
            result = client.request(query('/api/files', task_id=meta['inventory']['tasks'][kind],
                view='suggestions', kind=kind, limit=100))
            require(kind + '_task_suggestions_scope_is_exact', result['total'] == 30
                    and {row['id'] for row in result['items']} == {row['id'] for row in assessed})
            result = client.request(query('/api/files', task_id=meta['inventory']['tasks'][kind],
                view='suggestions', kind=kind, rating='5', limit=100))
            require(kind + '_rating_filter_is_exact', result['total'] == 6
                    and all(row['assessment'].get('rating') == 5 for row in result['items']))
        tool_registry = client.request('/api/cleanup-tools')
        require('tool_registry_remains_complete_without_diagnosis_or_execution',
                tool_registry.get('total') == 48 and len(tool_registry['items']) == 48)
        page = client.request(query('/api/file-browser', path=str(workspace), limit=50))
        rows, count = list(page['items']), 1
        while page['has_more']:
            page = client.request(query('/api/file-browser', path=str(workspace), limit=50, cursor=page['next_cursor']))
            rows.extend(page['items']); count += 1
            if count > 10:
                raise AssertionError('Unexpected number of pages in the owned file layer.')
        require('owned_file_layer_remains_complete_and_paged', len(rows) == 123
                and len({row['id'] for row in rows}) == 123 and page['enumeration_complete'] is True)
        require('no_cleanup_history_or_attempts', not client.request('/api/history')['history']
                and not client.request('/api/history')['attempts']
                and not client.request('/api/cleanup-tools/history')['history'])
        tasks_after = client.request('/api/tasks')['tasks']
        require('helper_neither_starts_tasks_nor_calls_models', {row['id'] for row in tasks_after} == expected_tasks
                and sum((row.get('usage') or {}).get('model_requests', 0) for row in tasks_after) == 0)
        require('owned_fixture_hashes_remain_unchanged', unchanged())
        report.update(passed=True, real_fixture_files_unchanged=True, direct_layer_count=123,
                      artificial_inventory_count=240, artificial_advice_count=120)
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    report['check_count'] = len(checks)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'], check_count=len(checks), report=str(destination),
                         error=report.get('error')), ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
