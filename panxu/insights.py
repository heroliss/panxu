"""「我的文件」: findings about the user's own files that ordinary tools miss.

People know their own downloads and documents, but after a while they forget
them too. This module walks the personal folders (never system folders, never
links, never cloud placeholders) and points out, with a reason for each:

* installers for programs that are already installed (or very old installers),
* downloads that never finished (.crdownload, .part, ...),
* archives that were already extracted next to themselves (verified for .zip),
* several versions of the same document ("论文(1)", "论文_最终版 - 副本"),
* large files nobody has touched for a long time,
* old files lingering in the Downloads folder,
* empty folders.

Nothing here deletes anything; findings feed the cleanup plan and the
「我的文件」 page, where the user decides (Recycle Bin by default).
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import time
import zipfile
from dataclasses import dataclass, field

from . import fsops, paths, winapi

DAY = 86400.0
INSTALLER_EXTS = {'.exe', '.msi', '.msix', '.msixbundle', '.appx', '.appxbundle'}
INCOMPLETE_EXTS = {'.crdownload', '.part', '.partial', '.download', '.opdownload', '.td', '.!qb', '.bc!', '.xltd', '.!ut'}
ARCHIVE_EXTS = {'.zip', '.rar', '.7z', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.zst'}
IGNORED_FILES = {'desktop.ini', 'thumbs.db', '.ds_store'}
SKIP_DIRS = {'.git', '.svn', '.hg', 'node_modules', '__pycache__', '.venv', 'venv', 'site-packages', '$recycle.bin',
             'system volume information', '.idea', '.vs'}
# Folders that programs fill and manage themselves, even inside Documents: caches, logs, chat
# storage, game data, application profiles. Their files are not the user's to sort out here, and
# removing them (even "identical copies" or empty folders) can break the program.
CACHE_DIRS = {'cache', 'caches', 'cache_data', 'code cache', 'cachestorage', 'service worker', 'logs', 'log', 'logfiles',
              'crashes', 'crashpad', 'crashdumps', 'temp', 'tmp', 'thumbnails'}
APP_DIRS = {'wechat files', 'xwechat_files', 'wxwork', 'tencent files', 'my games', 'adobe', 'zoom'}
MEDIA = {'image', 'video', 'audio'}
# kinds of files people keep several versions of; numbered app data (data_0, data_1...) is never one
VERSION_CATEGORIES = {'document', 'image', 'video', 'audio', 'archive'}
MAX_FILES = 400_000
# sub-folders programs bring along (Qt, Chromium / Electron, Java): next to an .exe they mark a program's folder
RUNTIME_DIRS = {'platforms', 'plugins', 'imageformats', 'locales', 'swiftshader', 'jre', 'runtime'}

KIND_INFO = {
    # kind: (label, what to do, removable by default rule)
    'installer_installed': ('已安装程序的安装包', '程序已经装好，安装包一般用不到了。需要重装时可以去官网下载最新版。'),
    'installer_old': ('很久以前下载的安装包', '这些安装包下载很久了，对应的程序没有安装或已卸载。需要时可以重新下载最新版。'),
    'incomplete_download': ('没有下载完的文件', '浏览器或下载工具中断后留下的半成品，无法直接打开。确认不再继续下载后可以删除。'),
    'archive_extracted': ('已经解压过的压缩包', '旁边已经有解压出来的文件夹。确认文件夹里的内容完整后，压缩包可以删除。'),
    'name_versions': ('同一文件的多个版本', '名字相近、像是同一份文件的不同版本或副本。通常只需要保留最新、最完整的一份。'),
    'stale_large': ('很久没动过的大文件', '这些大文件很久没有修改过。不需要的可以删除；珍贵的照片视频建议移到移动硬盘或网盘。'),
    'old_downloads': ('下载文件夹里的旧文件', '下载文件夹常被当作临时中转站，这些文件放了很久。看看哪些还需要，需要的移到合适的文件夹。'),
    'empty_folders': ('空文件夹', '这些文件夹里什么都没有，删除不会丢失任何内容。'),
}
KIND_ORDER = list(KIND_INFO)

# 建议清理度 (0-100): how strongly PanXu suggests removing a file, shown on every row so the decision can
# be read at a glance. It only explains and sorts; personal files are never pre-selected, whatever the score.
KIND_SCORE = {'incomplete_download': 90, 'installer_installed': 85, 'installer_old': 55, 'archive_extracted': 50,
              'name_versions': 35, 'stale_large': 30, 'old_downloads': 25, 'empty_folders': 90}


def file_score(kind: str, f: dict) -> int:
    if kind == 'name_versions':
        if f['note'].startswith('最新') or f['note'].startswith('与其他副本内容相同'):
            return 5   # the copy to keep
        return 85 if f.get('remove') else 35
    if kind == 'archive_extracted' and f.get('remove'):
        return 85   # every file verified in the extracted folder
    return KIND_SCORE[kind]


def with_ai(score: int, ai: dict | None) -> int:
    """The model's verdict on a file moves the score; it can lower it freely but raise it only so far."""
    if not ai:
        return score
    verdict = ai.get('verdict')
    if verdict == 'delete':
        return max(score, min(85, score + 30))
    if verdict == 'keep':
        return min(score, 15)
    if verdict == 'archive':
        return min(score, 40)
    return score

