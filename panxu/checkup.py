"""一键体检 and the cleanup plan (清理方案).

The check-up runs the existing tools one after another — junk rules, a scan of
the system drive, 「我的文件」, duplicates and (when a model is configured) the
AI — and turns everything into one plan of three groups:

* ``safe``   放心清理: rule-covered caches that are on by default, and folders the
             model is sure about inside application data. Pre-selected.
* ``review`` 建议你看看: everything else that could be removed. Never pre-selected;
             personal files always land here.
* ``info``   仅供了解: big things that can't (or shouldn't) be deleted from here,
             with what to do instead (hiberfil.sys, WSL disks, Steam games...).

The program decides what goes into the plan and what each action does; the
model only adds verdicts and explanations, and its verdicts never widen what
is allowed (every path still goes through ``ProtectionPolicy``).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import time
import uuid

from . import APP_NAME, fsops, paths, winapi
from .analyst import newer_sibling
from .duplicates import (AI_RANK, IN_USE, PLACE_RANK, find_duplicates, must_stay, place_label, removal_score,
                         verify_unchanged)
from .insights import KIND_SCORE, Insights, content_question, file_score, is_copy_name, managed_dir, with_ai
from .junk import RULES_BY_ID
from .scanner import FLAG_PROGRAM, SpaceScan

GROUPS = {'safe': '放心清理', 'review': '建议你看看', 'info': '仅供了解'}
ACTIONS = {'delete': '永久删除', 'recycle': '移到回收站', 'disable': '禁用开机启动', 'none': '不需要操作'}
SOURCES = {'rule': '规则', 'ai': 'AI', 'insight': '我的文件', 'dup': '重复文件', 'heuristic': '盘序判断',
           'knowledge': '说明'}

STAGES = [('junk', '扫描垃圾文件'), ('space', '分析系统盘空间'), ('insights', '整理我的文件'),
          ('dups', '查找重复文件'), ('ai', 'AI 复核'), ('plan', '生成清理方案')]

# 勾选门槛 (0-10): eligible items whose 建议清理度 reaches it are pre-ticked. 稳妥 8, 积极 5.
DEFAULT_THRESHOLD = 5
THRESHOLDS = {'careful': 8, 'bold': 5}   # older settings / options
MIN_AI_CONFIDENCE = 0.7      # below this a 「可清理」 verdict is never pre-ticked, whatever the threshold
ACTIVE_DAYS = 7             # folders changed more recently are left for the user
# rules never pre-ticked: no undo (Recycle Bin, rollback to the old Windows), very slow (DISM), or
# possibly holding the user's own builds (mvn install puts them in the local repository)
NEVER_AUTO_RULES = {'recycle_bin', 'windows_old', 'component_store', 'maven'}
# names inside a folder that suggest something worth keeping although nobody "made" it: game saves,
# profiles, backups. A folder containing one is never pre-ticked.
VALUABLE_DIRS = {'save', 'saves', 'savegames', 'savedgames', 'saved games', 'savedata', 'save data', '存档',
                 'profile', 'profiles', 'user data', 'userdata', 'backup', 'backups', '备份', 'license', 'licenses'}
CACHE_WORDS = ('cache', 'temp', 'tmp', 'log', 'crash', 'dump', 'shader', 'thumbnail')
LEFTOVER_MIN = 500 * 1024 * 1024
OLD_VERSION_MIN = 200 * 1024 * 1024
YEAR = 365 * 86400
INFO_MIN = 1024 ** 3
PERSONAL_PER_KIND = 80
# your files smaller than this stay on 我的文件: in a cleanup plan they free nothing and only add a risk
PLAN_MIN_FILE = 1024 * 1024
FILES_FOR_AI = 120
DUPS_FOR_AI = 36   # groups of duplicates the check-up asks about (three requests)
# 「需要你确认」: the model cannot tell, so its confidence in saying so is no reason to clean (5 points, held)
AI_REVIEW_SCORE = 50
# every copy of a duplicates group chosen: that is deleting the file itself, not a spare copy (2 points)
ALL_COPIES_SCORE = 15

# big things worth explaining, in the order they are shown
INFO_TARGETS = [
    '%SystemDrive%\\hiberfil.sys', '%SystemDrive%\\pagefile.sys', '%SystemDrive%\\System Volume Information',
    '%SystemRoot%\\WinSxS', '%LOCALAPPDATA%\\Docker', '%LOCALAPPDATA%\\wsl',
    '%LOCALAPPDATA%\\Packages\\CanonicalGroupLimited.*', '%ProgramFiles(x86)%\\Steam\\steamapps',
    '%USERPROFILE%\\Documents\\WeChat Files', '%USERPROFILE%\\Documents\\xwechat_files',
    '%USERPROFILE%\\Documents\\Tencent Files',
]


_REVEAL = {'kind': 'reveal', 'label': '打开文件夹'}
# what the user can do about each 仅供了解 item (kind: hibernate / settings / page / copy / reveal)
INFO_ACTIONS = {
    'hiberfil.sys': [{'kind': 'hibernate', 'label': '关闭休眠，删除这个文件'}],
    'pagefile.sys': [{'kind': 'settings', 'arg': 'virtual_memory', 'label': '打开虚拟内存设置'}],
    'system volume information': [{'kind': 'settings', 'arg': 'system_protection', 'label': '打开系统保护（管理还原点）'}],
    'winsxs': [{'kind': 'page', 'arg': 'junk', 'label': '去垃圾清理做「组件存储清理」'}],
    'docker': [{'kind': 'copy', 'arg': 'docker system prune', 'label': '复制清理命令'}, _REVEAL],
    'steamapps': [{'kind': 'page', 'arg': 'apps', 'label': '在「应用」里卸载不玩的游戏'}, _REVEAL],
}


def info_actions(path: str) -> list[dict]:
    name = os.path.basename(path.rstrip('\\/')).lower()
    return INFO_ACTIONS.get(name, [_REVEAL] if os.path.isdir(path) else [])


def personal_roots(env: dict, settings: dict) -> list[str]:
    out = []
    for name in ('desktop', 'documents', 'downloads', 'pictures', 'videos', 'music'):
        try:
            p = winapi.known_folder(name)
        except OSError:
            continue
        if p and os.path.isdir(p) and p not in out:
            out.append(p)
    for p in settings.get('extra_personal_paths') or []:
        if isinstance(p, str) and os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def system_skip(env: dict, policy, roots: list[str] | None = None):
    """Directories the personal-file tools never enter: system folders, app data, user exclusions, and
    folders programs manage themselves (caches, node_modules, chat storage...) below the chosen roots."""
    bases = [env.get(k) for k in ('SystemRoot', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData', 'LOCALAPPDATA',
                                  'APPDATA')]
    bases = [b for b in bases if b]
    chosen = {paths.key(r) for r in roots or []}

    def skip(path):
        if paths.key(path) not in chosen and managed_dir(os.path.basename(path.rstrip('\\/'))):
            return True
        return any(paths.is_within(path, b) for b in bases) or policy.reason(path) == '位于你设置的排除目录中'
    return skip


def _ident(prefix: str, text: str) -> str:
    return prefix + ':' + hashlib.md5(text.lower().encode('utf-8')).hexdigest()[:12]


def _ago(ts: float, now: float) -> str:
    days = (now - ts) / 86400
    return f'{days / 365:.0f} 年' if days >= 365 else f'{int(days)} 天'


class _Stub:
    """A scan-node stand-in when only a suggestion's modification time is known."""
    children = ()

    def __init__(self, mtime):
        self.mtime = mtime


