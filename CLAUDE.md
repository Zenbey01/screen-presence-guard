# Screen Presence Guard — Codebase Reference

## What it does
Webcam-based face detection app that keeps the screen "on" when the user is present.
- No face detected for N seconds → black overlay (`_BlackOverlay`) covers screen — looks off, but the OS never locks
- Runs on **Windows and macOS** — every OS call goes through `spgplatform/` (see below)
- Face / mouse / keyboard detected → overlay disappears instantly, no password needed
- Optional mouse circle jiggle (two independent: screen-ON and screen-OFF) to prevent IT-enforced lock

## File structure
```
screen-presence-guard/
├── main.py                          # the app — GUI + logic, NO direct OS calls
├── spgplatform/                     # the only place ctypes lives
│   ├── __init__.py                  #   picks a backend by sys.platform
│   ├── _win.py                      #   Win32: user32 / kernel32
│   └── _mac.py                      #   CoreGraphics / IOKit (no pyobjc)
├── blaze_face_short_range.tflite    # MediaPipe face detector model (230KB, committed)
├── icon.ico                         # app icon, Windows (person in blue frame)
├── icon.icns                        # same icon, macOS bundle
├── make_icon.py                     # regenerate icon.ico
├── build.ps1                        # Windows build → dist/ + zip + installer bat
├── build.sh                         # macOS build  → ScreenPresenceGuard.app + zip
├── create_shortcut.ps1              # dev-machine launcher + Desktop shortcut (run once)
├── launch.vbs                       # windowless python launcher
├── Screen Presence Guard.lnk        # launcher shortcut in project folder
├── README.md                        # user-facing docs (Thai)
├── CLAUDE.md / AGENTS.md            # this file — keep the two IDENTICAL
├── requirements.txt                 # pip dependencies
├── face_model.yml                   # LBPH face model (gitignored)
├── face_imgs.pkl                    # training images pool (gitignored)
├── dist/ScreenPresenceGuard/        # PyInstaller build (gitignored)
├── ScreenPresenceGuard.zip           # ready-to-share archive, Windows (gitignored)
└── ScreenPresenceGuard-macOS.zip     # ready-to-share archive, macOS (gitignored)
```

## Key architecture

### Black overlay (no lock screen)
```python
class _BlackOverlay(tk.Toplevel):
    # fullscreen black window — screen LOOKS off, Windows sees it as on
    # binds <Motion>/<Button>/<KeyPress> → master._wake()
    # checks master._jiggling before waking (suppress jiggle-triggered events)
    # checks master.use_mouse / master.use_keyboard vars
```

### Detection pipeline
- **Preview**: Haar cascade (fast, display only, NOT for presence decisions)
- **Presence decision**: MediaPipe `FaceDetector` (Tasks API, `blaze_face_short_range.tflite`,
  `min_detection_confidence=0.5`) — robust, handles glasses
- **Identity**: LBPH recognizer (`cv2.face`) — user-specific, threshold 90.0
- Background thread every `interval` seconds → `_bg_check()` → `_handle_presence()`

```python
# module level, AFTER _DIR is defined (needs the model path)
MP_MODEL_FILE = os.path.join(_DIR, "blaze_face_short_range.tflite")
_HAS_MP, _MP_ERR   # _MP_ERR is logged by _start() — the init failure is NEVER silent
_detect_boxes()    # → [(x, y, w, h)] from det.bounding_box.origin_x/origin_y/width/height
```
**Do not** use the legacy `mp.solutions.face_detection` API — mediapipe >= 0.10.30
removed the whole `mp.solutions` namespace. Tasks API only.
Keyboard is a wake trigger and presence signal; `_handle_presence` sees face + mouse + keyboard.
The wake trigger and status label are picked in that same priority order
(face / mouse / keyboard) — keep the three branches in sync when adding a source.

### Opening the camera
`_start` goes through `_open_camera(log)`, never `cv2.VideoCapture(0)` directly.
It walks `_camera_backends()` (Windows: DirectShow then MSMF then default;
macOS: AVFoundation; Linux: V4L2) crossed with device index `0..CAM_MAX_INDEX`,
and accepts a capture only after a real `read()` returns a frame.

