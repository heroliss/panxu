# v0.8 指导与证据覆盖审查

核对日期：2026-11-02。本文说明当前实现的 17 类指导和 22 项任务工具，不代替最终构建验收。全部动作仍为模拟；真实删除、系统调优、进程/服务控制和卸载未接入。

## 四种信息不可互换

| 信息 | 来源和保存方式 | 能说明什么 | 不能推断什么 |
|---|---|---|---|
| 经验指导 | 固定白名单 `list_skills` / `read_skill`；1.1.0 版、来源日期、SHA-256；事实类型 `skill_guidance` | 用途、调查方向、条件与取舍 | 当前文件无用、当前配置已核实或新增执行权限 |
| 本机观察 | 已授权位置和固定只读系统接口；时间、范围、来源与覆盖；类型 `tool_observation` | 当时实际取得的元数据、配置或统计 | 永久有效、依赖完整或执行成功 |
| 用户意愿 | 目标、勾选、保留、结构化问题回答和直接操作来源 | 本次偏好与明确选择 | 回答偏好即批准删除、历史一次同意涵盖后来所有同名文件 |
| 处理结果 | 精确版本的用户批准、同一模拟运行时逐项核验 | 工具内部记录的成功、失败和撤销状态 | 实际释放空间、真实文件恢复或系统性能提升 |

指南按需读取，不把 17 份正文注入每个请求。注册表版本由元数据生成；本轮为 `knowledge-v2-ac719b6e27bc2470`，后续更改时以运行时返回值为准。游标绑定目录查询或正文内容版本，不能跨指南复用。

## 17 类指导的判断重点