_CAMEL = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
_VERSION = re.compile(r'\d+(?:[._]\d+)+')
_INSTALLER_NOISE = {
    'setup', 'installer', 'install', 'x64', 'x86', 'amd64', 'arm64', 'win64', 'win32', 'win', 'windows', 'full',
    'offline', 'online', 'user', 'system', 'latest', 'stable', 'release', 'portable', 'cn', 'zh', 'en', 'us', 'web',
    'bit', '64bit', '32bit', 'desktop', 'client', 'beta', 'x', 'v', 'msi', 'exe', 'pc', 'official', 'chs', 'sc',
    '安装包', '安装程序', '安装', '官方版', '正式版', '中文版', '电脑版', '客户端', '最新版',
}
_COMPANY_NOISE = {'inc', 'ltd', 'llc', 'corp', 'corporation', 'co', 'company', 'gmbh', 'technology', 'technologies',
                  'software', 'network', 'the', '有限公司', '科技', 'microsoft'}
# common Chinese product names whose installers use the English name
ALIASES = {'微信': 'wechat', '腾讯qq': 'qq', '钉钉': 'dingtalk', '企业微信': 'wxwork', '网易云音乐': 'cloudmusic',
           '百度网盘': 'baidunetdisk', '飞书': 'feishu', '搜狗输入法': 'sogou', '有道云笔记': 'youdaonote',
           'visualstudiocode': 'vscode', 'microsoftvisualstudiocode': 'vscode', 'googlechrome': 'chrome'}


def tokens(text: str) -> list[str]:
    """Lower-case word tokens of a file or program name, without versions and packaging noise."""
    text = _CAMEL.sub(' ', text)
    text = _VERSION.sub(' ', text)
    text = re.sub(r'[\s\-_.,+()\[\]{}（）【】]+', ' ', text.lower())
    out = []
    for t in text.split():
        if t.isdigit() or t in _INSTALLER_NOISE or t in _COMPANY_NOISE:
            continue
        t = re.sub(r'^v\d+$', '', t)
        if t:
            out.append(t)
    return out


def compact(text: str) -> str:
    joined = ''.join(tokens(text))
    return ALIASES.get(joined, joined)


def version_of(text: str) -> tuple[int, ...] | None:
    m = _VERSION.search(text)
    if not m:
        return None
    try:
        return tuple(int(p) for p in re.split(r'[._]', m.group(0)) if p.isdigit())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# names that look like versions or copies of the same file
# ---------------------------------------------------------------------------

_COPY_MARK = re.compile(r'(\s*[\(（]\d{1,3}[\)）]|\s*-\s*(副本|copy)(\s*[\(（]?\d{1,3}[\)）]?)?|\s*的副本|\s*副本\d*|^copy of\s+)',
                        re.I)
