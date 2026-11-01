"""Fixed, bundled task guidance for model-led investigation.

This registry reads only explicitly listed application resources. It neither
loads user files/URLs nor recommends, executes, or grants an action. Callers may
send the returned public guidance to their selected model; no machine evidence
is collected here. Output budgets conservatively count serialized UTF-8 bytes,
not the unreliable English-only 'four characters per token' approximation.
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat

from .scanner import _checked_root, _identity, _is_reparse


CHECKED_AT = '2026-11-02'
SKILL_VERSION = '1.0.0'
MAX_RESOURCE_BYTES = 32768
MAX_OUTPUT_TOKENS = 8000
_ASSET_DIR = Path(__file__).absolute().parent.parent / 'skills'


def _source(title, url, version='web_snapshot_2026-11-02'):
    return dict(title=title, url=url, checked_at=CHECKED_AT, version=version)


_SOURCES = {
    'storage': _source('Microsoft · Windows 空间管理', 'https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/free-up-drive-space-in-windows'),
    'upgrade': _source('Microsoft · 删除以前的 Windows 版本', 'https://support.microsoft.com/en-us/windows/deployment/install-upgrade/delete-your-previous-version-of-windows'),
    'components': _source('Microsoft · WinSxS 组件清理', 'https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/clean-up-the-winsxs-folder?view=windows-11'),
    'drivers': _source('Microsoft · Driver Store', 'https://learn.microsoft.com/en-us/windows-hardware/drivers/install/driver-store'),
    'hibernate': _source('Microsoft · 休眠的禁用与恢复', 'https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/disable-and-re-enable-hibernation'),
    'power': _source('Microsoft · 系统电源状态', 'https://learn.microsoft.com/en-us/windows/win32/power/system-power-states'),
    'paging': _source('Microsoft · 分页文件大小评估', 'https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/how-to-determine-the-appropriate-page-file-size-for-64-bit-versions-of-windows'),
    'uninstall': _source('Microsoft · 正规卸载应用', 'https://support.microsoft.com/en-us/windows/apps/uninstall-or-remove-apps-and-programs-in-windows'),
    'installer': _source('Microsoft · Windows Installer 缓存', 'https://learn.microsoft.com/en-us/troubleshoot/windows-client/application-management/missing-windows-installer-cache'),
    'known_folders': _source('Microsoft · 已知文件夹重定向 API', 'https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shsetknownfolderpath'),
    'onedrive': _source('Microsoft · 迁移 OneDrive 文件夹', 'https://support.microsoft.com/en-us/onedrive/change-the-location-of-your-onedrive-folder'),
    'uv': _source('Astral · uv 缓存', 'https://docs.astral.sh/uv/concepts/cache/'),
    'pip': _source('PyPA · pip 缓存', 'https://pip.pypa.io/en/stable/topics/caching/', '26.2.2'),
    'npm': _source('npm · 缓存管理', 'https://docs.npmjs.com/cli/v11/commands/npm-cache/', '11.21.1'),
}


def _skill(ident, name, purpose, when, risk, readonly, sources, tags):
    return dict(id=ident, name=name, summary=purpose, purpose=purpose,
                applies_when=when, version=SKILL_VERSION, checked_at=CHECKED_AT,
                risk=risk, readonly_support=readonly, execution_support='guidance_only',
                capabilities=dict(readonly=True, reads_user_files=False, network=False,
                                  real_execution=False, automatic_recommendation=False),
                sources=[_SOURCES[key] for key in sources], tags=tags,
                provenance='既往整理经验的通用归纳与官方资料；不代表当前电脑状态。')


_SKILLS = [
    _skill('application-caches', '应用与开发工具缓存', '区分可重建缓存、安装环境和个人数据，解释重新下载或生成的代价。',
           ['发现较大缓存目录', '考虑清理 uv、pip、npm 或图形缓存'], 'review', 'partial_metadata', ['uv', 'pip', 'npm'],
           ['缓存', 'cache', 'uv', 'pip', 'npm', 'NVIDIA', 'DXCache', 'D3DSCache']),
    _skill('temporary-files', '临时文件与占用', '核实临时目录的实际归属和任务状态，按证据处理未使用内容。',
           ['看到 Temp、tmp 或临时前缀', '清理后空间很快重新增长'], 'review', 'partial_metadata', ['storage'],
           ['临时', 'temp', 'tmp', '占用', '锁定']),
    _skill('downloads-installers', '下载与旧安装包', '区分安装包、便携程序、解压目录和个人下载，帮助用户决定去留。',
           ['整理下载或指定目录', '发现 EXE、MSI、分卷压缩包'], 'personal_choice', 'partial_metadata', ['uninstall', 'installer'],
           ['下载', 'download', '安装包', 'exe', 'msi', '压缩包', '便携']),
    _skill('backups-upgrades', '旧备份与系统升级文件', '区分个人归档、临时清理备份、导出驱动和系统回退材料。',
           ['发现旧镜像或备份目录', '考虑 Windows.old 或旧驱动备份'], 'recovery_loss', 'partial_metadata', ['upgrade', 'drivers'],
           ['备份', 'backup', '镜像', 'GHO', '驱动', 'windows.old', '回退', '升级']),
    _skill('windows-components', 'Windows 组件与系统清理', '解释组件仓库与受管系统文件，应使用 Windows 支持的维护入口。',
           ['WinSxS 或 Windows 目录占用较大', '考虑系统更新与组件清理'], 'system_managed', 'explanation_only', ['components', 'installer'],
           ['系统', 'windows', 'winsxs', '组件', 'dism', '更新']),
    _skill('hibernation-startup', '休眠与快速启动', '结合断电恢复、低电量策略和启动体验评估取舍，不能视作普通垃圾。',
           ['发现 hiberfil.sys', '讨论休眠空间、合盖或快速启动'], 'system_setting', 'explanation_only', ['hibernate', 'power'],
           ['休眠', 'hibernate', 'hiberfil', '快速启动', '电源', '低电量']),
    _skill('pagefile-memory', '分页文件与虚拟内存', '根据提交峰值、转储需求和磁盘状态评估配置，避免通用固定大小。',
           ['发现 pagefile.sys', '出现临时页面文件或虚拟内存不足提示'], 'system_setting', 'explanation_only', ['paging'],
           ['分页', '页面文件', 'pagefile', '虚拟内存', '内存', '转储']),
    _skill('uninstall-residue', '正规卸载与残留核对', '先识别软件与正规卸载途径，重新观察后再判断是否存在可清残留。',
           ['程序目录或软件已不再使用', '卸载后仍看到目录、服务或启动项'], 'application_change', 'partial_metadata', ['uninstall', 'installer'],
           ['卸载', 'uninstall', '残留', '程序', '软件', '服务', '启动项']),
    _skill('data-folder-migration', '资料与已知文件夹迁移', '空间不足但资料仍有用时，区分普通复制、已知文件夹改址和云同步迁移。',
           ['资料需要保留但系统盘空间紧张', '考虑移动文档、下载、桌面或同步目录'], 'personal_data', 'partial_metadata', ['known_folders', 'onedrive', 'storage'],
           ['迁移', '移动', '资料', '已知文件夹', '文档', '桌面', 'onedrive', '下载', '其他盘']),
]
_BY_ID = {item['id']: item for item in _SKILLS}
BUNDLED_SKILL_FILES = tuple(item['id'] + '.md' for item in _SKILLS)
REGISTRY_VERSION = 'knowledge-v1-' + hashlib.sha256(json.dumps(_SKILLS, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _integer(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{label} 必须为 {low} 到 {high} 的整数')
    return value


def _stamp(payload, token_budget):
    """Budget the complete registry response, including its accounting fields."""
    payload['budget'] = dict(token_budget=token_budget, measurement='serialized_utf8_bytes_upper_bound', used=0)
    for _ in range(5):
        # Match the task runtime's normal tool-result JSON serialization. This
        # includes spacing, escaping, metadata and the accounting object itself.
        used = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        if used == payload['budget']['used']:
            break
        payload['budget']['used'] = used
    return payload['budget']['used']


def _cursor(kind, scope, offset):
    value = [REGISTRY_VERSION, kind, scope, offset]
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')


def _offset(cursor, kind, scope):
    if cursor is None:
        return 0
    try:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
            raise ValueError()
        version, actual_kind, actual_scope, position = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
        if version != REGISTRY_VERSION or actual_kind != kind or actual_scope != scope:
            raise ValueError()
        if type(position) is not int or position < 0:
            raise ValueError()
        return position
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError('cursor 无效、范围不符或指南内容版本已变化') from exc


def _body(skill_id):
    # No user-supplied path is joined here: look up a fixed resource first.
    if not isinstance(skill_id, str) or skill_id not in _BY_ID:
        raise ValueError('未知技能 ID；只允许读取程序内置指南')
    root, _ = _checked_root(str(_ASSET_DIR))
    path = os.path.join(root, skill_id + '.md')
    before = os.lstat(path)
    if _is_reparse(before) or not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RESOURCE_BYTES:
        raise ValueError('内置指南资源不是允许的普通文件或超过大小限制')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    try:
        info = os.fstat(descriptor)
        if _is_reparse(info) or _identity(info, path) != _identity(before, path) or info.st_size > MAX_RESOURCE_BYTES:
            raise ValueError('内置指南资源身份变化')
        chunks = []
        remaining = MAX_RESOURCE_BYTES + 1
        while remaining:
            data = os.read(descriptor, min(remaining, 8192))
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        raw = b''.join(chunks)
        after = os.fstat(descriptor)
        if len(raw) > MAX_RESOURCE_BYTES or after.st_size != info.st_size or after.st_mtime_ns != info.st_mtime_ns:
            raise ValueError('内置指南资源过大或在读取期间变化')
    finally:
        os.close(descriptor)
    text = raw.decode('utf-8')
    return text, hashlib.sha256(raw).hexdigest()


class SkillRegistry:
    """Stateless local allowlist; safe to share across task owners."""

    def discover(self, *, query='', cursor=None, limit=5, token_budget=3000):
        _integer(limit, 1, 10, 'limit')
        _integer(token_budget, 512, MAX_OUTPUT_TOKENS, 'token_budget')
        if not isinstance(query, str) or len(query) > 160 or '\x00' in query:
            raise ValueError('query 必须为最多 160 字符的文本')
        query = query.strip().casefold()
        scope = hashlib.sha256(query.encode()).hexdigest()[:24]
        after = _offset(cursor, 'catalog', scope)
        terms = query.split()
        records = [record for record in _SKILLS if not terms or any(term in json.dumps(record, ensure_ascii=False).casefold() for term in terms)]
        if after > len(records):
            raise ValueError('cursor 超出技能目录范围')
        result = dict(items=[], total=len(records), registry_version=REGISTRY_VERSION,
                      guidance_only=True, next_cursor=None, has_more=False)
        for record in records[after:after + limit]:
            # Discovery does not open content resources; full provenance remains
            # visible without eagerly loading nine separate guides.
            item = copy.deepcopy({key: value for key, value in record.items() if key not in ('tags', 'provenance', 'purpose')})
            result['items'].append(item)
            position = after + len(result['items'])
            result.update(has_more=position < len(records), next_cursor=_cursor('catalog', scope, position) if position < len(records) else None)
            if _stamp(result, token_budget) > token_budget:
                result['items'].pop()
                break
        position = after + len(result['items'])
        result.update(has_more=position < len(records), next_cursor=_cursor('catalog', scope, position) if position < len(records) else None)
        if _stamp(result, token_budget) > token_budget or not result['items'] and result['has_more']:
            raise ValueError('输出预算不足以容纳一项技能元数据，请提高程序侧预算')
        return result

    def load(self, skill_id, *, cursor=None, token_budget=4000):
        _integer(token_budget, 512, MAX_OUTPUT_TOKENS, 'token_budget')
        text, fingerprint = _body(skill_id)
        after = _offset(cursor, 'content', skill_id + ':' + fingerprint)
        if after > len(text):
            raise ValueError('cursor 超出指南内容范围')
        result = copy.deepcopy(_BY_ID[skill_id])
        result.pop('tags', None)
        result.update(skill_id=skill_id, content='', sha256=fingerprint, content_fingerprint=fingerprint,
                      read_at=datetime.now(timezone.utc).isoformat(), registry_version=REGISTRY_VERSION,
                      guidance_only=True, next_cursor=None, has_more=False, content_offset=after)

        def fill(end):
            result.update(content=text[after:end], has_more=end < len(text),
                          next_cursor=_cursor('content', skill_id + ':' + fingerprint, end) if end < len(text) else None)
            return _stamp(result, token_budget)

        if fill(len(text)) <= token_budget:
            return result
        # Search by Unicode character boundary; the complete serialized response
        # is measured, so Chinese, quoted text and cursor overhead all count.
        low, high = after, len(text)
        while low < high:
            end = (low + high + 1) // 2
            if fill(end) <= token_budget:
                low = end
            else:
                high = end - 1
        used = fill(low)
        if used > token_budget or low == after and after < len(text):
            raise ValueError('输出预算不足以容纳指南元数据和正文，请提高程序侧预算')
        return result

    catalog = discover
    read = load
