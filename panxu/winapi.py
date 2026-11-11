"""Thin ctypes wrappers over the Win32 APIs PanXu needs.

Every function degrades to a harmless fallback on non-Windows platforms so
that the platform-independent logic can be unit tested on Linux. Nothing
here decides *what* to delete; callers validate paths before calling in.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import uuid

IS_WINDOWS = sys.platform == 'win32'

# The simulation (演练环境, see sandbox.py) replaces the few calls that touch
# real system state: shell folders, the Recycle Bin, disk usage, elevation.
_sim = None


def set_simulation(sim) -> None:
    global _sim
    _sim = sim


def simulation():
    return _sim

# File attribute bits (also present in os.stat_result.st_file_attributes).
FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_SPARSE_FILE = 0x200
FILE_ATTRIBUTE_COMPRESSED = 0x800
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
# A file with any of these bits may be a cloud placeholder: reading its
# content would trigger a download, and it may occupy no local space.
CLOUD_ATTRIBUTES = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS

CREATE_NO_WINDOW = 0x08000000

if IS_WINDOWS:
    from ctypes import wintypes

    _shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    _version = ctypes.WinDLL('version', use_last_error=True)
    _ole32 = ctypes.WinDLL('ole32', use_last_error=True)
    _user32 = ctypes.WinDLL('user32', use_last_error=True)

    class SHFILEOPSTRUCTW(ctypes.Structure):
        # x64 layout uses natural alignment (pshpack1 applies to 32-bit only).
        _fields_ = [
            ('hwnd', wintypes.HWND),
            ('wFunc', wintypes.UINT),
            ('pFrom', wintypes.LPCWSTR),
            ('pTo', wintypes.LPCWSTR),
            ('fFlags', ctypes.c_ushort),
            ('fAnyOperationsAborted', wintypes.BOOL),
            ('hNameMappings', ctypes.c_void_p),
            ('lpszProgressTitle', wintypes.LPCWSTR),
        ]

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('i64Size', ctypes.c_int64), ('i64NumItems', ctypes.c_int64)]

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD),
                    ('Data4', ctypes.c_ubyte * 8)]

        @classmethod
        def from_string(cls, text):
            raw = uuid.UUID(text).bytes_le
            return cls.from_buffer_copy(raw)

    _shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    _shell32.SHFileOperationW.restype = ctypes.c_int
    _shell32.SHQueryRecycleBinW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SHQUERYRBINFO)]
    _shell32.SHQueryRecycleBinW.restype = ctypes.c_long
    _shell32.SHEmptyRecycleBinW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD]
    _shell32.SHEmptyRecycleBinW.restype = ctypes.c_long
    _shell32.IsUserAnAdmin.argtypes = []
    _shell32.IsUserAnAdmin.restype = wintypes.BOOL
    _shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.LPCWSTR, ctypes.c_int]
    _shell32.ShellExecuteW.restype = ctypes.c_void_p
    _shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                              ctypes.POINTER(ctypes.c_wchar_p)]
    _shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    _ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    _ole32.CoTaskMemFree.restype = None
    _ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _ole32.CoInitializeEx.restype = ctypes.c_long
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                                ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                                ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    _kernel32.GetVolumeInformationW.restype = wintypes.BOOL
    _kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetDriveTypeW.restype = wintypes.UINT
    _kernel32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetCompressedFileSizeW.restype = wintypes.DWORD
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
                                            ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    _version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    _version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    _version.GetFileVersionInfoW.restype = wintypes.BOOL
    _version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                        ctypes.POINTER(wintypes.UINT)]
    _version.VerQueryValueW.restype = wintypes.BOOL
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    _user32.MessageBoxW.restype = ctypes.c_int

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMMKDIR = 0x0200
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000
SHERB_NOCONFIRMATION = 0x1
SHERB_NOPROGRESSUI = 0x2
SHERB_NOSOUND = 0x4
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE, DRIVE_CDROM, DRIVE_RAMDISK = 2, 3, 4, 5, 6

KNOWN_FOLDERS = {
    'desktop': '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}',
    'documents': '{FDD39AD0-238F-46AF-ADB4-6C85480369C7}',
    'downloads': '{374DE290-123F-4565-9164-39C4925E467B}',
    'pictures': '{33E28130-4E1E-4676-835A-98395C3BC3BB}',
    'videos': '{18989B1D-99B5-455B-841C-AB7C74E4DDFC}',
    'music': '{4BD8D571-6D19-48D3-BE97-422220080E43}',
}
_KNOWN_FALLBACK = {'desktop': 'Desktop', 'documents': 'Documents', 'downloads': 'Downloads',
                   'pictures': 'Pictures', 'videos': 'Videos', 'music': 'Music'}


def co_initialize():
    """Initialise COM for the calling thread (needed by some shell APIs)."""
    if IS_WINDOWS:
        _ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED; S_FALSE if already done


def is_admin() -> bool:
    if _sim is not None:
        return _sim.is_admin()
    if not IS_WINDOWS:
        return hasattr(os, 'geteuid') and os.geteuid() == 0
    try:
        return bool(_shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin(executable: str, params: str, cwd: str | None = None) -> bool:
    """Ask UAC to start a new elevated instance. Returns False if refused."""
    if not IS_WINDOWS:
        return False
    rc = _shell32.ShellExecuteW(None, 'runas', executable, params, cwd, 1)
    return (rc or 0) > 32


def recycle_paths(paths: list[str], hwnd=None) -> tuple[int, bool]:
    """Move absolute paths to the Recycle Bin in one shell operation.

    FOF_WANTNUKEWARNING makes Windows ask before permanently deleting an item
    that cannot be recycled (too large for the bin, or a drive without one),
    so nothing silently bypasses the Recycle Bin.
    Returns (shell result code, aborted flag). Callers check which paths
    still exist to determine per-item success.
    """
    if not paths:
        return 0, False
    if _sim is not None:
        return _sim.recycle_paths(paths)
    if not IS_WINDOWS:
        raise OSError('Recycle Bin is only available on Windows')
    co_initialize()
    buffer = '\0'.join(paths) + '\0\0'
    op = SHFILEOPSTRUCTW()
    op.hwnd = hwnd
    op.wFunc = FO_DELETE
    op.pFrom = buffer
    op.pTo = None
    # not FOF_SILENT: a large folder takes minutes, and Windows' own progress window (with its 取消) shows how far
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_NOCONFIRMMKDIR | FOF_WANTNUKEWARNING
    rc = _shell32.SHFileOperationW(ctypes.byref(op))
    return rc, bool(op.fAnyOperationsAborted)


def _dollar_i_original(path: str) -> str | None:
    """The original path in a Recycle Bin $I record: an 8-byte version, size, deletion time, then the path
    (version 2, Windows 10+: a 4-byte length in characters first; version 1: 260 characters)."""
    try:
        with open(path, 'rb') as fh:
            data = fh.read(4096)
    except OSError:
        return None
    if len(data) < 24:
        return None
    if int.from_bytes(data[0:8], 'little') == 2 and len(data) >= 28:
        n = int.from_bytes(data[24:28], 'little')
        raw = data[28:28 + 2 * n]
    else:
        raw = data[24:24 + 520]
    text = raw.decode('utf-16-le', 'ignore').split('\0', 1)[0]
    return text or None


def recycled_since(root: str, since: float) -> set[str] | None:
    """Original paths of what went into this drive's Recycle Bin since ``since`` (each item there has a $I record
    holding it), or None when the bin cannot be read. Something that left its place without such a record was
    deleted for good -- e.g. too large for the bin, and the user answered Windows' 「直接删除?」 with yes."""
    if _sim is not None:
        return _sim.recycled_since(since)
    if not IS_WINDOWS:
        return None
    base = os.path.join(root, '$Recycle.Bin')
    found, readable = set(), False
    try:
        with os.scandir(base) as users:
            for user in users:
                try:
                    with os.scandir(user.path) as it:  # other users' folders are not readable: skipped
                        readable = True
                        for entry in it:
                            if entry.name.upper().startswith('$I') and entry.stat().st_mtime >= since - 2:
                                original = _dollar_i_original(entry.path)
                                if original:
                                    found.add(original)
                except OSError:
                    continue
    except OSError:
        return None
    return found if readable else None