_TAIL = [
    re.compile(r'(\s*[\(（]\d{1,3}[\)）])$'),
    re.compile(r'\s*-\s*(副本|copy)(\s*[\(（]?\d{1,3}[\)）]?)?$', re.I),
    re.compile(r'\s*(的)?副本\d*$'),
    re.compile(r'^copy of\s+', re.I),
    # explicit version words, v2 / ver2 / 版本2, dates and years -- but not bare numbers: "data_1",
    # "Font-900" or "Replay003" are separate files, not versions of one
    re.compile(r'[\s_\-.]*(final|最终版?|终稿|定稿|修改版?|修订版?|改|new|新版?|old|旧版?|bak|backup|备份|draft|草稿|初稿|'
               r'第[一二三四五六七八九十\d]+版|v\d+(\.\d+)*|ver\d+|版本\d+|\d{6,8}|(19|20)\d{2})$', re.I),
]
_FAMILY = {'.doc': '.docx', '.xls': '.xlsx', '.ppt': '.pptx', '.jpeg': '.jpg', '.htm': '.html', '.tif': '.tiff'}


def base_name(stem: str) -> str:
    """'毕业论文_最终版 - 副本' -> '毕业论文'; '需求文档_v3' -> '需求文档'."""
    s = stem.strip()
    for _ in range(6):
        before = s
        for pattern in _TAIL:
            s = pattern.sub('', s).strip()
        if s == before:
            break
    return s.lower().strip(' _-.')


def is_copy_name(stem: str) -> bool:
    return bool(_COPY_MARK.search(stem))


_ORDER_MARK = re.compile(r'(?:v|ver|版本)\s*(\d+(?:\.\d+)*)$|((?:19|20)\d{2})$|(\d{6,8})$|第(\d+)版$', re.I)


def name_version(stem: str) -> tuple[int, ...] | None:
    """The version a name states ('需求文档_v3' -> (3,), 'release-v0.4.0' -> (0, 4, 0), '简历_2025' -> (2025,))."""
    m = _ORDER_MARK.search(_COPY_MARK.sub('', stem).strip(' _-.'))
    if not m:
        return None
    text = next(g for g in m.groups() if g)
    return tuple(int(x) for x in text.split('.'))


def managed_dir(name: str) -> bool:
    """True for a folder a program manages itself (see CACHE_DIRS / APP_DIRS) or a tool's dot-folder."""
    low = name.lower().strip()
    return (low in SKIP_DIRS or low in CACHE_DIRS or low in APP_DIRS or low.startswith('.')
            or low.endswith(('cache', 'cache_data')))


# ---------------------------------------------------------------------------
# content previews (only ever sent to a model when the user allows it)
# ---------------------------------------------------------------------------

TEXT_EXTS = {'.txt', '.md', '.csv', '.json', '.log', '.ini', '.xml', '.html', '.htm', '.py', '.js', '.ts', '.java',
             '.c', '.cpp', '.h', '.go', '.rs', '.sql', '.yaml', '.yml', '.toml', '.bat', '.ps1', '.sh', '.tex'}
OFFICE = {'.docx': ('word/document.xml', 'w:t', 'w:p'), '.pptx': ('ppt/slides/', 'a:t', 'a:p'),
          '.xlsx': ('xl/sharedStrings.xml', 't', 'si')}
_TAG = re.compile(r'<[^>]+>')