| 指导 ID | 重点 | 官方资料 |
|---|---|---|
| `application-caches` | 下载/构建缓存与安装环境分开；重建和离线成本；Adobe 模型不等于程序或作品 | [Astral uv](https://docs.astral.sh/uv/concepts/cache/)、[pip](https://pip.pypa.io/en/stable/topics/caching/)、[npm](https://docs.npmjs.com/cli/v11/commands/npm-cache/)、[Adobe 神经滤镜](https://helpx.adobe.com/photoshop/desktop/effects-filters/neural-filters/neural-filters-faq.html) |
| `temporary-files` | 前缀、旧日期或大体积只是线索；占用、未完成任务和旧账号另查 | [微软空间管理](https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/free-up-drive-space-in-windows) |
| `downloads-installers` | 安装包、便携程序、解压目录和资料分别判断，个人选择保留 | [微软卸载说明](https://support.microsoft.com/en-us/windows/apps/uninstall-or-remove-apps-and-programs-in-windows) |
| `backups-upgrades` | 临时回滚备份、镜像、驱动导出和 OEM 包不同；不设统一删除天数 | [微软升级回退材料](https://support.microsoft.com/en-us/windows/deployment/install-upgrade/delete-your-previous-version-of-windows)、[Driver Store](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/driver-store)、[ASUS 官方来源](https://www.asus.com/support/download-center/) |
| `windows-components` | WinSxS、Installer 和传递优化走受管入口，不手动清系统目录 | [微软 WinSxS](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/clean-up-the-winsxs-folder?view=windows-11)、[Installer 缓存](https://learn.microsoft.com/en-us/troubleshoot/windows-client/application-management/missing-windows-installer-cache)、[传递优化](https://support.microsoft.com/en-us/windows/privacy/windows-update-delivery-optimization-and-privacy) |
| `hibernation-startup` | 会话恢复、快速启动和电源策略取舍，不统一建议关闭 | [微软休眠](https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/disable-and-re-enable-hibernation)、[电源状态](https://learn.microsoft.com/en-us/windows/win32/power/system-power-states) |
| `pagefile-memory` | 活动量、分配、配置、提交峰值和转储分开，不给固定通用大小 | [微软分页大小评估](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/how-to-determine-the-appropriate-page-file-size-for-64-bit-versions-of-windows) |
| `uninstall-residue` | 正规卸载优先；剩余目录不等于垃圾，旧账号不等于全部资料无用 | [微软卸载说明](https://support.microsoft.com/en-us/windows/apps/uninstall-or-remove-apps-and-programs-in-windows) |
| `data-folder-migration` | 普通复制、已知文件夹改址和同步迁移分别处理 | [微软已知文件夹 API](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shsetknownfolderpath)、[OneDrive 迁移](https://support.microsoft.com/en-us/onedrive/change-the-location-of-your-onedrive-folder) |
| `storage-sense-recycle` | 当前保留策略需核对，统计不保证回收/恢复，回收站不是独立备份 | [微软存储感知](https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/manage-drive-space-with-storage-sense)、[回收站统计 API](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shqueryrecyclebinw) |
| `cloud-placeholders` | 本地释放副本与同步删除不同；避免为查重触发占位文件下载 | [微软文件按需](https://support.microsoft.com/en-us/onedrive/save-disk-space-with-onedrive-files-on-demand-for-windows) |
| `virtual-disks-containers` | 虚拟盘包含环境/数据库，内部空闲与宿主占用不同，卷不是普通缓存 | [微软 WSL 空间](https://learn.microsoft.com/en-us/windows/wsl/disk-space)、[WSL 命令语义](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)、[Docker 清理范围](https://docs.docker.com/engine/manage-resources/pruning/) |
| `games-development` | 源码、Git 历史、素材、存档和可重建缓存分开，解释重新导入成本 | [Unity 项目目录](https://docs.unity.com/en-us/engine/6000.3/manual/get-started/project-configuration/default-directories)、[Valve 下载缓存](https://help.steampowered.com/en/faqs/view/6AD7-820D-8BE5-E51F) |
| `restore-recovery` | 系统还原、升级回退、WinRE、OEM 分区与个人备份不同，不统一关闭保护 | [微软系统保护](https://support.microsoft.com/en-us/windows/experience/backup-recovery/system-protection) |
| `disk-health` | 历史异常与当前健康分开；固件公告匹配型号/版本；不默认压力测试或重写冷数据 | [微软健康警告](https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/what-to-do-about-a-critical-warning-for-a-storage-device)、[Sandisk 特定固件问题](https://support-en.sandisk.com/app/answers/detailweb/a_id/51469/~/internal-ssd-critical-firmware-update-available-for-solving-bsod-on-windows-11)、[Dell 数据保持](https://www.dell.com/support/kbdoc/en-us/000198930/ssd-data-retention-considerations-when-powering-off-systems-for-a-prolonged-duration) |
| `startup-background` | 注册不等于启用，高工作集不等于无用，按需/停止/禁用/卸载不同 | [微软启动应用](https://support.microsoft.com/en-us/windows/experience/startup-boot/configure-startup-applications-in-windows)、[性能排查](https://support.microsoft.com/en-us/windows/experience/performance-optimization/tips-to-improve-pc-performance-in-windows) |
| `browser-privacy` | 缓存、Cookie/离线资料、历史和密码分开；同步影响；模型策略与安全浏览分开核对 | [Google 浏览数据](https://support.google.com/chrome/answer/2392709?co=GENIE.Platform%3DDesktop&hl=en)、[基础模型策略](https://chromeenterprise.google/policies/#GenAILocalFoundationalModelSettings)、[安全浏览等级](https://support.google.com/chrome/answer/9890866?hl=en) |

这些是项目撰写的条件式指导，不复制来源网页正文，也不把厂商特定产品的说明推广到所有设备。未知用途应继续收集证据或说明缺口，不因匹配名称就给批量删除建议。

## 两项新增只读证据工具

| 工具与参数 | 实际来源/输出 | 范围与预算 | 尚不能证明 |
|---|---|---|---|
| `collect_system_configuration {section?:"memory_power"}` | `GetPerformanceInfo`、`EnumPageFilesW`、`GetPwrCapabilities`；7 个固定 HKLM 配置值 | 机器内存/电源配置；最多 16 个活动分页项，3 次核心快照 API，另有固定注册表查询；结果上限 64 KiB；不读正文、不写配置 | 长期负载、最佳大小、转储可成功写出、完整休眠策略、磁盘健康；缺失临时分页标志保持未知 |
| `inspect_volume {scope_id}` | `GetDriveTypeW`、`GetVolumePathNameW`、`GetDiskFreeSpaceExW`、`SHQueryRecycleBinW` | 既有范围所在一个本地卷，最多 4 次核心 API；跳过网络/不支持介质，不枚举回收站内容 | 删除会进回收站、策略或配额已开启、可恢复、物理盘数量、性能和健康 |

结果包含 `collected_at`、`completed_at`、`source`、`scope`、`budget`、`status`、`partial`、`warnings`、`actions: []`。零值只表示确实取得的零；失败、缺值和未知格式不伪装成零或关闭。单次原生调用不可中途打断；有界调用次数并不承诺硬性墙钟超时。

默认隐藏外发路径时，结构化分页配置保留 `volume`、有限系统文件名、`initial_mb`、`maximum_mb` 和 `mode`；自定义文件名归为 `custom_system_file`。活动文件完整路径仍经统一隐私处理。原始事实和开发记录留在本机私有数据中，不应提交。

文件单选范围的父目录不开放遍历。`volume_scopes` 只提供前 8 个统计引用；更多引用可用 `list_scopes(purpose=volume)` 分页取得。默认 `list_scopes` 仍只列可展开范围，取得统计引用不会改变 `roots`、`known_ids`、`target_ids` 或 `action_directories`。

## 22 项工具与仍然缺少的能力

现有工具按用途为：能力/指导发现 3 项；系统证据 2 项；询问用户 1 项；范围、历史与交接 3 项；目录调查与扫描续接 5 项；对象/应用/重复/缓存调查 5 项；逐项建议、方案和结论 3 项。没有 shell、任意命令、任意注册表/文件正文读取或模型执行工具。

尚未实现的只读事实包括 SMART/温度/固件/事件日志与硬盘物理映射、存储感知当前策略、VSS/还原点/回退期限/WinRE、云同步/占位状态、WSL/Docker 内部对象、Git/项目正文与使用频率、文件占用锁、完整软件依赖及启动实际启用状态、电源按钮/合盖/严重低电量动作。指南会解释如何继续核对，不能借此宣称这些能力已存在。

## 可复用验收

`verification/check_system_tools.py` 仅接受回环应用与明确的本机验收模型配置，在可丢弃临时范围中建立自定义只读任务。它核对两种持久事实的来源/时间/未知/部分覆盖、工具定义与引用范围、路径隐藏后数值保留、无计划/无动作/无历史或库存改变。它会临时打开本机开发采集并恢复原开关，不清旧记录，不执行清理。

脚本不连接真实服务商、不设置密钥、不自动改模型配置；普通网络模型配置会被拒绝。报告须存入忽略的私有验证目录，不保存完整路径、个人文件名或模型对话。源码和最终原生构建应分别运行；程序构建成功不等于此检查通过。
