"""What 盘序 checks itself about a startup item or a service before the model judges it.

The model only sees what it is given. A service named 「Claude」 whose program sits in the Claude app's own package
looks like any background helper unless it is told that it is part of an app -- and that the app is open right
now. These facts are collected by the program (no model call, nothing outside the item's own folder is read) and
sent as short sentences.
"""
from __future__ import annotations

import ntpath
import os
import re

from . import winapi

_PUBLISHER = re.compile(r'<PublisherDisplayName>([^<]{1,120})</PublisherDisplayName>', re.I)


def app_root(exe: str) -> str:
    """The folder that holds a program's whole app: a Store / MSIX package folder (…\\WindowsApps\\Claude_4.1_x64__id),
    otherwise the program's own folder."""
    if not exe:
        return ''
    parts = re.split(r'[\\/]', exe)
    low = [p.lower() for p in parts]
    if 'windowsapps' in low:
        i = low.index('windowsapps')
        if i + 1 < len(parts) - 1:
            return '\\'.join(parts[:i + 2])
    return ntpath.dirname(exe)


def package_name(root: str) -> str:
    """'Claude' for …\\WindowsApps\\Claude_4.16336.0.0_x64__pzs8sxrjxfjjc ('' outside a package)."""
    parts = re.split(r'[\\/]', root or '')
    if len(parts) >= 2 and parts[-2].lower() == 'windowsapps':
        return parts[-1].split('_', 1)[0]
    return ''


def package_publisher(root: str) -> str:
    """The publisher a package's AppxManifest.xml names (「Anthropic, PBC」), '' when there is none."""
    if not package_name(root):
        return ''
    try:
        with open(os.path.join(root, 'AppxManifest.xml'), encoding='utf-8', errors='replace') as fh:
            m = _PUBLISHER.search(fh.read(200_000))
    except OSError:
        return ''
    return m.group(1).strip() if m else ''


def _key(p: str) -> str:
    return os.path.normcase(os.path.normpath(p or ''))


def _app_open(exe: str, root: str, running: set[str]) -> bool:
    """Another program of the same app (not this one) is running: the user has the app open."""
    if not root:
        return False
    mine, base = _key(exe), _key(root) + os.sep
    return any(p != mine and p.startswith(base) for p in running)


def service_facts(service: dict, running: set[str]) -> tuple[list[str], str]:
    """(facts, publisher) for a service: its state, the app it belongs to, whether that app is open now.
    ``running``: normcased paths of the programs running now."""
    facts = [('正在运行' if service.get('status') == 'running' else '没有运行') + '，' +
             {'auto': '开机自动启动', 'delayed': '开机后延迟自动启动', 'manual': '需要时才启动', 'disabled': '已禁用'}
             .get(service.get('start_type'), service.get('start_label') or '')]
    exe = service.get('exe') or ''
    root = app_root(exe)
    publisher = service.get('publisher') or package_publisher(root)
    pkg = package_name(root)
    if pkg:
        facts.append(f'程序在应用「{pkg}」自己的应用包里，是这个应用的一部分' + (f'（发布者 {publisher}）' if publisher else ''))
    if _app_open(exe, root, running):
        facts.append(f'「{pkg or ntpath.basename(root)}」这个应用现在正开着：用户在用它')
    return facts, publisher


def startup_facts(item: dict, running: set[str]) -> list[str]:
    """Facts for a startup item: is its program running now, is it part of a package."""
    exe = item.get('exe') or ''
    facts = []
    if exe and _key(exe) in running:
        facts.append('这个程序现在正在运行')
    pkg = package_name(app_root(exe))
    if pkg:
        facts.append(f'属于应用「{pkg}」')
    return facts


def running_programs() -> set[str]:
    """Normcased paths of the programs running now (the simulation's in 演练环境)."""
    sim = winapi.simulation()
    if sim is not None:
        return {_key(p) for p in sim.process_paths()}
    try:
        import psutil
    except ImportError:
        return set()
    out = set()
    for proc in psutil.process_iter(['exe']):
        exe = proc.info.get('exe')
        if exe:
            out.add(_key(exe))
    return out
