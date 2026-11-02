"""Shared cleanup capability registry and durable simulation plans.

Inspection uses bounded read-only adapters. This module never submits cleanup
commands or changes files/system settings; execution remains virtual.
"""
from __future__ import annotations

import copy
import hashlib
import json
import ntpath
import re
import secrets
import threading

from .actions import ActionRuntime, now
from .execution_control import ExecutionControl
from .simulation import AppError
from .system_evidence import system_configuration, volume_snapshot
from .tool_inspection import inspect_metadata, memory_presentation, volume_presentation
from .tool_launch import entry_definition, resolve_entry


REGISTRY_VERSION = 1
MAX_BATCH = 40
CATEGORIES = {'windows': 'Windows空间', 'privacy': '隐私记录', 'system': '系统配置',
              'applications': '软件与缓存', 'development': '开发环境', 'migration': '迁移与高级'}


def _field(name, label, *, choices=None, default=None, kind='string', required=False,
           minimum=None, maximum=None, format=None):
    value = dict(name=name, label=label, type=kind, required=required)
    if choices is not None:
        value['enum'] = list(choices)
    if default is not None:
        value['default'] = default
    if kind == 'string':
        value['maxLength'] = 1024 if format == 'path' else 120
    if minimum is not None:
        value['minimum'] = minimum
    if maximum is not None:
        value['maximum'] = maximum
    if format:
        value['format'] = format
    return value


VOLUME = _field('volume', '目标盘符', default='C:', format='volume')
BROWSER = _field('browser', '浏览器', choices=('edge', 'chrome', 'firefox'), default='edge')
TIME = _field('time_range', '时间范围', choices=('last_hour', 'last_day', 'last_week', 'all'), default='all')
PROFILE = _field('profile', '资料配置', default='current')
TARGET = _field('target_path', '目标目录', required=True, format='path')
SPACE = 'https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/free-up-drive-space-in-windows'
SETTINGS = 'https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings'


def _tool(ident, title, category, description, impact, recovery, *, parameters=(),
          uri=None, url=SPACE, common=False, read=None):
    settings = []  # Native destinations are registered below, separately from sources.
    return dict(id=ident, tool_id=ident, version=1, title=title, category=category,
        description=description, impact=impact, recovery=recovery, common=common,
        parameters=copy.deepcopy(list(parameters)), settings=settings,
        sources=[dict(title='官方说明', url=url)], read=read,
        capabilities=dict(inspect=True, prepare=True, open_settings=bool(uri), apply=False, simulation=True),
        simulation_only=True, implementation='simulation', platform='windows',
        requires_admin=ident in ('component_store', 'windows_upgrade', 'hibernation', 'pagefile', 'system_restore', 'old_drivers', 'optional_features'),
        requires_restart=ident in ('hibernation', 'pagefile', 'old_drivers', 'optional_features'))


