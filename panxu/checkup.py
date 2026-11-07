"""一键体检 and the cleanup plan (清理方案).

The check-up runs the existing tools one after another — junk rules, a scan of
the local fixed drives, 「我的文件」, duplicates and (when a model is configured) the
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
import ntpath
import os
import re
import time
import uuid

from . import APP_NAME, fsops, itemfacts, paths, winapi
from .analyst import FILE_BATCH, newer_sibling
from .duplicates import (AI_RANK, IN_USE, PLACE_RANK, find_duplicates, must_stay, place_label, removal_score,
                         verify_unchanged)
from .insights import KIND_SCORE, Insights, content_question, file_score, is_copy_name, managed_dir, with_ai
from .junk import RULES_BY_ID, resolve_target
from .scanner import FLAG_CLOUD, FLAG_DENIED, FLAG_EXCLUDED, FLAG_LINKS, FLAG_PROGRAM, SpaceScan

GROUPS = {'safe': '放心清理', 'review': '建议你看看', 'info': '仅供了解'}
ACTIONS = {'delete': '永久删除', 'recycle': '移到回收站', 'disable': '禁用开机启动', 'service': '改为手动启动',
           'uninstall': '运行它自己的卸载程序', 'none': '不需要操作'}
SOURCES = {'rule': '规则', 'ai': 'AI', 'insight': '我的文件', 'dup': '重复文件', 'heuristic': '盘序判断',
           'knowledge': '说明'}

STAGES = [('junk', '扫描垃圾文件'), ('space', '分析本机磁盘空间'), ('insights', '整理我的文件'),
          ('dups', '查找重复文件'), ('apps', '检查应用与服务'), ('ai', 'AI 复核'), ('plan', '生成清理方案')]
# the switch an AI scope needs: without it there is nothing for that scope to look at
AI_SCOPE_PARENT = {'space': 'space_scan', 'duplicates': 'duplicates', 'apps': 'apps_services', 'services': 'apps_services'}
AI_SCOPES = ('space', 'files', 'duplicates', 'startup', 'apps', 'services')

# 勾选门槛 (0-10): eligible items whose 建议清理度 reaches it are pre-ticked. 稳妥 8, 适中 7, 积极 5.
DEFAULT_THRESHOLD = 7
THRESHOLDS = {'careful': 8, 'bold': 5}   # older settings / options
MIN_AI_CONFIDENCE = 0.7      # below this a 「可清理」 verdict is never pre-ticked, whatever the threshold
ACTIVE_DAYS = 7             # folders changed more recently are left for the user
# rules never pre-ticked: no undo (Recycle Bin, rollback to the old Windows), very slow (DISM), or
# possibly holding the user's own builds (mvn install puts them in the local repository)
NEVER_AUTO_RULES = {'recycle_bin', 'windows_old', 'component_store', 'maven', 'dism_logs'}
# names inside a folder that suggest something worth keeping although nobody "made" it: game saves,
# profiles, backups. A folder containing one is never pre-ticked.
VALUABLE_DIRS = {'save', 'saves', 'savegames', 'savedgames', 'saved games', 'savedata', 'save data', '存档',
                 'profile', 'profiles', 'user data', 'userdata', 'backup', 'backups', '备份', 'license', 'licenses',
                 'user', 'history', 'local history', 'local storage', 'indexeddb', 'databases', 'vault', 'vaults'}
LEFTOVER_MIN = 10 * 1024 * 1024
OLD_VERSION_MIN = 20 * 1024 * 1024
RESIDUE_CATEGORIES = {'软件残留', '旧版本数据'}
YEAR = 365 * 86400
INFO_MIN = 1024 ** 3
PERSONAL_PER_KIND = 80
# your files smaller than this stay on 我的文件: in a cleanup plan they free nothing and only add a risk
PLAN_MIN_FILE = 1024 * 1024
DUPS_FOR_AI = 36   # groups of duplicates the check-up asks about (three requests)
# 「需要你确认」: the model cannot tell, so its confidence in saying so is no reason to clean (5 points, held)
AI_REVIEW_SCORE = 50
# every copy of a duplicates group chosen: that is deleting the file itself, not a spare copy (2 points)
ALL_COPIES_SCORE = 15
DUP_FILE_LIMIT = 400_000
DUP_HASH_LIMIT = 16 * 1024 ** 3  # bound full-content reads of whole disks; a chosen folder is searched completely
# the same bounds for every whole-disk duplicate search (首页 and the tool page): files listed, bytes read, seconds
DUP_BOUNDS = {'max_files': DUP_FILE_LIMIT, 'max_hash_bytes': DUP_HASH_LIMIT, 'max_reference_bytes': 256 * 1024 ** 2,
              'max_hash_seconds': 180, 'max_reference_seconds': 30}
# a program that has not run for this long and takes this much room is worth a look without the model
APP_IDLE_DAYS = 365
APP_IDLE_MIN = 200 * 1024 ** 2

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


def selected_drives(drives: list[dict], options: dict) -> list[dict]:
    """Only enumerated drives can be selected; preserve old all/system defaults until the first choice."""
    chosen = options.get('selected_drives')
    if isinstance(chosen, list):
        keys = {paths.key(p) for p in chosen if isinstance(p, str) and p}
        return [d for d in drives if paths.key(d['root']) in keys]
    return [d for d in drives if d.get('fixed') is True
            and (options.get('other_drives', True) or d.get('system'))]


def insights_roots(env: dict, settings: dict, drives: list[dict], *, other_drives: bool = True,
                   whole_drives: list[str] | None = None) -> list[str]:
    """Broad personal-file scope, using only an explicitly supplied drive list.

    Visit the familiar/picked folders first, then their wider containers. Only
    fixed local disks are added automatically; redirected shell folders and
    explicit extra folders remain usable on other kinds of storage. Turning
    off other_drives limits whole-disk enumeration, not these personal roots.
    """
    candidates = personal_roots(env, settings)
    candidates.extend(env.get(name) for name in ('USERPROFILE', 'PUBLIC'))
    if whole_drives is not None:
        candidates.extend(whole_drives)
    else:
        system = env.get('SystemDrive')
        if system:
            candidates.append(paths.native(system + ('' if system.endswith(('\\', '/')) else '\\')))
        candidates.extend(d.get('root') for d in drives if d.get('fixed') is True
                          and (other_drives or d.get('system'))
                          and isinstance(d.get('root'), str)
                          and not ntpath.splitdrive(d['root'])[0].startswith('\\\\'))
    out, seen = [], set()
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        key = paths.key(candidate)
        if key not in seen and os.path.isdir(candidate):
            out.append(candidate)
            seen.add(key)
    return out


def space_roots(env: dict, settings: dict, drives: list[dict], *, other_drives: bool = True,
                whole_drives: list[str] | None = None) -> list[str]:
    """Fixed disks plus explicitly known/added personal locations, with overlaps removed."""
    out = []
    for p in sorted(insights_roots(env, settings, drives, other_drives=other_drives, whole_drives=whole_drives), key=len):
        if not any(paths.is_within(p, old) for old in out):
            out.append(p)
    return out


def ordered_roots(env: dict, settings: dict, chosen: list[str]) -> list[str]:
    """``chosen`` with the user's own folders inside them put first, in the check-up's order: within a bound on
    files or reads they are searched before the rest of a disk. The walkers visit an overlap only once."""
    own = personal_roots(env, settings) + [env.get('USERPROFILE'), env.get('PUBLIC')]
    inside = [p for p in own if isinstance(p, str) and p and any(paths.is_within(p, c) for c in chosen)]
    out, seen = [], set()
    for p in inside + list(chosen):
        k = paths.key(p)
        if k not in seen and os.path.isdir(p):
            out.append(p)
            seen.add(k)
    return out


def space_skip(env: dict, policy, app_dirs=()):
    """Space totals may include Windows/app data, but never another account or an exclusion."""
    personal = [p for p in (env.get('USERPROFILE'), env.get('PUBLIC')) if p] + personal_roots(env, {})
    excluded = {paths.key(p) for p in list(policy.excluded) + list(app_dirs)}

    def skip(path):
        if policy.allowed_roots and not any(paths.is_within(path, r) for r in policy.allowed_roots):
            return True
        if paths.within_any(path, excluded):
            return True
        _, parts = policy._split_root(path)
        under_users = (len(parts) > 1 and parts[0].lower() == 'users') or any(
            paths.is_within(path, p) and paths.key(path) != p for p in policy.users_dirs)
        return under_users and not any(paths.is_within(path, p) or paths.is_within(p, path) for p in personal)
    return skip


def system_skip(env: dict, policy, roots: list[str] | None = None):
    """Paths the personal-file tools skip: system folders, app data, user exclusions, and
    folders programs manage themselves (caches, node_modules, chat storage...) below the chosen roots."""
    bases = [env.get(k) for k in ('SystemRoot', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData', 'LOCALAPPDATA',
                                  'APPDATA', 'LOCALLOW')]
    home = env.get('USERPROFILE')
    if home:
        bases.append(ntpath.join(home, 'AppData'))
    bases = {paths.key(b) for b in bases if b} | {paths.key(b) for b in list(policy.subtrees) + list(policy.excluded)}
    # Shell folders can be redirected into another drive's Users tree. Trust
    # their confirmed locations and the ancestors needed to reach them, while
    # keeping sibling accounts outside the scan. Broad drive roots are not a
    # statement of profile ownership, and must never join this list.
    personal = [p for p in (home, env.get('PUBLIC')) if p] + personal_roots(env, {})
    user_parents = set(policy.users_dirs)
    # Unlike deletion, scanning may enter a container that holds protected
    # children. Block only the forbidden subtree itself, never its ancestors.
    system_trees = paths._ROOT_SYSTEM_SUBTREES | {'program files', 'program files (x86)', 'programdata',
                                                'config.msi', 'msocache', 'documents and settings'}
    chosen = {paths.key(r) for r in roots or []}

    def skip(path):
        if policy.allowed_roots and not any(paths.is_within(path, root) for root in policy.allowed_roots):
            return True
        if paths.within_any(path, bases):
            return True
        _, parts = policy._split_root(path)
        parts = [p.lower().rstrip('. ') for p in parts]
        if parts:
            top = parts[0]
            if 'appdata' in parts:
                return True
            if top in system_trees or (len(parts) == 1 and top in paths._ROOT_SYSTEM_NAMES - {'users'}):
                return True
            if top == 'users' and len(parts) > 1 \
                    and not any(paths.is_within(path, p) or paths.is_within(p, path) for p in personal):
                return True
        key = paths.key(path)
        if any(paths.is_within(path, parent) and key != parent for parent in user_parents) \
                and not any(paths.is_within(path, p) or paths.is_within(p, path) for p in personal):
            return True
        if key not in chosen and managed_dir(ntpath.basename(path.rstrip('\\/'))):
            return True
        return False
    return skip


def _ident(prefix: str, text: str) -> str:
    return prefix + ':' + hashlib.md5(text.lower().encode('utf-8')).hexdigest()[:12]


def _ago(ts: float, now: float) -> str:
    days = (now - ts) / 86400
    return f'{days / 365:.0f} 年' if days >= 365 else f'{int(days)} 天'


def _size_text(n: int) -> str:
    return f'{n / 1024 ** 3:.1f} GB' if n >= 1024 ** 3 else f'{max(1, round(n / 1024 ** 2))} MB'


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
    return bool(re.search(r'(^|[^a-z])(caches?|temp|tmp|logs?|crash(?:es)?|dumps?|shaders?|thumbnails?)($|[^a-z])', low)
                or low.endswith(('cache', 'caches')))


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


def rule_assessment(row: dict) -> dict:
    """One evidence-based assessment for the home plan and the junk tool page."""
    rule = RULES_BY_ID[row['id']]
    hold = None
    if rule.id in ('recycle_bin', 'windows_old'):
        score, hold = 55, '删除后无法恢复，请你决定'
        basis = ['删除会失去回收站还原或系统回退的机会']
    elif rule.id == 'maven':
        score, hold = 30, '本地仓库可能含有你自己构建、无法重新下载的包'
        basis = [hold]
    elif rule.id == 'dism_logs':
        score, hold = 45, '旧日志可能用于排查系统维护故障，需要你确认'
        basis = ['只包含 7 天前已轮换的 DISM 日志，不包含当前日志', hold]
    elif rule.id == 'component_store':
        score, hold = 60, '耗时较长，且无法预估释放空间，请你决定'
        basis = ['通过 Windows 自带工具清理已替代的组件']
    elif rule.id in ('shader_cache', 'adobe_media_cache', 'gradle'):
        score, basis = 60, ['可以重建，但重新编译、分析媒体或构建的成本较高']
    elif rule.id == 'crash_dumps':
        score, basis = 70, ['崩溃快照不影响正常使用，但排查故障时仍可能需要']
    elif rule.group in ('browser', 'app') and rule.default:
        score, basis = 85, ['已知缓存位置，清理后会重新下载或生成，不是用户资料']
    elif not rule.default:
        score, basis = 70, ['已知可重建的缓存，再次用到时需要下载或生成']
    else:
        score, basis = 95, ['内置规则：符合保留期限的临时文件或可重建数据']
    size = row.get('size') or 0
    # Benefit and rebuild cost are separate from identity: large does not mean disposable.
    if size and rule.id not in NEVER_AUTO_RULES and rule.group in ('browser', 'app', 'dev'):
        if row.get('recent_bytes', 0) >= size / 2:
            score = max(40, score - 10)
            basis.append('至少一半占用来自最近 7 天新增或修改的文件，清理后可能很快重建')
        elif row.get('stale_bytes', 0) >= size * 0.8:
            score = min(90, score + 5)
            basis.append('至少八成占用已超过 90 天没有新增或修改，近期重建的可能性较低')
    if rule.id in NEVER_AUTO_RULES:
        hold = hold or '默认不自动勾选，请你决定'
    if row.get('unreadable'):
        basis.append('部分位置无法读取：大小仅统计已检查的文件')
    return {'score': score, 'basis': basis, 'eligible': hold is None,
            'auto_reason': '内置规则确认的可清理数据', 'hold_reason': hold}


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

    def _unclaimed_nodes(self, scan, node):
        """Split around rule-covered children instead of losing an entire old-version cache."""
        stack = [node]
        while stack:
            n = stack.pop()
            path = scan.path_of(n)
            if any(paths.is_within(path, c) for c in self._claimed):
                continue
            if self._taken(path):
                stack.extend(n.children or ())
            else:
                yield n, path

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
            if not row.get('available') or (size is not None and size <= 0 and not row.get('count') and rule.mode != 'command'):
                continue
            blocked = None
            if row.get('running'):
                blocked = '请先关闭 ' + '、'.join(row['running'])
            elif rule.admin and row.get('needs_admin'):
                blocked = '需要管理员权限'
            self.add({
                'id': 'rule:' + rule.id, 'kind': 'rule', 'rule': rule.id,
                'category': '垃圾文件', 'title': rule.name, 'size': size or 0,
                'logical_size': row.get('logical_size', size), 'count': row.get('count') or 0,
                'size_note': row.get('size_note', ''),
                'what': '', 'why': rule.description,
                'effect': rule.note or '', 'action': 'delete', 'source': 'rule',
                'confidence': 1.0 if rule.default else 0.7, 'blocked': blocked,
                'locations': row.get('locations', [])[:5], **rule_assessment(row)})
            # A rule may delete only old files or only one pattern inside these roots.
            # Never also offer their parent as an entire-folder AI/heuristic cleanup.
            self._claimed.extend(row.get('locations', []))

    def _folder_guards(self, path: str, node, owner: str | None, basis: list[str]) -> str | None:
        """Reasons a folder must not be pre-ticked whatever the score (also added to the basis)."""
        if getattr(node, 'flags', 0) & (FLAG_DENIED | FLAG_LINKS | FLAG_CLOUD | FLAG_EXCLUDED):
            basis.append('目录没有完整检查：包含无权访问、排除、链接或云端内容，不能凭部分结果清理整个目录')
            return '目录没有完整检查'
        mtime = max(getattr(node, 'changed', 0), getattr(node, 'mtime', 0)) if node is not None else None
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

    def reserve_rule_locations(self, env):
        """Residual-folder suggestions must not repackage rule caches or bypass their age filters."""
        for rule in RULES_BY_ID.values():
            for target in rule.targets:
                self._claimed.extend(resolve_target(target, env))

    def add_ai_dirs(self, suggestions: list[dict], env: dict, analyst=None, scan=None):
        app_bases = [env.get(k) for k in ('LOCALAPPDATA', 'APPDATA', 'LOCALLOW', 'ProgramData') if env.get(k)]
        for s in sorted(suggestions, key=lambda r: -r['size']):
            if s.get('source') in ('none', 'knowledge') or s.get('expanded') or s['verdict'] not in ('clean', 'review'):
                continue
            if self.policy.reason(s['path']):
                continue
            in_app_data = any(paths.is_within(s['path'], b) for b in app_bases)
            confidence = float(s.get('confidence') or 0)
            # Confidence describes recognition, not how valuable a deletion is.
            score = 65 if s['verdict'] == 'clean' else AI_REVIEW_SCORE
            basis = [f'AI：{s.get("verdict_label", "")}（把握 {round(confidence * 100)}%）' + ('，来自缓存' if s.get('source') == 'cache' else '')]
            if s['verdict'] == 'clean':
                basis.append('AI 的把握用于判断结论是否可信，不直接换算成建议清理度')
                if cache_like(os.path.basename(s['path'].rstrip('\\/'))):
                    score += 10
                    basis.append('目录名称符合缓存或临时数据特征（仍需结合内容判断）')
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
            changed = max(getattr(node, 'changed', 0), getattr(node, 'mtime', 0))
            if s['verdict'] == 'clean' and changed and self.now - changed >= 180 * 86400:
                score += 5
                basis.append('至少半年没有新增或修改文件，清理后立即重建的可能性较低')
            score = min(score, 85)
            hold = self._folder_guards(s['path'], node, owner, basis)
            if hold:
                score = min(score, 40 if hold.startswith('最近') else 30)
            if not in_app_data:
                basis.append('不在应用数据目录：只能由你决定')
                hold = hold or '不在应用数据目录'
            if s['verdict'] != 'clean':
                hold = hold or 'AI 也拿不准'
            elif confidence < MIN_AI_CONFIDENCE:
                score = min(score, 40)
                hold = hold or f'AI 的把握不到 {round(MIN_AI_CONFIDENCE * 100)}%'
            self.add({
                'id': _ident('dir', s['path']), 'kind': 'paths',
                'category': '软件残留' if in_app_data and owner == '' and s['verdict'] == 'review'
                    and changed and self.now - changed > 180 * 86400 else '应用数据',
                'title': s.get('app') if s.get('app') not in (None, '', '未知') else
                os.path.basename(s['path']), 'size': s['size'],
                'logical_size': getattr(node, 'logical_size', s.get('logical_size', s['size'])),
                'count': s.get('files', 0), 'what': '',
                'why': s.get('reason') or f'AI 判断：{s.get("verdict_label", "")}', 'effect': s.get('effect') or '',
                'action': 'recycle', 'source': 'ai', 'confidence': s.get('confidence'),
                'paths': [s['path']], 'node_id': s.get('node_id'), 'score': score, 'basis': basis,
                'eligible': hold is None,
                'auto_reason': f'AI 判断可清理（把握 {round(confidence * 100)}%），不属于仍在使用的程序，最近也没有改动',
                'hold_reason': hold})

    def add_heuristics(self, scan, analyst, env: dict, analysed: set[int]):
        """Without the model: likely leftovers of uninstalled programs and old versions."""
        app_bases = [env.get(k) for k in ('LOCALAPPDATA', 'APPDATA', 'LOCALLOW', 'ProgramData') if env.get(k)]
        frontier = [n for n in analyst.frontier(scan, min(LEFTOVER_MIN, OLD_VERSION_MIN))
                    if any(paths.is_within(scan.path_of(n), b) for b in app_bases)
                    and not self.policy.reason(scan.path_of(n))]
        for node in frontier[:500]:
            path = scan.path_of(node)
            if node.id in analysed or not any(paths.is_within(path, b) for b in app_bases):
                continue
            changed = max(node.mtime or 0, node.changed or 0)
            old = changed and self.now - changed > 180 * 86400
            candidates = [(node, path)] + [(c, scan.path_of(c)) for c in node.children or () if c.size >= OLD_VERSION_MIN]
            for n, p in candidates:
                n_old = n.mtime and self.now - n.mtime > YEAR
                newer = newer_sibling(n) if n_old else None
                if newer:
                    for candidate, location in self._unclaimed_nodes(scan, n):
                        if self.policy.reason(location):
                            continue
                        basis = [f'旧版本「{n.name}」旁边有更新的版本「{newer}」', f'{_ago(n.mtime, self.now)}没有修改过']
                        hold = self._folder_guards(location, candidate, None, basis)
                        if not cache_like(candidate.name):
                            hold = hold or '旧版本数据可能包含设置或本地历史，需要你确认'
                        self.add({'id': _ident('dir', location), 'kind': 'paths', 'category': '旧版本数据', 'title': candidate.name,
                              'eligible': not hold,
                              'size': candidate.size, 'logical_size': candidate.logical_size, 'count': candidate.files, 'what': '',
                              'why': f'可能是旧版本留下的：{_ago(n.mtime, self.now)}没有修改过。',
                              'effect': '如果还在用旧版本，它的设置和缓存会丢失。', 'action': 'recycle',
                              'source': 'heuristic', 'confidence': 0.6, 'paths': [location], 'score': 30 if hold else 60, 'basis': basis,
                              'auto_reason': '旧版本留下的数据，进回收站可以还原', 'hold_reason': hold})
            if node.size >= LEFTOVER_MIN and old and analyst.installed_app(path) == '':
                basis = ['「已安装的应用」里找不到对应的程序', f'{_ago(changed, self.now)}没有新增或修改',
                         '绿色软件和未登记的程序也可能使用它，未匹配不等于已卸载', '没有经过 AI 复核']
                hold = self._folder_guards(path, node, None, basis)
                self.add({'id': _ident('dir', path), 'kind': 'paths', 'category': '软件残留', 'title': node.name,
                          'eligible': False,
                          'size': node.size, 'logical_size': node.logical_size, 'count': node.files,
                          'what': '',
                          'why': f'可能是软件残留：没有匹配到安装登记，而且 {_ago(changed, self.now)}没有新增或修改。',
                          'effect': '如果程序其实还在（例如绿色版），它的设置、存档或缓存会丢失。', 'action': 'recycle',
                          'source': 'heuristic', 'confidence': 0.5, 'paths': [path], 'score': 20 if hold else 40, 'basis': basis,
                          'hold_reason': hold or '没有安装登记不足以确认已经卸载，需要你选择'})
        return {'candidates': len(frontier), 'checked': min(len(frontier), 500), 'truncated': len(frontier) > 500}

    def add_removed_apps(self, apps, scan):
        """An actual uninstall record is stronger evidence than a failed name match; settings still need consent."""
        removed = apps.removed_apps()
        for app in removed[:100]:
            for row in apps.leftovers(app['id'], scan=scan):
                node = scan.find(row['path'])
                for candidate, path in self._unclaimed_nodes(scan, node):
                    if not candidate.size or self.policy.reason(path):
                        continue
                    basis = [f'盘序记录过「{app["name"]}」的卸载，现在已没有该程序的安装登记',
                             '目录仍然存在，未被其他已安装程序认领']
                    hold = self._folder_guards(path, candidate, None, basis) or row['caution']
                    if row['where'] == '安装文件夹':
                        hold = hold or '卸载后留下的安装目录需要你确认，不能仅凭目录名称自动清理'
                    if not cache_like(candidate.name):
                        hold = hold or '卸载后留下的设置或资料仍可能有用，需要你选择'
                    self.add({'id': _ident('dir', path), 'kind': 'paths', 'category': '软件残留',
                              'title': app['name'] + ' · ' + candidate.name, 'paths': [path],
                              'size': candidate.size, 'logical_size': candidate.logical_size, 'count': candidate.files,
                              'action': 'recycle', 'score': 30 if hold else 65, 'eligible': not hold,
                              'basis': basis, 'hold_reason': hold, 'source': 'heuristic',
                              'why': '有卸载记录的剩余目录，确认不再需要其中的资料后可移到回收站。',
                              'effect': '重新安装时，原有设置、离线资料或存档可能无法恢复。'})
        return {'uninstall_records': len(removed), 'history_truncated': len(removed) > 100}

    def add_known_folders(self, scan, analyst):
        for row in analyst.known_cleanable(scan):
            self.add({'id': _ident('dir', row['path']), 'kind': 'paths', 'category': '其他临时目录',
                      'title': os.path.basename(row['path']), 'size': row['size'], 'count': row['files'],
                      'logical_size': scan.node(row['node_id']).logical_size,
                      'why': '磁盘上的临时目录，可能同时存放缓存和你手工暂存的文件。',
                      'advice': '先打开确认里面的内容，再决定是否移到回收站。',
                      'action': 'recycle', 'source': 'knowledge', 'paths': [row['path']], 'personal': True,
                      'score': 40, 'basis': ['位置符合临时目录特征，但不能据此证明里面的文件都可丢弃']})

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
                    'logical_size': x.get('logical_size', x['size']),
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
            copies = [{**{k: f[k] for k in ('path', 'mtime', 'size', 'logical_size', 'place', 'place_root', 'refs') if k in f}, 'ai': ai(f)}
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

    def add_services(self, services: list[dict], explained: dict, admin: bool):
        """Third-party services the model would not start at boot: offered as 「改为手动启动」 -- not started at boot
        any more, still started when a program asks for it, logged with the old start type so it can be put back.
        Never ticked: like a startup item it changes how a program behaves."""
        for svc in services:
            info = explained.get(svc['name'])
            if not svc.get('editable') or not info or info.get('suggestion') != 'disable':
                continue
            if svc.get('start_type') not in ('auto', 'delayed'):
                continue  # already manual or disabled: there is nothing at boot to save
            running = svc.get('status') == 'running'
            self.add({
                'id': 'service:' + svc['name'], 'kind': 'service', 'service_name': svc['name'], 'group': 'review',
                'category': '服务', 'title': svc['display_name'], 'what': info.get('what', ''),
                'why': 'AI 建议开机时不再自动运行：' + info.get('reason', ''),
                'effect': '这次不会停止正在运行的服务，下次开机起不再自动启动。' if running else '',
                'action': 'service', 'source': 'ai', 'confidence': 0.7, 'location': svc.get('exe') or '',
                'blocked': None if admin else '需要管理员权限',
                'score': 60, 'basis': ['AI 建议禁用：' + info.get('reason', '') if info.get('reason') else 'AI 建议禁用',
                                       f'现在是「{svc.get("start_label") or svc["start_type"]}」' + ('，正在运行' if running else ''),
                                       '改为手动后，需要它的程序仍能按需启动它',
                                       '随时可以在「开机与服务」或「操作记录」里改回'],
                'hold_reason': '服务由你决定：改为手动会改变程序的行为'})

    def add_apps(self, apps: list[dict], explained: dict, scan=None) -> int:
        """Programs worth uninstalling: the model suggests it (it knows when the program last ran and whether it is
        open now), or Windows' run history says it has not run for a year while it takes room. A program is removed
        by its own uninstaller, from its row and one at a time: never ticked, never run with the rest of the plan."""
        added = 0
        for a in apps:
            if a.get('kind') not in ('app', 'game') or not a.get('can_uninstall'):
                continue
            info = explained.get(a['id']) or {}
            ai_says = info.get('suggestion') == 'uninstall'
            idle = (self.now - a['last_used']) / 86400 if a.get('usage_known') and a.get('last_used') else None
            size = a.get('size')
            own = a.get('own_location') or ''
            node = scan.find(own) if own and scan is not None and scan.complete else None
            if node is not None and node.size:
                size = node.size  # what is on the disk, rather than what the installer once registered
            if not ai_says and not (idle is not None and idle >= APP_IDLE_DAYS and (size or 0) >= APP_IDLE_MIN):
                continue
            basis = []
            if ai_says:
                basis.append('AI 建议卸载：' + info['reason'] if info.get('reason') else 'AI 建议卸载')
            if idle is not None:
                basis.append(f'Windows 的运行记录：{_ago(a["last_used"], self.now)}没有运行过')
            elif not a.get('usage_known'):
                basis.append('没有运行记录可查（需要管理员权限），不知道最近是否用过')
            if size:
                basis.append('占用约 ' + _size_text(size))
            basis.append('卸载用它自己的卸载程序；应用数据可能留下，卸载后可以查找残留')
            score = (60 if idle is not None and idle >= 180 else 50) if ai_says else 40
            self.add({
                'id': 'app:' + a['id'], 'kind': 'app', 'app_id': a['id'], 'group': 'review', 'category': '可以卸载的程序',
                'title': a['name'], 'size': size or 0, 'what': info.get('what', ''),
                'why': ('AI 建议卸载：' + info.get('reason', '')) if ai_says
                       else f'{_ago(a["last_used"], self.now)}没有运行过，占用 {_size_text(size)}',
                'effect': '它的设置、存档或登录信息可能随卸载一起删除，或留在应用数据里。',
                'action': 'uninstall', 'source': 'ai' if ai_says else 'heuristic', 'confidence': 0.6 if ai_says else 0.4,
                'location': a['location'] if a.get('location_exists') else '', 'score': score, 'basis': basis,
                'hold_reason': '卸载要运行它自己的卸载程序，请你逐个确认'})
            added += 1
        return added

    def add_info(self, scan, env: dict):
        for template in INFO_TARGETS:
            expanded = paths.expand(template, env) if '%' in template else template
            if not expanded:
                continue
            matches = glob.glob(paths.native(expanded)) if '*' in expanded else [paths.native(expanded)]
            for path in matches:
                size = None
                logical_size = None
                node = scan.find(path) if scan is not None and scan.complete else None
                if node is not None:
                    size = node.size
                    logical_size = node.logical_size
                elif os.path.isfile(path):
                    try:
                        st = os.lstat(fsops.long_path(path))
                        size, logical_size = fsops.file_size(path, st), st.st_size
                    except OSError:
                        size = None
                if not size or size < INFO_MIN and not path.lower().endswith('hiberfil.sys'):
                    continue
                note = self.knowledge.annotate(path) if self.knowledge else None
                self.add({'id': _ident('info', path), 'kind': 'info', 'group': 'info', 'category': '说明',
                          'title': (note or {}).get('label') or os.path.basename(path), 'size': size,
                          'logical_size': logical_size,
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
    removed_size = sum(f.get('size', size) for f in remove)
    logical_size = size * len(remove)
    rated = [(f, *removal_score(f, f.get('ai'))) for f in remove]
    score = min(s for _, s, _ in rated)
    basis = ['内容逐字节相同']
    if not keepers:
        # the user chose to let every copy go: the file itself goes, only the Recycle Bin still has it
        name = os.path.basename(copies[0]['path'])
        return {
            'title': f'{name} 等 {len(copies)} 份', 'size': removed_size, 'logical_size': logical_size, 'count': len(remove),
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
        'title': f'{os.path.basename(first)} 等 {len(copies)} 份', 'size': removed_size,
        'logical_size': logical_size, 'count': len(remove),
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
        elif reason := policy.reason(s['path']):
            s.update(score=0, basis=['受保护的位置：' + reason, '没有自动勾选：保护规则不允许清理此位置'], selected=False)
        elif s.get('source') == 'knowledge':  # recognised without the model; may hold what someone parked there
            s.update(score=40, basis=[f'盘序认得：{s.get("app") or "已知位置"}，没有问 AI',
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
                                            if k in ('space_scan', 'other_drives', 'duplicates', 'cross_drive_duplicates',
                                                     'use_ai', 'apps_services')}}
    if 'selected_drives' in (options or {}) and (options['selected_drives'] is None or isinstance(options['selected_drives'], list)):
        opts['selected_drives'] = options['selected_drives']
    with_apps = bool(opts.get('apps_services', True))
    scopes = {k: True for k in AI_SCOPES}
    for source in (opts.get('ai_scopes'), (options or {}).get('ai_scopes')):
        if isinstance(source, dict):
            scopes.update({k: bool(v) for k, v in source.items() if k in scopes})
    opts['ai_scopes'] = scopes
    threshold = opts.get('auto_threshold', DEFAULT_THRESHOLD)
    if (options or {}).get('auto_select') in THRESHOLDS:
        threshold = THRESHOLDS[options['auto_select']]
    if isinstance((options or {}).get('auto_threshold'), int):
        threshold = options['auto_threshold']
    opts['auto_threshold'] = threshold = max(0, min(10, int(threshold)))
    ai_enabled = opts.get('use_ai', True) and any(on and opts.get(AI_SCOPE_PARENT.get(key, ''), True)
                                                  for key, on in scopes.items())
    client = backend.client_or_none('classify') if ai_enabled else None
    stages = [s for s in STAGES if (s[0] != 'space' or opts.get('space_scan', True))
              and (s[0] != 'dups' or opts.get('duplicates', True)) and (s[0] != 'apps' or with_apps)
              and (s[0] != 'ai' or client is not None)]
    total = len(stages)
    timings = {}
    completed = []
    job.update(completed=[])

    def ready(*pages):
        completed.extend(p for p in pages if p not in completed)
        job.update(completed=list(completed))
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
    drives = backend.m_drives()
    whole_drives = [d['root'] for d in selected_drives(drives, opts)]

    stage('junk')
    junk_rows = backend.junk.scan(cancel=job.cancel,
                                  progress=lambda i, n, name: part(i / n if n else None, detail=name, done=i, total=n))
    backend.junk_rows = junk_rows
    ready('junk')

    scan = None  # an earlier tool-page scan must not leak into a new, space-disabled check-up
    if opts.get('space_scan', True):
        stage('space')
        roots = space_roots(env, settings, drives, whole_drives=whole_drives)
        used = sum(d.get('used', 0) for d in drives if any(paths.key(d['root']) == paths.key(r) for r in roots))
        scan = SpaceScan(roots, skip=space_skip(env, policy, getattr(backend, 'app_dirs', ())))
        scan.run(cancel=job.cancel, progress=lambda p: part(
            min(0.98, p.get('bytes', 0) / used) if used else None, detail='、'.join(roots),
            count=f'已统计 {p.get("files", 0):,} 个文件'))
        backend.scan = scan
        backend.ai_results = {}
        ready('space', 'large')

    broad_roots = insights_roots(env, settings, drives, whole_drives=whole_drives)
    stage('insights')
    ins = Insights(env, settings, apps=backend.apps).collect(
        broad_roots, skip=system_skip(env, policy, broad_roots), cancel=job.cancel,
        container_roots=personal_roots(env, {}) + [d['root'] for d in drives],
        progress=lambda p: part(None, detail=p.get('current', ''), count=f'已看过 {p.get("files", 0):,} 个文件'))
    findings = ins.findings()
    backend.insights = {'findings': findings, 'stats': ins.stats, 'roots': ins.roots, 'time': time.time()}
    for finding in findings:
        for file in finding['files']:
            backend.file_ai.pop(file['path'], None)
    ready('mine')

    if opts.get('duplicates', True):
        stage('dups')
        min_mb = float(settings.get('duplicates', {}).get('min_size_mb', 1))
        dups = find_duplicates(broad_roots, min_size=int(min_mb * 1024 * 1024),
                               cross_drives=opts.get('cross_drive_duplicates', True), drive_roots=[d['root'] for d in drives],
                               skip=system_skip(env, policy, broad_roots), cancel=job.cancel,
                               container_roots=personal_roots(env, {}) + [d['root'] for d in drives]
                                               + [env.get('USERPROFILE'), env.get('PUBLIC')],
                               **DUP_BOUNDS,
                               progress=lambda p: (lambda f, label, count: part(f, detail=label, count=count))(*dup_progress(p)))
        for g in dups['groups']:
            for f in g['files']:
                f['blocked'] = policy.reason(f['path'])
        backend.dups = dups
        backend.dup_ai = {}  # a new scan must not reuse AI judgments when the user turned review off
        ready('dups')

    # installed programs and services: read only (a program's own uninstaller and a service change both need the user)
    apps_list, services_list = [], []
    apps_error = services_error = ''
    if with_apps:
        stage('apps')
        part(0.1, detail='已安装的应用')
        try:
            apps_list = backend.apps.list(fresh=True)
        except OSError as exc:
            apps_error = str(exc)
        part(0.5, detail='第三方服务', count=f'{len(apps_list)} 个程序')
        try:
            services_list = backend.services.list()
        except Exception as exc:  # noqa: BLE001 - psutil reports a locked service manager with errors of its own
            services_error = str(exc) or exc.__class__.__name__
        part(1.0, count=f'{len(apps_list)} 个程序，{sum(1 for s in services_list if s["kind"] == "third_party")} 个第三方服务')

    ai_info = {'used': False, 'enabled': ai_enabled, 'scopes': [], 'errors': [], 'usage': None}
    file_ai, startup_ai, app_ai, service_ai = {}, {}, {}, {}
    startup_items = []
    startup_error = ''
    try:
        startup_items = backend.startup.list()
    except OSError as exc:
        startup_error = str(exc)
    # registrations that look broken: computed before the AI, whose facts about a program mention them
    registry_issues = backend.apps.registry_issues([d['root'] for d in drives if d.get('fixed')])
    if client is not None:
        stage('ai')
        anonymize = settings['ai'].get('anonymize_paths', True)
        # as many file names as the request budget holds (it was a fixed 120: half of the default budget)
        budget = max(1, int(settings['ai'].get('max_requests', 8)))
        files = backend.files_for_ai([f['id'] for f in findings], include_content=False)[:budget * FILE_BATCH] if scopes['files'] else []
        # duplicates that need judging: a copy in a program, a project or next to one (loose copies are clear)
        unclear = [g for g in (backend.dups or {}).get('groups', []) if any(f.get('place') for f in g['files'])] \
            if opts.get('duplicates', True) and scopes['duplicates'] else []
        enabled = [i for i in startup_items if i['enabled']] if scopes['startup'] else []
        # programs (not runtimes or drivers: those stay) and third-party services, with what 盘序 checked about them
        app_items = []
        if with_apps and scopes['apps'] and apps_list:
            running = itemfacts.running_programs()
            app_items = [backend._app_for_ai(a, running, registry_issues) for a in apps_list if a['kind'] in ('app', 'game')]
        service_items = backend.services_for_ai(services_list) if with_apps and scopes['services'] and services_list else []
        # the AI stage in steps, each with its share of the bar: (name, will it run)
        steps = [s for s, on in (('space', scopes['space'] and scan is not None and scan.complete), ('files', bool(files)),
                                 ('dups', bool(unclear)), ('startup', bool(enabled)), ('apps', bool(app_items)),
                                 ('services', bool(service_items))) if on]
        ai_info['used'], ai_info['scopes'] = bool(steps), steps

        def ai_part(name, label, fraction=0.0, **extra):
            i = steps.index(name)
            part((i + min(1.0, fraction)) / len(steps), detail=label, **extra)

        def asked(p):
            return f'已问 {p.get("requests", 0)} 次'
        if 'space' in steps:
            ai_part('space', '空间分析')
            result = backend.analyst.analyze_space(
                scan, client, settings, cancel=job.cancel,
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
            result = backend.analyst.explain_items(items, client, anonymize, cancel=job.cancel, max_requests=budget)
            startup_ai = {i['id']: result['items'].get(i['key']) for i in items if result['items'].get(i['key'])}
            ai_info['errors'] += result['errors']
        if app_items:
            ai_part('apps', '已安装的应用（名称、发布者和使用情况）')
            result = backend.analyst.explain_items(app_items, client, anonymize, cancel=job.cancel, max_requests=budget)
            app_ai = {i['id']: result['items'][i['key']] for i in app_items if result['items'].get(i['key'])}
            ai_info['errors'] += result['errors']
        if service_items:
            ai_part('services', '第三方服务（名称、程序和盘序查到的情况）')
            result = backend.analyst.explain_items(service_items, client, anonymize, cancel=job.cancel, max_requests=budget)
            service_ai = {i['id']: result['items'][i['key']] for i in service_items if result['items'].get(i['key'])}
            ai_info['errors'] += result['errors']
        ai_info['usage'] = client.usage.as_dict()
        ai_info['model'] = client.model
        if client.usage.requests:
            backend.oplog.add('ai', f'一键体检：AI 复核（{client.usage.requests} 次请求）',
                              details={'usage': ai_info['usage'], 'model': client.model, 'errors': ai_info['errors'][:5]})

    fsops.check_cancel(job.cancel)  # the AI steps above may have been the last thing running
    stage('plan')
    backend.startup_ai = startup_ai
    builder = PlanBuilder(policy, backend.knowledge, threshold=threshold)
    builder.add_junk(junk_rows)
    builder.reserve_rule_locations(env)
    assessed_junk = {i['rule']: i for i in builder.items if i.get('kind') == 'rule'}
    for row in junk_rows:
        item = assessed_junk.get(row['id'])
        if item:
            row.update({k: item[k] for k in ('score', 'basis', 'eligible', 'selected', 'blocked')})
    residue_stats = {'checked': 0, 'truncated': False}
    if scan is not None and scan.complete:
        residue_stats.update(builder.add_removed_apps(backend.apps, scan))
        builder.add_ai_dirs(list(backend.ai_results.values()), env, backend.analyst, scan)
        analysed = {nid for nid, s in backend.ai_results.items() if s.get('source') != 'none'}
        residue_stats.update(builder.add_heuristics(scan, backend.analyst, env, analysed))
    builder.add_insights(findings, file_ai)
    builder.add_duplicates(backend.dups if opts.get('duplicates', True) else None, backend.dup_ai)
    if scan is not None:
        builder.add_known_folders(scan, backend.analyst)
    builder.add_startup(startup_items, startup_ai, env)
    uninstallable = 0
    if with_apps:
        # what this check-up's AI said replaces earlier answers (none when the AI was off), as for startup items
        backend.app_ai, backend.service_ai = app_ai, service_ai
        uninstallable = builder.add_apps(apps_list, app_ai, scan)
        builder.add_services(services_list, service_ai, backend.services.admin)
    builder.add_info(scan, env)
    backend.app_residues = residue_result(builder, scan, residue_stats, registry_issues)
    third_party = [s for s in services_list if s['kind'] == 'third_party']
    plan = builder.plan({'ai': ai_info, 'options': opts,
                         'personal_scan': {'truncated': bool(ins.stats.get('truncated')),
                                           'limit': ins.stats.get('limit'), 'files': ins.stats.get('files', 0)},
                         'coverage': {'space': scan.stats if scan else None,
                                      'junk': {'rules': len(junk_rows), 'unavailable': sum(bool(r.get('error') or r.get('scope_blocked')) for r in junk_rows)},
                                      'startup': {'count': len(startup_items), 'error': startup_error, 'ai_count': len(startup_ai)},
                                      'apps': {'residues': scan is not None, 'folders': len(backend.app_residues['items']),
                                               'registry': len(registry_issues), 'truncated': residue_stats.get('truncated', False),
                                               'listed': len(apps_list) if with_apps else None, 'error': apps_error,
                                               'ai_count': len(app_ai), 'suggested': uninstallable},
                                      'services': {'count': len(third_party), 'all': len(services_list), 'error': services_error,
                                                   'ai_count': len(service_ai),
                                                   'suggested': sum(1 for i in builder.items if i.get('kind') == 'service')}
                                                  if with_apps else None,
                                      'whole_drives': whole_drives,
                                      'personal_roots': broad_roots,
                                      'duplicates': (backend.dups or {}).get('stats') if opts.get('duplicates', True) else None,
                                      'omitted_drives': [d['root'] for d in drives if d['root'] not in whole_drives]},
                         'stats': {'insight_files': ins.stats.get('files', 0),
                                   'scanned_bytes': scan.stats.get('bytes') if scan else None}})
    plan['summary'] = summarize(plan)
    backend.plan = plan
    ready('apps', 'startup')
    starts = sorted(timings.items(), key=lambda kv: kv[1]) + [('', time.time())]
    save_timings(backend.data_dir, {k: max(0.1, round(starts[i + 1][1] - t, 1)) for i, (k, t) in enumerate(starts[:-1])})
    return plan


def residue_result(builder, scan, stats, registry):
    return {'items': [item for item in builder.items if item['category'] in RESIDUE_CATEGORIES],
            'registry': registry, 'stats': {**stats, 'roots': scan.roots if scan else [],
                                          'incomplete': bool(scan and scan.stats.get('incomplete'))},
            'scanned_at': time.time()}


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
        # a program is uninstalled by its own uninstaller from its row, never together with the rest
        if item is None or item['status'] != 'pending' or item['action'] in ('none', 'uninstall') or item['blocked']:
            continue
        chosen.append(item)
    if not chosen:
        raise ValueError('没有可以执行的项目')
    policy = backend.policy()
    report = {'deleted': {'count': 0, 'bytes': 0}, 'recycled': {'count': 0, 'bytes': 0}, 'disabled': [], 'services': [],
              'permanent': {'count': 0, 'bytes': 0, 'paths': []},
              'failures': [], 'items': {}}

    def done(item, status, note='', freed=None):
        """``note``: a few words on how it went; ``freed``: the bytes it gave back (the page writes the unit)."""
        item['status'] = status
        item['result'] = note
        if freed is not None:
            item['freed'] = int(freed)
        report['items'][item['id']] = {'status': status, 'note': note, 'freed': item.get('freed')}

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
                done(item, 'done', '已清理', freed=row['freed'])
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
            job.update(freed=s.bytes, detail=s.current or '移到回收站')
            backend.oplog.progress(pending, freed=s.bytes, done=s.files)
        try:
            stats, recycled = fsops.recycle(unique, cancel=job.cancel, hwnd=winapi.find_window(APP_NAME), progress=on_progress)
        except fsops.Cancelled:
            backend.oplog.drop(pending)  # stopped while sizing, before anything moved
            raise
        gone = {paths.key(p) for p in recycled}
        nuked = [p for p in stats.permanent]  # too large for the bin: Windows deleted them on the user's yes
        nuked_keys = {paths.key(p) for p in nuked}
        report['recycled']['count'] += len(recycled) - len(nuked)
        report['recycled']['bytes'] += stats.bytes - stats.permanent_bytes
        report['permanent'] = {'count': len(nuked), 'bytes': stats.permanent_bytes, 'paths': nuked[:50]}
        for f in stats.failures:
            report['failures'].append({'path': f.get('path'), 'reason': f.get('reason')})
        for item in {id(i): i for i, _ in to_recycle}.values():
            ok = [p for p in item['paths'] if paths.key(p) in gone or any(paths.is_within(p, g) for g in recycled)]
            share = sum(stats.measured[p][0] for p in ok if p in stats.measured)
            # A selected parent may have covered this path; use this item's scan measure only then.
            share += sum((item['size'] or 0) // max(1, len(item['paths'])) for p in ok if p not in stats.measured)
            # In the Recycle Bin: the space comes back when it is emptied.
            if ok and any(paths.within_any(p, nuked_keys) for p in ok):
                item['permanent'] = True
                done(item, 'done' if len(ok) == len(item['paths']) else 'partial', '回收站放不下，按你在 Windows 提示里的选择直接删除了',
                     freed=share)
            elif len(ok) == len(item['paths']):
                done(item, 'done', '已移到回收站', freed=share)
            elif ok:
                done(item, 'partial', f'{len(ok)}/{len(item["paths"])} 项已移到回收站', freed=share)
            else:
                done(item, 'failed', '没有移到回收站')
        if recycled or stats.failed:
            title = f'清理方案：移到回收站 {len(recycled) - len(nuked)} 项' + (
                f'，{len(nuked)} 项回收站放不下、已直接删除' if nuked else '')
            backend.oplog.add('recycle', title, freed=stats.bytes, items=recycled, failures=stats.failures,
                              details={'source': 'plan', 'mode': 'recycle', 'permanent': nuked[:200],
                                       'permanent_bytes': stats.permanent_bytes}, replaces=pending)
        else:
            backend.oplog.drop(pending)
        backend.forget_paths(recycled, stats.measured)

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

    # 4. services: not started at boot any more (the operation log keeps the old start type, so it can be undone)
    services = [i for i in chosen if i['kind'] == 'service']
    current = {}
    if services:
        try:
            current = {s['name']: s['start_type'] for s in backend.services.list()}
        except Exception:  # noqa: BLE001 - as in run(): then set_start_type reports the problem per service
            current = {}
    for item in services:
        job.update(stage='services', detail=item['title'])
        if current.get(item['service_name'], 'auto') not in ('auto', 'delayed'):
            # changed elsewhere since the check-up (to manual, or disabled on purpose): leave it as it is
            done(item, 'skipped', '启动方式在体检后已经改过，没有再改')
            continue
        try:
            backend.services.set_start_type(item['service_name'], 'manual')
            report['services'].append(item['title'])
            done(item, 'done', '已改为手动启动')
        except (ValueError, PermissionError, RuntimeError, OSError) as exc:
            done(item, 'failed', str(exc))
            report['failures'].append({'title': item['title'], 'reason': str(exc)})

    plan['summary'] = summarize(plan)
    report['summary'] = plan['summary']
    return report
