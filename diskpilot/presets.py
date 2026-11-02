"""Task intentions and useful starting locations, not automatic delete rules."""
from __future__ import annotations

import os
from pathlib import Path

from .simulation import AppError, within


PRESETS = {
    'system_cleanup': ('清理系统盘垃圾', '分析系统盘中值得清理的文件和目录，先查看提供的常见缓存和临时位置，再按需要调查较大目录。系统专项工具由程序提供独立入口；参考用户已执行、拒绝或暂缓的记录，避免重复建议，但打开工具不等于已清理。解释用途、原因和影响；个人资料由用户决定。'),
    'c_cleanup': ('清理系统盘垃圾', '分析系统盘中值得清理的内容，先查看提供的常见缓存和临时位置，再按需要调查较大目录。解释用途、原因和影响；个人资料由用户决定。'),
    'disk_cleanup': ('清理某个磁盘', '逐步调查所选磁盘中的无用或可整理内容，优先关注缓存、临时数据、旧安装包和重复副本。不能仅因时间旧或体积大就删除；说明尚未调查的范围，个人资料由用户决定。'),
    'downloads': ('整理下载文件夹', '整理下载内容，找出可能已不需要的旧安装包、重复下载和可归类的资料，解释用途。保留个人作品和仍需要的文件，最终去留由用户选择。'),
    'directory_cleanup': ('清理磁盘或目录', '调查所选磁盘或目录中的内容、用途和可整理项目。先概览目录分布，再按需要深入；程序目录按软件或组件整体理解，避免逐个解释细碎文件。说明未检查范围、处理原因和影响；个人资料由用户最后选择。'),
    'duplicates': ('查找重复文件', '在所选位置查找值得比较的重复文件，使用本地哈希取得证据，说明版本、用途和硬链接差别，再提出保留与清理建议，由用户决定。不要把同名或同大小当作内容一致。'),
    'background': ('优化开机与后台', '了解开机启动和后台程序，优先调查可按需运行的应用，解释用途和停用影响，提出少量建议。不要仅因内存高或第三方程序就认定无用，不要中断未保存工作。'),
    'installers': ('清理旧安装包', '在所选位置核对旧安装包与已安装软件，区分安装包、免安装程序和解压目录，解释保留离线安装包的取舍。个人下载最终由用户选择。'),
}


def system_root():
    drive = os.environ.get('SystemDrive', 'C:')
    return drive + '\\' if drive.endswith(':') else os.path.abspath(drive)


def known_locations(root):
    local = os.environ.get('LOCALAPPDATA', '')
    candidates = []
    if local and os.path.isabs(local):
        candidates += [(str(Path(local) / name), label) for name, label in (
            ('D3DSCache', 'Windows 图形缓存'), ('NVIDIA/DXCache', 'NVIDIA 图形缓存'),
            ('NVIDIA/GLCache', 'NVIDIA 图形缓存'), ('pip/Cache', 'Python 下载缓存'),
            ('npm-cache', 'Node.js 下载缓存'), ('Temp', '当前用户临时目录'),
            ('CrashDumps', '应用崩溃转储'))]
    for name in ('TEMP', 'TMP'):
        path = os.environ.get(name, '')
        if path and os.path.isabs(path):
            candidates.append((path, '当前用户临时目录'))
    candidates.append((str(Path(root) / 'Temp'), '磁盘临时目录'))
    seen, result = set(), []
    for path, label in candidates:
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized not in seen and within(path, root) and os.path.isdir(path):
            seen.add(normalized)
            result.append((path, label))
    return result[:12]


def resolve_preset(preset, path=None):
    if not isinstance(preset, str) or preset not in PRESETS:
        raise AppError('请选择支持的整理任务。', 'unknown_preset')
    title, goal = PRESETS[preset]
    if preset == 'background':
        return dict(title=title, goal=goal, kind='applications', paths=[], priority=[])
    if preset in ('c_cleanup', 'system_cleanup'):
        root = system_root()
    elif preset == 'downloads':
        root = path or str(Path.home() / 'Downloads')
    else:
        root = path
    if not isinstance(root, str) or not os.path.isabs(root):
        raise AppError('请选择要整理的磁盘或文件夹。')
    priority = known_locations(root) if preset in ('c_cleanup', 'system_cleanup', 'disk_cleanup', 'directory_cleanup') else []
    return dict(title=title, goal=goal, kind='filesystem', paths=[(root, title), *priority], priority=[p for p, _ in priority])


def system_tool_suggestions(system_drive=None):
    """Host-authored choices, usable without a model or permission to execute.

    The caller binds task/version/time. Registry definitions remain authoritative
    for capability, parameter validation, native entry points and simulation.
    """
    import ntpath
    from .cleanup_tools import REGISTRY

    root = system_root() if system_drive is None else system_drive
    if not isinstance(root, str):
        raise AppError('系统盘位置无效。', 'invalid_system_drive')
    drive, tail = ntpath.splitdrive(root)
    if (len(drive) != 2 or drive[1] != ':' or not drive[0].isascii() or not drive[0].isalpha()
            or tail not in ('', '\\', '/')):
        raise AppError('请选择系统盘根目录。', 'invalid_system_drive')
    drive = drive.upper()
    explanations = {
        'windows_temp': ('先查看Windows提供的临时文件分类，按类别决定是否清理。',
                         '部分分类可能包含下载文件和升级材料，需要保留的资料不能一起清掉。'),
        'component_store': ('使用Windows支持的组件维护入口，核对可清理的旧组件。',
                            '不能手动删除WinSxS目录；本项不包含ResetBase，也不删除当前系统组件。'),
        'recycle_bin': ('查看系统回收站占用，再决定是否清空；盘序专用已删除区独立保留。',
                        '清空系统回收站后，其中内容不能再从系统回收站还原；不会清理盘序专用保留记录。'),
    }
    result = []
    for tool_id, (explanation, impact) in explanations.items():
        tool = REGISTRY[tool_id]
        parameters = {'volume': drive} if any(field['name'] == 'volume' for field in tool['parameters']) else {}
        result.append(dict(id='host-system-' + tool_id, title=tool['title'], explanation=explanation,
            impact=impact, cleanup_rating=None, item_ids=[], origin='host',
            provenance=dict(source='local', provider=None, model=None, protocol=None),
            options=[dict(id='tool', label='查看清理方案', kind='tool', tool_id=tool_id, parameters=parameters),
                     dict(id='skip', label='暂不清理', kind='answer')]))
    return result