def content_excerpt(path: str, limit: int = 1200) -> str | None:
    """The first ``limit`` characters of a document's text, or None if unsupported.

    Cloud placeholders are never read (that would download them)."""
    try:
        st = os.stat(fsops.long_path(path))
    except OSError:
        return None
    if getattr(st, 'st_file_attributes', 0) & winapi.CLOUD_ATTRIBUTES:
        return None
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in TEXT_EXTS:
            with open(fsops.long_path(path), 'rb') as fh:
                raw = fh.read(64 * 1024)
            for enc in ('utf-8-sig', 'utf-16', 'gb18030'):
                try:
                    if enc == 'utf-16' and not raw.startswith((b'\xff\xfe', b'\xfe\xff')):
                        continue
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = raw.decode('utf-8', 'replace')
            return _squash(text)[:limit]
        if ext in OFFICE and st.st_size < 200 * 1024 * 1024:
            member, text_tag, para_tag = OFFICE[ext]
            with zipfile.ZipFile(fsops.long_path(path)) as zf:
                names = sorted((n for n in zf.namelist() if n.startswith(member) and n.endswith('.xml')),
                               key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', n)])
                parts = []
                for name in names[:30]:
                    xml = zf.read(name)[:2 * 1024 * 1024].decode('utf-8', 'replace')
                    xml = xml.replace(f'</{para_tag}>', '\n')
                    parts.append(_TAG.sub('', xml))
                    if sum(len(p) for p in parts) > limit * 2:
                        break
            text = '\n'.join(parts).replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
            return _squash(text)[:limit] or None
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, RuntimeError):
        return None
    return None


def _squash(text: str) -> str:
    lines = [re.sub(r'[ \t　]+', ' ', ln).strip() for ln in text.splitlines()]
    return '\n'.join(ln for ln in lines if ln)


# ---------------------------------------------------------------------------

@dataclass
class FileRec:
    path: str
    name: str
    folder: str
    size: int
    mtime: float
    changed: float      # max(mtime, creation) on Windows: when it last appeared/changed here
    atime: float
    category: str
    root: str           # which personal folder it was found under


@dataclass
class Finding:
    id: str
    kind: str
    title: str
    files: list = field(default_factory=list)   # [{'path','size','mtime','note','remove'}]
    size: int = 0
    reason: str = ''
    confidence: float = 0.5
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        label, advice = KIND_INFO[self.kind]
        removable = [f for f in self.files if f.get('remove')]
        return {'id': self.id, 'kind': self.kind, 'kind_label': label, 'advice': advice, 'title': self.title,
                'files': self.files, 'count': len(self.files), 'size': self.size,
                'removable_size': sum(f['size'] for f in removable), 'removable_count': len(removable),
                'reason': self.reason, 'confidence': round(self.confidence, 2), **self.extra}


def _hash(path: str, limit: int = 64 * 1024 * 1024) -> str | None:
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(fsops.long_path(path), 'rb') as fh:
            read = 0
            while read < limit:
                block = fh.read(1024 * 1024)
                if not block:
                    break
                h.update(block)
                read += len(block)
    except OSError:
        return None
    return h.hexdigest()


def _is_program_dir(files, dirs) -> bool:
    """A program's own folder: DLLs, or an .exe together with the sub-folders programs bring along."""
    names = [e.name.lower() for e in files]
    if any(n.endswith('.dll') for n in names):
        return True
    return any(n.endswith('.exe') for n in names) and any(d.name.lower() in RUNTIME_DIRS for d in dirs)


