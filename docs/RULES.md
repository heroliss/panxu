# 维护规则：加清理规则、修误报

盘序的判断大多来自几张「规则表」。这份说明写给以后来改规则的人（包括 Claude Code、Codex 等），保证每次都按同样的步骤做、不漏测试。约束见 [AGENTS.md](../AGENTS.md)，设计见 [DESIGN.md](DESIGN.md)。

## 什么时候需要改

- 体检或垃圾清理**漏掉**了明显的缓存（某个软件的 Cache 占了几 GB，却没人处理）。
- 体检**误报**：把不该删的东西列出来、勾上，或者某一类突然出现几百项。
- 系统大版本更新、换电脑之后，用 `tools/real_check.py` 检查发现异常。

报告问题时最好给出：路径、大概多大、你知道它是什么（或贴上那一行「问 AI」的回答），以及你觉得应该怎么处理。

## 改哪里

| 想做的事 | 位置 | 测试 |
|---|---|---|
| 新增一类可以清理的缓存 | `panxu/junk.py` 的 `RULES` | `tests/test_cleanup.py`（目录检查自动覆盖新规则） |
| 只给某个位置加说明（不确定能不能删） | `panxu/knowledge.py` 的 `ENTRIES` | 视情况在 `tests/test_core.py` 加断言 |
| 「我的文件」漏报或误报 | `panxu/insights.py`（版本标记、`managed_dir`、各类发现） | `tests/test_insights.py` |
| 自动勾选太松或太紧 | `panxu/checkup.py`（各来源的分数和 `eligible`、`_folder_guards`、`NEVER_AUTO_RULES`） | `tests/test_checkup.py` |
| 某个程序文件夹被误认成卸载残留 | `panxu/apps.py`（`match_folder`、`_GENERIC`）或 `insights.ALIASES` | `tests/test_apps.py` |

## 先判断：清理规则，还是只加说明？

只有**公认可以重建**的东西才能做成清理规则：缓存、临时文件、日志、下载后的安装包残留。删掉以后程序会自己重新生成或重新下载，用户不会丢任何东西。

以下情况**不要**做成规则，放进 `knowledge.py` 写说明（告诉用户是什么、在哪里处理）：

- 里面可能有用户数据：聊天记录、存档、配置、模板、许可证、虚拟机或数据库。
- 需要程序自己的功能来清理：微信「存储空间」、Docker `prune`、WSL 压缩磁盘。
- 不确定的位置。宁可只说明，也不要猜。

## 步骤

1. **确认事实。** 看清目录里是什么：文件类型、大小、修改时间，程序开着时能不能删，删了以后会怎样。能查到官方说明最好；不确定就按「只加说明」处理。
2. **写规则或说明。**
   ```python
   Rule('foo_cache', 'Foo 缓存', 'app',                       # id、显示名、分组（system/browser/app/dev/advanced）
        'Foo 的图片和网页缓存，删除后会重新生成。不影响设置和登录状态。',   # 说明：是什么、删了会怎样
        (Target('%LOCALAPPDATA%\\Foo\\Cache'),                 # 只能用 %VAR% 开头的路径，不能是基础目录本身
         Target('%APPDATA%\\Foo\\logs', pattern='*.log', min_age_hours=7 * 24)),
        processes=('foo.exe',),                                # 程序开着时整项跳过
        default=False,                                         # 有代价（重新下载、变慢）就默认不勾
        note='')                                               # 不可撤销或有明显影响时写在这里
   ```
   - 目标尽量具体，用 `pattern`、`min_age_hours`、`skip_dirs` 收窄范围；需要管理员权限的目标加 `admin=True`。
   - `checkup.rule_assessment` 按用途、重建成本和近期活动评分，不直接用 `default` 决定勾选；`eligible` 且达到门槛才自动勾。可能含用户自己的东西、移除恢复机会或不能合理预估影响时，加进 `checkup.NEVER_AUTO_RULES`（任何门槛下都不自动勾）。
3. **在演练电脑里放样例。** 在 `panxu/sandbox.py` 的 `_layout()` 里加几个对应的假文件：要清理的，以及必须保留的（比如旁边的配置文件），让以后的测试和演示都能覆盖到。
4. **补测试并跑全部检查。**
   ```powershell
   python -W error::ResourceWarning -m unittest discover -s tests -q
   node tests/test_ui.cjs
   python tools/sandbox_e2e.py
   ```
   `test_cleanup.CatalogueTest` 会自动检查新规则的目标没有越界。修误报时，**先写一个能重现误报的测试**（用临时目录里的夹具，不要读真实数据），再改代码。
5. **在真实电脑上只读检查。**
   ```powershell
   python tools/real_check.py --compare real-check-base.json   # 第一次用 --save real-check-base.json
   ```
   确认新规则只找到了该找的东西，「值得注意」里没有新问题，「和上次相比」的变化都能解释。这一步只读，不会删除任何东西。
