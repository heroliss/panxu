"""Non-blocking GitHub release update check, independent of file scans and cleanup jobs."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = 'heroliss/panxu'
RELEASES_URL = f'https://github.com/{REPO}/releases'
LATEST_RELEASE_URL = f'{RELEASES_URL}/latest'
LATEST_API_URL = f'https://api.github.com/repos/{REPO}/releases/latest'
ASSET_RE = re.compile(r'^PanXu(?:-v?\d+(?:\.\d+)*)?-(?:Windows|win)-x64\.zip$', re.IGNORECASE)
VERSION_RE = re.compile(r'^[vV]?(\d+(?:\.\d+){1,3})$')
AUTO_CHECK_INTERVAL = 12 * 3600
MAX_RESPONSE_BYTES = 512 * 1024


class UpdateError(Exception):
    """Localized update check error safe to display in the UI."""


def parse_version(text: str) -> tuple[int, ...] | None:
    m = VERSION_RE.match(str(text or '').strip())
    if not m:
        return None
    parts = [int(p) for p in m.group(1).split('.')]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def clean_version(text: str) -> str:
    m = VERSION_RE.match(str(text or '').strip())
    return m.group(1) if m else ''


def is_newer(candidate: str, current: str) -> bool:
    cand = parse_version(candidate)
    curr = parse_version(current)
    if cand is None or curr is None:
        return False
    return cand > curr


def format_size(size: int) -> str:
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return ''
    if size < 1024:
        return f'{size} B'
    if size < 1024 ** 2:
        return f'{size / 1024:.0f} KB'
    if size < 1024 ** 3:
        mb = size / (1024 ** 2)
        return f'{mb:.0f} MB' if mb >= 100 else f'{mb:.1f} MB'
    return f'{size / (1024 ** 3):.1f} GB'


def safe_github_url(url: object) -> str:
    """Allow only official https://github.com/heroliss/panxu/releases... URLs."""
    if not isinstance(url, str) or not url.strip():
        return ''
    try:
        parsed = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return ''
    if (parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.port is not None
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        return ''
    path = parsed.path or ''
    if path.lower() == f'/{REPO}/releases' or path.lower().startswith(f'/{REPO}/releases/'):
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
            return ''
        return url.strip()
    return ''


def extract_notes(body: object, limit: int = 6) -> list[str]:
    """Pull up to ``limit`` clean Chinese/plain-text highlights from a GitHub Release body."""
    if not isinstance(body, str) or not body.strip():
        return []
    notes: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(('```', '---', '>')):
            continue
        if line.startswith('#'):
            continue
        if 'sha256' in line.lower() or line.lower().startswith('checksum'):
            continue
        bullet = re.sub(r'^[-*+]\s+|^\d+\.\s+', '', line).strip()
        if not bullet:
            continue
        bullet = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', bullet)
        bullet = bullet.replace('**', '').replace('__', '').strip()
        if len(bullet) > 140:
            bullet = bullet[:139].rstrip() + '…'
        notes.append(bullet)
        if len(notes) >= limit:
            break
    return notes


def parse_release_payload(payload: object, current_version: str) -> dict:
    if not isinstance(payload, dict):
        raise UpdateError('版本信息格式异常，请稍后再试')
    if payload.get('draft') is not False or payload.get('prerelease') is not False:
        raise UpdateError('暂未找到公开的正式版发布信息')
    tag_name = str(payload.get('tag_name') or '').strip()
    latest = clean_version(tag_name)
    if not latest:
        raise UpdateError('远端版本号格式无法识别')
    release_url = (safe_github_url(payload.get('html_url'))
                   or f'https://github.com/{REPO}/releases/tag/{urllib.parse.quote(tag_name, safe="")}')
    assets = payload.get('assets') if isinstance(payload.get('assets'), list) else []
    matches = []
    expected_prefix = f'/{REPO}/releases/download/{urllib.parse.quote(tag_name, safe="")}/'.lower()
    for asset in assets:
        if not isinstance(asset, dict) or asset.get('state') != 'uploaded':
            continue
        name = str(asset.get('name') or '')
        size = asset.get('size')
        dl = safe_github_url(asset.get('browser_download_url'))
        if not name or not ASSET_RE.match(name) or not dl:
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            continue
        parsed_path = urllib.parse.urlsplit(dl).path
        if not parsed_path.lower().startswith(expected_prefix):
            continue
        if urllib.parse.unquote(parsed_path[len(expected_prefix):]) != name:
            continue
        matches.append((name, size, dl))

    asset_name, asset_size, download_url = matches[0] if len(matches) == 1 else ('', 0, release_url)
    published_at = str(payload.get('published_at') or '')[:10]
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', published_at):
        published_at = ''
    return {
        'latest_version': latest,
        'tag_name': tag_name,
        'has_update': is_newer(latest, current_version),
        'release_url': release_url,
        'download_url': download_url,
        'asset_name': asset_name,
        'asset_size': asset_size,
        'size_label': format_size(asset_size),
        'published_at': published_at,
        'notes': extract_notes(payload.get('body')),
    }


def fetch_latest_payload(current_version: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(
        LATEST_API_URL,
        headers={
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2026-03-10',
            'User-Agent': f'PanXu/{current_version}',
        },
        method='GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https://api.github.com URL
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise UpdateError('版本信息响应过大，已中止读取')
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise UpdateError('GitHub 接口访问较频繁，请稍后再试') from None
        if exc.code == 404:
            raise UpdateError('暂未找到公开的正式版发布信息') from None
        raise UpdateError(f'无法获取版本信息（状态码 {exc.code}）') from None
    except (TimeoutError, socket_timeout_types()) as exc:
        raise UpdateError('连接 GitHub 超时，请稍后再试') from exc
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, 'reason', None), TimeoutError):
            raise UpdateError('连接 GitHub 超时，请稍后再试') from None
        raise UpdateError('无法连接 GitHub 检查更新，请检查网络后重试') from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UpdateError('版本信息格式异常，请稍后再试') from None
    except OSError:
        raise UpdateError('网络暂时不可用，请稍后再试') from None


def socket_timeout_types() -> tuple[type[BaseException], ...]:
    import socket
    return (socket.timeout,)


class UpdateChecker:
    """Thread-safe, cached, non-blocking release checker."""

    def __init__(self, settings, current_version: str, probe=fetch_latest_payload, clock=time.time):
        self.settings = settings
        self.current_version = current_version
        self.probe = probe
        self.clock = clock
        self._lock = threading.RLock()
        self._running = False
        self._error = ''
        self._done = threading.Condition(self._lock)

    def _stored(self) -> dict:
        raw = self.settings.get().get('update_state')
        return dict(raw) if isinstance(raw, dict) else {}

    def status(self) -> dict:
        stored = self._stored()
        with self._lock:
            running = self._running
            error = self._error
        latest = clean_version(stored.get('latest_version', ''))
        checked_at = float(stored.get('checked_at') or 0)
        has_update = bool(latest and is_newer(latest, self.current_version))
        ignored_ver = clean_version(stored.get('ignored_version', ''))
        ignored = bool(has_update and ignored_ver == latest)
        release_url = safe_github_url(stored.get('release_url')) or LATEST_RELEASE_URL
        download_url = safe_github_url(stored.get('download_url')) or release_url
        asset_name = str(stored.get('asset_name') or '')
        asset_size = int(stored.get('asset_size') or 0) if isinstance(stored.get('asset_size'), (int, float)) else 0
        size_label = format_size(asset_size) or str(stored.get('size_label') or '')
        published_at = str(stored.get('published_at') or '')[:10]
        notes = [str(x) for x in (stored.get('notes') or []) if isinstance(x, str)][:6]
        tag_name = str(stored.get('tag_name') or (f'v{latest}' if latest else ''))

        if running:
            state = 'checking'
            message = '正在检查新版本…'
        elif error:
            state = 'error'
            message = error
        elif has_update:
            state = 'available'
            message = f'发现新版本 v{latest}' + (f'（{size_label}）' if size_label else '')
        elif checked_at > 0 and latest:
            state = 'up_to_date'
            message = f'当前已是最新版本 v{self.current_version}'
        else:
            state = 'idle'
            message = f'当前版本 v{self.current_version}'

        return {
            'state': state,
            'current_version': self.current_version,
            'latest_version': latest,
            'tag_name': tag_name,
            'has_update': has_update,
            'ignored': ignored,
            'checked_at': checked_at if checked_at > 0 else None,
            'release_url': release_url,
            'download_url': download_url,
            'asset_name': asset_name,
            'asset_size': asset_size,
            'size_label': size_label,
            'published_at': published_at,
            'notes': notes,
            'message': message,
        }

    def check(self, force: bool = False) -> dict:
        stored = self._stored()
        checked_at = float(stored.get('checked_at') or 0)
        now = self.clock()
        if not force and checked_at > 0 and 0 <= now - checked_at < AUTO_CHECK_INTERVAL:
            return self.status()
        with self._lock:
            if self._running:
                return self.status()
            self._running = True
            self._error = ''
            threading.Thread(target=self._run, daemon=True, name='panxu-update-check').start()
        return self.status()

    def _run(self) -> None:
        err = ''
        parsed = None
        try:
            payload = self.probe(self.current_version)
            parsed = parse_release_payload(payload, self.current_version)
        except UpdateError as exc:
            err = str(exc)
        except Exception:  # noqa: BLE001 - never surface raw network/OS tracebacks
            err = '无法连接 GitHub 检查更新，请稍后再试'
        if parsed is not None:
            prev = self._stored()
            ignored_ver = clean_version(prev.get('ignored_version', ''))
            if ignored_ver and is_newer(parsed['latest_version'], ignored_ver):
                ignored_ver = ''
            try:
                self.settings.update({'update_state': {
                    'checked_at': self.clock(),
                    'latest_version': parsed['latest_version'],
                    'tag_name': parsed['tag_name'],
                    'release_url': parsed['release_url'],
                    'download_url': parsed['download_url'],
                    'asset_name': parsed['asset_name'],
                    'asset_size': parsed['asset_size'],
                    'size_label': parsed['size_label'],
                    'published_at': parsed['published_at'],
                    'notes': parsed['notes'],
                    'ignored_version': ignored_ver,
                }})
            except OSError:
                pass
        with self._lock:
            self._error = err
            self._running = False
            self._done.notify_all()

    def wait(self, timeout: float = 15.0) -> dict:
        with self._done:
            self._done.wait_for(lambda: not self._running, timeout=timeout)
        return self.status()

    def ignore(self, version: str = '') -> dict:
        prev = self._stored()
        ver = clean_version(version) or clean_version(prev.get('latest_version', ''))
        if ver:
            self.settings.update({'update_state': {**prev, 'ignored_version': ver}})
        return self.status()
