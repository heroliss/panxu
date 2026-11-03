"""GET-only acceptance for the owned v0.28 sortable-file layout fixture.

Browsing may publish metadata/cache observations. This helper cannot start a
model, scan recursively, change settings, select/prepare/approve an operation,
or delete files. Native layout and interactions are separately observed.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from run_tool_http_acceptance import Client

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.28.0'
ASSETS = ('app.js', 'tasks.js', 'files.js', 'file-workspace.js', 'file-workspace.css',
          'hierarchy.css', 'style.css', 'analysis-progress.js', 'orchestration-ui.js',
          'background-workspace.js')


class GetOnlyClient(Client):
    def response(self, path, body=None):
        if body is not None:
            raise ValueError('This helper permits GET requests only.')
        return super().response(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(path):
    return os.path.normcase(os.path.abspath(path))


def timestamp(value):
    try:
        date = datetime.fromisoformat(value) if isinstance(value, str) else None
        return date.timestamp() if date is not None and date.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def monotonic_known_then_unknown(values, descending=False):
    known = [value for value in values if value is not None]
    return values == sorted(known, reverse=descending) + [None] * (len(values) - len(known))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', '--url', dest='url', default='http://127.0.0.1:3567')
    parser.add_argument('--fixture-meta', type=Path, required=True)
    parser.add_argument('--report', type=Path, default=ROOT / 'verification/agent-ui-acceptance.json')
    parser.add_argument('--target-kind', choices=('source-fixture', 'unmodified-exe'), default='unmodified-exe')
    parser.add_argument('--exe', type=Path)
    args = parser.parse_args()
    meta_path, destination = args.fixture_meta.resolve(), args.report.resolve()
    if not all(path.is_relative_to(ROOT / 'verification') for path in (meta_path, destination)):
        raise ValueError('Fixture metadata and report must remain inside owned verification data.')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    scope = Path(meta['scope']).resolve()
    if not scope.name.startswith('panxu-owned-v0280-files-') or meta.get('external_provider_used') is not False:
        raise ValueError('Only the explicitly owned offline file-layout fixture is accepted.')
    samples = {name: (scope / name).resolve() for name in meta['fixture_hashes']}
    if not samples or not all(path.is_relative_to(scope) for path in samples.values()):
        raise ValueError('Every fixture hash must refer to a child of the owned directory.')
    client, checks = GetOnlyClient(args.url), []
    report = dict(passed=False, version=VERSION, target_kind=args.target_kind, checks=checks,
        asset_hashes={}, actual_reclaimed_bytes=0, real_fixture_files_unchanged=False,
        requests_get_only=True, native_gui_assertion=False, real_provider_used=False,
        real_credentials_used=False, browser_orders={}, catalogue_orders={},
        limits=['HTTP/static-resource checks only; native layout and input interactions are separate.',
                'Current-layer metadata browsing may publish observations and temporary sort indexes.',
                'No model request, recursive scan, setting change, selection, approval or cleanup.'])

    def require(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
        if not condition:
            raise AssertionError(name)

    def unchanged():
        return all(sha(samples[name]) == value for name, value in meta['fixture_hashes'].items())

    def query(endpoint, **values):
        return endpoint + '?' + urlencode({key: value for key, value in values.items() if value is not None})

    def browser_pages(sort='enumeration', direction='asc', *, cursor_check=False):
        options = dict(path=str(scope), limit=50, sort=sort, direction=direction)
        page = client.request(query('/api/file-browser', **options))
        preparations = 0
        while page['status'] == 'sorting':
            require(f'{sort}_{direction}_preparation_{preparations}_honest_progress',
                page['items'] == [] and page['sort_progress']['complete'] is False
                and page['sort_progress']['total_entries'] is None and page['has_more']
                and page.get('next_cursor'))
            preparations += 1
            if preparations > 100:
                raise AssertionError('The owned123-entry sort did not finish its bounded preparation.')
            page = client.request(query('/api/file-browser', **options, cursor=page['next_cursor']))
        first, rows, pages = page, list(page['items']), 1
        if cursor_check and page.get('next_cursor'):
            status, _ = client.response(query('/api/file-browser', **dict(options, direction='desc'),
                                             cursor=page['next_cursor']))
            require('browser_cursor_binds_sort_direction', status == 409)
        while page['has_more']:
            page = client.request(query('/api/file-browser', **options, cursor=page['next_cursor']))
            rows.extend(page['items'])
            pages += 1
            if pages > 10:
                raise AssertionError('Unexpected page count for the owned current layer.')
        require(f'{sort}_{direction}_whole_layer_unique_and_complete',
            len(rows) == meta['root_file_count'] + 2 and len({row['id'] for row in rows}) == len(rows)
            and page['enumeration_complete'] is True and page['coverage']['known_total'] == len(rows)
            and page['coverage']['subtree_complete'] is False)
        report['browser_orders'][sort + '_' + direction] = dict(rows=len(rows), pages=pages,
            preparation_requests=preparations, first_name=rows[0]['name'], last_name=rows[-1]['name'])
        return rows, first

    try:
        health = client.request('/api/health')
        require('current_version_and_simulation', health.get('version') == VERSION
                and health.get('mode') == 'simulation' and health.get('live_executor_available') is False)
        require('owned_fixture_hashes_initially_unchanged', unchanged())
        tasks_before = client.request('/api/tasks')['tasks']
        model_calls_before = sum((task.get('usage') or {}).get('model_requests', 0) for task in tasks_before)
        require('fixture_has_no_model_calls', model_calls_before == 0)
        if args.exe:
            exe = args.exe.resolve()
            require('executable_inside_owned_dist', exe.is_relative_to(ROOT / 'dist')
                    and exe.name == 'PanXu.exe' and exe.is_file())
            report['exe_sha256'] = sha(exe)
        source_text = {}
        for name in ASSETS:
            status, raw = client.response('/' + name)
            value = hashlib.sha256(raw).hexdigest()
            require('current_asset_' + name, status == 200 and value == sha(ROOT / 'web' / name))
            report['asset_hashes'][name] = value
            source_text[name] = raw.decode('utf-8')
        require('merged_address_contract_in_current_asset', all(value in source_text['file-workspace.js']
            for value in ('workspace-address', 'addressEditing', '当前位置', '文件位置')))
        require('personal_intent_has_visible_caption', '我的意愿' in source_text['files.js'])
        require('file_times_and_advice_generation_are_distinct_copy', all(value in source_text['files.js']
            for value in ('建议生成', '不是文件修改时间')) and '文件修改时间' in source_text['file-workspace.js'])

        baseline, _ = browser_pages()
        baseline_ids = {row['id'] for row in baseline}
        known = next(row for row in baseline if canonical(row['path']) == canonical(meta['known_directory']))
        unknown = next(row for row in baseline if canonical(row['path']) == canonical(meta['unknown_directory']))
        require('known_scanned_directory_size_preserved', known['size_known'] is True
                and known['size_kind'] == 'known' and type(known['size_bytes']) is int and known['size_bytes'] > 0)
        require('unexpanded_directory_size_not_zero', unknown['size_known'] is False
                and unknown['size_kind'] == 'unknown' and unknown['size_bytes'] is None)
        require('metadata_time_fields_are_explicit', all('created_at' in row and 'modified_at' in row
                and row.get('details', {}).get('timestamps', {}).get('modified_at') == row['modified_at'] for row in baseline))
        for sort, direction in (('size', 'asc'), ('size', 'desc'), ('modified', 'asc'), ('modified', 'desc'),
                                ('created', 'asc'), ('name', 'asc'), ('type', 'asc')):
            rows, first = browser_pages(sort, direction, cursor_check=sort == 'size' and direction == 'asc')
            require(sort + '_' + direction + '_same_complete_membership', {row['id'] for row in rows} == baseline_ids)
            if sort == 'size':
                values = [row['size_bytes'] if row.get('size_known') is True else None for row in rows]
                require(sort + '_' + direction + '_global_order_unknown_last', monotonic_known_then_unknown(values, direction == 'desc')
                        and rows[-1]['id'] == unknown['id'])
            elif sort in ('modified', 'created'):
                field = 'modified_at' if sort == 'modified' else 'created_at'
                require(sort + '_' + direction + '_global_order_unknown_last',
                        monotonic_known_then_unknown([timestamp(row[field]) for row in rows], direction == 'desc'))
            elif sort == 'name':
                require('name_order_is_global_not_page_local', [row['name'].casefold() for row in rows]
                        == sorted(row['name'].casefold() for row in rows))
            if sort == 'size' and direction == 'asc':
                repeated = client.request(query('/api/file-browser', path=str(scope), sort=sort,
                    direction=direction, limit=50, cursor=first['current_cursor']))
                require('completed_first_page_cursor_is_idempotent', [row['id'] for row in repeated['items']]
                        == [row['id'] for row in first['items']])

        # The observed catalogue has a separate membership revision; its page
        # revision also binds order/filter, while checkbox scope stays stable.
        options = dict(scope=str(scope), kind='all', view='all', limit=50, sort='size', direction='desc')
        catalogue = client.request(query('/api/files', **options))
        all_known = list(catalogue['items'])
        offset = len(all_known)
        while catalogue['has_more']:
            catalogue = client.request(query('/api/files', **options, offset=offset))
            all_known.extend(catalogue['items'])
            offset += len(catalogue['items'])
        root_row = next(row for row in all_known if canonical(row['path']) == canonical(str(scope)))
        membership_revision = None
        for sort, direction in (('size', 'asc'), ('size', 'desc'), ('modified', 'desc')):
            options = dict(view='tree', parent_id=root_row['id'], limit=50, sort=sort, direction=direction)
            page = client.request(query('/api/files', **options))
            rows, first, offset = list(page['items']), page, len(page['items'])
            while page['has_more']:
                page = client.request(query('/api/files', **options, offset=offset, page_revision=first['page_revision']))
                rows.extend(page['items']); offset += len(page['items'])
            require('catalogue_' + sort + '_' + direction + '_full_direct_layer', len(rows) == len(baseline)
                    and {row['id'] for row in rows} == baseline_ids)
            values = ([row['size_bytes'] if row.get('size_known') is True else None for row in rows]
                      if sort == 'size' else [timestamp(row.get('modified_at')) for row in rows])
            require('catalogue_' + sort + '_' + direction + '_global_order', monotonic_known_then_unknown(values, direction == 'desc'))
            if membership_revision is None:
                membership_revision = first['selection_revision']
            require('catalogue_' + sort + '_' + direction + '_same_selection_scope', first['selection_revision'] == membership_revision)
            report['catalogue_orders'][sort + '_' + direction] = dict(rows=len(rows), page_revision=first['page_revision'])
        status, _ = client.response(query('/api/files', **dict(options, direction='asc'),
                                          offset=50, page_revision=first['page_revision']))
        require('catalogue_page_revision_binds_order', status == 409)
        reviewed = client.request(query('/api/files', task_id=meta['task_id'], view='suggestions', limit=50,
                                         sort='modified', direction='desc'))['items']
        require('saved_analysis_time_is_not_file_modification_time', any(row.get('modified_at')
                and row['modified_at'] != (row.get('assessment', {}).get('provenance') or {}).get('generated_at')
                for row in reviewed))
        tasks_after = client.request('/api/tasks')['tasks']
        require('helper_did_not_start_tasks_or_models', {task['id'] for task in tasks_after} == {task['id'] for task in tasks_before}
                and sum((task.get('usage') or {}).get('model_requests', 0) for task in tasks_after) == model_calls_before)
        require('no_cleanup_or_tool_operation_history', not client.request('/api/history')['history']
                and not client.request('/api/cleanup-tools/history')['history'])
        require('owned_fixture_hashes_remain_unchanged', unchanged())
        report.update(passed=True, real_fixture_files_unchanged=True, direct_layer_count=len(baseline),
                      known_directory_bytes=known['size_bytes'], unknown_directory_bytes=None)
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    report['check_count'] = len(checks)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'], check_count=len(checks), report=str(destination), error=report.get('error')),
                     ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