_ROWS = [
    _tool('windows_temp', 'Windows临时文件', 'windows', '使用系统清理分类检查临时文件。',
        '系统分类可能含下载和升级材料，仍需按具体类别选择。', '缓存可重新生成；已经清掉的临时内容不保证可恢复。',
        parameters=(VOLUME,), uri='ms-settings:storagesense', common=True, read='volume'),
    _tool('recycle_bin', '清空回收站', 'windows', '查看所选卷当前用户的回收站统计，再决定是否清空。',
        '清空会永久丢弃回收站内容；不会替其他账号清空。', '清空后不提供数据恢复承诺。',
        parameters=(VOLUME,), uri='ms-settings:storagesense', common=True, read='volume'),
    _tool('component_store', 'Windows组件清理', 'windows', '通过受支持的组件维护方式清理已替代的组件。',
        '不能直接删除WinSxS目录；本项不包含ResetBase或删除当前系统组件。', '组件维护清理不是可撤销的目录移动。',
        url='https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/clean-up-the-winsxs-folder?view=windows-11'),
    _tool('delivery_optimization', '传递优化缓存', 'windows', '清理Windows更新分发使用的下载缓存。',
        '后续更新可能重新下载；不会关闭更新功能。', '可重新下载不等于恢复原缓存副本。', uri='ms-settings:delivery-optimization'),
    _tool('windows_upgrade', '旧Windows升级材料', 'windows', '使用系统清理入口处理Windows.old等升级遗留。',
        '删除对应升级回退材料后，将失去相应回退能力。', '不能通过重新开启设置恢复旧系统材料。', uri='ms-settings:storagesense'),
    _tool('store_cache', 'Microsoft Store缓存', 'applications', '按照Store官方故障流程处理其缓存。',
        '真正的wsreset可能打开Store；不等于重置所有应用。', 'Store可重新建立缓存，不能作为数据恢复。',
        url='https://support.microsoft.com/en-us/accounts-billing/microsoft-store-doesn-t-open'),
    _tool('hibernation', '休眠与快速启动', 'system', '检查休眠能力，再选择完整休眠、仅快速启动或关闭。',
        '关闭休眠影响休眠会话和快速启动，需核对电源按钮、低电量动作。', '配置可改回；关闭前保存的休眠会话不能因此恢复。',
        parameters=(_field('mode', '目标模式', choices=('full', 'reduced', 'off'), default='full'),),
        uri='ms-settings:powersleep', read='memory', url='https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/powercfg-command-line-options'),
    _tool('pagefile', '分页文件配置', 'system', '检查提交压力、活动分页与待启动配置，再选择管理方式。',
        '过小可能造成提交失败；配置与当前活动值不同，改变后需重启核对。', '原配置可重新设置，不保证恢复中断的程序或满足所有转储要求。',
        parameters=(_field('mode', '管理方式', choices=('system', 'custom'), default='system'), VOLUME,
            _field('initial_mb', '初始大小（MB）', kind='integer', default=1024, minimum=16, maximum=1048576),
            _field('maximum_mb', '最大大小（MB）', kind='integer', default=2048, minimum=16, maximum=1048576)),
        read='memory', url='https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/how-to-determine-the-appropriate-page-file-size-for-64-bit-versions-of-windows'),
    _tool('system_restore', '系统保护与还原点', 'system', '核对系统保护配额、现有恢复材料及保留需要。',
        '缩减额度可能移除旧快照；关闭保护和删除既有还原点是不同动作。', '重新启用不能找回已经删除的还原点。',
        parameters=(VOLUME, _field('operation', '操作', choices=('review', 'quota', 'disable'), default='review'),
                    _field('quota_percent', '空间上限（%）', kind='integer', default=5, minimum=1, maximum=50)),
        url='https://support.microsoft.com/en-us/windows/experience/backup-recovery/system-protection'),
    _tool('browser_cache', '浏览器文件缓存', 'privacy', '使用浏览器自身入口清理网页文件缓存。',
        '下次访问可能重新下载；本项不清Cookie、密码或离线站点作品。', '缓存可重新生成，不是可撤销删除。',
        parameters=(BROWSER, PROFILE, TIME), common=True,
        url='https://support.google.com/chrome/answer/2392709?co=GENIE.Platform%3DDesktop&hl=en'),
    _tool('browser_privacy', '浏览器隐私记录', 'privacy', '分别选择浏览记录、Cookie、下载记录或站点数据。',
        'Cookie清理可能退出登录，站点数据可能含离线作品；同步账号范围需另核。', '不把隐私正文再次复制到记录或默认备份。',
        parameters=(BROWSER, PROFILE, TIME, _field('data_type', '清理类别', choices=('history', 'cookies', 'download_history', 'site_data'), default='history')),
        common=True, url='https://support.google.com/chrome/answer/2392709?co=GENIE.Platform%3DDesktop&hl=en'),
    _tool('clipboard_history', '剪贴板历史', 'privacy', '清除可清的本机剪贴板历史记录。',
        '固定项目、当前剪贴板和云同步范围需要分别核对。', '不读取或备份剪贴板正文；清除后不保证可恢复。',
        uri='ms-settings:clipboard', url='https://support.microsoft.com/en-us/windows/apps/using-the-clipboard'),
    _tool('recent_history', '最近使用记录', 'privacy', '核对最近文件、快速访问与跳转列表等本机痕迹。',
        '隐藏列表不等于清除全部记录；固定项和各应用历史需分别处理。', '不删除原始文档；记录本身不承诺可恢复。',
        uri='ms-settings:personalization-start', url='https://support.microsoft.com/en-us/windows/experience/fileexplorer/file-explorer-in-windows'),
    _tool('diagnostic_files', '故障转储与诊断材料', 'windows', '识别已不需要的崩溃转储及诊断材料。',
        '正在排查故障时应保留相关材料；不能将所有日志视为垃圾。', '历史故障信息清除后无法靠重新启用采集找回。',
        parameters=(VOLUME,), uri='ms-settings:storagesense', url='https://learn.microsoft.com/en-us/windows/win32/wer/collecting-user-mode-dumps'),
    _tool('uv_cache', 'uv缓存', 'development', '按官方prune或clean语义处理uv缓存。',
        '会减少离线安装和复用能力；symlink链接模式环境可能依赖缓存。', '可重新下载的包可重建，自定义或离线包不保证可找回。',
        parameters=(_field('operation', '清理方式', choices=('prune', 'clean'), default='prune'),),
        url='https://docs.astral.sh/uv/concepts/cache/'),
    _tool('pip_cache', 'pip缓存', 'development', '在明确的Python环境中选择wheel或全部pip缓存。',
        '全清还会清HTTP缓存；后续安装可能需要网络。', '清缓存不卸载已安装的包；原缓存副本不保证可恢复。',
        parameters=(_field('operation', '清理范围', choices=('wheels', 'all'), default='all'),), url='https://pip.pypa.io/en/stable/topics/caching/'),
    _tool('npm_cache', 'npm缓存', 'development', '优先核验npm缓存，空间需要明确时再选择清理。',
        'verify和clean不同；清缓存不删除项目源码或node_modules。', '公开包通常可重下，离线资源不保证恢复。',
        parameters=(_field('operation', '操作', choices=('verify', 'clean'), default='verify'),), url='https://docs.npmjs.com/cli/v11/commands/npm-cache/'),
    _tool('unity_cache', 'Unity项目导入缓存', 'development', '检查明确项目的Library等可重新导入内容。',
        '项目需正常关闭；下次打开可能长时间重新导入，源码和素材不应被纳入。', '可重建不等于原有导入状态可撤销恢复。',
        parameters=(_field('project_path', '项目位置', required=True, format='path'),),
        url='https://docs.unity3d.com/Manual/AssetDatabase.html'),
    _tool('known_folder_move', '文档等目录迁移', 'migration', '使用已知目录机制迁移文档、图片或桌面。',
        '先核对OneDrive重定向、权限、目标空间和路径冲突；修改路径与搬内容分开核验。', '需保留原配置并核实内容，回迁同样需要空间和冲突处理。',
        parameters=(_field('folder', '系统目录', choices=('documents', 'pictures', 'desktop', 'downloads'), default='documents'), TARGET),
        url='https://learn.microsoft.com/en-us/windows/win32/shell/working-with-known-folders'),
    _tool('cache_location', '缓存默认位置', 'migration', '通过受支持配置修改指定工具后续缓存位置。',
        '保存新位置不等于已搬走旧文件；仍在运行的进程可能继续使用旧环境。', '配置可改回，文件搬运和校验需要单独记录。',
        parameters=(_field('program', '工具', choices=('uv', 'pip', 'npm'), default='uv'), TARGET),
        url='https://docs.astral.sh/uv/concepts/cache/'),
    _tool('software_uninstall', '正规卸载与残留复查', 'applications', '核对卸载入口，先正规卸载，再检查已不需要的残留。',
        '卸载可能影响组件和配置，不能用删除程序目录替代。', '重新安装不等于恢复个人配置；不自动清用户资料。',
        parameters=(_field('application', '程序名称', required=True),), uri='ms-settings:appsfeatures',
        url='https://learn.microsoft.com/en-us/windows/package-manager/winget/uninstall'),
    _tool('old_drivers', '旧驱动包', 'applications', '核对使用关系和回退需要后处理旧驱动包。',
        '不按日期批删，不删除当前使用包；不默认force或卸载设备。', '导出包是辅助恢复材料，不保证全部设备配置可恢复。',
        parameters=(_field('published_name', '已核实驱动包名', required=True),),
        url='https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/pnputil-command-syntax'),
    _tool('optional_features', '可选组件与语言包', 'system', '核对依赖与用途，选择明确的Windows可选组件。',
        '禁用与删除负载不同，可能需重启；以后恢复可能需要安装源。', '未确认安装源前不承诺一键恢复。',
        parameters=(_field('feature_name', '已核实组件名', required=True),), uri='ms-settings:optionalfeatures',
        url='https://learn.microsoft.com/en-us/powershell/module/dism/disable-windowsoptionalfeature'),
    _tool('cloud_local_copy', '释放云文件本地副本', 'migration', '通过同步客户端将已同步内容设为仅联机。',
        '删除同步文件可能连云端一起删除；未上传内容和离线需要须先确认。', '云端存在时可重新下载，不等于独立备份。',
        parameters=(_field('provider', '同步客户端', choices=('onedrive', 'dropbox', 'google_drive'), default='onedrive'), TARGET),
        url='https://support.microsoft.com/en-us/onedrive/save-disk-space-with-onedrive-files-on-demand-for-windows'),
    _tool('virtual_disk', '虚拟磁盘空间回收', 'migration', '区分虚拟机内部清理与宿主VHD空间回收。',
        '应明确发行版或虚拟机及运行状态；不能通过注销发行版代替压缩。', '压缩不代表备份；中断和空间需求由实际适配器核对。',
        parameters=(_field('virtual_disk_path', '虚拟磁盘位置', required=True, format='path'),),
        url='https://learn.microsoft.com/en-us/windows/wsl/disk-space'),
]
_ROWS.extend([
    _tool('thumbnail_cache', '资源管理器缩略图缓存', 'windows', '统计缩略图数据库，再由系统清理机制处理。',
        '下次浏览图片或视频时会重新生成预览。', '可重新生成，不是恢复原缓存。',
        url='https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/cleanmgr'),
    _tool('storage_sense', '存储感知', 'windows', '查看所选卷空间，调整Windows自动清理规则。',
        '规则会影响将来的临时文件、回收站和所选类别，开启规则与立即清理不同。', '关闭规则不能恢复此前已删除的内容。',
        parameters=(VOLUME,), read='volume'),
    _tool('file_history', '文件历史记录旧版本', 'system', '通过文件历史记录管理已有备份版本。',
        '旧版本是恢复材料，需明确保留期限和备份目标。', '重新开启备份不能恢复已经清掉的历史版本。',
        url='https://learn.microsoft.com/en-us/windows/win32/devnotes/fhmanagew-exe'),
    _tool('printer_queue', '打印队列与暂存文件', 'windows', '核对打印状态和可读暂存目录。',
        '打印中的任务应先在原队列确认；不把暂存文件直接当作无用内容。', '重新打印需要原始文档。'),
    _tool('browser_models', '浏览器本地模型', 'applications', '查看浏览器下载的本地基础模型占用。',
        '模型可能支持离线AI功能，清理后相关功能可能不可用或再次下载。', '可下载的官方模型可能重建，不代表自定义资源可恢复。',
        parameters=(_field('browser', '浏览器', choices=('edge', 'chrome'), default='edge'),), url='https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/GenAILocalFoundationalModelSettings'),
    _tool('gpu_cache', '显卡着色器缓存', 'applications', '检查已知DirectX及显卡着色器缓存位置。',
        '首次进入游戏或应用可能重新编译着色器并短暂卡顿。', '缓存可重建，不改变驱动安装。'),
    _tool('steam_cache', 'Steam网页与下载缓存', 'applications', '检查可识别的Steam缓存并打开原程序。',
        '下载缓存清理可能需要重新登录，不包含游戏安装与存档。', '缓存可重建，账号登录需自行完成。',
        url='https://help.steampowered.com/en/faqs/view/6AD7-820D-8BE5-E51F'),
    _tool('adobe_cache', 'Adobe媒体缓存', 'applications', '统计已知媒体缓存位置，保留素材、工程与自动保存。',
        '部分产品共享缓存，重新打开工程可能需要重建。', '可重建缓存不等于工程文件备份。',
        url='https://helpx.adobe.com/premiere/desktop/troubleshooting/media-issues/clear-media-cache-using-preferences.html'),
    _tool('conda_cache', 'Conda包缓存', 'development', '检查已知Conda包缓存位置。',
        '软链接环境可能依赖包缓存，不能无差别清空包目录。', '离线与自定义包不保证可重新下载。',
        url='https://docs.conda.io/projects/conda/en/latest/commands/clean.html'),
    _tool('nuget_cache', 'NuGet缓存', 'development', '检查全局包和已知NuGet缓存位置。',
        '后续构建可能需要重新还原依赖。', '私有源或离线包不保证可重新取得。',
        url='https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-nuget-locals'),
    _tool('cargo_cache', 'Cargo项目构建产物', 'development', '检查所选Rust项目的target构建目录。',
        '下次构建可能耗时；不会把源码和清单当缓存。', '可重新构建不等于原有二进制可恢复。',
        parameters=(_field('project_path', '项目位置', required=True, format='path'),),
        url='https://doc.rust-lang.org/cargo/commands/cargo-clean.html'),
    _tool('docker_cache', 'Docker构建缓存与镜像', 'development', '在Docker Desktop中核对本机构建缓存、镜像和容器。',
        '停止的容器仍可能有重要可写数据；远程context不按本机处理。', '重新拉取镜像不能恢复容器未导出的数据。',
        url='https://docs.docker.com/engine/manage-resources/pruning/'),
    _tool('docker_volumes', 'Docker数据卷', 'development', '单独核对数据卷用途和数据库备份。',
        '未被容器引用不代表无用，不与普通缓存混选。', '数据卷需独立备份，不保证恢复。',
        url='https://docs.docker.com/engine/manage-resources/pruning/'),
    _tool('wsl_storage', 'WSL存储与发行版', 'migration', '区分Linux内部文件清理与宿主虚拟磁盘回收。',
        '注销发行版会删除数据，不能作为普通压缩步骤。', '发行版数据需要单独备份。',
        url='https://learn.microsoft.com/en-us/windows/wsl/disk-space'),
    _tool('hf_cache', 'Hugging Face模型缓存', 'development', '检查已知Hub模型缓存，不读取权重内容。',
        '模型版本可能共享文件，自建或离线权重不能按可重下缓存处理。', '私有或已下架模型不保证恢复。',
        url='https://huggingface.co/docs/huggingface_hub/guides/cli'),
    _tool('ollama_models', 'Ollama模型占用', 'applications', '检查已知模型存储，不读取模型内容。',
        '停止运行释放内存与删除模型释放磁盘是不同动作。', '自定义模型不保证可重新拉取。',
        url='https://docs.ollama.com/cli'),
    _tool('search_history', 'Windows搜索历史', 'privacy', '分别处理本机搜索记录与账号云端历史。',
        '清本机记录不表示已删除云端记录。', '历史记录清掉后不保证可恢复。'),
    _tool('activity_history', 'Windows活动历史', 'privacy', '核对本机活动记录及后续收集开关。',
        '关闭后续记录与清除已有记录是两种操作。', '重新打开记录不能找回已删除历史。'),
    _tool('diagnostic_privacy', 'Windows诊断数据', 'privacy', '在系统界面核对诊断数据及隐私选项。',
        '删除已有数据不等于停止未来收集。', '不把诊断正文另存到操作记录。'),
    _tool('app_reset', '应用修复与重置', 'applications', '优先修复，必要时再考虑重置应用数据。',
        '重置可能丢失本机设置和登录状态，不是普通清缓存。', '重新安装不能保证恢复原配置。'),
    _tool('background_services', '后台服务', 'system', '打开Windows服务管理，核对状态、用途和依赖。',
        '停止当前服务与禁用启动方式不同，可能影响依赖功能。', '重新启动服务不能恢复所有中断业务。'),
    _tool('startup_apps', '开机启动应用', 'system', '在系统界面核对登录时自动启动的应用。',
        '关闭启动不卸载程序，也不结束当前进程。', '可重新启用，但不会自动恢复先前会话。'),
    _tool('memory_usage', '内存与提交压力', 'system', '查看当前物理内存、提交量和分页快照。',
        '不把强制缩工作集包装成解决泄漏；一次快照不代表长期负载。', '正常关闭应用前需保存工作。', read='memory'),
])
REGISTRY = {row['id']: row for row in _ROWS}
_TOOL_ENTRIES = {
    'windows_temp': ('disk_cleanup', 'storage'), 'recycle_bin': ('recycle_bin',),
    'component_store': ('disk_cleanup',), 'delivery_optimization': ('delivery_optimization',),
    'windows_upgrade': ('disk_cleanup',), 'store_cache': ('store',), 'hibernation': ('power_options',),
    'pagefile': ('advanced_system',), 'system_restore': ('system_protection',),
    'clipboard_history': ('clipboard',), 'recent_history': ('folder_options', 'recent'),
    'diagnostic_files': ('disk_cleanup',), 'known_folder_move': ('explorer',),
    'software_uninstall': ('uninstall_programs',), 'optional_features': ('optional_features',),
    'thumbnail_cache': ('disk_cleanup',), 'storage_sense': ('storage_sense',),
    'file_history': ('file_history',), 'printer_queue': ('printers',),
    'steam_cache': ('steam',), 'docker_cache': ('docker',), 'docker_volumes': ('docker',),
    'search_history': ('search_privacy',), 'activity_history': ('activity_privacy',),
    'diagnostic_privacy': ('diagnostic_privacy',), 'app_reset': ('apps',),
    'background_services': ('services',), 'startup_apps': ('startup',), 'memory_usage': ('task_manager',),
}
for _ident, _entries in _TOOL_ENTRIES.items():
    REGISTRY[_ident]['settings'] = [entry_definition(entry) for entry in _entries]
