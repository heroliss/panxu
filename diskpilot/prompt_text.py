"""Compact, readable host facts for models; never an action/parser language.

The host retains typed facts separately. Protocol envelopes and tool arguments
remain native API data. This module only renders already privacy-filtered data.
"""
from __future__ import annotations

import copy
import math
import re


HEADER = '盘序数据 v1'
_EMPTY_LIST, _EMPTY_OBJECT = '（空列表）', '（空对象）'
_RESERVED = {'未知', 'true', 'false', _EMPTY_LIST, _EMPTY_OBJECT, 'nan', 'inf', '-inf'}
_REFERENCES = {'id', 'name', 'path', 'signature', 'parent_id', 'fact_id', 'fact_ids', 'scan_id',
               'task_id', 'scope_id', 'source_ref', 'inherited_from', 'cursor', 'next_cursor',
               'evidence_ids', 'target_ids', 'ids'}


def _task_view(value):
    """Project only the host's explicitly marked root; never walk user prose.

    The adapter needs three legacy fields to recognize this private message.
    They are not all useful model inputs. Data values in nested observations,
    including meaningful false/zero/unknown, are deliberately not pruned.
    """
    if not (isinstance(value, dict) and value.get('_prompt_projection') == 'task-v2'
            and isinstance(value.get('goal'), str)):
        return value
    result = copy.deepcopy(value)
    result.pop('_prompt_projection')
    # This exact build constant is already stated in the system instruction.
    if result.get('simulation_only') is True:
        result.pop('simulation_only')
    boundary = result.get('scope_boundary')
    if isinstance(boundary, str) and boundary:
        if result.get('scope_kind') in ('filesystem', 'applications'):
            result.pop('scope_kind')
        scopes = result.get('scopes')
        if (isinstance(scopes, list) and type(result.get('scope_count')) is int
                and result['scope_count'] == len(scopes)):
            result.pop('scope_count')
            if not scopes:
                result.pop('scopes')
        # Presets route the UI. The goal and explicit scope carry their intent.
        if result.get('preset') in ('analysis', 'background', 'c_cleanup', 'disk_cleanup',
                                   'directory_cleanup', 'downloads', 'duplicates', 'installers'):
            result.pop('preset')
    count = result.get('available_items')
    if type(count) is int and count == 0:
        result.pop('available_items')
        result['inventory_note'] = '尚无已保存的对象；不代表位置为空，需先取得观察。'
    elif (type(count) is int and isinstance(result.get('analysis'), dict)
          and result['analysis'].get('target_count') == count):
        result.pop('available_items')
    if isinstance(result.get('user_feedback'), list):
        for row in result['user_feedback']:
            if isinstance(row, dict) and row.get('role') == 'user':
                row.pop('role')
    analysis = result.get('analysis')
    if isinstance(analysis, dict):
        analysis.pop('catalog_revision', None)
        if analysis.get('initial_target_count') == analysis.get('target_count'):
            analysis.pop('initial_target_count', None)
        for field, default in (('query', ''), ('scope', ''), ('risk', 'all'), ('kind', None)):
            if field in analysis and analysis[field] == default:
                analysis.pop(field)
    handoff = result.get('handoff')
    if isinstance(handoff, dict):
        for field in ('updated_at', 'model', 'origin'):
            handoff.pop(field, None)
        for field in ('next_steps', 'unresolved'):
            if handoff.get(field) == []:
                handoff.pop(field)
    for field in ('connection', 'previous_connection'):
        if isinstance(result.get(field), dict):
            result[field].pop('connection_id', None)
    for skill in result.get('loaded_skills', []):
        if isinstance(skill, dict):
            skill.pop('at', None)
            skill.pop('read_at', None)
            if skill.get('content_fingerprint') == skill.get('sha256'):
                skill.pop('content_fingerprint', None)
    if (isinstance(result.get('facts'), dict) and type(result['facts'].get('total')) is int
            and result['facts'] == {'total': 0, 'has_more': False}):
        result['facts'] = '暂无可回读的事实记录。'
    return result


def _fact_page_view(value):
    """A read_facts result needs one payload, not a JSON preview of it as well."""
    if not (isinstance(value, dict) and isinstance(value.get('items'), list)
            and all(isinstance(row, dict) and isinstance(row.get('id'), str)
                    and row['id'].startswith('fact-') and 'tool' in row and 'result' in row
                    and row.get('requires_recheck_for_action') is True for row in value['items'])):
        return value
    result = copy.deepcopy(value)
    for row in result['items']:
        payload = row['result']
        # summary is facts_page's serialized preview, fully represented below.
        row.pop('summary', None)
        if isinstance(payload, dict):
            ids = {item['id'] for item in payload.get('items', [])
                   if isinstance(item, dict) and isinstance(item.get('id'), str)}
            if isinstance(row.get('object_ids'), list) and set(row['object_ids']) <= ids:
                row.pop('object_ids')
            if 'scope' in row and payload.get('_scope') == row['scope']:
                payload.pop('_scope')
    return result


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
    return HEADER + '\n' + '\n'.join(_lines(_fact_page_view(_task_view(value))))