def valuable_inside(node, depth: int = 4) -> str | None:
    """Name of a sub-folder that looks like saves, a profile or a backup (scan nodes only, no disk access)."""
    stack = [(c, 1) for c in node.children or ()]
    while stack:
        n, d = stack.pop()
        low = n.name.lower()
        if low in VALUABLE_DIRS or low.startswith('savegame'):
            return n.name
        if d < depth:
            stack.extend((c, d + 1) for c in n.children or ())
    return None


def cache_like(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in CACHE_WORDS)


def points(score) -> int | None:
    """建议清理度 as shown: 0-10, rounded half up (scores are kept 0-100 for sorting)."""
    return None if score is None else int(score / 10 + 0.5)


def decision_note(item: dict, threshold: int) -> list[str]:
    """Why an item was (not) ticked automatically -- the last line of its 评分依据."""
    if item['action'] == 'none':
        return []
    if item['blocked']:
        return ['没有勾选：' + item['blocked']]
    pts = points(item['score'])
    if item['selected']:
        return [f'自动勾选：{item.get("auto_reason") or "可以清理"}，{pts} 分达到勾选门槛（{threshold} 分）']
    if item.get('personal'):
        return ['没有自动勾选：你的文件只由你决定']
    if item.get('eligible'):
        return [f'没有自动勾选：{pts} 分，低于勾选门槛（{threshold} 分）']
    return ['没有自动勾选：' + (item.get('hold_reason') or '留给你决定')]


