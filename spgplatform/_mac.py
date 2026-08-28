"""macOS backend — CoreGraphics / IOKit via ctypes. No pyobjc dependency.

The API in spgplatform/__init__.py is written against Windows semantics; this
file is where the two OSes stop agreeing. What was measured on macOS 15:

| Call | Reality |
|---|---|
| `CGWarpMouseCursorPosition` | Moves the pointer, idle timer **unchanged** (42.44s -> 42.44s). Exactly the `SetCursorPos` trap, mirrored — useless for holding off a lock, perfect for the cosmetic snap-back. |
| `IOPMAssertionDeclareUserActivity` | Returns success and does **not** move the HID idle clock at all. It defers *display sleep*, not the screensaver/lock countdown. |
| `CGEventPost` of a mouseMoved to the **same** point | Silently coalesced away. Idle went 45.45s -> 45.58s, i.e. it kept counting. |
| `CGEventPost` of a mouseMoved that **displaces** the pointer | Actually resets it: 17.62s -> 0.19s, on all three counters (combined, HID-state, and ioreg HIDIdleTime). |

So `reset_idle()` cannot be invisible here the way `SendInput(0, 0)` is on
Windows: the event has to move the pointer or macOS drops it. It steps 1px and
warps straight back, and the warp does not undo the reset because warping is
not input. The overlay is built with `cursor="none"`, so nothing is visible.

Two permissions gate this backend, and both fail **silently** when missing —
`preflight()` exists so they are never diagnosed by guesswork:
  * **Accessibility** — without it `CGEventPost` is dropped, so the jiggle and
    the idle reset do nothing at all.
  * **Input Monitoring** — without it `CGEventSourceKeyState` always reports
    False, so keyboard wake never fires (mouse wake still works).
"""

import ctypes
import ctypes.util
import os

NAME = "macos"

_CG = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
_CF = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
_IOK = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
_AS = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("w", ctypes.c_double), ("h", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


kCGEventMouseMoved   = 5
kCGHIDEventTap       = 0
kCGCombinedSessionState = 0
kCGAnyInputEventType = 0xFFFFFFFF
kCFStringEncodingUTF8 = 0x08000100
kIOPMAssertionLevelOn = 255
kIOHIDRequestTypeListenEvent = 1

_CG.CGEventCreate.restype = ctypes.c_void_p
_CG.CGEventCreate.argtypes = [ctypes.c_void_p]
_CG.CGEventGetLocation.restype = _CGPoint
_CG.CGEventGetLocation.argtypes = [ctypes.c_void_p]
_CG.CGEventCreateMouseEvent.restype = ctypes.c_void_p
_CG.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                        _CGPoint, ctypes.c_uint32]
_CG.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_CG.CGWarpMouseCursorPosition.restype = ctypes.c_int32
_CG.CGWarpMouseCursorPosition.argtypes = [_CGPoint]
_CG.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
_CG.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_uint32,
                                                       ctypes.c_uint32]
_CG.CGEventSourceKeyState.restype = ctypes.c_bool
_CG.CGEventSourceKeyState.argtypes = [ctypes.c_uint32, ctypes.c_uint16]
_CG.CGGetActiveDisplayList.argtypes = [ctypes.c_uint32,
                                       ctypes.POINTER(ctypes.c_uint32),
                                       ctypes.POINTER(ctypes.c_uint32)]
_CG.CGDisplayBounds.restype = _CGRect
_CG.CGDisplayBounds.argtypes = [ctypes.c_uint32]
_CF.CFRelease.argtypes = [ctypes.c_void_p]
_CF.CFStringCreateWithCString.restype = ctypes.c_void_p
_CF.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                          ctypes.c_uint32]
_IOK.IOPMAssertionCreateWithName.restype = ctypes.c_int32
_IOK.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                             ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_uint32)]
_IOK.IOPMAssertionRelease.restype = ctypes.c_int32
_IOK.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
_AS.AXIsProcessTrusted.restype = ctypes.c_bool


def _cfstr(s: str):
    """CFString that is intentionally never released — module-lifetime constant."""
    return _CF.CFStringCreateWithCString(None, s.encode("utf-8"),
                                         kCFStringEncodingUTF8)


_NAME_CF    = _cfstr("Screen Presence Guard")
_DISPLAY_CF = _cfstr("PreventUserIdleDisplaySleep")
_SYSTEM_CF  = _cfstr("PreventUserIdleSystemSleep")

