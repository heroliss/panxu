# 清理工具候选与官方依据

核对日期：2026-11-02。仅研究官方文档及当前源码，没有执行下列命令、读取个人隐私记录、改变系统或安装软件。系统表与产品表存在交叉，行数不是已交付工具数量。

**A**：有官方命令/API，可考虑专用适配器；**M**：优先产品/系统界面；**E**：需要浏览器扩展及相应授权；**P**：持久策略配置。标记不代表当前盘序可执行、本机已验证或所有版本支持。入口、权限、参数、恢复和后置条件必须逐版本验证。

## Windows与系统配置

| ID | 候选能力 | 官方通路 | 需要保留的语义 |
| --- | --- | --- | --- |
| W01 | 临时文件、系统清理类别 | M/A：存储设置、[cleanmgr](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/cleanmgr) | 系统类别可能需要提权。`/sagerun`会枚举所有驱动器，不能靠`/d`严格限制其为单盘操作 |
| W02 | 存储感知规则 | M：`ms-settings:storagepolicies`、[存储感知](https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/manage-drive-space-with-storage-sense) | 清理现有数据和持续启用自动规则分别确认；停用规则不能恢复已清内容 |
| W03 | Windows.old与升级回退材料 | M：[释放空间](https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/free-up-drive-space-in-windows) | 与普通缓存分别列出；删除后会失去相应升级回退材料 |
| W04 | 回收站统计/清空 | A：[Clear-RecycleBin](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/clear-recyclebin) | 明确当前用户及盘符；清空是永久删除，不用删除整个`$Recycle.Bin`替代 |
| W05 | WinSxS组件存储 | A：[DISM组件清理](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/clean-up-the-winsxs-folder?view=windows-11) | 使用受支持清理机制，不能手删；`ResetBase`会失去卸载当前更新的能力，独立高级项 |
| W06 | 传递优化缓存 | M/A待验证：[Windows传递优化](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/delivery-optimization-in-windows) | 可重新下载；相关cmdlet文档存在说明占位，先探测模块及能力，不承诺通用自动化 |
| W07 | Microsoft Store缓存 | A：[wsreset官方故障步骤](https://support.microsoft.com/en-us/accounts-billing/microsoft-store-doesn-t-open) | 修复Store缓存，不等同重置所有应用数据；会打开Store，不伪装纯后台零副作用 |
| W08 | 下载、安装包及重复副本 | A：[IFileOperation标志](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags) | 用户选择具体对象；请求回收不等于一定可恢复，记录实际删除/回收结果及取消状态 |
| W09 | 文档/桌面/图片等已知目录迁移 | A/M：[Known Folders](https://learn.microsoft.com/en-us/windows/win32/shell/working-with-known-folders) | 查询重定向能力；每用户与公共目录权限不同，路径修改与内容搬运分别核验；OneDrive KFM单独处理 |
| W10 | 普通目录迁移 | A：[IFileOperation](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nn-shobjidl_core-ifileoperation) | 核对空间、ACL、引用及占用；不能作为任意已安装程序目录搬运器 |
| W11 | 云文件释放本地副本 | M：[OneDrive按需文件](https://support.microsoft.com/en-us/onedrive/save-disk-space-with-onedrive-files-on-demand-for-windows) | 联机状态变化不等于删除云内容；核对同步与离线需要，访问可能再次下载 |
| W12 | 正规软件卸载 | A/M：[WinGet卸载](https://learn.microsoft.com/en-us/windows/package-manager/winget/uninstall) | 精确匹配ID及已核实卸载入口；提权、重启和数据保留取决卸载器，安装回去不等于恢复配置 |
| W13 | 应用修复/重置 | M/A：[Reset-AppxPackage](https://learn.microsoft.com/en-us/powershell/module/appx/reset-appxpackage) | 修复与重置是不同操作；重置可能丢本机设置/登录，不标成清缓存 |
| W14 | 语言、OCR等能力包 | A/M：[Remove-WindowsCapability](https://learn.microsoft.com/en-us/powershell/module/dism/remove-windowscapability) | 系统级变更，按精确名称/依赖处理；可能重启，恢复可能需安装源 |
| W15 | Windows可选功能 | A/M：[Disable-WindowsOptionalFeature](https://learn.microsoft.com/en-us/powershell/module/dism/disable-windowsoptionalfeature) | 禁用与删除负载分开；NoRestart不是不需要重启，恢复源可能不可用 |
| W16 | 旧驱动包导出/删除 | A：[PnPUtil](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/pnputil-command-syntax) | 核对设备使用关系，保留当前及必要回退包；不能按日期批删，默认不force/uninstall |
| W17 | 分页文件配置和迁移 | A/M：[Win32_PageFileSetting](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-pagefilesetting) | 活动值与待启动配置分读；需要相应权限及重启后核验，不直接删除pagefile.sys |
| W18 | 崩溃转储设置/输出位置 | M优先：[内核/完整转储](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/generate-a-kernel-or-complete-crash-dump) | 输出文件位置和崩溃时分页/专用转储支撑分开；不承诺迁移Memory.dmp就满足全部需求 |
| W19 | 休眠与快速启动 | A/M：[powercfg](https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/powercfg-command-line-options) | full/reduced/off能力不同，reduced仅支撑快速启动；联动电源按钮/低电量动作，不设计为手动搬走hiberfil.sys |
| W20 | 系统保护、还原点及影子存储 | M/A高级：[系统保护](https://support.microsoft.com/en-us/windows/experience/backup-recovery/system-protection)、[VSS额度](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/vssadmin-resize-shadowstorage) | 官方入口systempropertiesprotection.exe；缩额可能丢快照。区分还原点与备份软件影子副本，不默认清全部 |
| W21 | 开机启动行为 | M优先：[启动应用](https://support.microsoft.com/en-us/windows/experience/startup-boot/configure-startup-applications-in-windows) | 禁用登录启动不是卸载或结束进程；现有启动盘点并不涵盖所有计划任务/驱动机制 |
| W22 | 后台服务停止/启动方式 | A：[Stop-Service](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/stop-service) | 先核对依赖和身份；停止与禁用启动分成两个动作，启动回来不能恢复全部中断业务 |
| W23 | 进程正常退出/结束 | A：[Stop-Process](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/stop-process) | 优先正常退出；绑定PID和创建时间，考虑未保存内容与账户权限 |
| W24 | CompactOS/文件压缩 | A高级：[compact](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/compact) | 压缩不是删除；可解压也需空间，耗时/性能因机器而异，不作为常用默认清理 |
| W25 | Edge隐私类别清理 | M优先：[Edge浏览数据](https://support.microsoft.com/en-us/edge/view-and-delete-browser-history-in-microsoft-edge) | 类型、时间段及同步范围分别选择，不删除整个Profile；与产品表交叉 |
| W26 | 剪贴板历史 | A/M：[ClearHistory](https://learn.microsoft.com/en-us/uwp/api/windows.applicationmodel.datatransfer.clipboard.clearhistory) | 不清固定项目，读取布尔结果；单次剪贴板、历史、固定项和云同步范围不能混称全部清除 |
| W27 | Windows搜索历史 | M：[搜索与隐私](https://support.microsoft.com/en-us/windows/privacy/windows-search-and-privacy) | 设备历史与云端历史分别操作；本机清理不能冒称账号数据已删除 |
| W28 | 诊断数据/活动历史 | M：[诊断数据](https://support.microsoft.com/en-us/windows/privacy/diagnostics-feedback-and-privacy-in-windows)、[活动历史](https://support.microsoft.com/en-us/windows/privacy/windows-activity-history-and-your-privacy) | 两套数据分开；删除已有数据不停止未来收集；入口受Windows版本影响 |
| W29 | 位置/摄像头/麦克风及应用后台权限 | M：[设置URI与版本要求](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings) | 权限变化会影响功能；不照搬过期的全局后台应用入口，按实际应用/版本定位 |
| W30 | 注册表定点修复 | A高级：[reg export](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/reg-export) | 仅有明确故障和精确键值时考虑；导出.reg不是完整系统备份，不设置通用“无效项一键清理” |
| W31 | 最近使用/快速访问/跳转列表 | M优先：[资源管理器](https://support.microsoft.com/en-us/windows/experience/fileexplorer/file-explorer-in-windows)、[设置URI](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings) | 显示开关、清除记录及固定项分别处理；官方资源管理器页部分隐私步骤属Win10分节，Win11各入口和清除范围仍须逐版本核验，不能声称一次清空所有应用MRU |
| W32 | 崩溃转储/WER及诊断材料 | M/来源专用：[本地转储](https://learn.microsoft.com/en-us/windows/win32/wer/collecting-user-mode-dumps)、[清理入口](https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/free-up-drive-space-in-windows) | 本地转储与发送给微软的报告可不同，先确认排障用途；保留必要证据，不为清理自动启用采集，也不把所有日志或事件记录当垃圾 |
| W33 | 文件历史记录旧版本 | A/M：[FhManagew.exe](https://learn.microsoft.com/en-us/windows/win32/devnotes/fhmanagew-exe) | 操作当前分配的目标，按年龄及保护范围/较新版本判断；age=0仍保留当前保护范围中各文件最新版本，不等于删除整套备份。清掉的历史版本无法由重新开启功能恢复 |

分页、转储与休眠应联动分析。分页需求取决峰值提交和转储要求，不套RAM固定倍数或所有机器C盘固定1–2GB；完整转储所需支撑约RAM+257MB，其他转储有不同条件。[微软分页大小说明](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/how-to-determine-the-appropriate-page-file-size-for-64-bit-versions-of-windows)

内存工具应解释工作集、提交和真正内存压力。移出工作集的页后续仍可能被访问并产生缺页，不将强制缩工作集包装成修复内存泄漏或永久优化。[微软工作集说明](https://learn.microsoft.com/en-us/windows/win32/memory/working-set)

软件盘点延续注册信息等方法；不能因看似只查询就任意使用Win32_Product，它会触发已安装包一致性检查及可能的修复。[微软软件盘点说明](https://learn.microsoft.com/en-us/powershell/scripting/samples/working-with-software-installations)

## 浏览器、开发环境与其他产品

| ID | 候选能力 | 官方通路 | 需要保留的语义 |
| --- | --- | --- | --- |
| P01 | Chrome网页缓存 | M/E：[清理类别](https://support.google.com/chrome/answer/2392709?co=GENIE.Platform%3DDesktop&hl=en)、[browsingData](https://developer.chrome.com/docs/extensions/reference/api/browsingData) | 桌面程序不能直接当扩展调用API；缓存与Cookie分开，重新下载不等于撤销 |
| P02 | Edge网页缓存 | M：[Edge说明](https://support.microsoft.com/en-us/edge/view-and-delete-browser-history-in-microsoft-edge) | 单独核对Edge版本/API/同步，不假设Chrome所有接口行为相同 |
| P03 | Firefox网页缓存 | M/E：[缓存](https://support.mozilla.org/en-US/kb/how-clear-firefox-cache)、[WebExtensions](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/browsingData) | 浏览器内部清理可运行中进行；外部删除Profile文件不是等价方案 |
| P04 | Cookie/会话/站点离线数据 | M/E：[Chrome数据类型](https://support.google.com/chrome/answer/2392709?co=GENIE.Platform%3DDesktop&hl=en)、[Firefox站点数据](https://support.mozilla.org/en-US/kb/clear-cookies-and-site-data-firefox) | 按站点及类型选择；会话可能退出，IndexedDB等可能有离线作品，不能都叫可重建缓存 |
| P05 | 浏览及下载记录 | M/E：[Chrome说明](https://support.google.com/chrome/answer/2392709?co=GENIE.Platform%3DDesktop&hl=en) | 下载历史不等于实际下载文件；本机/同步/账号活动范围分开 |
| P06 | 密码/通行密钥/自动填充 | M优先：[Chrome API](https://developer.chrome.com/docs/extensions/reference/api/browsingData)、[Firefox Sync](https://support.mozilla.org/en-US/kb/sync) | Chrome144起passwords/removePasswords已无效果，不能按旧示例返回成功；不默认导出明文密码备份 |
| P07 | Chrome/Edge本地基础模型 | P：[Chromium官方策略定义](https://raw.githubusercontent.com/chromium/chromium/main/components/policy/resources/templates/policy_definitions/GenerativeAI/GenAILocalFoundationalModelSettings.yaml)、[Edge定义](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/GenAILocalFoundationalModelSettings) | 当前GenAILocalFoundationalModelSettings=1禁止下载并删除已有基础模型，按产品/版本探测；非Profile策略，不等于禁止所有ML下载或关闭安全浏览 |
| P08 | Firefox翻译模型 | M/P：[翻译](https://support.mozilla.org/en-US/kb/website-translation) | 移除语言模型与停用功能分别操作，重新翻译可能再下载 |
| P09 | uv缓存 | A：[缓存](https://docs.astral.sh/uv/concepts/cache/)、[链接模式](https://docs.astral.sh/uv/reference/settings/) | 使用官方cache命令；symlink模式清缓存可能破坏环境；prune、定包清理与全清分开 |
| P10 | pip缓存 | A：[pip缓存](https://pip.pypa.io/en/stable/topics/caching/) | 绑定正确Python；remove针对wheel，purge还清HTTP；考虑离线安装需求 |
| P11 | npm缓存 | A：[npm-cache](https://docs.npmjs.com/cli/v11/commands/npm-cache/) | 优先verify，清理再考虑clean --force；与node_modules及源码分开，不当例行必需维护 |
| P12 | Conda缓存 | A：[conda clean](https://docs.conda.io/projects/conda/en/latest/commands/clean.html) | 可分类/dry-run；force-pkgs-dirs可能破坏软链接环境，不作默认组合 |
| P13 | NuGet缓存 | A：[dotnet nuget locals](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-nuget-locals) | HTTP/临时/全局包分别选，构建中占用按实报告，之后可能需要包还原 |
| P14 | Cargo项目构建产物 | A：[cargo clean](https://doc.rust-lang.org/cargo/commands/cargo-clean.html) | 固定manifest和target位置，先dry-run；构建产物非源码，也不等于可恢复副本 |
| P15 | Docker构建缓存 | A：[system df](https://docs.docker.com/reference/cli/docker/system/df/)、[prune](https://docs.docker.com/reference/cli/docker/system/prune/) | 核对context/daemon为本机及指定构建器；损失构建加速，再构建可能联网 |
| P16 | Docker容器/镜像/网络 | A：[对象清理](https://docs.docker.com/engine/manage-resources/pruning/) | 停止容器仍可能有未导出可写层，未使用镜像可能需要保留；不默认-a |
| P17 | Docker卷 | A独立高影响项：[pruning](https://docs.docker.com/engine/manage-resources/pruning/) | 未被引用不代表无用，卷可有数据库；不混入普通缓存自动批选 |
| P18 | WSL内部清理/VHD空间 | A/M：[WSL命令](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)、[磁盘](https://learn.microsoft.com/en-us/windows/wsl/disk-space)、[compact vdisk](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/compact-vdisk) | 识别发行版、真实VHD及运行状态；内部删文件与宿主缩小分开；unregister删除整发行版 |
| P19 | GPU着色器缓存 | M/专用适配：[NVIDIA](https://nvidia.custhelp.com/app/answers/detail/a_id/5735)、[AMD](https://www.amd.com/en/resources/support-articles/faqs/DH3-012.html) | NVIDIA当前故障流程涉及缓存开关、重启、清理及恢复；AMD有Reset Shader Cache。重编译可能卡顿，不长期默认禁用或安装DDU |
| P20 | Adobe媒体/磁盘缓存 | M：[Premiere](https://helpx.adobe.com/premiere/desktop/troubleshooting/media-issues/clear-media-cache-using-preferences.html)、[After Effects](https://helpx.adobe.com/after-effects/desktop/memory-storage-performance/memory-and-storage/memory-storage1.html) | unused/all分别选，部分缓存共享；保留工程、媒体、自动保存及指定模型，优先产品界面 |
| P21 | Steam下载缓存 | M：[Steam说明](https://help.steampowered.com/en/faqs/view/6AD7-820D-8BE5-E51F) | 会要求重新登录；不是卸载游戏或删存档，不承诺通用清理CLI |
| P22 | Hugging Face模型缓存 | A：[hf CLI](https://huggingface.co/docs/huggingface_hub/guides/cli) | 精确repo/revision与共享blob，支持dry-run；verify可能联系Hub。用户自建权重不自动当下载缓存 |
| P23 | Ollama模型及加载占用 | A：[Ollama CLI](https://docs.ollama.com/cli) | 识别本机服务端；stop释放运行资源，rm删除模型；自定义模型不保证可重新pull恢复 |
| P24 | OneDrive本地副本 | M：[Files On-Demand](https://support.microsoft.com/en-us/onedrive/save-disk-space-with-onedrive-files-on-demand-for-windows) | 先核同步及离线需要，删除文件可能同步删除云端；检查占位状态不应触发内容下载 |
| P25 | Google Drive/Dropbox离线副本 | M：[Google Drive](https://support.google.com/drive/answer/2375012?hl=en)、[Dropbox](https://help.dropbox.com/sync/make-files-online-only) | 流式缓存、镜像及未上传内容分别处理；不直接清同步数据库，在线化后再访问可能重下载 |

Unity等项目目录仍沿用已有项目指导：缓存、构建产物、源代码、素材和版本库分别识别，不能仅凭目录名或年代批删。以上列表不是对当前机器软件安装情况的调查。

## 适配时统一核验

1. 工具/产品及版本、当前账户/Profile/环境、作用域和运行状态明确；CLI真实可用与具有该能力分别判断。
2. 冻结具体文件/对象/配置与参数。对于prune等运行时动态再枚举命令，不能把先前预览当成执行时对象完全不变的证明。
3. 输出退出码、实际后置条件、部分失败、锁定与需重启；打开界面只记已打开。没有结果读取器时，人工完成仍待核实。
4. 本机、云端和未来收集规则分别记录；日志不保留隐私正文。可重新下载、可重新启用与可恢复被删内容分别标记。
5. 系统设置入口按官方[URI清单](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings)探测版本/SKU，无法深链则给准确路径。没有可靠入口时直接说明，不能伪造按钮。

研究结果将用于后续适配器和LLM指导，不等于已经支持上述全部操作。当前没有真实执行器，也没有开放任意控制台。
