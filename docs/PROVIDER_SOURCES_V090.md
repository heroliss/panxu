# v0.9 模型预设与请求协议核对

核对日期：**2026-11-02**。范围是 `diskpilot/providers.py` 的 18 个预设、应用实际发送的文本/函数工具请求及四种原生协议的流式映射。只查看官方文档并使用本机协议夹具；**未调用任何真实云模型、未读取真实密钥、未更改用户配置**。文档描述和本机测试均不能证明某个账户的权限、额度、网络或模型可用性。

## provider 与消息 role

`provider='deepseek'` 是本地选择连接适配器的设置，与 `model`、`protocol`、`base_url` 同属连接信息。它不是消息的角色。Chat API 实际收到的是 `messages=[{role:'system',…},{role:'user',…}]`，后续才加入 `assistant` 和 `tool` 消息。开发记录外层的 provider 用于标记记录来源，不会作为 API 顶层字段或消息角色外发。

本版改为按允许字段构造 Chat 请求，排除连接设置与未知内部字段，并对四协议的 canonical 消息角色进行校验。18 个预设分别有非流式/流式序列化回归；另有每预设两轮固定 nonce 工具往返夹具。固定连接测试不发送用户磁盘清单，并强制使用非流式，避免把连通性与流式解析混在一起。

## 四协议映射

| 协议 | 实际端点/开关 | 工具与续接 |
| --- | --- | --- |
| Chat Completions | `/chat/completions`，请求 `stream:true/false` | `messages[].role`；函数结果为 `tool` 消息；完整原生 assistant 消息只在同一连接的内存中续接。 |
| Responses | `/responses`，请求 `stream:true/false`、`store:false` | 扁平 `function` 定义；结果为 `function_call_output`；原生 output 中的 reasoning/加密内容原样续接。 |
| Anthropic Messages | `/v1/messages`，请求 `stream:true/false` | system 为顶层指令；user/assistant 内容块；`tool_result` 返回调用 ID；完整 thinking/signature/tool_use 块原样续接。 |
| Gemini Generate Content | `/models/{model}:generateContent` 或 `:streamGenerateContent?alt=sse` | `systemInstruction`、user/model contents；`functionCall`/`functionResponse`；原生 parts 与 thought signature 原样续接。流式开关在端点，不向 JSON 塞 `stream`。 |

流式请求使用 `Accept: text/event-stream`。流式组装由独立传输模块实现，不在本模块伪造分块；工具参数完整且通过校验之后才交回 Harness。Chat 的 `choices[].finish_reason`、Gemini 的 `candidates[].finishReason`、Messages 的 `stop_reason` 与 Responses 的 `status` 都保留，不能把截断误报完成。思考内容与协议签名不作为开发面板的普通文本保存。

## 18 项预设

表中 `auto` 是工具选择策略，不是测试放宽：必须实际返回预期函数与精确随机 nonce，再正确读取工具结果，才算工具往返通过。能力字段 `tool_choices` 是盘序目前对该预设支持的保守子集，不是厂商所有模型的完整能力表。