Both halves matter and both were real failures:
- **MSMF is the OpenCV default on Windows and it does not work everywhere.**
  It reports `isOpened() == False` on plenty of integrated webcams, or reports
  True and then never grabs. DirectShow opens the same camera fine.
- **Device 0 is not always the webcam** — a dock, a virtual cam (OBS, Teams
  background app) or a second USB camera pushes the built-in one to index 1/2.

The build is `--windowed`, so OpenCV's own stderr diagnosis goes nowhere. The
per-attempt `_log` lines are the only evidence a user can send back, and the
failure path prints the two causes worth checking by hand (the Windows camera
privacy toggles, and another app holding the device).

`_tick` must handle `cap.read()` returning `ok=False`. After `CAMERA_FAIL_SEC` of
dead frames it logs and calls `_stop()`: presence checking is impossible without
frames, and the previous silent no-op left the app looking alive while it had
stopped deciding anything. There is no auto-resume — the user presses Start again.

### Face registration
Storage lives in `_DATA_DIR`: the repo dir in dev, `plat.data_dir()` when frozen
(`%LOCALAPPDATA%\ScreenPresenceGuard` / `~/Library/Application Support/ScreenPresenceGuard`),
because a bundle under Program Files — or a signed `.app` — cannot write to itself.
`_finish_register` does `os.makedirs(_DATA_DIR, exist_ok=True)` inside its try.

- 40 sample frames (`REG_SAMPLES`) → LBPH train → saves `face_model.yml` + `face_imgs.pkl`
- Re-registering ADDS to existing pool (multiple sessions = more diversity)
- If no model: any face counts as "present"
- Cancellable: `reg_btn` toggles to cancel while `_reg_mode` — `_start_register` →
  `_cancel_register`. `_bg_register` / `_finish_register` both bail if `_reg_mode` is False
  (a queued thread must not resurrect a cancelled run). `_reset_reg_btn()` restores the button.
- `_finish_register` wraps train+write in try/except/finally: the recognizer is adopted
  only after both files land on disk, and the `finally` always frees `_reg_mode` and
  resets the button, so a write failure cannot leave it stuck on "cancel".
- `_load_face_data` records `_face_load_error` instead of `except: pass`; `_start()`
  logs it. A corrupt model falls all the way back to any-face rather than leaving
  identity filtering on with a sample count of 0.

### Mouse circle jiggle (two independent systems)
```python
# Screen-ON jiggle: prevents IT lock while screen is on
jiggle_on     = BooleanVar
jiggle_on_sec = IntVar(60)
# _sched_on() → _do_jiggle_on() → thread: _work_on() → _circle_move()

# Screen-OFF jiggle: keeps Windows from locking while overlay is dark
jiggle_off     = BooleanVar
jiggle_off_sec = IntVar(60)
# _sched_off() → _do_jiggle_off() → thread: _work_off() → _circle_move()

# _sched() is shared and getattr-based: attr names are passed as STRINGS,
# so grepping for `jiggle_off_sec` will not show the read site.

def _circle_move(x, y, radius=10, steps=12):
    # moves cursor in 12-point circle via _send_move (SendInput), snaps home
    # with SetCursorPos at the end; runs in daemon thread — does NOT block main

# _jiggling flag: prevents overlay from waking when jiggle moves mouse
```
Neither worker touches `last_seen`. `_work_on` used to set it, which meant any
ON-jiggle interval shorter than the dim timeout reset the countdown forever and
the screen never dimmed at all. The ON-jiggle range caps at 300s while the
timeout reaches 900s, so that line could not be made safe — it is gone.
Both cards are built in the settings tab (`_circle_card(..., "on")` and
`(..., "off")`); `which` must be exactly `"on"` / `"off"` — `_on_jiggle_toggle`
treats anything else as "off".

### Wake triggers (user-configurable)
- `use_mouse`: mouse movement wakes overlay — via the `<Motion>` binding **and**
  `_cursor_pos()` polling in `_handle_presence`
