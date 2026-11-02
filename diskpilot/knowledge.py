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
SKILL_VERSION = '1.2.0'
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
    'sense': _source('Microsoft · 存储感知', 'https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/manage-drive-space-with-storage-sense'),
    'recycle': _source('Microsoft · 回收站统计 API', 'https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shqueryrecyclebinw'),
    'cloud': _source('Microsoft · OneDrive 文件按需', 'https://support.microsoft.com/en-us/onedrive/save-disk-space-with-onedrive-files-on-demand-for-windows'),
    'delivery': _source('Microsoft · 传递优化与隐私', 'https://support.microsoft.com/en-us/windows/privacy/windows-update-delivery-optimization-and-privacy'),
    'restore': _source('Microsoft · 系统保护与还原点', 'https://support.microsoft.com/en-us/windows/experience/backup-recovery/system-protection'),
    'wsl': _source('Microsoft · WSL 磁盘空间', 'https://learn.microsoft.com/en-us/windows/wsl/disk-space'),
    'wsl_commands': _source('Microsoft · WSL 基本命令', 'https://learn.microsoft.com/en-us/windows/wsl/basic-commands'),
    'docker': _source('Docker · 对象清理边界', 'https://docs.docker.com/engine/manage-resources/pruning/'),
    'unity': _source('Unity · 项目目录', 'https://docs.unity.com/en-us/engine/6000.3/manual/get-started/project-configuration/default-directories', 'Unity_6000.3'),
    'steam': _source('Valve · Steam 下载缓存', 'https://help.steampowered.com/en/faqs/view/6AD7-820D-8BE5-E51F'),
    'health': _source('Microsoft · 存储设备健康警告', 'https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/what-to-do-about-a-critical-warning-for-a-storage-device'),
    'firmware': _source('Sandisk · 特定 SSD 的 Windows 11 24H2 固件问题', 'https://support-en.sandisk.com/app/answers/detailweb/a_id/51469/~/internal-ssd-critical-firmware-update-available-for-solving-bsod-on-windows-11'),
    'retention': _source('Dell · SSD 长期断电与数据保持', 'https://www.dell.com/support/kbdoc/en-us/000198930/ssd-data-retention-considerations-when-powering-off-systems-for-a-prolonged-duration'),
    'startup': _source('Microsoft · 启动应用配置', 'https://support.microsoft.com/en-us/windows/experience/startup-boot/configure-startup-applications-in-windows'),
    'performance': _source('Microsoft · 性能排查', 'https://support.microsoft.com/en-us/windows/experience/performance-optimization/tips-to-improve-pc-performance-in-windows'),
    'chrome_data': _source('Google · Chrome 浏览数据类别', 'https://support.google.com/chrome/answer/2392709?co=GENIE.Platform%3DDesktop&hl=en'),
    'chrome_model': _source('Google · 本地基础模型策略', 'https://chromeenterprise.google/policies/#GenAILocalFoundationalModelSettings'),
    'chrome_safe': _source('Google · 安全浏览保护等级', 'https://support.google.com/chrome/answer/9890866?hl=en'),
    'adobe': _source('Adobe · Photoshop 神经滤镜 FAQ', 'https://helpx.adobe.com/photoshop/desktop/effects-filters/neural-filters/neural-filters-faq.html'),
    'oem': _source('ASUS · 官方驱动与工具来源', 'https://www.asus.com/support/download-center/'),
    'stat_times': _source('Python · stat 时间语义与精度', 'https://docs.python.org/3.14/library/os.html#os.stat_result', 'Python_3.14'),
    'file_times': _source('Microsoft · Windows 文件时间', 'https://learn.microsoft.com/en-us/windows/win32/sysinfo/file-times'),
    'access_times': _source('Microsoft · NTFS 访问时间设置', 'https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/fsutil-behavior'),
    'copy_times': _source('Microsoft · Robocopy 保留元数据', 'https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/robocopy'),
    'install_registration': _source('Microsoft · 软件卸载登记属性', 'https://learn.microsoft.com/en-us/windows/win32/msi/uninstall-registry-key'),
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
    _skill('cleanup-assessment', '综合判断与清理倾向评分', '结合用户目标、用途、价值、重建成本和时间证据形成1–5倾向，区分未知与中立。',
           ['需要判断哪些内容更值得清理或保留', '结合时间、空间、重复和程序状态解释建议'], 'advisory_only', 'partial_metadata',
           ['stat_times', 'file_times', 'access_times', 'copy_times', 'install_registration'],
           ['评分', '倾向', 'rating', 'cleanup_rating', '时间', '创建', '修改', '访问', '用途', '价值', '综合', '保留']),
    _skill('application-caches', '应用与开发工具缓存', '区分可重建缓存、安装环境和个人数据，解释重新下载或生成的代价。',
           ['发现较大缓存目录', '考虑清理 uv、pip、npm 或图形缓存'], 'review', 'partial_metadata', ['uv', 'pip', 'npm', 'adobe'],
           ['缓存', 'cache', 'uv', 'pip', 'npm', 'NVIDIA', 'DXCache', 'D3DSCache']),
    _skill('temporary-files', '临时文件与占用', '核实临时目录的实际归属和任务状态，按证据处理未使用内容。',
           ['看到 Temp、tmp 或临时前缀', '清理后空间很快重新增长'], 'review', 'partial_metadata', ['storage'],
           ['临时', 'temp', 'tmp', '占用', '锁定']),
    _skill('downloads-installers', '下载与旧安装包', '区分安装包、便携程序、解压目录和个人下载，帮助用户决定去留。',
           ['整理下载或指定目录', '发现 EXE、MSI、分卷压缩包'], 'personal_choice', 'partial_metadata', ['uninstall', 'installer'],
           ['下载', 'download', '安装包', 'exe', 'msi', '压缩包', '便携']),
    _skill('backups-upgrades', '旧备份与系统升级文件', '区分个人归档、临时清理备份、导出驱动和系统回退材料。',
           ['发现旧镜像或备份目录', '考虑 Windows.old 或旧驱动备份'], 'recovery_loss', 'partial_metadata', ['upgrade', 'drivers', 'oem'],
           ['备份', 'backup', '镜像', 'GHO', '驱动', 'windows.old', '回退', '升级']),
    _skill('windows-components', 'Windows 组件与系统清理', '解释组件仓库与受管系统文件，应使用 Windows 支持的维护入口。',
           ['WinSxS 或 Windows 目录占用较大', '考虑系统更新与组件清理'], 'system_managed', 'explanation_only', ['components', 'installer', 'delivery'],
           ['系统', 'windows', 'winsxs', '组件', 'dism', '更新']),
    _skill('hibernation-startup', '休眠与快速启动', '结合断电恢复、低电量策略和启动体验评估取舍，不能视作普通垃圾。',
           ['发现 hiberfil.sys', '讨论休眠空间、合盖或快速启动'], 'system_setting', 'partial_configuration', ['hibernate', 'power'],
           ['休眠', 'hibernate', 'hiberfil', '快速启动', '电源', '低电量']),
    _skill('pagefile-memory', '分页文件与虚拟内存', '根据提交峰值、转储需求和磁盘状态评估配置，避免通用固定大小。',
           ['发现 pagefile.sys', '出现临时页面文件或虚拟内存不足提示'], 'system_setting', 'partial_configuration', ['paging'],
           ['分页', '页面文件', 'pagefile', '虚拟内存', '内存', '转储']),
    _skill('uninstall-residue', '正规卸载与残留核对', '先识别软件与正规卸载途径，重新观察后再判断是否存在可清残留。',
           ['程序目录或软件已不再使用', '卸载后仍看到目录、服务或启动项'], 'application_change', 'partial_metadata', ['uninstall', 'installer'],
           ['卸载', 'uninstall', '残留', '程序', '软件', '服务', '启动项']),
    _skill('data-folder-migration', '资料与已知文件夹迁移', '空间不足但资料仍有用时，区分普通复制、已知文件夹改址和云同步迁移。',
           ['资料需要保留但系统盘空间紧张', '考虑移动文档、下载、桌面或同步目录'], 'personal_data', 'partial_metadata', ['known_folders', 'onedrive', 'storage'],
           ['迁移', '移动', '资料', '已知文件夹', '文档', '桌面', 'onedrive', '下载', '其他盘']),
    _skill('storage-sense-recycle', '存储感知与回收站', '区分自动保留期限、普通删除、永久删除与模拟撤销，不把回收站统计当恢复保证。',
           ['考虑一键或定期清理', '需要判断能否恢复已删内容'], 'recovery_loss', 'partial_volume_metadata', ['sense', 'recycle'],
           ['存储感知', '回收站', 'recycle', '恢复', '永久删除', '定期清理']),
    _skill('cloud-placeholders', '云文件与同步占位', '区分仅释放本地副本与同步删除，避免读取占位文件触发下载。',
           ['OneDrive 等同步目录很大', '看到在线文件或希望释放本地空间'], 'personal_data', 'explanation_only', ['cloud'],
           ['云', '占位', 'onedrive', '同步', '在线', '释放空间', 'placeholder']),
    _skill('virtual-disks-containers', 'WSL、Docker 与虚拟磁盘', '虚拟磁盘可能包含完整环境和数据库，先在所属系统中区分缓存与持久数据。',
           ['发现 VHDX 或开发环境占用', 'WSL/Docker 内部删除后宿主空间未回落'], 'data_loss', 'metadata_only', ['wsl', 'wsl_commands', 'docker'],
           ['wsl', 'docker', 'vhd', 'vhdx', '容器', '虚拟盘', '镜像', 'volume']),
    _skill('games-development', '游戏与开发项目', '区分可重建缓存、源码与素材、游戏存档和便携运行环境。',
           ['Unity、Git 项目或游戏目录较大', '考虑清理构建产物、游戏缓存'], 'personal_data', 'partial_metadata', ['unity', 'steam'],
           ['游戏', 'steam', 'unity', 'library', 'node_modules', 'venv', 'git', '源码', '开发', '存档']),
    _skill('restore-recovery', '系统保护与恢复材料', '还原点、系统升级回退、OEM 恢复和个人备份用途不同，先核实恢复需求。',
           ['系统还原或恢复目录占用', '最近升级、换驱动或遇到崩溃'], 'recovery_loss', 'explanation_only', ['restore', 'upgrade'],
           ['还原点', '系统保护', '恢复分区', 'vss', 'system volume information', 'recovery', 'winre']),
    _skill('disk-health', '磁盘健康与低速排查', '把历史异常、当前健康和性能分开，先保留证据与备份，避免把刷新写入当常规清理。',
           ['磁盘降速、蓝屏或历史存储异常', '考虑固件、冷数据或刷新文件'], 'device_reliability', 'explanation_only', ['health', 'firmware', 'retention'],
           ['硬盘', 'ssd', 'nvme', '固件', '蓝屏', '健康', '降速', '冷数据', 'smart', '温度']),
    _skill('startup-background', '开机与后台应用', '按用途、状态和实际影响判断按需运行的收益，不把常驻和高内存直接视作无用。',
           ['想减少开机与后台占用', '有重复工具或不再使用的服务'], 'application_change', 'partial_inventory', ['startup', 'performance'],
           ['启动', '后台', '常驻', '进程', '服务', '开机', '内存', '安全软件']),
    _skill('browser-privacy', '浏览器缓存、隐私与本地模型', '分别解释可重建缓存、登录与站点资料、同步记录和模型功能，先取得用户偏好。',
           ['浏览器占用较大', '考虑清隐私记录、离线数据或本地AI模型'], 'privacy_and_session', 'metadata_only', ['chrome_data', 'chrome_model', 'chrome_safe'],
           ['浏览器', 'chrome', 'edge', 'cookie', '隐私', '历史', '密码', '安全浏览', '模型', '会话']),
]
_BY_ID = {item['id']: item for item in _SKILLS}
BUNDLED_SKILL_FILES = tuple(item['id'] + '.md' for item in _SKILLS)
REGISTRY_VERSION = 'knowledge-v2-' + hashlib.sha256(json.dumps(_SKILLS, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


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
            # visible without eagerly loading all separate guides.
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
