"""Built-in knowledge about well-known Windows locations.

The space view uses this to label folders without asking a model, and the AI
analyst uses it to decide where *not* to spend tokens:

* ``final``     — we already know what this is; the model is not consulted.
* ``container`` — a structural folder (C:\\Users, AppData\\Local, ...); the
                  analyst looks at its children instead of the folder itself.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from . import junk, paths, winapi


@dataclass(frozen=True)
class Entry:
    path: str          # %VAR% template; '*' matches one path component
    kind: str          # system | program | personal | appdata | cache | cloud | vdisk | game | dev | container
    label: str
    advice: str = ''
    final: bool = True
    container: bool = False
    rule: str = ''     # junk rule id that cleans this location


# a 演练环境 kept outside PanXu's data folder (paths.is_sandbox_root): recognised by its layout, not its path
SANDBOX = Entry('', 'dev', '盘序演练环境',
                '盘序的演练环境：一台模拟的电脑，里面的文件都是演练用的假文件，「我的文件」和「重复文件」不会列出它们。'
                '想重新开始可以在演练环境里「重建演练环境」；不再需要时可以把这个文件夹整个删除。')

KIND_LABELS = {
    'system': '系统', 'program': '程序', 'personal': '个人文件', 'appdata': '应用数据', 'cache': '可清理',
    'cloud': '云同步', 'vdisk': '虚拟磁盘', 'game': '游戏', 'dev': '开发', 'container': '',
}

_C = dict(final=False, container=True)

ENTRIES: list[Entry] = [
    # structural containers: look at children instead
    Entry('%SystemDrive%', 'container', '系统盘', **_C),
    Entry('%SystemDrive%\\Users', 'container', '用户目录', **_C),
    Entry('%USERPROFILE%', 'container', '你的用户文件夹', **_C),
    Entry('%USERPROFILE%\\AppData', 'container', '应用数据', **_C),
    Entry('%LOCALAPPDATA%', 'container', '本地应用数据', **_C),
    Entry('%APPDATA%', 'container', '漫游应用数据', **_C),
    Entry('%LOCALLOW%', 'container', '低权限应用数据', **_C),
    Entry('%ProgramData%', 'container', '程序共享数据', **_C),
    Entry('%LOCALAPPDATA%\\Packages', 'appdata', '应用商店应用的数据',
          '不要手动删除，否则应用（甚至开始菜单）可能无法打开。在「设置 > 应用 > 已安装的应用」里对应应用的「高级选项」中重置或卸载。'),

    # Windows itself
    Entry('%SystemRoot%', 'system', 'Windows 系统目录', '不要手动删除。可清理的部分已包含在「垃圾清理」中。'),
    Entry('%SystemRoot%\\WinSxS', 'system', '组件存储（WinSxS）',
          '其中大量文件是硬链接，实际占用比显示的小。用「垃圾清理 > 组件存储清理」安全缩减，切勿手动删除。'),
    Entry('%SystemRoot%\\Installer', 'system', '安装程序缓存', '已安装程序修复和卸载时需要，不要手动删除。'),
    Entry('%SystemRoot%\\System32\\DriverStore', 'system', '驱动程序库', '系统保存的驱动副本，不要手动删除。'),
    Entry('%SystemDrive%\\pagefile.sys', 'system', '虚拟内存页面文件',
          '内存不足时使用。可在「系统 > 高级系统设置 > 性能 > 虚拟内存」调整大小，不要删除。'),
    Entry('%SystemDrive%\\hiberfil.sys', 'system', '休眠文件',
          '用于休眠和快速启动。不需要这两项时，可用管理员命令 powercfg /h off 关闭，文件会自动消失。'),
    Entry('%SystemDrive%\\swapfile.sys', 'system', '应用交换文件', '系统管理，不要删除。'),
    Entry('%SystemDrive%\\System Volume Information', 'system', '系统还原点与卷影副本',
          '在「系统属性 > 系统保护 > 配置」里可以删除旧还原点或限制占用。'),
    Entry('%SystemDrive%\\$Recycle.Bin', 'system', '回收站', '可在「垃圾清理」中清空回收站。', rule='recycle_bin'),
    Entry('*:\\$Recycle.Bin', 'system', '回收站', '可在「垃圾清理」中清空回收站。', rule='recycle_bin'),
    Entry('*:\\System Volume Information', 'system', '系统还原点与卷影副本', '在「系统保护」中管理。'),
    Entry('%SystemDrive%\\Recovery', 'system', '系统恢复环境', '不要删除。'),
    Entry('%SystemDrive%\\Windows.old', 'cache', '旧版 Windows 安装', '可在「垃圾清理 > 系统深度清理」中删除。',
          rule='windows_old'),
    Entry('%SystemDrive%\\$WINDOWS.~BT', 'cache', '系统升级临时文件', '可在「垃圾清理 > 系统深度清理」中删除。',
          rule='upgrade_leftovers'),
    Entry('%SystemDrive%\\$WINDOWS.~WS', 'cache', '系统升级临时文件', '可在「垃圾清理 > 系统深度清理」中删除。',
          rule='upgrade_leftovers'),
    Entry('%SystemDrive%\\$WinREAgent', 'system', '恢复环境更新临时目录', '系统管理，不要手动删除。'),

    # Installed programs
    Entry('%ProgramFiles%', 'program', '已安装的程序', '不需要的程序请在「设置 > 应用 > 已安装的应用」中卸载。'),
    Entry('%ProgramFiles(x86)%', 'program', '已安装的程序（32 位）', '不需要的程序请在「设置 > 应用 > 已安装的应用」中卸载。'),
    Entry('%ProgramFiles%\\WindowsApps', 'program', '应用商店应用', '在「设置 > 应用」中卸载。'),
    Entry('%LOCALAPPDATA%\\Programs', 'program', '仅为你安装的程序', '在「设置 > 应用」中卸载。'),
    Entry('%LOCALAPPDATA%\\Microsoft\\WindowsApps', 'program', '应用执行别名', '系统管理。'),
    Entry('%ProgramData%\\Microsoft', 'system', 'Windows 与微软组件数据', '系统管理，不要手动删除。'),
    Entry('%ProgramData%\\Package Cache', 'program', '安装包缓存',
          'Visual Studio、.NET 等程序修复和卸载时需要，手动删除会导致无法卸载。'),
    Entry('%LOCALAPPDATA%\\Package Cache', 'program', '安装包缓存', '程序修复和卸载时需要，不要手动删除。'),
    Entry('%USERPROFILE%\\scoop', 'program', 'Scoop 安装的程序', '用 scoop cleanup * 和 scoop cache rm * 清理旧版本。'),

    # Personal folders: never sent to the model
    Entry('%USERPROFILE%\\Desktop', 'personal', '桌面', '个人文件。可在「大文件」「重复文件」中查找可删除的内容。'),
    Entry('%USERPROFILE%\\Documents', 'personal', '文档', '个人文件。可在「大文件」「重复文件」中查找可删除的内容。'),
    Entry('%USERPROFILE%\\Downloads', 'personal', '下载', '常见的旧安装包、压缩包可在「大文件」中按类型筛选。'),
    Entry('%USERPROFILE%\\Pictures', 'personal', '图片', '个人文件。'),
    Entry('%USERPROFILE%\\Videos', 'personal', '视频', '个人文件。'),
    Entry('%USERPROFILE%\\Music', 'personal', '音乐', '个人文件。'),
    # other accounts (and Public): their files, and even their names, are not ours to send anywhere.
    # Listed after %USERPROFILE% so that your own folder (same depth) still matches first.
    Entry('%SystemDrive%\\Users\\*', 'personal', '其他账户或公用的文件夹',
          '另一个 Windows 账户（或所有账户共用）的文件。盘序不分析、不发给 AI；需要清理时请登录那个账户。'),
    Entry('%USERPROFILE%\\OneDrive', 'cloud', 'OneDrive', '删除会同步到云端。可右键文件夹选「释放空间」，只保留在云端。'),
    Entry('%USERPROFILE%\\OneDrive - *', 'cloud', 'OneDrive（工作或学校）', '删除会同步到云端。可右键选「释放空间」。'),
    Entry('%USERPROFILE%\\Documents\\WeChat Files', 'personal', '微信聊天文件',
          '在微信「设置 > 存储空间」中清理。直接删除会导致聊天中的图片和文件无法打开。'),
    Entry('%USERPROFILE%\\Documents\\xwechat_files', 'personal', '微信聊天文件', '在微信「设置 > 存储空间」中清理。'),
    Entry('%USERPROFILE%\\Documents\\WXWork', 'personal', '企业微信聊天文件', '在企业微信「设置 > 文档/文件管理」中清理。'),
    Entry('%USERPROFILE%\\Documents\\Tencent Files', 'personal', 'QQ 聊天文件', '在 QQ 设置的「存储管理」中清理。'),
    Entry('*:\\WeChat Files', 'personal', '微信聊天文件', '在微信「设置 > 存储空间」中清理。直接删除会导致聊天中的图片和文件无法打开。'),
    Entry('*:\\xwechat_files', 'personal', '微信聊天文件', '在微信「设置 > 存储空间」中清理。'),
    Entry('*:\\WXWork', 'personal', '企业微信聊天文件', '在企业微信设置中清理。'),
    Entry('*:\\Tencent Files', 'personal', 'QQ 聊天文件', '在 QQ 设置的「存储管理」中清理。'),
    Entry('*:\\BaiduNetdiskDownload', 'personal', '百度网盘下载目录', '个人下载的文件。可在「大文件」或「我的文件」中按需整理。'),
    Entry('*:\\QuarkDownload', 'personal', '夸克网盘下载目录', '个人下载的文件。可在「大文件」或「我的文件」中按需整理。'),
    Entry('*:\\AliyunDriveDownload', 'personal', '阿里云盘下载目录', '个人下载的文件。可在「大文件」或「我的文件」中按需整理。'),
    Entry('*:\\阿里云盘', 'personal', '阿里云盘下载目录', '个人下载的文件。可在「大文件」或「我的文件」中按需整理。'),
    Entry('%APPDATA%\\Apple Computer\\MobileSync\\Backup', 'personal', 'iPhone / iPad 备份',
          'iTunes 创建的苹果设备整机备份，常占几十 GB。请在 iTunes 或 Apple Devices 的设备备份管理中删除旧备份，切勿直接删部分文件。'),
    Entry('%USERPROFILE%\\Apple\\MobileSync\\Backup', 'personal', 'iPhone / iPad 备份',
          'Apple Devices 创建的设备备份。请在 Apple Devices 应用里管理或删除旧备份。'),

    # Application data we can describe without a model
    Entry('%LOCALAPPDATA%\\Google\\Chrome\\User Data', 'appdata', 'Chrome 用户数据',
          '包含书签、密码、扩展和缓存。缓存可在「垃圾清理 > 浏览器缓存」中清理。'),
    Entry('%LOCALAPPDATA%\\Microsoft\\Edge\\User Data', 'appdata', 'Edge 用户数据',
          '包含书签、密码、扩展和缓存。缓存可在「垃圾清理 > 浏览器缓存」中清理。'),
    Entry('%LOCALAPPDATA%\\BraveSoftware\\Brave-Browser\\User Data', 'appdata', 'Brave 用户数据',
          '包含书签、密码、扩展和缓存。缓存可在「垃圾清理 > 浏览器缓存」中清理。'),
    Entry('%LOCALAPPDATA%\\Vivaldi\\User Data', 'appdata', 'Vivaldi 用户数据',
          '包含书签、密码、扩展和缓存。缓存可在「垃圾清理 > 浏览器缓存」中清理。'),
    Entry('%LOCALAPPDATA%\\Opera Software', 'appdata', 'Opera 浏览器缓存与数据',
          '缓存可在「垃圾清理 > 浏览器缓存」中清理。'),
    Entry('%APPDATA%\\Opera Software', 'appdata', 'Opera 浏览器配置与数据',
          '包含书签、密码、扩展和部分缓存。缓存可在「垃圾清理 > 浏览器缓存」中清理。'),
    Entry('%LOCALAPPDATA%\\Mozilla\\Firefox\\Profiles', 'appdata', 'Firefox 配置与数据',
          '包含书签、密码、扩展和缓存。缓存可在「垃圾清理 > 浏览器缓存」中清理。'),
    Entry('%APPDATA%\\Code', 'appdata', 'VS Code 配置与状态',
          '包含设置、工作区状态和缓存。其中的缓存和旧日志可在「垃圾清理 > 应用缓存」中清理。'),
    Entry('%APPDATA%\\Code - Insiders', 'appdata', 'VS Code Insiders 配置与状态',
          '包含设置、工作区状态和缓存。缓存可在「垃圾清理 > 应用缓存」中清理。'),
    Entry('%APPDATA%\\Cursor', 'appdata', 'Cursor 配置与状态',
          '包含设置、对话记录和缓存。其中的缓存和旧日志可在「垃圾清理 > 应用缓存」中清理。'),
    Entry('%APPDATA%\\Windsurf', 'appdata', 'Windsurf 配置与状态',
          '包含设置、对话记录和缓存。其中的缓存和旧日志可在「垃圾清理 > 应用缓存」中清理。'),
    Entry('%APPDATA%\\Trae', 'appdata', 'Trae 配置与状态',
          '包含设置、对话记录和缓存。其中的缓存和旧日志可在「垃圾清理 > 应用缓存」中清理。'),
    Entry('%APPDATA%\\obsidian', 'appdata', 'Obsidian 配置与界面数据',
          '包含仓库列表、插件全局设置和缓存。界面缓存可在「垃圾清理 > 应用缓存」中清理。'),
    Entry('%APPDATA%\\Notion', 'appdata', 'Notion 本地数据与缓存',
          '包含离线页面数据库与界面缓存。界面缓存可在「垃圾清理 > 应用缓存」中清理。'),
    Entry('%APPDATA%\\discord', 'appdata', 'Discord 应用数据',
          '包含登录状态、设置和缓存。网页与图片缓存可在「垃圾清理 > 应用缓存」中清理。'),
    Entry('%APPDATA%\\Slack', 'appdata', 'Slack 应用数据',
          '包含登录状态、设置和缓存。网页与图片缓存可在「垃圾清理 > 应用缓存」中清理。'),
    Entry('%APPDATA%\\LarkShell', 'appdata', '飞书应用与聊天数据',
          '请在飞书「设置 > 通用 > 存储管理」中清理缓存与聊天文件。'),
    Entry('%LOCALAPPDATA%\\Feishu', 'appdata', '飞书应用数据',
          '请在飞书「设置 > 通用 > 存储管理」中清理。'),
    Entry('*:\\Feishu', 'personal', '飞书文件目录', '请在飞书「设置 > 通用 > 存储管理」中清理。'),
    Entry('%APPDATA%\\DingTalk', 'appdata', '钉钉应用与聊天数据',
          '请在钉钉「设置 > 通用 > 存储空间管理」中清理。'),
    Entry('%LOCALAPPDATA%\\DingTalk', 'appdata', '钉钉应用数据',
          '请在钉钉「设置 > 通用 > 存储空间管理」中清理。'),
    Entry('%LOCALAPPDATA%\\JianyingPro', 'appdata', '剪映草稿与素材缓存',
          '包含视频剪辑草稿（Drafts）与代理缓存。请在剪映首页「全局设置」中清理缓存，切勿直接删除草稿目录。'),
    Entry('*:\\JianyingPro Drafts', 'personal', '剪映剪辑草稿',
          '你的视频剪辑工程草稿。请在剪映首页管理或删除不需要的草稿。'),
    Entry('%LOCALAPPDATA%\\CapCut', 'appdata', 'CapCut 草稿与素材缓存',
          '包含视频剪辑草稿与代理缓存。请在 CapCut 设置中清理缓存，切勿直接删除草稿目录。'),
    Entry('%LOCALAPPDATA%\\NetEase\\CloudMusic', 'appdata', '网易云音乐缓存与数据',
          '可在网易云音乐「设置 > 下载设置」中调整缓存上限或清理试听缓存。'),
    Entry('%APPDATA%\\Tencent\\QQMusic', 'appdata', 'QQ 音乐配置与数据',
          '可在 QQ 音乐「设置 > 下载与缓存」中清理缓存。'),
    Entry('%LOCALAPPDATA%\\Tencent\\QQMusic', 'appdata', 'QQ 音乐缓存与数据',
          '可在 QQ 音乐「设置 > 下载与缓存」中清理缓存。'),
    Entry('%APPDATA%\\kingsoft\\office6', 'appdata', 'WPS Office 配置与自动备份',
          '包含本地文档备份与模板缓存。请在 WPS「设置 > 备份中心」管理旧备份，切勿直接删除自动备份目录。'),
    Entry('%LOCALAPPDATA%\\kingsoft\\WPS Cloud Files', 'cloud', 'WPS 云文档缓存',
          'WPS 云同步文档的本地缓存。请在 WPS 云文档设置中清理本地缓存。'),
    Entry('%LOCALAPPDATA%\\Docker', 'vdisk', 'Docker Desktop 数据',
          '镜像和容器存放在虚拟磁盘里。先用 docker system prune 清理，再在 Docker 设置中压缩磁盘。'),
    Entry('%LOCALAPPDATA%\\Packages\\CanonicalGroupLimited.*', 'vdisk', 'WSL Linux 发行版',
          'Linux 文件存放在 ext4.vhdx 虚拟磁盘中。在 Linux 内清理后需压缩虚拟磁盘才会释放空间。'),
    Entry('%LOCALAPPDATA%\\wsl', 'vdisk', 'WSL 虚拟磁盘', '在 Linux 内清理后需压缩虚拟磁盘才会释放空间。'),
    Entry('%USERPROFILE%\\.android\\avd', 'vdisk', 'Android 模拟器镜像', '在 Android Studio 的 Device Manager 中删除不用的模拟器。'),
    Entry('%USERPROFILE%\\VirtualBox VMs', 'vdisk', 'VirtualBox 虚拟机镜像',
          '请在 VirtualBox 中删除不用的虚拟机或压缩虚拟磁盘。'),
    Entry('%USERPROFILE%\\Documents\\Virtual Machines', 'vdisk', 'VMware 虚拟机镜像',
          '请在 VMware 中删除不用的虚拟机或使用「清理磁盘」收缩空间。'),
    Entry('*:\\Virtual Machines', 'vdisk', '虚拟机磁盘镜像', '请在虚拟机软件中删除不用的虚拟机或收缩虚拟磁盘。'),
    Entry('%USERPROFILE%\\.nuget\\packages', 'dev', 'NuGet 全局包', '项目还原时需要。可用 dotnet nuget locals all --clear 清理。'),
    Entry('%LOCALAPPDATA%\\pnpm\\store', 'dev', 'pnpm 全局包存储',
          '与各工程的 node_modules 共用硬链接，实际多占的空间通常远小于统计值。请用 pnpm store prune 清理未引用的包。'),
    Entry('*:\\.pnpm-store', 'dev', 'pnpm 全局包存储',
          '与各工程的 node_modules 共用硬链接。请用 pnpm store prune 清理未引用的包。'),
    Entry('%USERPROFILE%\\.ollama\\models', 'dev', 'Ollama 本地模型文件', '大模型权重文件，可用 ollama list 查看并用 ollama rm 删除。'),
    Entry('%USERPROFILE%\\.lmstudio\\models', 'dev', 'LM Studio 本地模型',
          '大模型权重文件，请在 LM Studio 的 My Models 页面查看或删除。'),
    Entry('*:\\LMStudio\\models', 'dev', 'LM Studio 本地模型',
          '大模型权重文件，请在 LM Studio 的 My Models 页面查看或删除。'),
    Entry('%USERPROFILE%\\.cache\\huggingface', 'dev', 'Hugging Face 模型缓存', '下载的模型和数据集缓存，确认不再使用后可按目录清理。'),
    Entry('%USERPROFILE%\\.conda', 'dev', 'Conda 环境与包缓存',
          '压缩包归档可在「垃圾清理 > 开发缓存」中清理；旧虚拟环境可用 conda env remove 删除。'),
    Entry('%LOCALAPPDATA%\\pypoetry\\Cache', 'dev', 'Poetry 缓存与虚拟环境',
          '下载缓存在「垃圾清理 > 开发缓存」中可清理；virtualenvs 子目录存放项目的 Python 虚拟环境。', final=False),
    Entry('%LOCALAPPDATA%\\JetBrains', 'dev', 'JetBrains IDE 缓存与索引',
          '各版本 IDE 的索引与缓存目录；旧日志可在「垃圾清理」删除，已卸载旧版本的目录可按需移入回收站。', final=False),
    Entry('%LOCALAPPDATA%\\Pub\\Cache', 'dev', 'Flutter / Dart 包缓存',
          '工程依赖缓存，可用 dart pub cache clean 清理；清理后打开工程需重新执行 flutter pub get。'),
    Entry('%LOCALAPPDATA%\\UnrealEngine', 'dev', 'Unreal Engine 引擎缓存',
          '虚幻引擎版本数据与派生缓存目录；DerivedDataCache 可在「垃圾清理 > 开发工具缓存」中清理。', final=False),
    Entry('%USERPROFILE%\\.rustup', 'dev', 'Rust 工具链（rustup）', '可用 rustup toolchain list 查看并用 rustup toolchain uninstall 卸载旧版本。'),
    Entry('%LOCALAPPDATA%\\Android\\Sdk', 'dev', 'Android SDK', '在 Android Studio > SDK Manager 中卸载旧版系统镜像和构建工具。'),
    Entry('%USERPROFILE%\\.vscode\\extensions', 'dev', 'VS Code 扩展', '在 VS Code 中卸载不用的扩展。'),
    Entry('%USERPROFILE%\\.cursor\\extensions', 'dev', 'Cursor 扩展', '在 Cursor 中卸载不用的扩展。'),
    Entry('%USERPROFILE%\\.cache', 'dev', '命令行工具缓存', '通常可清理，具体看子目录对应的工具。', final=False),
    Entry('*:\\SteamLibrary', 'game', 'Steam 游戏库', '在 Steam 中卸载不玩的游戏。'),
    Entry('*:\\Epic Games', 'game', 'Epic 游戏库', '在 Epic Games 启动器中卸载不玩的游戏。'),
    Entry('%ProgramFiles%\\Epic Games', 'game', 'Epic 游戏', '在 Epic Games 启动器中卸载不玩的游戏。'),
    Entry('*:\\XboxGames', 'game', 'Xbox / 微软商店游戏', '在 Xbox 应用或「设置 > 应用」中卸载不玩的游戏。'),
    # a Temp folder at the top of a drive: by convention only for temporary files -- but people park things
    # there too, so this is advice, not a cleanup rule
    Entry('*:\\Temp', 'cache', '临时文件夹',
          '名字叫 Temp 的文件夹按惯例只放临时文件，程序或你自己用完就不再需要。看一眼里面没有要留的，就可以删除；'
          '正在被程序使用的文件会删不掉，跳过即可。'),
    Entry('*:\\Tmp', 'cache', '临时文件夹', '和 Temp 一样，按惯例只放临时文件。看一眼里面没有要留的，就可以删除。'),
    Entry('%ProgramFiles(x86)%\\Steam\\steamapps', 'game', 'Steam 游戏', '在 Steam 中卸载不玩的游戏。'),
]


def _split(path: str) -> list[str]:
    return [p for p in paths.key(path).split('\\') if p]


def _part_matches(part: str, pattern: str) -> bool:
    if '*' not in pattern:
        return part == pattern
    # only '*' is a wildcard; brackets/question marks in real names are literal
    escaped = pattern.replace('[', '[[]').replace('?', '[?]')
    return fnmatch.fnmatchcase(part, escaped)


class Knowledge:
    """Matches paths against built-in entries plus junk-rule targets."""

    def __init__(self, env: dict | None = None):
        self.env = env or paths.environment()
        compiled = []
        for entry in ENTRIES:
            expanded = paths.expand(entry.path, self.env) if '%' in entry.path else entry.path
            if expanded:
                compiled.append((_split(expanded), entry))
            if entry.path.startswith('*:\\'):
                # the 演练环境's C:\ and D:\ are folders: "the top of any drive" means the top of those too
                for root in paths.simulated_drives():
                    compiled.append((_split(root) + _split(entry.path)[1:], entry))
        for folder, label in (('desktop', '桌面'), ('documents', '文档'), ('downloads', '下载'),
                              ('pictures', '图片'), ('videos', '视频'), ('music', '音乐')):
            try:
                real = winapi.known_folder(folder)
            except OSError:
                continue
            if real:
                compiled.append((_split(real), Entry(real, 'personal', label, '个人文件。')))
        for expanded, rule in junk.known_rule_paths(self.env, include_finders=False):
            compiled.append((_split(expanded), Entry(expanded, 'cache', '可清理：' + rule.name,
                                                     rule.description, rule=rule.id)))
        # most specific first
        compiled.sort(key=lambda item: len(item[0]), reverse=True)
        self._entries = compiled
        self._finders_loaded = False
        self._finder_bases = [self.env[k] for k in ('APPDATA', 'LOCALAPPDATA') if self.env.get(k)]

    def _ensure_finders(self, path: str) -> None:
        if self._finders_loaded or not any(paths.is_within(path, b) for b in self._finder_bases):
            return
        self._finders_loaded = True
        extra = []
        for rule in junk.RULES:
            if rule.finder:
                for _, root, _ in junk.rule_entries(rule, self.env):
                    extra.append((_split(root), Entry(root, 'cache', '可清理：' + rule.name,
                                                      rule.description, rule=rule.id)))
        if extra:
            merged = self._entries + extra
            merged.sort(key=lambda item: len(item[0]), reverse=True)
            self._entries = merged

    def match(self, path: str) -> tuple[Entry | None, bool]:
        """(entry, exact) — the most specific entry at or above ``path``."""
        self._ensure_finders(path)
        parts = _split(path)
        for pattern, entry in self._entries:
            if len(pattern) > len(parts):
                continue
            if all(_part_matches(parts[i], pattern[i]) for i in range(len(pattern))):
                return entry, len(pattern) == len(parts)
        return None, False

    def annotate(self, path: str) -> dict | None:
        """Label for the space view, or None when nothing is known."""
        if paths.is_sandbox_root(path):
            return {'kind': SANDBOX.kind, 'kind_label': KIND_LABELS[SANDBOX.kind], 'label': SANDBOX.label,
                    'advice': SANDBOX.advice, 'rule': '', 'inherited': False}
        entry, exact = self.match(path)
        if entry is None or entry.kind == 'container':
            return None
        if not exact and not entry.final:
            return None
        return {'kind': entry.kind, 'kind_label': KIND_LABELS.get(entry.kind, ''), 'label': entry.label,
                'advice': entry.advice, 'rule': entry.rule, 'inherited': not exact}

    def ai_role(self, path: str) -> str:
        """'skip' (known), 'descend' (structural folder) or 'ask' (unknown) for the AI analyst."""
        entry, exact = self.match(path)
        if entry is None and paths.is_sandbox_root(path):
            return 'skip'  # 盘序's own 演练环境: nothing in it to ask the model about
        if entry is None:
            return 'ask'
        if entry.container:
            return 'descend' if exact else 'ask'
        if entry.final:
            return 'skip'
        return 'descend' if exact else 'ask'
