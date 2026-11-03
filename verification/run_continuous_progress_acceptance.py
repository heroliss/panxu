"""Drive only owned v0.27 loopback tasks; native UI is verified separately."""
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
    parser.add_argument('--scenario', choices=('startup', 'services', 'all'), default='all')
    parser.add_argument('--pause-at', type=int)
    parser.add_argument('--target-kind', choices=('source-fixture', 'unmodified-exe'), required=True)
    args = parser.parse_args()
    metadata_path, destination = args.fixture_meta.resolve(), args.report.resolve()
    if not all(path.is_relative_to(ROOT / 'verification') for path in (metadata_path, destination)):
        raise ValueError('Only owned verification metadata and report paths are accepted.')
    meta = json.loads(metadata_path.read_text(encoding='utf-8'))
    client = Client(args.url)
    settings = client.request('/api/bootstrap?compact=1')['settings']
    if settings.get('model') != 'offline-v0270-peer' or not settings.get('base_url', '').startswith('http://127.0.0.1:'):
        raise ValueError('Only the artificial offline peer profile is accepted.')
    report = dict(passed=False, target_kind=args.target_kind, scenarios=[], actual_reclaimed_bytes=0,
        real_provider_used=False, real_credentials_used=False, native_gui_assertion=False,
        limits=['Only frozen artificialstartup/service objects and loopbackSSE.',
                'No real cleanup, collector, approval or external model call.',
                'The 14-request threshold applies to an uninterrupted startup case; pause/resume tested separately.'])
    try:
        health = client.request('/api/health')
        report['version'] = health['version']
        assert health['mode'] == 'simulation' and health['live_executor_available'] is False
        kinds = ('startup', 'services') if args.scenario == 'all' else (args.scenario,)
        for kind in kinds:
            key = meta['tasks'][kind]
            task = client.request('/api/tasks/' + key)['task']
            assert task['status'] == 'ready', 'Use an unused frozen task; completed/partial data is never reset.'
            client.request('/api/tasks/' + key + '/continue', {})
            end = time.monotonic() + 150
            samples, requested_pause, paused, continued = [], False, False, False
            while time.monotonic() < end:
                task = client.request('/api/tasks/' + key)['task']
                progress = task['analysis_progress']
                value = progress['saved_count']
                if not samples or samples[-1] != value:
                    samples.append(value)
                    print(json.dumps(dict(case=kind, saved=value, status=task['status'])), flush=True)
                if kind == 'startup' and args.pause_at is not None and not requested_pause and value >= args.pause_at and value < 12 and task.get('job_id'):
                    client.request('/api/jobs/' + task['job_id'] + '/cancel', {})
                    requested_pause = True
                if task['status'] == 'paused' and requested_pause and not continued:
                    paused = True
                    client.request('/api/tasks/' + key + '/continue', {})
                    continued = True
                elif task['status'] not in ('ready', 'investigating'):
                    break
                time.sleep(.15)
            expected = 12 if kind == 'startup' else 4
            assert task['status'] == 'completed', (kind, task['status'], task.get('error'))
            assert task['analysis_progress']['saved_count'] == task['analysis_progress']['target_count'] == expected
            assert task['usage']['tool_failures'] == 0
            session = task.get('analysis_session') or task.get('budget', {}).get('session') or {}
            if kind == 'startup':
                assert task['usage']['model_requests'] >= meta['minimum_continuous_requests']
                assert len([n for n in samples if 0 < n < 12]) >= 5
                if args.pause_at is not None:
                    assert paused and continued
            actual = {row['id']: row for row in task['advice']}
            for ident in meta['startup_ids'] if kind == 'startup' else meta['service_ids']:
                expected_advice = meta['expected_state_advice'][ident]
                advice = actual[ident]
                assert advice['state_recommendation'] == expected_advice['recommendation']
                assert advice['state_scope'] == expected_advice['scope']
                assert advice['cleanup_rating'] == expected_advice['score']
            report['scenarios'].append(dict(kind=kind, task_id=key, passed=True, samples=samples,
                paused=paused, resumed=continued, usage=task['usage'], analysis_session=session,
                analysis_progress=task['analysis_progress']))
        unchanged = all(hashlib.sha256((Path(meta['scope']) / name).read_bytes()).hexdigest() == value
                        for name, value in meta['fixture_hashes'].items())
        assert unchanged and not client.request('/api/history')['history']
        report.update(passed=True, real_fixture_files_unchanged=True)
    except Exception as problem:
        report['error'] = type(problem).__name__ + ': ' + str(problem)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'], report=str(destination), error=report.get('error'))), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
