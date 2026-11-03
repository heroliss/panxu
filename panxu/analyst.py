"""Hybrid AI analysis: the program owns the flow, the model only classifies.

Space analysis works top-down over the scanned tree:

1. Built-in knowledge decides the obvious cases (Windows, Program Files,
   personal folders, rule-covered caches) — no tokens spent.
2. Structural folders (C:\\, AppData\\Local, ...) are opened automatically.
3. The first *unknown* folder on each branch that is large enough goes to the
   model in a batch, as a compact summary.
4. The model answers per folder: clean / keep / review / descend. Only
   ``descend`` costs more: the program then sends the named sub-folders in a
   later round, within a fixed request budget.

Verdicts are cached per (anonymised) path, so re-analysing costs nothing.
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import fsops, paths
from .ai_client import AIError
from .knowledge import Knowledge

VERDICTS = ('clean', 'keep', 'review', 'descend')
VERDICT_LABELS = {'clean': '可清理', 'keep': '建议保留', 'review': '需要你确认', 'descend': '内容混合'}
BATCH_SIZE = 12
MAX_ROUNDS = 4
CACHE_DAYS = 30
SUMMARY_FILE_CAP = 20000

SPACE_SYSTEM_PROMPT = """你是 Windows 磁盘清理专家。你会收到若干目录的摘要：路径（用户名已替换为 %USERPROFILE% 等变量）、总大小、文件数、最近修改时间、最大的子目录、文件类型分布和最大的几个文件。请逐个判断能否清理。

verdict 只能取以下之一：
- "clean"：整个目录都是可以安全删除的缓存、临时文件、日志、更新/安装包残留，或已卸载程序的残留。删除后不会丢失用户数据，相关程序会自动重建或不再需要。
- "keep"：应当保留，例如程序本体、用户数据、配置、游戏存档、聊天记录、虚拟机或数据库。不需要再细看。
- "review"：可能可以清理，但必须由用户确认，例如旧项目、下载的大文件、不确定是否还在使用的程序数据。
- "descend"：目录里混合了可清理和需要保留的内容，要看子目录才能判断。必须在 look_into 中列出要查看的子目录名（只能从给出的 subdirs 中选，最多 5 个，优先选体积大且可能可清理的）。

规则：
1. 只依据给出的信息判断。不确定时用 "review" 或 "descend"，不要猜成 "clean"。
2. 包含 .exe/.dll 的程序本体、驱动、系统组件不要判 "clean"。
3. 看起来是个人创作或资料（照片、视频、文档、代码项目、聊天记录）时，判 "keep" 或 "review"。
4. app：这个目录属于哪个程序或用途，不知道写 "未知"。
5. reason：中文，40 字以内，说明它是什么、为什么这样判断。
6. effect：仅 clean/review 需要，中文 25 字以内，说明删除后的影响，例如「下次打开时重新生成缓存」。
7. confidence：0 到 1 的小数，表示你对 verdict 的把握。

只输出一个 JSON 对象，不要输出其他文字：
{"results":[{"id":1,"verdict":"clean","app":"...","reason":"...","effect":"...","confidence":0.9,"look_into":[]}]}"""

ITEMS_SYSTEM_PROMPT = """你是 Windows 优化专家。你会收到若干开机启动项或系统服务的信息（名称、命令行或程序路径、发布者、描述）。请逐个说明它是什么，并给出是否建议禁用。

suggestion 只能取：
- "keep"：建议保留。安全软件、驱动和硬件控制、输入法、同步盘、用户明显需要常驻的程序。
- "optional"：可以禁用。禁用后不影响使用，只是需要时手动打开，或少了后台提醒。
- "disable"：建议禁用。自动更新器、广告推送、遥测、很少用的程序的常驻助手等。

规则：
1. what：中文 25 字以内，说明这是哪个程序、做什么。
2. reason：中文 40 字以内，说明建议理由和禁用后的影响。
3. 不认识时 what 写「未知程序」，suggestion 用 "keep"，reason 说明无法判断。