class PlanBuilder:
    """Collects plan items; a path is offered at most once (first source wins)."""

    def __init__(self, policy, knowledge=None, now: float | None = None, threshold: int = DEFAULT_THRESHOLD):
        self.policy = policy
        self.knowledge = knowledge
        self.now = now or time.time()
        # Each source decides whether an item may be pre-ticked at all ('eligible': never personal
        # files, nothing without an undo, nothing held back by a guard) and its score; add() then
        # ticks eligible items whose score reaches the threshold (0-10).
        self.threshold = threshold
        self.items: list[dict] = []
        self._claimed: list[str] = []

    def _taken(self, path: str) -> bool:
        return any(paths.is_within(path, c) or paths.is_within(c, path) for c in self._claimed)

    def add(self, item: dict) -> dict | None:
        base = {'id': '', 'group': 'review', 'category': '', 'title': '', 'size': 0, 'count': 0, 'what': '', 'advice': '', 'why': '',
                'effect': '', 'action': 'none', 'source': 'heuristic', 'confidence': None, 'selected': False,
                'paths': [], 'blocked': None, 'caution': None, 'ask': None, 'status': 'pending', 'ai': None,
                'score': None, 'basis': []}
        item = {**base, **item}
        if item['paths']:
            fresh = [p for p in item['paths'] if not self._taken(p)]
            if not fresh:
                return None
            item['paths'] = fresh
            reasons = [self.policy.reason(p) for p in fresh]
            blocked = next((r for r in reasons if r), None)
            if blocked:
                item['blocked'] = blocked
                item['selected'] = False
            item['caution'] = next((c for c in (self.policy.caution(p) for p in fresh) if c), None)
            self._claimed.extend(fresh)
        if item['group'] != 'info':
            item['eligible'] = bool(item.get('eligible')) and not item.get('personal')
            item['selected'] = (item['eligible'] and not item['blocked'] and item['score'] is not None
                                and points(item['score']) >= self.threshold)
            item['group'] = 'safe' if item['selected'] else 'review'
        item['basis'] = list(item['basis']) + decision_note(item, self.threshold)
        item['ask'] = item['ask'] or {'type': 'plan_item', 'id': item['id']}
        self.items.append(item)
        return item

    # -- sources ------------------------------------------------------------------
    def add_junk(self, rows: list[dict]):
        for row in rows:
            rule = RULES_BY_ID.get(row['id'])
            if rule is None:
                continue
            size = row.get('size')
            if not row.get('available') or (size is not None and size <= 0 and rule.mode != 'command'):
                continue
            blocked = None
            if row.get('running'):
                blocked = '请先关闭 ' + '、'.join(row['running'])
            elif rule.admin and row.get('needs_admin'):
                blocked = '需要管理员权限'
            hold = None
            if rule.default:
                score, basis, auto = 95, ['内置规则：公认可以重建的缓存和临时文件'], '默认开启的内置规则'
            elif rule.mode == 'recycle_bin' or rule.id == 'windows_old':
                score, basis, auto = 55, ['内置规则，但删除后无法恢复：' + (rule.note or '无法从回收站恢复')], None
                hold = '删除后无法恢复，请你决定'
            elif rule.id in NEVER_AUTO_RULES:
                score, basis, auto = 60, ['内置规则，默认不勾：' + (rule.note or '删除前请确认里面没有你自己的东西')], None
                hold = '默认不勾，请你决定'
            else:
                score = 70
                basis = ['内置规则：可以重建，只是下次用到时要重新下载或生成，第一次会慢一些']
                auto = '可以重建的缓存'
            self.add({
                'id': 'rule:' + rule.id, 'kind': 'rule', 'rule': rule.id,
                'category': '垃圾文件', 'title': rule.name, 'size': size or 0, 'count': row.get('count') or 0,
                'what': '', 'why': rule.description,
                'effect': rule.note or '', 'action': 'delete', 'source': 'rule',
                'confidence': 1.0 if rule.default else 0.7, 'blocked': blocked,
                'locations': row.get('locations', [])[:5], 'score': score, 'basis': basis,
                'eligible': hold is None, 'auto_reason': auto, 'hold_reason': hold})

    def _folder_guards(self, path: str, node, owner: str | None, basis: list[str]) -> str | None:
        """Reasons a folder must not be pre-ticked whatever the score (also added to the basis)."""
        mtime = node.mtime if node is not None else None
        if mtime and self.now - mtime < ACTIVE_DAYS * 86400:
            basis.append(f'最近 {ACTIVE_DAYS} 天内还有改动：可能还有程序在用')
            return '最近还有改动'
        inside = valuable_inside(node) if node is not None else None
        if inside:
            basis.append(f'里面有「{inside}」文件夹：可能是存档、个人资料或备份')
            return f'里面可能有存档或个人资料（「{inside}」）'
        if getattr(node, 'flags', 0) & FLAG_PROGRAM:
            basis.append('里面有程序文件（.exe / .dll）：可能是程序本身，删掉后它就不能运行了')
            return '里面有程序文件'
        if owner and not cache_like(os.path.basename(path.rstrip('\\/'))):
            # the whole folder of an installed program also holds its settings, licence or templates;
            # only its cache-named sub-folders may be ticked
            return f'属于仍在使用的程序「{owner}」'
        return None

    def add_ai_dirs(self, suggestions: list[dict], env: dict, analyst=None, scan=None):
        app_bases = [env.get(k) for k in ('LOCALAPPDATA', 'APPDATA', 'LOCALLOW', 'ProgramData') if env.get(k)]
        for s in sorted(suggestions, key=lambda r: -r['size']):
            if s.get('source') in ('none', 'knowledge') or s.get('expanded') or s['verdict'] not in ('clean', 'review'):
                continue
            in_app_data = any(paths.is_within(s['path'], b) for b in app_bases)
            confidence = float(s.get('confidence') or 0)
            score = round(confidence * 100) if s['verdict'] == 'clean' else AI_REVIEW_SCORE
            basis = [f'AI：{s.get("verdict_label", "")}（把握 {round(confidence * 100)}%）' + ('，来自缓存' if s.get('source') == 'cache' else '')]
            owner = analyst.installed_app(s['path']) if analyst is not None else None
            if owner:
                score = min(score, 70)
                basis.append(f'属于仍在使用的程序「{owner}」：里面可能还有设置或许可证')
            node = None
            if scan is not None and s.get('node_id') is not None:
                try:
                    node = scan.node(s['node_id'])
                except (KeyError, IndexError):
                    node = None
            if node is None and s.get('mtime'):
                node = _Stub(s['mtime'])
            hold = self._folder_guards(s['path'], node, owner, basis)
            if hold and hold.startswith('最近'):
                score = min(score, 75)
            if not in_app_data:
                basis.append('不在应用数据目录：只能由你决定')
                hold = hold or '不在应用数据目录'
            if s['verdict'] != 'clean':
                hold = hold or 'AI 也拿不准'
            elif confidence < MIN_AI_CONFIDENCE:
                hold = hold or f'AI 的把握不到 {round(MIN_AI_CONFIDENCE * 100)}%'
            self.add({
                'id': _ident('dir', s['path']), 'kind': 'paths',
                'category': '应用数据', 'title': s.get('app') if s.get('app') not in (None, '', '未知') else
                os.path.basename(s['path']), 'size': s['size'], 'count': s.get('files', 0), 'what': '',
                'why': s.get('reason') or f'AI 判断：{s.get("verdict_label", "")}', 'effect': s.get('effect') or '',
                'action': 'recycle', 'source': 'ai', 'confidence': s.get('confidence'),
                'paths': [s['path']], 'node_id': s.get('node_id'), 'score': score, 'basis': basis,
                'eligible': hold is None,
                'auto_reason': f'AI 判断可清理（把握 {round(confidence * 100)}%），不属于仍在使用的程序，最近也没有改动',
                'hold_reason': hold})

    def add_heuristics(self, scan, analyst, env: dict, analysed: set[int]):
        """Without the model: likely leftovers of uninstalled programs and old versions."""
        app_bases = [env.get(k) for k in ('LOCALAPPDATA', 'APPDATA', 'LOCALLOW', 'ProgramData') if env.get(k)]
        for node in analyst.frontier(scan, OLD_VERSION_MIN, limit=120):
            path = scan.path_of(node)
            if node.id in analysed or not any(paths.is_within(path, b) for b in app_bases):
                continue
            old = node.mtime and self.now - node.mtime > YEAR
            candidates = [(node, path)] + [(c, scan.path_of(c)) for c in node.children or () if c.size >= OLD_VERSION_MIN]
            for n, p in candidates:
                n_old = n.mtime and self.now - n.mtime > YEAR
                newer = newer_sibling(n) if n_old else None
                if newer:
                    basis = [f'旁边有更新的版本「{newer}」', f'{_ago(n.mtime, self.now)}没有修改过']
                    hold = self._folder_guards(p, n, None, basis)
                    self.add({'id': _ident('dir', p), 'kind': 'paths', 'category': '应用数据', 'title': n.name,
                              'eligible': not hold,
                              'size': n.size, 'count': n.files, 'what': '',
                              'why': f'可能是旧版本留下的：{_ago(n.mtime, self.now)}没有修改过。',
                              'effect': '如果还在用旧版本，它的设置和缓存会丢失。', 'action': 'recycle',
                              'source': 'heuristic', 'confidence': 0.6, 'paths': [p], 'score': 60, 'basis': basis,
                              'auto_reason': '旧版本留下的数据，进回收站可以还原', 'hold_reason': hold})
            if node.size >= LEFTOVER_MIN and old and analyst.installed_app(path) == '':
                basis = ['「已安装的应用」里找不到对应的程序', f'{_ago(node.mtime, self.now)}没有修改过', '没有经过 AI 复核']
                hold = self._folder_guards(path, node, None, basis)
                self.add({'id': _ident('dir', path), 'kind': 'paths', 'category': '应用数据', 'title': node.name,
                          'eligible': not hold,
                          'size': node.size, 'count': node.files,
                          'what': '',
                          'why': f'在「已安装的应用」里没有找到对应的程序，而且 {_ago(node.mtime, self.now)}没有修改过。',
                          'effect': '如果程序其实还在（例如绿色版），它的设置、存档或缓存会丢失。', 'action': 'recycle',
                          'source': 'heuristic', 'confidence': 0.5, 'paths': [path], 'score': 50, 'basis': basis,
                          'auto_reason': '很可能是已卸载程序的残留，进回收站可以还原', 'hold_reason': hold})

    def add_insights(self, findings: list[dict], file_ai: dict | None = None):
        file_ai = file_ai or {}
        for f in findings:
            if f['kind'] == 'empty_folders':
                # nothing to free: one item for all of them instead of a row per folder
                folders = [x['path'] for x in f['files']]
                self.add({'id': 'empty:' + f['id'], 'kind': 'paths', 'group': 'review', 'category': f['kind_label'],
                          'finding': f['id'], 'finding_kind': f['kind'], 'title': f'{len(folders)} 个空文件夹', 'size': 0,
                          'count': 0, 'what': '', 'advice': f['advice'], 'why': f['reason'],
                          'effect': '不释放空间，只是更整洁。', 'action': 'recycle',
                          'source': 'insight', 'confidence': f['confidence'], 'paths': folders, 'personal': True,
                          'score': KIND_SCORE['empty_folders'], 'basis': ['里面没有任何文件（包括子文件夹）']})
                continue
            files = [x for x in f['files'] if x.get('remove') or f['kind'] in ('stale_large', 'old_downloads',
                                                                             'installer_old')]
            if f['kind'] == 'name_versions':
                files = [x for x in f['files'] if (x.get('score') or 0) > 10]  # never the copy to keep
            files = [x for x in files if x['size'] >= PLAN_MIN_FILE]
            for x in sorted(files, key=lambda x: (not x.get('remove'), -x['size']))[:PERSONAL_PER_KIND]:
                ai = file_ai.get(x['path'])
                why = x.get('note') or f['reason']
                base_score = x.get('score', file_score(f['kind'], x))
                basis = [f'{f["kind_label"]}：{why}']
                question = content_question(f['kind'], x)
                if ai:
                    basis.append(f'AI：{ai.get("verdict_label", "")}' + (f'——{ai["reason"]}' if ai.get('reason') else ''))
                    if question and ai.get('verdict') == 'delete' and not ai.get('with_content'):
                        basis.append(f'AI 只看了文件名，没有看内容：{question}，所以分数不因此提高')
                self.add({
                    'id': _ident('file', x['path']), 'kind': 'paths', 'group': 'review', 'category': f['kind_label'],
                    'finding': f['id'], 'finding_kind': f['kind'], 'title': x['name'], 'size': x['size'],
                    'count': 1 if x.get('category') != 'folder' else 0, 'what': '', 'advice': f['advice'], 'why': why,
                    'effect': '', 'action': 'recycle', 'source': 'insight',
                    'confidence': f['confidence'] if x.get('remove') else min(f['confidence'], 0.4),
                    'paths': [x['path']], 'ai': ai, 'personal': True, 'score': with_ai(base_score, ai, bool(question)),
                    'basis': basis})

    def add_duplicates(self, dups: dict | None, dup_ai: dict | None = None):
        """One item per group of identical files: keep what is (or may be) in use, offer the rest. Each copy's
        risk comes from where it is, whether its project mentions it and the model's advice
        (duplicates.removal_score); the item scores as its riskiest copy to remove."""
        def plain_first(f):
            return is_copy_name(os.path.splitext(os.path.basename(f['path']))[0]), f['mtime']

        def ai(f):
            return (dup_ai or {}).get(f['path'])
        for g in (dups or {}).get('groups', []):
            files = g['files']
            if any(self._taken(f['path']) for f in files):
                continue  # already offered elsewhere (e.g. as a version); two "keep one" rules could remove both
            stay = [f for f in files if must_stay(f, ai(f))]
            if stay and len(stay) < len(files):
                keepers = stay
            else:  # keep the copy most likely needed: a program's, a project's source, ..., then the plain name
                keepers = [min(files, key=lambda f: (PLACE_RANK[f.get('place')], not (f.get('refs') or {}).get('count'),
                                                     AI_RANK.get((ai(f) or {}).get('verdict'), 1), *plain_first(f)))]
            # the group's copies with what is known about each: the item keeps them, so a later choice of the user
            # (api.m_plan_dup_choose) is scored by the same rules
            copies = [{**{k: f[k] for k in ('path', 'mtime', 'place', 'place_root', 'refs') if k in f}, 'ai': ai(f)}
                      for f in files]
            self._claimed.extend(f['path'] for f in keepers)  # what stays must not be offered by anything else
            self.add({
                'id': f'dup:{_ident("g", "|".join(sorted(f["path"] for f in files)))[2:]}', 'kind': 'dup',
                'group': 'review', 'category': '重复文件', 'what': '', 'action': 'recycle', 'source': 'dup',
                'dup_size': g['size'], 'dup_files': copies, 'personal': True,
                **dup_choice(g['size'], copies, {f['path'] for f in keepers})})

    def add_startup(self, items: list[dict], explained: dict, env: dict):
        system_root = env.get('SystemRoot')
        for item in items:
            info = explained.get(item['id'])
            if not item['enabled'] or not info or info.get('suggestion') != 'disable':
                continue
            if system_root and item.get('exe') and paths.is_within(item['exe'], system_root):
                continue  # parts of Windows and drivers: the model's advice alone is not enough here
            self.add({
                'id': 'startup:' + item['id'], 'kind': 'startup', 'startup_id': item['id'], 'group': 'review',
                'category': '开机启动', 'title': item['display_name'], 'what': info.get('what', ''),
                'why': 'AI 建议禁用：' + info.get('reason', ''), 'effect': '',
                'action': 'disable', 'source': 'ai', 'confidence': 0.7,
                'blocked': '需要管理员权限' if item.get('needs_admin') else None,
                'score': 60, 'basis': ['AI 建议禁用', '随时可以重新启用'],
                'hold_reason': '开机启动项由你决定：禁用会改变程序的行为'})

    def add_info(self, scan, env: dict):
        for template in INFO_TARGETS:
            expanded = paths.expand(template, env) if '%' in template else template
            if not expanded:
                continue
            matches = glob.glob(paths.native(expanded)) if '*' in expanded else [paths.native(expanded)]
            for path in matches:
                size = None
                node = scan.find(path) if scan is not None and scan.complete else None
                if node is not None:
                    size = node.size
                elif os.path.isfile(path):
                    try:
                        size = os.stat(path).st_size
                    except OSError:
                        size = None
                if not size or size < INFO_MIN and not path.lower().endswith('hiberfil.sys'):
                    continue
                note = self.knowledge.annotate(path) if self.knowledge else None
                self.add({'id': _ident('info', path), 'kind': 'info', 'group': 'info', 'category': '说明',
                          'title': (note or {}).get('label') or os.path.basename(path), 'size': size,
                          'what': '', 'advice': (note or {}).get('advice', ''), 'why': '体积较大，但不能直接删除。',
                          'action': 'none', 'source': 'knowledge', 'location': path, 'actions': info_actions(path)})

    # -- result ---------------------------------------------------------------------
    def plan(self, extra: dict | None = None) -> dict:
        order = {'safe': 0, 'review': 1, 'info': 2}
        self.items.sort(key=lambda i: (order[i['group']], i.get('personal', False), -(i['score'] or 0), -(i['size'] or 0)))
        return {'id': uuid.uuid4().hex[:10], 'created': time.time(), 'items': self.items, 'threshold': self.threshold,
                'groups': [{'id': g, 'name': n} for g, n in GROUPS.items()], **(extra or {})}


