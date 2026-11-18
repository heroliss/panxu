"""Minimal LLM client: OpenAI-compatible Chat Completions and Anthropic Messages.

Standard library only (urllib). The program asks for one JSON answer (or one
plain-text answer, for the advisor) per request; there are no tool calls or
agent loops.
"""
from __future__ import annotations

import http.client
import json
import re
import ssl
import urllib.error
import urllib.request

from . import __version__

PRESETS = [
    # DeepSeek (2026-10): deepseek-flash = latest V4.1 Flash, deepseek-v4-pro. Thinking is on by default
    # and is switched off for classification requests (faster, cheaper, no max_tokens truncation).
    {'id': 'deepseek', 'name': 'DeepSeek', 'base_url': 'https://api.deepseek.com', 'protocol': 'openai',
     'models': ['deepseek-flash', 'deepseek-v4-pro'], 'json_mode': True, 'thinking_toggle': True,
     'key_url': 'https://platform.deepseek.com/api_keys'},
    {'id': 'kimi', 'name': 'Kimi（月之暗面）', 'base_url': 'https://api.moonshot.cn/v1', 'protocol': 'openai',
     'models': [], 'json_mode': True, 'key_url': 'https://platform.moonshot.cn/console/api-keys'},
    {'id': 'glm', 'name': '智谱 GLM', 'base_url': 'https://open.bigmodel.cn/api/paas/v4', 'protocol': 'openai',
     'models': [], 'json_mode': True, 'key_url': 'https://open.bigmodel.cn/usercenter/apikeys'},
    {'id': 'qwen', 'name': '通义千问（阿里云百炼）', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
     'protocol': 'openai', 'models': [], 'json_mode': True, 'key_url': 'https://bailian.console.aliyun.com/'},
    {'id': 'siliconflow', 'name': '硅基流动', 'base_url': 'https://api.siliconflow.cn/v1', 'protocol': 'openai',
     'models': [], 'json_mode': False, 'key_url': 'https://cloud.siliconflow.cn/account/ak'},
    {'id': 'openai', 'name': 'OpenAI', 'base_url': 'https://api.openai.com/v1', 'protocol': 'openai',
     'models': [], 'json_mode': True, 'key_url': 'https://platform.openai.com/api-keys'},
    {'id': 'anthropic', 'name': 'Anthropic Claude', 'base_url': 'https://api.anthropic.com/v1', 'protocol': 'anthropic',
     'models': [], 'json_mode': False, 'key_url': 'https://console.anthropic.com/settings/keys'},
    {'id': 'ollama', 'name': '本地 Ollama', 'base_url': 'http://127.0.0.1:11434/v1', 'protocol': 'openai',
     'models': [], 'json_mode': True, 'needs_key': False},
    {'id': 'custom', 'name': '自定义（OpenAI 兼容接口）', 'base_url': '', 'protocol': 'openai', 'models': [],
     'json_mode': False},
]
PRESET_BY_ID = {p['id']: p for p in PRESETS}


class AIError(Exception):
    pass


class Usage:
    def __init__(self):
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, usage: dict | None):
        self.requests += 1
        if usage:
            self.input_tokens += int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0)
            self.output_tokens += int(usage.get('completion_tokens') or usage.get('output_tokens') or 0)

    def as_dict(self):
        return {'requests': self.requests, 'input_tokens': self.input_tokens, 'output_tokens': self.output_tokens}


def _friendly_http_error(code: int, body: str) -> str:
    detail = ''
    try:
        data = json.loads(body)
        err = data.get('error', data)
        detail = err.get('message') if isinstance(err, dict) else str(err)
    except (ValueError, AttributeError):
        detail = body[:200]
    hints = {401: 'API Key 无效或已过期', 402: '账户余额不足', 403: '没有访问该模型的权限',
             404: '接口地址或模型名称不正确', 429: '请求太频繁或额度已用完，请稍后再试'}
    if code in hints:
        has_cn = any('\u4e00' <= ch <= '\u9fff' for ch in (detail or ''))
        return f'{hints[code]}（{detail}）' if has_cn else hints[code]
    hint = f'服务返回错误 {code}'
    return f'{hint}（{detail}）' if detail else hint


