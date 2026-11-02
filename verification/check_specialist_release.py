"""HTTP acceptance of an owned PanXu fixture; never native UI acceptance.

Only fixed loopback HTTP routes are used. The only writes outside the app's
simulation store are this script's report under this project's verification
directory. Existing owned sample files are hashed but never modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise RuntimeError('Redirects are prohibited in this loopback-only check.')


class API:
    def __init__(self, base_url, deadline):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment or not parsed.port):
            raise ValueError('Use an explicit http://127.0.0.1:PORT loopback URL.')
        self.base = f'http://127.0.0.1:{parsed.port}'
        self.deadline = deadline
        self.csrf = None
        self.requests = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, payload=None, *, expect=200, raw=False):
        if not path.startswith('/api/') and path not in ('/cleanup-tools.js', '/specialist-links.js', '/cleanup-tools.css'):
            raise ValueError('Route is outside the fixed acceptance surface.')
        if path.startswith('/api/cleanup-tools/') and path.rsplit('/', 1)[-1] == 'open-settings':
            raise ValueError('Opening system settings or external pages is prohibited.')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Acceptance exceeded its 30 second budget.')
        headers = {}
        data = None
        if payload is not None:
            if not self.csrf:
                raise ValueError('A current local CSRF token is required.')
            headers = {'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf, 'Origin': self.base}
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        self.requests += 1
        try:
            response = self.opener.open(request, timeout=min(3.0, remaining))
        except urllib.error.HTTPError as response_error:
            response = response_error
        with response:
            status, mime = response.status, response.headers.get('Content-Type', '')
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError('Acceptance response exceeded the fixed output budget.')
        if status != expect:
            # Never copy a server response, configuration or token into a report.
            raise AssertionError(f'Unexpected HTTP status {status}; expected {expect}.')
        if raw:
            return body, mime
        return json.loads(body)

    def get(self, path, query=None):
        if query:
            path += '?' + urllib.parse.urlencode(query)
        return self.request(path)

    def post(self, path, payload, *, expect=200):
        return self.request(path, payload, expect=expect)


def sample_hashes(scope):
    if not scope.is_dir() or not scope.name.startswith('panxu-owned-v0190-'):
        raise ValueError('The scope must be an existing panxu-owned-v0190-* fixture.')
    for ancestor in (scope, *scope.parents):
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Fixture paths must not traverse links or reparse points.')
    result = {}
    # No recursive scan and no caller-selected arbitrary file/body reader.
    for name in ('应用缓存示例/sample-cache.bin', '我的资料.txt', '旧诊断样本.dmp'):
        path = scope / name
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400 or info.st_size > 2_000_000
                or not path.resolve().is_relative_to(scope.resolve())):
            raise ValueError('An owned sample is missing, linked or outside its size budget.')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run(base_url, fixture_meta, output):
    started = time.monotonic()
    report = dict(phase='http_owned_fixture_verification', status='failed', passed=False,
        scope='Loopback HTTP and owned fixture only; no native UI clicks were tested.',
        native_ui_verified=False, system_settings_opened=False, live_operations_performed=False,
        at=datetime.now(timezone.utc).isoformat(), checks=[])
    project = Path(__file__).resolve().parents[1]
    verification = project / 'verification'
    fixture_meta, output = fixture_meta.resolve(), output.resolve()
    if not fixture_meta.is_relative_to(verification) or not output.is_relative_to(verification) or output == fixture_meta:
        raise ValueError('Metadata and report must be separate files inside project verification/.')
    meta = json.loads(fixture_meta.read_text(encoding='utf-8'))
    scope = Path(meta['scope'])
    before = sample_hashes(scope)
    report['fixture_hashes_before'] = before
    api = API(base_url, started + 30)

    def check(name, condition, **evidence):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(dict(name=name, status='passed', **evidence))

    try:
        health = api.get('/api/health')
        check('version_and_simulation_boundary', str(health.get('version', '')).startswith('0.19.')
              and health.get('mode') == 'simulation' and health.get('live_executor_available') is False,
              version=health.get('version'))
        bootstrap = api.get('/api/bootstrap', {'compact': 1})
        api.csrf = bootstrap['csrf']
        del bootstrap
        for path, expected_mime in (('/cleanup-tools.js', 'text/javascript'),
                                    ('/specialist-links.js', 'text/javascript'),
                                    ('/cleanup-tools.css', 'text/css')):
            body, mime = api.request(path, raw=True)
            check('static_asset_' + path[1:], mime.startswith(expected_mime) and len(body) > 100,
                  size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        tools = api.get('/api/cleanup-tools')
        check('shared_registry_25_tools', tools.get('total') == 25 and len(tools.get('items', [])) == 25
              and tools.get('simulation_only') is True)
        inspected = api.post('/api/cleanup-tools/inspect', {'tool_id': 'pagefile', 'parameters': {}})
        evidence = inspected.get('evidence') or {}
        check('fixed_read_only_pagefile_observation', inspected.get('read_only') is True
              and evidence.get('read_only') is True and evidence.get('status') in ('observed', 'partial', 'unsupported')
              and evidence.get('budget', {}).get('reads_file_contents') is False,
              observation_status=evidence.get('status'))
        task_id = meta['task_id']
        task = api.get('/api/tasks/' + task_id)['task']
        model_requests_before = task.get('usage', {}).get('model_requests')
        task_scope_paths = {str(row.get('path', '')).casefold() for row in task.get('scopes', [])}
        check('task_is_owned_fixture', str(scope).casefold() in task_scope_paths)
        page = api.get('/api/files', {'task_id': task_id, 'limit': 100})
        rows = {row['id']: row for row in page['items']}
        cache_id = meta['item_ids']['应用缓存示例']
        child_id = meta['item_ids']['sample-cache.bin']
        diagnostic_id = meta['item_ids']['旧诊断样本.dmp']
        cache = rows[cache_id]
        link = api.get('/api/specialist', {'task_id': task_id, 'item_id': cache_id, 'signature': cache['signature']})
        check('cache_reference_is_typed_and_current', link['specialist']['tool_id'] == 'browser_cache'
              and link['specialist']['mode'] == 'replace' and link['source']['item_id'] == cache_id
              and type(link['source'].get('advice_version')) is int)
        child_link = api.get('/api/specialist', {'task_id': task_id, 'item_id': child_id, 'signature': rows[child_id]['signature']})
        check('inherited_reference_reuses_parent_operation', child_link['specialist']['parameters'] == link['specialist']['parameters']
              and child_link['source']['item_id'] == cache_id)
        automatic = api.post('/api/assessments/select', dict(task_id=task_id, category='high', mode='automatic',
            revision=page['revision'], selection_revision=page['selection_revision']))
        forbidden = {cache_id, child_id, diagnostic_id}
        check('ordinary_auto_selection_excludes_specialist_targets', not forbidden.intersection(automatic['ids'])
              and all(rows[ident]['can_prepare'] is False for ident in forbidden), selected_count=len(automatic['ids']))
        diagnostic = api.get('/api/specialist', {'task_id': task_id, 'item_id': diagnostic_id,
                             'signature': rows[diagnostic_id]['signature']})
        suggestion = diagnostic['extra_suggestion']
        check('extra_dialog_is_linked', suggestion['id'] == 'diagnostic-options'
              and diagnostic['source']['task_id'] == task_id)
        # Leave browser-cache and diagnostic selections unchanged while native
        # acceptance may be viewing them. Exercise the independent UV choice.
        suggestion = next(row for row in task['extra_suggestions'] if row['id'] == 'development-options')
        rejected = api.post('/api/tasks/' + task_id + '/specialist-answer',
            dict(suggestion_id=suggestion['id'], option_id='uv', version=suggestion['version'] + 1), expect=409)
        check('stale_answer_version_rejected', rejected.get('code') == 'specialist_stale')
        answer = api.post('/api/tasks/' + task_id + '/specialist-answer',
            dict(suggestion_id=suggestion['id'], option_id='uv', version=suggestion['version']))
        check('answer_does_not_authorize_execution', answer.get('execution_authorized') is False
              and answer['selection'].get('execution_authorized') is False
              and answer['specialist']['tool_id'] == 'uv_cache')
        browser_plan = api.post('/api/cleanup-tools/prepare', dict(tool_id='browser_cache',
            parameters=link['specialist']['parameters'], source={'task_id': task_id}))
        uv_plan = api.post('/api/cleanup-tools/prepare', dict(tool_id='uv_cache', parameters={'operation': 'prune'},
            source={'task_id': task_id}))
        check('browser_and_uv_parameters_are_frozen', browser_plan['parameters'] == link['specialist']['parameters']
              and uv_plan['parameters'] == {'operation': 'prune'} and bool(browser_plan.get('frozen_hash'))
              and bool(uv_plan.get('frozen_hash')))
        rejected = api.post('/api/cleanup-tools/execute',
            {'plan_id': browser_plan['id'], 'version': browser_plan['version'] + 1}, expect=409)
        check('wrong_plan_version_rejected', rejected.get('code') == 'cleanup_plan_changed')
        restored = api.get('/api/cleanup-plans/' + uv_plan['id'])
        check('pending_plan_can_be_resumed_without_execution', restored['status'] == 'awaiting_review'
              and restored['frozen_hash'] == uv_plan['frozen_hash'] and not restored.get('result'))
        cancelled = api.post('/api/cleanup-tools/cancel', {'plan_id': uv_plan['id'], 'version': uv_plan['version']})
        check('review_cancellation_is_durable', cancelled['status'] == 'cancelled'
              and api.get('/api/cleanup-plans/' + uv_plan['id'])['status'] == 'cancelled')
        api.post('/api/cleanup-tools/cancel', {'plan_id': browser_plan['id'], 'version': browser_plan['version']})
        batch = api.post('/api/cleanup-tools/prepare-many', dict(items=[
            {'tool_id': 'browser_cache', 'parameters': link['specialist']['parameters']},
            {'tool_id': 'browser_cache', 'parameters': link['specialist']['parameters']},
            {'tool_id': 'uv_cache', 'parameters': {'operation': 'prune'}}], source={'task_id': task_id}))
        check('batch_deduplicates_and_keeps_two_tools', len(batch['entries']) == 2
              and {entry['tool_id'] for entry in batch['entries']} == {'browser_cache', 'uv_cache'})
        result = api.post('/api/cleanup-tools/execute', {'plan_id': batch['id'], 'version': batch['version']})
        check('explicit_simulation_execution_is_verified', result['status'] == 'succeeded' and result['total'] == 2
              and result['succeeded'] == 2 and result['actual_reclaimed_bytes'] == 0
              and result['simulation_only'] is True and all(row['verified'] for row in result['items']))
        repeated = api.post('/api/cleanup-tools/execute', {'plan_id': batch['id'], 'version': batch['version']})
        check('repeat_execute_reuses_receipts', result == repeated)
        saved = api.get('/api/cleanup-plans/' + batch['id'])
        check('execution_result_is_restorable', saved['status'] == 'completed' and saved['result'] == result)
        after_task = api.get('/api/tasks/' + task_id)['task']
        returned = after_task['last_specialist_result']
        check('source_task_receives_truthful_result', returned['plan_id'] == batch['id']
              and returned['windows_changed'] is False and returned['actual_reclaimed_bytes'] == 0
              and returned['simulation_only'] is True)
        facts, offset = [], 0
        while offset < 200:
            fact_page = api.get('/api/tasks/' + task_id + '/facts', {'offset': offset, 'limit': 20})
            facts.extend(fact_page['items'])
            if not fact_page.get('has_more'):
                break
            offset += len(fact_page['items'])
        receipts = [row for row in facts if row.get('tool') == 'specialist_result'
                    and row.get('result', {}).get('plan_id') == batch['id']]
        check('facts_contain_one_receipt_not_duplicate_replay', len(receipts) == 1
              and any(row.get('tool') == 'specialist_answer' for row in facts))
        history = api.get('/api/cleanup-tools/history')['history']
        histories = {row['id']: row for row in history}
        check('history_retains_completed_and_cancelled', histories[batch['id']]['status'] == 'completed'
              and histories[uv_plan['id']]['status'] == 'cancelled'
              and histories[browser_plan['id']]['status'] == 'cancelled')
        check('no_model_request_added', after_task.get('usage', {}).get('model_requests') == model_requests_before)
        report.update(status='passed', passed=True)
    except Exception as exc:
        report['failure'] = dict(type=type(exc).__name__, message=str(exc)[:240])
    finally:
        try:
            after = sample_hashes(scope)
            report['fixture_hashes_after'] = after
            check('owned_sample_files_unchanged', before == after, file_count=len(before))
        except Exception as exc:
            report.update(status='failed', passed=False)
            report['fixture_hash_failure'] = dict(type=type(exc).__name__, message=str(exc)[:240])
        report['http_requests'] = api.requests
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['check_count'] = len(report['checks'])
        if report['elapsed_seconds'] >= 30:
            report.update(status='failed', passed=False)
            report['failure'] = dict(type='TimeoutError', message='Acceptance exceeded its 30 second budget.')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--fixture-meta', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.base_url, args.fixture_meta, args.output)
    print(json.dumps({key: report[key] for key in ('status', 'check_count', 'http_requests', 'elapsed_seconds')}, ensure_ascii=False))
    if not report['passed']:
        print(json.dumps(report.get('failure') or report.get('fixture_hash_failure'), ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