def dup_choice(size: int, copies: list[dict], keep: set[str]) -> dict:
    """The fields of a duplicates item that follow from which copies stay (``keep``: their paths): what goes,
    its 建议清理度 (the riskiest copy to remove, duplicates.removal_score) and the reasons. ``copies`` carry their
    place, refs and the model's advice ('ai')."""
    keepers = [f for f in copies if f['path'] in keep]
    remove = [f for f in copies if f['path'] not in keep]
    rated = [(f, *removal_score(f, f.get('ai'))) for f in remove]
    score = min(s for _, s, _ in rated)
    basis = ['内容逐字节相同']
    if not keepers:
        # the user chose to let every copy go: the file itself goes, only the Recycle Bin still has it
        name = os.path.basename(copies[0]['path'])
        return {
            'title': f'{name} 等 {len(copies)} 份', 'size': size * len(remove), 'count': len(remove),
            'why': f'这 {len(copies)} 份全部移到回收站', 'effect': '一份都不保留：这个文件只能从回收站还原了。',
            'confidence': 0.2, 'paths': [f['path'] for f in remove], 'keep': None, 'keeps': [],
            'score': min(score, ALL_COPIES_SCORE),
            'basis': basis + [f'你选了全部 {len(copies)} 份：「{name}」一份都不剩，只能从回收站还原']
                     + list(dict.fromkeys(why for _, _, why in rated if why))[:3]}
    if all(f.get('place') in IN_USE for f in copies) and any(f.get('place') != 'build' for f in copies):
        # e.g. the same font in two projects' lib folders: each project may load its own copy
        basis.append('每一份都在程序文件夹或代码工程里：它们可能各自引用自己的那一份，删掉一份可能让那个程序或工程出问题')
    elif all(why is None for _, _, why in rated):
        where = place_label(keepers[0]) if keepers[0].get('place') else ''
        basis.append(f'保留{where}里的那份，只删零散的副本，内容不会丢失' if where else '保留一份，内容不会丢失')
    mentioned = [f for f in keepers if (f.get('refs') or {}).get('count')]
    if mentioned:
        more = f'等 {len(mentioned)} 份' if len(mentioned) > 1 else ''
        basis.append(f'保留的「{os.path.basename(mentioned[0]["path"])}」{more}有文件提到：「{mentioned[0]["refs"]["found"][0]}」')
    basis += list(dict.fromkeys(why for _, _, why in rated if why))[:3]
    advice = [f'AI：「{os.path.basename(f["path"])}」{f["ai"].get("verdict_label", "")}'
              + (f'——{f["ai"]["reason"]}' if f['ai'].get('reason') else '') for f in copies if f.get('ai')]
    basis += list(dict.fromkeys(advice))[:3]
    first = keepers[0]['path']
    folder = os.path.basename(os.path.dirname(first)) or os.path.dirname(first)
    return {
        'title': f'{os.path.basename(first)} 等 {len(copies)} 份', 'size': size * len(remove), 'count': len(remove),
        'why': f'保留「{folder}」里的那份' + (f'等 {len(keepers)} 份' if len(keepers) > 1 else '') + f'，其余 {len(remove)} 份移到回收站',
        'effect': '内容还在保留的那份里，但用到被删那份的程序或工程可能会找不到它。' if score < 85 else '',
        'confidence': 0.9 if score >= 85 else 0.4, 'paths': [f['path'] for f in remove], 'keep': first,
        'keeps': [f['path'] for f in keepers], 'score': score, 'basis': basis}


