"""Documented connection presets and native, text/function-only API adapters.

No SDK, network, credentials, system tools or execution live in this module.
Protocol continuation is returned to the caller for in-memory round trips only.
"""
from __future__ import annotations

import copy
import json
import re
import secrets
from urllib.parse import quote

CHECKED_AT = '2026-11-02'
PROTOCOLS = ('chat_completions', 'responses', 'anthropic', 'gemini')


def _preset(ident, name, base, protocol, models, source, note=''):
    return dict(id=ident, name=name, base_url=base, protocol=protocol, models=models,
                default_model=models[0] if models else '', checked_at=CHECKED_AT,
                sources=[source], note=note, verification='official_docs_and_local_protocol_tests',
                live_provider_tested=False)


PRESETS = [
    _preset('custom', '自定义兼容服务', '', 'chat_completions', [],
            'https://developers.openai.com/api/docs/guides/function-calling', '填写模型 ID；须通过工具往返测试确认兼容。'),
    _preset('openai', 'OpenAI · GPT', 'https://api.openai.com/v1', 'responses',
            ['gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-luna', 'gpt-6-astra'],
            'https://developers.openai.com/api/docs/models', 'GPT-6 工具调用使用 Responses；API 账户权益以官方规则为准。'),
    _preset('glm', 'GLM · 智谱', 'https://open.bigmodel.cn/api/paas/v4', 'chat_completions',
            ['glm-5.3-flash', 'glm-5.3'], 'https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3',
            '标准 API，非 Coding Plan 端点。5.3 系列必须保留思考工具续接。'),
    _preset('zai', 'GLM · Z.AI', 'https://api.z.ai/api/paas/v4', 'chat_completions',
            ['glm-5.3-flash', 'glm-5.3'], 'https://docs.z.ai/guides/develop/openai/python', 'Z.AI 与智谱的账户和密钥分别配置。'),
    _preset('deepseek', 'DeepSeek', 'https://api.deepseek.com', 'chat_completions',
            ['deepseek-flash', 'deepseek-v4-pro'], 'https://api-docs.deepseek.com/quick_start/agent_integrations/',
            'Flash 是官方当前别名；思考上下文仅在本轮内存中传回。思考模式的工具测试使用 auto，不发送不支持的 required。'),
    _preset('kimi_cn', 'Kimi · 国内', 'https://api.moonshot.cn/v1', 'chat_completions',
            ['kimi-k3.1-pro', 'kimi-k3.1-pro-highspeed'], 'https://platform.kimi.com/docs/get-api-key',
            'K3.1 Pro 使用 max_completion_tokens，不能关闭思考；需账户有对应模型权限。'),
    _preset('kimi_global', 'Kimi · 国际', 'https://api.moonshot.ai/v1', 'chat_completions',
            ['kimi-k3.1-pro', 'kimi-k3.1-pro-highspeed'], 'https://platform.kimi.ai/docs/get-your-api-key', '国际与国内地址及账户分别配置。'),
    _preset('qwen_cn', '通义千问 · 北京', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'chat_completions',
            ['qwen3.8-max'], 'https://help.aliyun.com/en/model-studio/base-url',
            '使用按量 API。官方仍支持此共享地址，可在高级设置换成工作空间专属地址；密钥需匹配地域。'),
    _preset('qwen_global', '通义千问 · 新加坡', 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', 'chat_completions',
            ['qwen3.8-max'], 'https://help.aliyun.com/en/model-studio/base-url', '使用对应地域的标准 API Key；可配置专属工作空间地址。'),
    _preset('doubao', '豆包 · 火山方舟', 'https://ark.cn-beijing.volces.com/api/v3', 'responses',
            ['doubao-seed-2-1-pro-260915'], 'https://console.volcengine.com/ark/region:cn-beijing/docs/product-overview',
            '标准数据面 API；使用自定义推理接入点时，将模型 ID 改为已开通的 Endpoint ID。'),
    _preset('minimax', 'MiniMax · 国际', 'https://api.minimax.io/anthropic', 'anthropic',
            ['MiniMax-M3.1-Flash'], 'https://platform.minimax.io/docs/api-reference/text-anthropic-api', '使用官方 Anthropic 兼容协议。'),
    _preset('hunyuan', '腾讯混元 · 旧平台', 'https://api.hunyuan.cloud.tencent.com/v1', 'chat_completions',
            ['hunyuan-turbos-latest'], 'https://cloud.tencent.com/document/product/1729/111007',
            '旧平台已不再新增模型服务；已有服务可用，新用户应根据 TokenHub 官方接入信息使用自定义地址。'),
    _preset('qianfan', '百度千帆', 'https://qianfan.baidubce.com/v2', 'chat_completions', [],
            'https://cloud.baidu.com/doc/qianfan-docs/s/Fm9l6ocai', '填写控制台已开通且支持工具调用的模型 ID，避免猜测账户可用模型。'),
    _preset('siliconflow', '硅基流动', 'https://api.siliconflow.cn/v1', 'chat_completions', [],
            'https://docs.siliconflow.cn/docs/userguide/quickstart', '填写控制台支持工具调用的完整模型 ID。不同模型参数和能力需测试。'),
    _preset('anthropic', 'Anthropic · Claude', 'https://api.anthropic.com/v1', 'anthropic',
            ['claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-opus-5-5'], 'https://platform.claude.com/docs/en/models/overview',
            '原生 Messages 协议，API 版本 2026-10-21；不是 Chat Completions 转发。'),
    _preset('gemini', 'Google · Gemini', 'https://generativelanguage.googleapis.com/v1beta', 'gemini',
            ['gemini-3.8-flash'], 'https://ai.google.dev/api/generate-content', '原生 generateContent，保留工具调用的 thought signature。'),
    _preset('grok', 'xAI · Grok', 'https://api.x.ai/v1', 'responses', ['grok-4.7'],
            'https://docs.x.ai/developers/grok-4-7', '仅开放盘序提供的函数，不自动开启厂商联网或代码执行。'),
    _preset('ollama', '本地 · Ollama / 兼容服务', 'http://127.0.0.1:11434/v1', 'chat_completions', [],
            'https://docs.ollama.com/api/openai-compatibility', '填写本机已经安装且支持工具调用的模型；程序不会自动下载模型。'),
]