# Live power assertions, keyed by type. Windows folds this into one bitmask
# call; macOS hands out a separate id per assertion that must be released.
_assertions = {}


def data_dir() -> str:
    return os.path.expanduser(
        "~/Library/Application Support/ScreenPresenceGuard")


def _hold(key, type_cf):
    if key in _assertions:
        return
    aid = ctypes.c_uint32(0)
    rc = _IOK.IOPMAssertionCreateWithName(type_cf, kIOPMAssertionLevelOn,
                                          _NAME_CF, ctypes.byref(aid))
    if rc == 0:
        _assertions[key] = aid.value


def _drop(key):
    aid = _assertions.pop(key, None)
    if aid is not None:
        _IOK.IOPMAssertionRelease(aid)


def keep_display_on():
    _hold("display", _DISPLAY_CF)
    _drop("system")


def keep_system_on():
    _hold("display", _DISPLAY_CF)
    _hold("system", _SYSTEM_CF)


def release_awake():
    _drop("display")
    _drop("system")


def cursor_pos() -> tuple:
    ev = _CG.CGEventCreate(None)
    pt = _CG.CGEventGetLocation(ev)
    _CF.CFRelease(ev)
    return (int(pt.x), int(pt.y))


def move_cursor(x: int, y: int):
    """Injected move to an absolute point — counts as input if it displaces."""
    ev = _CG.CGEventCreateMouseEvent(None, kCGEventMouseMoved,
                                     _CGPoint(float(x), float(y)), 0)
    _CG.CGEventPost(kCGHIDEventTap, ev)
    _CF.CFRelease(ev)


def warp_cursor(x: int, y: int):
    _CG.CGWarpMouseCursorPosition(_CGPoint(float(x), float(y)))


def reset_idle():
    """Step 1px and warp home. The step is what macOS counts; the warp is not
    counted, so it restores the pointer without undoing the reset."""
    x, y = cursor_pos()
    move_cursor(x + 1, y)
    warp_cursor(x, y)


def idle_ms() -> int:
    return int(_CG.CGEventSourceSecondsSinceLastEventType(
        kCGCombinedSessionState, kCGAnyInputEventType) * 1000)


# CGEventSourceKeyState reports whether a key is down *right now*, with no
# "changed since last call" bit like GetAsyncKeyState's 0x0001. The rising
# edge is reconstructed here so callers see the same semantics on both OSes.
_key_prev = set()


def any_key_pressed() -> bool:
    global _key_prev
    down = {k for k in range(0x80)
            if _CG.CGEventSourceKeyState(kCGCombinedSessionState, k)}
    fresh = down - _key_prev
    _key_prev = down
    return bool(fresh)


def virtual_screen() -> tuple:
    """Bounding box over every active display, top-left origin — the same
    coordinate space cursor_pos() reports in."""
    count = ctypes.c_uint32()
    ids = (ctypes.c_uint32 * 16)()
    if _CG.CGGetActiveDisplayList(16, ids, ctypes.byref(count)) != 0 or not count.value:
        return (0, 0, 1920, 1080)
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for i in range(count.value):
        b = _CG.CGDisplayBounds(ids[i])
        x0, y0 = min(x0, b.origin.x), min(y0, b.origin.y)
        x1 = max(x1, b.origin.x + b.size.w)
        y1 = max(y1, b.origin.y + b.size.h)
    return (int(x0), int(y0), int(x1 - x0), int(y1 - y0))


def preflight() -> list:
    """Both grants fail silently, so they are reported before they are needed."""
    warn = []
    if not _AS.AXIsProcessTrusted():
        warn.append("macOS: ยังไม่ได้อนุญาต Accessibility — "
                    "หมุนเมาส์/กันล็อกจะไม่ทำงาน "
                    "(System Settings → Privacy & Security → Accessibility)")
    try:
        _IOK.IOHIDCheckAccess.restype = ctypes.c_uint32
        _IOK.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
        if _IOK.IOHIDCheckAccess(kIOHIDRequestTypeListenEvent) != 0:
            warn.append("macOS: ยังไม่ได้อนุญาต Input Monitoring — "
                        "คีย์บอร์ดจะปลุกจอไม่ได้ (เมาส์ยังใช้ได้) "
                        "(System Settings → Privacy & Security → Input Monitoring)")
    except AttributeError:
        pass
    return warn