class Insights:
    def __init__(self, env: dict | None = None, settings: dict | None = None, apps=None, now: float | None = None):
        self.env = env or paths.environment()
        self.settings = (settings or {}).get('insights', {}) if settings else {}
        self.apps = apps            # apps.AppManager (installed programs) or None
        self.now = now or time.time()
        self.files: list[FileRec] = []
        self.empty_dirs: list[str] = []
        self.roots: list[str] = []
        # folders whose files came together as one package (a program's folder, an extracted archive):
        # 「release-v0.3.1.md」 and 「release-v0.4.0.md」 there are two documents, not two versions of one
        self.bundle_dirs: list[str] = []
        # folders that are a program (a portable app unpacked in Downloads: its .exe, DLLs, plugins, its own
        # config and history): nothing inside is the user's loose file, and its .exe is no installer
        self.program_dirs: list[str] = []
        self.stats: dict = {}

    # -- collection ------------------------------------------------------------------
    def collect(self, roots: list[str], skip=None, cancel=None, progress=None) -> 'Insights':
        started = time.time()
        self.roots = []
        seen = set()
        dir_children: dict[str, list[str]] = {}
        dir_has_files: dict[str, bool] = {}
        projects: list[str] = []
        cloud = 0
        for root in roots:
            k = paths.key(root)
            if k in seen or not os.path.isdir(root) or any(paths.is_within(root, r) for r in self.roots):
                continue
            seen.add(k)
            self.roots.append(root)
            for dirpath, files, dirs in fsops.walk_files(root, cancel):
                if skip and dirpath != root and skip(dirpath):
                    dirs.clear()
                    continue
                if dirpath != root and _is_program_dir(files, dirs):
                    self.program_dirs.append(dirpath)
                    dir_has_files[dirpath] = True  # not walked further: not empty, nothing inside is offered
                    dirs.clear()
                    continue
                kept = [d for d in dirs if not managed_dir(d.name)]
                has = len(kept) != len(dirs)  # holds a program-managed folder (node_modules, .git...): not empty
                if any(d.name.lower() in ('.git', '.svn', '.hg') for d in dirs):
                    projects.append(dirpath)  # empty folders in a repository are often there on purpose
                dirs[:] = kept
                dir_children[dirpath] = [os.path.join(dirpath, d.name) for d in dirs]
                program = False
                for entry in files:
                    if entry.name.lower() in IGNORED_FILES:
                        continue
                    program = program or entry.name.lower().endswith(('.exe', '.dll'))
                    has = True
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    if getattr(st, 'st_file_attributes', 0) & winapi.CLOUD_ATTRIBUTES:
                        cloud += 1
                        continue
                    self.files.append(FileRec(os.path.join(dirpath, entry.name), entry.name, dirpath, st.st_size,
                                              st.st_mtime, fsops.age_time(st), st.st_atime,
                                              paths.category_of(entry.name), root))
                dir_has_files[dirpath] = has
                if program and dirpath != root:
                    # a program's folder (portable tool, unpacked package): its documents belong to the program
                    self.bundle_dirs.append(dirpath)
                if progress and len(self.files) % 2000 == 0:
                    progress({'files': len(self.files), 'current': dirpath})
                if len(self.files) >= MAX_FILES:
                    break
        # topmost folders with nothing in them at any depth
        empty: dict[str, bool] = {}

        def is_empty(d):
            if d in empty:
                return empty[d]
            value = not dir_has_files.get(d, True) and all(is_empty(c) for c in dir_children.get(d, []))
            empty[d] = value
            return value
        for d in sorted(dir_children, key=len, reverse=True):
            is_empty(d)
        root_keys = {paths.key(r) for r in self.roots}

        def topmost(d):
            parent = os.path.dirname(d)
            return paths.key(parent) in root_keys or not empty.get(parent, False)
        self.empty_dirs = sorted(d for d, e in empty.items() if e and paths.key(d) not in root_keys and topmost(d)
                                 and not any(paths.is_within(d, p) for p in projects))
        self.stats = {'files': len(self.files), 'cloud_skipped': cloud, 'bytes': sum(f.size for f in self.files),
                      'seconds': round(time.time() - started, 1), 'roots': self.roots,
                      'program_dirs': len(self.program_dirs)}
        return self

    # -- analysis --------------------------------------------------------------------
    def findings(self) -> list[dict]:
        claimed: set[str] = set()
        out: list[Finding] = []
        for fn in (self._incomplete, self._installers, self._archives, self._versions, self._stale, self._downloads):
            for f in fn(claimed):
                out.append(f)
                claimed.update(paths.key(x['path']) for x in f.files)
        out.extend(self._empty())
        out.sort(key=lambda f: (KIND_ORDER.index(f.kind), -f.size))
        for f in out:
            for x in f.files:
                x['score'] = file_score(f.kind, x)
        return [f.as_dict() for f in out]

    def _age_days(self, rec: FileRec) -> float:
        return (self.now - rec.changed) / DAY

    def _file(self, rec: FileRec, note: str = '', remove: bool = False) -> dict:
        return {'path': rec.path, 'name': rec.name, 'size': rec.size, 'mtime': rec.mtime, 'note': note, 'remove': remove,
                'category': rec.category}

    def _incomplete(self, claimed):
        rows = [r for r in self.files if os.path.splitext(r.name)[1].lower() in INCOMPLETE_EXTS
                and self._age_days(r) >= 3 and paths.key(r.path) not in claimed]
        if not rows:
            return []
        files = [self._file(r, f'{int(self._age_days(r))} 天前中断', remove=True) for r in rows]
        return [Finding('incomplete', 'incomplete_download', f'{len(rows)} 个没有下载完的文件', files,
                        sum(r.size for r in rows), '扩展名表明下载没有完成，而且已经超过 3 天没有继续。', 0.9)]

    def _installed_index(self):
        if self.apps is None:
            return []
        try:
            return self.apps.match_index()
        except Exception:  # noqa: BLE001 - registry trouble must not break the scan
            return []

    def _installers(self, claimed):
        index = self._installed_index()
        installed, old = [], []
        for r in self.files:
            ext = os.path.splitext(r.name)[1].lower()
            if ext not in INSTALLER_EXTS or r.size < 400 * 1024 or paths.key(r.path) in claimed:
                continue
            depth = len(paths.key(r.path)[len(paths.key(r.root)):].strip('\\').split('\\'))
            if depth > 2:  # only installers lying around, not programs inside a portable app folder
                continue
            name = compact(os.path.splitext(r.name)[0])
            app = _best_app(name, index) if len(name) >= 2 else None
            age = self._age_days(r)
            if app:
                note = f'已安装：{app["name"]}'
                iv, av = version_of(r.name), version_of(app.get('version') or '')
                if iv and av and iv < av:
                    note += f'（已装 {app["version"]}，比这个安装包新）'
                installed.append(self._file(r, note, remove=True))
            elif age >= 90:
                old.append(self._file(r, f'{int(age)} 天前下载，没有找到对应的已安装程序', remove=False))
        out = []
        if installed:
            out.append(Finding('installers', 'installer_installed', f'{len(installed)} 个已安装程序的安装包', installed,
                               sum(f['size'] for f in installed), '安装包对应的程序在「已安装的应用」中能找到。', 0.85))
        if old:
            out.append(Finding('old_installers', 'installer_old', f'{len(old)} 个很久以前下载的安装包', old,
                               sum(f['size'] for f in old), '下载超过 3 个月，而且对应的程序没有安装（或已卸载）。', 0.6))
        return out

    def _archives(self, claimed):
        by_folder: dict[str, dict[str, str]] = {}
        out_files = []
        for r in self.files:
            ext = os.path.splitext(r.name)[1].lower()
            if ext not in ARCHIVE_EXTS or paths.key(r.path) in claimed:
                continue
            stem = r.name[:-len(ext)]
            if stem.lower().endswith('.tar'):
                stem = stem[:-4]
            siblings = by_folder.get(r.folder)
            if siblings is None:
                try:
                    siblings = {base_name(e.name): os.path.join(r.folder, e.name)
                                for e in os.scandir(fsops.long_path(r.folder)) if e.is_dir(follow_symlinks=False)}
                except OSError:
                    siblings = {}
                by_folder[r.folder] = siblings
            folder = siblings.get(base_name(stem))
            if not folder:
                continue
            verified, total = _verify_zip(r.path, folder) if ext == '.zip' else (None, 0)
            if verified is False:
                continue
            self.bundle_dirs.append(folder)
            note = (f'其中 {total} 个文件都已在「{os.path.basename(folder)}」文件夹里，大小一致' if verified
                    else f'旁边有同名文件夹「{os.path.basename(folder)}」，可能已经解压过（这种格式无法自动核对内容）')
            out_files.append((self._file(r, note, remove=bool(verified)), verified))
        if not out_files:
            return []
        files = [f for f, _ in out_files]
        conf = 0.9 if all(v for _, v in out_files) else 0.6
        return [Finding('archives', 'archive_extracted', f'{len(files)} 个已经解压过的压缩包', files,
                        sum(f['size'] for f in files), '压缩包旁边已有同名的解压文件夹。', conf)]

    def _versions(self, claimed):
        groups: dict[tuple, list[FileRec]] = {}
        bundles = [paths.key(d) for d in self.bundle_dirs]
        for r in self.files:
            if paths.key(r.path) in claimed or r.size == 0:
                continue
            folder = paths.key(r.folder)
            if any(folder == b or folder.startswith(b + '\\') for b in bundles):
                continue  # documents that came with a program or an archive
            stem, ext = os.path.splitext(r.name)
            ext = _FAMILY.get(ext.lower(), ext.lower())
            if ext in INCOMPLETE_EXTS or ext in INSTALLER_EXTS:
                continue
            base = base_name(stem)
            if len(base) < 2:
                continue
            if r.category not in VERSION_CATEGORIES:
                continue  # fonts, databases, replays, extension-less app files
            if r.category in MEDIA and not is_copy_name(stem):
                continue  # IMG_0001 / IMG_0002 are different photos, not versions
            groups.setdefault((paths.key(r.folder), base, ext), []).append(r)
        out = []
        for (_, base, _), members in groups.items():
            if len(members) < 2 or len(members) > 40:
                continue
            # oldest first: by the version the name states (v0.4.0 after v0.3.1, even when copying or
            # unpacking gave both the same time), then by time; with equal times the name without a
            # copy marker counts as the newer one
            by_version = any(name_version(os.path.splitext(m.name)[0]) for m in members)
            members.sort(key=lambda m: (name_version(os.path.splitext(m.name)[0]) or (), m.mtime,
                                        not is_copy_name(os.path.splitext(m.name)[0])))
            digests = {}
            for m in members:
                if m.size <= 512 * 1024 * 1024:
                    digests[m.path] = _hash(m.path)
            by_digest: dict[str, list[FileRec]] = {}
            for m in members:
                d = digests.get(m.path)
                if d:
                    by_digest.setdefault(f'{m.size}:{d}', []).append(m)
            newest = members[-1]
            if is_copy_name(os.path.splitext(newest.name)[0]):
                # "论文 - 副本" saved last but identical to "论文": keep the original name
                twins = by_digest.get(f'{newest.size}:{digests.get(newest.path)}', []) if digests.get(newest.path) else []
                plain = [t for t in twins if not is_copy_name(os.path.splitext(t.name)[0])]
                if plain:
                    newest = max(plain, key=lambda t: t.mtime)
            files = []
            for m in members:
                same = [o for o in by_digest.get(f'{m.size}:{digests.get(m.path)}', []) if o is not m]
                if m is newest:
                    note, remove = ('最新的一份（版本号最大）' if by_version else '最新的一份（修改时间最晚）'), False
                elif same:
                    # keep exactly one of each identical set: the one without a copy marker, else the oldest
                    keeper = min([m] + same, key=lambda x: (is_copy_name(os.path.splitext(x.name)[0]), x.mtime))
                    if newest in same:
                        keeper = newest
                    remove = m is not keeper
                    note = (f'和「{keeper.name}」内容完全相同' if remove else '与其他副本内容相同，保留这一份')
                else:
                    note, remove = '较旧的版本', False
                files.append(self._file(m, note, remove))
            files.reverse()  # newest first
            files.sort(key=lambda f: f['path'] != newest.path)  # the one to keep always heads the group
            ident = 'ver-' + hashlib.md5((members[0].folder + '|' + base).encode('utf-8')).hexdigest()[:10]
            identical = sum(1 for f in files if f['remove'])
            reason = (f'{len(members)} 个文件名字相近，像是「{base}」的不同版本' +
                      (f'，其中 {identical} 个与另一份内容完全相同' if identical else '') + '。')
            title = f'「{os.path.splitext(newest.name)[0]}」等 {len(members)} 个版本'
            out.append(Finding(ident, 'name_versions', title, files, sum(m.size for m in members), reason,
                               0.85 if identical else 0.45,
                               {'group_base': base, 'folder': members[0].folder}))
        return out

    def _stale(self, claimed):
        min_size = float(self.settings.get('large_mb', 100)) * 1024 * 1024
        days = float(self.settings.get('stale_days', 365))
        rows = []
        for r in self.files:
            if r.size < min_size or paths.key(r.path) in claimed:
                continue
            age = self._age_days(r)
            opened = (self.now - r.atime) / DAY
            # Windows only keeps last-access times on small volumes (≤128 GB by default), so a
            # recent access time counts only when it is clearly later than the last change
            recently_opened = opened < days * 0.5 and age - opened > 30
            if age < days or recently_opened:
                continue
            rows.append(r)
        if not rows:
            return []
        files = [self._file(r, f'{_years(self._age_days(r))}没有修改过' + _category_hint(r.category)) for r in rows]
        files.sort(key=lambda f: -f['size'])
        return [Finding('stale', 'stale_large', f'{len(rows)} 个很久没动过的大文件', files, sum(r.size for r in rows),
                        f'大于 {int(min_size / 1024 / 1024)} MB，且超过 {int(days)} 天没有修改。', 0.4)]

    def _downloads(self, claimed):
        try:
            downloads = winapi.known_folder('downloads')
        except OSError:
            return []
        days = float(self.settings.get('download_days', 180))
        rows = [r for r in self.files if paths.is_within(r.path, downloads) and paths.key(r.path) not in claimed
                and self._age_days(r) >= days and r.size >= 1024 * 1024]
        if not rows:
            return []
        files = [self._file(r, f'{int(self._age_days(r))} 天前下载') for r in sorted(rows, key=lambda r: -r.size)]
        return [Finding('downloads', 'old_downloads', f'下载文件夹里 {len(rows)} 个放了很久的文件', files,
                        sum(r.size for r in rows), f'在下载文件夹里超过 {int(days)} 天。', 0.3)]

    def _empty(self):
        if not self.empty_dirs:
            return []
        files = [{'path': d, 'name': os.path.basename(d), 'size': 0, 'mtime': 0, 'note': '空文件夹', 'remove': True,
                  'category': 'folder'} for d in self.empty_dirs[:500]]
        return [Finding('empty', 'empty_folders', f'{len(files)} 个空文件夹', files, 0, '里面没有任何文件（包括子文件夹）。', 0.95)]


