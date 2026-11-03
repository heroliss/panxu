"""User settings (JSON) and the API key store (DPAPI-encrypted on Windows)."""
from __future__ import annotations

import base64
import copy
import json
import logging
import os
import threading

from . import winapi

log = logging.getLogger(__name__)

DEFAULTS = {
    'theme': 'system',  # system | light | dark
    'ai': {
        'preset': 'deepseek',
        'base_url': 'https://api.deepseek.com',
        'model': 'deepseek-flash',
        'protocol': 'openai',  # openai | anthropic
        'json_mode': True,
        'thinking': 'off',       # off | low | high (only for services with a thinking switch)
        'anonymize_paths': True,
        'send_file_content': False,  # allow sending the first lines of documents (asked again each time)
        'max_requests': 8,       # per analysis run
        'min_dir_mb': 500,       # directories smaller than this are not sent
        'timeout': 120,
    },
    'checkup': {
        'space_scan': True,      # scan the system drive during the one-click check-up
        'duplicates': True,
        'use_ai': True,          # ask the model when a key is configured
    },
    'insights': {
        'stale_days': 365,       # "很久没动" threshold for large personal files
        'large_mb': 100,
        'download_days': 180,    # downloads older than this are worth a look
    },
    'duplicates': {'min_size_mb': 1},
    'large_files': {'min_size_mb': 100},
    'excluded_paths': [],
    'extra_personal_paths': [],  # more folders for 「我的文件」 (e.g. D:\\资料)
}

# model ids that providers have retired -> replacement
_RETIRED_MODELS = {('deepseek', 'deepseek-chat'): 'deepseek-flash', ('deepseek', 'deepseek-reasoner'): 'deepseek-flash',
                   ('deepseek', 'deepseek-v4-flash'): 'deepseek-flash'}
# 0.x provider ids -> 1.x presets
_LEGACY_PROVIDERS = {'deepseek': 'deepseek', 'kimi_cn': 'kimi', 'kimi': 'kimi', 'glm': 'glm', 'zhipu': 'glm',
                     'qwen': 'qwen', 'dashscope': 'qwen', 'siliconflow': 'siliconflow', 'openai': 'openai',
                     'anthropic': 'anthropic', 'ollama': 'ollama'}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


class Settings:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, 'settings.json')
        self.secret_path = os.path.join(data_dir, 'secrets.json')
        self._lock = threading.Lock()
        os.makedirs(data_dir, exist_ok=True)
        self._data = _merge(DEFAULTS, self._read(self.path))
        ai = self._data['ai']
        replacement = _RETIRED_MODELS.get((ai.get('preset'), ai.get('model')))
        if replacement:
            ai['model'] = replacement

    @staticmethod
    def _read(path):
        try:
            with open(path, encoding='utf-8') as fh:
                value = json.load(fh)
                return value if isinstance(value, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write(self, path, value):
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, changes: dict) -> dict:
        with self._lock:
            self._data = _merge(self._data, changes)
            self._data['excluded_paths'] = [str(p) for p in self._data.get('excluded_paths', []) if str(p).strip()]
            self._write(self.path, self._data)
            return copy.deepcopy(self._data)

    # -- secrets -----------------------------------------------------------
    def set_secret(self, name: str, value: str) -> None:
        with self._lock:
            store = self._read(self.secret_path)
            if value:
                blob, scheme = winapi.protect_secret(value.encode('utf-8'))
                store[name] = {'scheme': scheme, 'data': base64.b64encode(blob).decode('ascii')}
            else:
                store.pop(name, None)
            self._write(self.secret_path, store)

    def get_secret(self, name: str) -> str:
        with self._lock:
            row = self._read(self.secret_path).get(name)
        if not row:
            return ''
        try:
            return winapi.unprotect_secret(base64.b64decode(row['data']), row['scheme']).decode('utf-8')
        except (ValueError, OSError, KeyError):
            return ''

    def has_secret(self, name: str) -> bool:
        return bool(self.get_secret(name))

    # -- migration from 0.x --------------------------------------------------------
    def import_legacy_key(self, candidates: list[str]) -> str | None:
        """Import the API key saved by PanXu 0.x (model_settings.json, DPAPI, same Windows user).

        Only runs when the matching 1.x preset has no key yet. Returns the preset id
        that received a key, or None. Nothing is sent anywhere.
        """
        for path in candidates:
            try:
                if not path or not os.path.isfile(path) or os.path.getsize(path) > 64 * 1024:
                    continue
                with open(path, encoding='utf-8') as fh:
                    old = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(old, dict) or not isinstance(old.get('key_dpapi'), str):
                continue
            preset = _LEGACY_PROVIDERS.get(str(old.get('provider') or '').lower(), 'custom')
            if self.has_secret('ai_key:' + preset):
                continue
            try:
                key = winapi.unprotect_secret(base64.b64decode(old['key_dpapi'], validate=True), 'dpapi').decode('utf-8')
            except (ValueError, OSError, UnicodeDecodeError):
                continue  # another Windows user, or not on Windows
            if not key.strip():
                continue
            self.set_secret('ai_key:' + preset, key.strip())
            changes = {}
            current = self.get()['ai']
            if current.get('preset') == preset or not self.has_secret('ai_key:' + current.get('preset', '')):
                from .ai_client import PRESET_BY_ID  # local import: settings must not depend on the client at import time
                info = PRESET_BY_ID.get(preset, {})
                model = str(old.get('model') or '')
                model = _RETIRED_MODELS.get((preset, model), model) or (info.get('models') or [''])[0]
                changes = {'preset': preset, 'base_url': str(old.get('base_url') or info.get('base_url', '')),
                           'model': model, 'protocol': info.get('protocol', 'openai'),
                           'json_mode': bool(info.get('json_mode', False))}
                self.update({'ai': changes})
            log.info('imported the 0.x API key for %s from %s', preset, path)
            return preset
        return None