- `use_keyboard`: keypress wakes overlay — via `_any_key_pressed()` **polling**
  (`_poll_keyboard`, every `KEY_POLL_MS`, only while `screen_off`)
- `_jiggling` flag bypasses all wake checks during jiggle

The overlay is `overrideredirect(True)`, so Windows never makes it the foreground
window. `focus_set()` gives it Tk-internal focus only: while the user sits in
another app their keystrokes go **there**, and `<KeyPress>` never fires. So the
keyboard must be polled with `GetAsyncKeyState`, never bound. Do not "fix" this
by calling `SetForegroundWindow` — that would steal focus and eat keystrokes the
user meant for their own app.

### Screen state
```python
self.screen_off = False   # True when overlay is shown

def _sleep():         # show overlay, update all status widgets
def _wake(trigger):   # destroy overlay, update widgets (guard: if not screen_off: return)
# _stop() and _close() also reset screen_off = False after destroying the overlay
```

### Close / Stop
- `_close()`: sets `running=False`, destroys overlay, resets `screen_off`,
  releases camera + stops tray in **daemon thread** (non-blocking), destroys window after 200ms
- `_stop()`: same cleanup but keeps window open; also cancels both jiggle timers

### The platform shim — `spgplatform/`
`main.py` must never import `ctypes` or call an OS API directly. It does
`import spgplatform as plat` and gets whichever backend matches the host; both
export the same names with the same meaning:

```python
plat.NAME               # "windows" | "macos"
plat.data_dir()         # writable per-user dir for the face model
plat.keep_display_on()  # user present            → display stays powered
plat.keep_system_on()   # overlay dark            → display + system stay awake
plat.release_awake()    # drop every keep-awake request
plat.reset_idle()       # reset the input-idle timer, pointer ends where it started
plat.move_cursor(x, y)  # absolute move that COUNTS as input (jiggle step)
plat.warp_cursor(x, y)  # absolute move that does NOT count (cosmetic snap-back)
plat.cursor_pos()       # -> (x, y)
plat.idle_ms()          # -> ms since the last real input
plat.any_key_pressed()  # -> True on a key going down since the last call
plat.virtual_screen()   # -> (x, y, w, h) spanning every monitor
plat.preflight()        # -> [str] missing-permission warnings, logged by _start()
```

`main.py` keeps four module-level aliases (`_reset_idle`, `_cursor_pos`,
`_idle_ms`, `_any_key_pressed`) so the call sites read as before.

**Idle timer — read this before touching the jiggle.** Each OS has a call that
looks like the right one and silently is not. Every row below was measured, not
read off a doc page:

| OS | Call | Reality |
|---|---|---|
| Win11 | `SetLastInputInfo` | **Not exported by user32 at all.** Documented on MSDN, absent in practice — the old `_reset_idle()` raised `AttributeError` on every single call for months. |
| Win11 | `SetCursorPos` | Moves the pointer, Windows does **not** count it as input. Idle went 7125ms → 7203ms. Cannot hold off a lock. |
| Win11 | `SendInput` | Actually resets it: 7203ms → 78ms. A `(0, 0)` relative move is a visual no-op that still registers — what `reset_idle()` uses. |
| macOS 15 | `CGWarpMouseCursorPosition` | Moves the pointer, idle **unchanged** (42.44s → 42.44s). The `SetCursorPos` trap, mirrored. Perfect for the snap-back. |
| macOS 15 | `IOPMAssertionDeclareUserActivity` | Returns success and does **not** move the HID idle clock. Defers *display sleep*, not the lock countdown. |
| macOS 15 | `CGEventPost` at the **same** point | Silently coalesced away — idle kept counting, 45.45s → 45.58s. |
| macOS 15 | `CGEventPost` that **displaces** the pointer | Actually resets it: 17.62s → 0.19s, on all three counters (combined, HID-state, `ioreg` HIDIdleTime). |

Consequence: `reset_idle()` cannot be invisible on macOS the way `SendInput(0,0)`
is on Windows. `_mac.reset_idle()` steps 1px and warps straight back; the warp
does not undo the reset because warping is not input.

