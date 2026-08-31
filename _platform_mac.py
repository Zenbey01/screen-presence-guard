"""macOS implementation of the platform-neutral primitives main.py needs.

**WRITTEN, NOT VERIFIED.** There is no Mac in the environment this was
written in. Every function here is implemented against Apple's documented
Quartz/CoreGraphics/IOKit APIs as precisely as could be researched, but
nothing in this file has been run. Before this backend is trusted, a human
on real Mac hardware must confirm every item in the checklist at the bottom
of this docstring. Do not remove this warning until that happens.

Same contract as _platform_win.py -- main.py imports these as bare names
(`from _platform_mac import _cursor_pos, ...`), which is what lets the
existing pytest suite's `monkeypatch.setattr(spg, "_cursor_pos", ...)` keep
working: the test suite never has to know or care which backend is loaded.

TCC (privacy permission) research done this session, with confidence levels:
  - Posting synthetic mouse events (`_send_move`) needs Accessibility
    (`kTCCServiceAccessibility`) -- HIGH confidence, corroborated by Apple's
    own CGPreflightPostEventAccess/CGRequestPostEventAccess docs and by
    several real open-source mac mouse-jiggler projects.
  - Polling key/idle state via CGEventSourceKeyState /
    CGEventSourceSecondsSinceLastEventType -- MEDIUM confidence that these
    are ungated (unlike CGEventTap, which definitely needs Input Monitoring).
    No authoritative source confirmed this either way. MUST be verified by
    hand; if wrong, the fix is to also request Input Monitoring.
  - Preventing sleep via IOPMAssertionCreateWithName needs NO permission at
    all -- HIGH confidence.
  - Screen Recording is NOT needed -- this app never captures screen pixels,
    only draws its own window and reads the webcam.
  - The camera needs the standard Camera TCC prompt via NSCameraUsageDescription
    in the packaged .app's Info.plist (Phase 4 concern, not this file).

Verification checklist for whoever has a Mac (see the project plan file for
the full list): Camera prompt appears and the feed works; Accessibility
prompt appears the first time a jiggle fires and motion is visible / idle
resets; whether _any_key_pressed() needs an Input Monitoring prompt; the
black overlay is genuinely borderless/always-on-top/cursor-hidden and spans
every monitor; keyboard/mouse wake still works while the overlay is up;
a jiggle actually holds off a real screensaver/lock.
"""
import ctypes
import ctypes.util

import Quartz

# ── mouse / idle-timer primitives (Quartz CoreGraphics event APIs) ──────────


def _cursor_pos() -> tuple[int, int]:
    pt = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (int(pt.x), int(pt.y))


def _send_move(dx: int, dy: int):
    """Inject a relative mouse move via CGEventPost. dx=dy=0 is a no-op move
    that still counts as real input for idle-timer purposes, matching the
    Windows SendInput(0,0) trick this mirrors.

    Requires Accessibility permission (System Settings > Privacy & Security >
    Accessibility). CGEventPost does not raise or return an error code when
    that permission is missing -- it just silently does nothing -- so this
    cannot detect a missing grant on its own. _start() checks
    AXIsProcessTrustedWithOptions once at startup and logs a warning instead.
    """
    x, y = _cursor_pos()
    event = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, (x + dx, y + dy),
        Quartz.kCGMouseButtonLeft)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def _set_cursor_pos(x: int, y: int):
    """Absolute, cosmetic-only pointer placement -- mirrors _platform_win's
    SetCursorPos snap-back. Used only to put the pointer back exactly where
    a jiggle circle started; the circle motion itself must still go through
    _send_move, which is the call that actually registers as input."""
    event = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, (x, y), Quartz.kCGMouseButtonLeft)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def _reset_idle():
    """Reset the idle timer without a visible pointer move."""
    _send_move(0, 0)


def _idle_ms() -> int:
    """How long macOS thinks the machine has been idle, in milliseconds.

    CGEventSourceSecondsSinceLastEventType returns seconds as a float; the
    Windows side returns whole milliseconds via GetLastInputInfo, so this
    matches that unit for the shared idle-logging code in main.py.
    """
    seconds = Quartz.CGEventSourceSecondsSinceLastEventType(
        Quartz.kCGEventSourceStateHIDSystemState, Quartz.kCGAnyInputEventType)
    return int(seconds * 1000)


# macOS keycodes are a fixed table (0-127), unlike Windows' contiguous
# 0x08-0xFE virtual-key range -- there is no single authoritative "highest
# keycode" constant to loop to, so this uses the conventional upper bound
# used by every reference table (kVK_* constants top out well under 128).
_MAC_KEYCODE_COUNT = 128


def _any_key_pressed() -> bool:
    """True if any key is currently held down.

    Unlike Windows' GetAsyncKeyState (edge-triggered: "went down since the
    last call", via its low bit), CGEventSourceKeyState is level-triggered
    ("is down right now"). A key held down reports True on every poll here
    instead of once. Harmless for how main.py uses this value (any touch at
    all counts as presence / a wake trigger), just a different signal shape
    than the Windows docstring describes -- do not port that docstring
    unchanged if it is ever revised.
    """
    return any(
        Quartz.CGEventSourceKeyState(
            Quartz.kCGEventSourceStateCombinedSessionState, code)
        for code in range(_MAC_KEYCODE_COUNT))


