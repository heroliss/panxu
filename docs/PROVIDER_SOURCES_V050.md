# 模型预设与协议实现核对快照

核对日期：2026-11-02。本文描述 0.5 连接适配及其来源快照，交付状态见发布验收。所有提供方的 `live_provider_tested` 仍为 `false`：本轮没有使用真实 API 密钥、付费账户或私人扫描数据调用云模型。自动化测试中的服务商响应是本机协议夹具；它能验证请求、工具回传与续接格式，不能证明某个账户具有模型权限，也不能替代真实任务质量评估。

## 官方入口

下表中的候选模型、地址和协议是当日官方文档快照，不是模型质量或价格排名。候选模型仍可手动编辑；新版本不自动替换用户保存的连接。

| 服务商 | 预设协议与地址 | 官方依据及特别条件 |
|---|---|---|
| OpenAI | Responses；`https://api.openai.com/v1` | [模型目录](https://developers.openai.com/api/docs/models)、[Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra)、[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)、[函数调用](https://developers.openai.com/api/docs/guides/function-calling)。本应用的 GPT-6 工具循环使用 Responses。 |
| 智谱 GLM | Chat Completions；`https://open.bigmodel.cn/api/paas/v4` | [GLM-5.3](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3)、[思考续接](https://docs.bigmodel.cn/cn/guide/capabilities/thinking-mode)。使用标准 API，不把 Coding Plan 当成默认入口。 |
| Z.AI | Chat Completions；`https://api.z.ai/api/paas/v4` | [官方兼容接口](https://docs.z.ai/guides/develop/openai/python)。与智谱国内连接分别配置账户和密钥。 |
| DeepSeek | Chat Completions；`https://api.deepseek.com` | [当前模型与代理接入](https://api-docs.deepseek.com/quick_start/agent_integrations/)、[思考工具循环](https://api-docs.deepseek.com/guides/thinking_mode/)。历史思考信息仅作为协议续接原样回传。 |
| Kimi 国内／国际 | Chat Completions；`https://api.moonshot.cn/v1`／`https://api.moonshot.ai/v1` | [国内接入](https://platform.kimi.com/docs/get-api-key)、[国际接入](https://platform.kimi.ai/docs/get-your-api-key)、[K3.1 参数与续接](https://platform.kimi.ai/docs/guide/kimi-k3-1-pro-quickstart)。K3.1 使用 `max_completion_tokens`，省略不支持的采样参数。 |
| 通义千问 北京／新加坡 | Chat Completions；DashScope 对应地域的 `compatible-mode/v1` | [地域、共享与工作空间专属地址](https://help.aliyun.com/en/model-studio/base-url)、[兼容协议](https://help.aliyun.com/en/model-studio/compatibility-of-openai-with-dashscope)、[模型服务概览](https://help.aliyun.com/en/model-studio/what-is-model-studio)。共享地址仍受支持；高级设置允许配置工作空间专属地址，密钥必须匹配地域。 |
| 豆包 | Responses；`https://ark.cn-beijing.volces.com/api/v3` | [火山方舟官方概览](https://console.volcengine.com/ark/region:cn-beijing/docs/product-overview)。按账户实际开通的模型或 Endpoint ID 配置。 |
| MiniMax 国际 | Anthropic 兼容 Messages；`https://api.minimax.io/anthropic` | [官方 Anthropic 兼容协议](https://platform.minimax.io/docs/api-reference/text-anthropic-api)。使用其兼容版本头，不自动套用 Anthropic 新版本。 |
| 腾讯混元旧平台 | Chat Completions；`https://api.hunyuan.cloud.tencent.com/v1` | [官方旧平台说明](https://cloud.tencent.com/document/product/1729/111007)。已不再新增模型服务；新用户需按 TokenHub 实际入口配置自定义连接。 |
| 百度千帆 | Chat Completions；`https://qianfan.baidubce.com/v2` | [官方兼容接口](https://cloud.baidu.com/doc/qianfan-docs/s/Fm9l6ocai)。没有猜测账户可用模型；用户填写已开通且支持工具的 ID。 |
| 硅基流动 | Chat Completions；`https://api.siliconflow.cn/v1` | [官方快速开始](https://docs.siliconflow.cn/docs/userguide/quickstart)。同一平台模型能力不同，保留手填 ID 与工具测试。 |
| Anthropic Claude | 原生 Messages；`https://api.anthropic.com/v1` | [模型目录](https://platform.claude.com/docs/en/models/overview)、[Messages](https://platform.claude.com/docs/en/api/messages/create)、[2026-10-21 迁移](https://platform.claude.com/docs/en/api/migration-2026-10-21)、[effort](https://platform.claude.com/docs/en/build-with-claude/effort)。官方连接使用新版本头；兼容平台单独处理。 |
| Google Gemini | 原生 generateContent；`https://generativelanguage.googleapis.com/v1beta` | [generateContent](https://ai.google.dev/api/generate-content)、[thinking 与签名](https://ai.google.dev/gemini-api/docs/generate-content/thinking)。使用 `x-goog-api-key` 与原生工具格式，保留函数调用的签名。 |
| xAI Grok | Responses；`https://api.x.ai/v1` | [Grok 4.7 官方说明](https://docs.x.ai/developers/grok-4-7)。不自动开放厂商联网、代码执行等工具。 |
| Ollama／本地兼容服务 | Chat Completions；`http://127.0.0.1:11434/v1` | [官方兼容接口](https://docs.ollama.com/api/openai-compatibility)。仅连接用户已安装、正在运行且支持工具的模型；不会下载模型。 |

另有空白的自定义预设。合计 18 项预设，其中国内／国际和不同地域分别列出，避免混用账户与密钥。百度、硅基流动、本地服务不提供未经账户核对的默认模型。

## 参数、上下文与诊断

- 使用四个协议适配器，转换消息、工具 schema、调用 ID、结果与输出预算。旧建议接口也使用相同适配器，不能因入口不同把原生模型发往 `/chat/completions`。
- 高级推理档按服务商、模型与协议验证。GLM、DeepSeek、Kimi 的 `medium` 明确映射为 `high` 并反映在保存设置中；Gemini 3.8 Flash 只有 low／medium／high；没有核对的组合只开放自动档。自定义 Chat／Responses 显式参数由高级用户和往返测试核对。
- Chat 保留必要的完整 assistant 消息；Responses 保留原生 output 项与加密续接；Messages 保留原生内容块；Gemini 保留完整 model parts。续接只在当前任务内存中使用，并绑定服务商、协议、地址、模型及推理设置，连接变化后不能重用。
- 开发追踪记录模型实际可见的请求、工具定义、工具结果与公开响应，默认关闭。密钥、鉴权头、原始思考文本、签名与不透明续接不落日志；请求中的普通思考配置仍可查看。日志失败不影响模型调用。
- “测试连接”进行两次请求：模型调用带随机 nonce 的只读固定测试工具，程序核对参数并回传固定结果，再核对最终回答。只回答“你好”不算通过；不发送文件目录清单。错误按鉴权、模型、参数、限流、网络及协议分类，不直接显示提供方可能含敏感内容的错误正文。

## 验证边界

本机自动化覆盖四种协议的工具往返、原生续接、参数映射、旧配置兼容、来源绑定密钥、错误分类、追踪脱敏及调用途中更换密钥的并发回归。当前请求为非流式、有超时和体积预算；未实现流式事件适配。真实厂商账户、地区连通性、额度、端点权限、长任务稳定性和建议质量仍待独立验证。不能仅凭预设存在或本地夹具通过就宣称所有服务商实测可用。
