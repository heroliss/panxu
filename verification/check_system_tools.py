"""Verify native evidence through a loopback application and the v0.8 test peer.

The configured model must be the explicitly named, unauthenticated local fixture.
Only a new read-only task and temporary local diagnostic capture are requested.
No model configuration, cleanup, approval, file-content read or system write is
performed. Reports contain checks/counts, not raw facts, paths or conversations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


GOAL = '验证系统证据：只读核对分页、休眠和所选测试位置所在卷的信息，不扫描或处理文件。'
FIXTURE_MODEL = 'local-acceptance-fixture'
EXPECTED_TOOLS = 22
READ_ONLY_CALLS = {'collect_system_configuration', 'inspect_volume', 'list_scopes', 'present_findings'}
REPOSITORY = Path(__file__).resolve().parents[1]


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def loopback_url(value, *, app=False):
    parsed = urlsplit(value)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or '').is_loopback
        port = parsed.port
    except ValueError:
        loopback, port = False, None
    require(parsed.scheme == 'http' and loopback and port and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment, 'loopback_literal_http_required')
    require(parsed.path in ('', '/') if app else parsed.path.rstrip('/') == '/v1', 'unexpected_endpoint_path')
    return value.rstrip('/')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('redirect_refused')


class Client:
    def __init__(self, base):
        self.base = loopback_url(base, app=True)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.csrf = None
        self.bootstrap = self.get('/api/bootstrap?compact=1')
        self.csrf = self.bootstrap['csrf']

    def request(self, path, payload=None):
        headers = {'Accept': 'application/json'}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            headers.update({'Content-Type': 'application/json', 'Origin': self.base, 'X-CSRF-Token': self.csrf})
        try:
            with self.opener.open(Request(self.base + path, data=data, headers=headers), timeout=30) as response:
                require(int(response.headers.get('Content-Length', '0')) <= 4 * 1024 * 1024, 'http_result_budget')
                raw = response.read(4 * 1024 * 1024 + 1)
                require(len(raw) <= 4 * 1024 * 1024, 'http_result_budget')
                return json.loads(raw)
        except HTTPError as exc:
            with exc:
                code = exc.code
            raise RuntimeError('http_' + str(code)) from None

    def get(self, path):
        return self.request(path)

    def post(self, path, payload):
        return self.request(path, payload)

    def wait(self, job_id, timeout=150):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            job = self.get('/api/jobs/' + job_id)
            if job['status'] != 'running':
                require(job['status'] == 'completed', 'read_only_task_job_failed')
                return job['result']['task']
            time.sleep(.08)
        raise RuntimeError('read_only_task_timeout')

    def traces(self, task_id):
        items, offset = [], 0
        while offset < 200:
            page = self.get('/api/developer?' + urlencode(dict(offset=offset, limit=30)))
            items.extend(item for item in page['entries'] if item['task_id'] == task_id)
            if not page['has_more']:
                break
            offset += 30
        return sorted(items, key=lambda item: item['seq'])


def disposable_scope(value):
    scope = Path(value).absolute()
    temp = Path(tempfile.gettempdir()).absolute()
    require(scope != temp and scope.is_relative_to(temp) and scope.resolve().is_relative_to(temp.resolve())
            and scope.is_dir(), 'scope_must_be_disposable_temp_child')
    # Check every ancestor below temp before listing, and exclude links inside
    # the bounded fixture. No source bytes or personal folder traversal needed.
    current = scope
    while current != temp:
        info = current.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400), 'scope_link_refused')
        current = current.parent
    return scope


def fixture_metadata(scope):
    queue, rows = [scope], []
    while queue:
        with os.scandir(queue.pop()) as entries:
            for entry in entries:
                require(len(rows) < 512, 'disposable_fixture_entry_budget')
                info = entry.stat(follow_symlinks=False)
                require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400), 'fixture_link_refused')
                rows.append((str(Path(entry.path).relative_to(scope)), info.st_size, info.st_mtime_ns, info.st_ino))
                if stat.S_ISDIR(info.st_mode):
                    queue.append(Path(entry.path))
    return hashlib.sha256(json.dumps(sorted(rows), ensure_ascii=False).encode()).hexdigest()


def stamp(value):
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except (TypeError, ValueError):
        return False


def unknowns(value):
    result = []
    if isinstance(value, dict):
        if value.get('status') == 'unknown':
            result.append(value)
        for child in value.values():
            result.extend(unknowns(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(unknowns(child))
    return result


def trace_payload(entry):
    value = entry.get('payload', {})
    return value.get('payload', {}) if isinstance(value, dict) else {}


def validate(client, scope):
    checks = {}

    def check(name, condition):
        require(condition, name)
        checks[name] = True

    settings = client.bootstrap['settings']
    loopback_url(settings.get('base_url', ''))
    check('fixture_identity', settings.get('provider') == 'custom' and settings.get('protocol') == 'chat_completions'
          and settings.get('model') == FIXTURE_MODEL and settings.get('has_key') is False)
    check('path_privacy_enabled', settings.get('send_paths') is False)
    check('simulation_only', client.get('/api/health').get('live_executor_available') is False)
    prior_trace = client.get('/api/developer?limit=1')
    history_before = client.get('/api/history')['history']
    token_before = client.bootstrap['catalog_revision']
    files_before = fixture_metadata(scope)
    try:
        if not prior_trace['enabled']:
            client.post('/api/developer', {'enabled': True})
        created = client.post('/api/tasks', {'goal': GOAL, 'path': str(scope)})
        task_id = created['task']['id']
        task = client.wait(created['job_id'])
        check('task_completed_without_plan', task['status'] == 'completed' and task['plan'] is None and task['last_result'] is None)
        check('scope_unchanged', task['scope_count'] == 1 and len(task['scopes']) == 1
              and Path(task['scopes'][0]['path']).resolve() == scope.resolve())
        scope_id = task['scopes'][0]['id']
        facts = client.get('/api/tasks/' + task_id + '/facts?limit=20')['items']
        by_tool = {fact['tool']: fact for fact in facts}
        required = {'collect_system_configuration', 'inspect_volume'}
        check('both_durable_facts', required <= set(by_tool))
        unknown_count, numeric_count = 0, 0
        for name in required:
            fact = by_tool[name]
            result = fact['result']
            check(name + '_historical_source_time', fact['source'] == 'tool_observation' and bool(result.get('source'))
                  and stamp(fact.get('at')) and stamp(result.get('collected_at')) and stamp(result.get('completed_at'))
                  and fact['freshness'] == 'historical_observation' and fact['requires_recheck_for_action'] is True)
            check(name + '_read_only_budget', result.get('read_only') is True and result.get('actions') == []
                  and result.get('budget', {}).get('reads_file_contents') is False and result.get('warnings'))
            missing = unknowns(result)
            unknown_count += len(missing)
            check(name + '_partial_truthful', type(result.get('partial')) is bool
                  and result['status'] in ('observed', 'partial') and (not missing or result['partial'] is True)
                  and (result['status'] != 'observed' or result['partial'] is False))
            check(name + '_unknown_not_zero', all('value' not in item or item['value'] is None for item in missing))
            check(name + '_no_target_expansion', fact['scope'].get('root_ids') == [scope_id]
                  and fact['scope'].get('target_count') == 0)
        configuration = by_tool['collect_system_configuration']['result']
        volume = by_tool['inspect_volume']['result']
        check('volume_reference_bound', volume.get('scope_id') == scope_id and volume.get('scope') == 'selected_location_volume_metadata_only'
              and volume['budget'].get('max_volumes') == 1 and volume['budget'].get('enumerates_recycle_items') is False)
        traces = client.traces(task_id)
        requests = [trace_payload(row) for row in traces if row['kind'] == 'request']
        check('actual_model_requests_recorded', len(requests) >= 3)
        specs = {value['function']['name']: value['function']['parameters'] for value in requests[0].get('tools', [])}
        check('actual_registry_22_tools', len(specs) == EXPECTED_TOOLS and required <= set(specs))
        check('list_scopes_volume_contract', specs.get('list_scopes', {}).get('properties', {}).get('purpose', {}).get('enum') == ['traverse', 'volume'])
        context = json.loads(requests[0]['messages'][1]['content'])
        check('volume_reference_discoverable', any(row.get('scope_id') == scope_id for row in context.get('volume_scopes', [])))
        calls = [trace_payload(row) for row in traces if row['kind'] == 'tool_call']
        check('only_read_only_model_calls', bool(calls) and all(call.get('name') in READ_ONLY_CALLS for call in calls))
        check('both_tools_actually_called', required <= {call.get('name') for call in calls})
        check('inspect_volume_exact_scope', all(call.get('arguments', {}).get('scope_id') == scope_id for call in calls if call.get('name') == 'inspect_volume'))
        outbound = []
        for request in requests:
            for message in request.get('messages', []):
                if message.get('role') == 'tool':
                    outbound.append(json.loads(message['content']))
        sent_config = next((row for row in outbound if row.get('source') == configuration.get('source') and 'memory' in row), None)
        check('actual_redacted_tool_result_observed', sent_config is not None)
        for field in ('commit_current_bytes', 'commit_limit_bytes', 'commit_peak_since_boot_bytes', 'physical_total_bytes'):
            value = configuration.get('memory', {}).get(field)
            if type(value) is int:
                numeric_count += 1
                check(field + '_preserved_outbound', sent_config.get('memory', {}).get(field) == value)
        limits = configuration.get('configuration', {}).get('values', {}).get('paging_files', {}).get('value', [])
        sent_limits = sent_config.get('configuration', {}).get('values', {}).get('paging_files', {}).get('value', [])
        check('pagefile_limits_preserved_without_paths', limits == sent_limits)
        raw_paths = [row['path'] for row in configuration.get('active_pagefiles', {}).get('items', []) if row.get('path')]
        serialized = json.dumps(requests, ensure_ascii=False)
        check('outbound_actual_paths_hidden', all(path not in serialized for path in raw_paths) and str(scope) not in serialized)
        check('numeric_evidence_available', numeric_count >= 3)
        check('history_unchanged', client.get('/api/history')['history'] == history_before)
        check('catalog_unchanged', client.get('/api/bootstrap?compact=1')['catalog_revision'] == token_before)
        check('fixture_metadata_unchanged', fixture_metadata(scope) == files_before)
        return dict(status='passed', phase='system_tools_http_acceptance', checks=checks, task_id=task_id,
                    counts=dict(tools=len(specs), facts=len(facts), requests=len(requests), unknown_fields=unknown_count,
                                numeric_fields_checked=numeric_count),
                    limits=['list_scopes volume参数与引用已核对；本固定协议流程不额外调用该工具。',
                            '未运行真实动作；原生API单次调用不可中途打断；没有校验硬盘健康或恢复能力。'])
    finally:
        if not prior_trace['enabled']:
            client.post('/api/developer', {'enabled': False})


def report_destination(value):
    destination = Path(value).absolute().resolve()
    require(destination.suffix.lower() == '.json', 'report_must_be_json')
    if destination.is_relative_to(REPOSITORY.resolve()):
        require(destination.is_relative_to((REPOSITORY / 'verification').resolve()), 'report_requires_ignored_verification_path')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--scope', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    destination = report_destination(args.report)
    try:
        scope = disposable_scope(args.scope)
        report = validate(Client(args.base_url), scope)
    except Exception as exc:
        # Known assertion codes are fixed strings; never print raw OS paths,
        # HTTP bodies, model responses, configuration or credential text.
        code = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        report = dict(status='failed', phase='system_tools_http_acceptance', error=code)
    report['checked_at'] = datetime.now(timezone.utc).isoformat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('status', 'phase', 'error', 'counts') if key in report}, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
