"""Position Tk tool windows on the owner's display rather than the primary display."""
from __future__ import annotations

import sys


def _work_area(parent):
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                        ('work', wintypes.RECT), ('flags', wintypes.DWORD)]

        user = ctypes.WinDLL('user32', use_last_error=True)
        user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user.MonitorFromWindow.restype = wintypes.HANDLE
        user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user.GetMonitorInfoW.restype = wintypes.BOOL
        monitor = user.MonitorFromWindow(parent.winfo_id(), 2)  # MONITOR_DEFAULTTONEAREST
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if monitor and user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.work
            return r.left, r.top, r.right, r.bottom
    x, y = parent.winfo_vrootx(), parent.winfo_vrooty()
    return x, y, x + parent.winfo_vrootwidth(), y + parent.winfo_vrootheight()


def _bounds(parent, size, area):
    px, py, pw, ph = parent
    left, top, right, bottom = area
    # wm geometry sizes are client sizes; reserve room for title bar and borders.
    width = min(size[0], max(1, right - left - 16))
    height = min(size[1], max(1, bottom - top - 44))
    x = max(left, min(px + (pw - width) // 2, right - width - 16))
    y = max(top, min(py + (ph - height) // 2, bottom - height - 44))
    return width, height, x, y


def show_centered(dialog, parent):
    """After populating a withdrawn Toplevel, map it at its final position."""
    parent = parent.winfo_toplevel()
    dialog.update_idletasks()
    size = (dialog.winfo_reqwidth(), dialog.winfo_reqheight())
    parent_rect = (parent.winfo_rootx(), parent.winfo_rooty(), parent.winfo_width(), parent.winfo_height())
    w, h, x, y = _bounds(parent_rect, size, _work_area(parent))
    prefix = f'{w}x{h}' if (w, h) != size else ''
    # Explicit '+' means offset from the left/top, even on a negative-coordinate monitor.
    dialog.geometry(f'{prefix}+{x}+{y}')
    # Callers can wait_visibility() immediately, including on Win32 where
    # deiconify() can make the window visible before tkwait starts.
    dialog.after_idle(dialog.deiconify)
