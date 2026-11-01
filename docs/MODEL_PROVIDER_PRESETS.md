# 模型服务商预设与协议适配提案

状态：方案与官方文档核对结果，尚未实现或进行真实服务调用。核对日期：2026-11-02；当前程序为 0.4.0。下列模型和参数是当日文档快照，实施和后续维护时须再核对。

## 普通用户的连接流程

默认只显示服务商、模型、API 密钥和“保存并测试”。服务商下拉提供 OpenAI（GPT 系列）、GLM（智谱标准 API）、DeepSeek、自定义兼容服务；Z.AI 作为 GLM 的不同平台连接单独标识，不混用域名和密钥。

选择服务商后自动提供官方地址、支持的协议、候选模型与该模型可用参数。默认使用经过盘序工具循环验证的配置；官方文档支持不等于盘序已实测。地址、协议、推理强度、超时等放在高级设置；不能让用户先看懂全部参数才开始。

参数自动设置应由“服务商＋具体模型＋协议”共同决定，至少覆盖工具格式、思考与续接、输出预算字段、流式事件及超时。首版优先采用官方支持且已通过盘序验收的组合；不把所有模型的 temperature、max_tokens 或思考开关设成同一个值。“省时／均衡／深入”若提供，也由该组合映射有效参数，保留用户保存的选择，不根据模型名称猜测。

模型清单优先来自对应提供方已验证的枚举接口；接口不存在或能力信息不足时使用随版本维护的官方文档清单，并保留输入模型 ID。模型枚举成功不证明可调用或支持工具；连接测试才验证当前账户、模型与协议组合。

模型预设带版本、官方来源和核对时间。更新建议不会悄悄替换用户已保存的模型或改到另一个计费端点。“自定义兼容”保留现有远程 HTTPS／本机回环 HTTP、服务来源与密钥保护约束。

## 当日官方依据

以下是接入参数证据，不是成本或效果排名。推理深度与默认模型要经过盘序真实任务的质量、耗时和用量评估，不能直接将厂商旗舰设为所有用户的最优选择。

| 预设 | 当日候选模型 | 官方地址与协议 | 适配重点 |
|---|---|---|---|
| OpenAI（GPT 系列） | `gpt-6.1-sol` | `https://api.openai.com/v1`；本模型的工具调用使用 Responses | `reasoning.effort` 支持 low、medium、high、xhigh、max；不能继续套用当前唯一的 Chat Completions 工具格式 |
| GLM（智谱标准 API） | `glm-5.3`、`glm-5.3-flash` | Chat Completions 基础地址 `https://open.bigmodel.cn/api/paas/v4` | 5.3 系列强制开启思考；不能为了省时发送 disabled。按对应模型和端点处理上下文 |
| GLM（可选 Responses） | `glm-5.3` | `https://open.bigmodel.cn/api/v1` | 官方注明部分有过 Coding Plan 的账户暂受协议限制；不能无条件宣称人人可用 |
| Z.AI（单独连接） | 按该平台当期清单及账户验证 | `https://api.z.ai/api/paas/v4`；Chat Completions | 与智谱国内地址分别配置和保存凭据，不从同属 GLM 推断账户互通 |
| DeepSeek | `deepseek-flash`、`deepseek-v4-pro` | `https://api.deepseek.com`；Chat Completions，可另行适配 Responses | 当前 Flash 名称已对应更新版本；思考工具循环需要保留协议要求的历史信息 |
| 自定义服务 | 用户指定并测试 | 用户地址与所选协议 | 不从模型名字或 `/models` 返回值猜测全部能力，不静默跨服务商降级 |