# Additional, task-specific protocol references. The model catalogue alone
# does not establish stream/tool/thinking parameter compatibility.
_API_SOURCES = {
    'openai': ['https://developers.openai.com/api/docs/guides/function-calling', 'https://developers.openai.com/api/docs/guides/streaming-responses'],
    'glm': ['https://docs.bigmodel.cn/api-reference/模型-api/对话补全'],
    'zai': ['https://docs.z.ai/api-reference/llm/chat-completion'],
    'deepseek': ['https://api-docs.deepseek.com/api/create-chat-completion/', 'https://api-docs.deepseek.com/guides/thinking_mode/'],
    'kimi_cn': ['https://platform.kimi.com/docs/guide/kimi-k3-1-pro-quickstart', 'https://platform.kimi.com/docs/guide/use-tool-choice', 'https://platform.kimi.com/docs/api/chat'],
    'kimi_global': ['https://platform.kimi.ai/docs/guide/use-tool-choice', 'https://platform.kimi.ai/docs/guide/utilize-the-streaming-output-feature-of-kimi-api'],
    'qwen_cn': ['https://help.aliyun.com/en/model-studio/qwen-function-calling', 'https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions'],
    'qwen_global': ['https://help.aliyun.com/en/model-studio/qwen-function-calling', 'https://help.aliyun.com/en/model-studio/deep-thinking'],
    'doubao': ['https://console.volcengine.com/ark/region:cn-beijing/docs/create-model-responses-api', 'https://console.volcengine.com/ark/region:cn-beijing/docs/streaming-output'],
    'anthropic': ['https://platform.claude.com/docs/en/api/messages/create', 'https://platform.claude.com/docs/en/api/migration-2026-10-21', 'https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5', 'https://platform.claude.com/docs/en/models/opus-5-5/whats-new-opus-5-5'],
    'gemini': ['https://ai.google.dev/gemini-api/docs/generate-content/function-calling', 'https://ai.google.dev/gemini-api/docs/generate-content/thinking', 'https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures'],
    'grok': ['https://docs.x.ai/developers/model-capabilities/text/streaming', 'https://docs.x.ai/developers/model-capabilities/text/reasoning', 'https://docs.x.ai/developers/tools/function-calling'],
    'qianfan': ['https://cloud.baidu.com/doc/qianfan-docs/s/xm95lyys5'],
    'siliconflow': ['https://docs.siliconflow.cn/docs/api/chat-completions-post'],
}
for _row in PRESETS:
    _row['sources'].extend(_API_SOURCES.get(_row['id'], []))


