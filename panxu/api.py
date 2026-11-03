"""The single bridge between the web UI and the backend.

pywebview exposes exactly one method, ``Bridge.call(method, params)``. It
dispatches to ``Backend.m_<method>`` and always returns
``{'ok': True, 'data': ...}`` or ``{'ok': False, 'error': '...'}``, so the
UI never sees a raw Python exception.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time

from . import APP_NAME, __version__, ai_client, fsops, paths, winapi
from .analyst import Analyst, VerdictCache
from .duplicates import find_duplicates, verify_unchanged
from .jobs import JobManager, UserError
from .junk import GROUPS, JunkEngine
from .knowledge import Knowledge
from .oplog import OperationLog
from .scanner import FLAG_CLOUD, FLAG_DENIED, FLAG_LINKS, SpaceScan
from .services import START_TYPES, ServiceManager
from .settings import Settings
from .startup import StartupManager

log = logging.getLogger(__name__)

SETTINGS_URIS = {
    'storage': 'ms-settings:storagesense',
    'apps': 'ms-settings:appsfeatures',
    'startup': 'ms-settings:startupapps',
    'recovery': 'ms-settings:recovery',
    'recyclebin': 'shell:RecycleBinFolder',
}


class Backend:
    def __init__(self, data_dir: str, window_provider=None, relaunch=None):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.env = paths.environment()
        self.admin = winapi.is_admin()
        self.settings = Settings(data_dir)
        self.oplog = OperationLog(os.path.join(data_dir, 'history.jsonl'))
        self.jobs = JobManager()
        self.knowledge = Knowledge(self.env)
        self.junk = JunkEngine(self.env, self.admin, oplog=self.oplog)
        self.startup = StartupManager(env=self.env, admin=self.admin, oplog=self.oplog,
                                      hkcu_ok=not paths.is_foreign_user())
        self.services = ServiceManager(env=self.env, admin=self.admin, oplog=self.oplog)
        self.cache = VerdictCache(os.path.join(data_dir, 'ai_cache.json'))
        self.analyst = Analyst(self.cache, self.env, self.knowledge)
        self.scan: SpaceScan | None = None
        self.ai_results: dict[int, dict] = {}
        self.dups: dict | None = None
        self._window = window_provider
        self._relaunch = relaunch
        self.app_dirs = [os.path.dirname(os.path.abspath(__file__)), data_dir]
        if getattr(sys, 'frozen', False):
            self.app_dirs.append(os.path.dirname(sys.executable))

    # ------------------------------------------------------------------ helpers
    def policy(self) -> paths.ProtectionPolicy:
        excluded = self.settings.get().get('excluded_paths', [])
        return paths.ProtectionPolicy(self.env, excluded, self.app_dirs)

    def _client(self):
        s = self.settings.get()
        key = self.settings.get_secret('ai_key:' + s['ai']['preset'])
        try:
            return ai_client.client_from_settings(s['ai'], key)
        except ai_client.AIError as exc:
            raise UserError(str(exc)) from None

    def _require_scan(self) -> SpaceScan:
        if self.scan is None or not self.scan.complete:
            raise UserError('请先完成一次空间扫描')
        return self.scan

    # ------------------------------------------------------------------ general
    def m_init(self):
        s = self.settings.get()
        return {
            'version': __version__, 'admin': self.admin, 'windows': winapi.IS_WINDOWS,
            'settings': s, 'has_key': self.settings.has_secret('ai_key:' + s['ai']['preset']),
            'presets': ai_client.PRESETS, 'drives': self.m_drives(), 'rules': self.junk.catalogue(),
            'groups': [{'id': g, 'name': n} for g, n in GROUPS], 'start_types': START_TYPES,
            'home': self.env.get('USERPROFILE', ''), 'system_drive': self.env.get('SystemDrive', 'C:') + '\\',
            'data_dir': self.data_dir,
        }

    def m_drives(self):
        rows = []
        try:
            import psutil
            parts = psutil.disk_partitions(all=False)
        except Exception:  # noqa: BLE001
            parts = []
        seen = set()
        for p in parts:
            root = p.mountpoint
            if root in seen or 'cdrom' in (p.opts or '') or not p.fstype:
                continue
            seen.add(root)
            try:
                usage = shutil.disk_usage(root)
            except OSError:
                continue
            rows.append({'root': root, 'label': winapi.volume_label(root), 'fstype': p.fstype,
                         'total': usage.total, 'used': usage.used, 'free': usage.free,
                         'system': paths.key(root) == paths.key(self.env.get('SystemDrive', 'C:') + '\\'),
                         'removable': winapi.drive_type(root) == winapi.DRIVE_REMOVABLE})
        rows.sort(key=lambda r: (not r['system'], r['root']))
        return rows

    def m_job(self, job_id: str):
        return self.jobs.status(job_id)

    def m_cancel_job(self, job_id: str):
        return self.jobs.cancel(job_id)

    def m_restart_as_admin(self):
        if self.admin:
            return True
        if any(j['kind'] in ('junk_clean', 'delete', 'dup_delete') for j in self.jobs.running()):
            raise UserError('清理正在进行，完成后再重启')
        if self._relaunch is None or not self._relaunch():
            raise UserError('没有获得管理员权限（可能在系统提示中选择了「否」）')
        return True

    def m_reveal(self, path: str):
        if not isinstance(path, str) or not os.path.isabs(path) or not os.path.exists(path):
            raise UserError('路径不存在')
        winapi.reveal_in_explorer(path)
        return True

    def m_open_system_page(self, page: str):
        uri = SETTINGS_URIS.get(page)
        if not uri:
            raise UserError('未知页面')
        winapi.open_uri(uri)
        return True

    def m_open_key_page(self, preset: str):
        url = ai_client.PRESET_BY_ID.get(preset, {}).get('key_url')
        if not url:
            raise UserError('这个服务没有提供网页地址')
        winapi.open_uri(url)
        return True

    def m_pick_folder(self):
        window = self._window() if self._window else None
        if window is None:
            raise UserError('当前环境不支持选择文件夹')
        import webview
        kind = getattr(getattr(webview, 'FileDialog', None), 'FOLDER', None) or getattr(webview, 'FOLDER_DIALOG')
        picked = window.create_file_dialog(kind)
        if not picked:
            return None
        return picked[0] if isinstance(picked, (list, tuple)) else picked

    # ------------------------------------------------------------------ junk
    def m_junk_scan(self):
        def run(job):
            def progress(i, total, name):
                job.update(index=i, total=total, current=name)
            return self.junk.scan(cancel=job.cancel, progress=progress)
        return self.jobs.start('junk_scan', run)

    def m_junk_clean(self, rule_ids: list):
        if not isinstance(rule_ids, list) or not rule_ids:
            raise UserError('请选择要清理的项目')

        def run(job):
            def progress(i, total, name, freed):
                job.update(index=i, total=total, current=name, freed=freed)
            return self.junk.clean([str(r) for r in rule_ids], cancel=job.cancel, progress=progress)
        return self.jobs.start('junk_clean', run, group='modify')

    # ------------------------------------------------------------------ space
    def m_space_scan(self, root: str):
        if not isinstance(root, str) or not os.path.isdir(root):
            raise UserError('请选择一个存在的磁盘或文件夹')
        if winapi.IS_WINDOWS:
            # canonical spelling (no 8.3 names, junctions or \\?\ prefixes) so every
            # path derived from the scan matches the protection rules
            root = os.path.realpath(root)

        def run(job):
            scan = SpaceScan(root)
            scan.run(cancel=job.cancel, progress=lambda p: job.update(**p))
            self.scan = scan
            self.ai_results = {}
            return {'root': self._node_row(scan, scan.root), 'stats': scan.stats}
        return self.jobs.start('space_scan', run, group='space')

    def _node_row(self, scan: SpaceScan, node) -> dict:
        path = scan.path_of(node)
        row = {'id': node.id, 'name': node.name if node.parent else path, 'path': path, 'type': 'dir',
               'size': node.size, 'files': node.files, 'dirs': node.dirs, 'mtime': node.mtime,
               'has_children': bool(node.children), 'denied': bool(node.flags & FLAG_DENIED),
               'links': bool(node.flags & FLAG_LINKS), 'cloud': bool(node.flags & FLAG_CLOUD),
               'note': self.knowledge.annotate(path)}
        if node.id in self.ai_results:
            row['ai'] = self.ai_results[node.id]
        return row

    def m_space_node(self, node_id: int, file_limit: int = 200):
        scan = self._require_scan()
        try:
            node = scan.node(int(node_id))
        except (KeyError, ValueError, TypeError):
            raise UserError('目录已失效，请重新扫描') from None
        policy = self.policy()
        children = [self._node_row(scan, c) for c in node.children or ()]
        files, total_files, rest = scan.own_files(node, limit=int(file_limit))
        for f in files:
            f['type'] = 'file'
            f['category'] = paths.category_of(f['name'])
            f['blocked'] = policy.reason(f['path'])
        for c in children:
            c['blocked'] = policy.reason(c['path'])
        return {
            'node': self._node_row(scan, node),
            'crumbs': [{'id': n.id, 'name': n.name if n.parent else scan.path_of(n)} for n in scan.ancestors(node)],
            'children': children, 'files': files, 'more_files': max(0, total_files - len(files)), 'more_bytes': rest,
        }

    def m_space_treemap(self, node_id: int, depth: int = 2, max_items: int = 160):
        scan = self._require_scan()
        node = scan.node(int(node_id))
        budget = [int(max_items)]

        def kind_of(n):
            ai = self.ai_results.get(n.id)
            if ai and ai['verdict'] == 'clean':
                return 'cache'
            note = self.knowledge.annotate(scan.path_of(n))
            return note['kind'] if note else 'unknown'

        def build(n, level):
            item = {'id': n.id, 'name': n.name if n.parent else scan.path_of(n), 'size': n.size, 'kind': kind_of(n),
                    'has_children': bool(n.children)}
            if level < depth and n.children and budget[0] > 0:
                kids = []
                shown = 0
                for c in n.children:
                    if budget[0] <= 0 or c.size < n.size * 0.004:
                        break
                    budget[0] -= 1
                    kids.append(build(c, level + 1))
                    shown += c.size
                if kids:
                    if n.size - shown > 0:
                        kids.append({'id': None, 'name': '其他', 'size': n.size - shown, 'other': True})
                    item['children'] = kids
            return item
        return build(node, 0)

    def m_space_find(self, path: str):
        scan = self._require_scan()
        node = scan.find(path)
        return node.id if node is not None else None

    def m_space_types(self):
        return self._require_scan().ext_summary()

    def m_large_files(self, min_mb: float = 100, category: str = '', older_days: int = 0, limit: int = 500):
        scan = self._require_scan()
        policy = self.policy()
        cutoff = time.time() - older_days * 86400 if older_days else None
        rows = []
        for f in scan.top_files:
            if f['size'] < min_mb * 1024 * 1024:
                break
            cat = paths.category_of(f['path'])
            if category and cat != category:
                continue
            if cutoff and f['mtime'] > cutoff:
                continue
            rows.append({**f, 'name': os.path.basename(f['path']), 'category': cat,
                         'category_label': paths.category_label(cat), 'blocked': policy.reason(f['path']),
                         'note': self.knowledge.annotate(f['path'])})
            if len(rows) >= limit:
                break
        return {'files': rows, 'scanned_root': scan.root_path, 'scanned_at': scan.finished_at}

    # ------------------------------------------------------------------ deletion of selected paths
    def m_check_paths(self, items: list):
        """Pre-flight for the confirmation dialog: sizes, blocks and warnings."""
        policy = self.policy()
        out = []
        for path in items or []:
            if not isinstance(path, str):
                continue
            node = self.scan.find(path) if self.scan and self.scan.complete else None
            if node is not None and node is not self.scan.root:
                size, count = node.size, node.files
            elif os.path.isfile(path):
                size, count = os.path.getsize(path), 1
            else:
                size, count = None, None
            out.append({'path': path, 'exists': os.path.lexists(path), 'size': size, 'files': count,
                        'blocked': policy.reason(path), 'caution': policy.caution(path),
                        'note': self.knowledge.annotate(path)})
        return out

    def m_delete_paths(self, items: list, mode: str = 'recycle', source: str = 'space'):
        return self._delete(items, mode, 'space' if source not in ('space', 'large_files', 'ai') else source)

    def _delete(self, items: list, mode: str, source: str, after=None):
        if mode not in ('recycle', 'permanent'):
            raise UserError('未知的删除方式')
        items = [p for p in items or [] if isinstance(p, str)]
        if not items:
            raise UserError('没有选择任何项目')
        policy = self.policy()
        blocked = [(p, policy.reason(p)) for p in items if policy.reason(p)]
        if blocked:
            raise UserError(f'「{blocked[0][0]}」不能删除：{blocked[0][1]}')
        # drop paths nested inside another selected path
        keys = sorted(set(items), key=len)
        selected = []
        for p in keys:
            if not any(paths.is_within(p, s) for s in selected):
                selected.append(p)

        def run(job):
            total = len(selected)
            stats = fsops.DeleteStats()
            done = []
            try:
                if mode == 'recycle':
                    part, recycled = fsops.recycle(selected, cancel=job.cancel, hwnd=winapi.find_window(APP_NAME),
                                                   progress=lambda s: job.update(total=total, freed=s.bytes, failed=s.failed))
                    done.extend(recycled)
                    stats.merge(part)
                else:
                    for i, p in enumerate(selected):
                        fsops.check_cancel(job.cancel)
                        job.update(done=i, total=total, current=p, freed=stats.bytes)
                        try:
                            fsops.delete_path(p, cancel=job.cancel, stats=stats)
                        finally:
                            if not os.path.lexists(p):
                                done.append(p)
            finally:
                # always record what was actually removed, even when stopped midway
                cancelled = job.cancel.is_set()
                if done or stats.failed:
                    title = ('移到回收站' if mode == 'recycle' else '永久删除') + f' {len(done)} 项' + ('（已停止）' if cancelled else '')
                    self.oplog.add('recycle' if mode == 'recycle' else 'delete', title, freed=stats.bytes, items=done,
                                   failures=stats.failures, details={'source': source, 'mode': mode, 'cancelled': cancelled})
                if self.scan is not None and self.scan.complete:
                    for p in done:
                        try:
                            self.scan.remove_path(p, 0, 0)
                        except (ValueError, KeyError):
                            pass
                if after is not None:
                    after(done)
            return {'done': done, 'freed': stats.bytes, 'failed': stats.failed, 'failures': stats.failures,
                    'mode': mode}
        return self.jobs.start('delete', run, group='modify')

    # ------------------------------------------------------------------ AI on space
    def m_ai_preview(self):
        return self.analyst.preview(self._require_scan(), self.settings.get())

    def m_ai_analyze(self, use_cache: bool = True):
        scan = self._require_scan()
        settings = self.settings.get()
        # check cheaply that there is something to do and a configured client
        client = self._client() if self.analyst.preview(scan, settings)['fresh'] or not use_cache else None

        def run(job):
            nonlocal client
            if client is None:
                client = _NoCallClient()
            result = self.analyst.analyze_space(scan, client, settings, cancel=job.cancel,
                                                progress=lambda p: job.update(**p), use_cache=use_cache)
            if result['requests']:
                self.oplog.add('ai', f'AI 分析 {len(result["suggestions"])} 个目录', details={
                    'usage': result['usage'], 'model': settings['ai']['model'], 'errors': result['errors'][:5]})
            self.ai_results = {s['node_id']: s for s in result['suggestions']}
            policy = self.policy()
            for s in result['suggestions']:
                s['blocked'] = policy.reason(s['path'])
                s['caution'] = policy.caution(s['path'])
            return result
        return self.jobs.start('ai_analyze', run, group='ai')

    def m_ai_results(self):
        return sorted(self.ai_results.values(), key=lambda r: r['size'], reverse=True)

    # ------------------------------------------------------------------ duplicates
    def m_dup_default_roots(self):
        out = []
        for name in ('desktop', 'documents', 'downloads', 'pictures', 'videos', 'music'):
            try:
                p = winapi.known_folder(name)
            except OSError:
                continue
            if p and os.path.isdir(p) and p not in out:
                out.append(p)
        return out

    def m_dup_scan(self, roots: list, min_size_mb: float = 1):
        roots = [r for r in roots or [] if isinstance(r, str) and os.path.isdir(r)]
        if not roots:
            raise UserError('请至少选择一个存在的文件夹')
        policy = self.policy()
        skip_bases = [self.env.get('SystemRoot'), self.env.get('ProgramFiles'), self.env.get('ProgramFiles(x86)'),
                      self.env.get('ProgramData'), self.env.get('LOCALAPPDATA'), self.env.get('APPDATA')]
        skip_bases = [b for b in skip_bases if b]

        def skip(path):
            return any(paths.is_within(path, b) for b in skip_bases) or policy.reason(path) == '位于你设置的排除目录中'

        def run(job):
            result = find_duplicates(roots, min_size=int(float(min_size_mb) * 1024 * 1024), skip=skip,
                                     cancel=job.cancel, progress=lambda p: job.update(**p))
            for g in result['groups']:
                for f in g['files']:
                    f['blocked'] = policy.reason(f['path'])
            self.dups = result
            return result
        return self.jobs.start('dup_scan', run, group='dups')

    def m_dup_delete(self, paths_to_delete: list):
        if not self.dups:
            raise UserError('请先查找重复文件')
        chosen = {paths.key(p) for p in paths_to_delete or [] if isinstance(p, str)}
        targets = []
        for g in self.dups['groups']:
            members = [f for f in g['files'] if paths.key(f['path']) in chosen]
            if not members:
                continue
            if len(members) >= len(g['files']):
                raise UserError('每组重复文件至少要保留一份')
            for f in members:
                if not verify_unchanged(f['path'], g['size'], f['mtime']):
                    raise UserError(f'「{f["path"]}」在查找后被修改过，请重新查找')
                targets.append(f['path'])
        if not targets:
            raise UserError('没有选择任何文件')
        def forget(done):
            gone = {paths.key(p) for p in done}
            groups = []
            for g in self.dups['groups']:
                g['files'] = [f for f in g['files'] if paths.key(f['path']) not in gone]
                if len(g['files']) > 1:
                    g['wasted'] = g['size'] * (len(g['files']) - 1)
                    groups.append(g)
            self.dups['groups'] = groups
        return self._delete(targets, 'recycle', 'duplicates', after=forget)

    # ------------------------------------------------------------------ startup & services
    def m_startup_list(self):
        return self.startup.list()

    def m_startup_set(self, item_id: str, enabled: bool):
        try:
            return self.startup.set_enabled(str(item_id), bool(enabled))
        except (ValueError, PermissionError) as exc:
            raise UserError(str(exc)) from None

    def m_services_list(self):
        return self.services.list()

    def m_service_set(self, name: str, start_type: str, stop_now: bool = False):
        try:
            return self.services.set_start_type(str(name), str(start_type), bool(stop_now))
        except (ValueError, PermissionError, RuntimeError) as exc:
            raise UserError(str(exc)) from None

    def m_ai_explain(self, kind: str, use_cache: bool = True):
        if kind == 'startup':
            items = [{'key': 'startup:' + i['name'] + '|' + i['command'], 'id': i['id'], 'kind': 'startup',
                      'name': i['display_name'], 'command': i['command'], 'publisher': i['publisher'],
                      'description': ''} for i in self.startup.list()]
        elif kind == 'services':
            items = [{'key': 'service:' + s['name'] + '|' + s['binpath'], 'id': s['name'], 'kind': 'service',
                      'name': s['display_name'], 'command': s['binpath'], 'publisher': s['publisher'],
                      'description': s['description']} for s in self.services.list() if s['kind'] == 'third_party']
        else:
            raise UserError('未知类型')
        s = self.settings.get()
        needs_model = any(self.cache.get('item:' + i['key']) is None for i in items) or not use_cache
        client = self._client() if needs_model else _NoCallClient()

        def run(job):
            job.update(count=len(items))
            result = self.analyst.explain_items(items, client, s['ai'].get('anonymize_paths', True), use_cache)
            by_id = {i['id']: result['items'].get(i['key']) for i in items}
            return {'items': {k: v for k, v in by_id.items() if v}, 'usage': result['usage'],
                    'errors': result['errors']}
        return self.jobs.start('ai_explain', run, group='ai')

    # ------------------------------------------------------------------ history
    def m_history(self, limit: int = 200):
        return self.oplog.entries(int(limit))

    def m_revert(self, entry_id: str):
        entry = self.oplog.get(str(entry_id))
        if not entry or 'revert' not in entry:
            raise UserError('这条记录不能撤销')
        if entry.get('followup', {}).get('reverted'):
            raise UserError('这条记录已经撤销过了')
        revert = entry['revert']
        try:
            if revert['action'] == 'startup':
                self.startup.set_enabled(revert['id'], revert['enabled'], log=False)
            elif revert['action'] == 'service':
                self.services.set_start_type(revert['name'], revert['start_type'], log=False)
            else:
                raise UserError('这条记录不能撤销')
        except (ValueError, PermissionError, RuntimeError) as exc:
            raise UserError(str(exc)) from None
        self.oplog.mark(entry['id'], reverted=True, reverted_at=time.time())
        return True

    # ------------------------------------------------------------------ settings
    def m_settings_save(self, changes: dict):
        if not isinstance(changes, dict):
            raise UserError('设置格式不正确')
        s = self.settings.update(changes)
        return {'settings': s, 'has_key': self.settings.has_secret('ai_key:' + s['ai']['preset'])}

    def m_set_api_key(self, preset: str, key: str):
        if preset not in ai_client.PRESET_BY_ID:
            raise UserError('未知的模型服务')
        self.settings.set_secret('ai_key:' + preset, (key or '').strip())
        return self.settings.has_secret('ai_key:' + preset)

    def m_has_api_key(self, preset: str):
        return self.settings.has_secret('ai_key:' + str(preset))

    def m_ai_models(self):
        try:
            return self._client().list_models()
        except ai_client.AIError as exc:
            raise UserError(str(exc)) from None

    def m_ai_test(self):
        client = self._client()
        try:
            reply = client.complete_json('你是连接测试助手。只输出 JSON。', '请回复 {"ok": true}')
        except (ai_client.AIError, ValueError) as exc:
            raise UserError(f'测试失败：{exc}') from None
        return {'reply': reply, 'usage': client.usage.as_dict(), 'model': client.model}

    def m_ai_clear_cache(self):
        self.cache.clear()
        return True


class _NoCallClient:
    """Stands in when every answer comes from the cache."""
    model = 'cache'

    class usage:  # noqa: N801 - mimic Client.usage
        @staticmethod
        def as_dict():
            return {'requests': 0, 'input_tokens': 0, 'output_tokens': 0}

    def complete_json(self, *_):
        raise ai_client.AIError('请先在设置中配置模型')


class Bridge:
    """The only object handed to pywebview (``js_api``)."""

    def __init__(self, backend: Backend):
        self._backend = backend

    def call(self, method, params=None):
        handler = getattr(self._backend, 'm_' + str(method), None) if str(method).isidentifier() else None
        if handler is None:
            return {'ok': False, 'error': f'未知操作：{method}'}
        try:
            data = handler(**(params or {})) if isinstance(params, dict) else handler()
            return {'ok': True, 'data': data}
        except UserError as exc:
            return {'ok': False, 'error': str(exc)}
        except TypeError as exc:
            log.exception('bad call %s', method)
            return {'ok': False, 'error': f'参数错误：{exc}'}
        except Exception as exc:  # noqa: BLE001
            log.exception('call %s failed', method)
            return {'ok': False, 'error': f'内部错误：{exc}'}