`_INPUT`'s union must be sized for `_MOUSEINPUT` (`sizeof(_INPUT) == 40` on x64).
A union holding only `KEYBDINPUT` yields 32 bytes and `SendInput` silently
returns **0** without injecting anything.

`_idle_ms()` is logged around every jiggle and on the dark heartbeat:
`idle before 34s -> idle after 0ms`. Keep it. The overlay is created with
`cursor="none"`, so a moving pointer cannot be observed while the screen is
dark — the log is the only way a user can tell the jiggle fired at all, and the
idle pair is the only proof the OS accepted it.

**macOS permissions fail silently, so `preflight()` exists.** Without
**Accessibility**, `CGEventPost` is dropped and the jiggle plus the idle reset
do nothing at all. Without **Input Monitoring**, `CGEventSourceKeyState` always
reports False and keyboard wake never fires (mouse wake still works). Neither
raises; `_start()` logs `plat.preflight()` so it is never diagnosed by guesswork.

`CGEventSourceKeyState` is a *level* read with no "changed since last call" bit
like `GetAsyncKeyState`'s `0x0001`. `_mac.any_key_pressed()` reconstructs the
rising edge against a stored set so both OSes have identical semantics.

## Color palette (dark navy/blue)
```python
C_BG      = "#090c15"   # outer bg / window
C_PANEL   = "#0c0f1a"   # inner panel
C_BAR     = "#0e1220"   # title / status bar
C_CAM     = "#080b12"   # camera area
C_CARD    = "#141928"   # chips / cards
C_BORDER  = "#1e2840"   # borders
C_ACCENT  = "#3b82f6"   # blue (action)
C_ON      = "#22c55e"   # green (face on)
C_WARN    = "#f59e0b"   # amber (countdown)
C_OFF     = "#ef4444"   # red (screen off)
C_TEXT1/2/3, C_DIM, C_VDIM, C_BTN_RUN
FONT = "Segoe UI"; FONT_MONO = "Consolas"
```

Sliders: `ดับจอหลังจาก` is 5s..900s (15 min) with `number_of_steps=179`, i.e. a
5s grid — `(900-5)/179 == 5.0` exactly. Both the value readout and the range end
labels go through `_fmt_sec` (`5s` under a minute, `15:00` above), as does the
countdown chip; a bare second count is unreadable at a 15-minute timeout.

## Layout (960×610, not resizable)
```
┌───────────────────────────────────────────────────────┐
│ status strip (h=72): dot + label + sub | Screen badge │  _build_status_strip()
├────────────────────────────────┬──────────────────────┤
│ camera preview (CTkLabel)      │ tab bar (3 tabs)     │  _build_body()
│ 3 stat chips                   │ ┌──────────────────┐ │   ├ _build_left()
│ Start / Minimize to tray       │ │ settings/faces/  │ │   └ _build_right()
│                                │ │ log frame        │ │
└────────────────────────────────┴──────────────────────┘
```
Right panel is tabbed — `_tab_frames` / `_tab_btns` dicts, switched by `_switch_tab(key)`
using `grid()` / `grid_remove()`. Keys: `"settings"`, `"faces"`, `"log"`.

## UI widget references (update in _handle_presence / _sleep / _wake / _start / _stop)
```python
self.status_dot, self.status_label, self.status_sub   # status strip
self.screen_badge_dot, self.screen_badge_lbl         # Screen ON/OFF badge
self.face_stat, self.screen_stat, self.time_stat     # 3 chips (via _chip())
self.cam_label                                       # camera preview
self.start_btn, self.reg_btn, self.face_count_lbl, self.log_box
```

## Dependencies
```
opencv-contrib-python   # cv2.face (LBPH) + cv2.data.haarcascades
mediapipe>=0.10.0       # tasks.python.vision.FaceDetector
customtkinter           # dark-theme GUI
Pillow                  # image processing
pystray                 # system tray icon
```
`numpy` is used directly but comes in transitively via opencv/mediapipe.