def presets():
    rows = copy.deepcopy(PRESETS)
    for row in rows:
        row['reasoning_efforts'] = supported_efforts(dict(provider=row['id'], protocol=row['protocol'], model=row['default_model']))
        row['capabilities'] = capabilities(dict(provider=row['id'], protocol=row['protocol'], model=row['default_model']))
    return dict(presets=rows, protocols=list(PROTOCOLS), checked_at=CHECKED_AT)


def get_preset(ident):
    return next((copy.deepcopy(p) for p in PRESETS if p['id'] == ident), None)


def connection_probe_tool_choice(settings):
    """Use the same reasoning mode as normal work, with supported selection.

    Consult the documented per-preset capabilities: several current thinking
    models reject forced choice. An auto probe must still return an actual,
    locally checked tool call; text-only replies never count as success.
    """
    if 'required' not in capabilities(settings)['tool_choices']:
        return 'auto'
    return 'required'


def capabilities(settings):
    """Documented request choices, not an account/model availability promise.

    The registry describes the selected preset protocol only. A custom model
    on an aggregator must still pass the actual, nonce-checked tool round trip.
    """
    provider = settings.get('provider', 'custom')
    protocol = settings.get('protocol', 'chat_completions')
    model = settings.get('model', '').lower()
    choices = ['auto', 'none', 'required']
    note = '工具能力仍取决于所选模型和账户；本机夹具测试不代表云端实测。'
    if protocol == 'chat_completions':
        if provider in ('glm', 'zai'):
            choices = ['auto', 'none']
            note = '官方 Chat API 仅列 auto；禁用本轮工具时省略 tools/tool_choice，保留历史工具结果。'
        elif provider in ('deepseek', 'qwen_cn', 'qwen_global'):
            choices = ['auto', 'none']
            note = '思考模式不支持强制 required；工具测试使用 auto，仍必须返回正确工具和随机校验值。'
        elif provider in ('hunyuan', 'qianfan', 'siliconflow', 'ollama'):
            choices = ['auto', 'none']
            note = '跨模型的强制工具能力不统一；探测使用 auto，不能将文本回复当作工具兼容成功。'
    elif protocol == 'anthropic' and provider == 'anthropic' and model in ('claude-sonnet-5-5', 'claude-opus-5-5'):
        choices = ['auto', 'none']
        note = 'Sonnet/Opus 5.5 不支持强制 any/tool；保留默认思考和完整签名续接。'
    return dict(streaming=True, stream_transport='sse', tool_choices=choices,
                tool_choice_none='omit_definitions' if protocol == 'chat_completions' and provider in ('glm', 'zai', 'siliconflow') else 'native',
                documented_for_preset=provider != 'custom', live_provider_tested=False,
                checked_at=CHECKED_AT, note=note)


def connection_identity(settings):
    return [settings.get(k, '') for k in ('provider', 'protocol', 'base_url', 'model', 'reasoning_effort')]


def supported_efforts(settings):
    provider, model = settings.get('provider', 'custom'), settings.get('model', '')
    if settings.get('protocol') == 'gemini':
        return ['default', 'low', 'medium', 'high'] if model == 'gemini-3.8-flash' else ['default']
    if provider in ('glm', 'zai') and model.startswith('glm-5.3'):
        return ['default', 'low', 'high', 'max']
    if provider == 'deepseek' and model in ('deepseek-flash', 'deepseek-v4-pro'):
        return ['default', 'low', 'high', 'max']
    if provider in ('kimi_cn', 'kimi_global') and model.startswith('kimi-k3'):
        return ['default', 'low', 'high', 'max']
    if provider == 'anthropic' and model in ('claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-opus-5-5'):
        return ['default', 'low', 'medium', 'high', 'max']
    if provider == 'minimax' and model == 'MiniMax-M3.1-Flash':
        return ['default', 'low', 'medium', 'high', 'max']
    if provider == 'openai' and model in ('gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-luna', 'gpt-6-astra'):
        return ['default', 'low', 'medium', 'high', 'max']
    if provider == 'custom' and settings.get('protocol', 'chat_completions') in ('chat_completions', 'responses'):
        return ['default', 'low', 'medium', 'high', 'max']
    return ['default']


def _native(message, settings):
    continuation = message.get('_continuation')
    if continuation is None:
        return None
    if not isinstance(continuation, dict) or continuation.get('connection') != connection_identity(settings):
        raise ValueError('provider_configuration_changed')
    return copy.deepcopy(continuation.get('value'))


