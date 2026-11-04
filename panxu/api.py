"""The single bridge between the web UI and the backend.

pywebview exposes exactly one method, ``Bridge.call(method, params)``. It
dispatches to ``Backend.m_<method>`` and always returns
``{'ok': True, 'data': ...}`` or ``{'ok': False, 'error': '...'}``, so the
UI never sees a raw Python exception.
"""
from __future__ import annotations

import logging
import ntpath
import os
import shutil
import subprocess
import sys
import time

from . import APP_NAME, __version__, ai_client, checkup, fsops, insights, itemfacts, paths, winapi
from .analyst import Analyst, VerdictCache
from .analyst import _fmt_date as fmt_date
from .analyst import _fmt_size as fmt_size
from .apps import KIND_LABELS as APP_KINDS
from .apps import AppManager
from .duplicates import find_duplicates, verify_unchanged
from .jobs import JobManager, UserError
from .junk import GROUPS, RULES_BY_ID, JunkEngine
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
# classic dialogs that have no ms-settings: page (System32 programs)
SETTINGS_PROGRAMS = {
    'virtual_memory': 'SystemPropertiesPerformance.exe',   # 性能选项 > 高级 > 虚拟内存
    'system_protection': 'SystemPropertiesProtection.exe',  # 系统保护（还原点）
}
# opening these runs something (programs, installers, scripts, shortcuts to them): 「打开」 refuses them
# (the same list is in web/ui.js, PX.canOpen)
RUNS_CODE = {'.exe', '.com', '.scr', '.pif', '.cpl', '.msi', '.msix', '.msixbundle', '.msp', '.appx', '.appxbundle',
             '.application', '.appref-ms', '.bat', '.cmd', '.ps1', '.psm1', '.vbs', '.vbe', '.js', '.jse', '.wsf', '.wsh',
             '.hta', '.jar', '.lnk', '.url', '.reg', '.inf', '.msc', '.sys', '.dll', '.ocx'}


