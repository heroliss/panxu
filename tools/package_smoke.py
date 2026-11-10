"""Start a built package the way a user gets it: extracted from a zip that was downloaded.

    python tools/package_smoke.py dist/PanXu-<version>-win-x64.zip

A browser marks a downloaded zip as coming from the internet (the Zone.Identifier stream) and Windows passes
the mark on to every extracted file. A package that starts fine from the build folder can fail with it (2.2.0
did: .NET refused to load Python.Runtime.dll). This extracts the zip into a temporary folder, marks every file,
starts PanXu.exe in a temporary 演练环境 (no UAC, nothing real is touched) and checks that its window opens.
Windows only; exit code 0 when the window opened.
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

MARK = '[ZoneTransfer]\r\nZoneId=3\r\n'
WINDOW_TITLE = '演练环境'   # app.py's sandbox window; the error dialog is titled just 「盘序」


def mark_downloaded(folder: str) -> int:
    count = 0
    for base, _, files in os.walk(folder):
        for name in files:
            with open(os.path.join(base, name) + ':Zone.Identifier', 'w', encoding='ascii', newline='') as fh:
                fh.write(MARK)
            count += 1
    return count


def window_titles(pid: int) -> list[str]:
    user32 = ctypes.windll.user32
    titles = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            text = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(ctypes.c_void_p(hwnd), text, 256)
            if text.value:
                titles.append(text.value)
        return True
    user32.EnumWindows(visit, None)
    return titles


def main() -> int:
    if os.name != 'nt' or len(sys.argv) != 2:
        print(__doc__)
        return 2
    # 盘序 allows one window at a time: with one open, the package would only say 「盘序已经在运行」
    kernel32 = ctypes.windll.kernel32
    running = kernel32.OpenMutexW(0x00100000, False, 'Local\\PanXu-SingleInstance')   # SYNCHRONIZE
    if running or ctypes.get_last_error() == 5 or kernel32.GetLastError() == 5:       # 5: an elevated instance
        if running:
            kernel32.CloseHandle(running)
        print('盘序正在运行：请先关闭它再检查（同一时间只允许一个窗口），这次没有检查打包版。')
        return 3
    work = tempfile.mkdtemp(prefix='panxu-package-')
    proc = None
    try:
        with zipfile.ZipFile(sys.argv[1]) as zf:
            zf.extractall(work)
        exe = os.path.join(work, 'PanXu', 'PanXu.exe')
        if not os.path.isfile(exe):
            print('PanXu\\PanXu.exe is not in the zip')
            return 1
        print(f'marked {mark_downloaded(work)} files as downloaded')
        proc = subprocess.Popen([exe, '--sandbox', os.path.join(work, 'sandbox')])
        titles: list[str] = []
        end = time.time() + 60
        while time.time() < end and proc.poll() is None:
            titles = window_titles(proc.pid)
            if titles:
                time.sleep(2)   # the error dialog, if any, comes instead of the window: look once more
                titles = window_titles(proc.pid)
                break
            time.sleep(0.5)
        if any(WINDOW_TITLE in t for t in titles):
            print('ok: the window opened:', titles)
            return 0
        print('FAILED: no 盘序 window. Windows of the process:', titles or 'none',
              '(exit code %s)' % proc.poll() if proc.poll() is not None else '')
        return 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(10)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
