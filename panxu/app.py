"""Application entry: single instance, elevation hand-off, pywebview window."""
from __future__ import annotations

import argparse
import base64
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import time

from . import APP_NAME, __version__, paths, ui_bundle, winapi
from .api import Backend, Bridge

log = logging.getLogger('panxu')


def default_data_dir() -> str:
    base = paths.environment().get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'PanXu')


def legacy_key_paths(data_dir: str) -> list[str]:
    """Where PanXu 0.x kept model_settings.json (its API key is imported once)."""
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else ''
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = [os.path.join(exe_dir, 'userdata', 'model_settings.json')] if exe_dir else []
    out += [os.path.join(project, 'dist', 'PanXu', 'userdata', 'model_settings.json'),
            os.path.join(data_dir, 'model_settings.json')]
    return out


def prepare_sandbox(root: str, real_data_dir: str):
    """Open (or build) the simulation and its own data folder.

    The first time, the configured model and its key are copied from the real
    data folder, so the simulation works with AI straight away. Nothing else
    (history, exclusions, cache) is shared.
    """
    from . import sandbox
    from .settings import Settings
    sb = sandbox.Sandbox(root)
    sandbox.activate(sb)
    data_dir = os.path.join(sb.root, 'data')
    if not os.path.isdir(data_dir):
        os.makedirs(data_dir)
        real = Settings(real_data_dir)
        sim = Settings(data_dir)
        ai = real.get()['ai']
        sim.update({'ai': ai, 'extra_personal_paths': [sb.native('D/资料'), sb.native('D/下载')]})
        for name in ('ai_key:' + ai.get('preset', ''),):
            key = real.get_secret(name)
            if key:
                sim.set_secret(name, key)
    return sb, data_dir


def setup_logging(data_dir: str, debug: bool):
    os.makedirs(data_dir, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(os.path.join(data_dir, 'panxu.log'), maxBytes=2_000_000,
                                                   backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)


def _self_command() -> tuple[str, list[str]]:
    """How to start this program again (frozen exe or python + run.py)."""
    if getattr(sys, 'frozen', False):
        return sys.executable, []
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'run.py')
    return sys.executable, [script]


def main(argv=None):
    parser = argparse.ArgumentParser(description='盘序：Windows 磁盘清理助手')
    parser.add_argument('--data-dir')
    parser.add_argument('--debug', action='store_true', help='enable WebView developer tools')
    parser.add_argument('--elevated', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--user-env', help=argparse.SUPPRESS)
    parser.add_argument('--sandbox', nargs='?', const='', metavar='DIR',
                        help='演练环境: run against a simulated PC (default %%LOCALAPPDATA%%\PanXu\sandbox)')
    args = parser.parse_args(argv)

    if args.user_env:
        try:
            paths.set_env_override(json.loads(base64.b64decode(args.user_env).decode('utf-8')))
        except (ValueError, UnicodeDecodeError):
            pass
    data_dir = args.data_dir or default_data_dir()
    sb = None
    if args.sandbox is not None:
        sb, data_dir = prepare_sandbox(args.sandbox or os.path.join(default_data_dir(), 'sandbox'), data_dir)
    setup_logging(data_dir, args.debug)
    log.info('PanXu %s starting (admin=%s, sandbox=%s)', __version__, winapi.is_admin(), sb.root if sb else None)

    instance = winapi.SingleInstance()
    deadline = time.time() + (15 if args.elevated else 0)
    while not instance.acquire():
        if time.time() >= deadline:
            if not winapi.focus_window(APP_NAME):
                winapi.message_box('盘序已经在运行。', APP_NAME)
            return 0
        time.sleep(0.3)  # the previous (non-elevated) instance is still closing

    try:
        import webview
    except ImportError:
        winapi.message_box('缺少 pywebview 组件。请运行 pip install -r requirements.txt。', APP_NAME)
        return 1

    window_ref = {}

    def relaunch() -> bool:
        exe, prefix = _self_command()
        user_env = {k: os.environ.get(k, '') for k in paths.USER_ENV_KEYS}
        user_env.update({k: v for k, v in paths.environment().items() if k in paths.USER_ENV_KEYS})
        token = base64.b64encode(json.dumps(user_env).encode('utf-8')).decode('ascii')
        extra = ['--elevated', '--user-env', token]
        if args.data_dir:
            extra += ['--data-dir', args.data_dir]
        if args.sandbox is not None:
            extra += ['--sandbox', sb.root]
        params = subprocess.list2cmdline(prefix + extra)
        if not winapi.relaunch_as_admin(exe, params, os.getcwd()):
            return False
        # The mutex is released only after the window has closed (finally below);
        # the elevated instance waits for it.
        window = window_ref.get('w')
        if window is not None:
            window.destroy()
        return True

    backend = Backend(data_dir, window_provider=lambda: window_ref.get('w'), relaunch=relaunch, sandbox=sb,
                      legacy_key_paths=None if sb else legacy_key_paths(data_dir))
    html = ui_bundle.build()
    title = APP_NAME + ('（演练环境）' if sb else '')
    window = webview.create_window(title, html=html, js_api=Bridge(backend), width=1320, height=860,
                                   min_size=(1040, 700), text_select=True, background_color='#F4F5F7')
    window_ref['w'] = window
    # Separate WebView2 profile folders: an elevated and a normal WebView2 must
    # never share one user-data folder.
    storage = os.path.join(data_dir, 'webview-admin' if winapi.is_admin() else 'webview')
    options = dict(gui='edgechromium' if winapi.IS_WINDOWS else None, debug=args.debug)
    try:
        try:
            webview.start(storage_path=storage, **options)
        except TypeError:  # very old pywebview without storage_path
            webview.start(**options)
    finally:
        instance.release()
    return 0
