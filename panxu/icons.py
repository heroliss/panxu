"""Program icons as small PNG images (for 应用 and 开机与服务). Standard library only.

An icon is drawn twice, onto black and onto white; the difference gives the
alpha channel for both modern (32-bit) and old (masked) icons. Extracting
reads the file's resources only, nothing is executed.
"""
from __future__ import annotations

import base64
import ctypes
import os
import re
import struct
import zlib

from . import winapi

SIZE = 32


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """A truecolour-with-alpha PNG from rows of RGBA bytes."""
    stride = width * 4
    raw = b''.join(b'\x00' + rgba[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def unblend(on_black: bytes, on_white: bytes) -> bytes:
    """RGBA from the same image drawn on black and on white (both BGRA, as GDI writes them)."""
    out = bytearray(len(on_black))
    for i in range(0, len(on_black), 4):
        b0, g0, r0 = on_black[i], on_black[i + 1], on_black[i + 2]
        b1, g1, r1 = on_white[i], on_white[i + 1], on_white[i + 2]
        # on white = alpha * colour + (1 - alpha) * 255, on black = alpha * colour
        alpha = 255 - ((r1 - r0) + (g1 - g0) + (b1 - b0)) // 3
        alpha = max(0, min(255, alpha))
        if alpha:
            out[i] = min(255, r0 * 255 // alpha)
            out[i + 1] = min(255, g0 * 255 // alpha)
            out[i + 2] = min(255, b0 * 255 // alpha)
        out[i + 3] = alpha
    return bytes(out)


def data_url(png: bytes) -> str:
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


def _shortcut_icon(path: str) -> tuple[str, int] | None:
    """Where the icon of a .lnk lives (file, index), as the shell resolves it -- without the shortcut arrow."""
    from ctypes import wintypes

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [('hIcon', wintypes.HICON), ('iIcon', ctypes.c_int), ('dwAttributes', wintypes.DWORD),
                    ('szDisplayName', wintypes.WCHAR * 260), ('szTypeName', wintypes.WCHAR * 80)]

    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    ole32 = ctypes.WinDLL('ole32', use_last_error=True)
    shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT]
    shell32.SHGetFileInfoW.restype = ctypes.c_size_t
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    com = ole32.CoInitializeEx(None, 0x2)  # resolving a shortcut needs COM on this thread
    try:
        info = SHFILEINFOW()
        if not shell32.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info), 0x1000):  # SHGFI_ICONLOCATION
            return None
        where = os.path.expandvars(info.szDisplayName)
        return (where, info.iIcon) if where and not where.lower().endswith('.lnk') else None
    finally:
        if com in (0, 1):  # S_OK / S_FALSE
            ole32.CoUninitialize()


def _gdi():
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
    user32.PrivateExtractIconsW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
                                            wintypes.UINT, wintypes.UINT]
    user32.PrivateExtractIconsW.restype = wintypes.UINT
    user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT, wintypes.HBRUSH, wintypes.UINT]
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    return user32, gdi32


def _render(icon, size: int) -> bytes | None:
    """PNG of an HICON (which is destroyed afterwards)."""
    from ctypes import wintypes
    user32, gdi32 = _gdi()

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long), ('biHeight', ctypes.c_long),
                    ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                    ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]

    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(dc, bitmap) if bitmap else None
    try:
        if not bitmap or not bits:
            return None
        frames = []
        for background in (0, 255):
            ctypes.memset(bits, background, size * size * 4)
            if not user32.DrawIconEx(dc, 0, 0, icon, size, size, 0, None, 0x0003):  # DI_NORMAL
                return None
            gdi32.GdiFlush()
            frames.append(ctypes.string_at(bits, size * size * 4))
        return encode_png(size, size, unblend(*frames))
    finally:
        if old:
            gdi32.SelectObject(dc, old)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, screen)
        user32.DestroyIcon(icon)


def file_icon(path: str, index: int = 0, size: int = SIZE) -> bytes | None:
    """PNG of an icon in an .exe/.dll/.ico (index as in DisplayIcon, negative = resource id), or None."""
    if winapi.simulation() is not None or not winapi.IS_WINDOWS or not path or not os.path.isfile(path):
        return None
    from ctypes import wintypes
    user32, _ = _gdi()
    icon = wintypes.HICON()
    icon_id = wintypes.UINT()
    found = user32.PrivateExtractIconsW(path, index, size, size, ctypes.byref(icon), ctypes.byref(icon_id), 1, 0)
    if found in (0, 0xFFFFFFFF) or not icon:
        if path.lower().endswith('.lnk'):
            where = _shortcut_icon(path)  # a Startup-folder shortcut: the icon it shows in Explorer
            return file_icon(where[0], where[1], size) if where else None
        return None
    return _render(icon, size)


TYPE_KEY = re.compile(r'^(folder|\.[a-z0-9_+~-]{1,16})$')


def type_icon(kind: str, size: int = SIZE) -> bytes | None:
    """PNG of the icon Explorer shows for a file type ('.pdf') or for 'folder'. Only the name is passed to
    the shell (SHGFI_USEFILEATTRIBUTES): no file is read, so this also works for files that are gone."""
    if not winapi.IS_WINDOWS or not TYPE_KEY.match(kind or ''):
        return None
    from ctypes import wintypes

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [('hIcon', wintypes.HICON), ('iIcon', ctypes.c_int), ('dwAttributes', wintypes.DWORD),
                    ('szDisplayName', wintypes.WCHAR * 260), ('szTypeName', wintypes.WCHAR * 80)]

    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    ole32 = ctypes.WinDLL('ole32', use_last_error=True)
    shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT]
    shell32.SHGetFileInfoW.restype = ctypes.c_size_t
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    name, attrs = ('folder', 0x10) if kind == 'folder' else ('file' + kind, 0x80)  # DIRECTORY / NORMAL
    com = ole32.CoInitializeEx(None, 0x2)
    try:
        info = SHFILEINFOW()
        if not shell32.SHGetFileInfoW(name, attrs, ctypes.byref(info), ctypes.sizeof(info), 0x100 | 0x10) or not info.hIcon:
            return None  # SHGFI_ICON | SHGFI_USEFILEATTRIBUTES (large, 32 px)
        return _render(info.hIcon, size)
    finally:
        if com in (0, 1):
            ole32.CoUninitialize()
