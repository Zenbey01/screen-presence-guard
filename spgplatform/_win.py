"""Windows backend — Win32 via ctypes. See spgplatform/__init__.py for the API.

Two traps live here, both measured on Win11, both of which cost this project
weeks before they were understood:

| Call | Reality |
|---|---|
| `SetLastInputInfo` | **Not exported by user32 at all.** Documented on MSDN, absent in practice — the original `reset_idle()` raised `AttributeError` on every call for months. |
| `SetCursorPos`     | Moves the pointer, but Windows does **not** count it as input. Idle timer went 7125ms -> 7203ms across a call. Cannot hold off a lock. |
| `SendInput`        | Actually resets it: 7203ms -> 78ms. A `(0, 0)` relative move is a visual no-op that still registers. |

`_INPUT`'s union must be sized for `_MOUSEINPUT` (`sizeof(_INPUT) == 40` on
x64). A union holding only `KEYBDINPUT` yields 32 bytes and `SendInput`
silently returns **0** without injecting anything.
"""

import ctypes
import os

NAME = "windows"

ES_CONTINUOUS       = 0x80000000
ES_SYSTEM_REQUIRED  = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

INPUT_MOUSE      = 0
MOUSEEVENTF_MOVE = 0x0001

_user32   = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


def data_dir() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                        "ScreenPresenceGuard")


def keep_display_on():
    _kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED)


def keep_system_on():
    _kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)


def release_awake():
    _kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def _send_move(dx: int, dy: int):
    inp = _INPUT(type=INPUT_MOUSE,
                 mi=_MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, None))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def reset_idle():
    """A zero-distance relative move: invisible, and Windows still counts it."""
    _send_move(0, 0)


def move_cursor(x: int, y: int):
    """Injected move to an absolute point — counts as input.

    SendInput is relative, so the delta is computed against the pointer's real
    position each step rather than dead-reckoned. Pointer acceleration still
    makes the landing imprecise; the caller finishes with warp_cursor().
    """
    cx, cy = cursor_pos()
    _send_move(x - cx, y - cy)


def warp_cursor(x: int, y: int):
    _user32.SetCursorPos(int(x), int(y))


def cursor_pos() -> tuple:
    pt = _POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def idle_ms() -> int:
    """GetLastInputInfo *is* exported (unlike SetLastInputInfo) and is the
    value a standard enforced-lock policy watches."""
    li = _LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    _user32.GetLastInputInfo(ctypes.byref(li))
    return _kernel32.GetTickCount() - li.dwTime


def any_key_pressed() -> bool:
    """True if any key went down since the previous call.

    Polled, never bound: the overlay is overrideredirect(True), so Windows
    never makes it the foreground window. While the user sits in another app
    their keystrokes go there and Tk's <KeyPress> never fires. Range starts at
    0x08 to skip the mouse buttons (0x01-0x06).
    """
    gaks = _user32.GetAsyncKeyState
    return any(gaks(vk) & 0x0001 for vk in range(0x08, 0xFF))


def virtual_screen() -> tuple:
    g = _user32.GetSystemMetrics
    return (g(76), g(77), g(78), g(79))   # XVIRTUALSCREEN, Y, CX, CY


def preflight() -> list:
    """Windows needs no runtime permission grant for any of the above."""
    return []
