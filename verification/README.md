# 本机验收

这里的 Python 脚本属于可复现的工程工具。数据库、截图、原始协议记录和产物报告由 `.gitignore` 排除，不随源码提交。

## 验收顺序

1. 在源码运行严格资源检查与前端语法检查。扫描续接、任务交接、协议适配和逐项动作分别用可丢弃夹具验证。
2. `python verification/run_local_acceptance.py` 检查只读系统观察、模拟结果、夹具内容与回环模型连接。
3. `python verification/serve_v050_fixture.py --data-dir verification/<新的测试目录>` 提供本地模型及文件夹具，用实际页面检查分析、保留反馈、审阅、模拟结果、重开和开发记录。输出地址为随机回环端口。创建该目录下的 `stop-fixture` 文件即可正常退出并核对夹具散列。
4. 关闭旧版窗口，先预览再执行 `python verification/finalize.py --archive-previous --apply`，保留上一版程序及其 `userdata`；运行 `./build.ps1`。
5. 用独立测试数据目录启动最终 EXE，实际检查原生窗口。保存截图与验收判定，核对 EXE 散列，不能用源码页面或构建成功替代。
6. 执行 `python verification/finalize.py --tests-run <实际测试数> --health-url <最终EXE的/api/health>`。最终包必须没有 `userdata`、测试设置或凭据。ZIP 完成后，才恢复用户原有数据到本机运行目录，并启动正常窗口。

报告中的 `passed` 只涵盖其明确记录的范围。回环协议测试不等于真实云服务可用；模拟成功不等于文件被删除或系统被修改。当前没有真实执行器。

## 数据边界

所有验收操作都使用本项目内的数据目录或脚本自行创建的临时夹具。禁止将用户真实资料作为删除、恢复、服务控制或卸载测试对象。原始协议可能包含路径和用户意图，只可保存在本机，开发记录默认关闭。