def _verify_zip(path: str, folder: str) -> tuple[bool | None, int]:
    """True if (nearly) every file in the zip exists in ``folder`` with the same size."""
    try:
        with zipfile.ZipFile(fsops.long_path(path)) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()][:5000]
    except (OSError, zipfile.BadZipFile, ValueError, RuntimeError):
        return None, 0
    if not infos:
        return None, 0
    names = [i.filename.replace('\\', '/') for i in infos]
    prefixes = {n.split('/', 1)[0] for n in names}
    strip = len(prefixes) == 1 and all('/' in n for n in names)

    def found(info):
        rel = info.filename.replace('\\', '/')
        candidates = [rel] + ([rel.split('/', 1)[1]] if strip else [])
        for c in candidates:
            p = os.path.join(folder, *c.split('/'))
            try:
                if os.path.getsize(fsops.long_path(p)) == info.file_size:
                    return True
            except OSError:
                continue
        return False
    hits = sum(1 for i in infos if found(i))
    if hits >= 0.9 * len(infos):
        return True, len(infos)
    return (False if hits < 0.5 * len(infos) else None), len(infos)


def _best_app(name: str, index: list[dict]) -> dict | None:
    best, score = None, 0
    for app in index:
        for k in app['keys']:
            if not k or len(k) < 2:
                continue
            if name == k:
                s = 3
            elif len(name) >= 4 and len(k) >= 4 and (k.startswith(name) or name.startswith(k)):
                s = 2
            else:
                continue
            if s > score:
                best, score = app, s
    return best


def _years(days: float) -> str:
    if days >= 365:
        return f'{days / 365:.1f} 年'.replace('.0 年', ' 年')
    return f'{int(days)} 天'


def _category_hint(cat: str) -> str:
    return {'video': '，如果是珍贵的视频可以移到移动硬盘', 'disk': '，系统镜像可以随时重新下载',
            'archive': '，旧压缩包', 'installer': '，旧安装包', 'image': ''}.get(cat, '')
