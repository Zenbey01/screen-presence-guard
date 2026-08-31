"""Windows implementation of the platform-neutral primitives main.py needs.

main.py imports these five names as bare identifiers (`from _platform_win
import _cursor_pos, ...`), not as `_platform_win._cursor_pos(...)` — that is
deliberate. The 60-test pytest suite does `monkeypatch.setattr(spg,
"_cursor_pos", ...)` against main.py's own module namespace, so the import
must bind the name directly into main.py's globals. Do not change the import
style in main.py to a qualified `_win.foo()` form, or every test that mocks
one of these breaks.
"""
import ctypes

ES_CONTINUOUS       = 0x80000000
ES_SYSTEM_REQUIRED  = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# SendInput plumbing. SetCursorPos moves the pointer but Windows does NOT
# count it as user input, so it never resets the idle timer and cannot hold off
# an enforced lock. Measured on Win11: after SetCursorPos the idle timer stayed
# at 7125ms -> 7203ms; one SendInput dropped it to 78ms. Also note that
# SetLastInputInfo (the old approach here) is documented but not exported by
# user32 at all, so every _reset_idle() call used to raise AttributeError.
INPUT_MOUSE      = 0
MOUSEEVENTF_MOVE = 0x0001


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


def _send_move(dx: int, dy: int):
    """Inject a relative mouse move. dx=dy=0 is a no-op move that still
    registers as input, which is what actually resets the idle timer."""
    inp = _INPUT(type=INPUT_MOUSE,
                 mi=_MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, None))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _any_key_pressed() -> bool:
    """True if any key went down since the previous call.

    Polled rather than bound: the overlay is overrideredirect(True), so Windows
    never makes it the foreground window. While the user sits in another app,
    their keystrokes go there and Tk's <KeyPress> binding never fires.
    Range starts at 0x08 to skip the mouse buttons (0x01-0x06).
    """
    gaks = ctypes.windll.user32.GetAsyncKeyState
    return any(gaks(vk) & 0x0001 for vk in range(0x08, 0xFF))


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


def _idle_ms() -> int:
    """How long Windows thinks the machine has been idle.

    GetLastInputInfo does exist (unlike SetLastInputInfo) and is the value a
    standard enforced-lock policy watches. Logged around the jiggle so the user
    can confirm the injection landed: the overlay hides the cursor, so a moving
    pointer is impossible to observe while the screen is dark.
    """
    li = _LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return ctypes.windll.kernel32.GetTickCount() - li.dwTime


def _reset_idle():
    """Reset the Windows idle timer without moving the pointer."""
    _send_move(0, 0)


def _cursor_pos() -> tuple[int, int]:
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def _set_cursor_pos(x: int, y: int):
    """Absolute, cosmetic-only pointer placement -- does NOT reset the idle
    timer (see _send_move's docstring). Used only to snap the pointer back to
    its exact starting point after a jiggle circle; the circle itself must
    still go through _send_move."""
    ctypes.windll.user32.SetCursorPos(x, y)


_EXEC_STATE_FLAGS = {
    "allow":   ES_CONTINUOUS,
    "display": ES_CONTINUOUS | ES_DISPLAY_REQUIRED,
    "system":  ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED,
}


def _prevent_sleep(mode: str):
    """mode: "allow" releases any hold; "display" keeps the screen on;
    "system" keeps the whole machine (and display) from sleeping."""
    ctypes.windll.kernel32.SetThreadExecutionState(_EXEC_STATE_FLAGS[mode])


def _overlay_bounds() -> tuple[int, int, int, int]:
    """(x, y, w, h) spanning the full multi-monitor virtual desktop."""
    gsm = ctypes.windll.user32.GetSystemMetrics
    return (gsm(76), gsm(77), gsm(78), gsm(79))
