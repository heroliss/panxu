"""Drive owned, preseeded analysis tasks through the unmodified packaged host.

Only loopback fixture tasks are accepted. No plan/approval, collector or live
model call is issued. GUI acceptance is a separate step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from run_tool_http_acceptance import Client

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--fixture-meta', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    meta_path, destination = args.fixture_meta.resolve(), args.report.resolve()
    if not all(path.is_relative_to(ROOT / 'verification') for path in (meta_path, destination)):
        raise ValueError('Only owned verification metadata is allowed.')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    client = Client(args.url)
    settings = client.request('/api/bootstrap?compact=1')['settings']
    if settings.get('model') != 'offline-v0260-peer' or not settings.get('base_url', '').startswith('http://127.0.0.1:'):
        raise ValueError('This helper only accepts the artificial offline model profile.')
    results = []
    report = dict(passed=False, version='0.26.0', phase='packaged_offline_loop',
                  real_fixture_files_unchanged=False, actual_reclaimed_bytes=0,
                  real_provider_used=False, scenarios=results,
                  limits=['No native GUI assertion; browser/native flows are verified separately.',
                          'Artificial selected startup, file and frozen tool snapshots only.'])
    try:
        for kind, expected in (('startup', 12), ('files', 2), ('tools', 3)):
            key = meta['tasks'][kind]
            data = client.request('/api/tasks/' + key + '/continue', {})
            current, deadline = data['task'], time.monotonic() + 110
            samples, paused_at, resumed = [], None, False
            while time.monotonic() < deadline:
                current = client.request('/api/tasks/' + key)['task']
                value = current['analysis_progress']['saved_count']
                if not samples or samples[-1] != value:
                    samples.append(value)
                    print(json.dumps(dict(case=kind, saved=value, status=current['status'])), flush=True)
                if kind == 'startup' and paused_at is None and 0 < value < expected and current.get('job_id'):
                    client.request('/api/jobs/' + current['job_id'] + '/cancel', {})
                    paused_at = value
                if current['status'] == 'paused' and paused_at is not None and not resumed:
                    assert current['analysis_progress']['saved_count'] >= paused_at
                    client.request('/api/tasks/' + key + '/continue', {})
                    resumed = True
                if current['status'] not in ('ready', 'investigating', 'paused'):
                    break
                time.sleep(.2)
            progress = current['analysis_progress']
            assert current['status'] == 'completed', (kind, current['status'])
            assert progress['target_count'] == progress['saved_count'] == expected and progress['remaining_count'] == 0
            assert current['usage']['tool_failures'] == 0
            assert progress['budget']['model_requests']['limit'] == 6 and progress['budget']['tool_calls']['limit'] == 12
            if kind == 'startup':
                assert paused_at and resumed and samples[-1] == 12
            if kind == 'tools':
                assert progress['stale_count'] == 3 and all(row['stale'] for row in current['advice'])
            results.append(dict(kind=kind, task_id=key, passed=True, samples=samples,
                                paused_at=paused_at, resumed=resumed,
                                analysis_progress=progress, usage=current['usage']))
        unchanged = all(hashlib.sha256((Path(meta['scope']) / name).read_bytes()).hexdigest() == value
                        for name, value in meta['fixture_hashes'].items())
        assert unchanged and not client.request('/api/history')['history']
        report.update(passed=True, real_fixture_files_unchanged=True)
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'], scenarios=len(results), report=str(destination), error=report.get('error'))), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