def recycle_bin_info(root: str | None = None) -> tuple[int, int]:
    """(bytes, item count) in the Recycle Bin of a drive, or all drives."""
    if _sim is not None:
        return _sim.recycle_bin_info()
    if not IS_WINDOWS:
        return 0, 0
    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
    hr = _shell32.SHQueryRecycleBinW(root, ctypes.byref(info))
    if hr != 0:
        return 0, 0
    return int(info.i64Size), int(info.i64NumItems)


def empty_recycle_bin(root: str | None = None) -> bool:
    if _sim is not None:
        return _sim.empty_recycle_bin()
    if not IS_WINDOWS:
        return False
    hr = _shell32.SHEmptyRecycleBinW(None, root, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND)
    # E_UNEXPECTED is returned when the bin is already empty.
    return hr in (0, ctypes.c_long(0x8000FFFF).value)


def known_folder(name: str) -> str:
    """Resolve a user shell folder, honouring redirection (OneDrive, D:\\...)."""
    if _sim is not None:
        return _sim.known_folder(name)
    home = os.path.expanduser('~')
    fallback = os.path.join(home, _KNOWN_FALLBACK[name])
    if not IS_WINDOWS:
        return fallback
    guid = GUID.from_string(KNOWN_FOLDERS[name])
    out = ctypes.c_wchar_p()
    hr = _shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
    try:
        if hr == 0 and out.value:
            return out.value
    finally:
        if out:
            _ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
    return fallback