class Backend:
    def __init__(self, data_dir: str, window_provider=None, relaunch=None, sandbox=None, env: dict | None = None,
                 legacy_key_paths: list[str] | None = None):
        """``sandbox``: a sandbox.Sandbox — every system access then goes to the simulation (演练环境).
        ``env`` overrides the base folders (tests). ``legacy_key_paths``: where PanXu 0.x may have
        saved an API key (imported once when no key is configured)."""
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.sandbox = sandbox
        if sandbox is not None and winapi.simulation() is not sandbox:
            from . import sandbox as sandbox_mod
            sandbox_mod.activate(sandbox)
        self.env = env or paths.environment()
        self.admin = winapi.is_admin()
        self.settings = Settings(data_dir)
        if legacy_key_paths:
            try:
                self.settings.import_legacy_key(legacy_key_paths)
            except Exception:  # noqa: BLE001 - a broken old file must not stop the program
                log.exception('importing the 0.x API key failed')
        self.oplog = OperationLog(os.path.join(data_dir, 'history.jsonl'))
        self.jobs = JobManager()
        self.knowledge = Knowledge(self.env)
        self.junk = JunkEngine(self.env, self.admin, oplog=self.oplog)
        registry = sandbox.registry if sandbox is not None else None
        self.startup = StartupManager(registry=registry, env=self.env, admin=self.admin, oplog=self.oplog,
                                      hkcu_ok=sandbox is not None or not paths.is_foreign_user())
        self.services = ServiceManager(env=self.env, admin=self.admin, oplog=self.oplog,
                                       source=sandbox.services if sandbox is not None else None,
                                       runner=sandbox.sc if sandbox is not None else None)
        self.apps = AppManager(registry=registry, env=self.env, admin=self.admin, oplog=self.oplog)
        self.cache = VerdictCache(os.path.join(data_dir, 'ai_cache.json'))
        self.analyst = Analyst(self.cache, self.env, self.knowledge, app_matcher=self._match_app)
        # shared state: whatever one page (or the check-up) finds, the others see
        self.scan: SpaceScan | None = None
        self.ai_results: dict[int, dict] = {}
        self.dups: dict | None = None
        self.junk_rows: list[dict] | None = None
        self.insights: dict | None = None
        self.file_ai: dict[str, dict] = {}
        self.dup_ai: dict[str, dict] = {}   # duplicate copy -> the model's advice (Analyst.review_duplicates)
        self.plan: dict | None = None
        self._app_index: tuple[float, list] | None = None
        self._icons: dict[tuple, str | None] = {}
        self._type_icons: dict[str, str | None] = {}
        self._window = window_provider
        self._relaunch = relaunch
        self.app_dirs = [os.path.dirname(os.path.abspath(__file__)), data_dir]
        if getattr(sys, 'frozen', False):
            self.app_dirs.append(os.path.dirname(sys.executable))

    # ------------------------------------------------------------------ helpers
    def policy(self) -> paths.ProtectionPolicy:
        excluded = self.settings.get().get('excluded_paths', [])
        if self.sandbox is not None:
            return paths.ProtectionPolicy(self.env, excluded, self.app_dirs, allowed_roots=[self.sandbox.root],
                                          drive_roots=self.sandbox.drive_roots)
        return paths.ProtectionPolicy(self.env, excluded, self.app_dirs)

    def _client(self, purpose: str = 'classify'):
        s = self.settings.get()
        key = self.settings.get_secret('ai_key:' + s['ai']['preset'])
        try:
            return ai_client.client_from_settings(s['ai'], key, purpose=purpose)
        except ai_client.AIError as exc:
            raise UserError(str(exc)) from None

    def client_or_none(self, purpose: str = 'classify'):
        try:
            return self._client(purpose)
        except UserError:
            return None

    def _match_app(self, folder_name: str) -> str | None:
        if self._app_index is None or time.time() - self._app_index[0] > 60:
            try:
                self._app_index = (time.time(), self.apps.match_index())
            except OSError:
                self._app_index = (time.time(), [])
        app = self.apps.match_folder(folder_name, self._app_index[1])
        return app['name'] if app else None

    def forget_paths(self, removed: list[str]):
        """Keep the shared results in step after files were deleted or recycled."""
        if not removed:
            return
        if self.scan is not None and self.scan.complete:
            for p in removed:
                try:
                    self.scan.remove_path(p, 0, 0)
                except (ValueError, KeyError):
                    pass
        gone = {paths.key(p) for p in removed}

        def alive(path):
            return not paths.within_any(path, gone)
        # the plan on 首页 shows what was done on the tool pages too
        for item in (self.plan or {}).get('items', []):
            if item['status'] == 'pending' and item.get('paths') and not any(alive(p) for p in item['paths']):
                item['status'], item['result'] = 'done', '已处理'
        if self.insights:
            kept = []
            for f in self.insights['findings']:
                f['files'] = [x for x in f['files'] if alive(x['path'])]
                if f['files']:
                    f['count'] = len(f['files'])
                    f['size'] = sum(x['size'] for x in f['files'])
                    f['removable_count'] = sum(1 for x in f['files'] if x.get('remove'))
                    f['removable_size'] = sum(x['size'] for x in f['files'] if x.get('remove'))
                    kept.append(f)
            self.insights['findings'] = kept
        if self.dups:
            groups = []
            for g in self.dups['groups']:
                g['files'] = [f for f in g['files'] if alive(f['path'])]
                if len(g['files']) > 1:
                    g['wasted'] = g['size'] * (len(g['files']) - 1)
                    groups.append(g)
            self.dups['groups'] = groups

    def files_for_ai(self, finding_ids: list, include_content: bool) -> list[dict]:
        """「我的文件」 findings flattened into what analyst.review_files sends."""
        if not self.insights:
            raise UserError('请先整理一次「我的文件」')
        wanted = {str(i) for i in finding_ids or []}
        out = []
        for f in self.insights['findings']:
            if f['id'] not in wanted or f['kind'] == 'empty_folders':
                continue
            for x in f['files']:
                row = {'path': x['path'], 'name': x['name'], 'size': x['size'], 'mtime': x['mtime'],
                       'category': x.get('category', 'other'), 'group': f['kind_label'], 'note': x.get('note', ''),
                       'group_id': f['id'] if f['kind'] == 'name_versions' else ''}
                if include_content:
                    excerpt = insights.content_excerpt(x['path'], 800)
                    if excerpt:
                        row['excerpt'] = excerpt
                out.append(row)
        return out

    def startup_items_for_ai(self, items: list[dict]) -> list[dict]:
        running = itemfacts.running_programs()
        return [{'key': 'startup:' + i['name'] + '|' + i['command'], 'id': i['id'], 'kind': 'startup',
                 'name': i['display_name'], 'command': i['command'], 'publisher': i['publisher'],
                 'description': '', 'facts': itemfacts.startup_facts(i, running)} for i in items]

    def services_for_ai(self, services: list[dict]) -> list[dict]:
        """Third-party services with what 盘序 checked about them (running, the app they belong to, is it open)."""
        running = itemfacts.running_programs()
        out = []
        for s in services:
            if s['kind'] != 'third_party':
                continue
            facts, publisher = itemfacts.service_facts(s, running)
            out.append({'key': 'service:' + s['name'] + '|' + s['binpath'], 'id': s['name'], 'kind': 'service',
                        'name': s['display_name'], 'command': s['binpath'], 'publisher': publisher,
                        'description': s['description'], 'facts': facts})
        return out

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
            'home': self.env.get('USERPROFILE', ''), 'system_drive': self._system_drive(),
            'data_dir': self.data_dir, 'sandbox': self._sandbox_info(),
            'plan_groups': [{'id': g, 'name': n} for g, n in checkup.GROUPS.items()],
            'checkup_stages': [{'id': s, 'label': lbl} for s, lbl in checkup.STAGES],
        }

    def _system_drive(self) -> str:
        drive = self.env.get('SystemDrive', 'C:')
        return paths.native(drive if drive.endswith(('\\', '/')) else drive + '\\')

    def _sandbox_info(self):
        if self.sandbox is None:
            return None
        size, count = self.sandbox.recycle_bin_info()
        return {'root': self.sandbox.root, 'recycle_count': count, 'recycle_size': size}

    def m_drives(self):
        if self.sandbox is not None:
            return self.sandbox.drives()
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
        """Show a path in Explorer (a file selected in its folder, a folder opened). A path that is gone --
        cleaned, moved to the Recycle Bin -- opens the nearest folder that is still there."""
        if not isinstance(path, str) or not os.path.isabs(path):
            raise UserError('路径不存在')
        target = path
        for _ in range(8):
            if os.path.exists(target):
                break
            parent = os.path.dirname(target.rstrip('\\/'))
            if not parent or parent == target:
                raise UserError('路径不存在')
            target = parent
        else:
            raise UserError('路径不存在')
        winapi.reveal_in_explorer(target)
        return {'shown': target, 'missing': target != path}

    def changes_running(self) -> list[dict]:
        """Jobs that delete or move files right now (closing PanXu would cut them off)."""
        return self.jobs.running('modify')

    def stop_changes(self, timeout: float = 8.0) -> bool:
        """Stop them cleanly: each finishes the file it is on and writes its 操作记录 entry."""
        return self.jobs.stop('modify', timeout)

    def m_flash_window(self):
        """Flash PanXu's taskbar button (a slow job finished while another window was in front)."""
        return winapi.flash_window((APP_NAME, APP_NAME + '（演练环境）'))

    def m_open_path(self, path: str):
        """Open a file with its default program, or a folder, like a double-click in Explorer. Programs,
        scripts and shortcuts are refused: for them "open" means run, and an old installer or an unknown
        script lying in Downloads must never start by a click in a disk cleaner."""
        if not isinstance(path, str) or not os.path.isabs(path) or not os.path.exists(path):
            raise UserError('文件不存在：可能已经被删除或移走')
        if os.path.isdir(path):
            winapi.reveal_in_explorer(path)
            return {'opened': path}
        if os.path.splitext(path)[1].lower() in RUNS_CODE:
            raise UserError('这是程序或脚本，打开就会运行它。盘序不直接运行程序；可以在资源管理器中查看后自己决定。')
        try:
            winapi.open_file(path)
        except OSError as exc:
            raise UserError('没有能打开它的程序' if getattr(exc, 'winerror', None) == 1155 else f'打不开：{exc}') from None
        return {'opened': path}

    def m_open_system_page(self, page: str):
        uri = SETTINGS_URIS.get(page)
        program = SETTINGS_PROGRAMS.get(page)
        if not uri and not program:
            raise UserError('未知页面')
        if self.sandbox is not None:
            if page != 'recyclebin':
                raise UserError('演练环境不会打开真实的系统设置：在那里做的修改会影响你的电脑')
            folder = self.sandbox.recycle_dir()
            os.makedirs(folder, exist_ok=True)
            winapi.reveal_in_explorer(folder)  # the simulated Recycle Bin, not the real one
            return True
        if program:
            exe = os.path.join(self.env.get('SystemRoot', 'C:\\Windows'), 'System32', program)
            if not winapi.shell_execute(exe):
                raise UserError('没能打开这个设置')
            return True
        winapi.open_uri(uri)
        return True

    # ------------------------------------------------------------------ hibernation (仅供了解 > 休眠文件)
    def _hiberfil(self) -> str:
        drive = self.env.get('SystemDrive', 'C:')
        return paths.native(os.path.join(drive if drive.endswith(('\\', '/')) else drive + '\\', 'hiberfil.sys'))

    def _set_hibernate(self, enabled: bool):
        sim = winapi.simulation()
        if sim is not None:
            sim.set_hibernate(enabled)
            return
        if not self.admin:
            raise PermissionError('开关休眠需要管理员权限')
        exe = os.path.join(self.env.get('SystemRoot', 'C:\\Windows'), 'System32', 'powercfg.exe')
        proc = winapi.run_hidden([exe, '/hibernate', 'on' if enabled else 'off'], timeout=60)
        if proc.returncode != 0:
            text = winapi.decode_console(proc.stdout + proc.stderr).strip()
            raise RuntimeError(text.splitlines()[-1] if text else f'powercfg 返回 {proc.returncode}')

    def m_hibernate_set(self, enabled: bool):
        """Switch hibernation (and with it hiberfil.sys and Fast Startup) off or on; logged with an undo."""
        path = self._hiberfil()
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        try:
            self._set_hibernate(bool(enabled))
        except (PermissionError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            raise UserError(str(exc)) from None
        title = '开启休眠' if enabled else '关闭休眠（删除 hiberfil.sys，快速启动也会关闭）'
        self.oplog.add('system', title, freed=0 if enabled else size,
                       items=[{'name': 'hiberfil.sys', 'path': path, 'size': size}],
                       revert={'action': 'hibernate', 'enabled': not enabled})
        for item in (self.plan or {}).get('items', []):
            if item.get('location') and paths.key(item['location']) == paths.key(path):
                item['status'], item['result'] = ('pending', '') if enabled else ('done', '已关闭休眠')
        return {'enabled': bool(enabled), 'freed': 0 if enabled else size}

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

    def m_running_check(self):
        """Which cleanup rules wait for a program right now (no rescan); updates the plan too."""
        running = self.junk.running_now()
        for row in self.junk_rows or []:
            if row['id'] in running:
                row['running'] = running[row['id']]
        changed = checkup.refresh_running(self.plan, running) if self.plan else []
        return {'running': running, 'plan_changed': changed}

    def m_junk_clean(self, rule_ids: list):
        if not isinstance(rule_ids, list) or not rule_ids:
            raise UserError('请选择要清理的项目')

        def run(job):
            def progress(i, total, name, freed):
                job.update(index=i, total=total, current=name, freed=freed)
            result = self.junk.clean([str(r) for r in rule_ids], cancel=job.cancel, progress=progress)
            # the same rules in the plan on 首页 are done now
            cleaned = {r['id'] for r in result['rules'] if not r['skipped']}
            for item in (self.plan or {}).get('items', []):
                if item.get('kind') == 'rule' and item['rule'] in cleaned and item['status'] == 'pending':
                    item['status'], item['result'] = 'done', '已在「垃圾清理」清理'
            return result
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
        return self._delete(items, mode, 'space' if source not in ('space', 'large_files', 'ai', 'plan', 'leftovers') else source)

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
        # drop duplicates and paths nested inside another selected path
        chosen = {paths.key(p) for p in items}
        selected, seen = [], set()
        for p in sorted(items, key=len):
            k = paths.key(p)
            parent = paths.key(ntpath.dirname(k))
            if k in seen or (parent != k and paths.within_any(parent, chosen)):
                continue
            seen.add(k)
            selected.append(p)

        def run(job):
            total = len(selected)
            stats = fsops.DeleteStats()
            done = []
            kind, verb = ('recycle', '移到回收站') if mode == 'recycle' else ('delete', '永久删除')
            # on record before the first file goes: if PanXu is killed half way, 操作记录 still says so
            pending = self.oplog.begin(kind, f'{verb} {total} 项', items=selected)
            try:
                if mode == 'recycle':
                    def on_progress(s):
                        job.update(total=s.total_items or total, done=s.done_items, current=s.current, freed=s.bytes,
                                   failed=s.failed)
                        self.oplog.progress(pending, freed=s.bytes, done=s.files)
                    part, recycled = fsops.recycle(selected, cancel=job.cancel, hwnd=winapi.find_window(APP_NAME),
                                                   progress=on_progress)
                    done.extend(recycled)
                    stats.merge(part)
                else:
                    for i, p in enumerate(selected):
                        fsops.check_cancel(job.cancel)
                        job.update(done=i, total=total, current=p, freed=stats.bytes)
                        self.oplog.progress(pending, freed=stats.bytes, done=len(done))
                        try:
                            fsops.delete_path(p, cancel=job.cancel, stats=stats)
                        finally:
                            if not os.path.lexists(p):
                                done.append(p)
            finally:
                # always record what was actually removed, even when stopped midway
                cancelled = job.cancel.is_set()
                if done or stats.failed:
                    title = f'{verb} {len(done)} 项' + ('（已停止）' if cancelled else '')
                    if stats.permanent:  # too large for the bin: Windows deleted them, on the user's yes
                        title = (f'移到回收站 {len(done) - len(stats.permanent)} 项，{len(stats.permanent)} 项回收站放不下、已直接删除'
                                 + ('（已停止）' if cancelled else ''))
                    self.oplog.add(kind, title, freed=stats.bytes, items=done, failures=stats.failures,
                                   details={'source': source, 'mode': mode, 'cancelled': cancelled,
                                            'permanent': stats.permanent[:200], 'permanent_bytes': stats.permanent_bytes},
                                   replaces=pending)
                else:
                    self.oplog.drop(pending)
                self.forget_paths(done)
                if after is not None:
                    after(done)
            return {'done': done, 'freed': stats.bytes, 'failed': stats.failed, 'failures': stats.failures,
                    'mode': mode, 'permanent': stats.permanent, 'permanent_bytes': stats.permanent_bytes}
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
            # folders PanXu recognises itself (a drive's Temp folder): listed too, without asking the model
            # (and not kept as AI verdicts for the check-up)
            known = [k for k in self.analyst.known_cleanable(scan)
                     if not any(paths.is_within(k['path'], s['path']) for s in result['suggestions'])]
            for s in known:
                s['blocked'] = policy.reason(s['path'])
                s['caution'] = policy.caution(s['path'])
            result['suggestions'] += known
            # the same score, reasons and auto-tick as the check-up
            checkup.assess_ai_dirs(result['suggestions'], policy, self.env, self.analyst, scan,
                                   settings.get('checkup', {}).get('auto_threshold', checkup.DEFAULT_THRESHOLD))
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
        skip = checkup.system_skip(self.env, policy, roots)

        def run(job):
            result = find_duplicates(roots, min_size=int(float(min_size_mb) * 1024 * 1024), skip=skip,
                                     cancel=job.cancel, progress=lambda p: job.update(**p))
            for g in result['groups']:
                for f in g['files']:
                    f['blocked'] = policy.reason(f['path'])
                    f['ai'] = self.dup_ai.get(f['path'])
            self.dups = result
            return result
        return self.jobs.start('dup_scan', run, group='dups')

    def m_dup_ai_review(self, group_ids: list):
        """Ask the model which copy of these groups is safer to delete (paths and 盘序's findings only)."""
        if not self.dups:
            raise UserError('请先查找重复文件')
        wanted = {int(i) for i in group_ids or [] if isinstance(i, (int, str)) and str(i).isdigit()}
        groups = [g for g in self.dups['groups'] if g['id'] in wanted]
        if not groups:
            raise UserError('没有可以发送的重复文件')
        s = self.settings.get()['ai']
        client = self._client()

        def run(job):
            result = self.analyst.review_duplicates(groups, client, s.get('anonymize_paths', True),
                                                    max_requests=int(s.get('max_requests', 8)), cancel=job.cancel,
                                                    progress=lambda p: job.update(**p))
            self.dup_ai.update(result['copies'])
            for g in groups:
                for f in g['files']:
                    f['ai'] = self.dup_ai.get(f['path'])
            if result['requests']:
                self.oplog.add('ai', f'AI 查看 {len(groups)} 组重复文件', details={
                    'usage': result['usage'], 'model': client.model, 'errors': result['errors'][:5]})
            return {'copies': result['copies'], 'usage': result['usage'], 'errors': result['errors']}
        return self.jobs.start('dup_ai_review', run, group='ai')

    def m_dup_delete(self, paths_to_delete: list):
        if not self.dups:
            raise UserError('请先查找重复文件')
        chosen = {paths.key(p) for p in paths_to_delete or [] if isinstance(p, str)}
        targets = []
        for g in self.dups['groups']:
            members = [f for f in g['files'] if paths.key(f['path']) in chosen]
            if not members:
                continue
            # every copy of a group is the user's call too: the page warns, and all go to the Recycle Bin
            for f in members:
                if not verify_unchanged(f['path'], g['size'], f['mtime']):
                    raise UserError(f'「{f["path"]}」在查找后被修改过，请重新查找')
                targets.append(f['path'])
        if not targets:
            raise UserError('没有选择任何文件')
        return self._delete(targets, 'recycle', 'duplicates')  # forget_paths updates the groups

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
            items = self.startup_items_for_ai(self.startup.list())
        elif kind == 'services':
            items = self.services_for_ai(self.services.list())
        elif kind == 'apps':
            running = itemfacts.running_programs()
            items = [self._app_for_ai(a, running) for a in self.apps.list()]
        else:
            raise UserError('未知类型')
        s = self.settings.get()
        prefix = 'item:' if kind == 'apps' else 'item2:'  # as Analyst.explain_items keys them
        needs_model = any(self.cache.get(prefix + i['key']) is None for i in items) or not use_cache
        client = self._client() if needs_model else _NoCallClient()

        def run(job):
            job.update(count=len(items))
            result = self.analyst.explain_items(items, client, s['ai'].get('anonymize_paths', True), use_cache)
            by_id = {i['id']: result['items'].get(i['key']) for i in items}
            return {'items': {k: v for k, v in by_id.items() if v}, 'usage': result['usage'],
                    'errors': result['errors']}
        return self.jobs.start('ai_explain', run, group='ai')

    # ------------------------------------------------------------------ installed programs
    def m_apps_list(self, fresh: bool = False):
        apps = self.apps.list(fresh=bool(fresh))
        return {'apps': apps, 'admin': self.admin, 'usage_known': any(a['usage_known'] for a in apps)}

    def m_app_measure(self, app_id: str):
        try:
            return self.apps.measure(str(app_id))
        except ValueError as exc:
            raise UserError(str(exc)) from None

    def _icon(self, path: str, index: int = 0) -> str | None:
        from . import icons
        key = (paths.key(path), index)
        if key not in self._icons:
            try:
                png = icons.file_icon(path, index)
            except (OSError, ValueError):
                png = None
            self._icons[key] = icons.data_url(png) if png else None
        return self._icons[key]

    def m_app_icons(self, ids: list):
        """{app id: PNG data URL} for the given programs; programs without a usable icon are left out."""
        wanted = {str(i) for i in ids or []}
        out = {}
        for a in self.apps.list():
            if a['id'] in wanted and a.get('icon'):
                url = self._icon(a['icon'], a.get('icon_index', 0))
                if url:
                    out[a['id']] = url
        return out

    def m_file_icons(self, files: list):
        """{path: PNG data URL} for executables (startup items, services)."""
        out = {}
        for p in (files or [])[:300]:
            if isinstance(p, str) and p.lower().endswith(('.exe', '.dll', '.ico', '.lnk')):
                url = self._icon(p)
                if url:
                    out[p] = url
        return out

    def m_type_icons(self, kinds: list):
        """{'.pdf' | 'folder': PNG data URL}: the icon Explorer shows for a file type, for file lists."""
        from . import icons
        out = {}
        for k in (kinds or [])[:200]:
            k = str(k).lower()
            if k not in self._type_icons:
                try:
                    png = icons.type_icon(k)
                except (OSError, ValueError):
                    png = None
                self._type_icons[k] = icons.data_url(png) if png else None
            if self._type_icons[k]:
                out[k] = self._type_icons[k]
        return out

    def m_apps_measure_all(self):
        """Measure every program folder that has no size yet (background job)."""
        def run(job):
            todo = [a for a in self.apps.list() if a['location_exists'] and a['size_source'] != 'measured' and not a['size']]
            out = {}
            for i, a in enumerate(todo):
                fsops.check_cancel(job.cancel)
                job.update(done=i, total=len(todo), current=a['name'])
                try:
                    out[a['id']] = self.apps.measure(a['id'])['size']
                except (ValueError, OSError):
                    continue
            return out
        return self.jobs.start('apps_measure', run, group='apps')

    def m_app_uninstall(self, app_id: str):
        try:
            result = self.apps.uninstall(str(app_id))
        except (ValueError, FileNotFoundError, OSError) as exc:
            raise UserError(str(exc)) from None
        self._app_index = None
        return result

    def m_app_installed(self, app_ids: list):
        """{app id: still registered?} -- polled while an uninstaller runs."""
        return {str(i): self.apps.installed(str(i)) for i in (app_ids or [])[:20]}

    def m_app_leftovers(self, app_id: str):
        """Folders an uninstalled program left behind (a background job: sizes take a moment)."""
        def run(job):
            try:
                rows = self.apps.leftovers(str(app_id))
            except ValueError as exc:
                raise UserError(str(exc)) from None
            policy = self.policy()
            for r in rows:
                r['blocked'] = policy.reason(r['path'])
            return rows
        return self.jobs.start('app_leftovers', run, group='apps')

    def _app_for_ai(self, a: dict, running: set[str] | None = None) -> dict:
        extra = {'version': a['version'], 'category': APP_KINDS.get(a['kind'], '应用')}
        if a['size']:
            extra['size'] = fmt_size(a['size'])
        if a['installed']:
            extra['installed'] = fmt_date(a['installed'])
        if a['usage_known']:
            extra['last_run'] = fmt_date(a['last_used']) if a['last_used'] else '没有运行记录'
        # without administrator rights there is no run history: a program open right now is still plainly in use
        if running and a.get('location') and a.get('location_exists'):
            base = os.path.normcase(os.path.normpath(a['location'])) + os.sep
            if any(p.startswith(base) for p in running):
                extra['open_now'] = '现在正开着'
        return {'key': 'app:' + a['name'] + '|' + a['version'], 'id': a['id'], 'kind': 'app', 'name': a['name'],
                'publisher': a['publisher'], 'command': '', 'extra': extra}

    # ------------------------------------------------------------------ 我的文件
    def m_insights_roots(self):
        return checkup.personal_roots(self.env, self.settings.get())

    def m_insights_scan(self, roots: list | None = None):
        settings = self.settings.get()
        roots = [r for r in (roots or checkup.personal_roots(self.env, settings)) if isinstance(r, str) and os.path.isdir(r)]
        if not roots:
            raise UserError('请至少选择一个存在的文件夹')
        policy = self.policy()

        def run(job):
            ins = insights.Insights(self.env, settings, apps=self.apps).collect(
                roots, skip=checkup.system_skip(self.env, policy, roots), cancel=job.cancel,
                progress=lambda p: job.update(**p))
            self.insights = {'findings': ins.findings(), 'stats': ins.stats, 'roots': ins.roots, 'time': time.time()}
            return self.m_insights_result()
        return self.jobs.start('insights_scan', run, group='insights')

    def m_insights_result(self):
        if not self.insights:
            return None
        policy = self.policy()
        findings = []
        for f in self.insights['findings']:
            files = [{**x, 'ai': self.file_ai.get(x['path']), 'blocked': policy.reason(x['path']),
                      'score': insights.with_ai(x.get('score', 0), self.file_ai.get(x['path']),
                                                bool(insights.content_question(f['kind'], x)))} for x in f['files']]
            findings.append({**f, 'files': files})
        return {**self.insights, 'findings': findings}

    def m_insights_delete(self, finding_id: str, paths_to_delete: list):
        if not self.insights:
            raise UserError('请先整理一次「我的文件」')
        finding = next((f for f in self.insights['findings'] if f['id'] == finding_id), None)
        if finding is None:
            raise UserError('这组结果已失效，请重新整理')
        known = {paths.key(x['path']): x['path'] for x in finding['files']}
        chosen = [known[paths.key(p)] for p in paths_to_delete or [] if isinstance(p, str) and paths.key(p) in known]
        if not chosen:
            raise UserError('没有选择任何文件')
        # every version of a document is the user's call too (the page warns); personal files only ever go to
        # the Recycle Bin
        return self._delete(chosen, 'recycle', 'insights')

    def m_ai_review_preview(self, finding_ids: list):
        """What would be sent: the confirmation dialog lists it, content files separately."""
        files = self.files_for_ai(finding_ids, include_content=False)
        readable = [f for f in files if os.path.splitext(f['path'])[1].lower() in
                    insights.TEXT_EXTS | set(insights.OFFICE)]
        s = self.settings.get()['ai']
        return {'files': [{'path': f['path'], 'name': f['name'], 'size': f['size']} for f in files],
                'content_files': [{'path': f['path'], 'name': f['name']} for f in readable],
                'content_allowed': bool(s.get('send_file_content')), 'anonymize': bool(s.get('anonymize_paths', True))}

    def m_ai_review_files(self, finding_ids: list, include_content: bool = False):
        s = self.settings.get()
        if include_content and not s['ai'].get('send_file_content'):
            raise UserError('设置中没有允许读取文档内容')
        files = self.files_for_ai(finding_ids, include_content=bool(include_content))
        if not files:
            raise UserError('没有可以发送的文件')
        client = self._client()

        def run(job):
            result = self.analyst.review_files(files, client, s['ai'].get('anonymize_paths', True),
                                               max_requests=int(s['ai'].get('max_requests', 8)), cancel=job.cancel,
                                               progress=lambda p: job.update(**p))
            self.file_ai.update(result['files'])
            if result['requests']:
                sent = sum(1 for f in files if f.get('excerpt'))
                self.oplog.add('ai', f'AI 查看 {len(files)} 个文件' + (f'（含 {sent} 个文档的开头内容）' if sent else ''),
                               details={'usage': result['usage'], 'model': client.model, 'errors': result['errors'][:5],
                                        'content_files': [f['path'] for f in files if f.get('excerpt')]})
            return {'files': result['files'], 'usage': result['usage'], 'errors': result['errors']}
        return self.jobs.start('ai_review', run, group='ai')

    # ------------------------------------------------------------------ 一键体检 and the plan
    def m_checkup(self, options: dict | None = None):
        if any(j['kind'] in ('junk_clean', 'delete', 'plan_execute') for j in self.jobs.running()):
            raise UserError('清理正在进行，完成后再体检')

        def run(job):
            return checkup.run(self, job, options if isinstance(options, dict) else {})
        return self.jobs.start('checkup', run, group='checkup')

    def m_plan(self):
        if self.plan is None:
            return None
        self.plan['summary'] = checkup.summarize(self.plan)
        return self.plan

    def m_plan_dup_choose(self, item_id: str, remove: list):
        """The user picks which copies of a duplicates item go to the Recycle Bin (all of them too: the item then
        says so and scores low); the item is scored again by the same rules and returned."""
        plan = self._require_plan()
        item = next((i for i in plan['items'] if i['id'] == item_id and i.get('kind') == 'dup'), None)
        if item is None or item['status'] != 'pending':
            raise UserError('这一项已经不在清理方案里了，请重新体检')
        wanted = {paths.key(p) for p in remove or [] if isinstance(p, str)}
        copies = item['dup_files']
        keep = {f['path'] for f in copies if paths.key(f['path']) not in wanted}
        if len(keep) == len(copies):
            raise UserError('至少选一份要移到回收站的副本')
        item.update(checkup.dup_choice(item['dup_size'], copies, keep), chosen=True)
        threshold = plan.get('threshold', checkup.DEFAULT_THRESHOLD)
        item['basis'] = item['basis'] + checkup.decision_note(item, threshold)
        plan['summary'] = checkup.summarize(plan)
        return item

    def m_plan_preview(self, item_ids: list):
        """Totals for the confirmation dialog, split by what will happen."""
        plan = self._require_plan()
        wanted = {str(i) for i in item_ids or []}
        out = {'delete': {'count': 0, 'size': 0, 'titles': []}, 'recycle': {'count': 0, 'size': 0, 'titles': []},
               'disable': {'count': 0, 'titles': []}, 'skipped': [], 'all_copies': []}
        for item in plan['items']:
            if item['id'] not in wanted:
                continue
            if item['blocked'] or item['status'] != 'pending' or item['action'] == 'none':
                out['skipped'].append({'title': item['title'], 'reason': item['blocked'] or '不需要操作'})
                continue
            if item.get('kind') == 'dup' and not item.get('keep'):
                out['all_copies'].append(item['title'])  # every copy chosen: the dialog says so
            slot = out[item['action']]
            slot['count'] += 1
            slot['titles'].append(item['title'])
            if 'size' in slot:
                slot['size'] += item['size'] or 0
        return out

    def m_plan_execute(self, item_ids: list):
        plan = self._require_plan()
        if not isinstance(item_ids, list) or not item_ids:
            raise UserError('请选择要执行的项目')

        def run(job):
            try:
                return checkup.execute(self, plan, [str(i) for i in item_ids], job)
            except ValueError as exc:
                raise UserError(str(exc)) from None
        return self.jobs.start('plan_execute', run, group='modify')

    def _require_plan(self) -> dict:
        if self.plan is None:
            raise UserError('请先做一次体检')
        return self.plan

    # ------------------------------------------------------------------ 问 AI
    def m_ai_ask(self, messages: list, target: dict | None = None, page: str = '', include_content: bool = False):
        if not isinstance(messages, list) or not messages:
            raise UserError('请输入问题')
        context = self._ask_context(target if isinstance(target, dict) else {'type': 'global'}, str(page or ''),
                                    bool(include_content))
        client = self._client('advise')

        def run(job):
            try:
                return {**self.analyst.ask(client, context, messages), 'model': client.model}
            except (ai_client.AIError, ValueError) as exc:
                raise UserError(str(exc)) from None
        return self.jobs.start('ai_ask', run)

    def _anon(self, path: str) -> str:
        return paths.anonymize(path, self.env) if self.settings.get()['ai'].get('anonymize_paths', True) else path

    def _path_context(self, path: str) -> dict:
        policy = self.policy()
        ctx = {'path': self._anon(path)}
        node = self.scan.find(path) if self.scan is not None and self.scan.complete else None
        if node is not None:
            ctx.update(type='文件夹', size=fmt_size(node.size), files=node.files, modified=fmt_date(node.mtime),
                       largest_subfolders=[[c.name, fmt_size(c.size)] for c in (node.children or ())[:8]])
            hints = self.analyst.hints(node)
            if hints:
                ctx['hints'] = hints
            if node.id in self.ai_results:
                r = self.ai_results[node.id]
                ctx['earlier_ai_verdict'] = {'verdict': r['verdict_label'], 'reason': r['reason']}
        elif os.path.isfile(path):
            st = os.stat(path)
            ctx.update(type=paths.category_label(paths.category_of(path)), size=fmt_size(st.st_size),
                       modified=fmt_date(st.st_mtime))
        elif os.path.isdir(path):
            ctx['type'] = '文件夹'
        note = self.knowledge.annotate(path)
        if note:
            ctx['panxu_knows'] = {'label': note['label'], 'advice': note['advice']}
        reason, caution = policy.reason(path), policy.caution(path)
        if reason:
            ctx['protected'] = reason
        elif caution:
            ctx['caution'] = caution
        app = self.analyst.installed_app(path)
        if app is not None:
            ctx['installed_app'] = app or '未找到对应的已安装程序'
        return ctx

    def _ask_context(self, target: dict, page: str, include_content: bool) -> dict:
        kind = target.get('type')
        ctx: dict = {'current_page': page} if page else {}
        if kind == 'path' and isinstance(target.get('path'), str):
            ctx['about'] = self._path_context(target['path'])
        elif kind == 'rule' and target.get('id') in RULES_BY_ID:
            rule = RULES_BY_ID[target['id']]
            ctx['about'] = {'type': '垃圾清理规则', 'name': rule.name, 'description': rule.description,
                            'note': rule.note, 'needs_admin': rule.admin}
            row = next((r for r in self.junk_rows or [] if r['id'] == rule.id), None)
            if row and row.get('size') is not None:
                ctx['about']['found'] = fmt_size(row['size'])
        elif kind == 'startup':
            item = next((i for i in self.startup.list() if i['id'] == target.get('id')), None)
            if item is None:
                raise UserError('启动项已不存在，请刷新列表')
            ctx['about'] = {'type': '开机启动项', 'name': item['display_name'], 'command': self._anon_text(item['command']),
                            'publisher': item['publisher'], 'enabled': item['enabled']}
        elif kind == 'service':
            svc = next((s for s in self.services.list() if s['name'] == target.get('id')), None)
            if svc is None:
                raise UserError('服务不存在，请刷新列表')
            ctx['about'] = {'type': '系统服务', 'name': svc['display_name'], 'service_name': svc['name'],
                            'description': svc['description'][:300], 'start_type': svc['start_label'],
                            'status': svc['status_label'], 'publisher': svc['publisher'],
                            'program': self._anon_text(svc['binpath']), 'microsoft': svc['kind'] != 'third_party'}
        elif kind == 'app':
            app = self.apps.get(str(target.get('id')))
            if app is None:
                raise UserError('程序已不存在，请刷新列表')
            ctx['about'] = {'type': '已安装的程序', 'name': app['name'], 'publisher': app['publisher'],
                            **self._app_for_ai(app)['extra']}
        elif kind == 'file' and isinstance(target.get('path'), str):
            ctx['about'] = self._file_context(target['path'], include_content)
        elif kind == 'plan_item':
            item = next((i for i in (self.plan or {}).get('items', []) if i['id'] == target.get('id')), None)
            if item is None:
                raise UserError('清理方案已更新，请重新打开')
            ctx['about'] = {'type': '清理方案中的一项', 'title': item['title'], 'group': checkup.GROUPS[item['group']],
                            'action': checkup.ACTIONS[item['action']], 'source': checkup.SOURCES[item['source']],
                            'size': fmt_size(item['size'] or 0), 'what': item['what'], 'why': self._anon_text(item['why']),
                            'advice': item.get('advice', ''), 'effect': item['effect'],
                            'basis': [self._anon_text(b) for b in item.get('basis', [])[:8]],
                            'paths': [self._anon(p) for p in item['paths'][:5]]}
            if item.get('blocked'):
                ctx['about']['protected'] = item['blocked']
            if len(item['paths']) == 1:
                detail = (self._file_context(item['paths'][0], False) if item.get('personal')
                          else self._path_context(item['paths'][0]))
                ctx['about']['detail'] = detail
        else:
            ctx['about'] = {'type': '整台电脑'}
            ctx.update(self._global_context())
        return ctx

    def _anon_text(self, text: str) -> str:
        from .analyst import anonymize_text
        return anonymize_text(text, self.env) if self.settings.get()['ai'].get('anonymize_paths', True) else text

    def _file_context(self, path: str, include_content: bool) -> dict:
        ctx = {'path': self._anon(path), 'type': paths.category_label(paths.category_of(path))}
        try:
            st = os.stat(path)
            ctx.update(size=fmt_size(st.st_size), modified=fmt_date(st.st_mtime))
        except OSError:
            ctx['missing'] = True
        for f in (self.insights or {}).get('findings', []):
            hit = next((x for x in f['files'] if paths.key(x['path']) == paths.key(path)), None)
            if hit:
                ctx['found_as'] = {'group': f['kind_label'], 'reason': f['reason'], 'note': hit.get('note', '')}
                break
        if path in self.file_ai:
            r = self.file_ai[path]
            ctx['earlier_ai_verdict'] = {'verdict': r['verdict_label'], 'what': r['what'], 'reason': r['reason']}
        if include_content:
            if not self.settings.get()['ai'].get('send_file_content'):
                raise UserError('设置中没有允许读取文档内容')
            excerpt = insights.content_excerpt(path, 1200)
            if excerpt:
                ctx['excerpt'] = excerpt
        return ctx

    def _global_context(self) -> dict:
        out: dict = {'drives': [{'drive': d.get('letter') or d['root'], 'label': d['label'], 'total': fmt_size(d['total']),
                                 'free': fmt_size(d['free']), 'system': d['system']} for d in self.m_drives()]}
        if self.junk_rows:
            rows = sorted((r for r in self.junk_rows if r.get('size')), key=lambda r: -r['size'])[:10]
            out['junk_found'] = [[RULES_BY_ID[r['id']].name, fmt_size(r['size'])] for r in rows]
        if self.scan is not None and self.scan.complete:
            nodes = []
            for child in self.scan.root.children or ():
                nodes.append(child)
                nodes.extend((child.children or ())[:5])
            nodes.sort(key=lambda n: -n.size)
            out['largest_folders'] = [[self._anon(self.scan.path_of(n)), fmt_size(n.size)] for n in nodes[:10]]
        if self.plan:
            out['cleanup_plan'] = [[i['title'], checkup.GROUPS[i['group']], fmt_size(i['size'] or 0)]
                                   for i in self.plan['items'][:15]]
        return out

    # ------------------------------------------------------------------ 演练环境
    def _require_sandbox(self):
        if self.sandbox is None:
            raise UserError('现在不是演练环境')
        return self.sandbox

    def m_sandbox_recycle_list(self):
        sb = self._require_sandbox()
        return [{'id': r['id'], 'original': r['original'], 'name': os.path.basename(r['original']),
                 'size': r['size'], 'time': r['time']} for r in sb.recycle_items()]

    def m_sandbox_restore(self, item_id: str):
        sb = self._require_sandbox()
        try:
            return sb.restore(str(item_id))
        except (FileNotFoundError, FileExistsError, OSError) as exc:
            raise UserError(str(exc)) from None

    def m_sandbox_reset(self):
        sb = self._require_sandbox()
        if self.jobs.running():
            raise UserError('还有任务在进行，完成后再重建')
        try:
            sb.reset()
        except (RuntimeError, OSError) as exc:
            raise UserError(f'重建失败：{exc}') from None
        self.scan, self.ai_results, self.dups, self.junk_rows = None, {}, None, None
        self.insights, self.file_ai, self.plan, self._app_index = None, {}, None, None
        self.apps._cache = None
        self.junk.last_scan.clear()
        return self._sandbox_info()

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
            elif revert['action'] == 'hibernate':
                self._set_hibernate(bool(revert['enabled']))
            else:
                raise UserError('这条记录不能撤销')
        except (ValueError, PermissionError, RuntimeError, OSError) as exc:
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
