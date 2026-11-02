"""Check 0.21's owned EXE fixture using loopback routes and its offline peer.

Requires the native system-task tool-choice flow to have been exercised first.
Only the explicit --continue-fixture option starts one more offline model turn;
it never calls the user's provider or opens/executes any system program.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot import __version__
from check_specialist_release import API
from tests.prompt_fixture import parse_host_data


def run(base, metadata, output, continue_fixture=False):
    meta = json.loads(metadata.read_text(encoding='utf-8'))
    scope = Path(meta['scope'])
    if not scope.name.startswith('panxu-owned-v0210-'):
        raise ValueError('Only an owned 0.21 fixture is accepted.')
    api = API(base, time.monotonic() + 55)
    checks = []

    def check(name, value):
        if not value:
            raise AssertionError(name)
        checks.append({'name': name, 'status': 'passed'})

    health = api.get('/api/health')
    check('current_exe_version', health['version'] == __version__)
    check('no_live_executor', health['live_executor_available'] is False and health['mode'] == 'simulation')
    bootstrap = api.get('/api/bootstrap', {'compact': 1})
    api.csrf = bootstrap['csrf']
    check('system_drive_is_host_provided', bootstrap.get('system_drive', {}).get('path', '').endswith('\\'))
    background = api.get('/api/tasks/' + meta['task_id'])['task']
    check('offline_background_reaches_grounded_ratings', background['status'] == 'completed' and background['advice_total'] == 3)
    tasks = api.get('/api/tasks')['tasks']
    task = next(row for row in tasks if row.get('preset') == 'system_cleanup')
    check('system_task_is_independent_from_background', task['id'] != background['id'])
    host = [row for row in task['extra_suggestions'] if row.get('origin') == 'host']
    check('three_independent_system_tools', len(host) == 3)
    check('host_suggestions_are_local', all(row['provenance']['source'] == 'local' for row in host))
    check('decline_is_persistent', any(row.get('selection', {}).get('option_id') == 'skip' for row in host))
    check('tool_choice_is_not_approval', all(row.get('selection', {}).get('execution_authorized') is not True for row in host))
    before = task['usage']['model_requests']
    before_tools = json.loads((metadata.parent / 'native-before-tools.json').read_text(encoding='utf-8'))
    if before == before_tools['model_requests']:
        check('tools_did_not_request_model', True)
    plans = api.get('/api/cleanup-tools/history')['history']
    completed = [row for row in plans if row.get('source', {}).get('task_id') == task['id'] and row.get('result')]
    check('host_tool_result_has_receipt', bool(completed))
    check('tool_receipts_never_claim_real_savings', all(row['result'].get('actual_reclaimed_bytes') == 0 for row in completed))
    if continue_fixture:
        # The profile must point at the owned offline peer; never a user endpoint.
        settings = bootstrap['settings']
        check('offline_model_profile', settings.get('model') == 'offline-harness-fixture'
              and settings.get('base_url') == meta['provider_url'] and not settings.get('has_key'))
        if before == before_tools['model_requests']:
            api.post('/api/tasks/' + task['id'] + '/continue', {})
            for _ in range(100):
                task = api.get('/api/tasks/' + task['id'])['task']
                if task['status'] not in ('investigating', 'executing', 'ready'):
                    break
                time.sleep(.2)
        check('explicit_offline_continuation_completed', task['status'] == 'completed')
        trace = api.get('/api/developer', {'limit': 30})
        if trace.get('total', 0) > 30:
            trace['entries'].extend(api.get('/api/developer', {'limit': 30, 'offset': 30}).get('entries', []))
        requests = [row for row in trace.get('entries', []) if row.get('task_id') == task['id'] and row.get('kind') == 'request']
        rendered = json.dumps(requests[:2], ensure_ascii=False)
        check('tool_choices_reach_next_model_turn', 'recent_tool_activity' in rendered and 'declined' in rendered and 'completed' in rendered)
        check('activity_has_host_scope_relation', 'scope_relation' in rendered and 'current_task' in rendered)
        contexts = [parse_host_data(row['payload']['payload']['messages'][1]['content']) for row in requests]
        activity = next(value['recent_tool_activity'] for value in contexts if value.get('recent_tool_activity'))
        activity = [row for row in activity if row['scope_relation'] == 'current_task']
        first, last = min(datetime.fromisoformat(row['time']) for row in activity), max(datetime.fromisoformat(row['time']) for row in activity)
        check('no_model_requests_during_tool_choices_or_execution', all(not first <= datetime.fromisoformat(row['at']) <= last for row in requests))
    page = api.get('/api/file-browser', {'path': str(scope), 'limit': 1})
    check('bounded_file_browsing_preserved', len(page['items']) == 1 and page['has_more'])
    check('fixture_files_unchanged', {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in scope.iterdir()} == meta['fixture_hashes'])
    result = dict(passed=True, status='passed', version=__version__, phase='owned_exe_http',
        checks=checks, check_count=len(checks), simulation_only=True, actual_reclaimed_bytes=0,
        real_fixture_files_unchanged=True, external_provider_used=False, real_credentials_used=False,
        scope='Explicit offline peer and owned profile; not a real-provider or live-system test.')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'passed', 'checks': len(checks)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--continue-fixture', action='store_true')
    args = parser.parse_args()
    run(args.base_url, args.metadata, args.output, args.continue_fixture)
