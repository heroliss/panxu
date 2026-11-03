"""User settings (JSON) and the API key store (DPAPI-encrypted on Windows)."""
from __future__ import annotations

import base64
import copy
import json
import os
import threading

from . import winapi

DEFAULTS = {
    'theme': 'system',  # system | light | dark
    'ai': {
        'preset': 'deepseek',
        'base_url': 'https://api.deepseek.com',
        'model': 'deepseek-chat',
        'protocol': 'openai',  # openai | anthropic
        'json_mode': True,
        'anonymize_paths': True,
        'max_requests': 8,       # per analysis run
        'min_dir_mb': 500,       # directories smaller than this are not sent
        'timeout': 120,
    },
    'duplicates': {'min_size_mb': 1},
    'large_files': {'min_size_mb': 100},
    'excluded_paths': [],
}


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
