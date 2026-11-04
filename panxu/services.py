"""Windows services: list all, change the start type of third-party ones.

Windows and Microsoft services are shown read-only. Changes go through the
system ``sc.exe`` tool, require elevation, and are logged with the previous
start type so they can be undone from the history page.
"""
from __future__ import annotations

import ntpath
import os

from . import itemfacts, paths, startup, winapi

START_TYPES = {'auto': '自动', 'delayed': '自动（延迟启动）', 'manual': '手动', 'disabled': '禁用'}
_SC_START = {'auto': 'auto', 'delayed': 'delayed-auto', 'manual': 'demand', 'disabled': 'disabled'}
_PSUTIL_START = {'automatic': 'auto', 'manual': 'manual', 'disabled': 'disabled'}
STATUS_LABELS = {'running': '正在运行', 'stopped': '已停止', 'paused': '已暂停', 'start_pending': '正在启动',
                 'stop_pending': '正在停止', 'continue_pending': '正在继续', 'pause_pending': '正在暂停'}


def classify(binpath: str, env: dict) -> tuple[str, str, dict]:
    """('windows' | 'microsoft' | 'third_party', exe path, version info)."""
    exe, _ = startup.split_command(binpath or '', env)
    exe = startup.resolve_executable(exe, env)
    system_root = env.get('SystemRoot', 'C:\\Windows')
    info = winapi.file_version_info(exe) if exe and os.path.isfile(exe) else {}
    if exe and paths.is_within(exe, system_root):
        return 'windows', exe, info
    company = info.get('CompanyName', '')
    if 'microsoft' in company.lower():
        return 'microsoft', exe, info
    return 'third_party', exe, info


class ServiceManager:
    def __init__(self, env: dict | None = None, admin: bool | None = None, oplog=None, source=None, runner=None):
        self.env = env or paths.environment()
        self.admin = winapi.is_admin() if admin is None else admin
        self.oplog = oplog
        self._source = source    # injectable: callable returning list of dicts like psutil's as_dict()
        self._runner = runner    # injectable: callable(argv) -> (returncode, output)

    def _raw(self) -> list[dict]:
        if self._source is not None:
            return self._source()
        import psutil
        rows = []
        for svc in psutil.win_service_iter():
            try:
                rows.append(svc.as_dict())
            except Exception:  # noqa: BLE001 - access denied / vanished services are skipped
                continue
        return rows

    def _delayed(self, name: str) -> bool:
        if not winapi.IS_WINDOWS:
            return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf'SYSTEM\CurrentControlSet\Services\{name}') as key:
                return winreg.QueryValueEx(key, 'DelayedAutostart')[0] == 1
        except OSError:
            return False

    def list(self) -> list[dict]:
        out = []
        for row in self._raw():
            name = row.get('name') or ''
            start = _PSUTIL_START.get(row.get('start_type'), row.get('start_type') or 'manual')
            delayed = row.get('delayed')
            if start == 'auto' and (delayed if delayed is not None else self._delayed(name)):
                start = 'delayed'
            kind, exe, info = classify(row.get('binpath', ''), self.env)
            # a Store app's service often has no company in its version info: its package manifest names it
            publisher = info.get('CompanyName', '') or itemfacts.package_publisher(itemfacts.app_root(exe))
            status = row.get('status') or ''
            out.append({
                'name': name, 'display_name': row.get('display_name') or name,
                'description': (row.get('description') or '').strip(), 'status': status,
                'status_label': STATUS_LABELS.get(status, status), 'start_type': start,
                'start_label': START_TYPES.get(start, start), 'binpath': row.get('binpath', ''),
                'exe': exe, 'publisher': publisher, 'kind': kind,
                'editable': kind == 'third_party', 'needs_admin': not self.admin,
            })
        out.sort(key=lambda s: (s['kind'] != 'third_party', s['display_name'].lower()))
        return out

    def _run(self, argv: list[str]) -> tuple[int, str]:
        if self._runner is not None:
            return self._runner(argv)
        proc = winapi.run_hidden(argv, timeout=60)
        return proc.returncode, winapi.decode_console(proc.stdout + proc.stderr)

    def _sc(self) -> str:
        return ntpath.join(self.env.get('SystemRoot', 'C:\\Windows'), 'System32', 'sc.exe')

    def set_start_type(self, name: str, start: str, stop_now: bool = False, log: bool = True,
                       allow_system: bool = False) -> dict:
        if start not in _SC_START:
            raise ValueError('未知的启动类型')
        current = next((s for s in self.list() if s['name'] == name), None)
        if current is None:
            raise ValueError('服务不存在，请刷新列表')
        if not current['editable'] and not allow_system:
            raise PermissionError('这是 Windows 或微软的服务，盘序不修改系统服务')
        if not self.admin:
            raise PermissionError('修改服务需要管理员权限')
        rc, output = self._run([self._sc(), 'config', name, 'start=', _SC_START[start]])
        if rc != 0:
            raise RuntimeError(output.strip().splitlines()[-1] if output.strip() else f'sc.exe 返回 {rc}')
        stopped = False
        if stop_now and start == 'disabled' and current['status'] == 'running':
            rc2, _ = self._run([self._sc(), 'stop', name])
            stopped = rc2 == 0
        if log and self.oplog is not None and current['start_type'] != start:
            self.oplog.add('service', f'服务「{current["display_name"]}」启动类型：{current["start_label"]} → {START_TYPES[start]}',
                           items=[{'name': name, 'display_name': current['display_name'], 'from': current['start_type'],
                                   'to': start, 'stopped': stopped}],
                           revert={'action': 'service', 'name': name, 'start_type': current['start_type']})
        return {'name': name, 'start_type': start, 'stopped': stopped}
