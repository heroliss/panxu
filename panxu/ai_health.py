"""Non-blocking model connectivity, independent of file scans and cleanup jobs."""
from __future__ import annotations

import hashlib
import json
import threading
import time

from .ai_client import AIError, PRESET_BY_ID

NOTICE_SECONDS = 24 * 3600


class AIHealth:
    def __init__(self, settings, probe, clock=time.time):
        self.settings, self.probe, self.clock = settings, probe, clock
        self._lock = threading.RLock()
        self._signature = ''
        self._state = {}
        self._running = False
        self._pending = None
        self._active = None
        self._result = None
        self._done = threading.Condition(self._lock)

    def configuration(self):
        ai = self.settings.get()['ai']
        key = self.settings.get_secret('ai_key:' + ai['preset'])
        signature = self.signature(ai, key)
        missing = []
        if not str(ai.get('base_url') or '').strip():
            missing.append('服务地址')
        if not str(ai.get('model') or '').strip():
            missing.append('模型名称')
        if PRESET_BY_ID.get(ai['preset'], {}).get('needs_key', True) and not key.strip():
            missing.append('API Key')
        return signature, ai, key, missing

    @staticmethod
    def signature(ai, key):
        fields = {k: ai.get(k) for k in ('preset', 'base_url', 'model', 'protocol', 'json_mode')}
        return hashlib.sha256(json.dumps([fields, key], sort_keys=True).encode()).hexdigest()

    def _sync(self, config):
        signature, ai, _, missing = config
        if signature != self._signature:
            self._signature = signature
            self._state = {'state': 'unconfigured' if missing else 'untested', 'checked_at': None,
                           'message': '请填写 ' + '、'.join(missing) if missing else '当前模型连接尚未验证',
                           'help': '基础扫描仍可使用；配置并测试模型后，才能使用 AI 分析和问 AI。',
                           'model': ai.get('model', ''), 'preset': ai['preset'], 'signature': signature}

    def status(self):
        config = self.configuration()
        with self._lock:
            self._sync(config)
            s = dict(self._state)
        notice = self.settings.get().get('ai_status_notice', {})
        acknowledged = (notice.get('signature') == s['signature'] and notice.get('state') == s['state']
                        and notice.get('message') == s['message']
                        and 0 <= self.clock() - float(notice.get('time') or 0) < NOTICE_SECONDS)
        s['notice_due'] = s['state'] in ('unconfigured', 'error') and not acknowledged
        return s

    def acknowledge(self, signature):
        state = self.status()
        if state['signature'] == signature:
            self.settings.update({'ai_status_notice': {'signature': signature, 'state': state['state'], 'message': state['message'], 'time': self.clock()}})
        return self.status()

    @staticmethod
    def error_text(exc):
        # Provider errors sometimes echo credentials. Publish our own explanations, never raw bodies.
        code = getattr(exc, 'status', None)
        errors = {401: ('API Key 无效或已过期', '在设置中更新 API Key，再测试连接。'),
                  402: ('模型账户余额不足', '检查服务商账户余额或更换模型服务。'),
                  403: ('没有访问该模型的权限', '检查 Key 的权限，以及所选模型是否已开通。'),
                  404: ('接口地址或模型名称不正确', '核对接口地址和模型名称，再测试连接。'),
                  429: ('模型服务限流或额度已用完', '稍后重新检测，或检查服务商的额度限制。')}
        if code in errors:
            return errors[code]
        if '超时' in str(exc) or isinstance(exc, TimeoutError):
            return '模型连接超时', '检查网络与服务地址，稍后重新检测；基础扫描仍可使用。'
        return '模型暂时无法使用', '请检查网络、服务地址、模型名称和 API Key，再测试连接。'

    def record(self, signature, error=None):
        config = self.configuration()
        with self._lock:
            self._sync(config)
            if signature != self._signature:
                return  # an old request must not certify different settings
            message, help_text = self.error_text(error) if error else ('模型连接正常', 'AI 分析与问 AI 可以使用。')
            self._state.update(state='error' if error else 'ready', message=message, help=help_text, checked_at=self.clock())

    def check(self, force=False):
        config = self.configuration()
        with self._lock:
            self._sync(config)
            if config[3] or (not force and self._state['state'] == 'ready'):
                return self.status()
            if self._running and self._active == config[0] and self._pending is None:
                return self.status()
            self._pending = config
            self._state.update(state='checking', message='正在检测模型连接', help='仅发送固定测试消息，不包含文件名或文件内容。')
            if not self._running:
                self._running = True
                threading.Thread(target=self._run, daemon=True, name='ai-connection-check').start()
        return self.status()

    def _run(self):
        while True:
            with self._lock:
                config, self._pending = self._pending, None
                self._active = config[0]
            error = None
            try:
                result = self.probe(config[1], config[2])
            except Exception as exc:  # a transport failure must not stop the app or reveal raw data
                error = exc
            self.record(config[0], error)
            with self._lock:
                if not error and self._signature == config[0]:
                    self._result = result
                if self._pending is None:
                    self._running, self._active = False, None
                    self._done.notify_all()
                    return

    def test(self):
        self.check(force=True)
        signature = self.configuration()[0]
        with self._done:
            if not self._done.wait_for(lambda: not self._running, timeout=45):
                raise AIError('连接测试仍在进行，请稍后查看状态')
        state = self.status()
        if state['signature'] != signature or state['state'] != 'ready':
            raise AIError(state['message'])
        return self._result or {'model': state['model'], 'reply': {'ok': True}}

    def observe(self, client, signature=None):
        health, signature = self, signature or self.configuration()[0]

        class ObservedClient:
            def __getattr__(self, name):
                attr = getattr(client, name)
                if name not in ('chat', 'complete', 'complete_json'):
                    return attr
                def call(*args, **kwargs):
                    try:
                        result = attr(*args, **kwargs)
                    except (AIError, ValueError) as exc:
                        health.record(signature, exc)
                        raise
                    health.record(signature)
                    return result
                return call
        return ObservedClient()
