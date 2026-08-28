"""OS services the guard needs, behind one API.

`main.py` must never call `ctypes.windll` (or CoreGraphics) directly again — it
imports this package and gets whichever backend matches the host. Every symbol
below exists on both backends with the same signature and the same meaning, so
the app logic stays identical on Windows and macOS.

Why an explicit indirection rather than `if sys.platform` sprinkled inline:
the two OSes disagree about *which* call resets the input-idle timer, and each
has a call that looks like it should work and silently does not (see the
Idle timer notes in CLAUDE.md). Keeping that knowledge in one file per OS is
the only way to stop it leaking back into the app.

API
---
NAME                 "windows" | "macos"
data_dir()           writable per-user directory for the face model
keep_display_on()    ask the OS to keep the display powered (user present)
keep_system_on()     keep display *and* system awake (overlay is dark)
release_awake()      drop every keep-awake request
reset_idle()         reset the input-idle timer, pointer ends where it started
move_cursor(x, y)    absolute move that COUNTS as user input (jiggle step)
warp_cursor(x, y)    absolute move that does NOT count as input (snap-back)
cursor_pos()         -> (x, y)
idle_ms()            -> int, ms since the last real input
any_key_pressed()    -> bool, True on a key going down since the last call
virtual_screen()     -> (x, y, w, h) spanning every monitor
preflight()          -> [str] warnings to log on Start (missing permissions)
"""

import sys

if sys.platform == "win32":
    from ._win import *          # noqa: F401,F403
    from ._win import NAME       # noqa: F401
elif sys.platform == "darwin":
    from ._mac import *          # noqa: F401,F403
    from ._mac import NAME       # noqa: F401
else:
    raise ImportError(
        f"Screen Presence Guard supports Windows and macOS; got {sys.platform}"
    )
