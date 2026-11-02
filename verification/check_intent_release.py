"""Owned EXE HTTP checks for user intent and direct batch planning.

No native UI acceptance is claimed. No Windows target is approved, no files are
deleted, and no model is called unless --exercise-offline-analysis is supplied.
That option accepts only the explicitly configured local fixture peer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot import __version__
from check_specialist_release import API


def owned_hashes(scope, expected):
    if re.fullmatch(r'panxu-owned-v0220-[A-Za-z0-9_-]+', scope.name) is None:
        raise ValueError('Only the owned v0220 fixture is accepted.')
    if set(expected) != {'旧安装样本.zip', '需要保留的资料.txt'}:
        raise ValueError('Only the two fixed fixture files may be read.')
    for ancestor in (scope, *scope.parents):
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Owned fixture must not traverse links.')
    result = {}
    for name in expected:
        path = scope / name
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400 or info.st_size > 2_000_000):
            raise ValueError('Owned sample is missing, linked or too large.')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run(base, metadata, output, exercise_offline_analysis=False):
    metadata, output = metadata.resolve(), output.resolve()
    verification = ROOT / 'verification'
    if (not metadata.is_relative_to(verification) or not output.is_relative_to(verification)
            or output == metadata or metadata.name != 'fixture-meta.json'):
        raise ValueError('Use separate owned metadata/report files inside verification/.')
    meta = json.loads(metadata.read_text(encoding='utf-8'))
    scope = Path(meta['scope'])
    before = owned_hashes(scope, meta['fixture_hashes'])
    if before != meta['fixture_hashes']:
        raise ValueError('The owned fixture has changed since its creation.')
    api = API(base, time.monotonic() + 55)
    checks = []
    report = dict(version=__version__, status='failed', passed=False, phase='owned_exe_intent_http',
        at=datetime.now(timezone.utc).isoformat(), checks=checks, native_ui_verified=False,
        live_operations_performed=False, actual_reclaimed_bytes=0, external_provider_used=False,
        scope='Owned metadata and two unchanged sample files; HTTP checks only.')
    saved = {}

    def check(name, condition, **evidence):
        if not condition:
            raise AssertionError(name)
        checks.append(dict(name=name, status='passed', **evidence))

    def item(ident):
        return api.get('/api/item', {'id': ident})['item']

    def disposition(row, value, *, expect=200):
        return api.post('/api/item-dispositions', dict(id=row['id'], disposition=value, snapshot=row['signature']), expect=expect)

    def plan(*rows, expect=200, revision=None):
        if revision is None:
            revision = api.get('/api/applications')['revision']
        return api.post('/api/actions/plan', dict(ids=[row['id'] for row in rows],
            snapshots={row['id']: row['signature'] for row in rows}, revision=revision), expect=expect)

    def requests():
        return sum(row.get('usage', {}).get('model_requests', 0) for row in api.get('/api/tasks')['tasks'])

    try:
        health = api.get('/api/health')
        check('current_exe_simulation_boundary', health['version'] == __version__ and health['mode'] == 'simulation'
              and health['live_executor_available'] is False)
        bootstrap = api.get('/api/bootstrap', {'compact': 1})
        api.csrf = bootstrap['csrf']
        source = api.get('/api/tasks/' + meta['task_id'])['task']
        check('three_fixture_startups_have_prior_advice', source['advice_total'] == 3 and source['status'] == 'completed')
        apps = api.get('/api/applications')
        rows = {row['id']: row for row in apps['items']}
        startups = [rows[ident] for ident in meta['startup_ids']]
        check('applications_publish_versioned_intent', bool(apps['revision']) and len(startups) == 3
              and all('user_disposition' in row for row in startups))
        files = api.get('/api/files', {'scope': str(scope), 'limit': 100})['items']
        files_by_name = {row['name']: row for row in files}
        first = files_by_name['旧安装样本.zip']
        second = files_by_name['需要保留的资料.txt']
        parent = next(row for row in files if row['path'] == str(scope))
        for row in [*startups, first, second, parent]:
            saved[row['id']] = (row, (row.get('user_disposition') or {}).get('value')
                               if not (row.get('user_disposition') or {}).get('inherited') else None)
            disposition(row, None)
        baseline_requests = requests()
        task_count = len(api.get('/api/tasks')['tasks'])
        skipped = api.post('/api/tasks/analyze', dict(section='background', ids=[row['id'] for row in startups]), expect=409)
        check('already_analyzed_skips_without_job', skipped.get('code') == 'nothing_to_analyze'
              and 'job_id' not in skipped and len(api.get('/api/tasks')['tasks']) == task_count)
        check('skip_does_not_request_model', requests() == baseline_requests)
        for value in ('lean_clean', 'lean_keep', 'direct_clean', 'direct_keep'):
            result = disposition(second, value)
            check('user_choice_' + value + '_persists', result['item']['user_disposition']['value'] == value
                  and item(second['id'])['user_disposition']['value'] == value)
        disposition(second, None)
        check('clear_restores_unspecified_choice', item(second['id'])['user_disposition'] is None)
        disposition(parent, 'lean_keep')
        child_choice = item(second['id'])['user_disposition']
        check('directory_choice_inherits', child_choice['value'] == 'lean_keep' and child_choice['inherited'] is True
              and child_choice['source_id'] == parent['id'])
        disposition(second, 'lean_clean')
        check('specific_child_choice_wins', item(second['id'])['user_disposition']['value'] == 'lean_clean'
              and item(second['id'])['user_disposition']['inherited'] is False)
        disposition(parent, None)
        disposition(second, None)
        old_revision = api.get('/api/applications')['revision']
        disposition(first, 'lean_clean')
        stale = plan(first, expect=409, revision=old_revision)
        check('changed_intent_invalidates_old_selection', stale.get('code') == 'catalog_changed')
        frozen = plan(first)
        check('direct_file_plan_freezes_exact_selection', frozen['picked_ids'] == [first['id']]
              and frozen['plan']['origin'] == 'user_direct' and frozen['plan']['items'][0]['signature'] == first['signature'])
        disposition(first, 'direct_keep')
        rejected = api.post('/api/tasks/' + frozen['task']['id'] + '/approve',
            dict(version=frozen['plan']['version'], ids=frozen['picked_ids']), expect=409)
        check('new_keep_blocks_previously_frozen_plan', rejected.get('code') == 'protected_item' and 'job_id' not in rejected)
        parent_rejected = plan(parent, expect=409)
        check('keep_blocks_parent_directory_plan', parent_rejected.get('code') == 'directory_conflict')
        disposition(first, None)
        bad_snapshot = api.post('/api/item-dispositions', dict(id=first['id'], disposition='direct_clean', snapshot='owned-stale'), expect=409)
        check('stale_preference_snapshot_is_rejected', bad_snapshot.get('code') == 'selection_changed')
        startup_rejected = plan(startups[0], expect=409)
        check('fake_startup_fails_real_identity_preflight', startup_rejected.get('code') == 'target_changed')
        disposition(startups[0], 'direct_keep')
        kept_startup = plan(startups[0], expect=409)
        check('startup_keep_blocks_before_identity_preflight', kept_startup.get('code') == 'protected_item')
        disposition(startups[0], 'direct_clean')
        strong_skip = api.post('/api/tasks/analyze', dict(section='background', ids=[startups[0]['id']], reanalyze=True), expect=409)
        check('strong_choice_also_skips_forced_model_analysis', strong_skip.get('code') == 'nothing_to_analyze')
        disposition(startups[0], None)
        check('direct_operations_do_not_request_model', requests() == baseline_requests)
        tools = api.get('/api/cleanup-tools')
        check('all_48_tools_available', tools['total'] == 48 and len(tools['items']) == 48)
        drives = api.get('/api/file-browser/drives')
        check('drive_dropdown_has_host_roots', bool(drives['items']) and all(row.get('path') for row in drives['items']))
        with tempfile.TemporaryDirectory(prefix='intent-empty-v0220-', dir=verification) as empty_project:
            (Path(empty_project) / 'Library').mkdir()
            inspection = api.post('/api/cleanup-tools/inspect', dict(tool_id='unity_cache', parameters={'project_path': empty_project}))
            presentation = inspection['presentation']
            check('confirmed_empty_tool_is_not_actionable', inspection['read_only'] is True and presentation['complete'] is True
                  and presentation['action_availability']['available'] is False)
        unknown = api.post('/api/cleanup-tools/inspect', dict(tool_id='docker_cache', parameters={}))
        check('unknown_tool_is_not_reported_as_empty', unknown['presentation'].get('action_availability', {}).get('available') is not False)
        if exercise_offline_analysis:
            settings = bootstrap['settings']
            peer = urllib.parse.urlsplit(meta['provider_url'])
            check('explicit_offline_peer_only', settings.get('model') == 'offline-harness-fixture'
                  and settings.get('base_url') == meta['provider_url'] and not settings.get('has_key')
                  and peer.scheme == 'http' and peer.hostname == '127.0.0.1'
                  and meta['external_provider_used'] is False and meta['real_credentials_used'] is False)
            job = api.post('/api/tasks/analyze', dict(section='background', ids=[row['id'] for row in startups], reanalyze=True))
            for _ in range(200):
                result = api.get('/api/jobs/' + job['job_id'])
                if result['status'] != 'running':
                    break
                time.sleep(.05)
            final = api.get('/api/tasks/' + job['task']['id'])['task']
            check('explicit_reanalysis_uses_only_offline_fixture', final['status'] == 'completed' and final['advice_total'] == 3
                  and final['usage']['model_requests'] > 0)
        report.update(status='passed', passed=True, offline_analysis_exercised=exercise_offline_analysis)
    finally:
        for row, value in saved.values():
            disposition(row, value)
        after = owned_hashes(scope, meta['fixture_hashes'])
        report.update(check_count=len(checks), fixture_hashes_unchanged=before == after,
                      simulation_only=True, http_requests=api.requests)
        if before != after:
            report.update(status='failed', passed=False)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not report['passed']:
        raise AssertionError('Owned intent checks did not pass.')
    print(json.dumps(dict(status='passed', checks=len(checks))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--exercise-offline-analysis', action='store_true')
    args = parser.parse_args()
    run(args.base_url, args.metadata, args.output, args.exercise_offline_analysis)
