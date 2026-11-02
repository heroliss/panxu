"""Owned HTTP workflow/history acceptance; mutations are simulation only.

--read-only leaves demonstration plans untouched. The full check uses only the
separate checker task and its owned receipts, without invoking cloud models,
program launchers, inventory collectors or real file/system mutation routes.
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
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot import __version__
from check_specialist_release import API
from check_v0230_release import write_report
from serve_v0240_fixture import NAMES


def fixture_hashes(scope, bulk_count=0):
    if type(bulk_count) is not int or not 0 <= bulk_count <= 100:
        raise ValueError('Owned bulk sample count must be between 0 and 100.')
    if re.fullmatch(r'panxu-owned-v0240-[A-Za-z0-9_-]+', scope.name) is None:
        raise ValueError('Use this check only with a new owned v0240 fixture.')
    for parent in (scope, *scope.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Fixture must not traverse links.')
    result = {}
    names = (*NAMES, *(f'bulk-{index:03d}.tmp' for index in range(bulk_count)))
    if {path.name for path in scope.iterdir()} != set(names):
        raise ValueError('Owned fixture directory has unexpected or missing samples.')
    for name in names:
        path = scope / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 100000 or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Fixture samples must be small ordinary files.')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run(base, metadata, output, artifact=None, *, read_only=False):
    metadata, output = metadata.resolve(), output.resolve()
    if (not metadata.is_relative_to(ROOT / 'verification') or not output.is_relative_to(ROOT / 'verification')
            or metadata == output or metadata.name != 'fixture-meta.json'):
        raise ValueError('Use distinct metadata and output files under verification/.')
    meta = json.loads(metadata.read_text(encoding='utf-8'))
    scope = Path(meta['scope'])
    bulk_count = meta.get('bulk_history', {}).get('count', 0)
    if fixture_hashes(scope, bulk_count) != meta['fixture_hashes']:
        raise ValueError('Owned samples changed before acceptance.')
    if artifact is not None:
        artifact = artifact.resolve()
        if not artifact.is_relative_to(ROOT / 'dist') or artifact.suffix.lower() != '.exe':
            raise ValueError('Attribute only an explicit dist executable.')
    api = API(base, time.monotonic() + 90)
    report = dict(passed=False, status='failed', version=__version__,
        phase='owned_final_exe_system_review_http' if artifact else 'owned_source_system_review_http',
        at=datetime.now(timezone.utc).isoformat(), checks=[], read_only=read_only,
        real_fixture_files_unchanged=False, actual_reclaimed_bytes=0, simulation_only=True,
        native_ui_verified=False, external_provider_used=False, real_credentials_used=False,
        artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact else None,
        limits=['Explicit owned deterministic seed; model intelligence is tested separately.',
                'HTTP contracts and simulation journal only; native clicks and layout are separate evidence.',
                'No cloud request, actual cleanup, system launch or live background mutation.'])

    def check(name, condition, **evidence):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(dict(name=name, status='passed', **evidence))

    def task(key):
        return api.get('/api/tasks/' + key)['task']

    def wait(job):
        while True:
            current = api.get('/api/jobs/' + job['job_id'])
            if current['status'] != 'running':
                if current['status'] != 'completed':
                    raise AssertionError('Owned simulation job did not complete.')
                return current
            time.sleep(.04)

    def peer_stats():
        endpoint = urlsplit(meta['provider_stats'])
        if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or endpoint.path != '/stats'
                or endpoint.username or endpoint.password):
            raise ValueError('Only owned loopback provider statistics are allowed.')
        with api.opener.open(meta['provider_stats'], timeout=3) as response:
            return json.loads(response.read(64000))

    try:
        health = api.get('/api/health')
        check('current_version_and_simulation_boundary', health['version'] == __version__
            and health['mode'] == 'simulation' and health['live_executor_available'] is False)
        boot = api.get('/api/bootstrap', {'compact': 1})
        api.csrf = boot['csrf']
        settings = boot['settings']
        check('owned_profile_and_no_credentials', settings['model'] == 'offline-v0240-peer'
            and settings['base_url'] == meta['provider_url'] and settings['has_key'] is False
            and settings['send_paths'] is False)
        before_peer = peer_stats()
        for name, mime in (('history-presentation.js', 'text/javascript'), ('history.css', 'text/css')):
            # This fixed asset set is intentionally separate from the older API helper's allowlist.
            with api.opener.open(api.base + '/' + name, timeout=3) as response:
                body = response.read(300000)
                check('static_' + name, response.status == 200
                    and response.headers.get('Content-Type', '').startswith(mime)
                    and body == (ROOT / 'web' / name).read_bytes())
        history = api.get('/api/history')
        check('history_is_bounded_and_has_explicit_attempt_channel', len(history['history']) <= 100
            and len(history['attempts']) <= 100 and type(history['attempts_has_more']) is bool)
        if bulk_count:
            bulk = meta['bulk_history']
            bulk_task = task(bulk['task_id'])
            grouped = [row for row in history['history'] if row.get('operation_group') == {
                'task_id': bulk['task_id'], 'plan_version': bulk['plan_version']}]
            expected_receipts = {row['batch_id']: row['item_id'] for row in bulk['items']}
            check('bulk_receipts_keep_one_confirmation_and_exact_undo_identities',
                bulk_task['last_result']['succeeded'] == bulk_count and grouped
                and all(len(row['items']) == 1 and expected_receipts.get(row['id']) == row['items'][0]['id']
                        for row in grouped))
        mixed = next(row for row in history['history'] if row['id'] == meta['mixed_batch_id'])
        rows = {row['name']: row for row in mixed['items']}
        check('mixed_batch_keeps_original_outcome_and_current_item_states', mixed['status'] == 'partially_undone'
            and rows['retained-a.tmp']['status'] == rows['restored-b.tmp']['status'] == 'simulated'
            and rows['retained-a.tmp']['current_state'] == 'recorded'
            and rows['restored-b.tmp']['current_state'] == 'undone')
        attempts = {row['task_id']: row for row in history['attempts']}
        check('failure_cancellation_and_unverified_attempts_are_visible', all(
            attempts[value['task_id']]['status'] == value['status'] for value in meta['attempts'].values())
            and {r['status'] for r in attempts.values()} >= {'failed', 'cancelled', 'unverified'})
        uncertain = attempts[meta['attempts']['unverified']['task_id']]
        check('unverified_does_not_erase_a_committed_receipt', uncertain['current_state'] == 'recorded'
            and uncertain['batch_id'] and uncertain['actual_reclaimed_bytes'] == 0)
        check('history_does_not_mix_specialist_attempts_into_ordinary_channel', all(
            row['item']['kind'] != 'cleanup_tool' and row['actual_reclaimed_bytes'] == 0
            for row in history['attempts']))
        plans = {row['id']: row for row in api.get('/api/cleanup-tools/history')['history']}
        check('specialist_states_remain_distinct', all(plans[meta['tool_plans'][label]]['status'] == status
            for label, status in (('completed', 'completed'), ('cancelled', 'cancelled'), ('awaiting', 'awaiting_review'))))
        complete = plans[meta['tool_plans']['completed']]
        check('specialist_results_are_real_simulation_receipts', complete['result']['succeeded'] == 1
            and complete['result']['actual_reclaimed_bytes'] == 0
            and complete['result']['items'][0]['status'] == 'succeeded')
        native = task(meta['tasks']['native_files'])
        checker = task(meta['tasks']['checker_files'])
        check('file_analysis_scope_and_saved_advice_survive', native['task_kind'] == checker['task_kind'] == 'analysis'
            and native['advice_total'] == checker['advice_total'] == 2)
        scoped = api.get('/api/files', {'task_id': native['id'], 'limit': 50, 'view': 'all'})
        check('file_results_are_from_the_same_task_scope', {row['id'] for row in scoped['items']} == {
            meta['file_ids']['native-clean.tmp'], meta['file_ids']['native-notes.txt']})
        background = task(meta['tasks']['native_background'])
        background_rows = api.get('/api/files', {'task_id': background['id'], 'limit': 50, 'view': 'all'})
        check('fake_background_scope_is_explicit_and_never_installed', background['scope_kind'] == 'applications'
            and {row['id'] for row in background_rows['items']} == set(meta['background_ids'])
            and background['advice_total'] == 2)
        feedback = native.get('last_specialist_result')
        check('tool_receipt_returns_to_its_original_task', feedback is not None
            and feedback['plan_id'] == complete['id'] and feedback['windows_changed'] is False
            and feedback['actual_reclaimed_bytes'] == 0)
        if not read_only:
            if checker['status'] == 'awaiting_review':
                plan = checker['plan']
                wait(api.post('/api/tasks/' + checker['id'] + '/approve',
                    {'version': plan['version'], 'ids': [row['id'] for row in plan['items']]}))
            checker = task(checker['id'])
            check('reviewed_file_task_finishes_without_a_model_roundtrip', checker['status'] == 'completed'
                and checker['plan'] is None and checker['last_result']['succeeded'] == 1
                and checker['last_result']['actual_reclaimed_bytes'] == 0)
            entry_id = checker['last_result']['items'][0]['recovery_entry_id']
            recovery = api.get('/api/recovery', {'status': 'all', 'limit': 100})
            entry = next(row for row in recovery['items'] if row['id'] == entry_id)
            if entry['status'] == 'retained':
                prepared = api.post('/api/recovery/prepare', {'ids': [entry_id], 'action': 'restore'})
                api.post('/api/recovery/execute', {'plan_id': prepared['id'], 'confirmation': prepared['confirmation']})
            current = task(checker['id'])['last_result']['items'][0]
            check('recovery_preserves_historical_success_and_reports_current_undo',
                current['status'] == 'succeeded' and current['current_state'] == 'undone'
                and current['recovery_status'] == 'restored')
            target = meta['attempts']['checker_unverified']
            current_attempts = api.get('/api/history')['attempts']
            target_row = next(row for row in current_attempts if row['task_id'] == target['task_id'])
            if target_row['current_state'] == 'recorded':
                api.post('/api/undo', {'batch_id': target['batch_id']})
            current_attempts = api.get('/api/history')['attempts']
            target_row = next(row for row in current_attempts if row['task_id'] == target['task_id'])
            check('unverified_history_tracks_undo_without_rewriting_the_attempt',
                target_row['status'] == 'unverified' and target_row['current_state'] == 'undone')
        after_peer = peer_stats()
        check('direct_execution_and_recovery_do_not_call_a_model', after_peer['requests'] == before_peer['requests'])
        check('offline_peer_has_no_protocol_errors_or_path_leaks', not after_peer['errors'] and after_peer['path_leaks'] == 0)
        report['real_fixture_files_unchanged'] = fixture_hashes(scope, bulk_count) == meta['fixture_hashes']
        check('all_owned_file_bytes_unchanged', report['real_fixture_files_unchanged'])
        report.update(passed=True, status='passed')
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        report['check_count'] = len(report['checks'])
        write_report(output, report)
        print(json.dumps(dict(passed=report['passed'], checks=report['check_count'], output=str(output))))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--artifact', type=Path)
    parser.add_argument('--read-only', action='store_true')
    args = parser.parse_args()
    run(args.base_url, args.metadata, args.output, args.artifact, read_only=args.read_only)


if __name__ == '__main__':
    main()