## Distribution
Two bundles, one Release. `.github/workflows/build.yml` runs a `windows` job and
a `macos` job on every `demo-*` / `v*` tag; each verifies MediaPipe loads,
verifies `spgplatform` imports on that OS, builds, and uploads its asset. Both
jobs create the Release if it is missing and tolerate losing that race.

### Windows — `.\build.ps1`
- Build: `.\build.ps1` → `dist/ScreenPresenceGuard/` → `ScreenPresenceGuard.zip`
- `build.ps1` must `--add-data` **both** `icon.ico` and `blaze_face_short_range.tflite`;
  they land in `_internal/`, which is what `_DIR` resolves to when frozen
- The generated installer bat points `IconLocation` at the **exe**
  (icon.ico is inside `_internal/`, not next to the exe)
- `build.ps1` verifies the exe plus `_internal/icon.ico` and
  `_internal/blaze_face_short_range.tflite` exist and exits 1 if any is missing,
  so a bundle can never ship without the face model again
- Recipients: extract zip → run the installer bat → double-click Desktop shortcut

### macOS — `./build.sh`
- Build: `./build.sh` → `dist/ScreenPresenceGuard.app` → `ScreenPresenceGuard-macOS.zip`
- `--add-data` separator is **`:`**, not the `;` Windows wants
- Info.plist **must** carry `NSCameraUsageDescription` — without it macOS denies
  the camera *silently*, which looks exactly like the MSMF bug above
- Packed with `ditto -c -k --keepParent`, never `zip`: plain zip drops the
  symlinks inside `Contents/Frameworks` and the bundle will not launch
- Signed **ad-hoc** (`codesign -s -`), so Gatekeeper still needs right-click →
  Open the first time, and the signature changes every build, which makes macOS
  re-ask for camera / accessibility after each new download
- `_DIR` resolves to `Contents/Frameworks` when frozen (PyInstaller `sys._MEIPASS`)

No Python required on either target machine (PyInstaller --onedir bundle).

## Known issues / gotchas
- `main.py` is saved with a **UTF-8 BOM** — read it as `utf-8-sig` when scripting edits
  (plain `utf-8` + `ast.parse` fails on U+FEFF)
- `time.sleep()` must NOT run on main tkinter thread → always use daemon thread for jiggle
- `_jiggle_on_id` / `_jiggle_off_id`: cancel before re-scheduling to prevent duplicate loops
- `_wake()` has guard `if not self.screen_off: return` — prevents double-call
- `_tick()` rebuilds a `CTkImage` every 33 ms; do not add more per-frame allocations
- `_log` trims the textbox to `MAX_LOG_LINES`; it settles one line over the cap,
  which is intended — the point is the bound, not an exact count
- face_model.yml / face_imgs.pkl are personal — exclude from shared distribution
- Frozen builds store face_model.yml / face_imgs.pkl in `plat.data_dir()`
- Desktop shortcut: recreate via `create_shortcut.ps1` if Python path changes
- Never swallow a dependency-init exception silently (see `_MP_ERR`) — a dead MediaPipe
  fell back to Haar unnoticed for two months
- `_work_on` / `_work_off` call `self.after()` from a **worker thread**. That is not
  documented as safe, and it only works because the main thread sits in `mainloop()`.
  Tests that drive the app with `update()` in a loop instead will see the worker die
  on `RuntimeError: main thread is not in main loop` — a test artifact, not a bug.
- Settings tab lives in a `CTkScrollableFrame`; adding rows just extends the scroll
- Anything new that touches the OS goes in `spgplatform/`, **both** backends, or
  the app breaks on the platform you did not test
- macOS overlay covers the display Tk put the window on, not the whole virtual
  screen — `plat.virtual_screen()` reports the union correctly, but Tk/Aqua will
  not span it. Multi-monitor macOS leaves the other screens lit; unresolved.
- The Homebrew `cv2` build has no `cv2.data` submodule, which `main.py` reads at
  import. The pip `opencv-contrib-python` wheel the app ships does have it —
  only a dev-machine trap.