def _call(ident, name, arguments):
    return {'id': ident, 'type': 'function', 'function': {
        'name': name, 'arguments': arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)}}


def _tool_name(messages, ident):
    for message in messages:
        for call in message.get('tool_calls') or []:
            if call.get('id') == ident:
                return call['function']['name']
    raise ValueError('invalid_tool')


def _gemini_schema(value):
    if isinstance(value, list):
        return [_gemini_schema(v) for v in value]
    if not isinstance(value, dict):
        return value
    allowed = {'type', 'description', 'properties', 'required', 'items', 'enum', 'minimum', 'maximum', 'minItems', 'maxItems', 'nullable', 'anyOf'}
    result = {k: _gemini_schema(v) for k, v in value.items() if k in allowed}
    if 'properties' in value:
        result['properties'] = {k: _gemini_schema(v) for k, v in value['properties'].items()}
    if isinstance(result.get('type'), str):
        result['type'] = result['type'].upper()
    return result


def prepare(settings, payload, key):
    """Translate canonical messages/tools into one native request."""
    protocol, base = settings.get('protocol', 'chat_completions'), settings['base_url'].rstrip('/')
    messages, tools = payload['messages'], payload.get('tools', [])
    choice, budget = payload.get('tool_choice', 'auto'), payload.get('max_tokens', 8192)
    stream = payload.get('stream', False)
    if not isinstance(stream, bool):
        raise ValueError('provider_parameters')
    if choice not in ('auto', 'none', 'required'):
        raise ValueError('provider_parameters')
    if any(not isinstance(m, dict) or m.get('role') not in ('system', 'user', 'assistant', 'tool') for m in messages):
        raise ValueError('invalid_message_role')
    if tools and choice not in capabilities(settings)['tool_choices']:
        raise ValueError('provider_parameters')
    effort = settings.get('reasoning_effort', 'default')
    headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream' if stream else 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    if protocol == 'chat_completions':
        # provider/protocol/base_url and other connection metadata are local
        # settings, never Chat roles or request fields. Build a bounded wire
        # schema rather than forwarding arbitrary internal payload fields.
        outgoing = {k: copy.deepcopy(payload[k]) for k in
                    ('messages', 'tools', 'tool_choice', 'max_tokens', 'response_format') if k in payload}
        outgoing.update(model=settings['model'], stream=stream)
        message_fields = ('role', 'content', 'name', 'tool_calls', 'tool_call_id', 'function_call')
        outgoing['messages'] = [_native(m, settings) or {k: copy.deepcopy(m[k]) for k in message_fields if k in m} for m in messages]
        if capabilities(settings)['tool_choice_none'] == 'omit_definitions' and choice == 'none':
            outgoing.pop('tools', None)
            outgoing.pop('tool_choice', None)
        model = settings['model'].lower()
        if settings.get('provider') in ('glm', 'zai') and model.startswith('glm-5.3'):
            outgoing.update(thinking={'type': 'enabled', 'clear_thinking': False}, reasoning_effort='high' if effort == 'default' else ('high' if effort == 'medium' else effort))
        elif settings.get('provider') == 'deepseek':
            outgoing.update(thinking={'type': 'enabled'}, reasoning_effort='high' if effort == 'default' else ('high' if effort == 'medium' else effort))
        elif settings.get('provider') in ('kimi_cn', 'kimi_global') and model.startswith('kimi-k3'):
            outgoing['max_completion_tokens'] = outgoing.pop('max_tokens')
            outgoing['reasoning_effort'] = 'high' if effort == 'default' else ('high' if effort == 'medium' else effort)
        elif effort != 'default' and settings.get('provider') == 'custom':
            # Explicit custom settings, not an assumption based on a model name.
            outgoing['reasoning_effort'] = effort
        return (base if base.endswith('/chat/completions') else base + '/chat/completions'), outgoing, headers
    if protocol == 'responses':
        inputs = []
        for m in messages:
            native = _native(m, settings)
            if native is not None:
                if not isinstance(native, list):
                    raise ValueError('invalid_response')
                inputs.extend(native)
            elif m['role'] == 'tool':
                inputs.append({'type': 'function_call_output', 'call_id': m['tool_call_id'], 'output': m['content']})
            else:
                if m.get('content'):
                    inputs.append({'role': m['role'], 'content': m['content']})
                for c in m.get('tool_calls') or []:
                    inputs.append({'type': 'function_call', 'call_id': c['id'], 'name': c['function']['name'], 'arguments': c['function']['arguments']})
        outgoing = dict(model=settings['model'], input=inputs, max_output_tokens=budget, stream=stream, store=False)
        if tools:
            outgoing['tools'] = [dict(type='function', name=t['function']['name'], description=t['function'].get('description', ''),
                                      parameters=t['function']['parameters'], strict=False) for t in tools]
            if settings.get('provider') in ('doubao', 'grok'):
                # Ark Responses lists name/description/parameters; xAI's
                # tools are implicitly strict. Do not send an OpenAI-specific
                # opt-out flag as an unverified cross-vendor parameter.
                for tool in outgoing['tools']:
                    tool.pop('strict', None)
            outgoing['tool_choice'] = choice
        if effort != 'default':
            outgoing['reasoning'] = {'effort': effort}
        if payload.get('response_format'):
            outgoing['text'] = {'format': payload['response_format']}
        # Stateless OpenAI reasoning continuity is carried via encrypted items.
        if settings.get('provider') in ('openai', 'grok'):
            outgoing['include'] = ['reasoning.encrypted_content']
        return (base if base.endswith('/responses') else base + '/responses'), outgoing, headers
    if protocol == 'anthropic':
        system, converted = [], []
        for m in messages:
            if m['role'] == 'system':
                system.append(m.get('content', ''))
                continue
            native = _native(m, settings)
            if native is not None:
                converted.append({'role': 'assistant', 'content': native})
                continue
            if m['role'] == 'tool':
                row = {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': m['tool_call_id'], 'content': m['content']}]}
                if converted and converted[-1]['role'] == 'user' and isinstance(converted[-1]['content'], list):
                    converted[-1]['content'].extend(row['content'])
                else:
                    converted.append(row)
                continue
            blocks = [{'type': 'text', 'text': m['content']}] if m.get('content') else []
            for c in m.get('tool_calls') or []:
                blocks.append(dict(type='tool_use', id=c['id'], name=c['function']['name'], input=json.loads(c['function']['arguments'])))
            converted.append({'role': m['role'], 'content': blocks})
        outgoing = dict(model=settings['model'], system='\n\n'.join(system), messages=converted, max_tokens=budget, stream=stream)
        if tools:
            outgoing['tools'] = [dict(name=t['function']['name'], description=t['function'].get('description', ''), input_schema=t['function']['parameters']) for t in tools]
            outgoing['tool_choice'] = {'type': {'auto': 'auto', 'required': 'any', 'none': 'none'}[choice]}
        if effort != 'default':
            outgoing['output_config'] = {'effort': effort}
        headers['anthropic-version'] = '2026-10-21' if settings.get('provider') == 'anthropic' else '2023-06-01'
        if base.endswith('/messages'):
            endpoint = base
        else:
            endpoint = base + ('/messages' if base.endswith('/v1') else '/v1/messages')
        return endpoint, outgoing, headers
    if protocol == 'gemini':
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', settings['model']):
            raise ValueError('invalid_model_id')
        headers.pop('Authorization', None)
        if key:
            headers['x-goog-api-key'] = key
        system, contents = [], []
        for m in messages:
            if m['role'] == 'system':
                system.append({'text': m.get('content', '')})
                continue
            native = _native(m, settings)
            if native is not None:
                contents.append(native)
                continue
            if m['role'] == 'tool':
                name = _tool_name(messages, m['tool_call_id'])
                try:
                    result = json.loads(m['content'])
                except ValueError:
                    result = {'output': m['content']}
                if not isinstance(result, dict):
                    result = {'output': result}
                response = {'name': name, 'response': result}
                # Preserve the native call ID only when the model emitted one.
                for previous in contents:
                    for part in previous.get('parts', []):
                        call = part.get('functionCall', {})
                        if call.get('id') == m['tool_call_id']:
                            response['id'] = call['id']
                part = {'functionResponse': response}
                if contents and contents[-1]['role'] == 'user':
                    contents[-1]['parts'].append(part)
                else:
                    contents.append({'role': 'user', 'parts': [part]})
                continue
            parts = [{'text': m['content']}] if m.get('content') else []
            for c in m.get('tool_calls') or []:
                parts.append({'functionCall': {'name': c['function']['name'], 'args': json.loads(c['function']['arguments'])}})
            contents.append({'role': 'model' if m['role'] == 'assistant' else 'user', 'parts': parts})
        outgoing = dict(contents=contents, systemInstruction={'parts': system}, generationConfig={'maxOutputTokens': budget})
        if tools:
            outgoing['tools'] = [{'functionDeclarations': [dict(name=t['function']['name'], description=t['function'].get('description', ''), parameters=_gemini_schema(t['function']['parameters'])) for t in tools]}]
            outgoing['toolConfig'] = {'functionCallingConfig': {'mode': {'auto': 'AUTO', 'required': 'ANY', 'none': 'NONE'}[choice]}}
        if payload.get('response_format'):
            outgoing['generationConfig']['responseMimeType'] = 'application/json'
        if effort != 'default':
            if settings['model'] != 'gemini-3.8-flash' or effort not in ('low', 'medium', 'high'):
                raise ValueError('unsupported_reasoning_effort')
            outgoing['generationConfig']['thinkingConfig'] = {'thinkingLevel': effort}
        action = ':streamGenerateContent?alt=sse' if stream else ':generateContent'
        return base + '/models/' + quote(settings['model'], safe='') + action, outgoing, headers
    raise ValueError('unsupported_protocol')


def normalize(settings, response):
    """One canonical assistant message, plus opaque native continuation."""
    protocol = settings.get('protocol', 'chat_completions')
    if not isinstance(response, dict):
        raise ValueError('invalid_response')
    finish_reason = response.get('stop_reason') or response.get('status')
    if protocol == 'chat_completions':
        try:
            raw = response['choices'][0]['message']
        except (TypeError, KeyError, IndexError):
            raise ValueError('invalid_response') from None
        if not isinstance(raw, dict):
            raise ValueError('invalid_response')
        result = {k: copy.deepcopy(raw[k]) for k in ('role', 'content', 'tool_calls', 'function_call') if k in raw}
        native = copy.deepcopy(raw)
        finish_reason = response['choices'][0].get('finish_reason')
    elif protocol == 'responses':
        output = response.get('output')
        if not isinstance(output, list):
            raise ValueError('invalid_response')
        content, calls = [], []
        for item in output:
            if not isinstance(item, dict):
                raise ValueError('invalid_response')
            if item.get('type') == 'message':
                blocks = item.get('content', [])
                if not isinstance(blocks, list) or any(not isinstance(p, dict) for p in blocks):
                    raise ValueError('invalid_response')
                content.extend(p.get('text', '') for p in blocks if p.get('type') == 'output_text')
            elif item.get('type') == 'function_call':
                calls.append(_call(item.get('call_id'), item.get('name'), item.get('arguments')))
            elif item.get('type') != 'reasoning':
                raise ValueError('invalid_response')
        result = dict(role='assistant', content='\n'.join(content) or None, tool_calls=calls)
        native = copy.deepcopy(output)
    elif protocol == 'anthropic':
        blocks = response.get('content')
        if not isinstance(blocks, list):
            raise ValueError('invalid_response')
        text, calls = [], []
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError('invalid_response')
            if block.get('type') == 'text':
                text.append(block.get('text', ''))
            elif block.get('type') == 'tool_use':
                calls.append(_call(block.get('id'), block.get('name'), block.get('input')))
            elif block.get('type') not in ('thinking', 'redacted_thinking'):
                raise ValueError('invalid_response')
        result = dict(role='assistant', content='\n'.join(text) or None, tool_calls=calls)
        native = copy.deepcopy(blocks)
    elif protocol == 'gemini':
        try:
            native = copy.deepcopy(response['candidates'][0]['content'])
            parts = native['parts']
        except (TypeError, KeyError, IndexError):
            raise ValueError('invalid_response') from None
        if not isinstance(parts, list) or any(not isinstance(p, dict) for p in parts):
            raise ValueError('invalid_response')
        finish_reason = response['candidates'][0].get('finishReason')
        text, calls = [], []
        for index, part in enumerate(parts):
            if part.get('text') and part.get('thought') is not True:
                text.append(part['text'])
            if 'functionCall' in part:
                c = part['functionCall']
                if not isinstance(c, dict):
                    raise ValueError('invalid_response')
                calls.append(_call(c.get('id') or f'gemini-{secrets.token_hex(8)}-{index}', c.get('name'), c.get('args', {})))
        result = dict(role='assistant', content='\n'.join(text) or None, tool_calls=calls)
    else:
        raise ValueError('unsupported_protocol')
    result['_continuation'] = dict(connection=connection_identity(settings), value=native)
    return {'choices': [{'message': result}], 'usage': copy.deepcopy(response.get('usage') or response.get('usageMetadata') or {}),
            'finish_reason': finish_reason}
