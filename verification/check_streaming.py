"""Loopback-only v0.9 end-to-end stream acceptance, with private bounded reports.

Requires the explicitly named unauthenticated local fixture and a disposable
Temp child. The only writes are test tasks, cancellation and diagnostic capture
inside that test profile. No approval, cleanup or real provider is requested.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.request import Request

from check_system_tools import Client, disposable_scope, fixture_metadata, loopback_url, report_destination, require, trace_payload
from serve_v090_fixture import FINAL_TEXT, PRIVATE_CANARY


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class Monitor:
    def __init__(self, client, task_id):
        self.client, self.task_id = client, task_id
        self.rows, self.errors = [], []
        self.lock, self.stop_event = threading.Lock(), threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.started = time.monotonic()
        self.thread.start()
        return self

    def run(self):
        try:
            request = Request(self.client.base + '/api/tasks/' + self.task_id + '/stream/events', headers={'Accept': 'text/event-stream'})
            with self.client.opener.open(request, timeout=6) as response:
                require('text/event-stream' in response.headers.get('Content-Type', ''), 'local_sse_content_type')
                total = 0
                while not self.stop_event.is_set():
                    line = response.readline(65537)
                    require(len(line) <= 65536, 'local_sse_line_budget')
                    total += len(line)
                    require(total <= 2 * 1024 * 1024, 'local_sse_total_budget')
                    if not line:
                        break
                    if not line.startswith(b'data: '):
                        continue
                    value = json.loads(line[6:])
                    stream = value.get('stream')
                    if stream is not None:
                        require(isinstance(stream, dict) and len(stream.get('text', '')) <= 8000, 'local_snapshot_budget')
                        with self.lock:
                            require(len(self.rows) < 256, 'local_snapshot_count_budget')
                            if not self.rows or (self.rows[-1][1]['epoch'], self.rows[-1][1]['sequence']) != (stream['epoch'], stream['sequence']):
                                self.rows.append((time.monotonic() - self.started, stream))
        except Exception as exc:
            if not self.stop_event.is_set():
                with self.lock:
                    self.errors.append(str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)

    def snapshot(self):
        with self.lock:
            return list(self.rows), list(self.errors)

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join(timeout=3)
        require(not self.thread.is_alive(), 'local_sse_monitor_did_not_close')


def wait_job(client, job_id, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get('/api/jobs/' + job_id)
        if job['status'] != 'running':
            require(job['status'] in ('completed', 'cancelled') and job.get('result'), 'fixture_task_job_failed')
            return job['result']['task']
        time.sleep(.06)
    raise RuntimeError('fixture_task_timeout')


def run_case(client, scope, mode):
    suffix = {'normal': '', 'cancel': '-取消', 'disconnect': '-断连'}[mode]
    created = client.post('/api/tasks', {'goal': '验证流式回复' + suffix, 'path': str(scope)})
    task_id, job_id = created['task']['id'], created['job_id']
    early_empty, complete_buffer_empty, pending_at, cancelled = False, False, None, False
    began = time.monotonic()
    with Monitor(client, task_id) as monitor:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            rows, errors = monitor.snapshot()
            require(not errors, errors[0] if errors else 'monitor_error')
            if mode == 'normal' and rows:
                first_turn = rows[0][1]['turn_id']
                pending = next((row for row in reversed(rows) if row[1]['turn_id'] == first_turn
                                and row[1]['status'] == 'streaming' and row[1].get('tool_pending')), None)
                if pending and pending_at is None:
                    pending_at = time.monotonic()
                    early_empty = client.get('/api/tasks/' + task_id + '/facts?limit=20')['total'] == 0
                if pending_at is not None and not complete_buffer_empty and time.monotonic() - pending_at >= .8:
                    # Writer has sent all three argument slices by now but is
                    # still holding the successful model terminal for 1 second.
                    complete_buffer_empty = client.get('/api/tasks/' + task_id + '/facts?limit=20')['total'] == 0
                    require(complete_buffer_empty, 'tool_executed_before_successful_terminal')
            if mode == 'cancel' and not cancelled and any(len(row[1].get('text', '')) >= 18 for row in rows):
                client.post('/api/jobs/' + job_id + '/cancel', {})
                cancelled = True
            job = client.get('/api/jobs/' + job_id)
            if job['status'] != 'running':
                task = wait_job(client, job_id)
                break
            time.sleep(.05)
        else:
            client.post('/api/jobs/' + job_id + '/cancel', {})
            raise RuntimeError('fixture_task_timeout')
        time.sleep(.15)
        rows, errors = monitor.snapshot()
    require(not errors, errors[0] if errors else 'monitor_error')
    require(rows and PRIVATE_CANARY not in json.dumps(rows, ensure_ascii=False), 'no_private_reasoning_in_live_stream')
    require(all(row[1].get('delivery') == 'sse' for row in rows), 'actual_sse_delivery_required')
    facts = client.get('/api/tasks/' + task_id + '/facts?limit=20')
    require(task['plan'] is None and task['last_result'] is None, 'no_action_or_plan')
    require(task['scope_count'] == 1 and Path(task['scopes'][0]['path']).resolve() == scope.resolve(), 'scope_not_widened')
    live = client.get('/api/tasks/' + task_id + '/stream')['stream']
    traces = client.traces(task_id)
    calls = [trace_payload(row).get('name') for row in traces if row['kind'] == 'tool_call']
    require(set(calls) <= {'collect_system_configuration', 'present_findings'}, 'no_unexpected_tool')
    if mode == 'normal':
        require(early_empty and complete_buffer_empty, 'partial_or_unterminated_tool_was_not_executed')
        require(task['status'] == 'completed' and task['summary'] == FINAL_TEXT, 'final_persistent_text_exact')
        require(live and live['text'] == FINAL_TEXT and live['status'] == 'completed', 'final_live_text_exact')
        require(calls == ['collect_system_configuration', 'present_findings'], 'complete_tools_executed_once')
        require(facts['total'] == 1 and facts['items'][0]['tool'] == 'collect_system_configuration', 'only_one_read_only_fact')
        by_turn = {}
        for at, row in rows:
            if row['status'] == 'streaming' and row.get('text'):
                by_turn.setdefault(row['turn_id'], []).append((at, len(row['text'])))
        growth = []
        spans = []
        for values in by_turn.values():
            increases = [(at, length) for index, (at, length) in enumerate(values) if not index or length > values[index - 1][1]]
            growth.append(len(increases))
            spans.append(increases[-1][0] - increases[0][0] if increases else 0)
        require(max(growth, default=0) >= 5 and max(spans, default=0) >= 3, 'multiple_growth_before_final_for_three_seconds')
        return dict(status='passed', snapshots=len(rows), model_turns=len(by_turn), max_growth_samples=max(growth),
                    max_growth_span_seconds=round(max(spans), 3), elapsed_seconds=round(time.monotonic() - began, 3),
                    final_text_sha256=sha(FINAL_TEXT), complete_tool_calls=len(calls), facts=facts['total'],
                    no_early_tool=True, no_actions=True)
    require(facts['total'] == 0 and not calls, 'interrupted_stream_executed_no_tool')
    if mode == 'cancel':
        require(cancelled and task['status'] == 'paused' and live.get('status') == 'cancelled', 'cancelled_without_tool_execution')
    else:
        require(task['status'] == 'error' and live.get('status') == 'error', 'disconnect_not_reported_as_success')
    return dict(status='passed', snapshots=len(rows), elapsed_seconds=round(time.monotonic() - began, 3),
                final_preview_sha256=sha(live.get('text', '')), facts=0, complete_tool_calls=0, no_actions=True)


def validate(client, scope):
    settings = client.bootstrap['settings']
    loopback_url(settings.get('base_url', ''))
    require(settings.get('provider') == 'custom' and settings.get('protocol') == 'chat_completions'
            and settings.get('model') == 'local-acceptance-fixture' and settings.get('has_key') is False,
            'explicit_unauthenticated_local_fixture_required')
    require(settings.get('send_paths') is False and settings.get('stream') is True, 'private_stream_fixture_settings_required')
    require(client.get('/api/health').get('live_executor_available') is False, 'simulation_only_required')
    before_capture = client.get('/api/developer?limit=1')['enabled']
    before_history = client.get('/api/history')['history']
    before_catalog, before_files = client.bootstrap['catalog_revision'], fixture_metadata(scope)
    try:
        if not before_capture:
            client.post('/api/developer', {'enabled': True})
        # Probe deliberately remains two JSON turns despite saved stream=true.
        probe = client.post('/api/settings/test', {})
        require(probe.get('passed') is True, 'nonstream_probe_passed')
        cases = {mode: run_case(client, scope, mode) for mode in ('normal', 'cancel', 'disconnect')}
        require(client.get('/api/history')['history'] == before_history, 'history_unchanged')
        require(client.get('/api/bootstrap?compact=1')['catalog_revision'] == before_catalog, 'catalog_unchanged')
        require(fixture_metadata(scope) == before_files, 'fixture_metadata_unchanged')
        require(client.get('/api/bootstrap?compact=1')['settings'] == settings, 'settings_unchanged')
        return dict(status='passed', phase='streaming_http_acceptance', cases=cases, nonstream_probe_passed=True,
                    no_real_provider=True, no_real_actions=True, fixture_metadata_unchanged=True,
                    limits=['报告仅核对可丢弃夹具的元数据未变；未把它称为文件逐字节哈希验证。',
                            '本验收使用Chat SSE与本地事件订阅；另三协议由专项真实回环HTTP测试覆盖。'])
    finally:
        if not before_capture:
            client.post('/api/developer', {'enabled': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--scope', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    destination = report_destination(args.report)
    try:
        report = validate(Client(args.base_url), disposable_scope(args.scope))
    except Exception as exc:
        report = dict(status='failed', phase='streaming_http_acceptance',
                      error=str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
    report['checked_at'] = datetime.now(timezone.utc).isoformat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('status', 'phase', 'error') if key in report}, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
