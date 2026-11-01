from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs

from . import __version__
from .duplicates import verify_duplicates
from .catalog import FileCatalog
from .llm import ModelAssistant
from .lease import DataLease
from .live_streams import LiveStreams
from .knowledge import SkillRegistry
from .icons import IconService, IconNotFound
from .recommendations import build_recommendations
from .scanner import scan_directory
from .scan_index import ScanIndex
from .simulation import AppError, SimulationState
from .tasks import TaskHarness
from .trace import DeveloperTrace
from .updates import UpdateChecker, about
from .windows import disks, inventory, validate_system_items


UI_DEFAULTS = dict(theme='system', developer_live=False, developer_conversation=True,
                   developer_skills=True, developer_tools=False)


def common_cache_paths():
    """Only well-known current-profile locations; no broad drive traversal."""
    local = os.environ.get('LOCALAPPDATA')
    if not local or not os.path.isabs(local):
        return []
    root = Path(local)
    candidates = [root / 'D3DSCache', root / 'NVIDIA' / 'DXCache',
                  root / 'pip' / 'Cache', root / 'npm-cache']
    return [str(path) for path in candidates if path.is_dir()]


class Application:
    def __init__(self, data_dir, *, developer_fixtures=False):
        self.lease = DataLease(data_dir)
        self.state = SimulationState(data_dir)
        self.developer_fixtures = developer_fixtures
        if not developer_fixtures:
            self.state.discard_development_fixtures()
        self.assistant = ModelAssistant(Path(data_dir))
        self.trace = DeveloperTrace(data_dir, lambda value: self.assistant.clean_text(value, limit=1024 * 1024))
        self.assistant.trace_hook = self.trace.record
        self.live_streams = LiveStreams()
        self.stream_connections = threading.BoundedSemaphore(4)
        self.assistant.stream_hook = lambda event: self.live_streams.record(self.trace.current_task_id(), event)
        self.scan_index = ScanIndex(Path(data_dir) / 'scan_index.sqlite3')
        self.skills = SkillRegistry()
        self.updates = UpdateChecker()
        self.harness = TaskHarness(self.state, self.assistant, trace=self.trace, scan_index=self.scan_index, skill_registry=self.skills)
        self.catalog = FileCatalog(self.state, self.patterns)
        self.icons = IconService(self.icon_item)
        self.recommendation_cache = None
        self.recommendation_generation = 0
        self.settings_revision = 0
        self.csrf = secrets.token_urlsafe(32)
        self.jobs = {}
        self.job_lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='read-only-scan')
        self.web_root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'web'

    def patterns(self):
        return self.assistant.public_settings().get('protect_patterns', [])

    def ui_preferences(self, payload=None):
        with self.state.lock:
            row = self.state.db.execute("SELECT value FROM meta WHERE key='ui_preferences'").fetchone()
            try:
                saved = json.loads(row[0]) if row else {}
            except (ValueError, TypeError):
                saved = {}
            current = dict(UI_DEFAULTS)
            if isinstance(saved, dict):
                for key, value in saved.items():
                    if key in UI_DEFAULTS and ((key == 'theme' and value in ('dark', 'light', 'system'))
                                              or (key != 'theme' and type(value) is bool)):
                        current[key] = value
            if payload is not None:
                if not isinstance(payload, dict) or set(payload) - set(UI_DEFAULTS):
                    raise AppError('界面偏好格式无效。')
                for key, value in payload.items():
                    if (key == 'theme' and value not in ('dark', 'light', 'system')) or (key != 'theme' and type(value) is not bool):
                        raise AppError('请选择有效的界面偏好。')
                current.update(payload)
                with self.state.db:
                    self.state.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('ui_preferences', json.dumps(current)))
            return {'preferences': current}

    def view(self):
        return self.state.view(self.patterns())

    def recommendation_token(self):
        return (self.state.revision, self.settings_revision, tuple(self.patterns()))

    def recommendations(self, *, use_model=False):
        with self.state.lock:
            token = self.recommendation_token()
            if not use_model and self.recommendation_cache and self.recommendation_cache[0] == token:
                return self.recommendation_cache[1]
            if use_model:
                self.recommendation_generation += 1
            generation = self.recommendation_generation
            items = self.view()['items']
        result = build_recommendations(items, self.assistant, use_model=use_model)
        with self.state.lock:
            if self.recommendation_token() != token or self.recommendation_generation != generation:
                raise AppError('检查结果或设置已更新，请重新查看建议。', 'stale_recommendations', 409)
            previous = self.recommendation_cache[1] if self.recommendation_cache and self.recommendation_cache[0] == token else None
            if not use_model and previous is not None:
                return previous  # a completed analysis supersedes an in-flight local GET
            if (use_model and result.get('summary', {}).get('model_mode') != 'llm' and previous is not None
                    and any(item.get('advice', {}).get('origin') == 'model' for item in previous.get('items', []))):
                retained = copy.deepcopy(previous)
                retained['analysis_notice'] = dict(status='retained_previous',
                    message='本次未取得新建议，保留上次建议。', new_advice_accepted=False)
                return retained
            self.recommendation_cache = (token, result)
        return result

    def start_job(self, kind, payload):
        with self.job_lock:
            if sum(x['status'] == 'running' for x in self.jobs.values()) >= 2:
                raise AppError('已有两个检查或整理任务，请等待或停止其中一个。', 'job_limit', 409)
            finished = [key for key, job in self.jobs.items() if job['status'] != 'running']
            for old in finished[:-18]:
                self.jobs.pop(old, None)
            key = secrets.token_hex(8)
            if kind == 'agent':
                task_id = payload.get('task_id')
                with self.state.lock:
                    if payload.get('execute'):
                        self.harness.validate_approval(task_id, payload.get('version'), payload.get('ids'))
                    elif payload.get('feedback'):
                        self.harness.feedback(task_id, payload.get('message', ''), payload.get('keep_ids'))
                    elif payload.get('answer') is not None:
                        self.harness.answer(task_id, payload['answer'])
                    self.harness.claim(task_id, execute=bool(payload.get('execute')))
                    self.harness.attach_job(task_id, key)
            cancel = threading.Event()
            job = dict(id=key, status='running', progress={'message': '准备只读采集'}, result=None, error=None)
            self.jobs[key] = dict(job, _cancel=cancel)
        def progress(value):
            with self.job_lock:
                self.jobs[key]['progress'] = value
        def run():
            try:
                if kind == 'agent':
                    task = self.harness.investigate(payload['task_id'], cancel=cancel, progress=progress,
                                                    execute=bool(payload.get('execute')))
                    with self.job_lock:
                        self.jobs[key].update(status='cancelled' if cancel.is_set() else 'completed',
                                              result={'task': task}, progress={'message': task['summary']})
                    return
                pending = []
                if kind == 'scan':
                    path = payload.get('path')
                    if not isinstance(path, str) or not path.strip():
                        raise AppError('请输入要扫描的绝对目录。')
                    path = os.path.expanduser(path.strip())
                    if not os.path.isabs(path):
                        raise AppError('请使用绝对目录，不能使用相对路径。')
                    entries, depth = payload.get('max_entries', 25000), payload.get('max_depth', 12)
                    if type(entries) is not int or type(depth) is not int:
                        raise AppError('扫描条目和深度必须为整数。')
                    result = scan_directory(path, max_entries=min(max(entries, 1), 100000),
                                            max_depth=min(max(depth, 1), 32), cancel=cancel, progress=progress)
                    if result.get('summary', {}).get('status') == 'error':
                        raise AppError('该目录无法扫描，旧清单已保留；请确认路径、权限及链接状态。', 'scan_failed')
                    group = 'filesystem:' + os.path.normcase(os.path.abspath(path))
                    pending.append((group, result.get('items', [])))
                elif kind == 'check':
                    reports, warnings = [], []
                    paths = common_cache_paths()
                    for index, path in enumerate(paths):
                        if cancel.is_set():
                            break
                        progress({'message': f'正在检查常见缓存（{index + 1}/{len(paths)}）'})
                        report = scan_directory(path, max_entries=5000, max_depth=12, cancel=cancel)
                        reports.append({'scope': 'cache', 'path': path, 'summary': report.get('summary', {})})
                        if report.get('summary', {}).get('status') == 'error':
                            warnings.append('有一个缓存目录暂时无法读取，已保留其原清单。')
                            continue
                        pending.append(('filesystem:' + os.path.normcase(os.path.abspath(path)), report.get('items', [])))
                    for section, title in (('background', '后台项目'), ('software', '已安装软件')):
                        if cancel.is_set():
                            break
                        progress({'message': f'正在了解{title}，不会修改系统设置'})
                        report = inventory(section)
                        pending.append((section, report.get('items', [])))
                        reports.append({'scope': section, 'summary': report.get('summary', {})})
                    result = {'scopes': reports, 'warnings': warnings,
                              'summary': {'status': 'cancelled' if cancel.is_set() else 'completed',
                                          'scope_description': '常见缓存、后台项目与已安装软件；未扫描个人文件内容或整个磁盘。',
                                          'cache_locations_checked': len(paths)}}
                else:
                    result = inventory(payload.get('section', 'background'))
                    group = payload.get('section', 'background')
                    pending.append((group, result.get('items', [])))
                # Cancellation keeps previous catalog; partial result remains available in task history.
                if not cancel.is_set():
                    with self.state.lock:
                        for group, items in pending:
                            self.state.publish(items, group)
                        result['catalog'] = self.state.overview(self.patterns()) if payload.get('compact') else self.view()
                        result['catalog']['catalog_revision'] = self.catalog.token()
                with self.job_lock:
                    self.jobs[key].update(status='cancelled' if cancel.is_set() else 'completed', result=result,
                                          progress={'message': '已取消' if cancel.is_set() else '只读采集完成'})
            except Exception as exc:
                message = str(exc) if isinstance(exc, AppError) else '采集失败：' + type(exc).__name__
                with self.job_lock:
                    self.jobs[key].update(status='failed', error=message)
        self.pool.submit(run)
        return {'job_id': key}

    def job(self, key):
        with self.job_lock:
            value = self.jobs.get(key)
            if not value:
                raise AppError('任务不存在。', 'unknown_job', 404)
            return {k: v for k, v in value.items() if not k.startswith('_')}

    def icon_item(self, key):
        """Resolve only an existing catalog ID; icon paths stay local and private."""
        if not isinstance(key, str) or not 0 < len(key) <= 128:
            return None
        with self.state.lock:
            row = self.state.db.execute('SELECT payload FROM catalog WHERE id=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def icon(self, query):
        if set(query) - {'id', 'v'} or len(query.get('id', [])) != 1:
            raise AppError('请选择清单中的内容。', 'invalid_icon', 400)
        key = query['id'][0]
        if not isinstance(key, str) or not 0 < len(key) <= 128:
            raise AppError('图标对应的内容不存在。', 'not_found', 404)
        try:
            return self.icons.get(key)
        except IconNotFound:
            raise AppError('图标对应的内容不存在。', 'not_found', 404) from None

    def get(self, path, query):
        if path == '/api/about':
            return about()
        if path.startswith('/api/tasks/') and path.endswith('/stream'):
            parts = path.split('/')
            if len(parts) != 5:
                raise AppError('任务不存在。', 'not_found', 404)
            self.harness.public(parts[3])
            return {'stream': self.live_streams.snapshot(parts[3]), 'volatile': True}
        if path == '/api/ui-preferences':
            return self.ui_preferences()
        if path == '/api/skills' or path.startswith('/api/skills/'):
            try:
                if path == '/api/skills':
                    return self.skills.discover(query=query.get('query', [''])[0], cursor=query.get('cursor', [None])[0],
                        limit=int(query.get('limit', ['5'])[0]), token_budget=3000)
                if len(path.split('/')) != 4:
                    raise ValueError('技能接口不存在')
                return self.skills.load(path.split('/')[3], cursor=query.get('cursor', [None])[0], token_budget=4000)
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 'invalid_skill') from None
        if path == '/api/providers':
            return self.assistant.provider_presets()
        if path == '/api/developer':
            try:
                offset, limit = int(query.get('offset', ['0'])[0]), int(query.get('limit', ['10'])[0])
            except (ValueError, TypeError):
                raise AppError('请选择有效的开发记录页码。') from None
            return self.trace.page(offset, limit)
        if path == '/api/bootstrap':
            view = self.state.overview(self.patterns()) if query.get('compact') == ['1'] else self.view()
            return dict(view, catalog_revision=self.catalog.token(), csrf=self.csrf, version=__version__, settings=self.assistant.public_settings(), disks=disks(), default_scan_path=str(Path.home() / 'Downloads'), latest_task=self.harness.latest())
        if path == '/api/files':
            try:
                offset = int(query.get('offset', ['0'])[0])
                limit = int(query.get('limit', ['50'])[0])
            except (ValueError, TypeError):
                raise AppError('请使用有效的页码。') from None
            return self.catalog.page(offset=offset, limit=limit, query=query.get('query', [''])[0],
                scope=query.get('scope', [''])[0], risk=query.get('risk', ['all'])[0], expected=query.get('revision', [''])[0])
        if path == '/api/applications':
            section = query.get('section', ['background'])[0]
            kinds = {'background': ('startup', 'service', 'process'), 'software': ('software',)}.get(section)
            if not kinds:
                raise AppError('请选择后台或软件清单。')
            with self.state.lock:
                items = [i for i in self.state.snapshot(self.patterns())['items'] if i['kind'] in kinds]
                return {'items': items}
        if path == '/api/health':
            return {'status': 'ok', 'mode': 'simulation', 'version': __version__, 'live_executor_available': False}
        if path == '/api/history':
            return {'history': self.state.history()}
        if path == '/api/recommendations':
            return self.recommendations()
        if path == '/api/tasks':
            return {'tasks': [self.harness.public(t['id']) for t in self.harness._all()[:15]]}
        if path.startswith('/api/tasks/') and path.endswith('/advice'):
            parts = path.split('/')
            if len(parts) != 5:
                raise AppError('建议接口不存在。', 'not_found', 404)
            try:
                offset, limit = int(query.get('offset', ['0'])[0]), int(query.get('limit', ['20'])[0])
            except (ValueError, TypeError):
                raise AppError('请选择有效的建议页码。') from None
            ids = query.get('ids', [''])[0].split(',') if query.get('ids') else None
            return self.harness.advice_page(parts[3], offset, limit, ids)
        if path.startswith('/api/tasks/') and path.endswith('/facts'):
            parts = path.split('/')
            if len(parts) != 5:
                raise AppError('事实接口不存在。', 'not_found', 404)
            try:
                offset, limit = int(query.get('offset', ['0'])[0]), int(query.get('limit', ['20'])[0])
            except (ValueError, TypeError):
                raise AppError('请选择有效的事实页码。') from None
            ids = query.get('ids', [''])[0].split(',') if query.get('ids') else None
            return self.harness.facts_page(parts[3], offset, limit, ids)
        if path.startswith('/api/tasks/'):
            return {'task': self.harness.public(path.rsplit('/', 1)[-1])}
        if path.startswith('/api/jobs/'):
            return self.job(path.rsplit('/', 1)[-1])
        if path == '/api/item':
            key = query.get('id', [''])[0]
            item = next((x for x in self.state.snapshot(self.patterns())['items'] if x['id'] == key), None)
            if not item:
                raise AppError('项目不存在。', 'unknown_item', 404)
            return {'item': item}
        raise AppError('接口不存在。', 'not_found', 404)

    def post(self, path, payload):
        if path == '/api/updates/check':
            if payload:
                raise AppError('检查更新不接受网址、凭据或其他参数。', 'invalid_update_request')
            return self.updates.check()
        if path == '/api/preferences':
            if set(payload) != {'custom_instructions'} or not isinstance(payload['custom_instructions'], str):
                raise AppError('请填写整理偏好。', 'invalid_preferences')
            try:
                settings = self.assistant.save_settings({'custom_instructions': payload['custom_instructions']})
            except ValueError as exc:
                raise AppError(str(exc), 'invalid_preferences') from None
            self.settings_revision += 1
            self.recommendation_cache = None
            return {'preferences': {'custom_instructions': settings['custom_instructions']}, 'settings': settings}
        if path == '/api/ui-preferences':
            return self.ui_preferences(payload)
        if path == '/api/developer':
            if set(payload) - {'enabled', 'clear'}:
                raise AppError('开发模式设置格式无效。')
            return self.trace.update(enabled=payload.get('enabled'), clear=payload.get('clear', False))
        if path == '/api/settings/test':
            try:
                return self.assistant.test_connection()
            except ValueError as exc:
                raise AppError(str(exc), 'connection_test_failed') from None
        if path == '/api/tasks/analyze':
            task = self.harness.create_analysis(payload)
            job = self.start_job('agent', {'task_id': task['id']})
            return dict(job, task=self.harness.public(task['id']))
        if path == '/api/tasks':
            task = self.harness.create(payload.get('goal'), payload.get('path'), preset=payload.get('preset'))
            job = self.start_job('agent', {'task_id': task['id']})
            return dict(job, task=self.harness.public(task['id']))
        if path.startswith('/api/tasks/'):
            parts = path.split('/')
            if len(parts) == 5 and parts[4] == 'direct-plan':
                return self.harness.direct_plan(parts[3], payload)
            if len(parts) == 5 and parts[4] in ('continue', 'approve', 'feedback', 'answer'):
                data = dict(task_id=parts[3], execute=parts[4] == 'approve', feedback=parts[4] == 'feedback',
                            message=payload.get('message', ''), keep_ids=payload.get('keep_ids'),
                            version=payload.get('version'), ids=payload.get('ids'))
                if parts[4] == 'answer':
                    data['answer'] = payload
                job = self.start_job('agent', data)
                return dict(job, task=self.harness.public(parts[3]))
            raise AppError('任务操作不存在。', 'not_found', 404)
        if path == '/api/demo' and self.developer_fixtures:
            self.state.replace_demo()
            return self.view()
        if path == '/api/scan':
            return self.start_job('scan', payload)
        if path == '/api/check':
            return self.start_job('check', payload)
        if path == '/api/recommendations/analyze':
            return self.recommendations(use_model=True)
        if path == '/api/inventory':
            if payload.get('section') not in ('background', 'software'):
                raise AppError('请选择后台或软件清单。')
            return self.start_job('inventory', payload)
        if path.startswith('/api/jobs/') and path.endswith('/cancel'):
            key = path.split('/')[-2]
            with self.job_lock:
                if key not in self.jobs:
                    raise AppError('任务不存在。', 'unknown_job', 404)
                self.jobs[key]['_cancel'].set()
            return {'job': self.job(key)}
        if path == '/api/plan':
            with self.state.lock:
                snapshots = payload.get('snapshots')
                if snapshots is not None:
                    ids = payload.get('ids')
                    if not isinstance(snapshots, dict) or not isinstance(ids, list) or len(ids) > 500:
                        raise AppError('选择信息无效，请重新选择。')
                    current = {i['id']: i for i in self.state.snapshot(self.patterns())['items']}
                    if any(not isinstance(key, str) or key not in current or key not in snapshots or
                           current[key].get('signature') != snapshots[key] for key in ids):
                        raise AppError('所选内容已更新，请重新查看后选择。', 'selection_changed', 409)
                mode = payload.get('selection_mode', 'manual')
                if mode not in ('manual', 'recommended'):
                    raise AppError('请选择处理推荐项或手动选择项目。')
                if mode == 'recommended':
                    ids = payload.get('ids')
                    advice = self.recommendations()
                    allowed = {x['id'] for x in advice['items'] if x.get('advice', {}).get('auto_select') is True
                               and x['advice'].get('group') == 'recommended' and not x['advice'].get('needs_user_choice')}
                    if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or x not in allowed for x in ids):
                        raise AppError('个人文件和需要确认的项目不能加入一键推荐，请到“我的文件”自行选择。', 'user_choice_required')
                return self.state.prepare(payload.get('ids'), self.patterns(), payload.get('action'))
        if path == '/api/execute':
            return self.state.execute(payload.get('plan_id'), self.patterns(), validate_system_items)
        if path == '/api/undo':
            return self.state.undo(payload.get('batch_id'), self.patterns())
        if path == '/api/reset':
            return self.state.reset(self.patterns())
        if path == '/api/settings':
            try:
                settings = self.assistant.save_settings(payload)
            except ValueError as exc:
                raise AppError(str(exc), 'invalid_settings') from None
            self.settings_revision += 1
            self.recommendation_cache = None
            return {'settings': settings}
        if path in ('/api/duplicates', '/api/assistant'):
            items = self.view()['items']
            ids = payload.get('ids')
            if ids is not None and (not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(x, str) for x in ids)):
                raise AppError('项目选择格式无效。')
            if path == '/api/duplicates':
                if not ids:
                    raise AppError('请明确选择要校验的文件。')
                selected = [x for x in items if x['id'] in ids]
                return verify_duplicates(selected)
            question = payload.get('question', '')
            if not isinstance(question, str) or not question.strip() or len(question) > 4000:
                raise AppError('请输入不超过 4000 字的分析要求。')
            return self.assistant.analyze(question, items, ids)
        raise AppError('接口不存在。', 'not_found', 404)

    def close(self):
        self.live_streams.close()
        for job in self.jobs.values():
            job['_cancel'].set()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.icons.close()
        self.scan_index.close()
        self.trace.close()
        self.state.close()
        self.lease.close()