def assess_ai_dirs(suggestions: list[dict], policy, env: dict, analyst=None, scan=None,
                   threshold: int = DEFAULT_THRESHOLD) -> None:
    """Write the check-up's judgement onto 空间分析's AI suggestions: the same 建议清理度, basis and
    auto-tick, so a folder is ticked there exactly when it would be in the plan on 首页."""
    builder = PlanBuilder(policy, threshold=threshold)
    builder.add_ai_dirs(suggestions, env, analyst, scan)
    by_path = {paths.key(item['paths'][0]): item for item in builder.items}
    for s in suggestions:
        item = by_path.get(paths.key(s['path']))
        if item is not None:
            s.update(score=item['score'], basis=item['basis'], selected=item['selected'])
        elif s.get('source') == 'knowledge':  # recognised without the model; may hold what someone parked there
            s.update(score=60, basis=[f'盘序认得：{s.get("app") or "已知位置"}，没有问 AI',
                                      '没有自动勾选：里面也可能有别人临时放的东西，请你看一眼再决定'], selected=False)
        else:  # 建议保留, or folders the model looked inside
            pct = round(float(s.get('confidence') or 0) * 100)
            s.update(score=None, basis=[f'AI：{s.get("verdict_label", "")}（把握 {pct}%）'], selected=False)


