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
import os
import time
import uuid

from . import APP_NAME, fsops, paths, winapi
from .analyst import newer_sibling
from .duplicates import find_duplicates, verify_unchanged
from .insights import KIND_SCORE, Insights, file_score, is_copy_name, managed_dir, with_ai
from .junk import RULES_BY_ID
from .scanner import SpaceScan

GROUPS = {'safe': '放心清理', 'review': '建议你看看', 'info': '仅供了解'}
ACTIONS = {'delete': '永久删除', 'recycle': '移到回收站', 'disable': '禁用开机启动', 'none': '不需要操作'}
SOURCES = {'rule': '规则', 'ai': 'AI', 'insight': '我的文件', 'dup': '重复文件', 'heuristic': '盘序判断',
           'knowledge': '说明'}

STAGES = [('junk', '扫描垃圾文件'), ('space', '分析系统盘空间'), ('insights', '整理我的文件'),
          ('dups', '查找重复文件'), ('ai', 'AI 复核'), ('plan', '生成清理方案')]

AUTO_AI_CONFIDENCE = 0.8     # 稳妥: the model must be this sure to pre-tick a folder
BOLD_AI_CONFIDENCE = 0.7     # 积极
ACTIVE_DAYS = 7             # folders changed more recently are left for the user
# 积极 still leaves these rules unticked: no undo (Recycle Bin, rollback to the old Windows), very slow
# (DISM), or possibly holding the user's own builds (mvn install puts them in the local repository)
BOLD_EXCLUDED_RULES = {'recycle_bin', 'windows_old', 'component_store', 'maven'}
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
FILES_FOR_AI = 120

# big things worth explaining, in the order they are shown
INFO_TARGETS = [
    '%SystemDrive%\\hiberfil.sys', '%SystemDrive%\\pagefile.sys', '%SystemDrive%\\System Volume Information',
    '%SystemRoot%\\WinSxS', '%LOCALAPPDATA%\\Docker', '%LOCALAPPDATA%\\wsl',
    '%LOCALAPPDATA%\\Packages\\CanonicalGroupLimited.*', '%ProgramFiles(x86)%\\Steam\\steamapps',
    '%USERPROFILE%\\Documents\\WeChat Files', '%USERPROFILE%\\Documents\\xwechat_files',
    '%USERPROFILE%\\Documents\\Tencent Files',
]


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


def decision_note(item: dict) -> list[str]:
    """Why an item was (not) ticked automatically -- the last line of its 评分依据."""
    if item['action'] == 'none':
        return []
    if item['blocked']:
        return ['没有勾选：' + item['blocked']]
    if item['selected']:
        return ['自动勾选：' + (item.get('auto_reason') or '默认开启的内置规则')]
    if item.get('personal'):
        return ['没有自动勾选：你的个人文件只由你决定']
    return ['没有自动勾选：' + (item.get('hold_reason') or '把握不够，留给你决定')]


