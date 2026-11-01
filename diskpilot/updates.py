"""Manual public-release discovery; never an installer or credential consumer."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import __version__


REPOSITORY_URL = 'https://github.com/heroliss/panxu'
RELEASE_API = 'https://api.github.com/repos/heroliss/panxu/releases/latest'
# The source repository is private. Enabling public discovery requires a
# deliberate release-source decision, not a user-supplied URL or credential.
UPDATE_SOURCE_PUBLIC = False
MAX_RESPONSE_BYTES = 128 * 1024
REQUEST_TIMEOUT = 4
READ_DEADLINE = 10
CHECK_INTERVAL = 60
NO_SOURCE = '尚未提供公开更新源，暂时无法比较版本。'


def about():
    return dict(name='盘序', subtitle='AI 磁盘清理助手', version=__version__, author='Liss',
                description='面向磁盘整理的 LLM Harness，协助模型调查、解释并记录用户选择。',
                repository_url=REPOSITORY_URL,
                license_name='PolyForm Noncommercial License 1.0.0',
                license_url=REPOSITORY_URL + '/blob/main/LICENSE', simulation=True,
                updates=dict(manual_check=True, automatic_download=False, automatic_install=False,
                             source='github_releases',
                             source_state='configured' if UPDATE_SOURCE_PUBLIC else 'not_configured',
                             message='仅手动检查公开正式发布版本。' if UPDATE_SOURCE_PUBLIC else NO_SOURCE))


class _CheckError(Exception):
    def __init__(self, code, *, retry_after=0):
        super().__init__(code)
        self.retry_after = retry_after


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise _CheckError('redirect_blocked')


def _read_release():
    """One anonymous fixed-host request; no proxy, cookies, auth or retries."""
    request = Request(RELEASE_API, headers={
        'Accept': 'application/vnd.github+json', 'Accept-Encoding': 'identity',
        'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'PanXu-update-check/' + __version__,
    }, method='GET')
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=REQUEST_TIMEOUT) as response:
            if response.status != 200:
                raise _CheckError('unexpected_status')
            if response.headers.get_content_type() != 'application/json':
                raise _CheckError('invalid_response')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                raise _CheckError('response_budget')
            deadline, chunks, size = time.monotonic() + READ_DEADLINE, [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise _CheckError('network_timeout')
                chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise _CheckError('response_budget')
                chunks.append(chunk)
            value = json.loads(b''.join(chunks))
            if not isinstance(value, dict):
                raise _CheckError('invalid_response')
            return value
    except HTTPError as exc:
        status = exc.code
        limited = status == 429 or (status == 403 and (exc.headers.get('X-RateLimit-Remaining') == '0' or exc.headers.get('Retry-After')))
        waits = [CHECK_INTERVAL]
        if limited:
            for name, offset in (('Retry-After', 0), ('X-RateLimit-Reset', time.time())):
                value = exc.headers.get(name, '')
                if len(value) <= 12 and value.isdigit():
                    waits.append(min(86400, max(0, int(value) - offset)))
        exc.close()
        raise _CheckError('no_public_source' if status == 404 else 'rate_limited' if limited else 'remote_error',
                          retry_after=max(waits)) from None
    except (TimeoutError, URLError, OSError):
        raise _CheckError('network_error') from None
    except (ValueError, UnicodeError):
        raise _CheckError('invalid_response') from None


_VERSION = re.compile(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
                      r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
                      r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z', re.ASCII)


def _version(value):
    if not isinstance(value, str) or len(value) > 128 or not (match := _VERSION.fullmatch(value)):
        raise _CheckError('unsupported_version')
    prerelease = (match[4] or '').split('.') if match[4] else []
    if any(part.isdigit() and len(part) > 1 and part[0] == '0' for part in prerelease):
        raise _CheckError('unsupported_version')
    return tuple(int(match[i]) for i in (1, 2, 3)), prerelease


def _compare(first, second):
    left, right = _version(first), _version(second)
    if left[0] != right[0]:
        return 1 if left[0] > right[0] else -1
    a, b = left[1], right[1]
    if not a or not b:
        return (not a) - (not b)
    for x, y in zip(a, b):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return 1 if int(x) > int(y) else -1
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return 1 if x > y else -1
    return (len(a) > len(b)) - (len(a) < len(b))


class UpdateChecker:
    def __init__(self, *, public_source=None):
        self.public_source = UPDATE_SOURCE_PUBLIC if public_source is None else public_source
        self.lock = threading.Lock()
        self.last = None
        self.last_at = float('-inf')
        self.wait = CHECK_INTERVAL

    @staticmethod
    def _result(status, message, *, code=None, latest=None, url=None):
        return dict(status=status, current_version=__version__, latest_version=latest,
                    release_url=url, message=message, code=code, source='github_releases',
                    checked_at=datetime.now(timezone.utc).isoformat(), cached=False)

    def check(self):
        if not self.public_source:
            return self._result('not_configured', NO_SOURCE, code='source_not_configured')
        if not self.lock.acquire(blocking=False):
            return self._result('failed', '正在检查更新，请稍候。', code='check_in_progress')
        try:
            if self.last is not None and time.monotonic() - self.last_at < self.wait:
                return dict(self.last, cached=True)
            self.wait = CHECK_INTERVAL
            try:
                release = _read_release()
                if release.get('draft') is not False or release.get('prerelease') is not False:
                    raise _CheckError('invalid_release')
                tag = release.get('tag_name')
                comparison = _compare(tag, __version__)
                latest = tag.removeprefix('v')
                # Do not trust an arbitrary html_url returned by a remote body.
                url = REPOSITORY_URL + '/releases/tag/' + quote(tag, safe='')
                if comparison > 0:
                    result = self._result('update_available', f'发现新版本 {latest}。可查看发布说明；程序不会自动下载或安装。', latest=latest, url=url)
                elif comparison == 0:
                    result = self._result('up_to_date', '当前版本与最新公开正式发布版本相同。', latest=latest, url=url)
                else:
                    result = self._result('current_ahead', '当前版本高于最新公开正式发布版本。', latest=latest, url=url)
            except _CheckError as exc:
                code = str(exc)
                self.wait = max(CHECK_INTERVAL, exc.retry_after)
                if code == 'no_public_source':
                    result = self._result('no_public_source', '尚未提供可访问的公开正式发布版本，暂时无法比较版本。', code=code)
                else:
                    message = '检查更新暂时受限，请稍后再试。' if code == 'rate_limited' else '暂时无法检查更新，请稍后重试；当前版本状态尚未确认。'
                    result = self._result('failed', message, code=code)
            self.last, self.last_at = result, time.monotonic()
            return dict(result)
        finally:
            self.lock.release()