def _friendly_net_error(reason) -> str:
    if isinstance(reason, TimeoutError):
        return '模型服务响应超时'
    text = str(reason or '')
    low = text.lower()
    if 'timed out' in low or 'timeout' in low:
        return '模型服务响应超时'
    if 'getaddrinfo failed' in low or 'name or service not known' in low or '11001' in low:
        return '无法解析服务器地址，请检查网络连接或接口地址是否正确'
    if 'connection refused' in low or '10061' in low:
        return '服务器拒绝连接（若使用本地服务，请确认它已启动）'
    if 'connection reset' in low or '10054' in low or 'eof occurred' in low:
        return '与模型服务的网络连接中断，请稍后重试'
    if 'certificate_verify_failed' in low or 'ssl' in low:
        return '安全连接（SSL）证书校验失败，请检查网络环境或接口地址'
    if any('\u4e00' <= ch <= '\u9fff' for ch in text):
        return f'无法连接模型服务：{text}'
    return '无法连接模型服务，请检查网络连接与接口地址'


class Client:
    # request fields that some providers reject; dropped (once) when the service answers 400
    OPTIONAL_FIELDS = ('response_format', 'thinking', 'reasoning_effort', 'temperature')

    def __init__(self, base_url: str, model: str, api_key: str = '', protocol: str = 'openai',
                 json_mode: bool = False, timeout: float = 120, max_tokens: int = 4096,
                 thinking: str = '', temperature: float | None = None):
        if not base_url:
            raise AIError('请先在设置中填写模型服务地址')
        if not model:
            raise AIError('请先在设置中选择模型')
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.api_key = api_key
        self.protocol = protocol
        self.json_mode = json_mode
        self.timeout = timeout
        self.max_tokens = max_tokens
        # '' = provider default; 'off' | 'low' | 'high' | 'max' for services with a thinking switch
        self.thinking = thinking
        self.temperature = temperature
        self._dropped: set[str] = set()
        self._use_completion_tokens = False
        self.usage = Usage()

    # -- transport ---------------------------------------------------------------
    def _request(self, method: str, url: str, body: dict | None = None) -> dict:
        if self.api_key and not self.api_key.isascii():
            raise AIError('API Key 包含无效字符（如空格或中文符号），请重新粘贴')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'User-Agent': f'PanXu/{__version__}'}
        if self.protocol == 'anthropic':
            headers['x-api-key'] = self.api_key
            headers['anthropic-version'] = '2023-06-01'
        elif self.api_key:
            headers['Authorization'] = 'Bearer ' + self.api_key
        data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ssl.create_default_context()) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            try:
                body_text = exc.read().decode('utf-8', 'replace')
            finally:
                exc.close()
            err = AIError(_friendly_http_error(exc.code, body_text))
            err.status = exc.code
            raise err from None
        except urllib.error.URLError as exc:
            raise AIError(_friendly_net_error(exc.reason)) from None
        except TimeoutError:
            raise AIError('模型服务响应超时') from None
        except (ValueError, UnicodeDecodeError):
            raise AIError('模型服务返回了无法解析的内容') from None
        except (http.client.HTTPException, ssl.SSLError, OSError) as exc:
            raise AIError(_friendly_net_error(exc)) from None

    def list_models(self) -> list[str]:
        data = self._request('GET', self.base_url + '/models')
        rows = data.get('data') or data.get('models') or []
        ids = [r.get('id') or r.get('name') for r in rows if isinstance(r, dict)]
        return sorted({i for i in ids if i})

    def _openai_body(self, system: str, messages: list[dict], want_json: bool) -> dict:
        token_key = 'max_completion_tokens' if self._use_completion_tokens else 'max_tokens'
        body = {'model': self.model, token_key: self.max_tokens,
                'messages': [{'role': 'system', 'content': system}] + messages}
        if want_json and self.json_mode:
            body['response_format'] = {'type': 'json_object'}
        if self.thinking == 'off':
            body['thinking'] = {'type': 'disabled'}
        elif self.thinking in ('low', 'high', 'max'):
            body['thinking'] = {'type': 'enabled'}
            body['reasoning_effort'] = self.thinking
        if self.temperature is not None:
            body['temperature'] = self.temperature
        for name in self._dropped:
            body.pop(name, None)
        return body

    def chat(self, system: str, messages: list[dict], want_json: bool = False) -> str:
        """One request with a conversation (``[{'role': 'user'|'assistant', 'content': str}]``)."""
        raw_msgs = [{'role': m['role'], 'content': str(m['content'])} for m in messages
                    if m.get('role') in ('user', 'assistant') and m.get('content')]
        while raw_msgs and raw_msgs[0]['role'] != 'user':
            raw_msgs.pop(0)
        merged: list[dict] = []
        for m in raw_msgs:
            if merged and merged[-1]['role'] == m['role']:
                merged[-1]['content'] += '\n\n' + m['content']
            else:
                merged.append(m)
        messages = merged
        if not messages or messages[-1]['role'] != 'user':
            raise AIError('没有要发送的问题')
        if self.protocol == 'anthropic':
            body = {'model': self.model, 'max_tokens': self.max_tokens, 'system': system, 'messages': messages}
            if self.temperature is not None:
                body['temperature'] = self.temperature
            data = self._request('POST', self.base_url + '/messages', body)
            self.usage.add(data.get('usage'))
            parts = [c.get('text', '') for c in data.get('content', []) if c.get('type') == 'text']
            content = ''.join(parts)
            if not content.strip() and data.get('stop_reason') == 'max_tokens':
                raise AIError('模型的回答被截断（思考内容过长）。可以在设置中关闭「深度思考」后重试')
            return content
        body = self._openai_body(system, messages, want_json)
        try:
            data = self._request('POST', self.base_url + '/chat/completions', body)
        except AIError as exc:
            optional = [k for k in self.OPTIONAL_FIELDS if k in body]
            msg_low = str(exc).lower()
            retried = False
            if getattr(exc, 'status', None) == 400:
                if not self._use_completion_tokens and 'max_completion_tokens' in msg_low:
                    self._use_completion_tokens = True
                    retried = True
                if optional:
                    # the service rejected an optional field (JSON mode, thinking switch, ...): retry plainly
                    self._dropped.update(optional)
                    if 'response_format' in optional:
                        self.json_mode = False
                    retried = True
            if retried:
                data = self._request('POST', self.base_url + '/chat/completions',
                                     self._openai_body(system, messages, want_json))
            else:
                raise
        self.usage.add(data.get('usage'))
        try:
            message = data['choices'][0]['message']
            content = message.get('content') or ''
        except (KeyError, IndexError, TypeError):
            raise AIError('模型服务返回的格式不正确') from None
        if not content.strip() and data['choices'][0].get('finish_reason') == 'length':
            raise AIError('模型的回答被截断（思考内容过长）。可以在设置中关闭「深度思考」后重试')
        return content

    def complete(self, system: str, user: str, want_json: bool = False) -> str:
        return self.chat(system, [{'role': 'user', 'content': user}], want_json=want_json)

    def complete_json(self, system: str, user: str) -> dict:
        text = self.complete(system, user, want_json=True)
        try:
            return extract_json(text)
        except ValueError:
            # one corrective retry, then give up
            text = self.complete(system, user + '\n\n上一次的回答不是有效 JSON。请只输出 JSON 对象，不要有其他文字。',
                                 want_json=True)
            return extract_json(text)