OpenAI 的模型页明确要求 GPT-6.1 Sol 工具调用使用 Responses，并列出上述推理强度。[模型文档](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[工具循环与结果回传](https://developers.openai.com/api/docs/guides/function-calling)。

GLM-5.3 支持的思考强度是 low、high、max，不能关闭思考；Flash 系列文档同样说明强制思考和工具能力。上表的 Chat Completions、Responses 地址和账户条件来自模型页。[GLM-5.3](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3)、[GLM-5.3-Flash](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)、[国内 OpenAI 兼容接口](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)、[Z.AI 接口](https://docs.z.ai/guides/develop/openai/python)。

DeepSeek 当日入门页给出 `deepseek-flash`、`deepseek-v4-pro`，并说明 Flash 别名当前对应 DeepSeek-V4.1-Flash-1017。思考模式的工具请求必须按协议传回历史 `reasoning_content`；不能只保留最终文本和工具参数。[入门与当前模型](https://api-docs.deepseek.com/quick_start/agent_integrations/)、[思考工具循环](https://api-docs.deepseek.com/guides/thinking_mode/)。

### 账户、凭据与计费的表述

预设默认连接标准模型 API，不把对话产品登录、Coding Plan、模型 API 和第三方中转视为同一个账户权益。API 密钥按已保存连接分别保护；切换平台不得把旧密钥发给新域名。切换协议、API 路径或计费入口时也应核对适用凭据与账户条件，不能仅比较域名。

OpenAI 当期官方文档允许符合条件的 ChatGPT Pro／Business 订阅领取并绑定 API 组织额度。因此不能写死“所有 ChatGPT 订阅与 API 额度完全无关”；也不能说只登录 ChatGPT 就已接通盘序。应用接入使用相应 API 组织的项目密钥，可用权益以账号及当期规则为准。[订阅 API 额度规则](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)。

智谱 2026-10-16 公告区分标准、个人套餐、团队套餐和历史兼容 Key，后者计费还与端点及账户历史有关。盘序不默认选择 Coding Plan 端点，不承诺非编程用途可用套餐；若将来提供套餐配置，应另行核对该平台适用条件并向用户说明。[Key 分类与计费入口](https://docs.bigmodel.cn/cn/coding-plan/notice/api-key-management)。

## 协议适配层

任务系统只消费统一的文本摘要、工具调用、用量、结束原因与错误。提供方适配器负责端点、鉴权、消息与工具格式、续接状态、流式事件、输出预算及不支持参数的处理。

- Responses 不能仅把路径换成 `/responses`；输入、函数定义、工具调用输出、调用 ID 与结果回传都要适配。
- GLM 交错思考要求保留 reasoning content；启用保留式思考时，应按文档原样传回，不对其重新排列、摘要或清洗后冒充原始协议内容。[GLM 思考模式](https://docs.bigmodel.cn/cn/guide/capabilities/thinking-mode)。
- DeepSeek 思考工具循环有相应的历史回传要求；不支持的采样参数不得原样套用所有模型。
- 需要保持的协议内容作为内部续接状态，不能当作面向用户的用途解释，也不写入普通操作日志。当前任务保存不记录原始推理，后续重开可从持久目标、事实、选择与结果建立新一轮上下文；若设计持久续接状态，应另设受保护存储与保留策略。
- 上下文减少通过聚合本地观察、选取相关证据和开始新的有据任务轮次实现，不能破坏仍在使用的协议续接状态。
- 工具参数仍经作用域、ID、动作及身份验证；厂商支持联网、Shell 或 Computer Use，不表示盘序自动开放这些能力。

建议内部保存 `ProviderPreset`、`ConnectionProfile` 与 `ModelCapabilities`，分别表达官方模板、用户具体连接与文档/实测能力。预设至少记录服务商 ID、协议、官方来源、核对日期、凭据作用域、候选模型、可用推理档、输出限制语义、续接要求与能力验证状态。

## “测试连接”需要测试什么

用户主动测试时，说明会发起少量模型请求；不发送个人文件名、目录、后台清单或其他扫描数据。

1. 使用当前服务商、模型、协议和凭据发起测试，请模型调用一个返回固定测试数据的只读函数。
2. 核对调用 ID 与参数，返回固定结果，再取得可解释的最终响应。
3. 记录当前组合是否完成工具往返，而不把列出模型或回答“你好”当作完整兼容证明。
4. 区分鉴权、无该模型权限、额度、限流、协议、参数、工具不支持和网络故障；保留已输入设置，不自动更换付费服务。
5. 对流式、取消、超时、格式错误、多次工具调用和思考续接分别验收。后续仍需用盘序整理任务验证建议质量。

## 当前实现差距和下一步

当前 `diskpilot/llm.py` 只拼接 `/chat/completions`，统一发送 `max_tokens`、`tool_choice` 等字段；`agent_turn` 只返回清理后的内容与调用信息，没有完整的 Responses 和思考续接状态。设置界面只有自定义地址、模型和密钥，没有服务商预设或工具往返测试。

因此，不能只加下拉菜单就宣称最新 OpenAI、GLM 和 DeepSeek 已经可用。建议先实现协议适配、模板与无私人数据的连接测试，再接统一列表分析任务；所有提供方仍需用真实凭据和账户验证。本文只做官方文档核对，没有调用外部模型、保存新密钥或修改当前模型配置。
