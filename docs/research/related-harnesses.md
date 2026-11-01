# 文件整理智能体：相关 harness 源码核对

核对日期：2026-11-02。范围是帮助盘序重新规划任务运行时，不是选择现成软件替换盘序。本轮仅通过 GitHub 官方仓库、API 与固定提交源码做静态检查；没有安装、克隆、导入或执行这些项目，没有调用其模型，也没有让它们操作本机文件。README 宣称、源码实现和运行验收分别看待；下列“可见实现”不等于本机运行通过。

## 1. File-Organiser / fileops-hub

[仓库](https://github.com/isiraglobal/File-Organiser)，核对提交 [`e7968ed0328b986a6eccbeb73fec7991561d1553`](https://github.com/isiraglobal/File-Organiser/tree/e7968ed0328b986a6eccbeb73fec7991561d1553)。它是公开可读源码项目，**不是可自由复用的开源项目**；其专有许可证限制修改、分发与衍生。这里只借鉴通用架构思想，不复制代码、提示词或资源。[固定版本许可](https://github.com/isiraglobal/File-Organiser/blob/e7968ed0328b986a6eccbeb73fec7991561d1553/LICENSE#L17-L57)

| 方面 | 固定源码可见实现 | 对盘序的启发与边界 |
|---|---|---|
| 小型 agent 循环 | 模型用文本中的 JSON action 提议 list/read/move/delete/mkdir；只读结果返回模型，变更另走执行层，自动续接有次数上限。 | 证明“提供工具说明→模型调查→结果回传”的结构不依赖庞大框架。盘序已有原生函数协议，继续保留其 schema、调用 ID 和续接，没必要退回文本动作解析。[agent.py](https://github.com/isiraglobal/File-Organiser/blob/e7968ed0328b986a6eccbeb73fec7991561d1553/fileops_hub/agent.py#L34-L66) |
| 有界路径与计划 | 路径解析后验证仍在 workspace；批量计划核对来源清单，拒绝部分非法路径；执行前再查源、目标与冲突。 | “工具帮助 LLM 调查”与“落实准确范围”可以并存。盘序可用目录 ID、扫描版本与完整性证据减少路径歧义，同时让模型自主选择下一步调查。[sandbox.py](https://github.com/isiraglobal/File-Organiser/blob/e7968ed0328b986a6eccbeb73fec7991561d1553/fileops_hub/sandbox.py#L21-L44)、[organizer.py](https://github.com/isiraglobal/File-Organiser/blob/e7968ed0328b986a6eccbeb73fec7991561d1553/fileops_hub/organizer.py#L113-L165) |
| 计划与演练 | 批量移动使用同一计划入口；默认 dry-run，真实动作前后分别写 proposed/result。演练状态明确为 dry_run。 | 共享动作生命周期是合适方向；模拟应验证虚拟后置条件并能产生失败，不能永远声称成功。演练仍有日志副作用，不能描述成程序完全没写入任何内容。[organizer.py](https://github.com/isiraglobal/File-Organiser/blob/e7968ed0328b986a6eccbeb73fec7991561d1553/fileops_hub/organizer.py#L127-L165) |
| 程序化去重 | 先按大小分组，仅对候选读取并计算 SHA-256；模型不参与字节是否相同的判定。 | 把精确计算交给本地工具，再让模型结合路径用途与用户偏好决定建议。此处默认按时间保留一个副本的策略不适合直接推广到项目、备份及个人资料。[dedupe.py](https://github.com/isiraglobal/File-Organiser/blob/e7968ed0328b986a6eccbeb73fec7991561d1553/fileops_hub/dedupe.py#L25-L89) |

需谨慎理解其恢复与审计宣称：

- ledger 的本次实现是追加 JSONL，记录提议、结果、撤销；未见哈希链、签名或不可变存储，不能仅据此称为防篡改审计。[ledger.py](https://github.com/isiraglobal/File-Organiser/blob/e7968ed0328b986a6eccbeb73fec7991561d1553/fileops_hub/ledger.py#L20-L56)
- 删除把对象移入 workspace 的 `_trash`；它既不是 Windows 回收站，也不是另一份独立备份。删除记录的 dst 为 None，而 `undo_last` 按 dst→src 处理所有成功动作；静态检查不能支持“删除、建目录和移动均可一键撤销”的完整承诺。[删除实现](https://github.com/isiraglobal/File-Organiser/blob/e7968ed0328b986a6eccbeb73fec7991561d1553/fileops_hub/agent.py#L143-L164)、[撤销实现](https://github.com/isiraglobal/File-Organiser/blob/e7968ed0328b986a6eccbeb73fec7991561d1553/fileops_hub/organizer.py#L168-L184)
- 文件预览先 `read_bytes()` 再截取，返回长度受限并不等于磁盘读取与内存分配受限。盘序后续正文工具应从文件句柄限量读取，并分别记录截断、编码、格式和大小限制。[预览实现](https://github.com/isiraglobal/File-Organiser/blob/e7968ed0328b986a6eccbeb73fec7991561d1553/fileops_hub/agent.py#L101-L111)

## 2. Agentic File Explorer

[仓库](https://github.com/darthblanc/agentic-file-explorer)，核对提交 [`e95a17baa2f7dec24e6c480dadbb78a03833e203`](https://github.com/darthblanc/agentic-file-explorer/tree/e95a17baa2f7dec24e6c480dadbb78a03833e203)。本次完整树与 GitHub 仓库元数据未见许可文件或明确开源许可；公开可读不等于取得复制、修改和再分发授权。因此同样只记录思想，不引入代码。

| 方面 | 固定源码可见实现 | 对盘序的启发与边界 |
|---|---|---|
| 工具分层 | 顶层只看到 txt/csv/directory/traversal 四个领域工具，每个工具内部是有专用提示与工具的子 agent；子模型独立配置。 | 工具说明可以按领域渐进提供，但不必为每次目录操作增加一次模型调用。盘序优先使用“发现能力→取得具体 schema→调用本地工具”，确有跨领域复杂任务再考虑子 agent。[hierarchical_agent_tools.py](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/tools/hierarchical_agent_tools.py#L19-L68) |
| 文件引用 | FileDictionary 保存文件对象；File 保存 ID、路径、大小和类型，`__str__` 输出引用，内容由显式工具按需读取。 | 很适合盘序的“大目录先聚合、证据按需展开”：上下文放对象引用与少量事实，不把目录树和所有正文一次塞入 prompt。其读取函数仍会读取全部文本，引用机制本身不保证读取预算。[file_dictionary.py](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/core/file_dictionary.py#L4-L34)、[file.py](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/core/file.py#L5-L28) |
| 上下文压缩 | 按阈值调用摘要模型，用摘要加最后一条消息继续，并保留摘要前的消息归档。系统 prompt 与文件引用稍后注入。 | 可借鉴稳定事实摘要，但不能直接照搬到必须保持完整工具往返的原生协议。总预算必须计入系统提示、工具定义、工具结果与续接；归档也要有保留预算。[context.py](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/core/context.py#L8-L37)、[stm.py](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/core/stm.py#L9-L33) |
| 结果与恢复反馈 | 文件写入后异步 Git 提交；UI 独立调用 rollback，并把结果通知注入后续对话。 | “外部动作发生后要更新 agent 所知状态”值得采用。用户微调、恢复与取消都应生成任务事件，避免模型继续使用过时计划。[txt_functions.py](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/functions/txt_functions.py#L20-L49)、[ui.py](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/entrypoints/ui.py#L350-L369) |

不建议直接复用的部分：

- 逐文件、逐次调用是项目提示中的明确选择。对含数万细碎文件的程序目录，这会放大延迟与 token；盘序应支持聚合检查、分页、筛选批次与基于变化的缓存。[base_prompt.md](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/prompts/system_prompts/base_prompt.md#L9-L13)
- Git 版本适合小型文本工作区；这里先写入、后 fire-and-forget 提交，提交异常未转化为动作失败，整体恢复还使用 `reset --hard`。这不是 Windows 大型二进制、跨盘文件、服务与卸载的统一恢复方案。盘序应为各类动作分别提供恢复收据与核验，而不是给整个磁盘加 Git。[rollback.py](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/core/rollback.py#L31-L75)
- 其路径帮助函数主要是 base/path 拼接，未在该函数内验证最终解析路径仍属于 base；不能从默认 `data` 目录或提示词推导出完善的 Win11 作用域与重解析点保护。[setup_directory.py](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/core/setup_directory.py#L9-L37)
- README 的速度对比来自作者特定 Linux／RTX 3050／模型组合，不是盘序在本机的性能证据。不能据此推断统一关闭思考或增加子 agent 一定更快。[固定版本 README](https://github.com/darthblanc/agentic-file-explorer/blob/e95a17baa2f7dec24e6c480dadbb78a03833e203/README.md)

## 对重新规划的建议

以下是结合源码与盘序目标作出的设计判断，不是上述项目已实现的盘序功能。

1. **LLM 决定调查路径，本地工具承担精确工作。** 分层目录统计、候选搜索、哈希、版本与应用证据、缓存聚合交给确定性服务；模型决定哪些证据值得继续取得、如何解释与提出方案。工具目录告诉模型能力、取得方法、输入输出、成本及不完整条件，不把固定垃圾配方变成唯一判断入口。
2. **先引用和摘要，再深入。** 对象 ID、当前版本、目录聚合、已核查范围、未知点作为事实；少量正文预览须另有用户可见的读取与外发政策。目录级判断不必分析每个叶子，但整目录动作仍须检查完整性和保留项。
3. **持久事实与协议续接分开。** 保存用户目标、保留意愿、证据引用、计划版本、结果与下一步；提供方原始思考、签名与加密续接只用于当前原生循环。启动新轮次时从事实恢复，不把未完成的工具往返截断后冒充有效历史。
4. **计划、执行、恢复共用事件基础，分别核验。** 模拟和真实后端可以共用冻结计划及 proposed/running/result 等事件；执行器返回可核查收据，状态包括失败、部分成功、待核实。恢复有自己的前置条件、目标冲突与结果，不仅写一个 undone 标志。
5. **先做可测的一条主线，暂不引入通用多 agent 框架。** 普通用户仍只看到目标、建议原因、主要操作；工具与协议细节放开发视图。先用混合大目录、未知程序、重复备份、用户保留、失败恢复、预算耗尽和连接变化等任务测量建议质量、用户决定次数、调用数、耗时与成本，再判断是否需要独立子 agent。

这些结论支持继续以 LLM 为中心，而不是退回单纯的缓存规则工具；同时说明 harness 的价值在于提供可验证的新证据、可靠操作和持续反馈。当前盘序仍只有模拟执行，研究没有扩大正文、网络或真实操作能力。