def protect_secret(data: bytes) -> tuple[bytes, str]:
    """Encrypt for the current Windows user with DPAPI. Returns (blob, scheme)."""
    if not IS_WINDOWS:
        return data, 'plain'
    blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not _crypt32.CryptProtectData(ctypes.byref(blob_in), 'PanXu', None, None, None, 0x1, ctypes.byref(blob_out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData), 'dpapi'
    finally:
        _kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def unprotect_secret(blob: bytes, scheme: str) -> bytes:
    if scheme == 'plain':
        return blob
    if scheme != 'dpapi' or not IS_WINDOWS:
        raise ValueError('无法在此系统上解密已保存的密钥')
    blob_in = DATA_BLOB(len(blob), ctypes.cast(ctypes.create_string_buffer(blob, len(blob)), ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not _crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def file_version_info(path: str) -> dict:
    """CompanyName / FileDescription / ProductName from an executable, if any."""
    if _sim is not None:
        return _sim.file_version_info(path)
    if not IS_WINDOWS or not path:
        return {}
    try:
        handle = wintypes.DWORD(0)
        size = _version.GetFileVersionInfoSizeW(path, ctypes.byref(handle))
        if not size:
            return {}
        buf = ctypes.create_string_buffer(size)
        if not _version.GetFileVersionInfoW(path, 0, size, buf):
            return {}
        ptr, length = ctypes.c_void_p(), wintypes.UINT(0)
        codepages = []
        if _version.VerQueryValueW(buf, '\\VarFileInfo\\Translation', ctypes.byref(ptr), ctypes.byref(length)) and length.value >= 4:
            words = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD))
            for i in range(0, length.value // 4):
                codepages.append('%04x%04x' % (words[i * 2], words[i * 2 + 1]))
        codepages += ['040904b0', '080404b0', '040904e4']
        result = {}
        for field in ('CompanyName', 'FileDescription', 'ProductName'):
            for cp in codepages:
                ptr, length = ctypes.c_void_p(), wintypes.UINT(0)
                if _version.VerQueryValueW(buf, f'\\StringFileInfo\\{cp}\\{field}', ctypes.byref(ptr), ctypes.byref(length)) and length.value:
                    value = ctypes.wstring_at(ptr, length.value).rstrip('\0').strip()
                    if value:
                        result[field] = value
                        break
        return result
    except (OSError, ValueError):
        return {}


def disk_usage(root: str):
    """shutil.disk_usage, or the simulated drive's figures."""
    if _sim is not None:
        return _sim.disk_usage(root)
    return shutil.disk_usage(root)


def volume_label(root: str) -> str:
    if not IS_WINDOWS:
        return ''
    name = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    if _kernel32.GetVolumeInformationW(root, name, 261, None, None, None, fs, 261):
        return name.value
    return ''


def drive_type(root: str) -> int:
    if not IS_WINDOWS:
        return DRIVE_FIXED
    return int(_kernel32.GetDriveTypeW(root))


class SingleInstance:
    """Named mutex guarding against two windows scanning/deleting at once."""

    def __init__(self, name: str = 'Local\\PanXu-SingleInstance'):
        self.name = name
        self.handle = None

    def acquire(self) -> bool:
        if not IS_WINDOWS:
            return True
        handle = _kernel32.CreateMutexW(None, False, self.name)
        err = ctypes.get_last_error()
        if not handle:
            return err != ERROR_ACCESS_DENIED  # access denied: owned by another (elevated) instance
        if err == ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def release(self):
        if IS_WINDOWS and self.handle:
            _kernel32.CloseHandle(self.handle)
            self.handle = None


_restart_manager = None


def programs_using(files: list[str]) -> list[str] | None:
    """Names of the programs that have any of these files open right now (Windows' Restart Manager, the
    service installers use to ask 「which programs must close?」), or None when that cannot be told.

    It sees every open handle, not only locks: a cache file a program opened while allowing others to delete
    it (Chromium does) can be moved away by Windows, yet the program is still using it. Read-only: the
    session is only asked for its list and ended; nothing is shut down or restarted."""
    if not files:
        return []
    if _sim is not None:
        return _sim.programs_using(files)
    if not IS_WINDOWS:
        return None
    global _restart_manager
    from ctypes import wintypes

    class UniqueProcess(ctypes.Structure):
        _fields_ = [('dwProcessId', wintypes.DWORD), ('ProcessStartTime', wintypes.FILETIME)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [('Process', UniqueProcess), ('strAppName', wintypes.WCHAR * 256),
                    ('strServiceShortName', wintypes.WCHAR * 64), ('ApplicationType', ctypes.c_int),
                    ('AppStatus', wintypes.ULONG), ('TSSessionId', wintypes.DWORD), ('bRestartable', wintypes.BOOL)]
    try:
        if _restart_manager is None:
            _restart_manager = ctypes.WinDLL('rstrtmgr', use_last_error=True)
        rm = _restart_manager
        session = wintypes.DWORD()
        key = ctypes.create_unicode_buffer(33)
        if rm.RmStartSession(ctypes.byref(session), 0, key):
            return None
    except (OSError, AttributeError):
        return None
    try:
        array = (wintypes.LPCWSTR * len(files))(*files)
        if rm.RmRegisterResources(session, len(files), array, 0, None, 0, None):
            return None
        needed, count, reasons = wintypes.UINT(0), wintypes.UINT(0), wintypes.DWORD(0)
        rc = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        if rc == 0:
            return []
        if rc != 234:  # ERROR_MORE_DATA: how many there are
            return None
        infos = (ProcessInfo * needed.value)()
        count = wintypes.UINT(needed.value)
        if rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons)):
            return None
        mine = os.getpid()
        names = []
        for info in infos[:count.value]:
            name = info.strAppName.strip() or f'进程 {info.Process.dwProcessId}'
            if info.Process.dwProcessId != mine and name not in names:
                names.append(name)
        return names
    finally:
        rm.RmEndSession(session)


def find_window(title: str):
    if not IS_WINDOWS:
        return None
    return _user32.FindWindowW(None, title) or None


def focus_window(title: str) -> bool:
    if not IS_WINDOWS:
        return False
    hwnd = _user32.FindWindowW(None, title)
    if not hwnd:
        return False
    _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    return bool(_user32.SetForegroundWindow(hwnd))


def flash_window(titles: tuple[str, ...]) -> bool:
    """Flash the taskbar button of our window until it comes to the front (FLASHW_TRAY | FLASHW_TIMERNOFG):
    the usual Windows way to say "done" without stealing focus."""
    if not IS_WINDOWS:
        return False
    from ctypes import wintypes

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.UINT), ('hwnd', wintypes.HWND), ('dwFlags', wintypes.DWORD),
                    ('uCount', wintypes.UINT), ('dwTimeout', wintypes.DWORD)]

    for title in titles:
        hwnd = find_window(title)
        if hwnd:
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x2 | 0xC, 0, 0)
            _user32.FlashWindowEx(ctypes.byref(info))
            return True
    return False


