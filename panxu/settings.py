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

SETTINGS_VERSION = 4
DEFAULTS = {
    'version': SETTINGS_VERSION,
    'theme': 'system',  # system | light | dark
    'admin_on_start': True,  # request UAC once; a refusal still opens the app normally
    'ai_check_on_start': True,
    'ai_status_notice': {'signature': '', 'state': '', 'message': '', 'time': 0},
    'ai': {
        'preset': 'deepseek',
        'base_url': 'https://api.deepseek.com',
        'model': 'deepseek-flash',
        'protocol': 'openai',  # openai | anthropic
        'json_mode': True,
        'thinking': 'low',       # off | low | high, for 问 AI only (classification never thinks)
        'anonymize_paths': True,
        'send_file_content': False,  # allow sending the first lines of documents (asked again each time)
        'max_requests': 8,       # per analysis run
        'min_dir_mb': 0,         # an optional floor; 0: any non-empty folder, biggest first, one budget per run
        'timeout': 120,
    },
    'checkup': {
        'space_scan': True,      # scan fixed local disks and explicitly added/redirected personal roots
        'other_drives': True,    # also enumerate other fixed disks; personal locations remain in the base scope
        'selected_drives': None, # None keeps legacy defaults; [] means personal locations only, no whole disks
        'duplicates': True,
        'cross_drive_duplicates': True,
        'use_ai': True,          # ask the model when a key is configured
        'other_accounts': False, # also look through other accounts' personal files (administrator only; never auto-ticked)
        'apps_services': True,   # read installed programs and third-party services (nothing is changed by the check-up)
        'ai_scopes': {'space': True, 'files': True, 'duplicates': True, 'startup': True, 'apps': True, 'services': True},
        'auto_threshold': 7,     # 勾选门槛 0-10: pre-tick eligible items scoring at least this (稳妥 8, 适中 7, 积极 5)
    },
    'insights': {
        'stale_days': 365,       # "很久没动" threshold for large personal files
        'large_mb': 100,
        'download_days': 180,    # downloads older than this are worth a look
        'other_accounts': False, # 「我的文件」 page: include other accounts' folders (administrator only)
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
        raw = self._read(self.path)
        self._data = _merge(DEFAULTS, raw)
        ai = self._data['ai']
        replacement = _RETIRED_MODELS.get((ai.get('preset'), ai.get('model')))
        if replacement:
            ai['model'] = replacement
        if raw and int(raw.get('version') or 1) < SETTINGS_VERSION:
            self._migrate(raw)

    def _migrate(self, raw: dict):
        """Settings written by earlier builds (once; the file then carries the current version)."""
        version = int(raw.get('version') or 1)
        old_ai = raw.get('ai') if isinstance(raw.get('ai'), dict) else {}
        if version < 2:
            if old_ai.get('thinking') in (None, 'off'):
                self._data['ai']['thinking'] = 'low'   # 'off' was the default, not a choice
            old_checkup = raw.get('checkup') if isinstance(raw.get('checkup'), dict) else {}
            if old_checkup.get('auto_select') == 'careful' and 'auto_threshold' not in old_checkup:
                self._data['checkup']['auto_threshold'] = 8   # 自动勾选 稳妥/积极 -> 勾选门槛 8/5
        old_floor = old_ai.get('min_dir_mb')
        if old_floor is None or (version < 3 and old_floor == 500) or (version == 3 and old_floor == 200):
            # the default of the build that wrote the file (500 MB before version 3, then 200 MB), not a choice: a
            # 300 MB Temp folder was never looked at, and small folders are worth a look too now that each run takes
            # the next batch, biggest first. Any other value was picked by the user and is kept.
            self._data['ai']['min_dir_mb'] = 0
        self._data['version'] = SETTINGS_VERSION
        try:
            self._write(self.path, self._data)
        except OSError:
            pass

    @staticmethod
    def _read(path):
        try:
            with open(path, encoding='utf-8') as fh:
                value = json.load(fh)
                return value if isinstance(value, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write(self, path, value):
        # written next to it, on disk, then swapped in: a crash or power cut leaves the old file or the new one
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
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