def refresh_running(plan: dict, running: dict[str, list[str]]) -> list[str]:
    """Re-evaluate 「请先关闭」 on rule items after programs were opened or closed.

    A rule that is no longer blocked goes back to where the check-up would have put it (a default
    rule: 放心清理, ticked). Other blocks (administrator rights) are left alone. Returns changed ids."""
    changed = []
    for item in plan['items']:
        if item.get('kind') != 'rule' or item['status'] != 'pending' or item['rule'] not in running:
            continue
        was = item['blocked']
        if was and not was.startswith('请先关闭'):
            continue
        procs = running[item['rule']]
        blocked = '请先关闭 ' + '、'.join(procs) if procs else None
        if blocked == was:
            continue
        threshold = plan.get('threshold', DEFAULT_THRESHOLD)
        safe = bool(item.get('eligible')) and not blocked and points(item['score']) >= threshold
        item.update(blocked=blocked, group='safe' if safe else 'review', selected=safe)
        item['basis'] = [b for b in item['basis'] if not b.startswith(('自动勾选', '没有自动勾选', '没有勾选'))] \
            + decision_note(item, threshold)
        changed.append(item['id'])
    if changed:
        plan['summary'] = summarize(plan)
    return changed


def summarize(plan: dict) -> dict:
    out = {}
    for g in GROUPS:
        rows = [i for i in plan['items'] if i['group'] == g]
        out[g] = {'count': len(rows), 'size': sum(i['size'] or 0 for i in rows),
                  'selected': sum(1 for i in rows if i['selected'] and i['status'] == 'pending'),
                  'selected_size': sum(i['size'] or 0 for i in rows if i['selected'] and i['status'] == 'pending')}
    return out


# ---------------------------------------------------------------------------
# running the check-up (a background job; ``backend`` is api.Backend)
# ---------------------------------------------------------------------------

