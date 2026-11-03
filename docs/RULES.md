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
| 自动勾选太松或太紧 | `panxu/checkup.py`（`PlanBuilder`、`_folder_guards`、`BOLD_EXCLUDED_RULES`） | `tests/test_checkup.py` |
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
   - 默认不勾的规则在「积极」模式下会被自动勾选；删除后不能撤销，或者可能含用户自己的东西时，加进 `checkup.BOLD_EXCLUDED_RULES`。
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
- 自动勾选太松，就在 `checkup._folder_guards` 里加守卫（守卫会自动写进评分依据，用户能看到为什么没勾）。

原则：让它**漏掉**，也不要让它**删错**。

## 真实电脑检查（`tools/real_check.py`）

在真实电脑上把一键体检完整跑一遍，**只看不删**：不删除、不移动、不修改任何设置，也不碰盘序自己的设置和记录。输出三组方案的摘要，加一段「值得注意」：

- **严重**（违反了盘序的承诺，退出码 2）：个人文件被自动勾选；自动勾选了应用数据以外的文件夹；方案里出现其他账户的文件；程序管理的文件夹里的文件被当成个人文件。
- **提醒**（退出码 1）：某一类超过 100 项、「多个版本」超过 30 组、单项超过 20 GB、方案里出现受保护的位置、规则扫描出错、AI 出错。

常用参数：`--ai`（同时让设置里的模型复核，会发送匿名化后的文件夹和文件名），`--save real-check-base.json` / `--compare real-check-base.json`（和上次比较），`--out real-check-report.txt`，`--full-paths`（不替换用户名）。报告和基线里有你的文件路径；以 `real-check-` 开头的文件已在 .gitignore 里，不会被提交。

建议在 Windows 大版本更新后、换电脑后、装了一批新软件后各跑一次。
