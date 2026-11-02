"""Owned loopback HTTP acceptance for 0.23; native window checks are separate.

Use the explicitly configured offline fixture peer only. This script can stage,
approve and undo the simulation executor on one disposable .tmp file. It never
deletes that file, invokes a system program, or accesses a cloud provider.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import stat
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot import __version__
from check_specialist_release import API

NAMES = ('duplicate-a.bin', 'duplicate-b.bin', 'old-cache.tmp', 'keep.txt')


def write_report(path, report):
    """Replace a complete report only after its replacement is fully written."""
    temporary = path.with_name(path.name + '.tmp-' + secrets.token_hex(6))
    try:
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def fixture_hashes(scope):
    if re.fullmatch(r'panxu-owned-v0230-[A-Za-z0-9_-]+', scope.name) is None:
        raise ValueError('Only the new owned v0230 fixture is accepted.')
    for ancestor in (scope, *scope.parents):
        value = ancestor.lstat()
        if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Fixture must not traverse links.')
    result = {}
    for name in NAMES:
        path = scope / name; info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 100000 or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Owned sample must be a small ordinary file.')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run(base, metadata, output, artifact=None, *, recovery_only=False):
    metadata, output = metadata.resolve(), output.resolve()
    if (not metadata.is_relative_to(ROOT / 'verification') or not output.is_relative_to(ROOT / 'verification')
            or metadata == output or metadata.name != 'fixture-meta.json'):
        raise ValueError('Use separate fixture metadata and report files under verification/.')
    meta = json.loads(metadata.read_text(encoding='utf-8'))
    scope = Path(meta['scope'])
    if fixture_hashes(scope) != meta['fixture_hashes']:
        raise ValueError('Fixture changed before acceptance.')
    if artifact is not None:
        artifact = artifact.resolve()
        if not artifact.is_relative_to(ROOT / 'dist') or artifact.suffix.lower() != '.exe':
            raise ValueError('Only an explicit final dist EXE artifact can be reported.')
    previous = json.loads(output.read_text(encoding='utf-8')) if recovery_only else {}
    report = dict(version=__version__, phase='owned_final_exe_http' if artifact else 'owned_source_http',
        at=datetime.now(timezone.utc).isoformat(), passed=False, status='failed', checks=list(previous.get('checks', [])),
        native_ui_verified=False, simulation_only=True, actual_reclaimed_bytes=0,
        external_provider_used=False, real_credentials_used=False, live_operations_performed=False,
        artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact else None,
        artifact=str(artifact) if artifact else None,
        limits=['Deterministic offline SSE peer; not model quality or real provider availability.',
                'HTTP acceptance only; native window interaction is separately recorded.',
                'Current executor is simulation; real deletion, retained storage, system changes remain untested.',
                'Active worker cancellation is covered by unit tests; HTTP checks cancellation ownership/idempotence.'])
    api = API(base, time.monotonic() + 150)

    def check(name, condition, **evidence):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(dict(name=name, status='passed', **evidence))

    def task(key):
        return api.get('/api/tasks/' + key)['task']

    def job_done(job):
        key = job['job_id']
        while True:
            row = api.get('/api/jobs/' + key)
            if row['status'] != 'running':
                check('job_' + key + '_completed', row['status'] == 'completed')
                return row
            time.sleep(.06)

    def investigate(key):
        job_done(api.post('/api/tasks/' + key + '/continue', {}))
        value = task(key)
        check('task_' + key + '_completed', value['status'] == 'completed')
        return value

    def hashes_unchanged():
        report['real_fixture_files_unchanged'] = fixture_hashes(scope) == meta['fixture_hashes']
        check('owned_fixture_bytes_unchanged', report['real_fixture_files_unchanged'])

    try:
        health = api.get('/api/health')
        check('current_version_simulation_only', health['version'] == __version__ and health['mode'] == 'simulation'
              and health['live_executor_available'] is False)
        bootstrap = api.get('/api/bootstrap', {'compact': 1}); api.csrf = bootstrap['csrf']
        settings = bootstrap['settings']
        check('owned_offline_model_only', settings['model'] == 'offline-v0230-peer'
              and settings['base_url'] == meta['provider_url'] and settings.get('has_key') is not True
              and settings['send_paths'] is False)
        budget = api.get('/api/agent-budget')
        check('persisted_budget', budget == {'model_requests': 10, 'tool_calls': 24})
        if recovery_only:
            check('previous_first_pass_was_final_exe', previous.get('passed') is True and previous.get('artifact_sha256') == report['artifact_sha256'])
            report['first_pass'] = {key: previous[key] for key in ('check_count', 'at', 'artifact_sha256', 'receipts')}
            receipts = previous['receipts']
            retained = api.get('/api/recovery', {'status': 'restored', 'limit': 50})
            check('restored_journal_survived_process_restart', any(row['id'] == receipts['recovery_id'] for row in retained['items']))
            restored = task(receipts['file_task'])
            check('operation_receipt_survived_process_restart', bool(restored['last_result']))
            source = task(meta['tasks']['custom'])
            check('source_feedback_survived_process_restart', bool(source.get('last_cleanup_result')))
            saved = api.get('/api/user-tools/' + receipts['tool_id'])
            check('immutable_tool_version_survived_process_restart', saved['version'] == receipts['tool_version'])
            check('duplicate_facts_survived_process_restart', task(meta['tasks']['duplicates'])['advice_total'] == 2)
            report['receipts'] = receipts
        else:
            schema = api.get('/api/user-tools/schema')
            check('custom_schema_has_no_scripts', schema['types'] == ['files', 'preset'])
            system = investigate(meta['tasks']['system'])
            diagnostic = system['diagnostics']
            check('system_has_lightweight_registry_coverage', diagnostic['coverage']['tool_count'] == 48
                  and diagnostic['coverage']['all_tools_fully_diagnosed'] is False)
            page = api.get('/api/tasks/' + system['id'] + '/diagnostics/' + diagnostic['snapshot_id'], {'limit': 5})
            check('diagnostics_are_paginated_and_readonly', len(page['items']) == 5 and page['has_more'] and page['read_only'])
            bad = api.request('/api/tasks/' + meta['tasks']['custom'] + '/diagnostics/' + diagnostic['snapshot_id'], expect=409)
            check('diagnostic_task_ownership', bad.get('code') == 'outside_scope')
            suggestion = next(row for row in system['extra_suggestions'] if row['id'] == 'owned-system')
            check('system_recommendation_is_model_selected', suggestion['provenance']['source'] != 'local'
                  and suggestion['options'][0]['parameters']['project_path'] == str(scope))
            answer = api.post('/api/tasks/' + system['id'] + '/specialist-answer',
                {'suggestion_id': suggestion['id'], 'option_id': 'skip', 'version': suggestion['version']})
            check('tool_decline_is_feedback_not_approval', answer['execution_authorized'] is False)
            custom = investigate(meta['tasks']['custom'])
            suggestion = next(row for row in custom['extra_suggestions'] if row['id'] == 'owned-custom')
            answer = api.post('/api/tasks/' + custom['id'] + '/specialist-answer',
                {'suggestion_id': suggestion['id'], 'option_id': 'open', 'version': suggestion['version']})
            draft_id = answer['user_tool']['draft_id']
            listing = api.get('/api/user-tools')
            draft = next(row for row in listing['items'] if row.get('draft_id') == draft_id)
            check('model_draft_has_explicit_provenance_and_task', draft['status'] == 'draft'
                  and draft['source']['task_id'] == custom['id'] and draft['provenance']['model'] == 'offline-v0230-peer')
            check('draft_not_auto_saved_or_executable', not any(row.get('status') == 'saved' for row in listing['items']))
            saved = api.post('/api/user-tools/save', {'draft_id': draft_id})
            run = api.post('/api/user-tools/inspect', {'tool_id': saved['id'], 'version': saved['version']})
            check('saved_rules_match_only_owned_temporary_file', run['matched_count'] == 1 and run['items'][0]['name'] == 'old-cache.tmp')
            paged = api.get('/api/user-tool-runs/' + run['run_id'], {'offset': 0, 'limit': 1})
            check('run_snapshot_pagination_is_stable', paged['items'][0]['id'] == run['items'][0]['id'] and not paged['has_more'])
            prepared = api.post('/api/user-tools/prepare', {'run_id': run['run_id']})
            check('plan_is_exact_file_snapshot', len(prepared['picked_ids']) == 1 and len(prepared['plan']['items']) == 1
                  and prepared['plan']['items'][0]['signature'] == run['items'][0]['signature'])
            check('plan_freezes_user_tool_version', prepared['plan']['user_tool']['version'] == saved['version']
                  and prepared['plan']['user_tool']['run_id'] == run['run_id'])
            edit = api.post('/api/user-tools/draft', {'definition': saved['definition'],
                'recipe_id': saved['id'], 'base_version': saved['version'], 'source': {'task_id': custom['id']}})
            latest = api.post('/api/user-tools/save', {'draft_id': edit['draft_id']})
            rejected = api.post('/api/tasks/' + prepared['task']['id'] + '/approve',
                {'version': prepared['plan']['version'], 'ids': prepared['picked_ids']}, expect=409)
            check('old_version_cannot_start_apply', rejected.get('code') == 'user_tool_changed' and 'job_id' not in rejected)
            history = api.get('/api/user-tools/' + saved['id'], {'version': saved['version']})
            check('old_definition_is_immutable_and_readable', history['content_hash'] == saved['content_hash'] and history['version'] == 1)
            run = api.post('/api/user-tools/inspect', {'tool_id': latest['id'], 'version': latest['version']})
            prepared = api.post('/api/user-tools/prepare', {'run_id': run['run_id']})
            job_done(api.post('/api/tasks/' + prepared['task']['id'] + '/approve',
                {'version': prepared['plan']['version'], 'ids': prepared['picked_ids']}))
            result = task(prepared['task']['id'])
            check('simulation_receipt_verified', result['last_result']['succeeded'] == 1
                  and result['last_result']['actual_reclaimed_bytes'] == 0)
            source = task(custom['id'])
            check('saved_tool_execution_feedback_returns_to_original_task', source['last_cleanup_result']['succeeded'] == 1)
            retained = api.get('/api/recovery', {'status': 'retained', 'limit': 50})
            entry = next(row for row in retained['items'] if row['item_id'] == prepared['picked_ids'][0])
            check('simulated_delete_uses_recovery_journal', entry['status'] == 'retained' and entry['actual_moved'] is False)
            restore = api.post('/api/recovery/prepare', {'ids': [entry['id']], 'action': 'restore'})
            restored = api.post('/api/recovery/execute', {'plan_id': restore['id'], 'confirmation': restore['confirmation']})
            check('journal_restore_is_successful_simulation', restored['count'] == 1 and restored['actual_reclaimed_bytes'] == 0)
            duplicate = investigate(meta['tasks']['duplicates'])
            check('async_duplicate_members_receive_advice', duplicate['advice_total'] == 2)
            work = next(row for row in duplicate['background_work'] if row['kind'] == 'duplicates')
            check('duplicate_work_completed_with_content_group', work['status'] == 'completed' and work['summary']['content_groups'] == 1)
            scoped = api.get('/api/tasks/' + duplicate['id'] + '/work/' + work['id'])
            check('durable_work_route_returns_same_job', scoped['id'] == work['id'] and scoped['read_only'])
            denied = api.request('/api/tasks/' + custom['id'] + '/work/' + work['id'], expect=404)
            check('work_cannot_be_read_by_other_task', denied.get('code') == 'outside_scope')
            denied = api.post('/api/tasks/' + custom['id'] + '/work/' + work['id'] + '/cancel', {}, expect=404)
            check('work_cannot_be_cancelled_by_other_task', denied.get('code') == 'outside_scope')
            cancelled = api.post('/api/tasks/' + duplicate['id'] + '/work/' + work['id'] + '/cancel', {})
            check('cancel_completed_work_is_idempotent', cancelled['status'] == 'completed')
            facts = api.get('/api/tasks/' + duplicate['id'] + '/facts', {'limit': 20})
            check('duplicate_results_are_persisted_facts', any(row['tool'] == 'duplicates_work' for row in facts['items'])
                  and any(row['tool'] == 'read_duplicate_members' for row in facts['items']))
            before = len(duplicate['background_work'])
            continued = investigate(duplicate['id'])
            check('resume_preserves_work_and_advice_without_recollection', continued['advice_total'] == 2
                  and len(continued['background_work']) == before)
            report['receipts'] = dict(file_task=prepared['task']['id'], recovery_id=entry['id'],
                tool_id=latest['id'], tool_version=latest['version'], duplicate_work=work['id'])
        parsed = urllib.parse.urlsplit(meta['provider_stats'])
        check('provider_statistics_are_loopback_only', parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.path == '/stats')
        with api.opener.open(meta['provider_stats'], timeout=3) as response:
            stats = json.loads(response.read(64000))
        check('peer_received_no_tool_protocol_errors', not stats['errors'])
        check('raw_owned_paths_never_sent_to_model', stats['path_leaks'] == 0)
        hashes_unchanged()
        report.update(passed=True, status='passed', peer_requests=stats['requests'], check_count=len(report['checks']),
                      recovery_after_restart_verified=recovery_only)
    except Exception as exc:
        report.update(error=type(exc).__name__ + ': ' + str(exc), check_count=len(report['checks']))
        raise
    finally:
        destination = output
        if recovery_only and not report['passed']:
            # A failed restart probe is a new attempt, not a replacement for
            # the successful first pass needed to retry the restart checks.
            destination = output.with_name(output.stem + '.recovery-failed-' + str(time.time_ns()) + '.json')
            report['preserved_report'] = str(output)
            if 'receipts' in previous:
                report['receipts'] = previous['receipts']
        write_report(destination, report)
        print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']),
                          'output': str(destination), 'previous_report_preserved': destination != output}))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--artifact', type=Path)
    parser.add_argument('--recovery-only', action='store_true')
    args = parser.parse_args()
    run(args.base_url, args.metadata, args.output, args.artifact, recovery_only=args.recovery_only)
