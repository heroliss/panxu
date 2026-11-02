"""Read-only acceptance of the completed artificial startup task and list APIs.

The caller runs the UI/loop separately. This helper never starts analysis,
changes a setting, invokes a collector, approves a plan or launches a process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from run_tool_http_acceptance import Client

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ('app.js', 'tasks.js', 'files.js', 'cleanup-tools.js', 'hierarchy.css',
          'analysis-progress.js', 'analysis-progress.css',
          'background-workspace.js', 'background-workspace.css')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--fixture-meta', type=Path, required=True)
    parser.add_argument('--task-id')
    parser.add_argument('--target-kind', choices=('source-fixture', 'unmodified-exe'), required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    meta_path, destination = args.fixture_meta.resolve(), args.report.resolve()
    if not meta_path.is_relative_to(ROOT / 'verification') or not destination.is_relative_to(ROOT / 'verification'):
        raise ValueError('Acceptance metadata and report must remain inside owned verification data.')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    client, checks = Client(args.url), []

    def require(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
        if not condition:
            raise AssertionError(name)

    def files_unchanged():
        return all(digest(Path(meta['scope']) / name) == value for name, value in meta['fixture_hashes'].items())

    report = dict(passed=False, version='0.26.0', target_kind=args.target_kind,
                  actual_reclaimed_bytes=0, real_fixture_files_unchanged=False,
                  real_provider_used=False, checks=checks, asset_hashes={},
                  limits=['Read-only API checks; UI actions and pause/resume are separately observed.',
                          'Artificial startup metadata and loopback SSE peer only.',
                          'No real deletion, startup/service/process change or external model request.'])
    try:
        health = client.request('/api/health')
        require('current_version_and_simulation', health.get('version') == '0.26.0'
                and health.get('mode') == 'simulation' and health.get('live_executor_available') is False)
        require('owned_samples_initially_unchanged', files_unchanged())
        for name in ASSETS:
            status, data = client.response('/' + name)
            expected = digest(ROOT / 'web' / name)
            actual = hashlib.sha256(data).hexdigest()
            require('current_asset_' + name, status == 200 and actual == expected)
            report['asset_hashes'][name] = actual
        settings = client.request('/api/bootstrap?compact=1')['settings']
        require('isolated_offline_provider', settings.get('model') == 'offline-v0260-peer'
                and settings.get('send_paths') is False
                and settings.get('base_url', '').startswith('http://127.0.0.1:'))
        candidates = client.request('/api/tasks')['tasks']
        if args.task_id:
            task = client.request('/api/tasks/' + args.task_id)['task']
        else:
            task = next(value for value in candidates if value.get('scope_kind') == 'applications'
                        and value.get('analysis_progress', {}).get('target_count') == 12
                        and value.get('status') == 'completed')
        approved = set(meta['startup_ids'])
        progress = task['analysis_progress']
        advice = client.request('/api/tasks/' + task['id'] + '/advice?limit=50')['items']
        require('exact_owned_startup_scope', task.get('scope_kind') == 'applications'
                and task.get('analysis', {}).get('target_count') == 12
                and {row['id'] for row in advice} == approved)
        require('completed_only_after_full_coverage', task['status'] == 'completed'
                and progress['target_count'] == 12 and progress['remaining_count'] == 0)
        require('all_twelve_results_available', len(advice) == progress['saved_count'] == 12
                and len({row['id'] for row in advice}) == 12)
        require('progress_distribution_is_complete', sum(progress['rating_counts'].values()) == progress['rated_count']
                and progress['rated_count'] + progress['skipped_count'] + progress['no_advice_count'] == 12)
        require('round_budget_uses_correct_units', progress['budget']['model_requests']['limit'] == 6
                and progress['budget']['tool_calls']['limit'] == 12)
        require('revision_and_zero_tool_failures', isinstance(task['advice_revision'], int)
                and task['usage']['tool_failures'] == 0)
        page = client.request('/api/files?' + urlencode(dict(task_id=task['id'], view='suggestions', kind='startup', rating='5')))
        expected_ids = {row['id'] for row in advice if row.get('cleanup_rating') == 5}
        require('score_filter_matches_all_saved_results', {row['id'] for row in page['items']} == expected_ids
                and page['total'] == len(expected_ids))
        selection = client.request('/api/assessments/select', dict(task_id=task['id'], category='5', mode='manual',
            include_inherited=True, view='suggestions', kind='startup', limit=500, revision=page['revision'],
            selection_revision=page['selection_revision']))
        require('filtered_select_all_has_exact_same_scope', {row['id'] for row in selection['items']} == expected_ids)
        require('no_execution_history', not client.request('/api/history')['history'])
        require('owned_samples_remain_unchanged', files_unchanged())
        report.update(passed=True, task_id=task['id'], analysis_progress=progress,
                      usage=task['usage'], advice_revision=task['advice_revision'], real_fixture_files_unchanged=True)
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    report['check_count'] = len(checks)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(report=str(destination), passed=report['passed'], check_count=len(checks),
                         target_kind=args.target_kind, error=report.get('error')), ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