6. **提交并开 PR。** 在功能分支上提交（例如 `claude/rule-foo-cache`），PR 里写清：加了或改了什么、依据是什么（来源、观察到的目录内容）、测过什么（第 4、5 步的结果）。由维护者合并。

## 修误报

流程和上面一样，第 2 步换成收紧判断：

- 识别条件太宽（比如把编号文件当成版本），就收紧模式，并加反例测试。
- 进了不该进的文件夹，就扩充 `insights.managed_dir` 的名单。
- 自动勾选太松，就在 `checkup._folder_guards` 里加守卫，或者调低那一类的分数（守卫和门槛都会写进评分依据，用户能看到为什么没勾）。不要靠提高默认门槛来修一类误报。

原则：让它**漏掉**，也不要让它**删错**。

## 真实电脑检查（`tools/real_check.py`）

在真实电脑上把一键体检完整跑一遍，**只看不删**：不删除、不移动、不修改任何设置，也不碰盘序自己的设置和记录。输出三组方案的摘要，加一段「值得注意」：

- **严重**（违反了盘序的承诺，退出码 2）：个人文件被自动勾选；自动勾选了应用数据以外的文件夹；方案里出现其他账户的文件（用户勾选了「其他账户的个人文件」、并且这些文件标明了账户且没有被勾选时除外）；程序管理的文件夹里的文件被当成个人文件。
- **提醒**（退出码 1）：某一类超过 100 项、「多个版本」超过 30 组、单项超过 20 GB、方案里出现受保护的位置、规则扫描出错、AI 出错。

常用参数：`--ai`（同时让设置里的模型复核，会发送匿名化后的文件夹和文件名），`--threshold 8`（换一个勾选门槛看看，默认 7），`--save real-check-base.json` / `--compare real-check-base.json`（和上次比较），`--out real-check-report.txt`，`--full-paths`（不替换用户名）。报告和基线里有你的文件路径；以 `real-check-` 开头的文件已在 .gitignore 里，不会被提交。

建议在 Windows 大版本更新后、换电脑后、装了一批新软件后各跑一次。

## 2026-11-06：新增缓存与评分依据

新增规则只扩展到明确的产品目录，不把任意名为 Cache 的文件夹变成清理目标：

