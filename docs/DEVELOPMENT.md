# 开发说明

盘序是一个 Python 后端加原生 JS 界面的 Windows 桌面程序：pywebview 打开 WebView2 窗口，界面通过 `Bridge.call` 调用后端，没有本地 HTTP 服务。设计取舍见 [DESIGN.md](DESIGN.md)，工程约定见 [AGENTS.md](../AGENTS.md)。

## 从源码运行

```powershell
python -m pip install -r requirements.txt
python run.py              # --debug 打开 WebView 开发者工具
python run.py --sandbox    # 演练环境（默认在 %LOCALAPPDATA%\PanXu\sandbox，也可以指定文件夹）
```

项目根目录的 `启动盘序.cmd` 用 `pythonw` 从源码启动，不留控制台窗口；启动出错会弹窗说明，详细信息写在 `%LOCALAPPDATA%\PanXu\panxu.log`。

**演练环境**会生成一台「用了几年的电脑」：C 盘和 D 盘、各种缓存、下载的安装包、论文的好几个版本、代码工程、假的注册表、服务和正在运行的程序。所有删除、禁用、卸载都只发生在演练文件夹里；演练回收站可以还原，也可以重建整个环境。大文件是稀疏文件，几十 GB 的逻辑大小只占几 MB 磁盘（需要 NTFS）。第一次进入时会复制已配置的模型和 Key。新增访问系统状态的代码（路径、注册表、服务、进程、命令）必须能注入假实现，并在 `sandbox.py` 里提供模拟。

## 项目结构

```text
panxu/
  app.py          启动：单实例、管理员重启交接、pywebview 窗口、关闭前确认
  api.py          界面调用的全部操作（唯一入口 Bridge.call）
  jobs.py         后台任务、进度、取消
  winapi.py       Win32 调用（回收站及其 $I 记录、DPAPI、已知文件夹、版本信息……）
  paths.py        路径策略：环境变量、匿名化、受保护位置
  fsops.py        遍历和删除（不穿越链接），移到回收站并核对是否真的进了回收站
  junk.py         垃圾清理规则与执行
  scanner.py      空间扫描
  knowledge.py    内置的位置知识（决定哪些不必问 AI）
  duplicates.py   重复文件：位置、工程引用检查、每份副本的风险
  startup.py      开机启动项
  services.py     服务
  itemfacts.py    交给 AI 之前程序自己查的事实（属于哪个应用、是否正在运行）
  ai_client.py    模型接口（OpenAI 兼容 / Anthropic）
  ai_health.py    独立后台连接检测、配置失效与提醒冷却，不占用文件任务
  analyst.py      AI 分析流程、文件和重复文件复核、问 AI、缓存
  checkup.py      一键体检、清理方案、评分、执行
  windowing.py    本程序窗口的统一顶栏控制与 Windows 边框兼容
  insights.py     我的文件
  apps.py         已安装的应用、卸载、残留
  icons.py        程序图标
  sandbox.py      演练环境
  oplog.py        操作记录（开始、进度、结果三步写入，被打断也有记录）
  settings.py     设置与密钥
web/              界面（原生 JS，无构建步骤；启动时内联成一个页面）
web/dev/mock.js   浏览器预览用的假后端，不会打包
tools/            开发工具（见下；make_icon.py 重新生成 assets/panxu.ico，需要 Pillow）
assets/panxu.ico  程序图标（打包时嵌进 exe）
tests/            单元测试（只用临时目录里的夹具）
```

## 测试和检查

```powershell
python -W error::ResourceWarning -m unittest discover -s tests -q   # 后端
node tests/test_ui.cjs                                              # 界面的纯函数；改过的 JS 再跑 node --check
python tools/build_preview.py preview.html --page home              # 用假数据生成某个页面的预览（加 #sandbox 显示演练提示条）
python tools/ui_harness.py                                          # 浏览器里把真实界面接到演练环境的真实后端：http://127.0.0.1:8766/
python tools/ui_harness.py --native --stage-delay 2                  # 同一演练后端的原生窗口；阶段延时便于验证跨页进度
python tools/ui_harness.py --resume-demo                             # 模拟普通体检后提权交接与补查，不请求真实 UAC
python tools/ui_harness.py --ai-status-demo missing                  # 缺配置提示；error/ready 模拟失败/成功，不联网
python tools/sandbox_e2e.py                                         # 在临时演练环境里跑完整流程；--live 连接设置里的真实模型（只发送演练环境里的假文件名）
python tools/real_check.py                                          # 在真实电脑上只读地跑一遍体检，列出方案和可疑之处，不删除也不修改任何东西
```

改了规则、检测或自动勾选后，跑一次 `real_check.py`，报告里不能有「严重」。报告含有你自己的路径，以 `real-check-` 开头的文件不会被提交。

Windows 专有的行为（回收站、UAC、注册表、WebView2）只能在 Windows 上确认；打包成功、演练环境通过都不等于 Windows 验收通过，验收按 [ACCEPTANCE.md](ACCEPTANCE.md) 走。

## 打包和发布

```powershell
python -m pip install -r requirements.txt pyinstaller
.\build.ps1
```

先跑单元测试，再生成 `dist\PanXu\PanXu.exe`（文件夹版，带 `_internal`）和 `dist\PanXu-<版本>-win-x64.zip`。打包后运行 `python tools\package_smoke.py dist\PanXu-<版本>-win-x64.zip`：它把压缩包解压到临时目录、给每个文件加上「来自互联网」标记（浏览器下载的压缩包解压后就是这样），在临时演练环境里启动并确认窗口打开——从构建目录直接运行发现不了这类问题（2.2.0 因此带着无法启动的问题发布）。已有的 `dist\PanXu` 会被改名保留，不会被覆盖（0.x 曾把用户数据放在 exe 旁边）。版本号在 `panxu/__init__.py`；发布时更新 [CHANGELOG](../CHANGELOG.md)，在 `main` 上打 `v<版本>` 标签，把 zip 附到 GitHub Release。

## 维护规则

加清理规则、修正误报按 [RULES.md](RULES.md) 的步骤做（Claude Code 里是 `maintain-rules` 技能）。新规则的目标必须是公认可以重建的缓存或临时数据；不确定的位置放进 `knowledge.py` 作为说明，而不是规则。
