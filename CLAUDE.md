# Screen Presence Guard — Codebase Reference

## What it does
Webcam-based face detection app that keeps the screen "on" when the user is present.
- No face detected for N seconds → black overlay (`_BlackOverlay`) covers screen — looks off, but Windows never locks
- Face / mouse / keyboard detected → overlay disappears instantly, no password needed
- Optional mouse circle jiggle (two independent: screen-ON and screen-OFF) to prevent IT-enforced lock

## File structure
```
screen-presence-guard/
├── main.py                    # entire app (~950 lines)
├── icon.ico                   # app icon (person in blue frame)
├── make_icon.py               # regenerate icon.ico
├── create_shortcut.ps1        # creates launcher in folder + Desktop shortcut (run once)
├── Screen Presence Guard.lnk  # launcher shortcut in project folder
├── requirements.txt           # pip dependencies
├── face_model.yml             # LBPH face model (gitignore this)
├── face_imgs.pkl              # training images pool (gitignore this)
├── dist/ScreenPresenceGuard/  # PyInstaller build for distribution
│   ├── ScreenPresenceGuard.exe
│   └── ติดตั้ง shortcut.bat
└── ScreenPresenceGuard.zip    # ready-to-share archive
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
- **Presence decision**: MediaPipe FaceDetection (model_selection=0) — robust, handles glasses
- **Identity**: LBPH recognizer (`cv2.face`) — user-specific, threshold 90.0
- Background thread every `interval` seconds → `_bg_check()` → `_handle_presence()`

### Face registration
- 40 sample frames → LBPH train → saves `face_model.yml` + `face_imgs.pkl`
- Re-registering ADDS to existing pool (multiple sessions = more diversity)
- If no model: any face counts as "present"

### Mouse circle jiggle (two independent systems)
```python
# Screen-ON jiggle: prevents IT lock while screen is on
jiggle_on     = BooleanVar   # toggle
jiggle_on_sec = IntVar(60)   # interval seconds
# _sched_on() → _do_jiggle_on() → thread: _work_on() → _circle_move()

# Screen-OFF jiggle: keeps Windows from locking while overlay is dark
jiggle_off     = BooleanVar
jiggle_off_sec = IntVar(60)
# _sched_off() → _do_jiggle_off() → thread: _work_off() → _circle_move()

def _circle_move(x, y, radius=10, steps=12):
    # moves cursor in 12-point circle, returns to origin
    # runs in daemon thread — does NOT block main thread

# _jiggling flag: prevents overlay from waking when jiggle moves mouse
```

### Wake triggers (user-configurable)
- `use_mouse`: mouse movement wakes overlay
- `use_keyboard`: keypress wakes overlay
- Both checked in `_BlackOverlay` bindings AND `_handle_presence`
- `_jiggling` flag bypasses all wake checks during jiggle

### Screen state
```python
self.screen_off = False   # True when overlay is shown

def _sleep():   # show overlay, update all status widgets
def _wake(trigger):  # destroy overlay, update widgets (guard: if not screen_off: return)
```

### Close / Stop
- `_close()`: sets `running=False`, cancels jiggle timers, destroys overlay,
  releases camera + stops tray in **daemon thread** (non-blocking), destroys window after 200ms
- `_stop()`: same cleanup but keeps window open

### Windows API calls
```python
ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED  # prevent sleep
SetLastInputInfo()   # reset Windows idle timer
GetSystemMetrics(76/77/78/79)  # virtual screen coords for multi-monitor overlay
SetCursorPos(x, y)  # move mouse (jiggle)
```

## Color palette (dark navy/blue)
```python
C_BG      = "#0d1117"   # main background
C_SIDEBAR = "#161d2e"   # sidebar / right panel
C_CARD    = "#1c2237"   # cards
C_BANNER  = "#1e3a8a"   # top banner
C_ACCENT  = "#4361ee"   # blue accent
C_ON      = "#3fb950"   # green (face detected)
C_WARN    = "#f9a825"   # yellow (countdown)
C_OFF     = "#f85149"   # red (screen off / no face)
```

## Layout (960×610, 3-column)
```
[sidebar 60px] | [main area] | [right panel 284px]
sidebar: icon, status dot, close button
main: banner header, camera preview (CTkLabel+CTkImage), 3 stat cards, Start/Tray buttons
right: Face Registration, Settings (sliders + toggles), Log textbox
```

## UI widget references (update in _handle_presence / _sleep / _wake)
```python
self.banner_title, self.banner_sub, self.banner_badge
self.sidebar_dot
self.dot, self.status_lbl, self.screen_lbl
self.face_stat, self.screen_stat, self.time_stat
self.cam_label
```

## Dependencies
```
opencv-contrib-python   # cv2.face (LBPH) + cv2.data.haarcascades
mediapipe               # FaceDetection
customtkinter           # dark-theme GUI
Pillow                  # image processing
pystray                 # system tray icon
```

## Distribution
- Build: `.\build.ps1` → `dist/ScreenPresenceGuard/` → `ScreenPresenceGuard.zip`
- Recipients: extract zip → run `ติดตั้ง shortcut.bat` → double-click Desktop shortcut
- No Python required on target machine (PyInstaller --onedir bundle)

## Known issues / gotchas
- `time.sleep()` must NOT run on main tkinter thread → always use daemon thread for jiggle
- `_jiggle_on_id` / `_jiggle_off_id`: cancel before re-scheduling to prevent duplicate loops
- `_wake()` has guard `if not self.screen_off: return` — prevents double-call
- face_model.yml / face_imgs.pkl are personal — exclude from shared distribution
- Desktop shortcut: recreate via `create_shortcut.ps1` if Python path changes