| 规则 | 位置与依据 | 保留内容 |
|---|---|---|
| Chrome Beta / Dev / Canary | [Chromium 用户目录文档](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md)列出了 Windows 的 `Chrome Beta`、`Chrome Dev`、`Chrome SxS` 用户目录；复用现有 Chrome 的缓存子目录白名单 | 书签、Cookies、密码、历史、IndexedDB、Local Storage、Service Worker |
| VS Code Insiders | 独立的 `Code - Insiders` 用户目录（[微软仓库记录](https://github.com/microsoft/vscode/issues/112846)），只取 Cache / Code Cache / GPUCache | User、History、工作区资料、设置和扩展 |
| Obsidian 界面缓存 | [Obsidian 数据存放说明](https://obsidian.md/help/Files%2Band%2Bfolders/How%2BObsidian%2Bstores%2Bdata)确认 `%APPDATA%\Obsidian`；只取 Electron/Chromium 的三个渲染缓存子目录 | 笔记库、配置、IndexedDB 和元数据索引 |

渲染缓存名称依据 [Electron 的 app.getPath 文档](https://www.electronjs.org/docs/latest/api/app#appgetpathname)。这些规则要求先关闭对应程序；重新运行会重建缓存。沙箱包含对应缓存和必须保留的相邻资料。

规则评分由 `checkup.rule_assessment` 集中生成：符合期限的临时数据基础 95 分，浏览器/界面缓存 85，下载缓存 70，重建成本较高的着色器/媒体/构建缓存 60。最近 7 天新增/修改的占用超过一半减 10；至少八成超过 90 天未变加 5。不可还原的操作仍单独确认，Maven 本地仓库可能有用户自产包，降到 30 且永不自动勾选。文件大小只影响排序和释放量，不提高可丢弃的把握。

新实现保留 `eligible + 分数门槛` 的单一自动勾选规则。扫描和清理都遵守用户排除目录；整目录建议不能再覆盖已由规则计入的位置。老版本目录含规则日志时只继续建议未覆盖的子目录，不删除父目录来绕过日志保留期限。

## 2026-11-06：软件残留及系统缓存补充

- 新版 Teams：微软的[缓存维护说明](https://learn.microsoft.com/en-us/troubleshoot/microsoftteams/teams-administration/clear-teams-cache)确认新版独立包的 LocalCache 路径。本规则进一步仅取 EBWebView/Default 下 Cache、Code Cache、GPUCache，保留 Cookies、Local Storage 和其他应用资料；进程 ms-teams.exe 存在时不执行。目录子项白名单依据 WebView/Chromium 的缓存用途，不能扩大为整个 LocalCache 或应用重置。
- NVIDIA：[官方着色器缓存说明](https://nvidia.custhelp.com/app/answers/detail/a_id/5735)列出 Local/NVIDIA Corporation/NV_Cache，补入现有着色器缓存规则。重新编译可能使首次游戏运行变慢，继续按较高重建成本评分。
- DISM：[官方日志说明](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/deployment-image-servicing-and-management--dism--best-practices)区分当前 dism.log 与轮换的 .bak。仅匹配 dism.log.bak、保留 7 天并检查维护进程；诊断历史不可重建，列为手动确认且永不自动勾选。不会清理当前日志、其他备份或系统事件日志。
- 失效登记仅供核实，不把全注册表里陌生、空或过时的键批量当成垃圾。软件的修复、卸载元数据仍受保护；Windows 维护组件、Installer、DriverStore 等继续通过系统工具或说明入口处理。

## 2026-11-11：按结构识别的应用界面缓存

2026-11-06 的原则是「不把任意名为 Cache 的文件夹变成清理目标」，这条仍然成立。但逐个产品写规则跟不上：在维护者的电脑上只读排查，`%APPDATA%` 和 `%LOCALAPPDATA%` 里有 37 个程序各带一套 Chromium 缓存（哔哩哔哩、Cursor、Unity Hub、Steam、有道、Claude、各种 WebView2 小工具……），合计 2.4 GB，此前只有 VS Code、Slack 等几个有规则，其余靠 AI 当成普通文件夹建议、整个移进回收站。

新规则 `app_web_cache`（`Rule.finder='chromium'`，`junk.find_chromium_caches`）不看名字，要求两个条件同时成立：

1. **上一级是 Chromium 的用户资料目录**：里面有 `Local State` 或 `Preferences`，或者同时有 `Network`、`Local Storage`、`Session Storage` 中的两个。
2. **文件夹本身是缓存的固定格式**：
   - `Cache\Cache_Data`（网络缓存，里面是 block-file 的 `index` + `data_N`，或 simple cache 的 `index` / `index-dir`）；旧布局的 `Cache` 只取 `index`、`data_?`、`f_??????`。
   - `Code Cache\js`、`wasm`、`webui_js`（各有 `index` 或 `index-dir`）。
   - `GPUCache`、`DawnCache`、`DawnGraphiteCache`、`DawnWebGPUCache`、`GrShaderCache`、`ShaderCache`、`GraphiteDawnCache`：有 `index` + `data_0` + `data_1`，只取 `index`、`data_?`、`f_??????`，不递归。

依据：[Electron 的 app.getPath 文档](https://www.electronjs.org/docs/latest/api/app#appgetpathname)（sessionData 下的缓存目录）和 Chromium `net/disk_cache` 的两种磁盘格式。同一目录下的 Cookies、Local Storage、IndexedDB、Service Worker、扩展和程序自己的文件都不在目标里；名字叫 Cache 但不满足上面两条的文件夹（例如某个程序把工程数据放在 `Cache` 里）不会被发现。

范围：只在当前用户的 `%APPDATA%`、`%LOCALAPPDATA%` 里找，最深 4 层，不进入 `Packages`、`Programs`、`Temp`、`Microsoft\Windows` 等受保护或另有规则的位置；找到的位置必须在应用数据里至少两层深、通过和固定规则相同的运行时检查，并且不与任何固定路径规则重叠（浏览器、VS Code 等仍由各自的规则处理，不会重复计数）。

**正在运行的程序整个跳过**（`JunkEngine.running_apps`）。这类规则没有固定的进程名单可等，所以清理时逐个程序判断，两个信号任一成立即算在运行：Windows 的重启管理器说有程序打开了缓存的索引文件（`index` / `data_0`，Chromium 运行期间一直开着）；或者有进程的名字和这个程序的文件夹名一致。只问索引文件是实测的结果：把一个程序的全部缓存文件都拿去问，在真实电脑上用了六分钟（每个被打开的文件都会拖慢回答），只问索引文件是十秒左右，认出的程序相同。

评分 80（界面缓存，可重建），与其他规则一样再按近期活动调整；达到门槛会自动勾选。演练环境里有两个 Electron 程序、一个 WebView2 程序和一个只是名字叫 Cache 的反例。

同时新增三条开发缓存规则（都默认不勾）：`pnpm`（`%LOCALAPPDATA%\pnpm-cache`，元数据缓存，不含 store）、`browser_downloads`（Puppeteer 的 `%USERPROFILE%\.cache\puppeteer` 和 Playwright 的 `%LOCALAPPDATA%\ms-playwright`，可重新下载，55 分）、`cpptools_ipch`（VS Code C/C++ 扩展的预编译头缓存）。

排查时看到但**没有**做成规则的：`.cache\codex-runtimes`（2 GB，不确定能否随时重建）、`.cache\huggingface`（模型，重新下载成本高）、各程序的 `logs`（新版 Outlook、微信等，格式和保留需求各不相同）、浏览器的 `Service Worker\CacheStorage`（离线数据）、`.vscode\extensions`、`%ProgramData%\Package Cache`（卸载和修复需要）。
