"""Disposable UI fixture for local duplicates and contextual task navigation."""
from pathlib import Path
import os

import serve_v0110_fixture as previous

fixture = previous.fixture
original_tree = fixture.create_tree


def create_tree(root, count):
    scope, parent = original_tree(root, count)
    duplicates = scope / '重复查询测试'
    first, second = duplicates / '原件', duplicates / '下载副本'
    first.mkdir(parents=True)
    second.mkdir()
    content = b'owned-duplicate-contents\n' * 65536
    (first / '资料.zip').write_bytes(content)
    (second / '资料的副本.zip').write_bytes(content)
    (second / '同大小不同内容.zip').write_bytes(b'x' * len(content))
    (duplicates / '唯一文件.txt').write_text('唯一的自建测试资料。', encoding='utf-8')
    os.link(first / '资料.zip', first / '资料硬链接.zip')
    return scope, parent


if __name__ == '__main__':
    fixture.create_tree = create_tree
    fixture.answer = previous.answer
    fixture.SEED_GOAL = '验证清单、左上返回与独立本地重复检查；所有文件保留，仅允许模拟。'
    fixture.main()