| ID | 基础地址 / 协议 | 预设模型 | 工具、思考与流式核对 |
| --- | --- | --- | --- |
| `custom` | 用户填写 / 默认 Chat | 用户填写 | 不推断兼容服务的能力；四协议可选。固定数据往返验证，不能把普通文本成功当工具成功。[OpenAI 函数协议](https://developers.openai.com/api/docs/guides/function-calling)仅作格式依据。 |
| `openai` | `https://api.openai.com/v1` / Responses | `gpt-6.1-sol`、`gpt-6.1-terra`、`gpt-6-luna`、`gpt-6-astra` | 选择 Responses 支持思考时的函数工具；stream 使用原生 SSE 事件。显式 include 加密 reasoning，`store:false` 下原样续接；不启用厂商内置联网/代码执行工具。见下方 OpenAI 来源。 |
| `glm` | `https://open.bigmodel.cn/api/paas/v4` / Chat | `glm-5.3-flash`、`glm-5.3` | 5.3 保留 thinking 与 `clear_thinking:false`，effort 使用文档支持值。Chat 参数页只列 `tool_choice:auto`；探测用 auto，禁用本轮工具时省略定义与选择字段，保留既有消息。支持 SSE；不额外假设 `tool_stream` 必须开启。见[模型说明](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3)、[Chat API](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)。 |
| `zai` | `https://api.z.ai/api/paas/v4` / Chat | 同 GLM | 按国际 API 文档独立核对；工具选择同样只列 auto，采用同一省略禁用方式。账户与国内端点不混用。[Chat API](https://docs.z.ai/api-reference/llm/chat-completion)。 |
| `deepseek` | `https://api.deepseek.com` / Chat | `deepseek-flash`、`deepseek-v4-pro` | thinking 默认保留，保守 high；**思考模式不能 required/named**。探测用 auto，末轮 none；有工具时保留历史 assistant 的 reasoning_content。支持 SSE，不添加 beta strict、stream_options 等额外字段。[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)、[思考续接](https://api-docs.deepseek.com/guides/thinking_mode/)、[当前接入](https://api-docs.deepseek.com/quick_start/agent_integrations/)。 |
| `kimi_cn` | `https://api.moonshot.cn/v1` / Chat | `kimi-k3.1-pro`、highspeed | K3.1 不关闭思考；使用 `max_completion_tokens`；不发送固定以外的采样参数。**官方明确支持 required**，不能照搬 DeepSeek 限制。完整消息续接；支持 SSE。[快速开始](https://platform.kimi.com/docs/get-api-key)、[K3.1](https://platform.kimi.com/docs/guide/kimi-k3-1-pro-quickstart)、[工具选择](https://platform.kimi.com/docs/guide/use-tool-choice)、[Chat API](https://platform.kimi.com/docs/api/chat)。 |
| `kimi_global` | `https://api.moonshot.ai/v1` / Chat | 同 Kimi 国内 | 国际快速开始确认该域名和模型；K3.1 参数与续接按官方对应指南。不同地域账户分别配置。[国际快速开始](https://platform.kimi.ai/docs/get-your-api-key)、[工具选择](https://platform.kimi.ai/docs/guide/use-tool-choice)、[流式](https://platform.kimi.ai/docs/guide/utilize-the-streaming-output-feature-of-kimi-api)。 |
| `qwen_cn` | `https://dashscope.aliyuncs.com/compatible-mode/v1` / Chat | `qwen3.8-max` | 共享北京地址仍可用；官方也推荐工作空间专属地址，不静默迁移用户配置。默认思考时 required/named 不支持，探测用 auto；保留 reasoning_content。支持 stream，当前 Max 也支持非流式思考。见阿里来源。 |
| `qwen_global` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` / Chat | 同 Qwen 北京 | 新加坡地域密钥需匹配；工具/思考/流式采用同一模型 API 规则，不将阿里部署的其他厂商模型限制套到原厂端点。见阿里来源。 |
| `doubao` | `https://ark.cn-beijing.volces.com/api/v3` / Responses | `doubao-seed-2-1-pro-260915` | 当前官方示例模型；支持 `store`、`stream` 和扁平函数定义。只发送函数文档列出的 name/description/parameters/type，不夹带 OpenAI 的 `strict:false`。工具选择受具体模型支持范围约束，需实际工具测试。[概览](https://console.volcengine.com/ark/region:cn-beijing/docs/product-overview)、[创建 Response](https://console.volcengine.com/ark/region:cn-beijing/docs/create-model-responses-api)、[SSE](https://console.volcengine.com/ark/region:cn-beijing/docs/streaming-output)。 |
| `minimax` | `https://api.minimax.io/anthropic` / Messages | `MiniMax-M3.1-Flash` | 官方原生兼容端点及 Bearer curl；支持 system/tools/tool_choice/stream，完整 response.content 续接。M3.1 思考不能 disabled；effort 仅显式选择时发送；默认保持服务默认值。[官方 API](https://platform.minimax.io/docs/api-reference/text-anthropic-api)。 |
| `hunyuan` | `https://api.hunyuan.cloud.tencent.com/v1` / Chat | `hunyuan-turbos-latest` | **旧平台**，已有服务仍可用，新购迁往 TokenHub。文档工具策略为 none/auto/custom，无通用 required，探测用 auto。SSE 从 delta 组装；不自动开启搜索增强。[官方兼容说明](https://cloud.tencent.com/document/product/1729/111007)。 |
| `qianfan` | `https://qianfan.baidubce.com/v2` / Chat | 用户填写 | 不猜账户已开通模型；支持工具的模型支持 auto/none，但 required 仅部分系列，故探测用 auto。不擅自添加应用 ID、联网或账户私有字段。官方提供思考流式示例。[SDK/地址](https://cloud.baidu.com/doc/qianfan-docs/s/Fm9l6ocai)、[工具支持范围](https://cloud.baidu.com/doc/qianfan-docs/s/xm95lyys5)。 |
| `siliconflow` | `https://api.siliconflow.cn/v1` / Chat | 用户填写 | 当前 API 明列 tools 和 SSE，官方例子使用 auto；不对全部托管模型承诺 required。禁用本轮工具省略定义与选择字段，避免发未确证的枚举值；不将原厂 DeepSeek/GLM 思考参数移植到聚合平台。[快速开始](https://docs.siliconflow.cn/docs/userguide/quickstart)、[Chat API](https://docs.siliconflow.cn/docs/api/chat-completions-post)。 |
| `anthropic` | `https://api.anthropic.com/v1` / Messages | Sonnet/Haiku/Opus 5.5 | API 版本 `2026-10-21`；Bearer 合法。Sonnet/Opus5.5 禁止 any/tool 强制选择，探测用 auto；Haiku 仍可 required。默认思考不关闭，完整内容块与签名续接。支持原生 Messages SSE。见 Claude 来源。 |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` / Generate Content | `gemini-3.8-flash` | 原生工具 AUTO/ANY/NONE、functionResponse 和 thought signature；不伪装 OpenAI Chat。思考只支持 low/medium/high，max 在设置阶段拒绝。SSE 端点固定 `:streamGenerateContent?alt=sse`，密钥在 header，不放 URL。见 Google 来源。 |
| `grok` | `https://api.x.ai/v1` / Responses | `grok-4.7` | SSE；函数选择 auto/required/none；加密 reasoning 原样续接。xAI 工具隐式 strict，不发送 strict:false。显式高级 effort 暂未开放，不猜 max 到 xhigh 的映射。[模型](https://docs.x.ai/developers/grok-4-7)、[SSE](https://docs.x.ai/developers/model-capabilities/text/streaming)、[思考](https://docs.x.ai/developers/model-capabilities/text/reasoning)、[工具](https://docs.x.ai/developers/tools/function-calling)、[严格输出](https://docs.x.ai/developers/model-capabilities/text/structured-outputs)。 |
| `ollama` | `http://127.0.0.1:11434/v1` / Chat | 用户填写已安装模型 | 官方仅承诺 OpenAI API 子集，当前文档列 stream/tools/tool_choice。使用该兼容 SSE 协议，不把原生 NDJSON 当 SSE；工具与 effort 依模型，保守 auto 探测。程序不下载模型、不切换到云服务。[官方兼容清单](https://docs.ollama.com/api/openai-compatibility)。 |

## 重点官方补充来源

### OpenAI

- [Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra)、[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)、[Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)：核对预设 ID、工具与流式能力。默认使用 Responses；不要从 Chat 兼容性推断思考时的工具兼容性。
- [函数调用](https://developers.openai.com/api/docs/guides/function-calling)、[流式 Responses](https://developers.openai.com/api/docs/guides/streaming-responses)、[Chat SSE 参考](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、[Responses SSE 参考](https://developers.openai.com/api/reference/resources/responses/streaming-events)。用量块可能无 choices，工具参数按 ID/索引聚合后再验证。

### 阿里云 Qwen

- [地址与地域](https://help.aliyun.com/en/model-studio/base-url)、[函数工具限制](https://help.aliyun.com/en/model-studio/qwen-function-calling)、[OpenAI Chat 参数](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)、[思考模式](https://help.aliyun.com/en/model-studio/deep-thinking)。当前 Qwen3.8-Max 默认思考与 preserve_thinking 均开启；应用保留原生 assistant 消息，不强制关闭思考以换取 required。

### Claude

- [Messages API](https://platform.claude.com/docs/en/api/messages/create)、[2026-10-21 迁移](https://platform.claude.com/docs/en/api/migration-2026-10-21)：system 顶层、工具内容块、stream 与 Bearer 鉴权。
- [Sonnet5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5)、[Opus5.5](https://platform.claude.com/docs/en/models/opus-5-5/whats-new-opus-5-5)、[定义工具](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)、[effort](https://platform.claude.com/docs/en/build-with-claude/effort)：Sonnet/Opus 强制工具限制不同于 Haiku；不统一将所有模型设为 any，也不发送旧手动 budget_tokens。

### Google

- [Generate Content API 参考](https://ai.google.dev/api/generate-content)核对 REST、SSE、字段和 enum。
- 官方当前将本适配器对应指南归在 **Generate Content (Legacy)**：[函数调用](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)、[思考 REST 示例](https://ai.google.dev/gemini-api/docs/generate-content/thinking)、[签名续接](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)。这些不能与新的 Interactions 请求/事件结构混用；当前未实现 Interactions 适配器。REST 指南中 thinkingLevel 使用小写 low，沿用该官方示例。

## 修复、验证与限制

- 已确证并修复：除 DeepSeek 既有修复外，GLM/Z.ai 不再发不在文档枚举中的 required/none；Qwen 思考探测不用 required；Sonnet/Opus5.5 不强制 any/tool。Kimi required 保持，避免把一家限制泛化为所有模型限制。
- 谨慎兼容：混元/未指定具体模型的聚合和本地服务用 auto 探测；硅基流动禁用工具省略定义；方舟/xAI 不接收盘序假定通用的 strict:false。这里的保守发送不等于已经证明对端拒绝其他值。
- 四协议继续保留同一连接的原生续接；本地连接身份绑定阻止跨模型/端点移植。将 `provider` 误当消息 role 的路径有负向测试，连接元信息泄漏有允许字段测试。
- 本地夹具覆盖 18 项两轮工具往返，以及 18 项×流式开关的请求结构、厂商特殊拒绝组合、签名/思考续接与终态。**云端真实 SSE、计费、延迟、长文本/截断、代理行为和每个账户仍未实测**。
- 工具测试用少量固定数据和有界输出预算；思考模型可能把预算耗在思考后才停止，不能通过自动关闭思考、无限扩大预算或将普通回复算成功来掩盖失败。自定义模型/端点或账户策略变化时应重测。
- 预设不自动覆盖已保存设置。高级手动换协议、非预设模型、Coding Plan/企业网关/专属地址不在本表的完整兼容承诺内；应按相应服务文档和两轮测试单独确认。
