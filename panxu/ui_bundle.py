"""Inline web/index.html's local CSS and JS into one HTML document.

The app loads the UI with ``html=`` (no local web server), and the same
bundle with a mock backend is used for browser previews during development.
"""
from __future__ import annotations

import os
import re
import sys

_LINK = re.compile(r'<link\s+rel="stylesheet"\s+href="([^":]+)"\s*/?>')
_SCRIPT = re.compile(r'<script\s+src="([^":]+)"\s*></script>')
_CLOSE_STYLE = re.compile(r'</(style)', re.I)
_CLOSE_SCRIPT = re.compile(r'</(script)', re.I)


def web_dir() -> str:
    base = getattr(sys, '_MEIPASS', None)
    if base:
        return os.path.join(base, 'web')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web')


def build(directory: str | None = None, extra_scripts: list[str] | None = None, theme: str | None = None) -> str:
    directory = directory or web_dir()
    with open(os.path.join(directory, 'index.html'), encoding='utf-8') as fh:
        html = fh.read()
    if theme in ('dark', 'light'):
        html = html.replace('<html lang="zh-CN">', f'<html lang="zh-CN" data-theme="{theme}">', 1)

    def read(name):
        with open(os.path.join(directory, name), encoding='utf-8') as fh:
            return fh.read()

    html = _LINK.sub(lambda m: '<style>\n' + _CLOSE_STYLE.sub(r'<\\/\1', read(m.group(1))) + '\n</style>', html)
    # '</script' inside inlined code would end the tag early (HTML closes <script> case-insensitively)
    html = _SCRIPT.sub(lambda m: '<script>\n' + _CLOSE_SCRIPT.sub(r'<\\/\1', read(m.group(1))) + '\n</script>', html)
    if extra_scripts:
        injected = ''.join('<script>\n' + _CLOSE_SCRIPT.sub(r'<\\/\1', read(n)) + '\n</script>\n' for n in extra_scripts)
        html = html.replace('<!-- mock -->', injected)
    return html