def ask_yes_no(text: str, title: str, owner=None) -> bool:
    """A Windows question box (是 / 否, warning icon); No is the default button. Off Windows: yes."""
    if not IS_WINDOWS:
        return True
    MB_YESNO, MB_ICONWARNING, MB_DEFBUTTON2, IDYES = 0x4, 0x30, 0x100, 6
    return _user32.MessageBoxW(owner, text, title, MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) == IDYES


def message_box(text: str, title: str) -> None:
    if IS_WINDOWS:
        _user32.MessageBoxW(None, text, title, 0x40)
    else:
        print(f'{title}: {text}', file=sys.stderr)


def run_hidden(args: list[str], timeout: float | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Run a console tool without flashing a window. Args is a list (no shell)."""
    flags = CREATE_NO_WINDOW if IS_WINDOWS else 0
    return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags, **kwargs)


def popen_hidden(args: list[str], **kwargs) -> subprocess.Popen:
    flags = CREATE_NO_WINDOW if IS_WINDOWS else 0
    return subprocess.Popen(args, creationflags=flags, **kwargs)


def decode_console(data: bytes) -> str:
    """Decode output from Windows console tools (often OEM code page, e.g. GBK)."""
    if not data:
        return ''
    for enc in ('utf-8', 'oem', 'mbcs', 'gbk'):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('utf-8', 'replace')


def reveal_in_explorer(path: str) -> None:
    if not IS_WINDOWS:
        return
    if os.path.isdir(path):
        os.startfile(path)  # noqa: S606 - path comes from our own scan results
    else:
        subprocess.Popen(['explorer', '/select,', path])


def open_uri(uri: str) -> None:
    if IS_WINDOWS:
        os.startfile(uri)


def open_file(path: str) -> None:
    """Open a document with its default program, like a double-click in Explorer."""
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606 - callers refuse programs and scripts first


def shell_execute(executable: str, params: str = '', verb: str = 'open') -> bool:
    """Start a program the way Explorer does (lets it ask for elevation itself)."""
    if not IS_WINDOWS:
        return False
    rc = _shell32.ShellExecuteW(None, verb, executable, params or None, None, 1)
    return (rc or 0) > 32


def set_file_times(path: str, created: float | None = None, modified: float | None = None,
                   accessed: float | None = None) -> bool:
    """Set timestamps including the creation time (Windows only; used to build the simulation)."""
    if modified is not None or accessed is not None:
        st = os.stat(path)
        os.utime(path, (accessed if accessed is not None else st.st_atime,
                        modified if modified is not None else st.st_mtime))
    if not IS_WINDOWS or created is None:
        return True
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    k32.SetFileTime.restype = wintypes.BOOL
    handle = k32.CreateFileW(path, 0x100, 0x7, None, 3, 0x02000000, None)  # FILE_WRITE_ATTRIBUTES, BACKUP_SEMANTICS
    if handle in (None, ctypes.c_void_p(-1).value):
        return False
    try:
        ft = ctypes.c_ulonglong(int((created + 11644473600) * 10_000_000))
        return bool(k32.SetFileTime(handle, ctypes.byref(ft), None, None))
    finally:
        k32.CloseHandle(handle)


def file_allocation_size(path: str) -> int | None:
    """Disk bytes for a sparse/compressed file, or None if unavailable.

    Metadata only: this does not read file contents. Callers must first reject
    links and cloud placeholders, because GetCompressedFileSizeW follows links.
    https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-getcompressedfilesizew
    """
    if _sim is not None:
        return _sim.file_allocation_size(path)
    if not IS_WINDOWS:
        return None
    high = wintypes.DWORD(0)
    ctypes.set_last_error(0)  # 0xffffffff can also be a successful low DWORD
    low = _kernel32.GetCompressedFileSizeW(path, ctypes.byref(high))
    if low == 0xffffffff and ctypes.get_last_error():
        return None
    return (high.value << 32) | low


def make_sparse(fileobj) -> bool:
    """Mark an open file as sparse so a large simulated file uses no disk space (NTFS)."""
    if not IS_WINDOWS:
        return True  # Linux/macOS file systems create holes by themselves
    try:
        import msvcrt
        handle = msvcrt.get_osfhandle(fileobj.fileno())
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k32.DeviceIoControl.restype = wintypes.BOOL
        returned = wintypes.DWORD(0)
        return bool(k32.DeviceIoControl(handle, 0x000900C4, None, 0, None, 0, ctypes.byref(returned), None))
    except (OSError, ValueError):
        return False
