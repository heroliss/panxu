"""Read-only HTTP acceptance of completed artificial0.27 analysis tasks.

Only GET requests are allowed. The caller performs all analysis and GUI actions
separately; this helper cannot start tasks, change settings, collect inventory,
select or prepare objects, approve plans, or execute an operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from run_tool_http_acceptance import Client

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.27.0'
ASSETS = ('app.js', 'tasks.js', 'files.js', 'analysis-progress.js', 'analysis-progress.css',
          'orchestration-ui.js', 'orchestration-ui.css', 'hierarchy.css',
          'background-workspace.js', 'background-workspace.css')
DEFAULT_SESSION = dict(model_requests=96, tool_calls=256, elapsed_minutes=30,
                       total_tokens=1_000_000, no_progress_requests=6, consecutive_failures=3)
DEFAULT_CHECKPOINT = dict(model_requests=6, tool_calls=12)


class GetOnlyClient(Client):
    def response(self, path, body=None):
        if body is not None:
            raise ValueError('This acceptance helper allows GET requests only.')
        return super().response(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:3567')
    parser.add_argument('--fixture-meta', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--paused-snapshot', type=Path, default=ROOT / 'verification/v0270-native-paused.json')
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--expected-exe-sha256')
    args = parser.parse_args()
    metadata_path, destination, pause_path = args.fixture_meta.resolve(), args.report.resolve(), args.paused_snapshot.resolve()
    if not all(path.is_relative_to(ROOT / 'verification') for path in (metadata_path, destination, pause_path)):
        raise ValueError('Only owned verification metadata, snapshot and report paths are accepted.')
    meta = json.loads(metadata_path.read_text(encoding='utf-8'))
    scope = Path(meta['scope']).resolve()
    expected_files = {f'fixture-startup-{n:02}.exe' for n in range(1, 13)} | {
                     f'fixture-service-{n:02}.exe' for n in range(1, 5)}
    if not scope.name.startswith('panxu-owned-v0270-') or set(meta.get('fixture_hashes', {})) != expected_files:
        raise ValueError('This is not the expected owned16-sentinel fixture.')
    checks, client = [], GetOnlyClient(args.url)
    report = dict(passed=False, version=VERSION, target_kind='unmodified-exe', checks=checks,
        asset_hashes={}, actual_reclaimed_bytes=0, real_fixture_files_unchanged=False,
        real_provider_used=False, real_credentials_used=False, requests_get_only=True,
        native_gui_assertion=False,
        limits=['Read-only HTTP and local artifact checks; native GUI actions are separately observed.',
                'Artificial frozen startup/service objects and loopback SSE provider only.',
                'No realfiledeletion, service/startupchange, inventorycollection or externalmodelrequest.'])

    def require(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
        if not condition:
            raise AssertionError(name)

    def unchanged():
        return all(sha(scope / name) == value for name, value in meta['fixture_hashes'].items())

    try:
        health = client.request('/api/health')
        require('current_version_and_simulation', health.get('version') == VERSION and health.get('mode') == 'simulation'
                and health.get('live_executor_available') is False)
        require('owned16_sentinels_initially_unchanged', unchanged())
        if args.exe:
            executable = args.exe.resolve()
            require('executable_inside_owned_dist', executable.is_relative_to(ROOT / 'dist')
                    and executable.is_file() and executable.name == 'PanXu.exe')
            report['exe_sha256'] = sha(executable)
            report['exe_path'] = str(executable)
            if args.expected_exe_sha256:
                require('executable_matches_expected_candidate', report['exe_sha256'] == args.expected_exe_sha256.lower())
        elif args.expected_exe_sha256:
            raise ValueError('--expected-exe-sha256 requires --exe.')
        for name in ASSETS:
            status, data = client.response('/' + name)
            actual = hashlib.sha256(data).hexdigest()
            require('current_asset_' + name, status == 200 and actual == sha(ROOT / 'web' / name))
            report['asset_hashes'][name] = actual
        settings = client.request('/api/bootstrap?compact=1')['settings']
        require('owned_offline_provider_and_no_path_sending', settings.get('model') == 'offline-v0270-peer'
                and settings.get('send_paths') is False
                and settings.get('base_url', '').startswith('http://127.0.0.1:'))
        require('session_resource_settings', client.request('/api/analysis-budget') == DEFAULT_SESSION)
        require('legacy_checkpoint_settings', client.request('/api/agent-budget') == DEFAULT_CHECKPOINT)
        report['tasks'] = {}
        projections = {}
        for kind, count, ids in (('startup', 12, meta['startup_ids']), ('services', 4, meta['service_ids'])):
            key = meta['tasks'][kind]
            task = client.request('/api/tasks/' + key)['task']
            advice = client.request('/api/tasks/' + key + '/advice?limit=50')['items']
            progress, session = task['analysis_progress'], task['analysis_session']
            require(kind + '_completed_exact_coverage', task['status'] == 'completed'
                    and task.get('scope_kind') == 'applications' and progress['target_count'] == count
                    and progress['saved_count'] == count and progress['remaining_count'] == 0
                    and len(advice) == count and {row['id'] for row in advice} == set(ids))
            require(kind + '_session_and_progress_agree', session == progress['session']
                    and session['status'] == 'completed' and session['limits'] == DEFAULT_SESSION)
            require(kind + '_zero_tool_failures', task['usage']['tool_failures'] == 0)
            require(kind + '_checkpoint_units_retained', progress['budget']['model_requests']['limit'] == 6
                    and progress['budget']['tool_calls']['limit'] == 12)
            require(kind + '_all_state_directions_and_ratings_preserved', all(
                row['state_recommendation'] == meta['expected_state_advice'][row['id']]['recommendation']
                and row['state_scope'] == meta['expected_state_advice'][row['id']]['scope']
                and row['cleanup_rating'] == meta['expected_state_advice'][row['id']]['score'] for row in advice))
            page = client.request('/api/files?' + urlencode(dict(task_id=key, view='all',
                kind='startup' if kind == 'startup' else 'service', limit=50)))
            rows = {row['id']: row for row in page['items']}
            require(kind + '_same_exact_scope_in_list', page['total'] == count and set(rows) == set(ids)
                    and page['has_more'] is False)
            require(kind + '_state_projection_present', all(row.get('assessment', {}).get('state_advice')
                and row['assessment']['state_advice']['recommendation'] == meta['expected_state_advice'][ident]['recommendation']
                and row['assessment']['state_advice']['scope'] == meta['expected_state_advice'][ident]['scope']
                for ident, row in rows.items()))
            expected_auto = {ident for ident in ids if meta['expected_state_advice'][ident]['recommendation'] == 'disable'
                             and meta['expected_state_advice'][ident]['scope'] != 'service_startup'
                             and meta['expected_state_advice'][ident]['score'] in (4, 5)}
            require(kind + '_automatic_selection_only_supported_close_advice',
                    {ident for ident, row in rows.items() if row['can_auto_select']} == expected_auto)
            non_actions = [row for ident, row in rows.items()
                           if meta['expected_state_advice'][ident]['recommendation'] != 'disable']
            require(kind + '_enable_keep_unknown_cannot_prepare_cleanup', all(
                row['can_auto_select'] is False and row['can_prepare'] is False
                and row['assessment']['state_advice']['actionable'] is False for row in non_actions))
            if kind == 'startup':
                require('continuous12_uses14_requests_and2_checkpoints', session['used']['model_requests'] == 14
                        and task['usage']['model_requests'] == 14 and session['checkpoints'] == 2
                        and session['used']['tool_calls'] == 14)
                require('continuous12_has_complete_reported_fixture_tokens', session['token_usage_complete'] is True
                        and session['used']['input_tokens'] == 896 and session['used']['output_tokens'] == 896
                        and session['cost_estimate'] is None)
                disabled = [row for row in rows.values() if row['assessment']['state_advice']['current_state'] == 'disabled']
                require('disabled_startup_entries_not_logically_selectable', len(disabled) == 2
                        and all(row['can_logical_select'] is False for row in disabled))
            else:
                require('service_scope_is_not_runtime_startup_conflation', len(expected_auto) == 1
                        and all(rows[ident]['action'] == 'stop_service'
                                and rows[ident]['assessment']['state_advice']['current_state'] == 'running'
                                for ident in expected_auto))
                unknown = [row for row in rows.values() if row['assessment']['state_recommendation'] == 'investigate']
                require('unknown_service_state_and_score_stay_unknown', len(unknown) == 1
                        and unknown[0]['assessment']['rating'] is None
                        and unknown[0]['assessment']['state_advice']['current_state'] == 'unknown'
                        and unknown[0]['assessment']['state_advice']['current_state_known'] is False
                        and progress['rated_count'] == 3)
                paused = json.loads(pause_path.read_text(encoding='utf-8'))
                before = paused['analysis_session']
                require('real_native_pause_snapshot_matches_three_results', paused['status'] == 'paused'
                        and paused['analysis_progress']['saved_count'] == 3
                        and before['used']['model_requests'] == 5)
                require('resumed_service_session_identity_and_usage_preserved', session['id'] == before['id']
                        and session['used']['model_requests'] >= 7
                        and all(session['used'][field] >= before['used'][field]
                                for field in ('tool_calls', 'input_tokens', 'output_tokens', 'elapsed_ms')))
                report['paused_snapshot'] = dict(file=str(pause_path.relative_to(ROOT)), sha256=sha(pause_path),
                                                analysis_session=before, saved_count=3)
            score5 = client.request('/api/files?' + urlencode(dict(task_id=key, view='suggestions',
                kind='startup' if kind == 'startup' else 'service', rating='5', limit=50)))
            require(kind + '_score5_filter_matches_saved_advice',
                    {row['id'] for row in score5['items']} == {row['id'] for row in advice if row['cleanup_rating'] == 5})
            report['tasks'][kind] = dict(task_id=key, analysis_session=session,
                analysis_progress=progress, usage=task['usage'], advice_revision=task['advice_revision'],
                automatic_candidate_ids=sorted(expected_auto))
            projections[kind] = [dict(id=ident, action=row['action'], can_auto_select=row['can_auto_select'],
                can_prepare=row['can_prepare'], can_logical_select=row['can_logical_select'],
                state_advice=row['assessment']['state_advice']) for ident, row in rows.items()]
        require('no_file_or_tool_operation_history', not client.request('/api/history')['history']
                and not client.request('/api/cleanup-tools/history')['history'])
        require('owned16_sentinels_remain_unchanged', unchanged())
        report.update(passed=True, state_projections=projections, real_fixture_files_unchanged=True)
    except Exception as problem:
        report['error'] = type(problem).__name__ + ': ' + str(problem)
    report['check_count'] = len(checks)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'], check_count=len(checks), report=str(destination),
                         error=report.get('error')), ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