def create_server(data_dir, port=0, *, developer_fixtures=False):
    app = Application(data_dir, developer_fixtures=developer_fixtures)
    class Handler(BaseHTTPRequestHandler):
        server_version = 'PanXu/0.3'
        def log_message(self, *_):
            pass

        def valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def send(self, status, body, mime='application/json; charset=utf-8', headers=None):
            encoded = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {'error': '不接受该 Host。', 'code': 'bad_host'})
            parsed = urlsplit(self.path)
            try:
                if parsed.path.startswith('/api/tasks/') and parsed.path.endswith('/stream/events'):
                    parts = parsed.path.split('/')
                    if len(parts) != 6:
                        raise AppError('任务不存在。', 'not_found', 404)
                    app.harness.public(parts[3])
                    if not app.stream_connections.acquire(blocking=False):
                        return self.send(429, {'error': '实时连接已达上限。', 'code': 'stream_limit'})
                    try:
                        self.connection.settimeout(5)
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.wfile.write(b'retry: 700\n\n')
                        self.wfile.flush()
                        deadline, sequence, initial = time.monotonic() + 25, -1, True
                        while not app.live_streams.closed and time.monotonic() < deadline:
                            value = app.live_streams.snapshot(parts[3]) if initial else app.live_streams.wait(parts[3], sequence, timeout=1)
                            next_sequence = value['sequence'] if value else -1
                            if initial or next_sequence != sequence:
                                body = json.dumps({'stream': value}, ensure_ascii=False).encode('utf-8')
                                self.wfile.write(b'data: ' + body + b'\n\n')
                                sequence = next_sequence
                            else:
                                self.wfile.write(b': keep-alive\n\n')
                            self.wfile.flush()
                            initial = False
                    except (OSError, TimeoutError):
                        pass  # window closed or a slow reader; no model request is replayed
                    finally:
                        self.close_connection = True
                        app.stream_connections.release()
                    return
                if parsed.path == '/api/icon':
                    icon = app.icon(parse_qs(parsed.query))
                    extra = {'X-Panxu-Icon-Status': icon['status'], 'ETag': icon['etag']}
                    if icon.get('retry_after'):
                        extra['Retry-After'] = str(icon['retry_after'])
                    return self.send(200, icon['body'], icon['mime'], extra)
                if parsed.path.startswith('/api/'):
                    return self.send(200, app.get(parsed.path, parse_qs(parsed.query)))
                names = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'),
                         '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/tasks.js': ('tasks.js', 'text/javascript; charset=utf-8'), '/files.js': ('files.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'),
                         '/stream-view.js': ('stream-view.js', 'text/javascript; charset=utf-8'), '/stream-channel.js': ('stream-channel.js', 'text/javascript; charset=utf-8'), '/icons.js': ('icons.js', 'text/javascript; charset=utf-8'), '/icons.css': ('icons.css', 'text/css; charset=utf-8'), '/hierarchy.css': ('hierarchy.css', 'text/css; charset=utf-8')}
                if parsed.path not in names:
                    raise AppError('页面不存在。', 'not_found', 404)
                name, mime = names[parsed.path]
                body = (app.web_root / name).read_bytes()
                if name == 'index.html':
                    theme = app.ui_preferences()['preferences']['theme'].encode('ascii')
                    body = body.replace(b'data-theme-preference="system"', b'data-theme-preference="' + theme + b'"', 1)
                return self.send(200, body, mime)
            except AppError as exc:
                return self.send(exc.status, {'error': str(exc), 'code': exc.code})
            except Exception:
                return self.send(500, {'error': '无法加载本地页面或数据。', 'code': 'internal_error'})

        def do_POST(self):
            origin = self.headers.get('Origin')
            allowed = (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
            if not self.valid_host() or (origin is not None and origin not in allowed) or not secrets.compare_digest(self.headers.get('X-CSRF-Token', '').encode('utf-8'), app.csrf.encode('utf-8')):
                return self.send(403, {'error': '请求来源或会话校验失败，请刷新页面。', 'code': 'csrf_failed'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1_000_000 or not self.headers.get('Content-Type', '').startswith('application/json'):
                    raise AppError('请求必须为不超过 1 MB 的 JSON。')
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise AppError('请求必须为 JSON 对象。')
                return self.send(200, app.post(urlsplit(self.path).path, payload))
            except AppError as exc:
                return self.send(exc.status, {'error': str(exc), 'code': exc.code})
            except (ValueError, TypeError, KeyError) as exc:
                return self.send(400, {'error': '参数或模型配置无效：' + type(exc).__name__, 'code': 'invalid_request'})
            except Exception:
                return self.send(500, {'error': '本地请求未完成；系统状态没有被操作。', 'code': 'internal_error'})
    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except Exception:
        app.close()
        raise
    server.daemon_threads = True
    server.app = app
    return server