def run(backend, job, options: dict | None = None) -> dict:
    settings = backend.settings.get()
    opts = {**settings.get('checkup', {}), **{k: bool(v) for k, v in (options or {}).items()
                                            if k in ('space_scan', 'duplicates', 'use_ai')}}
    threshold = opts.get('auto_threshold', DEFAULT_THRESHOLD)
    if (options or {}).get('auto_select') in THRESHOLDS:
        threshold = THRESHOLDS[options['auto_select']]
    if isinstance((options or {}).get('auto_threshold'), int):
        threshold = options['auto_threshold']
    opts['auto_threshold'] = threshold = max(0, min(10, int(threshold)))
    client = backend.client_or_none('classify') if opts.get('use_ai', True) else None
    stages = [s for s in STAGES if (s[0] != 'space' or opts.get('space_scan', True))
              and (s[0] != 'dups' or opts.get('duplicates', True)) and (s[0] != 'ai' or client is not None)]
    total = len(stages)
    timings = {}
    # how long each stage took here last time (seconds): the progress bar's proportions and the time estimate
    learned = load_timings(backend.data_dir)
    expected = {s: learned[s] for s, _ in stages if s in learned}

    def stage(key, **extra):
        index = [s[0] for s in stages].index(key)
        job.update(stage=key, stage_label=dict(STAGES)[key], step=index + 1, steps=total,
                   stages=[{'id': s, 'label': lbl} for s, lbl in stages], detail='', part=None, count=None,
                   expected=expected, **extra)
        timings[key] = time.time()

    def part(value, **extra):
        """How far the current stage is (0-1), when it can tell; ``count``: a number that shows it is working."""
        job.update(part=max(0.0, min(1.0, value)) if value is not None else None, **extra)

    policy = backend.policy()
    env = backend.env

    stage('junk')
    junk_rows = backend.junk.scan(cancel=job.cancel,
                                  progress=lambda i, n, name: part(i / n if n else None, detail=name, done=i, total=n))
    backend.junk_rows = junk_rows

    if opts.get('space_scan', True):
        stage('space')
        root = env.get('SystemDrive', 'C:') + ('' if env.get('SystemDrive', '').endswith('\\') else '\\')
        root = paths.native(root)
        if winapi.IS_WINDOWS and winapi.simulation() is None:
            root = os.path.realpath(root)
        # the drive's used space is about what the scan will count
        used = next((d['used'] for d in backend.m_drives() if d.get('system')), 0)
        scan = SpaceScan(root)
        scan.run(cancel=job.cancel, progress=lambda p: part(
            min(0.98, p.get('bytes', 0) / used) if used else None, detail=p.get('current', ''),
            count=f'已统计 {p.get("files", 0):,} 个文件'))
        backend.scan = scan
        backend.ai_results = {}

    roots = personal_roots(env, settings)
    skip = system_skip(env, policy, roots)
    stage('insights')
    ins = Insights(env, settings, apps=backend.apps).collect(
        roots, skip=skip, cancel=job.cancel,
        progress=lambda p: part(None, detail=p.get('current', ''), count=f'已看过 {p.get("files", 0):,} 个文件'))
    findings = ins.findings()
    backend.insights = {'findings': findings, 'stats': ins.stats, 'roots': ins.roots, 'time': time.time()}

    if opts.get('duplicates', True):
        stage('dups')
        min_mb = float(settings.get('duplicates', {}).get('min_size_mb', 1))
        dups = find_duplicates(roots, min_size=int(min_mb * 1024 * 1024), skip=skip, cancel=job.cancel,
                               progress=lambda p: (lambda f, label, count: part(f, detail=label, count=count))(*dup_progress(p)))
        for g in dups['groups']:
            for f in g['files']:
                f['blocked'] = policy.reason(f['path'])
        backend.dups = dups

    ai_info = {'used': False, 'errors': [], 'usage': None}
    file_ai, startup_ai = {}, {}
    startup_items = []
    try:
        startup_items = backend.startup.list()
    except OSError:
        startup_items = []
    if client is not None:
        stage('ai')
        ai_info['used'] = True
        anonymize = settings['ai'].get('anonymize_paths', True)
        files = backend.files_for_ai([f['id'] for f in findings], include_content=False)[:FILES_FOR_AI]
        # duplicates that need judging: a copy in a program, a project or next to one (loose copies are clear)
        unclear = [g for g in (backend.dups or {}).get('groups', []) if any(f.get('place') for f in g['files'])] \
            if opts.get('duplicates', True) else []
        enabled = [i for i in startup_items if i['enabled']]
        # the AI stage in steps, each with its share of the bar: (name, will it run)
        steps = [s for s, on in (('space', backend.scan is not None and backend.scan.complete), ('files', bool(files)),
                                 ('dups', bool(unclear)), ('startup', bool(enabled))) if on]

        def ai_part(name, label, fraction=0.0, **extra):
            i = steps.index(name)
            part((i + min(1.0, fraction)) / len(steps), detail=label, **extra)

        def asked(p):
            return f'已问 {p.get("requests", 0)} 次'
        if 'space' in steps:
            ai_part('space', '空间分析')
            result = backend.analyst.analyze_space(
                backend.scan, client, settings, cancel=job.cancel,
                progress=lambda p: ai_part('space', '空间分析', p.get('requests', 0) / max(1, p.get('budget', 1)), count=asked(p)))
            backend.ai_results = {s['node_id']: s for s in result['suggestions']}
            for s in result['suggestions']:
                s['blocked'] = policy.reason(s['path'])
                s['caution'] = policy.caution(s['path'])
            ai_info['errors'] += result['errors']
        if files:
            ai_part('files', '我的文件（只发送文件名和大小）')
            result = backend.analyst.review_files(
                files, client, anonymize, max_requests=int(settings['ai'].get('max_requests', 8)), cancel=job.cancel,
                progress=lambda p: ai_part('files', '我的文件（只发送文件名和大小）', p.get('done', 0) / max(1, p.get('total', 1)),
                                           count=f'{p.get("done", 0)} / {p.get("total", 0)} 个文件'))
            file_ai = result['files']
            backend.file_ai.update(file_ai)
            ai_info['errors'] += result['errors']
        if unclear:
            ai_part('dups', '重复文件（只发送路径）')
            result = backend.analyst.review_duplicates(
                unclear[:DUPS_FOR_AI], client, anonymize, max_requests=DUPS_FOR_AI // 12, cancel=job.cancel,
                progress=lambda p: ai_part('dups', '重复文件（只发送路径）', p.get('done', 0) / max(1, p.get('total', 1))))
            backend.dup_ai.update(result['copies'])
            ai_info['errors'] += result['errors']
        if enabled:
            ai_part('startup', '开机启动项')
            items = backend.startup_items_for_ai(enabled)
            result = backend.analyst.explain_items(items, client, anonymize)
            startup_ai = {i['id']: result['items'].get(i['key']) for i in items if result['items'].get(i['key'])}
            ai_info['errors'] += result['errors']
        ai_info['usage'] = client.usage.as_dict()
        ai_info['model'] = client.model
        if client.usage.requests:
            backend.oplog.add('ai', f'一键体检：AI 复核（{client.usage.requests} 次请求）',
                              details={'usage': ai_info['usage'], 'model': client.model, 'errors': ai_info['errors'][:5]})

    stage('plan')
    builder = PlanBuilder(policy, backend.knowledge, threshold=threshold)
    builder.add_junk(junk_rows)
    if backend.scan is not None and backend.scan.complete:
        builder.add_ai_dirs(list(backend.ai_results.values()), env, backend.analyst, backend.scan)
        analysed = {nid for nid, s in backend.ai_results.items() if s.get('source') != 'none'}
        builder.add_heuristics(backend.scan, backend.analyst, env, analysed)
    builder.add_insights(findings, file_ai)
    builder.add_duplicates(backend.dups if opts.get('duplicates', True) else None, backend.dup_ai)
    builder.add_startup(startup_items, startup_ai, env)
    builder.add_info(backend.scan, env)
    plan = builder.plan({'ai': ai_info, 'options': opts,
                         'stats': {'insight_files': ins.stats.get('files', 0),
                                   'scanned_bytes': backend.scan.stats.get('bytes') if backend.scan else None}})
    plan['summary'] = summarize(plan)
    backend.plan = plan
    starts = sorted(timings.items(), key=lambda kv: kv[1]) + [('', time.time())]
    save_timings(backend.data_dir, {k: max(0.1, round(starts[i + 1][1] - t, 1)) for i, (k, t) in enumerate(starts[:-1])})
    return plan


TIMINGS_FILE = 'checkup_timings.json'


def load_timings(data_dir: str) -> dict:
    """Seconds each stage of the last check-up took on this PC (empty the first time)."""
    try:
        with open(os.path.join(data_dir, TIMINGS_FILE), encoding='utf-8') as fh:
            data = json.load(fh)
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float)) and v >= 0}
    except (OSError, ValueError, AttributeError):
        return {}