class PlanBuilder:
    """Collects plan items; a path is offered at most once (first source wins)."""

    def __init__(self, policy, knowledge=None, now: float | None = None, bold: bool = False):
        self.policy = policy
        self.knowledge = knowledge
        self.now = now or time.time()
        # 积极: besides default rules, also pre-tick rebuildable caches, leftovers and old versions, and
        # folders the model is fairly sure about. Never personal files, never anything without an undo.
        self.bold = bold
        self.items: list[dict] = []
        self._claimed: list[str] = []

    def _taken(self, path: str) -> bool:
        return any(paths.is_within(path, c) or paths.is_within(c, path) for c in self._claimed)

    def add(self, item: dict) -> dict | None:
        base = {'id': '', 'group': 'review', 'category': '', 'title': '', 'size': 0, 'count': 0, 'what': '', 'why': '',
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
        item['basis'] = list(item['basis']) + decision_note(item)
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
            if rule.default:
                score, basis, auto = 95, ['内置规则：公认可以重建的缓存和临时文件'], '默认开启的内置规则'
            elif rule.mode == 'recycle_bin' or rule.id == 'windows_old':
                score, basis, auto = 55, ['内置规则，但删除后无法恢复：' + (rule.note or '无法从回收站恢复')], None
            elif rule.id in BOLD_EXCLUDED_RULES:
                score, basis, auto = 60, ['内置规则，默认不勾：' + (rule.note or '删除前请确认里面没有你自己的东西')], None
            else:
                score = 80
                basis = ['内置规则：可以重建，只是下次用到时要重新下载或生成，第一次会慢一些']
                auto = '积极模式：可以重建的缓存' if self.bold else None
            self.add({
                'id': 'rule:' + rule.id, 'kind': 'rule', 'rule': rule.id, 'group': 'safe' if auto and not blocked else 'review',
                'category': '垃圾文件', 'title': rule.name, 'size': size or 0, 'count': row.get('count') or 0,
                'what': '', 'why': rule.description,
                'effect': rule.note or '相关程序会在需要时重新生成这些文件。', 'action': 'delete', 'source': 'rule',
                'confidence': 1.0 if rule.default else 0.7, 'selected': bool(auto) and not blocked, 'blocked': blocked,
                'locations': row.get('locations', [])[:5], 'score': score, 'basis': basis,
                'auto_ok': bool(auto), 'auto_reason': auto,
                'hold_reason': None if auto else ('删除后无法恢复，请你决定' if score < 60 else '默认不勾，请你决定')})

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
        if owner and not (self.bold and cache_like(os.path.basename(path.rstrip('\\/')))):
            # the whole folder of an installed program also holds its settings, licence or templates;
            # in 积极 mode its cache-named sub-folders may still be ticked
            return f'属于仍在使用的程序「{owner}」'
        return None

    def add_ai_dirs(self, suggestions: list[dict], env: dict, analyst=None, scan=None):
        app_bases = [env.get(k) for k in ('LOCALAPPDATA', 'APPDATA', 'LOCALLOW', 'ProgramData') if env.get(k)]
        threshold = BOLD_AI_CONFIDENCE if self.bold else AUTO_AI_CONFIDENCE
        for s in sorted(suggestions, key=lambda r: -r['size']):
            if s.get('source') == 'none' or s.get('expanded') or s['verdict'] not in ('clean', 'review'):
                continue
            in_app_data = any(paths.is_within(s['path'], b) for b in app_bases)
            confidence = float(s.get('confidence') or 0)
            score = round(confidence * 100) if s['verdict'] == 'clean' else min(55, round(confidence * 100))
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
            sure = s['verdict'] == 'clean' and confidence >= threshold and not hold
            if s['verdict'] != 'clean':
                hold = hold or 'AI 也拿不准'
            elif not sure and not hold:
                hold = f'AI 的把握不到 {round(threshold * 100)}%'
            auto = (('积极模式：' if self.bold and confidence < AUTO_AI_CONFIDENCE else '')
                    + f'AI 判断可清理（把握 {round(confidence * 100)}%），不属于仍在使用的程序，最近也没有改动') if sure else None
            item = self.add({
                'id': _ident('dir', s['path']), 'kind': 'paths', 'group': 'safe' if sure else 'review',
                'category': '应用数据', 'title': s.get('app') if s.get('app') not in (None, '', '未知') else
                os.path.basename(s['path']), 'size': s['size'], 'count': s.get('files', 0), 'what': '',
                'why': s.get('reason') or f'AI 判断：{s.get("verdict_label", "")}', 'effect': s.get('effect') or '',
                'action': 'recycle', 'source': 'ai', 'confidence': s.get('confidence'), 'selected': sure,
                'paths': [s['path']], 'node_id': s.get('node_id'), 'score': score, 'basis': basis,
                'auto_reason': auto, 'hold_reason': hold})
            if item is not None and item['blocked']:
                item['group'] = 'review'

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
                    hold = self._folder_guards(p, n, None, basis) or (None if self.bold else '盘序的推测，留给你决定')
                    self.add({'id': _ident('dir', p), 'kind': 'paths', 'category': '应用数据', 'title': n.name,
                              'group': 'review' if hold else 'safe', 'selected': not hold,
                              'size': n.size, 'count': n.files, 'what': f'「{n.name}」的数据，旁边还有更新的版本「{newer}」。',
                              'why': f'可能是旧版本留下的：{_ago(n.mtime, self.now)}没有修改过。',
                              'effect': '如果还在用旧版本，它的设置和缓存会丢失。', 'action': 'recycle',
                              'source': 'heuristic', 'confidence': 0.6, 'paths': [p], 'score': 60, 'basis': basis,
                              'auto_reason': '积极模式：旧版本留下的数据，进回收站可以还原', 'hold_reason': hold})
            if node.size >= LEFTOVER_MIN and old and analyst.installed_app(path) == '':
                basis = ['「已安装的应用」里找不到对应的程序', f'{_ago(node.mtime, self.now)}没有修改过', '没有经过 AI 复核']
                hold = self._folder_guards(path, node, None, basis) or (None if self.bold else '盘序的推测，留给你决定')
                self.add({'id': _ident('dir', path), 'kind': 'paths', 'category': '应用数据', 'title': node.name,
                          'group': 'review' if hold else 'safe', 'selected': not hold,
                          'size': node.size, 'count': node.files,
                          'what': '找不到对应的已安装程序，可能是已卸载程序的残留。',
                          'why': f'在「已安装的应用」里没有找到对应的程序，而且 {_ago(node.mtime, self.now)}没有修改过。',
                          'effect': '如果程序其实还在（例如绿色版），它的设置、存档或缓存会丢失。', 'action': 'recycle',
                          'source': 'heuristic', 'confidence': 0.5, 'paths': [path], 'score': 50, 'basis': basis,
                          'auto_reason': '积极模式：很可能是已卸载程序的残留，进回收站可以还原', 'hold_reason': hold})

    def add_insights(self, findings: list[dict], file_ai: dict | None = None):
        file_ai = file_ai or {}
        for f in findings:
            if f['kind'] == 'empty_folders':
                # nothing to free: one item for all of them instead of a row per folder
                folders = [x['path'] for x in f['files']]
                self.add({'id': 'empty:' + f['id'], 'kind': 'paths', 'group': 'review', 'category': f['kind_label'],
                          'finding': f['id'], 'finding_kind': f['kind'], 'title': f'{len(folders)} 个空文件夹', 'size': 0,
                          'count': 0, 'what': f['advice'], 'why': f['reason'],
                          'effect': '移到回收站，需要时可以从回收站还原。不释放空间，只是更整洁。', 'action': 'recycle',
                          'source': 'insight', 'confidence': f['confidence'], 'paths': folders, 'personal': True,
                          'score': KIND_SCORE['empty_folders'], 'basis': ['里面没有任何文件（包括子文件夹）']})
                continue
            files = [x for x in f['files'] if x.get('remove') or f['kind'] in ('stale_large', 'old_downloads',
                                                                             'installer_old')]
            if f['kind'] == 'name_versions':
                files = [x for x in f['files'] if (x.get('score') or 0) > 10]  # never the copy to keep
            for x in sorted(files, key=lambda x: (not x.get('remove'), -x['size']))[:PERSONAL_PER_KIND]:
                ai = file_ai.get(x['path'])
                why = x.get('note') or f['reason']
                base_score = x.get('score', file_score(f['kind'], x))
                basis = [f'{f["kind_label"]}：{why}']
                if ai:
                    basis.append(f'AI：{ai.get("verdict_label", "")}' + (f'——{ai["reason"]}' if ai.get('reason') else ''))
                self.add({
                    'id': _ident('file', x['path']), 'kind': 'paths', 'group': 'review', 'category': f['kind_label'],
                    'finding': f['id'], 'finding_kind': f['kind'], 'title': x['name'], 'size': x['size'],
                    'count': 1 if x.get('category') != 'folder' else 0, 'what': f['advice'], 'why': why,
                    'effect': '移到回收站，需要时可以从回收站还原。', 'action': 'recycle', 'source': 'insight',
                    'confidence': f['confidence'] if x.get('remove') else min(f['confidence'], 0.4),
                    'paths': [x['path']], 'ai': ai, 'personal': True, 'score': with_ai(base_score, ai), 'basis': basis})

    def add_duplicates(self, dups: dict | None):
        for g in (dups or {}).get('groups', []):
            files = g['files']
            if any(self._taken(f['path']) for f in files):
                continue  # already offered elsewhere (e.g. as a version); two "keep one" rules could remove both
            keeper = min(files, key=lambda f: (is_copy_name(os.path.splitext(os.path.basename(f['path']))[0]),
                                               f['mtime']))
            remove = [f['path'] for f in files if f is not keeper]
            self._claimed.append(keeper['path'])  # the copy that stays must not be offered by anything else
            self.add({
                'id': f'dup:{_ident("g", "|".join(sorted(f["path"] for f in files)))[2:]}', 'kind': 'dup',
                'group': 'review', 'category': '重复文件',
                'title': f'{os.path.basename(keeper["path"])} 等 {len(files)} 份', 'size': g['size'] * len(remove),
                'count': len(remove), 'what': f'{len(files)} 个文件内容完全相同（逐字节比较）。',
                'why': f'保留「{keeper["path"]}」，其余 {len(remove)} 份移到回收站。',
                'effect': '每组保留一份，内容不会丢失。', 'action': 'recycle', 'source': 'dup', 'confidence': 0.9,
                'paths': remove, 'keep': keeper['path'], 'dup_size': g['size'],
                'dup_files': [{'path': f['path'], 'mtime': f['mtime']} for f in files], 'personal': True,
                'score': 85, 'basis': ['内容逐字节相同', '保留一份，内容不会丢失']})

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
                'why': 'AI 建议禁用：' + info.get('reason', ''), 'effect': '开机时不再自动运行，需要时可以手动打开；随时可以在「开机与服务」里重新启用。',
                'action': 'disable', 'source': 'ai', 'confidence': 0.7,
                'blocked': '需要管理员权限' if item.get('needs_admin') else None,
                'score': 60, 'basis': ['AI 建议禁用', '随时可以重新启用']})

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
                          'what': (note or {}).get('advice', ''), 'why': '体积较大，但不能直接删除。',
                          'action': 'none', 'source': 'knowledge', 'location': path})

    # -- result ---------------------------------------------------------------------
    def plan(self, extra: dict | None = None) -> dict:
        order = {'safe': 0, 'review': 1, 'info': 2}
        self.items.sort(key=lambda i: (order[i['group']], i.get('personal', False), -(i['score'] or 0), -(i['size'] or 0)))
        return {'id': uuid.uuid4().hex[:10], 'created': time.time(), 'items': self.items,
                'groups': [{'id': g, 'name': n} for g, n in GROUPS.items()], **(extra or {})}


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
        safe = item.get('auto_ok', RULES_BY_ID[item['rule']].default) and not blocked
        item.update(blocked=blocked, group='safe' if safe else 'review', selected=safe)
        item['basis'] = [b for b in item['basis'] if not b.startswith(('自动勾选', '没有自动勾选', '没有勾选'))] \
            + decision_note(item)
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
    if (options or {}).get('auto_select') in ('bold', 'careful'):
        opts['auto_select'] = options['auto_select']
    client = backend.client_or_none('classify') if opts.get('use_ai', True) else None
    stages = [s for s in STAGES if (s[0] != 'space' or opts.get('space_scan', True))
              and (s[0] != 'dups' or opts.get('duplicates', True)) and (s[0] != 'ai' or client is not None)]
    total = len(stages)
    timings = {}

    def stage(key, **extra):
        index = [s[0] for s in stages].index(key)
        job.update(stage=key, stage_label=dict(STAGES)[key], step=index + 1, steps=total,
                   stages=[{'id': s, 'label': lbl} for s, lbl in stages], detail='', **extra)
        timings[key] = time.time()

    policy = backend.policy()
    env = backend.env

    stage('junk')
    junk_rows = backend.junk.scan(cancel=job.cancel,
                                  progress=lambda i, n, name: job.update(detail=name, done=i, total=n))
    backend.junk_rows = junk_rows

    if opts.get('space_scan', True):
        stage('space')
        root = env.get('SystemDrive', 'C:') + ('' if env.get('SystemDrive', '').endswith('\\') else '\\')
        root = paths.native(root)
        if winapi.IS_WINDOWS and winapi.simulation() is None:
            root = os.path.realpath(root)
        scan = SpaceScan(root)
        scan.run(cancel=job.cancel, progress=lambda p: job.update(detail=p.get('current', ''), files=p.get('files')))
        backend.scan = scan
        backend.ai_results = {}

    roots = personal_roots(env, settings)
    skip = system_skip(env, policy, roots)
    stage('insights')
    ins = Insights(env, settings, apps=backend.apps).collect(
        roots, skip=skip, cancel=job.cancel, progress=lambda p: job.update(detail=p.get('current', '')))
    findings = ins.findings()
    backend.insights = {'findings': findings, 'stats': ins.stats, 'roots': ins.roots, 'time': time.time()}

    if opts.get('duplicates', True):
        stage('dups')
        min_mb = float(settings.get('duplicates', {}).get('min_size_mb', 1))
        dups = find_duplicates(roots, min_size=int(min_mb * 1024 * 1024), skip=skip, cancel=job.cancel,
                               progress=lambda p: job.update(detail=p.get('phase', '')))
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
        if backend.scan is not None and backend.scan.complete:
            job.update(detail='空间分析')
            result = backend.analyst.analyze_space(backend.scan, client, settings, cancel=job.cancel)
            backend.ai_results = {s['node_id']: s for s in result['suggestions']}
            for s in result['suggestions']:
                s['blocked'] = policy.reason(s['path'])
                s['caution'] = policy.caution(s['path'])
            ai_info['errors'] += result['errors']
        files = backend.files_for_ai([f['id'] for f in findings], include_content=False)[:FILES_FOR_AI]
        if files:
            job.update(detail='我的文件（只发送文件名和大小）')
            result = backend.analyst.review_files(files, client, anonymize,
                                                  max_requests=int(settings['ai'].get('max_requests', 8)),
                                                  cancel=job.cancel)
            file_ai = result['files']
            backend.file_ai.update(file_ai)
            ai_info['errors'] += result['errors']
        enabled = [i for i in startup_items if i['enabled']]
        if enabled:
            job.update(detail='开机启动项')
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
    builder = PlanBuilder(policy, backend.knowledge, bold=opts.get('auto_select', 'bold') != 'careful')
    builder.add_junk(junk_rows)
    if backend.scan is not None and backend.scan.complete:
        builder.add_ai_dirs(list(backend.ai_results.values()), env, backend.analyst, backend.scan)
        analysed = {nid for nid, s in backend.ai_results.items() if s.get('source') != 'none'}
        builder.add_heuristics(backend.scan, backend.analyst, env, analysed)
    builder.add_insights(findings, file_ai)
    builder.add_duplicates(backend.dups if opts.get('duplicates', True) else None)
    builder.add_startup(startup_items, startup_ai, env)
    builder.add_info(backend.scan, env)
    plan = builder.plan({'ai': ai_info, 'options': opts,
                         'stats': {'insight_files': ins.stats.get('files', 0),
                                   'scanned_bytes': backend.scan.stats.get('bytes') if backend.scan else None}})
    plan['summary'] = summarize(plan)
    backend.plan = plan
    return plan


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
            keeper_ok = verify_unchanged(item['keep'], item['dup_size'], files[paths.key(item['keep'])]['mtime'])
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
        unique = []
        for _, p in sorted(to_recycle, key=lambda ip: len(ip[1])):
            if not any(paths.is_within(p, u) for u in unique):
                unique.append(p)
        stats, recycled = fsops.recycle(unique, cancel=job.cancel, hwnd=winapi.find_window(APP_NAME),
                                        progress=lambda s: job.update(freed=s.bytes))
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
                              failures=stats.failures, details={'source': 'plan', 'mode': 'recycle'})
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