for _ident in ('browser_cache', 'browser_privacy'):
    REGISTRY[_ident]['settings'] = [dict(entry_definition(browser + '_clear'), browser=browser)
                                  for browser in ('edge', 'chrome', 'firefox')]
REGISTRY['browser_models']['settings'] = [dict(entry_definition(browser + '_settings'), browser=browser)
                                        for browser in ('edge', 'chrome')]
for _row in _ROWS:
    _row['capabilities']['open_settings'] = bool(_row['settings'])


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class ToolSimulationBackend:
    name = 'simulation'

    def __init__(self, toolkit):
        self.toolkit, self.state = toolkit, toolkit.state

    def accepts(self, entry):
        return entry.get('kind') == 'cleanup_tool'

    def receipt(self, operation_id):
        with self.state.lock:
            row = self.state.db.execute('SELECT payload FROM cleanup_tool_receipts WHERE operation_id=?', (operation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def apply(self, entry, operation_id, patterns):
        receipt = self.receipt(operation_id)
        if receipt:
            return receipt
        self.toolkit._validate_entry(entry)
        receipt = dict(id='tool-receipt-' + operation_id, operation_id=operation_id,
            status='simulated', entry_id=entry['id'], signature=entry['signature'],
            tool_id=entry['tool_id'], parameters=copy.deepcopy(entry['parameters']),
            actual_reclaimed_bytes=0, at=now(), active=True)
        with self.state.lock, self.state.db:
            self.state.db.execute('INSERT INTO cleanup_tool_receipts VALUES(?,?,?)',
                (operation_id, receipt['id'], json.dumps(receipt, ensure_ascii=False)))
        return receipt

    def verify(self, entry, batch):
        receipt = self.receipt(batch.get('operation_id', ''))
        return bool(receipt and receipt['status'] == 'simulated' and receipt.get('active') is True
            and receipt['entry_id'] == entry['id'] and receipt['signature'] == entry['signature']
            and receipt['tool_id'] == entry['tool_id'] and receipt['parameters'] == entry['parameters'])

    def current_state(self, batch_id):
        with self.state.lock:
            row = self.state.db.execute('SELECT payload FROM cleanup_tool_receipts WHERE id=?', (batch_id,)).fetchone()
        return 'undone' if row and not json.loads(row[0]).get('active') else 'recorded'


class CleanupToolkit:
    def __init__(self, state):
        self.state = state
        with state.lock, state.db:
            state.db.execute('CREATE TABLE IF NOT EXISTS cleanup_tool_plans (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT)')
            state.db.execute('CREATE TABLE IF NOT EXISTS cleanup_tool_receipts (operation_id TEXT PRIMARY KEY, id TEXT UNIQUE, payload TEXT)')
            if not hasattr(state, '_cleanup_tool_lock'):
                state._cleanup_tool_lock = threading.RLock()
                state._cleanup_tool_controls = {}
            self._lock = state._cleanup_tool_lock
            self._controls = state._cleanup_tool_controls
        self.runtime = ActionRuntime(state, backend=ToolSimulationBackend(self))
        with self._lock:
            # The harness and direct UI may share this store via distinct facade
            # instances. Their active work is not a crashed previous process.
            if not self._controls:
                self.runtime.recover()
                with state.lock:
                    rows = state.db.execute("SELECT payload FROM cleanup_tool_plans WHERE json_extract(payload,'$.status') IN ('executing','interrupted')").fetchall()
                for row in rows:
                    plan = json.loads(row[0])
                    result = self.runtime.result(plan['id'], plan['version'])
                    result.update(plan_id=plan['id'], source=plan['source'], simulation_only=True)
                    plan.update(status='interrupted', result=result)
                    self._save(plan)

    @staticmethod
    def _text(value, label, limit=120):
        if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
            raise AppError(label + '格式不正确。', 'invalid_tool_parameter')
        return value.strip()

    def list(self, query='', category='all'):
        if not isinstance(query, str) or len(query) > 160 or category not in ('all', *CATEGORIES):
            raise AppError('请选择有效的工具分类或搜索内容。', 'invalid_tool_query')
        needle = query.strip().casefold()
        items = []
        for row in _ROWS:
            if category != 'all' and row['category'] != category:
                continue
            if needle and needle not in ' '.join((row['id'], row['title'], row['description'], row['impact'])).casefold():
                continue
            items.append({key: copy.deepcopy(row[key]) for key in ('id', 'tool_id', 'title', 'category', 'description', 'impact', 'common', 'simulation_only', 'capabilities')})
        return dict(items=items, categories=[dict(id=k, title=v) for k, v in CATEGORIES.items()],
                    total=len(items), registry_version=REGISTRY_VERSION, simulation_only=True)

    def detail(self, tool_id):
        if not isinstance(tool_id, str) or tool_id not in REGISTRY:
            raise AppError('此专项工具不可用，请重新选择。', 'unknown_cleanup_tool', 404)
        result = copy.deepcopy(REGISTRY[tool_id])
        result.pop('read', None)
        result['settings'] = [dict(resolve_entry(entry['entry_id']),
                                   **({'browser': entry['browser']} if entry.get('browser') else {}))
                              for entry in result['settings']]
        return result

    def entrypoint(self, tool_id, index=0, parameters=None):
        reference = self.validate_reference(dict(tool_id=tool_id, parameters={} if parameters is None else parameters), allow_missing=True)
        entries = REGISTRY[reference['tool_id']]['settings']
        if type(index) is not int or not 0 <= index < len(entries):
            raise AppError('这个程序入口暂不可用。', 'entrypoint_unavailable')
        entry = entries[index]
        launch_parameters = {'volume': reference['parameters']['volume']} if entry['entry_id'] == 'disk_cleanup' and reference['parameters'].get('volume') else {}
        return dict(resolve_entry(entry['entry_id'], launch_parameters),
                    **({'browser': entry['browser']} if entry.get('browser') else {}))

    def validate_reference(self, ref, *, allow_missing=False):
        if not isinstance(ref, dict) or set(ref) - {'tool_id', 'parameters'} or 'tool_id' not in ref:
            raise AppError('专项工具引用格式不正确。', 'invalid_tool_reference')
        tool = self.detail(ref['tool_id'])
        params = ref.get('parameters', {})
        if not isinstance(params, dict) or set(params) - {f['name'] for f in tool['parameters']}:
            raise AppError('专项工具参数不受支持。', 'invalid_tool_parameter')
        normalized = {}
        for field in tool['parameters']:
            name = field['name']
            if name not in params and 'default' not in field:
                if field.get('required') and not allow_missing:
                    raise AppError('请填写' + field['label'] + '。', 'missing_tool_parameter')
                continue
            value = params.get(name, field.get('default'))
            if field['type'] == 'integer':
                if type(value) is not int or not field['minimum'] <= value <= field['maximum']:
                    raise AppError(field['label'] + '超出允许范围。', 'invalid_tool_parameter')
            else:
                value = self._text(value, field['label'], field['maxLength'])
                if field.get('format') == 'volume':
                    value = value.upper()
                    if not re.fullmatch('[A-Z]:', value):
                        raise AppError('请填写本地盘符，例如C:。', 'invalid_tool_parameter')
                if field.get('format') == 'path':
                    if not ntpath.isabs(value) or value.startswith(('\\\\', '\\??\\', '\\\\?\\')) or re.match(r'^[A-Za-z]:', value) is None:
                        raise AppError('请填写明确的本地绝对目录。', 'invalid_tool_parameter')
            if 'enum' in field and value not in field['enum']:
                raise AppError(field['label'] + '选项不受支持。', 'invalid_tool_parameter')
            normalized[name] = value
        if tool['id'] == 'pagefile' and normalized['mode'] == 'custom' and normalized['initial_mb'] > normalized['maximum_mb']:
            raise AppError('分页文件最大值不能小于初始值。', 'invalid_tool_parameter')
        return dict(tool_id=tool['id'], parameters=normalized)

    def inspect(self, tool_id, parameters=None):
        ref = self.validate_reference(dict(tool_id=tool_id, parameters={} if parameters is None else parameters), allow_missing=True)
        tool = self.detail(tool_id)
        missing = [field['label'] for field in tool['parameters'] if field.get('required') and field['name'] not in ref['parameters']]
        if missing:
            return dict(tool_id=tool_id, read_only=True, at=now(), parameters=ref['parameters'],
                evidence=dict(status='needs_input'), simulation_only=True,
                presentation=dict(kind='status', status='needs_input', complete=False,
                    summary='请先选择' + '、'.join(missing), metrics=[], items=[], warnings=[]))
        evidence = {}
        read = REGISTRY[tool_id]['read']
        if read == 'memory':
            evidence = system_configuration()
            presentation = memory_presentation(tool_id, evidence)
        elif read == 'volume' or tool_id == 'recycle_bin':
            evidence = volume_snapshot(ref['parameters']['volume'] + '\\')
            presentation = volume_presentation(tool_id, ref['parameters'], evidence)
        else:
            evidence, presentation = inspect_metadata(tool_id, ref['parameters'])
        if tool_id == 'windows_temp':
            measured, cache_presentation = inspect_metadata(tool_id, ref['parameters'])
            evidence['temporary_files'] = measured
            presentation['items'] = cache_presentation['items']
            presentation['metrics'] += cache_presentation['metrics']
            presentation['warnings'] = cache_presentation['warnings']
            presentation['complete'] = presentation['complete'] and cache_presentation['complete']
        return dict(tool_id=tool_id, read_only=True, at=now(), evidence=evidence, parameters=ref['parameters'],
                    impact=tool['impact'], recovery=tool['recovery'], simulation_only=True,
                    presentation=presentation,
                    note='检查不会修改系统；工具安装情况、清理量和可恢复性未核实的部分保持未知。')

    def _source(self, source):
        if source is None:
            return {}
        allowed = {'task_id', 'item_id', 'signature', 'advice_version', 'suggestion_id', 'return_context', 'object_ids', 'advice_id'}
        if not isinstance(source, dict) or set(source) - allowed:
            raise AppError('专项来源信息格式不正确。', 'invalid_tool_source')
        try:
            serialized = json.dumps(source, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError):
            raise AppError('专项来源信息格式不正确。', 'invalid_tool_source') from None
        if len(serialized) > 8192:
            raise AppError('专项来源信息过长。', 'invalid_tool_source')
        result = {}
        for key, value in source.items():
            if value is None:
                continue
            if key == 'return_context':
                context_fields = {'view', 'query', 'risk', 'origin', 'subtype', 'limit', 'scroll_y'}
                if not isinstance(value, dict) or set(value) - context_fields:
                    raise AppError('返回位置格式不正确。', 'invalid_tool_source')
                for field, entry in value.items():
                    if field in ('limit', 'scroll_y'):
                        if type(entry) not in (int, float) or not 0 <= entry <= (10000 if field == 'limit' else 100000000):
                            raise AppError('返回位置数值不正确。', 'invalid_tool_source')
                    elif not isinstance(entry, str) or len(entry) > 160 or any(ord(c) < 32 for c in entry):
                        raise AppError('返回位置文字不正确。', 'invalid_tool_source')
                # Navigation state is data only; no HTML, code, URI or commands are interpreted.
                result[key] = copy.deepcopy(value)
            elif key == 'object_ids':
                if not isinstance(value, list) or len(value) > 100:
                    raise AppError('来源对象过多。', 'invalid_tool_source')
                result[key] = [self._text(x, '对象引用', 160) for x in value]
            elif key == 'advice_version' and type(value) is int:
                if not 0 <= value <= 2**53 - 1:
                    raise AppError('建议版本不正确。', 'invalid_tool_source')
                result[key] = value
            else:
                result[key] = self._text(value, '来源引用', 200)
        return result

    @staticmethod
    def _frozen(plan):
        return {key: plan[key] for key in ('id', 'version', 'registry_version', 'entries', 'source')}

    def _save(self, plan):
        with self.state.lock, self.state.db:
            self.state.db.execute('INSERT OR REPLACE INTO cleanup_tool_plans VALUES(?,?,?)',
                (plan['id'], plan['created_at'], json.dumps(plan, ensure_ascii=False)))

    def _validate_entry(self, entry):
        reference = self.validate_reference(dict(tool_id=entry.get('tool_id'), parameters=entry.get('parameters')))
        expected = _digest(dict(reference, registry_version=REGISTRY_VERSION, tool_version=REGISTRY[reference['tool_id']]['version']))
        if entry.get('kind') != 'cleanup_tool' or entry.get('signature') != expected:
            raise AppError('专项工具或参数已有变化，请重新预览。', 'cleanup_tool_changed', 409)

    def prepare(self, tool_id, parameters=None, source=None):
        return self.prepare_many([dict(tool_id=tool_id, parameters={} if parameters is None else parameters)], source=source)

    def prepare_many(self, items, source=None):
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
            raise AppError('每批请选择1至40项专项操作。', 'invalid_tool_batch')
        source = self._source(source)
        entries, seen = [], set()
        for raw in items:
            ref = self.validate_reference(raw)
            signature = _digest(dict(ref, registry_version=REGISTRY_VERSION, tool_version=REGISTRY[ref['tool_id']]['version']))
            if signature in seen:
                continue
            seen.add(signature)
            tool = self.detail(ref['tool_id'])
            entries.append(dict(id='cleanup-tool-' + signature[:24], kind='cleanup_tool',
                name=tool['title'], title=tool['title'], tool_id=tool['id'], parameters=ref['parameters'],
                signature=signature, action='specialist_cleanup', impact=tool['impact'],
                recovery=tool['recovery'], requires_restart=tool['requires_restart']))
        # Different modes for one target are alternatives, never sequential bulk operations.
        identities = set()
        identity_fields = {
            'browser_cache': ('browser', 'profile'), 'browser_privacy': ('browser', 'profile', 'data_type'),
            'pagefile': ('volume',), 'system_restore': ('volume',),
            'known_folder_move': ('folder',), 'cache_location': ('program',),
            'software_uninstall': ('application',), 'old_drivers': ('published_name',),
            'optional_features': ('feature_name',), 'unity_cache': ('project_path',),
            'cargo_cache': ('project_path',), 'browser_models': ('browser',),
            'cloud_local_copy': ('provider', 'target_path'), 'virtual_disk': ('virtual_disk_path',),
        }
        for row in entries:
            identity = (row['tool_id'], *(row['parameters'].get(key) for key in identity_fields.get(row['tool_id'], ('volume',))))
            if identity in identities:
                raise AppError('同一工具的不同方案不能同时执行，请保留一个选项。', 'conflicting_tool_options')
            identities.add(identity)
        plan = dict(id='tool-plan-' + secrets.token_hex(12), version=1, registry_version=REGISTRY_VERSION,
            created_at=now(), status='awaiting_review', source=source, entries=entries,
            title=entries[0]['name'] if len(entries) == 1 else f'{len(entries)}项专项操作',
            tool_id=entries[0]['tool_id'] if len(entries) == 1 else None,
            parameters=entries[0]['parameters'] if len(entries) == 1 else {},
            impact='；'.join(dict.fromkeys(row['impact'] for row in entries)),
            simulation_only=True, actual_reclaimed_bytes=0)
        plan['frozen_hash'] = _digest(self._frozen(plan))
        self._save(plan)
        return copy.deepcopy(plan)

    def get_plan(self, plan_id):
        if not isinstance(plan_id, str):
            raise AppError('专项方案不存在。', 'cleanup_plan_missing', 404)
        with self.state.lock:
            row = self.state.db.execute('SELECT payload FROM cleanup_tool_plans WHERE id=?', (plan_id,)).fetchone()
        if not row:
            raise AppError('专项方案不存在。', 'cleanup_plan_missing', 404)
        plan = json.loads(row[0])
        if any(key not in plan for key in ('id', 'version', 'registry_version', 'entries', 'source')) or plan.get('frozen_hash') != _digest(self._frozen(plan)):
            raise AppError('专项方案内容有变化，请重新预览。', 'cleanup_plan_changed', 409)
        for entry in plan['entries']:
            self._validate_entry(entry)
        # The persisted plan stays frozen; its live control snapshot is a
        # read-only projection for progress and cancellation in the UI.
        with self._lock:
            control = self._controls.get(plan_id)
        if control:
            plan['execution'] = control.snapshot()
        return plan

    def execute(self, plan_id, version, *, cancel=None, progress=None):
        with self._lock:
            plan = self.get_plan(plan_id)
            if type(version) is not int or version != plan['version']:
                raise AppError('专项方案版本已变化，请重新预览。', 'cleanup_plan_changed', 409)
            if plan['status'] == 'executing':
                raise AppError('这一批专项操作正在进行。', 'cleanup_running', 409)
            if plan['status'] != 'awaiting_review':
                return copy.deepcopy(plan.get('result') or self.runtime.result(plan_id, version))
            plan['status'] = 'executing'
            self._save(plan)
            control = ExecutionControl(plan_id, version, plan['entries'])
            self._controls[plan_id] = control
        try:
            result = self.runtime.run(plan_id, version, plan['entries'], cancel=cancel, progress=progress,
                guard=self._validate_entry, control=control)
            result.update(plan_id=plan_id, source=plan['source'], simulation_only=True)
            plan.update(status='completed', result=result, completed_at=now(), execution=control.snapshot())
            self._save(plan)
            return copy.deepcopy(result)
        except BaseException:
            plan.update(status='interrupted', result=self.runtime.result(plan_id, version), interrupted_at=now())
            self._save(plan)
            raise
        finally:
            with self._lock:
                self._controls.pop(plan_id, None)

    def control(self, plan_id, action):
        with self._lock:
            control = self._controls.get(plan_id)
            if not control:
                raise AppError('没有正在进行的专项操作。', 'cleanup_not_running', 409)
            return control.request(action)

    def cancel_plan(self, plan_id, version):
        with self._lock:
            plan = self.get_plan(plan_id)
            if type(version) is not int or version != plan['version']:
                raise AppError('专项方案版本已变化。', 'cleanup_plan_changed', 409)
            if plan['status'] == 'awaiting_review':
                result = self.runtime.result(plan_id, version)
                result.update(status='cancelled', plan_id=plan_id, source=plan['source'], simulation_only=True)
                plan.update(status='cancelled', cancelled_at=now(), result=result)
                self._save(plan)
            elif plan['status'] == 'executing':
                self.control(plan_id, 'cancel')
            return plan

    def history(self):
        with self.state.lock:
            rows = self.state.db.execute('SELECT payload FROM cleanup_tool_plans ORDER BY created_at DESC LIMIT 100').fetchall()
        return [json.loads(row[0]) for row in rows]

    def undo(self, plan_id):
        """Only undo virtual receipts; this is never real configuration recovery."""
        with self._lock:
            plan = self.get_plan(plan_id)
            if plan['status'] not in ('completed', 'interrupted', 'undone'):
                raise AppError('专项操作尚未结束，不能撤销其记录。', 'cleanup_not_finished', 409)
            with self.state.lock, self.state.db:
                for entry in plan['entries']:
                    operation_id = hashlib.sha256(f'{plan_id}:{plan["version"]}:{entry["id"]}'.encode()).hexdigest()
                    receipt = self.runtime.backend.receipt(operation_id)
                    if receipt:
                        receipt['active'] = False
                        self.state.db.execute('UPDATE cleanup_tool_receipts SET payload=? WHERE operation_id=?',
                            (json.dumps(receipt, ensure_ascii=False), operation_id))
            result = self.runtime.result(plan_id, plan['version'])
            result.update(plan_id=plan_id, source=plan['source'], simulation_only=True)
            plan.update(status='undone', result=result)
            self._save(plan)
            return copy.deepcopy(plan)

    def get_origin(self, task_id=None, item_id=None):
        return [plan for plan in self.history() if (task_id is None or plan['source'].get('task_id') == task_id)
                and (item_id is None or plan['source'].get('item_id') == item_id or item_id in plan['source'].get('object_ids', []))]