def save_timings(data_dir: str, timings: dict) -> None:
    try:
        tmp = os.path.join(data_dir, TIMINGS_FILE + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(timings, fh)
        os.replace(tmp, os.path.join(data_dir, TIMINGS_FILE))
    except OSError:
        pass  # only the next progress bar's proportions depend on it


DUP_PHASES = {'listing': ('列出文件', 0.0, 0.3), 'quick': ('快速比对', 0.3, 0.5), 'full': ('逐字节比对内容', 0.5, 0.95),
              'refs': ('查看工程里有没有用到', 0.95, 1.0)}


def dup_progress(p: dict) -> tuple[float | None, str, str]:
    """find_duplicates' progress as (part of the stage, or None while listing has no total; what it does; a count)."""
    label, start, end = DUP_PHASES.get(p.get('phase'), ('', 0.0, 0.0))
    count = f'已找到 {p.get("files", 0):,} 个文件'
    if p.get('phase') == 'full' and p.get('bytes_total'):
        f = p.get('bytes_done', 0) / p['bytes_total']
    elif p.get('total'):
        f = p.get('done', 0) / p['total']
    else:
        return None, label, count
    return start + (end - start) * min(1.0, f), label, count


# ---------------------------------------------------------------------------
# executing selected plan items
# ---------------------------------------------------------------------------

def execute(backend, plan: dict, item_ids: list[str], job) -> dict:
    by_id = {i['id']: i for i in plan['items']}
    chosen = []
    for item_id in item_ids:
        item = by_id.get(item_id)
        if item is None or item['status'] != 'pending' or item['action'] == 'none' or item['blocked']:
            continue
        chosen.append(item)
    if not chosen:
        raise ValueError('没有可以执行的项目')
    policy = backend.policy()
    report = {'deleted': {'count': 0, 'bytes': 0}, 'recycled': {'count': 0, 'bytes': 0}, 'disabled': [],
              'failures': [], 'items': {}}

    def done(item, status, note=''):
        item['status'] = status
        item['result'] = note
        report['items'][item['id']] = {'status': status, 'note': note}

    # 1. junk rules (permanent deletion of rebuildable caches)
    rules = [i for i in chosen if i['kind'] == 'rule']
    if rules:
        job.update(stage='junk', detail='清理垃圾文件')
        result = backend.junk.clean([i['rule'] for i in rules], cancel=job.cancel,
                                    progress=lambda i, n, name, freed: job.update(detail=name, freed=freed))
        rows = {r['id']: r for r in result['rules']}
        for item in rules:
            row = rows.get(item['rule'])
            if row is None:
                done(item, 'failed', '没有执行')
            elif row['skipped']:
                done(item, 'skipped', row['skipped'])
                report['failures'].append({'title': item['title'], 'reason': row['skipped']})
            else:
                report['deleted']['count'] += row['files']
                report['deleted']['bytes'] += row['freed']
                done(item, 'done', f'释放 {row["freed"]} 字节')
                if row['failed']:
                    report['failures'].append({'title': item['title'], 'reason': f'{row["failed"]} 个文件正在使用，已跳过'})

    # 2. folders and personal files -> Recycle Bin (each path re-checked against the policy)
    to_recycle: list[tuple[dict, str]] = []
    for item in chosen:
        if item['kind'] == 'paths':
            for p in item['paths']:
                reason = policy.reason(p)
                if reason:
                    report['failures'].append({'title': item['title'], 'path': p, 'reason': reason})
                else:
                    to_recycle.append((item, p))
        elif item['kind'] == 'dup':
            files = {paths.key(f['path']): f for f in item['dup_files']}
            changed = [p for p in item['paths'] if not verify_unchanged(p, item['dup_size'],
                                                                          files[paths.key(p)]['mtime'])]
            keeper_ok = not item['keep'] or verify_unchanged(item['keep'], item['dup_size'],
                                                             files[paths.key(item['keep'])]['mtime'])
            if changed or not keeper_ok:
                done(item, 'skipped', '文件在体检后被修改过，请重新体检')
                report['failures'].append({'title': item['title'], 'reason': '文件在体检后被修改过'})
                continue
            for p in item['paths']:
                reason = policy.reason(p)
                if reason:
                    report['failures'].append({'title': item['title'], 'path': p, 'reason': reason})
                else:
                    to_recycle.append((item, p))
    if to_recycle:
        job.update(stage='recycle', detail='移到回收站')
        unique, taken = [], set()
        for _, p in sorted(to_recycle, key=lambda ip: len(ip[1])):
            if not paths.within_any(p, taken):
                unique.append(p)
                taken.add(paths.key(p))
        pending = backend.oplog.begin('recycle', f'清理方案：移到回收站 {len(unique)} 项', items=unique)

        def on_progress(s):
            job.update(freed=s.bytes)
            backend.oplog.progress(pending, freed=s.bytes, done=s.files)
        try:
            stats, recycled = fsops.recycle(unique, cancel=job.cancel, hwnd=winapi.find_window(APP_NAME), progress=on_progress)
        except fsops.Cancelled:
            backend.oplog.drop(pending)  # stopped while sizing, before anything moved
            raise
        gone = {paths.key(p) for p in recycled}
        report['recycled']['count'] += len(recycled)
        report['recycled']['bytes'] += stats.bytes
        for f in stats.failures:
            report['failures'].append({'path': f.get('path'), 'reason': f.get('reason')})
        for item in {id(i): i for i, _ in to_recycle}.values():
            ok = [p for p in item['paths'] if paths.key(p) in gone or any(paths.is_within(p, g) for g in recycled)]
            if len(ok) == len(item['paths']):
                done(item, 'done', '已移到回收站')
            elif ok:
                done(item, 'partial', f'{len(ok)}/{len(item["paths"])} 项已移到回收站')
            else:
                done(item, 'failed', '没有移到回收站')
        if recycled or stats.failed:
            backend.oplog.add('recycle', f'清理方案：移到回收站 {len(recycled)} 项', freed=stats.bytes, items=recycled,
                              failures=stats.failures, details={'source': 'plan', 'mode': 'recycle'}, replaces=pending)
        else:
            backend.oplog.drop(pending)
        backend.forget_paths(recycled)

    # 3. startup items
    for item in chosen:
        if item['kind'] != 'startup':
            continue
        try:
            backend.startup.set_enabled(item['startup_id'], False)
            report['disabled'].append(item['title'])
            done(item, 'done', '已禁用开机启动')
        except (ValueError, PermissionError, OSError) as exc:
            done(item, 'failed', str(exc))
            report['failures'].append({'title': item['title'], 'reason': str(exc)})

    plan['summary'] = summarize(plan)
    report['summary'] = plan['summary']
    return report
