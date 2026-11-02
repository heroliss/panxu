"""Compact, readable host facts for models; never an action/parser language.

The host retains typed facts separately. Protocol envelopes and tool arguments
remain native API data. This module only renders already privacy-filtered data.
"""
from __future__ import annotations

import math
import re


HEADER = '盘序数据 v1'
_EMPTY_LIST, _EMPTY_OBJECT = '（空列表）', '（空对象）'
_RESERVED = {'未知', 'true', 'false', _EMPTY_LIST, _EMPTY_OBJECT, 'nan', 'inf', '-inf'}
_REFERENCES = {'id', 'name', 'path', 'signature', 'parent_id', 'fact_id', 'fact_ids', 'scan_id',
               'task_id', 'scope_id', 'source_ref', 'inherited_from', 'cursor', 'next_cursor',
               'evidence_ids', 'target_ids', 'ids'}


def _quote(text, *, key=False):
    escaped = ''.join('\\' + {'\\': '\\', '\n': 'n', '\r': 'r', '\t': 't'}[char]
                      if char in ('\\', '\n', '\r', '\t') else
                      f'\\u{ord(char):04x}' if ord(char) < 32 or ord(char) in (127, 133, 0x2028, 0x2029) else char for char in text)
    ambiguous = (not text or text.strip() != text or text in _RESERVED or ':' in text
                 or text.startswith(('-', "'", '#', '[', '{', '!', '@'))
                 or re.fullmatch(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?', text)
                 or re.fullmatch(r'列表 \d+项', text))
    ambiguous = ambiguous or key and "'" in text
    if ambiguous:
        return "'" + escaped.replace("'", "''") + "'"
    return escaped


def _scalar(value):
    if value is None:
        return '未知'
    if type(value) is bool:
        return 'true' if value else 'false'
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            return repr(value)
        return repr(value)
    if isinstance(value, str):
        return _quote(value)
    if value == []:
        return _EMPTY_LIST
    if value == {}:
        return _EMPTY_OBJECT
    raise TypeError('Host facts must contain only JSON-compatible values')


def _shared(rows):
    if len(rows) < 3 or not all(isinstance(row, dict) for row in rows):
        return {}
    common = {}
    for key, value in rows[0].items():
        if (key in _REFERENCES or key.endswith(('_id', '_ids', '_cursor', '_signature'))
                or not all(key in row and type(row[key]) is type(value) and row[key] == value for row in rows[1:])):
            continue
        common[key] = value
    # A short list should not acquire a longer explanatory wrapper.
    saved = (len(rows) - 1) * sum(len(str(key)) + len(str(value)) + 4 for key, value in common.items())
    return common if saved > 72 else {}


def _lines(value, indent=0):
    prefix = ' ' * indent
    if isinstance(value, dict) and value:
        result = []
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError('Host fact keys must be strings')
            label = prefix + _quote(key, key=True) + ':'
            shared = _shared(child) if isinstance(child, list) else {}
            if shared:
                result.append(label + f' 列表 {len(child)}项')
                result.append(prefix + '  每项相同:')
                result.extend(_lines(shared, indent + 4))
                result.append(prefix + '  条目:')
                result.extend(_lines([{key: item for key, item in row.items() if key not in shared} for row in child], indent + 4))
            elif isinstance(child, (dict, list)) and child:
                result.append(label)
                result.extend(_lines(child, indent + 2))
            else:
                result.append(label + ' ' + _scalar(child))
        return result
    if isinstance(value, list) and value:
        result = []
        for child in value:
            if isinstance(child, (dict, list)) and child:
                nested = _lines(child, indent + 2)
                if isinstance(child, dict):
                    result.append(prefix + '- ' + nested[0][indent + 2:])
                    result.extend(nested[1:])
                else:
                    result.append(prefix + '-')
                    result.extend(nested)
            else:
                result.append(prefix + '- ' + _scalar(child))
        return result
    return [prefix + _scalar(value)]


def render_host_text(value):
    """Retain values/IDs/unknowns, sharing identical row facts only within a list."""
    return HEADER + '\n' + '\n'.join(_lines(value))