_FENCE = re.compile(r'```(?:json)?\s*(.*?)```', re.S | re.I)
_THINK = re.compile(r'<think>.*?(?:</think>|$)', re.S | re.I)


def strip_thinking(text: str) -> str:
    """Remove inline <think>...</think> blocks emitted by reasoning models."""
    return _THINK.sub('', text or '').strip()


def _find_json_dict(candidate: str) -> dict | None:
    pos = candidate.find('{')
    while pos >= 0:
        depth, in_str, escape = 0, False, False
        end = -1
        for i in range(pos, len(candidate)):
            ch = candidate[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > pos:
            try:
                value = json.loads(candidate[pos:end])
                if isinstance(value, dict):
                    return value
            except ValueError:
                pass
        pos = candidate.find('{', pos + 1)
    return None


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a model reply (tolerates <think>, code fences and prose)."""
    cleaned = strip_thinking(text)
    if not cleaned:
        raise ValueError('模型返回了空内容')
    for m in _FENCE.finditer(cleaned):
        found = _find_json_dict(m.group(1))
        if found is not None:
            return found
    found = _find_json_dict(cleaned)
    if found is not None:
        return found
    raise ValueError('模型未返回有效的结构化结果')


def client_from_settings(ai: dict, api_key: str, purpose: str = 'classify') -> Client:
    """``purpose``: 'classify' (JSON verdicts, thinking off where possible) or 'advise' (free text)."""
    preset = PRESET_BY_ID.get(ai.get('preset'), {})
    if preset.get('needs_key', True) and not api_key:
        raise AIError('请先在设置中填写 API Key')
    thinking = ''
    if preset.get('thinking_toggle'):
        # verdicts never think: slower, dearer, and long reasoning can use up max_tokens (empty answer)
        thinking = 'off' if purpose == 'classify' else str(ai.get('thinking') or 'off')
    return Client(ai.get('base_url') or preset.get('base_url', ''), ai.get('model', ''), api_key,
                  ai.get('protocol') or preset.get('protocol', 'openai'), bool(ai.get('json_mode')),
                  float(ai.get('timeout') or 120), thinking=thinking,
                  temperature=0.2 if purpose == 'classify' else None)