只输出一个 JSON 对象，不要输出其他文字：
{"results":[{"id":1,"what":"...","suggestion":"optional","reason":"..."}]}"""


def _fmt_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.0f}{unit}' if unit in ('B', 'KB') else f'{n:.1f}{unit}'
        n /= 1024
    return str(n)


def _fmt_date(ts: float) -> str:
    return time.strftime('%Y-%m-%d', time.localtime(ts)) if ts else '未知'


def anonymize_text(text: str, env: dict) -> str:
    """Replace user-specific folders inside free text (command lines)."""
    out = text
    for name in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE'):
        base = env.get(name)
        if not base:
            continue
        low, base_low = out.lower(), base.lower()
        idx = low.find(base_low)
        while idx >= 0:
            out = out[:idx] + '%' + name + '%' + out[idx + len(base):]
            low = out.lower()
            idx = low.find(base_low, idx + len(name) + 2)
    return out


class VerdictCache:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        try:
            with open(path, encoding='utf-8') as fh:
                self.data = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            self.data = {}

    def get(self, key: str, size: int | None = None) -> dict | None:
        row = self.data.get(key.lower())
        if not row or time.time() - row.get('time', 0) > CACHE_DAYS * 86400:
            return None
        if size is not None and row.get('size'):
            ratio = size / max(1, row['size'])
            if ratio < 0.5 or ratio > 2:
                return None  # changed a lot since the model looked at it
        return row

    def put(self, key: str, value: dict):
        with self._lock:
            self.data[key.lower()] = {**value, 'time': time.time()}

    def clear(self):
        with self._lock:
            self.data = {}
            self.save()

    def save(self):
        with self._lock:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(self.data, fh, ensure_ascii=False)
            os.replace(tmp, self.path)


class Analyst:
    child_floor = 50 * 1024 * 1024  # sub-folders smaller than this are not worth a request

    def __init__(self, cache: VerdictCache, env: dict | None = None, knowledge: Knowledge | None = None):
        self.cache = cache
        self.env = env or paths.environment()
        self.knowledge = knowledge or Knowledge(self.env)

    # -- space analysis ----------------------------------------------------------
    def frontier(self, scan, min_size: int, limit: int = 60) -> list:
        """Unknown folders worth asking about, biggest first (no model calls)."""
        out = []
        stack = [scan.root]
        while stack:
            node = stack.pop()
            if node.size < min_size:
                continue
            path = scan.path_of(node)
            role = 'descend' if node is scan.root else self.knowledge.ai_role(path)
            if role == 'skip':
                continue
            if role == 'descend':
                stack.extend(node.children or ())
                continue
            out.append(node)
        out.sort(key=lambda n: n.size, reverse=True)
        return out[:limit]

    def summarize(self, scan, node, ident: int, anonymize: bool, cancel=None) -> dict:
        path = scan.path_of(node)
        shown = paths.anonymize(path, self.env) if anonymize else path
        subdirs = [[c.name, _fmt_size(c.size), c.files] for c in (node.children or ())[:12]]
        exts: dict = {}
        largest = []
        oldest = None
        seen = 0
        for dirpath, files, _ in fsops.walk_files(path, cancel):
            for entry in files:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                ext = os.path.splitext(entry.name)[1].lower() or '(无)'
                exts[ext] = exts.get(ext, 0) + st.st_size
                largest.append((st.st_size, entry.name))
                if oldest is None or st.st_mtime < oldest:
                    oldest = st.st_mtime
                seen += 1
            if len(largest) > 200:
                largest = sorted(largest, reverse=True)[:20]
            if seen >= SUMMARY_FILE_CAP:
                break
        top_exts = sorted(exts.items(), key=lambda kv: kv[1], reverse=True)[:8]
        own = [n for n in sorted(largest, reverse=True)[:6]]
        summary = {
            'id': ident, 'path': shown, 'size': _fmt_size(node.size), 'files': node.files,
            'subdir_count': len(node.children or ()), 'newest': _fmt_date(node.mtime), 'oldest': _fmt_date(oldest or 0),
            'subdirs': subdirs, 'types': [[e, _fmt_size(s)] for e, s in top_exts],
            'largest_files': [[name, _fmt_size(size)] for size, name in own],
        }
        if seen >= SUMMARY_FILE_CAP:
            summary['note'] = f'文件很多，只统计了前 {SUMMARY_FILE_CAP} 个'
        return summary

    def preview(self, scan, settings: dict) -> dict:
        ai = settings['ai']
        min_size = int(ai.get('min_dir_mb', 500)) * 1024 * 1024
        nodes = self.frontier(scan, min_size)
        rows = []
        for node in nodes:
            path = scan.path_of(node)
            cached = self.cache.get(paths.anonymize(path, self.env), node.size)
            rows.append({'id': node.id, 'path': path, 'size': node.size,
                         'shown_as': paths.anonymize(path, self.env) if ai.get('anonymize_paths', True) else path,
                         'cached': bool(cached)})
        fresh = sum(1 for r in rows if not r['cached'])
        return {'candidates': rows, 'fresh': fresh, 'requests_estimate': min(int(ai.get('max_requests', 8)),
                                                                            -(-fresh // BATCH_SIZE) if fresh else 0)}

    def analyze_space(self, scan, client, settings: dict, cancel=None, progress=None, use_cache=True) -> dict:
        ai = settings['ai']
        anonymize = bool(ai.get('anonymize_paths', True))
        min_size = int(ai.get('min_dir_mb', 500)) * 1024 * 1024
        budget = max(1, int(ai.get('max_requests', 8)))
        queue = [(n, 1) for n in self.frontier(scan, min_size)]
        results: dict[int, dict] = {}
        requests = 0
        errors = []

        def note(stage, **extra):
            if progress:
                progress({'stage': stage, 'requests': requests, 'budget': budget, 'done': len(results), **extra})

        while queue:
            fsops.check_cancel(cancel)
            # answer from cache first
            fresh = []
            next_queue = []
            for node, depth in queue:
                key = paths.anonymize(scan.path_of(node), self.env)
                cached = self.cache.get(key, node.size) if use_cache else None
                if cached:
                    self._record(scan, node, cached, results, next_queue, depth, min_size, source='cache')
                else:
                    fresh.append((node, depth))
            queue = next_queue
            if not fresh:
                continue
            if requests >= budget:
                for node, depth in fresh:
                    results[node.id] = self._unanalysed(scan, node, '已达到本次分析的请求上限，未继续分析')
                continue
            batch, rest = fresh[:BATCH_SIZE], fresh[BATCH_SIZE:]
            queue = rest + queue
            note('summarize', current=scan.path_of(batch[0][0]))
            summaries = [self.summarize(scan, node, i + 1, anonymize, cancel) for i, (node, _) in enumerate(batch)]
            note('ask', count=len(batch))
            requests += 1
            try:
                reply = client.complete_json(SPACE_SYSTEM_PROMPT,
                                             '请判断这些目录：\n' + json.dumps(summaries, ensure_ascii=False))
            except (AIError, ValueError) as exc:
                errors.append(str(exc))
                for node, _ in batch:
                    results[node.id] = self._unanalysed(scan, node, f'模型请求失败：{exc}')
                if isinstance(exc, AIError) and getattr(exc, 'status', None) in (401, 402, 403, 404):
                    break  # configuration problem: don't burn the remaining budget
                continue
            answers = {}
            for row in reply.get('results') or []:
                if isinstance(row, dict) and isinstance(row.get('id'), int):
                    answers[row['id']] = row
            for i, (node, depth) in enumerate(batch):
                verdict = self._clean_answer(answers.get(i + 1), node)
                self.cache.put(paths.anonymize(scan.path_of(node), self.env),
                               {**verdict, 'size': node.size, 'model': client.model})
                self._record(scan, node, verdict, results, queue, depth, min_size, source='ai')
        self.cache.save()
        suggestions = sorted(results.values(), key=lambda r: r['size'], reverse=True)
        return {'suggestions': suggestions, 'usage': client.usage.as_dict(), 'errors': errors,
                'requests': requests, 'budget': budget}

    def _clean_answer(self, row: dict | None, node) -> dict:
        if not row:
            return {'verdict': 'review', 'app': '未知', 'reason': '模型没有返回这个目录的判断', 'effect': '',
                    'confidence': 0.0, 'look_into': []}
        verdict = row.get('verdict') if row.get('verdict') in VERDICTS else 'review'
        names = {c.name.lower(): c.name for c in node.children or ()}
        look = [names[n.lower()] for n in row.get('look_into') or [] if isinstance(n, str) and n.lower() in names][:5]
        try:
            confidence = max(0.0, min(1.0, float(row.get('confidence', 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        return {'verdict': verdict, 'app': str(row.get('app') or '未知')[:40], 'reason': str(row.get('reason') or '')[:120],
                'effect': str(row.get('effect') or '')[:80], 'confidence': confidence, 'look_into': look}

    def _record(self, scan, node, verdict: dict, results: dict, queue: list, depth: int, min_size: int, source: str):
        if verdict['verdict'] == 'descend':
            child_min = max(min_size // 5, self.child_floor)
            wanted = {n.lower() for n in verdict.get('look_into', [])}
            children = [c for c in node.children or () if c.name.lower() in wanted and c.size >= child_min]
            children = [c for c in children if self.knowledge.ai_role(scan.path_of(c)) != 'skip']
            if children and depth < MAX_ROUNDS:
                queue.extend((c, depth + 1) for c in children)
                results[node.id] = self._row(scan, node, verdict, source, expanded=True)
                return
            verdict = {**verdict, 'verdict': 'review',
                       'reason': verdict.get('reason') or '内容混合，需要你打开目录查看'}
        results[node.id] = self._row(scan, node, verdict, source)

    def _row(self, scan, node, verdict: dict, source: str, expanded: bool = False) -> dict:
        return {'node_id': node.id, 'path': scan.path_of(node), 'size': node.size, 'files': node.files,
                'verdict': verdict['verdict'], 'verdict_label': VERDICT_LABELS[verdict['verdict']],
                'app': verdict.get('app', ''), 'reason': verdict.get('reason', ''), 'effect': verdict.get('effect', ''),
                'confidence': verdict.get('confidence', 0), 'source': source, 'expanded': expanded}

    def _unanalysed(self, scan, node, reason: str) -> dict:
        return self._row(scan, node, {'verdict': 'review', 'app': '', 'reason': reason, 'effect': '',
                                      'confidence': 0}, 'none')

    # -- startup items and services ------------------------------------------------
    def explain_items(self, items: list[dict], client, anonymize: bool = True, use_cache: bool = True) -> dict:
        """items: [{'key', 'kind': 'startup'|'service', 'name', 'command', 'publisher', 'description'}]"""
        out, pending = {}, []
        for item in items:
            cached = self.cache.get('item:' + item['key']) if use_cache else None
            if cached:
                out[item['key']] = {**cached, 'source': 'cache'}
            else:
                pending.append(item)
        errors = []
        for start in range(0, len(pending), 25):
            batch = pending[start:start + 25]
            payload = []
            for i, item in enumerate(batch):
                command = item.get('command', '')
                payload.append({'id': i + 1, 'type': '开机启动项' if item['kind'] == 'startup' else '系统服务',
                                'name': item.get('name', ''),
                                'command': anonymize_text(command, self.env) if anonymize else command,
                                'publisher': item.get('publisher', ''), 'description': item.get('description', '')[:200]})
            try:
                reply = client.complete_json(ITEMS_SYSTEM_PROMPT, '请判断：\n' + json.dumps(payload, ensure_ascii=False))
            except (AIError, ValueError) as exc:
                errors.append(str(exc))
                break
            answers = {r.get('id'): r for r in reply.get('results') or [] if isinstance(r, dict)}
            for i, item in enumerate(batch):
                row = answers.get(i + 1)
                if not row:
                    continue
                suggestion = row.get('suggestion') if row.get('suggestion') in ('keep', 'optional', 'disable') else 'keep'
                value = {'what': str(row.get('what') or '')[:60], 'suggestion': suggestion,
                         'reason': str(row.get('reason') or '')[:120]}
                self.cache.put('item:' + item['key'], value)
                out[item['key']] = {**value, 'source': 'ai'}
        self.cache.save()
        return {'items': out, 'usage': client.usage.as_dict(), 'errors': errors}
