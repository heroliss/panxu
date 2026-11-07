"""App-owned window controls. No desktop settings are changed; a fake window can be injected."""
from __future__ import annotations

import ctypes
import logging
import sys

from .jobs import UserError

log = logging.getLogger(__name__)


class WindowControls:
    def __init__(self, provider=None):
        self.provider = provider or (lambda: None)
        self.maximized = False
        self.custom = True
        self._frame_ready = False

    def bind(self, window):
        window.events.maximized += lambda: self._maximized(True)
        window.events.restored += lambda: self._maximized(False)
        window.events.shown += lambda: self._prepare_frame(window)

    def _maximized(self, value):
        self.maximized = value

    def state(self):
        return {'available': self.provider() is not None, 'maximized': self.maximized, 'custom': self.custom}

    def _prepare_frame(self, window):
        if sys.platform != 'win32':
            return
        try:
            # A borderless WinForms form otherwise loses its resize frame. Retain the native
            # sizing/system-menu styles without WS_CAPTION, and maximize inside the work area.
            from System import Action
            from System.Windows.Forms import Screen
            native = window.native
            def configure():
                user = _user32()
                hwnd = native.Handle.ToInt64()
                if not self._frame_ready:
                    style = user.GetWindowLongPtrW(hwnd, -16)
                    user.SetWindowLongPtrW(hwnd, -16, (style | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000) & ~0x00C00000)
                    user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027)  # frame changed, no move/size/z-order
                    self._preserve_restore_size(native, user)
                    self._frame_ready = True
                native.MaximizedBounds = Screen.FromHandle(native.Handle).WorkingArea
            native.Invoke(Action(configure))
        except Exception:
            log.exception('custom window frame unavailable; restoring the system frame')
            self.custom = False
            try:
                from System import Action
                from System.Windows.Forms import FormBorderStyle
                window.native.Invoke(Action(lambda: setattr(window.native, 'FormBorderStyle', FormBorderStyle.Sizable)))
            except Exception:
                log.exception('restoring system frame failed')

    def perform(self, action):
        if action not in ('minimize', 'toggle_maximize', 'close'):
            raise UserError('未知窗口操作')
        window = self.provider()
        if window is None:
            raise UserError('窗口操作只在桌面程序中可用')
        if action == 'minimize':
            window.minimize()
        elif action == 'toggle_maximize':
            target = not self.maximized
            if not target:
                window.restore()
            else:
                # Re-evaluate the current monitor after the window has been moved.
                self._prepare_frame(window)
                window.maximize()
            self.maximized = target
        elif action == 'close':
            window.destroy()  # emits the existing closing event, including cleanup confirmation
        return self.state()

    @staticmethod
    def _preserve_restore_size(native, user):
        # WinForms caches client dimensions for a borderless form. Our native sizing
        # frame must not be subtracted again every time the window is restored.
        from System import Action
        normal = [native.Width, native.Height]
        state = [str(native.WindowState)]
        pending = [False]
        def resized(sender, event):
            current = str(native.WindowState)
            previous, state[0] = state[0], current
            if current != 'Normal' or pending[0]:
                return
            if previous == 'Normal':
                normal[:] = [native.Width, native.Height]
                return
            expected = tuple(normal)
            pending[0] = True
            def repair():
                try:
                    actual = (native.Width, native.Height)
                    frame = (native.Width - native.ClientSize.Width, native.Height - native.ClientSize.Height)
                    # Only repair this known border discrepancy. A snap to a different
                    # size or a move to a differently scaled monitor remains Windows-owned.
                    if restore_size_needed(expected, actual, frame):
                        user.SetWindowPos(native.Handle.ToInt64(), None, 0, 0, *expected, 0x0016)
                    normal[:] = [native.Width, native.Height]
                finally:
                    pending[0] = False
            native.BeginInvoke(Action(repair))
        native.Resize += resized


def restore_size_needed(previous, current, frame):
    return any(frame) and all(abs(old - new - edge) <= 1 for old, new, edge in zip(previous, current, frame))


def _user32():
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.SetWindowPos.restype = wintypes.BOOL
    return user
