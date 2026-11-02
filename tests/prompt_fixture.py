"""Test/acceptance-only decoder; production never parses model text as actions."""
from __future__ import annotations

import copy
import json
import re

from diskpilot.prompt_text import HEADER


def _unescape(text):
    def replace(match):
        token = match.group(1)
        if token.startswith('u'):
            return chr(int(token[1:], 16))
        return {'\\': '\\', 'n': '\n', 'r': '\r', 't': '\t'}[token]
    return re.sub(r'\\(u[0-9a-fA-F]{4}|[\\nrt])', replace, text)


def _value(text):
    if text.startswith("'") and text.endswith("'"):
        return _unescape(text[1:-1].replace("''", "'"))
    if text == '未知':
        return None
    if text in ('true', 'false'):
        return text == 'true'
    if text == '（空列表）':
        return []
    if text == '（空对象）':
        return {}
    if re.fullmatch(r'[-+]?\d+', text):
        return int(text)
    if re.fullmatch(r'[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?|[-+]?\d+[eE][-+]?\d+', text):
        return float(text)
    if text in ('nan', 'inf', '-inf'):
        return float(text)
    return _unescape(text)


def _field(text):
    quoted, index = False, 0
    while index < len(text):
        char = text[index]
        if char == "'":
            if quoted and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif char == ':' and not quoted and (index + 1 == len(text) or text[index + 1] == ' '):
            return _value(text[:index]), text[index + 1:].lstrip(' ')
        index += 1
    return None


def parse_host_data(content):
    """Accept previous JSON fixtures, new readable facts and Gemini's wrapper."""
    if isinstance(content, dict) and set(content) == {'output'} and isinstance(content['output'], str) and content['output'].startswith(HEADER + '\n'):
        return parse_host_data(content['output'])
    if not isinstance(content, str):
        return copy.deepcopy(content)
    if not content.startswith(HEADER + '\n'):
        return json.loads(content)
    lines = []
    for line in content.split('\n')[1:]:
        indent, body = len(line) - len(line.lstrip(' ')), line.lstrip(' ')
        if body.startswith('- ') and _field(body[2:]):
            lines.extend([(indent, '-'), (indent + 2, body[2:])])
        else:
            lines.append((indent, body))

    def block(index, indent):
        if lines[index][0] != indent:
            raise ValueError('Unexpected fact indentation')
        sequence = lines[index][1] == '-' or lines[index][1].startswith('- ')
        result = [] if sequence else {}
        while index < len(lines) and lines[index][0] == indent:
            body = lines[index][1]
            if sequence:
                if body == '-':
                    child, index = block(index + 1, indent + 2)
                elif body.startswith('- '):
                    child, index = _value(body[2:]), index + 1
                else:
                    break
                result.append(child)
                continue
            field = _field(body)
            if field is None:
                if not result:
                    return _value(body), index + 1
                raise ValueError('Invalid fact field')
            key, text = field
            index += 1
            if not text:
                child, index = block(index, indent + 2)
            elif re.fullmatch(r'列表 \d+项', text):
                groups, index = block(index, indent + 2)
                common, rows = groups['每项相同'], groups['条目']
                child = [dict(copy.deepcopy(common), **row) for row in rows]
                if len(child) != int(text[3:-1]):
                    raise ValueError('Fact list count differs')
            else:
                child = _value(text)
            result[key] = child
        return result, index

    value, end = block(0, 0)
    if end != len(lines):
        raise ValueError('Unconsumed fact lines')
    return value