# ── multi-monitor overlay bounds ────────────────────────────────────────────

_MAX_DISPLAYS = 16


def _overlay_bounds() -> tuple[int, int, int, int]:
    """(x, y, w, h) spanning the union of every active display's bounds.

    Deliberately not Tk's `-fullscreen` (that only covers one display and
    switches to a dedicated Space on macOS, which conflicts with this app's
    always-on-top-but-still-alt-tab-able overlay model) and not a single
    NSScreen (misses additional monitors). This mirrors Windows'
    SM_XVIRTUALSCREEN/SM_YVIRTUALSCREEN/SM_CXVIRTUALSCREEN/SM_CYVIRTUALSCREEN
    semantics as closely as Quartz allows.
    """
    err, display_ids, _count = Quartz.CGGetActiveDisplayList(
        _MAX_DISPLAYS, None, None)
    if err != 0 or not display_ids:
        # Fall back to whatever the main display reports rather than crash;
        # a single-display bound is still a usable overlay.
        main_id = Quartz.CGMainDisplayID()
        display_ids = [main_id]

    rects = [Quartz.CGDisplayBounds(d) for d in display_ids]
    min_x = min(r.origin.x for r in rects)
    min_y = min(r.origin.y for r in rects)
    max_x = max(r.origin.x + r.size.width for r in rects)
    max_y = max(r.origin.y + r.size.height for r in rects)
    return (int(min_x), int(min_y), int(max_x - min_x), int(max_y - min_y))


# ── sleep prevention (IOKit power management, raw ctypes) ──────────────────
#
# Deliberately ctypes, not pyobjc: IOPMAssertionCreateWithName is a plain C
# ABI with no TCC/permission concerns (unlike the CGEvent APIs above), so
# this keeps the same ctypes-first idiom _platform_win.py already uses for
# its one plain-C-ABI surface, rather than mixing in a second binding style
# just for this. This is the same recipe used internally by the published
# `caffeine`/`wakepy` packages for preventing macOS sleep from Python.

_iokit = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/IOKit.framework/IOKit")
_corefoundation = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

_corefoundation.CFStringCreateWithCString.restype = ctypes.c_void_p
_corefoundation.CFStringCreateWithCString.argtypes = [
    ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
_CF_STRING_ENCODING_UTF8 = 0x08000100

_iokit.IOPMAssertionCreateWithName.argtypes = [
    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32)]
_iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]

_IOPM_ASSERTION_LEVEL_ON = 255
_ASSERTION_TYPE_NO_DISPLAY_SLEEP = "NoDisplaySleepAssertion"
_ASSERTION_TYPE_NO_IDLE_SLEEP = "PreventUserIdleSystemSleep"


def _cf_string(s: str) -> ctypes.c_void_p:
    return _corefoundation.CFStringCreateWithCString(
        None, s.encode("utf-8"), _CF_STRING_ENCODING_UTF8)


def _make_assertion(assertion_type: str, name: str) -> int:
    assertion_id = ctypes.c_uint32(0)
    _iokit.IOPMAssertionCreateWithName(
        _cf_string(assertion_type), _IOPM_ASSERTION_LEVEL_ON,
        _cf_string(f"ScreenPresenceGuard: {name}"),
        ctypes.byref(assertion_id))
    return assertion_id.value


# Holds whatever assertion(s) are currently active, keyed by type, so
# _prevent_sleep can release exactly what it previously created rather than
# leaking an assertion every time the mode changes.
_active_assertions: dict[str, int] = {}


def _release_all_assertions():
    for assertion_id in _active_assertions.values():
        _iokit.IOPMAssertionRelease(assertion_id)
    _active_assertions.clear()


def _prevent_sleep(mode: str):
    """mode: "allow" releases every hold; "display" keeps only the screen
    on; "system" keeps the whole machine (and display) from sleeping --
    mirrors _platform_win._prevent_sleep's three modes exactly, including
    "system" holding both the display and idle-system assertions
    simultaneously the way Windows holds ES_SYSTEM_REQUIRED|ES_DISPLAY_REQUIRED
    together in main.py's screen-dark branch.
    """
    _release_all_assertions()
    if mode == "allow":
        return
    _active_assertions["display"] = _make_assertion(
        _ASSERTION_TYPE_NO_DISPLAY_SLEEP, "display")
    if mode == "system":
        _active_assertions["system"] = _make_assertion(
            _ASSERTION_TYPE_NO_IDLE_SLEEP, "system")


# ── Accessibility permission check (used once, at _start()) ────────────────


def _accessibility_trusted() -> bool:
    """True if this process already has Accessibility permission granted.
    Does not prompt -- main.py decides whether/when to nudge the user."""
    return bool(Quartz.AXIsProcessTrustedWithOptions(None))
