# 盘序工程约定

远程仓库：`https://github.com/heroliss/panxu`（私有），维护者 Liss。

## 继续开发前

- 先读 `README.md` 和 `docs/DESIGN.md`。1.0 是完全重写，0.x 的设计只保留在 git 历史中，不要重新引入任务编排、模型主导的工具循环或本地 HTTP 服务。
- 面向普通用户：先给结论，原因和技术细节按需展开；按钮文字说明会发生什么。
- 个人文件、来源不明的文件只能由用户选择后处理，默认进回收站。模型的建议不能扩大处理范围，也不能绕过 `paths.ProtectionPolicy`。

## 删除与系统修改

- 新增垃圾清理规则时，目标必须是公认可以重建的缓存或临时数据，写清说明和影响；`tests/test_cleanup.py` 的目录检查必须通过。不确定的位置放进 `knowledge.py` 作为说明，而不是规则。加规则和修误报按 `docs/RULES.md` 的步骤做（Claude Code 里是 `maintain-rules` 技能）。
- 任何删除都经过 `fsops`（不穿越链接）；用户和 AI 选择的路径必须先经过 `ProtectionPolicy.reason()`。
- 修改注册表或服务时记录原值（`oplog` 的 `revert` 字段），保证能撤销。
- 测试只使用临时目录里的夹具，不读写开发者自己的真实数据或系统设置。

## 验证

- 后端：`python -W error::ResourceWarning -m unittest discover -s tests -q`
- 前端：`node tests/test_ui.cjs`，以及对每个改过的 JS 文件运行 `node --check`
- 界面改动：`python tools/build_preview.py preview.html --page <页面>` 生成假数据预览并实际查看；需要真实后端时用 `python tools/ui_harness.py`（演练环境，浏览器打开 http://127.0.0.1:8766/）。
- 整体流程：`python tools/sandbox_e2e.py`（脚本模型，不联网）；需要验证真实模型时加 `--live`，只会发送演练环境里的假文件名。
- 真实电脑：`python tools/real_check.py`（只读，不删除不修改），改了规则、检测或自动勾选后跑一次，报告里不能有「严重」。
- 演练环境：`python run.py --sandbox` 打开真窗口，所有删除和系统修改只发生在演练目录里。新增访问系统状态的代码（路径、注册表、服务、进程、命令）必须能注入假实现，并在 `sandbox.py` 里提供模拟。
- Windows 专有行为（回收站、UAC、注册表、WebView2）只能在 Windows 上确认。不要把「打包成功」「Linux 测试通过」或「演练环境通过」说成 Windows 验收通过。

## 提交

- 在功能分支上按功能提交（例如 `claude/…`、`codex/…`），保持提交可审阅。
- 不提交 API Key、设置文件、扫描结果、操作记录、个人文件、截图和构建输出（`build/`、`dist/`）。
- 仓库可见性和许可证由维护者决定。
