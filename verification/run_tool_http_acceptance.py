"""Verify v0.25 tool HTTP contracts on an already running owned profile.

No monkeypatches, application launch, model requests or real cleanup commands.
The one simulation plan points only at the fixture's disposable project scope.
Source fixture and unmodified packaged application evidence are kept separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, Request

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.25.0'
ASSETS = ('app.js', 'tasks.js', 'cleanup-tools.js', 'cleanup-tools.css', 'orchestration-ui.js')


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Client:
    def __init__(self, url):
        parts = urlsplit(url)
        if parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost') or parts.path not in ('', '/') \
                or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('Only an explicit loopback application URL is supported.')
        self.base = url.rstrip('/')
        self.opener = build_opener(ProxyHandler({}))
        self.csrf = self.request('/api/bootstrap?compact=1')['csrf']

    def response(self, path, body=None):
        headers, data = {'Accept': 'application/json'}, None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers.update({'Content-Type': 'application/json', 'Origin': self.base, 'X-CSRF-Token': self.csrf})
        try:
            with self.opener.open(Request(self.base + path, data=data, headers=headers), timeout=30) as response:
                return response.status, response.read()
        except HTTPError as exc:
            with exc:
                return exc.code, exc.read()

    def request(self, path, body=None):
        status, raw = self.response(path, body)
        result = json.loads(raw)
        if status != 200:
            raise RuntimeError(f'HTTP {status}: {result.get("code", "unknown")}')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--fixture-meta', type=Path, required=True)
    parser.add_argument('--target-kind', choices=('source-fixture', 'unmodified-exe'))
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    meta_path = args.fixture_meta.resolve()
    if not meta_path.is_relative_to(ROOT / 'verification'):
        raise ValueError('Only owned verification metadata is supported.')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    kind = args.target_kind or ('source-fixture' if args.url.rstrip('/') == meta['app_url'].rstrip('/') else 'unmodified-exe')
    destination = (args.report or Path(meta['source_profile'] if kind == 'source-fixture' else meta['native_profile'])
                   / 'agent-ui-acceptance.json').resolve()
    if not destination.is_relative_to(ROOT / 'verification'):
        raise ValueError('Write the report only under owned verification profiles.')
    scope = Path(meta['scope']).resolve()
    if not scope.name.startswith('panxu-owned-v0250-') or set(meta['fixture_hashes']) != {'test-cache.tmp', 'keep-notes.txt'}:
        raise ValueError('This is not the expected disposable v0.25 fixture.')
    report = dict(passed=False, version=None, target_kind=kind, checks=[],
        source_fixture_injected=kind == 'source-fixture', unmodified_exe_declared=kind == 'unmodified-exe',
        actual_reclaimed_bytes=0, real_fixture_files_unchanged=False,
        external_provider_used=False, real_model_requests=0,
        limits=['HTTP checks do not prove native pixels or interaction quality.',
                'Target executable identity is declared by the caller and verified separately during release.',
                'Source fixture synthetic observations are not evidence of packaged collector coverage.',
                'No real cleanup adapter is enabled; simulated execution releases zero bytes.'])
    client, previous_trace = None, None

    def check(name, success, **evidence):
        report['checks'].append(dict(name=name, passed=bool(success), **evidence))
        if not success:
            raise AssertionError(name)

    def file_hashes():
        return {name: sha((scope / name).read_bytes()) for name in meta['fixture_hashes']}

    def reject(name, path, body, codes):
        status, raw = client.response(path, body)
        payload = json.loads(raw)
        check(name, status in (400, 404, 409) and payload.get('code') in codes,
              http_status=status, code=payload.get('code'))

    try:
        check('owned_sample_initial_hashes', file_hashes() == meta['fixture_hashes'])
        client = Client(args.url)
        health = client.request('/api/health')
        report['version'] = health.get('version')
        check('version_and_simulation_boundary', health.get('version') == VERSION
              and health.get('mode') == 'simulation' and health.get('live_executor_available') is False)
        previous_trace = client.request('/api/developer')['enabled']
        client.request('/api/developer', {'enabled': True})
        before_trace = client.request('/api/developer?limit=30')
        before_tasks = client.request('/api/tasks')['tasks']
        catalog = client.request('/api/cleanup-tools/assessments')
        rows = catalog.get('items', [])
        required = {'id', 'tool_id', 'tool_ref', 'definition', 'recommendation', 'benefit', 'inspection',
                    'inspection_id', 'status', 'selectable', 'can_execute', 'requires_parameters'}
        check('all_48_shared_assessment_rows', len(rows) == catalog.get('total') == 48
              and len({row['id'] for row in rows}) == 48 and all(required <= set(row) for row in rows))
        check('fixed_explanations_and_distinct_unknowns', all(
            all(row['recommendation'].get(key) for key in ('purpose', 'reason', 'advice', 'impact', 'recovery'))
            and row['recommendation']['source'] in ('builtin', 'llm')
            and (row['benefit']['estimated_reclaimable_bytes'] is None or row['benefit']['estimate_kind'] == 'logical_upper_bound')
            for row in rows))
        after_trace = client.request('/api/developer?limit=30')
        after_tasks = client.request('/api/tasks')['tasks']
        check('catalog_did_not_call_model_or_start_task', before_trace['total'] == after_trace['total']
              and [(task['id'], task['status'], task.get('usage')) for task in before_tasks]
              == [(task['id'], task['status'], task.get('usage')) for task in after_tasks])
        tool_task_id, file_task_id = meta['tasks']['tools'], meta['tasks']['files']
        tool_task = client.request('/api/tasks/' + tool_task_id)['task']
        targets = tool_task.get('cleanup_tool_targets', [])
        check('persisted_exact_frozen_tool_references', tool_task.get('scope_kind') == 'cleanup_tools'
              and targets and all(target['tool_ref'] == meta['cases'][target['tool_ref']['tool_id']]['tool_ref']
                  and target['inspection_id'] == meta['cases'][target['tool_ref']['tool_id']]['inspection_id']
                  and target.get('diagnostic') and target.get('assessment') for target in targets), count=len(targets))
        check('offline_fixture_advice_has_explicit_provenance', tool_task.get('advice') and all(
            advice.get('kind') == 'cleanup_tool' and advice.get('provenance', {}).get('source') == 'llm'
            and advice.get('provenance', {}).get('model') == 'offline-v0250-fixture'
            and advice.get('reason') and advice.get('purpose') and advice.get('impact') for advice in tool_task['advice']))
        file_task = client.request('/api/tasks/' + file_task_id)['task']
        check('file_analysis_task_preserved', file_task.get('scope_kind') == 'filesystem'
              and len(file_task.get('advice', [])) == 2
              and {advice['cleanup_rating'] for advice in file_task['advice']} == {1, 5})
        assets = {}
        for name in ASSETS:
            status, data = client.response('/' + name)
            assets[name] = dict(local_sha256=sha((ROOT / 'web' / name).read_bytes()), served_sha256=sha(data))
            check('current_static_asset_' + name, status == 200
                  and assets[name]['local_sha256'] == assets[name]['served_sha256'])
        report['assets'] = assets
        reject('invalid_tool_parameter_rejected', '/api/cleanup-tools/assess',
               {'tool_id': 'pagefile', 'parameters': {'volume': 'Z:\\invalid'}}, {'invalid_tool_parameter'})
        reject('foreign_parameter_field_rejected', '/api/cleanup-tools/assess',
               {'tool_id': 'gpu_cache', 'parameters': {'command': 'not-a-command'}}, {'invalid_tool_parameter'})
        reject('invalid_refresh_type_rejected', '/api/cleanup-tools/assess',
               {'tool_id': 'gpu_cache', 'refresh': 'yes'}, {'invalid_request'})
        reject('foreign_snapshot_cannot_start_model_analysis', '/api/tasks/tool-analysis',
               {'items': [dict(targets[0]['tool_ref'], inspection_id='foreign-fixture-inspection')]}, {'tool_inspection_changed'})
        # Only inspect an explicitly owned artificial project. The packaged
        # collector runs as shipped; its missing Library must stay unknown, not 0.
        ref = dict(tool_id='unity_cache', parameters={'project_path': str(scope)})
        inspected = client.request('/api/cleanup-tools/assess', dict(ref, refresh=True))
        old = dict(inspected['tool_ref'], inspection_id=inspected['inspection_id'])
        check('owned_project_has_no_confirmed_cleanup', inspected['can_execute'] is False
              and inspected['benefit']['estimated_reclaimable_bytes'] is None,
              observed_status=inspected['status'])
        reject('unknown_target_cannot_use_unified_execution', '/api/cleanup-tools/prepare-selection',
               {'items': [old]}, {'tool_not_actionable'})
        refreshed = client.request('/api/cleanup-tools/assess', dict(ref, refresh=True))
        check('refresh_changes_inspection_identity', refreshed['inspection_id'] != old['inspection_id'])
        reject('old_inspection_cannot_be_prepared', '/api/cleanup-tools/prepare-selection',
               {'items': [old]}, {'tool_inspection_changed'})
        # Keep the execution independent of either UI task. The owned project
        # path binds its only scope, without changing task selection or recency.
        plan = client.request('/api/cleanup-tools/prepare', dict(ref, source={}))
        check('legacy_owned_plan_is_simulation_only', plan['simulation_only'] is True
              and plan['actual_reclaimed_bytes'] == 0 and len(plan['entries']) == 1
              and plan['entries'][0]['parameters']['project_path'] == str(scope))
        execute = {'plan_id': plan['id'], 'version': plan['version']}
        result = client.request('/api/cleanup-tools/execute', execute)
        report['actual_reclaimed_bytes'] = result.get('actual_reclaimed_bytes')
        check('owned_tool_simulation_verified_zero_real_bytes', result.get('simulation_only') is True
              and result.get('actual_reclaimed_bytes') == 0 and result.get('succeeded') == 1)
        replay = client.request('/api/cleanup-tools/execute', execute)
        check('repeat_execute_returns_same_receipt_without_replay', replay == result)
        restored_task = client.request('/api/tasks/' + tool_task_id)['task']
        check('expired_ai_advice_remains_readable', restored_task.get('requires_recheck_for_execution') is True
              and restored_task.get('advice') and all(advice.get('stale') is True
                  and advice.get('reason') and advice.get('provenance', {}).get('model') == 'offline-v0250-fixture'
                  for advice in restored_task['advice'])
              and all(target.get('stale') is True for target in restored_task.get('cleanup_tool_targets', [])))
        reject('post_execution_snapshot_is_invalidated', '/api/cleanup-tools/prepare-selection',
               {'items': [dict(refreshed['tool_ref'], inspection_id=refreshed['inspection_id'])]}, {'tool_inspection_changed'})
        summary = client.request('/api/cleanup-tools/assessments/summary', {'items': [target['tool_ref'] for target in targets]})
        check('unknown_summary_does_not_invent_zero_or_sum', summary['estimated_reclaimable_bytes'] is None
              and summary['estimate_complete'] is False and summary['unknown_count'] == len(targets)
              and summary['actual_reclaimed_bytes'] == 0)
        final_trace = client.request('/api/developer?limit=30')
        baseline = max([entry['seq'] for entry in before_trace['entries']] or [0])
        added = [entry for entry in final_trace['entries'] if entry['seq'] > baseline]
        report['real_model_requests'] = sum(entry.get('kind') == 'request' for entry in added)
        check('no_model_request_during_acceptance', report['real_model_requests'] == 0
              and (not final_trace['has_more'] or any(entry['seq'] <= baseline for entry in final_trace['entries'])))
        report['real_fixture_files_unchanged'] = file_hashes() == meta['fixture_hashes']
        check('owned_sample_hashes_unchanged_after_simulation', report['real_fixture_files_unchanged'])
        report['passed'] = True
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)[:240]
    finally:
        if client is not None and previous_trace is not None:
            try:
                client.request('/api/developer', {'enabled': previous_trace})
            except Exception as exc:
                report['passed'] = False
                report['trace_restore_error'] = type(exc).__name__
        try:
            report['real_fixture_files_unchanged'] = file_hashes() == meta['fixture_hashes']
        except OSError:
            report['real_fixture_files_unchanged'] = False
            report['passed'] = False
        report['check_count'] = len(report['checks'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(report=str(destination), passed=report['passed'], version=report['version'],
            check_count=report['check_count'], target_kind=kind,
            real_fixture_files_unchanged=report['real_fixture_files_unchanged'],
            actual_reclaimed_bytes=report['actual_reclaimed_bytes'], error=report.get('error')), ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
