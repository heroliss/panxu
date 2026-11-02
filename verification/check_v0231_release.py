"""Scoped task-hub HTTP acceptance using the owned offline 0.23.1 peer.

--read-only preserves every demonstration state. Without it, only the two
separate checker tasks are continued/answered; no clean/approve route is used.
Native interaction and model intelligence are outside this HTTP report.
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

NAMES = ('old-cache.tmp', 'keep-notes.txt', 'direct-only.tmp')


def fixture_hashes(scope):
    if re.fullmatch(r'panxu-owned-v0231-[A-Za-z0-9_-]+', scope.name) is None:
        raise ValueError('Only the new owned task-hub scope is accepted.')
    for parent in (scope, *scope.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Owned fixture must not traverse a link.')
    result = {}
    for name in NAMES:
        path = scope / name; info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 100000 or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Owned samples must be small ordinary files.')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run(base, metadata, output, artifact=None, *, read_only=False):
    metadata, output = metadata.resolve(), output.resolve()
    if (not metadata.is_relative_to(ROOT / 'verification') or not output.is_relative_to(ROOT / 'verification')
            or metadata == output or metadata.name != 'fixture-meta.json'):
        raise ValueError('Use separate metadata and report under verification/.')
    meta = json.loads(metadata.read_text(encoding='utf-8'))
    scope = Path(meta['scope'])
    if fixture_hashes(scope) != meta['fixture_hashes']:
        raise ValueError('Owned files changed before checking.')
    if artifact is not None:
        artifact = artifact.resolve()
        if not artifact.is_relative_to(ROOT / 'dist') or artifact.suffix.lower() != '.exe':
            raise ValueError('Only the explicit dist EXE can be attributed.')
    api = API(base, time.monotonic() + 90)
    report = dict(passed=False, status='failed', version=__version__,
        phase='owned_final_exe_task_hub_http' if artifact else 'owned_source_task_hub_http',
        at=datetime.now(timezone.utc).isoformat(), checks=[], read_only=read_only,
        real_fixture_files_unchanged=False, actual_reclaimed_bytes=0, simulation_only=True,
        native_ui_verified=False, external_provider_used=False, real_credentials_used=False,
        artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact else None,
        limits=['Deterministic offline SSE peer; not a real cloud/model quality evaluation.',
                'Public state/continuation checks only; native layout and clicks are recorded separately.',
                'No approve/delete/tool-launch or system-changing HTTP endpoint is called.'])

    def check(name, condition, **evidence):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(dict(name=name, status='passed', **evidence))

    def task(key):
        return api.get('/api/tasks/' + key)['task']

    def finish(job):
        while True:
            row = api.get('/api/jobs/' + job['job_id'])
            if row['status'] != 'running':
                if row['status'] != 'completed':
                    raise AssertionError('Owned continuation job did not complete.')
                return
            time.sleep(.06)

    try:
        health = api.get('/api/health')
        check('current_version_and_simulation_boundary', health['version'] == __version__
            and health['mode'] == 'simulation' and health['live_executor_available'] is False)
        boot = api.get('/api/bootstrap', {'compact': 1}); api.csrf = boot['csrf']
        settings = boot['settings']
        check('only_owned_loopback_peer_configured', settings['model'] == 'offline-v0231-peer'
            and settings['base_url'] == meta['provider_url'] and settings['has_key'] is False
            and settings['send_paths'] is False)
        demo = {name: task(key) for name, key in meta['tasks'].items()}
        expected = {'paused': 'paused', 'error': 'error', 'needs_model': 'needs_model',
                    'budget': 'budget_exhausted', 'completed': 'completed', 'question': 'needs_input'}
        check('mixed_states_keep_existing_advice', all(demo[name]['status'] == state
            and demo[name]['advice_total'] == 2
            and (artifact is None or demo[name].get('task_kind') == 'analysis') for name, state in expected.items()))
        question = demo['question']['question']
        check('question_has_stable_native_answer_refs', bool(question['id']) and question['version'] >= 1
            and len(question['options']) == 2 and all(option['id'] for option in question['options']))
        tool = demo['tools_only']
        check('tool_only_result_does_not_invent_a_file_list', tool['status'] == 'completed'
            and tool['advice_total'] == 0 and tool['plan'] is None
            and tool['extra_suggestions'][0]['options'][0]['tool_id'] == 'recycle_bin')
        direct = demo['direct_completed']
        check('direct_operation_has_a_real_simulation_receipt', direct['status'] == 'completed'
            and direct['last_result']['succeeded'] == 1
            and direct['last_result']['actual_reclaimed_bytes'] == 0
            and (artifact is None or direct.get('task_kind') == 'operation'))
        baseline = len(api.get('/api/history')['history'])
        if not read_only:
            paused = task(meta['checker_tasks']['paused']); before = paused['advice_total']
            finish(api.post('/api/tasks/' + paused['id'] + '/continue', {}))
            continued = task(paused['id'])
            check('paused_task_continues_without_losing_advice', continued['status'] == 'completed'
                and continued['advice_total'] == before == 2 and not continued['question'])
            waiting = task(meta['checker_tasks']['question']); q = waiting['question']
            finish(api.post('/api/tasks/' + waiting['id'] + '/answer',
                {'version': q['version'], 'question_id': q['id'], 'option_id': q['options'][0]['id']}))
            answered = task(waiting['id'])
            check('answer_resumes_the_same_task_and_preserves_advice', answered['status'] == 'completed'
                and answered['advice_total'] == 2 and not answered['question'])
        check('no_new_operation_batches_from_task_navigation', len(api.get('/api/history')['history']) == baseline)
        endpoint = urlsplit(meta['provider_stats'])
        check('peer_statistics_endpoint_is_loopback', endpoint.scheme == 'http'
            and endpoint.hostname == '127.0.0.1' and endpoint.path == '/stats'
            and not endpoint.username and not endpoint.password)
        with api.opener.open(meta['provider_stats'], timeout=3) as response:
            stats = json.loads(response.read(64000))
        check('task_protocol_and_path_privacy', not stats['errors'] and stats['path_leaks'] == 0)
        report['real_fixture_files_unchanged'] = fixture_hashes(scope) == meta['fixture_hashes']
        check('owned_sample_bytes_unchanged', report['real_fixture_files_unchanged'])
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
