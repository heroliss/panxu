# dsh-disk-manager 源码调研

调研日期：2026-11-02。只读检查公开仓库和固定提交，未 clone、安装、导入或运行其代码，也未让其访问本机用户文件。本文件记录源码事实和对盘序的设计启发，不代表对该插件进行过运行验收。

- 仓库：[wangzhanchao883/dsh-disk-manager](https://github.com/wangzhanchao883/dsh-disk-manager)。
- 固定提交：[`66c0bc366429e0147d955285ce555fa92547e5db`](https://github.com/wangzhanchao883/dsh-disk-manager/commit/66c0bc366429e0147d955285ce555fa92547e5db)，提交时间 2026-10-02，包版本 0.1.3。
- 许可证：[MIT，版权 wangzhanchao883，2026](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/LICENSE)。本次没有复制实现代码；以后若引入其代码或实质片段，需保留相应版权和许可声明，不能顺便替盘序选择整个项目的许可证。

## 定位和架构：它是宿主插件，不是另一套完整磁盘智能体

插件依赖 DSH 提供 tools、llm、commands、settings、Web 服务器与客户端会话系统。包中核心是少量 JavaScript 模块：扫描、运行进程线索、规则分类、一次 LLM 兜底分类、安全门与执行；前端是宿主设置页中的 React 组件，没有独立桌面程序或独立模型连接系统。[package.json](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/package.json)、[宿主服务与工具注册](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L1-L14)、[前端入口](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/client.js#L1-L16)。

其用户流程大致为：宿主按钮或 `/disk_scan` → 扫描配置根目录 → 静态规则/用户确认缓存/LLM 单次分类 → 按类别输出文本报告 → 宿主对话调用 preview、execute 等工具。LLM 兜底本身没有按需展开、循环取证和修订计划；它每次接收一个目录的名称、完整路径、大小和最多 50 个一级子目录名，返回一个分类 JSON。若宿主智能体另行多次调用工具，其外层循环来自 DSH，不能归功于该插件。[分类调用](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/classify.mjs#L88-L143)、[扫描后逐条分类](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L252-L263)。

它实际注册六个模型工具：disk_drives、disk_scan、disk_preview、disk_execute、disk_classify_confirm、disk_undo；另有宿主直接调用的 `/disk_scan` 命令和读取盘符的 HTTP 路由。不是 MCP 服务器，也没有自己的工具动态发现协议。[注册源码](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L109-L247)。

## 实现与文案要分开看

| 文案或表面能力 | 固定提交中的实际实现 | 对盘序的含义 |
|---|---|---|
| 扫描整 C 盘 | 遍历配置中的若干根目录的一级子目录；默认根写死 Administrator 的 AppData、用户目录和 Program Files，Windows 不在范围内；根中的普通文件不生成条目 | 首页意图可叫清理 C 盘，但结果必须显示实际范围与缺口，不能把默认路径当真实用户位置 |
| 规则、缓存和 LLM 分类 | 高置信规则优先；其次按目录名及名称特征查分类缓存；未知对象调用宿主 LLM 单次分类，等待人工确认 | 可以借鉴特征摘要与经验来源；不宜把它的三级分类直接替换我们的 LLM 调查主线 |
| 正在使用/冷废弃 | 扫描后快照进程名，再用名称匹配和短白名单避免部分废弃误判；所谓 lastAccess 主要是部分目录项 mtime | 进程快照是有用线索，“没匹配到进程”和旧 mtime 都不证明不用了 |
| dry-run 后执行 | 有预览函数；执行工具的描述写着必须先预览，但接口没有预览记录、方案 ID、版本或确认令牌的强制绑定 | 文字指令不足以成为执行授权，仍须保留我们的版本化计划与独立确认 |
| undo/可回滚 | 成功后可写 JSON 日志；disk_undo 只把日志列出来，没有执行恢复的分支 | 操作日志、可再生成和真实恢复必须分开命名 |
| 官方改址 | 返回 npm/pnpm/pip/Docker 等指引，函数明确说明未自动修改；没有验证设置已改变 | 指引结果不能显示为已完成迁移 |
| 前端 | 设置、目标盘、扫描按钮、等待状态、文本报告；没有独立文件表格、建议选择、逐项恢复 UI | 适合作为工具接入示例，不是可直接替换我们产品界面的现成组件 |

证据：[默认根和本地配置](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/config.mjs#L10-L36)、[扫描输出与 mtime 线索](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/scanner.mjs#L44-L138)、[进程线索](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/active.mjs#L22-L69)、[冷废弃分组](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L275-L293)、[改址指引](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/executor.mjs#L78-L93)、[undo 只读工具](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L215-L225)。

## 最值得采纳的三个机制

### 1. 将“用途/数据角色”和“合适动作”对应，而不是都叫垃圾

分类覆盖可再生缓存、软件官方改址、迁移线索、可能不再使用的软件、配置记忆与个人文件。它至少在产品概念上解释了同样占用很大、适合的操作却不同。[分类元信息](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/riskmap.mjs#L301-L307)。

盘序可独立实现为：本机事实与经验 → LLM 解释对象角色 → 提出一个或多个受支持动作及影响。分类标签用来帮助解释和检索经验，不能成为“只要 A/C 就放行”的执行能力令牌。一次建议可以是保留、继续调查、使用官方设置、正规卸载或清理具体子项；不要求所有对象落进一个强制删除分组。

### 2. 用目录特征摘要减少模型输入，同时积累带来源的经验

它没有把目录内每份文件塞给模型，而是取目录大小、位置和有限子目录名；用户确认后保存分类经验。进程名也只在一次扫描末尾快照一次，避免逐目录重复查询。[特征提示](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/classify.mjs#L88-L105)、[确认缓存](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/classify.mjs#L196-L209)。

盘序可采用“目录概况卡”：所属应用、空间/文件类型分布、主要子目录、保留和未知例外、观察覆盖与时间；先看整体，再由 LLM 调工具深入。确认过的偏好与用途知识可复用，但需绑定路径/实体、所属软件、证据版本、用户偏好和知识版本，不能照搬其名称缓存。活动证据只用于减少误判，不升级为“用户一定需要/不需要”的事实。

### 3. 给人和模型的入口复用同一个可靠操作服务，并区分请求是否受理

它让设置页通过宿主命令直接运行扫描，模型工具也调用同一个 runScan；前端明确区分未受理、执行失败、没有文本结果，以及已得到报告。保存设置若宿主返回 false，则撤回乐观值并显示失败，而不是假报保存成功。[命令和工具复用](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L141-L153)、[命令入口](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L227-L263)、[设置回执](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/client.js#L182-L203)、[长任务回执](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/client.js#L295-L344)。

盘序应让首页、单行分析、分析勾选、分析全集和 LLM 工具共用任务/观察服务，返回结构化 job/task ID。开发模式显示模型请求与工具结果；普通界面只显示当前任务状态。采用明确的 accepted/running/completed/failed/cancelled/unverified 状态，不因请求发出、超时或工具名字存在就宣称完成。我们已有独立任务 ID，不必采用它的“猜最近更新会话”逻辑或纯文本报告协议。

## 必须避免的具体实现问题

### A. 类别由调用者传入，预览没有与执行绑定

disk_execute 直接解析调用者提供的 JSON 路径、category 和 action，再交给 canExecute。canExecute 主要检查调用者类别和原始路径上的大小写敏感字符串黑名单；没有从已扫描库存重新取得分类，没有规范化作用域/身份，也没有检查预览与确认记录。因此仅就插件代码而言，“E/P 不处理”和“先预览”不是完整的安全边界。宿主 DSH 可能另有审批，本次未检查宿主实现，不能据此声称宿主会或不会拦截。[执行入口](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L155-L194)、[安全门](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/safety.mjs#L39-L78)。

另一个可见问题是所有工具的 presentCall.kind 都是 other，包括删除与移动，降低了逐种动作的可见性。若宿主审批依赖此字段还可能有额外影响，但那需要核对宿主才能下结论。[通用呈现包装](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L71-L81)。

`disk_classify_confirm` 也由模型工具入口提供，直接接收路径、名称、类别并写入确认缓存；插件层没有把这次“确认”绑定到特定用户交互回执。因此工具名里写 confirm，不等于已经证明用户亲自确认过。盘序的用户反馈入口和模型建议工具应继续分开。[确认工具](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L196-L213)。

盘序应继续使用模型可观察对象 ID、冻结的用户范围、版本化方案、用户选择、执行前身份与保护核查。告诉模型工具“如何用”不等于把授权判断交给模型文本。

### B. 实际永久删除和覆盖目标，与恢复文案不匹配

deleteDir 直接使用递归 rmSync，没有回收站或备份。junctionMove 会先递归删除已经存在的目标目录，再移动源目录并创建链接；目标名主要从显示名清洗得来，不含强唯一身份。若移动成功后建链接失败，没有恢复原位的事务步骤，失败也不会写成功 undo 记录。成功日志只在动作完成后追加，写失败会被忽略；没有崩溃前意图记录、逐步收据或真正的 undo 执行器。[实际动作](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/executor.mjs#L34-L75)、[目标名生成](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L295-L303)、[日志写入与 runAction](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/safety.mjs#L19-L36)。

盘序不能复用这套真实执行代码来填补尚未实现的能力。迁移需检测冲突、禁止覆盖未知目标、验证复制/移动结果、分步记录和失败恢复；真实删除需按实际恢复能力设计和验收。

### C. 名称规则与名称缓存缺乏足够证据

规则把名称含 cache 的目录直接高置信判为缓存，且该子串规则排在 npm-cache 的官方改址规则之前，后者在这一路径上会被前者抢先命中。某些应用名整体判成可 junction 数据，但没有检查其中是否混有资料或设置。名称相同并不证明两个位置有相同用途。[规则顺序](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/riskmap.mjs#L132-L195)、[分类控制流](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/riskmap.mjs#L242-L294)。

缓存主键包含目录名和由名称推导的几个标志，没有目标实体、路径、子树版本或失效时间；命中缓存后直接返回，不重新走 LLM 分支的 redline 检查。应借鉴“用户经验可积累”，而不是这一具体缓存有效性规则。[缓存优先级](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/classify.mjs#L154-L186)、[名称签名](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/classify.mjs#L196-L209)。

### D. 只读扫描也要审查脚本构造、阻塞和覆盖事实

体积统计通过同步 execFileSync 运行递归 PowerShell，单次超时可达 600 秒，未提供取消信号或条目/深度预算。错误被吞掉后没有完整覆盖状态；lastAccess 只看有限目录项，不能当使用历史。默认扫描根含固定 Administrator 目录，盘符检测失败又返回一组假定盘符。[统计脚本](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/scanner.mjs#L15-L42)、[时间近似](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/scanner.mjs#L56-L79)、[盘符回退](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/index.mjs#L16-L28)。

静态审查还发现统计脚本把来自目录枚举的路径拼入 PowerShell 双引号字符串，只处理双引号，没有阻止 PowerShell 的变量、反引号或子表达式解释。`-LiteralPath` 只能影响 cmdlet 如何解释已经求值的参数，不能解决字符串构造阶段的插值问题。本次未运行恶意目录名或 PoC；这是应避免的代码构造风险。[脚本构造行](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/scanner.mjs#L20-L31)。

盘序保留有界、本机原生枚举和可取消后台任务；如果以后调用辅助程序，使用结构化参数/数据通道，不拼接不可信目录名为脚本。输出同时记录 scanned/omitted/error/budget 情况。

### E. 模型路由、隐私与验证证据还需补强

插件将完整路径发给模型；默认关闭推理；当前默认模型不可得时，选择第一个可用提供方的第一个模型。这是具体宿主下的兼容策略，不适合照搬到盘序：我们需要用户明确的连接、模型能力适配、默认隐藏路径和无静默跨服务商回退。[模型选择](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/classify.mjs#L38-L85)、[发送内容](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/core/classify.mjs#L88-L128)。

固定提交没有测试目录或测试脚本；可见 CI 只核对 tag 与包版本，然后发布 npm，没有执行扫描/执行/回滚测试。CHANGELOG 中存在作者本机联调的描述，应作为作者声明而非我们的验收证据。[发布工作流](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/.github/workflows/publish.yml#L28-L57)、[包配置](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/package.json)、[变更记录](https://github.com/wangzhanchao883/dsh-disk-manager/blob/66c0bc366429e0147d955285ce555fa92547e5db/CHANGELOG.md#L10-L25)。

## 对当前方案的结论

值得吸收的是“空间对象有不同角色与动作”“给模型有界目录概况并复用已确认经验”“人和模型共用服务且有明确回执”。它进一步支持盘序 LLM 主导、程序提供证据和可操作环境的方向，并不提供一个可以直接替代当前 TaskHarness、权限、审阅或恢复层的成熟底座。

不建议据此 clone 为代码基地、增加整个 DSH 运行时依赖，或导入其永久删除/junction 执行器。当前完成的 0.5 工作仍可保留为候选实现；在继续交付前，优先核对任务范围、按需目录观察、经验缓存、动作恢复与开发模式的证据语义，而不是以“同样有工具/undo 日志”判断功能已经等价。
