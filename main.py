#!/usr/bin/env python3
"""Screen Presence Guard — keeps screen on when you're here, dark when you leave."""

import cv2
import ctypes
import math
import tkinter as tk
import time
import threading
import os
import pickle
import sys
import numpy as np
import customtkinter as ctk
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item

_HAS_LBPH = hasattr(cv2, "face")

_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

ES_CONTINUOUS       = 0x80000000
ES_SYSTEM_REQUIRED  = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_DIR            = os.path.dirname(os.path.abspath(__file__))
FACE_SIZE       = (100, 100)
LBPH_THRESHOLD  = 90.0
REG_SAMPLES     = 40
KEY_POLL_MS     = 120
CAMERA_FAIL_SEC = 5
MAX_LOG_LINES   = 1000
MP_MODEL_FILE   = os.path.join(_DIR, "blaze_face_short_range.tflite")

# Frozen bundles may be installed in read-only locations such as Program Files.
_DATA_DIR = (os.path.join(os.environ.get("LOCALAPPDATA", _DIR),
                          "ScreenPresenceGuard")
             if getattr(sys, "frozen", False) else _DIR)
FACE_MODEL_FILE = os.path.join(_DATA_DIR, "face_model.yml")
FACE_IMGS_FILE  = os.path.join(_DATA_DIR, "face_imgs.pkl")

# MediaPipe drives every presence decision. Haar cascade below is preview-only
# fallback: too many false negatives (glasses, tilted head) to dim the screen on.
try:
    import mediapipe as mp
    from mediapipe.tasks.python import vision as _mp_vision
    from mediapipe.tasks.python.core.base_options import BaseOptions as _MPBaseOptions

    _mp_detect = _mp_vision.FaceDetector.create_from_options(
        _mp_vision.FaceDetectorOptions(
            base_options=_MPBaseOptions(model_asset_path=MP_MODEL_FILE),
            running_mode=_mp_vision.RunningMode.IMAGE,
            min_detection_confidence=0.5,
        )
    )
    _mp_lock = threading.Lock()
    _HAS_MP  = True
    _MP_ERR  = ""
except Exception as e:
    _HAS_MP = False
    _MP_ERR = f"{type(e).__name__}: {e}"   # surfaced in _start(), never silent

# ── Color palette (dark navy / blue accent) ───────────────────────────────────
C_BG      = "#090c15"   # outer bg / window
C_PANEL   = "#0c0f1a"   # inner panel
C_BAR     = "#0e1220"   # title/status bar
C_CAM     = "#080b12"   # camera area
C_CARD    = "#141928"   # chips / cards
C_BORDER  = "#1e2840"   # borders
C_ACCENT  = "#3b82f6"   # blue (action)
C_ON      = "#22c55e"   # green (face on)
C_WARN    = "#f59e0b"   # amber (countdown)
C_OFF     = "#ef4444"   # red (screen off)
C_TEXT1   = "#e1e7f5"   # main text
C_TEXT2   = "#b0bbcc"   # secondary
C_TEXT3   = "#8a96b0"   # muted
C_DIM     = "#566083"   # dim
C_VDIM    = "#3a4460"   # very dim
C_BTN_RUN = "#1e2a1e"   # stop button bg
FONT      = "Segoe UI"
FONT_MONO = "Consolas"


def _fmt_sec(v) -> str:
    """5 -> 5s, 900 -> 15:00. The dim timeout spans seconds to a quarter hour,
    so a bare second count stops being readable past a minute."""
    v = int(v)
    return f"{v}s" if v < 60 else f"{v // 60}:{v % 60:02d}"


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


def _detect_boxes(frame) -> list:
    if _HAS_MP:
        with _mp_lock:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = _mp_detect.detect(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        return [(max(0, d.bounding_box.origin_x), max(0, d.bounding_box.origin_y),
                 d.bounding_box.width, d.bounding_box.height)
                for d in res.detections]
    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = _cascade.detectMultiScale(gray, 1.1, 5, minSize=(80, 80))
    return faces.tolist() if len(faces) > 0 else []


class _BlackOverlay(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master, bg="black", cursor="none")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        x = ctypes.windll.user32.GetSystemMetrics(76)
        y = ctypes.windll.user32.GetSystemMetrics(77)
        w = ctypes.windll.user32.GetSystemMetrics(78)
        h = ctypes.windll.user32.GetSystemMetrics(79)
        self.geometry(f"{w}x{h}+{x}+{y}")
        def wake(t, var=None):
            if master._jiggling:
                return
            if var is None or var.get():
                master.after(0, lambda: master._wake(t))
        self.bind("<Motion>",   lambda e: wake("เมาส์",     master.use_mouse))
        self.bind("<Button>",   lambda e: wake("คลิก",      master.use_mouse))
        self.bind("<KeyPress>", lambda e: wake("คีย์บอร์ด", master.use_keyboard))
        self.focus_set()


def _tray_icon() -> Image.Image:
    img  = Image.new("RGB", (64, 64), "#68217a")
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 20, 56, 44], fill="#4361ee")
    draw.ellipse([22, 25, 42, 39], fill="#68217a")
    draw.ellipse([28, 29, 36, 35], fill="#60a5fa")
    return img


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Screen Presence Guard")
        self.geometry("960x610")
        self.resizable(False, False)
        self.configure(fg_color=C_BG)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        _ico = os.path.join(_DIR, "icon.ico")
        if os.path.exists(_ico):
            self.iconbitmap(_ico)

        self.running     = False
        self.screen_off  = False
        self.last_seen   = 0.0
        self._last_check = 0.0
        self._heartbeat  = 0
        self._mouse_pos  = (-1, -1)
        self._overlay    = None
        self.cap         = None
        self.tray        = None
        self._bg_busy    = False
        self._camera_failed_since = None
        self._reg_mode   = False
        self._reg_buffer = []
        self._recognizer = None
        self._known_count = 0
        self._face_load_error = ""

        self.timeout          = ctk.IntVar(value=30)
        self.interval         = ctk.DoubleVar(value=2.0)
        self.use_mouse        = ctk.BooleanVar(value=True)
        self.use_keyboard     = ctk.BooleanVar(value=True)
        self.jiggle_on        = ctk.BooleanVar(value=False)  # circle while screen ON
        self.jiggle_on_sec    = ctk.IntVar(value=60)
        self.jiggle_off       = ctk.BooleanVar(value=False)  # circle while screen OFF
        self.jiggle_off_sec   = ctk.IntVar(value=60)
        self._jiggling        = False
        self._jiggle_on_id    = None
        self._jiggle_off_id   = None
        self._key_poll_id     = None

        self._load_face_data()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ── Face data ─────────────────────────────────────────────────────────────

    def _load_face_data(self):
        if not _HAS_LBPH:
            return
        if os.path.exists(FACE_MODEL_FILE) and os.path.exists(FACE_IMGS_FILE):
            try:
                rec = cv2.face.LBPHFaceRecognizer_create()
                rec.read(FACE_MODEL_FILE)
                with open(FACE_IMGS_FILE, "rb") as f:
                    imgs, _ = pickle.load(f)
                self._recognizer = rec
                self._known_count = len(imgs)
            except Exception as e:
                self._recognizer = None
                self._known_count = 0
                self._face_load_error = f"{type(e).__name__}: {e}"

    def _face_status_text(self) -> str:
        if not _HAS_LBPH:
            return "MediaPipe detection เท่านั้น (ไม่มี opencv-contrib)"
        if self._known_count == 0:
            return "ยังไม่ได้ลงทะเบียน → จับได้ทุกหน้า"
        return f"ลงทะเบียนแล้ว {self._known_count} ตัวอย่าง"

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._tab_frames = {}
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build_status_strip()
        self._build_body()

    # ── Status strip ──────────────────────────────────────────────────────────

    def _build_status_strip(self):
        strip = ctk.CTkFrame(self, fg_color=C_BAR, corner_radius=0, height=72)
        strip.grid(row=0, column=0, sticky="ew")
        strip.grid_propagate(False)
        strip.grid_columnconfigure(1, weight=1)

        # Left: dot + labels
        self.status_dot = ctk.CTkLabel(strip, text="⬤",
                                        font=ctk.CTkFont(size=11),
                                        text_color=C_DIM, width=22)
        self.status_dot.grid(row=0, column=0, rowspan=2, padx=(20, 10), sticky="ns",
                              pady=16)

        self.status_label = ctk.CTkLabel(
            strip, text="ยังไม่เริ่มทำงาน",
            font=ctk.CTkFont(family=FONT, size=18, weight="bold"),
            text_color=C_DIM, anchor="w"
        )
        self.status_label.grid(row=0, column=1, sticky="sw", pady=(14, 1))

        self.status_sub = ctk.CTkLabel(
            strip, text="กดปุ่ม Start เพื่อเริ่มตรวจจับใบหน้า",
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=C_VDIM, anchor="w"
        )
        self.status_sub.grid(row=1, column=1, sticky="nw", pady=(1, 14))

        # Right: screen badge
        badge = ctk.CTkFrame(strip, fg_color=C_CARD, corner_radius=20,
                              border_width=1, border_color=C_BORDER)
        badge.grid(row=0, column=2, rowspan=2, padx=(10, 20), sticky="ns",
                   pady=20)
        badge.grid_columnconfigure(1, weight=1)
        self.screen_badge_dot = ctk.CTkLabel(badge, text="⬤",
                                              font=ctk.CTkFont(size=8),
                                              text_color=C_VDIM)
        self.screen_badge_dot.grid(row=0, column=0, padx=(12, 5))
        self.screen_badge_lbl = ctk.CTkLabel(badge, text="Screen OFF",
                                              font=ctk.CTkFont(family=FONT, size=12),
                                              text_color=C_DIM)
        self.screen_badge_lbl.grid(row=0, column=1, padx=(0, 14))

    # ── Body (left + right) ───────────────────────────────────────────────────

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        self._build_left(body)
        # vertical divider
        ctk.CTkFrame(body, width=1, fg_color=C_BORDER, corner_radius=0).grid(
            row=0, column=1, sticky="ns", padx=0, pady=16)
        self._build_right(body)

    def _build_left(self, parent):
        left = ctk.CTkFrame(parent, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # Camera card
        cam_card = ctk.CTkFrame(left, fg_color=C_CAM, corner_radius=10,
                                  border_width=1, border_color=C_BORDER)
        cam_card.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        cam_card.grid_rowconfigure(0, weight=1)
        cam_card.grid_columnconfigure(0, weight=1)

        self.cam_label = ctk.CTkLabel(
            cam_card, text="Camera Feed",
            fg_color="transparent", corner_radius=8,
            text_color=C_BORDER, font=ctk.CTkFont(family=FONT, size=11,
                                                    weight="bold"),
        )
        self.cam_label.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")

        # Stat chips
        chips = ctk.CTkFrame(left, fg_color="transparent")
        chips.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        chips.grid_columnconfigure((0, 1, 2), weight=1)

        self.face_stat   = self._chip(chips, 0, "Face Detection")
        self.screen_stat = self._chip(chips, 1, "Display State")
        self.time_stat   = self._chip(chips, 2, "นับถอยหลัง")

        # Buttons
        btns = ctk.CTkFrame(left, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="ew")
        btns.grid_columnconfigure(0, weight=2)
        btns.grid_columnconfigure(1, weight=1)

        self.start_btn = ctk.CTkButton(
            btns, text="▶  Start", height=44,
            font=ctk.CTkFont(family=FONT, size=15, weight="bold"),
            corner_radius=8, fg_color="#1a2540",
            border_width=1, border_color="#2d4878",
            hover_color="#1e2d4d", text_color=C_ACCENT,
            command=self._toggle
        )
        self.start_btn.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        ctk.CTkButton(
            btns, text="⊡  Tray", height=44,
            font=ctk.CTkFont(family=FONT, size=14),
            corner_radius=8, fg_color=C_CARD,
            border_width=1, border_color=C_BORDER, text_color=C_DIM,
            hover_color="#1a2035", command=self._to_tray
        ).grid(row=0, column=1, sticky="ew")

    def _chip(self, parent, col, label):
        card = ctk.CTkFrame(parent, corner_radius=8, fg_color=C_CARD,
                             border_width=1, border_color=C_BORDER)
        card.grid(row=0, column=col, padx=(0, 10) if col < 2 else 0, sticky="ew")
        ctk.CTkLabel(card, text=label,
                      font=ctk.CTkFont(family=FONT, size=9),
                      text_color=C_DIM, anchor="center").pack(pady=(13, 7))
        val = ctk.CTkLabel(card, text="—",
                            font=ctk.CTkFont(family=FONT, size=15, weight="bold"),
                            text_color=C_DIM, anchor="center")
        val.pack(pady=(0, 13))
        return val

    # ── Right panel (tabbed) ──────────────────────────────────────────────────

    def _build_right(self, parent):
        rp = ctk.CTkFrame(parent, fg_color="transparent", width=290)
        rp.grid(row=0, column=2, sticky="nsew", padx=(0, 0))
        rp.grid_propagate(False)
        rp.grid_rowconfigure(1, weight=1)
        rp.grid_columnconfigure(0, weight=1)

        # Tab bar
        tab_bar = ctk.CTkFrame(rp, fg_color="transparent", height=44)
        tab_bar.grid(row=0, column=0, sticky="ew")
        tab_bar.grid_propagate(False)
        ctk.CTkFrame(rp, height=1, fg_color=C_BORDER, corner_radius=0).grid(
            row=0, column=0, sticky="sew")

        self._tab_btns = {}
        for i, (key, lbl) in enumerate([("settings", "ตั้งค่า"),
                                          ("faces",    "ใบหน้า"),
                                          ("log",      "บันทึก")]):
            b = ctk.CTkButton(
                tab_bar, text=lbl, width=94, height=44,
                corner_radius=0, font=ctk.CTkFont(family=FONT, size=13),
                fg_color="transparent", hover_color=C_CARD,
                text_color=C_ACCENT if key == "settings" else C_DIM,
                command=lambda k=key: self._switch_tab(k)
            )
            b.grid(row=0, column=i, sticky="nsew")
            self._tab_btns[key] = b

        # Tab content area
        content = ctk.CTkScrollableFrame(rp, fg_color="transparent",
                                          scrollbar_button_color=C_BORDER,
                                          scrollbar_button_hover_color=C_DIM)
        content.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        content.grid_columnconfigure(0, weight=1)

        # ── SETTINGS tab ──────────────────────────────────────────────────────
        sf = ctk.CTkFrame(content, fg_color="transparent")
        sf.grid(row=0, column=0, sticky="ew")
        sf.grid_columnconfigure(0, weight=1)
        self._tab_frames["settings"] = sf

        # 5s .. 15min in 5s steps: (900-5)/179 == 5.0 exactly
        self._add_slider_v(sf, 0, "ดับจอหลังจาก", self.timeout,  5, 900,
                           _fmt_sec, steps=179)
        self._add_slider_v(sf, 1, "ตรวจสอบทุก",   self.interval, 1, 10,
                           lambda v: f"{v:.1f}s")

        ctk.CTkFrame(sf, height=1, fg_color=C_BORDER).grid(
            row=2, column=0, sticky="ew", pady=(4, 14))

        # ปลุกจอด้วย
        ctk.CTkLabel(sf, text="ปลุกจอด้วย",
                     font=ctk.CTkFont(family=FONT, size=14),
                     text_color=C_TEXT2, anchor="w").grid(
            row=3, column=0, sticky="w", pady=(0, 16))

        self._toggle_row(sf, 4, "เมาส์",    "ขยับเมาส์เพื่อปลุกจอ",    self.use_mouse)
        self._toggle_row(sf, 5, "คีย์บอร์ด", "กดแป้นใดก็ได้เพื่อปลุก", self.use_keyboard)

        ctk.CTkFrame(sf, height=1, fg_color=C_BORDER).grid(
            row=6, column=0, sticky="ew", pady=(14, 14))

        # ขยับเมาส์วิ่งกลม
        hdr = ctk.CTkFrame(sf, fg_color="transparent")
        hdr.grid(row=7, column=0, sticky="ew", pady=(0, 4))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="ขยับเมาส์วิ่งกลม",
                     font=ctk.CTkFont(family=FONT, size=14),
                     text_color=C_TEXT2, anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(sf, text="ป้องกันจอดับด้วยการขยับเมาส์เป็นวงกลมอัตโนมัติ",
                     font=ctk.CTkFont(family=FONT, size=11),
                     text_color=C_VDIM, anchor="w").grid(
            row=8, column=0, sticky="w", pady=(0, 14))

        # จอเปิด card
        self._circle_card(sf, 9, "จอเปิด", C_ON, self.jiggle_on,
                          self.jiggle_on_sec, "on")

        # จอดับ card
        self._circle_card(sf, 10, "จอดับ", C_OFF, self.jiggle_off,
                          self.jiggle_off_sec, "off")

        # ── FACES tab ──────────────────────────────────────────────────────────
        ff = ctk.CTkFrame(content, fg_color="transparent")
        ff.grid(row=0, column=0, sticky="ew")
        ff.grid_columnconfigure(0, weight=1)
        ff.grid_remove()
        self._tab_frames["faces"] = ff

        # Face count card
        fc_card = ctk.CTkFrame(ff, fg_color=C_CARD, corner_radius=10,
                                border_width=1, border_color=C_BORDER)
        fc_card.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        fc_card.grid_columnconfigure(1, weight=1)

        icon_wrap = ctk.CTkFrame(fc_card, width=46, height=46, corner_radius=23,
                                  fg_color="#0d1e38")
        icon_wrap.grid(row=0, column=0, padx=(18, 14), pady=18)
        icon_wrap.grid_propagate(False)
        ctk.CTkLabel(icon_wrap, text="👤", font=ctk.CTkFont(size=20)).place(
            relx=0.5, rely=0.5, anchor="center")

        fc_txt = ctk.CTkFrame(fc_card, fg_color="transparent")
        fc_txt.grid(row=0, column=1, sticky="w")
        self.face_count_lbl = ctk.CTkLabel(
            fc_txt, text=str(self._known_count),
            font=ctk.CTkFont(family=FONT, size=28, weight="bold"),
            text_color=C_TEXT1
        )
        self.face_count_lbl.pack(anchor="w")
        ctk.CTkLabel(fc_txt, text="ตัวอย่างที่ลงทะเบียน",
                      font=ctk.CTkFont(family=FONT, size=12),
                      text_color=C_DIM).pack(anchor="w")

        self.reg_btn = ctk.CTkButton(
            ff, text="+ ลงทะเบียนใบหน้าใหม่", height=44,
            font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
            corner_radius=8, fg_color="#1a2540",
            border_width=1, border_color="#2d4878",
            hover_color="#1e2d4d", text_color=C_ACCENT,
            command=self._start_register,
            state="normal" if _HAS_LBPH else "disabled",
        )
        self.reg_btn.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        ctk.CTkButton(
            ff, text="× ล้างข้อมูลทั้งหมด", height=40,
            font=ctk.CTkFont(family=FONT, size=13),
            corner_radius=8, fg_color="transparent",
            border_width=1, border_color="#3d1515",
            hover_color="#1a0a0a", text_color="#6b2020",
            command=self._clear_face_data,
        ).grid(row=2, column=0, sticky="ew")

        # ── LOG tab ────────────────────────────────────────────────────────────
        lf = ctk.CTkFrame(content, fg_color="transparent")
        lf.grid(row=0, column=0, sticky="nsew")
        lf.grid_columnconfigure(0, weight=1)
        lf.grid_rowconfigure(0, weight=1)
        lf.grid_remove()
        self._tab_frames["log"] = lf

        self.log_box = ctk.CTkTextbox(
            lf, fg_color=C_CARD, corner_radius=8, border_width=0,
            font=ctk.CTkFont(family=FONT_MONO, size=10),
            text_color=C_DIM
        )
        self.log_box.grid(row=0, column=0, sticky="nsew")
        self.log_box.configure(state="disabled")

    def _switch_tab(self, key: str):
        for k, frame in self._tab_frames.items():
            if k == key:
                frame.grid()
            else:
                frame.grid_remove()
        for k, btn in self._tab_btns.items():
            btn.configure(text_color=C_ACCENT if k == key else C_DIM)

    def _add_slider_v(self, parent, row, label, var, lo, hi, fmt, steps=None):
        """Vertical slider block matching prototype."""
        blk = ctk.CTkFrame(parent, fg_color="transparent")
        blk.grid(row=row, column=0, sticky="ew", pady=(0, 22))
        blk.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(blk, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=label,
                      font=ctk.CTkFont(family=FONT, size=14),
                      text_color=C_TEXT2).pack(side="left")
        val = ctk.CTkLabel(top, text=fmt(var.get()),
                            font=ctk.CTkFont(family=FONT, size=17, weight="bold"),
                            text_color=C_ACCENT)
        val.pack(side="right")
        ctk.CTkSlider(blk, from_=lo, to=hi, variable=var, number_of_steps=steps,
                       fg_color=C_CARD, progress_color=C_ACCENT,
                       button_color=C_ACCENT, button_hover_color="#60a5fa",
                       height=14).pack(fill="x", pady=(10, 4))
        rng = ctk.CTkFrame(blk, fg_color="transparent")
        rng.pack(fill="x")
        ctk.CTkLabel(rng, text=fmt(lo),
                      font=ctk.CTkFont(family=FONT, size=11),
                      text_color=C_VDIM).pack(side="left")
        ctk.CTkLabel(rng, text=fmt(hi),
                      font=ctk.CTkFont(family=FONT, size=11),
                      text_color=C_VDIM).pack(side="right")
        var.trace_add("write", lambda *_: val.configure(text=fmt(var.get())))

    def _toggle_row(self, parent, row, title, subtitle, var):
        """Toggle row with label + subtitle + switch."""
        row_f = ctk.CTkFrame(parent, fg_color="transparent")
        row_f.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        row_f.grid_columnconfigure(0, weight=1)

        txt = ctk.CTkFrame(row_f, fg_color="transparent")
        txt.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(txt, text=title,
                      font=ctk.CTkFont(family=FONT, size=14),
                      text_color=C_TEXT3, anchor="w").pack(anchor="w")
        ctk.CTkLabel(txt, text=subtitle,
                      font=ctk.CTkFont(family=FONT, size=11),
                      text_color=C_VDIM, anchor="w").pack(anchor="w")

        sw = ctk.CTkSwitch(row_f, text="", variable=var,
                            fg_color=C_CARD, progress_color=C_ACCENT,
                            button_color="#dde5f5", button_hover_color="#ffffff",
                            width=40, height=20)
        sw.pack(side="right")

    def _circle_card(self, parent, row, title, dot_color, var, delay_var, which):
        """Compact card for circle jiggle setting."""
        card = ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=8,
                             border_width=1, border_color=C_BORDER)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 0))
        hdr.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(hdr, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text="⬤",
                      font=ctk.CTkFont(size=8), text_color=dot_color).pack(
            side="left", padx=(0, 6))
        ctk.CTkLabel(left, text=title,
                      font=ctk.CTkFont(family=FONT, size=13),
                      text_color=C_TEXT3).pack(side="left")
        ctk.CTkLabel(left, text="— หลังจาก",
                      font=ctk.CTkFont(family=FONT, size=11),
                      text_color=C_VDIM).pack(side="left", padx=(6, 0))

        right = ctk.CTkFrame(hdr, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        val_lbl = ctk.CTkLabel(right, text=f"{delay_var.get()}s",
                                font=ctk.CTkFont(family=FONT, size=15, weight="bold"),
                                text_color=C_ACCENT)
        val_lbl.pack(side="left", padx=(0, 8))
        delay_var.trace_add("write",
            lambda *_: val_lbl.configure(text=f"{delay_var.get()}s"))
        ctk.CTkSwitch(right, text="", variable=var,
                       fg_color=C_BORDER, progress_color=C_ACCENT,
                       button_color="#dde5f5", button_hover_color="#ffffff",
                       width=40, height=20,
                       command=lambda: self._on_jiggle_toggle(which)).pack(side="left")

        sl = ctk.CTkSlider(card, from_=10, to=300, variable=delay_var,
                            fg_color=C_PANEL, progress_color=C_ACCENT,
                            button_color=C_ACCENT, button_hover_color="#60a5fa",
                            height=12)
        sl.grid(row=1, column=0, sticky="ew", padx=12, pady=(10, 12))

    def _add_slider(self, parent, row, label, var, lo, hi, fmt):
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(family=FONT, size=11),
                     text_color="#ddd0e0", anchor="w").grid(
            row=row, column=0, padx=(0, 8), pady=4, sticky="w")
        ctk.CTkSlider(parent, from_=lo, to=hi, variable=var,
                      fg_color="#e0d0e8", progress_color=C_ACCENT,
                      button_color="#c8d0e0", button_hover_color="#ffffff",
                      height=14).grid(
            row=row, column=1, padx=(0, 6), pady=4, sticky="ew")
        val = ctk.CTkLabel(parent, text=fmt(var.get()), width=32,
                            font=ctk.CTkFont(family=FONT, size=11, weight="bold"),
                            text_color=C_ACCENT, anchor="e")
        val.grid(row=row, column=2)
        var.trace_add("write", lambda *_: val.configure(text=fmt(var.get())))

    def _make_switch(self, parent, text, var, command=None, pack_side="left",
                     padx=(0, 0), grid=None):
        sw = ctk.CTkSwitch(
            parent, text=text, variable=var,
            font=ctk.CTkFont(family=FONT, size=11), text_color="#ddd0e0",
            fg_color="#e0d0e8", progress_color=C_ACCENT,
            button_color="#c8d0e0", button_hover_color="#ffffff",
            width=40, height=20, command=command,
        )
        if grid:
            sw.grid(row=grid[0], column=grid[1], sticky="w")
        else:
            sw.pack(side=pack_side, padx=padx)
        return sw

    def _inline_slider(self, parent, var, lo, hi, fmt, col):
        sl = ctk.CTkSlider(parent, from_=lo, to=hi, variable=var,
                           fg_color="#e0d0e8", progress_color=C_ACCENT,
                           button_color="#c8d0e0", button_hover_color="#ffffff",
                           height=12)
        sl.grid(row=0, column=col, padx=(0, 4), sticky="ew")
        val = ctk.CTkLabel(parent, text=fmt(var.get()), width=30,
                           font=ctk.CTkFont(family=FONT, size=10, weight="bold"),
                           text_color=C_ACCENT)
        val.grid(row=0, column=col + 1)
        var.trace_add("write", lambda *_: val.configure(text=fmt(var.get())))

    # ── Face registration ─────────────────────────────────────────────────────

    def _start_register(self):
        if not self.running:
            self._log("กด Start ก่อน แล้วค่อยลงทะเบียน")
            return
        if self._reg_mode:
            self._cancel_register()
            return
        self._reg_mode   = True
        self._reg_buffer = []
        tip = " (เพิ่มตัวอย่างเดิม)" if self._known_count else ""
        self._log(f"ลงทะเบียนหน้า{tip}... หันหน้าตรง ขยับเล็กน้อย")
        self.reg_btn.configure(text="×  ยกเลิกการลงทะเบียน", text_color=C_WARN)

    def _cancel_register(self):
        self._reg_mode   = False
        self._reg_buffer = []
        self._reset_reg_btn()
        self._log("ยกเลิกการลงทะเบียน")

    def _reset_reg_btn(self):
        self.reg_btn.configure(text="+ ลงทะเบียนใบหน้าใหม่",
                                state="normal" if _HAS_LBPH else "disabled",
                                text_color=C_ACCENT)

    def _bg_register(self, frame):
        try:
            if not self._reg_mode:      # cancelled while this thread was queued
                return
            boxes = _detect_boxes(frame)
            if boxes:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                x, y, w, h = boxes[0]
                roi = gray[y:y+h, x:x+w]
                if roi.size > 0:
                    roi = cv2.resize(roi, FACE_SIZE)
                    self._reg_buffer.append(roi)
                    count = len(self._reg_buffer)
                    self.after(0, lambda c=count: self._log(
                        f"  · {c}/{REG_SAMPLES}"))
                    if count >= REG_SAMPLES:
                        self.after(0, self._finish_register)
        except Exception as e:
            self.after(0, lambda err=e: self._log(f"[warn] register: {err}"))
        finally:
            self._bg_busy = False

    def _finish_register(self):
        if not self._reg_mode:
            return
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            existing_imgs, existing_labels = [], []
            if os.path.exists(FACE_IMGS_FILE):
                with open(FACE_IMGS_FILE, "rb") as f:
                    existing_imgs, existing_labels = pickle.load(f)
            all_imgs   = existing_imgs + self._reg_buffer
            all_labels = existing_labels + [0] * len(self._reg_buffer)
            rec = cv2.face.LBPHFaceRecognizer_create()
            rec.train(all_imgs, np.array(all_labels, dtype=np.int32))
            rec.write(FACE_MODEL_FILE)
            with open(FACE_IMGS_FILE, "wb") as f:
                pickle.dump((all_imgs, all_labels), f)
            self._recognizer = rec
            self._known_count = len(all_imgs)
            self._log(f"ลงทะเบียนสำเร็จ! รวม {self._known_count} ตัวอย่าง")
        except Exception as e:
            self._log(f"ERROR: บันทึกข้อมูลหน้าไม่สำเร็จ — {type(e).__name__}: {e}")
        finally:
            self._reg_mode   = False
            self._reg_buffer = []
            self._reset_reg_btn()
            self.face_count_lbl.configure(text=str(self._known_count))

    def _clear_face_data(self):
        self._recognizer  = None
        self._known_count = 0
        for f in (FACE_MODEL_FILE, FACE_IMGS_FILE):
            if os.path.exists(f):
                os.remove(f)
        self.face_count_lbl.configure(text="0")
        self._log("ล้างข้อมูลหน้าแล้ว")

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def _toggle(self):
        (self._stop if self.running else self._start)()

    def _start(self):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            cap.release()
            self.cap = None
            self._log("ERROR: เปิดกล้องไม่ได้")
            return
        self.cap = cap
        self.running     = True
        self.screen_off  = False
        self.last_seen   = time.time()
        self._last_check = 0.0
        self._heartbeat  = 0
        self._camera_failed_since = None
        self._bg_busy    = False
        self._reg_mode   = False
        self._mouse_pos  = _cursor_pos()
        self.start_btn.configure(
            text="■  Stop", fg_color=C_BTN_RUN,
            border_color="#2d5e2d", hover_color="#243324", text_color=C_ON
        )
        det = "MediaPipe" if _HAS_MP else "Haar (fallback)"
        if not _HAS_MP:
            self._log(f"[warn] MediaPipe ใช้ไม่ได้ → {_MP_ERR}")
            self._log("[warn] ใช้ Haar แทน — ความแม่นยำต่ำกว่า (แว่น/หันหน้า)")
        rec = f"LBPH ({self._known_count} ตย.)" if self._recognizer else "any-face"
        if self._face_load_error:
            self._log(f"[warn] โหลด face model ไม่สำเร็จ → {self._face_load_error}")
        self.status_dot.configure(text_color=C_ON)
        self.status_label.configure(text="ตรวจพบใบหน้า", text_color=C_ON)
        self.status_sub.configure(text=f"กำลังตรวจจับ — {det} · {rec}")
        self.screen_badge_dot.configure(text_color=C_ON)
        self.screen_badge_lbl.configure(text="Screen ON", text_color=C_ON)
        self._log(f"เริ่มทำงาน — {det} + {rec}")
        if self.jiggle_on.get():
            self._sched_on()
        if self.jiggle_off.get():
            self._sched_off()
        self._tick()

    def _on_jiggle_toggle(self, which: str):
        if which == "on":
            state = self.jiggle_on.get()
            self._log(f"[jiggle-ON] toggle → {'เปิด' if state else 'ปิด'} | running={self.running}")
            self._sched_on() if state else self._cancel_jiggle_on()
        else:
            state = self.jiggle_off.get()
            self._log(f"[jiggle-OFF] toggle → {'เปิด' if state else 'ปิด'} | running={self.running}")
            self._sched_off() if state else self._cancel_jiggle_off()

    def _cancel_jiggle_on(self):
        if self._jiggle_on_id:
            self.after_cancel(self._jiggle_on_id)
            self._jiggle_on_id = None

    def _cancel_jiggle_off(self):
        if self._jiggle_off_id:
            self.after_cancel(self._jiggle_off_id)
            self._jiggle_off_id = None

    def _stop(self):
        self.running   = False
        self._camera_failed_since = None
        self._reg_mode = False
        self._cancel_jiggle_on()
        self._cancel_jiggle_off()
        self._cancel_key_poll()
        if self._overlay:
            self._overlay.destroy()
            self._overlay = None
        self.screen_off = False
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        if self.cap:
            self.cap.release()
            self.cap = None
        self.start_btn.configure(
            text="▶  Start", fg_color="#1a2540",
            border_color="#2d4878", hover_color="#1e2d4d", text_color=C_ACCENT
        )
        self.status_dot.configure(text_color=C_DIM)
        self.status_label.configure(text="ยังไม่เริ่มทำงาน", text_color=C_DIM)
        self.status_sub.configure(text="กดปุ่ม Start เพื่อเริ่มตรวจจับใบหน้า")
        self.face_stat.configure(text="—", text_color=C_DIM)
        self.screen_stat.configure(text="—", text_color=C_DIM)
        self.time_stat.configure(text="—", text_color=C_DIM)
        self.cam_label.configure(image=None, text="Camera Feed")
        self.screen_badge_dot.configure(text_color=C_VDIM)
        self.screen_badge_lbl.configure(text="Screen OFF", text_color=C_DIM)
        self._reset_reg_btn()
        self._log("หยุดทำงาน")

    # ── Tick ──────────────────────────────────────────────────────────────────

    def _tick(self):
        if not self.running or not self.cap:
            return
        try:
            ok, frame = self.cap.read()
            if ok:
                gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                haar  = _cascade.detectMultiScale(gray, 1.1, 5, minSize=(80, 80))
                prev  = frame.copy()
                for (x, y, w, h) in haar:
                    cv2.rectangle(prev, (x, y), (x + w, y + h), (67, 97, 238), 2)
                img = ctk.CTkImage(
                    Image.fromarray(cv2.cvtColor(prev, cv2.COLOR_BGR2RGB)),
                    size=(self.cam_label.winfo_width() or 580,
                          self.cam_label.winfo_height() or 310),
                )
                self.cam_label.configure(image=img, text="")
                self.cam_label.image = img

                self._camera_failed_since = None
                now = time.time()
                if now - self._last_check >= self.interval.get() and not self._bg_busy:
                    self._last_check = now
                    self._bg_busy    = True
                    tgt = self._bg_register if self._reg_mode else self._bg_check
                    threading.Thread(target=tgt, args=(frame.copy(),), daemon=True).start()
            else:
                if self._camera_failed_since is None:
                    self._camera_failed_since = time.time()
                elif time.time() - self._camera_failed_since >= CAMERA_FAIL_SEC:
                    self._log(f"ERROR: กล้องไม่ส่งภาพต่อเนื่องเกิน {CAMERA_FAIL_SEC} วินาที")
                    self._stop()
        except Exception as e:
            self._log(f"[warn] tick: {e}")
        self.after(33, self._tick)

    def _bg_check(self, frame):
        try:
            face_detected = self._check_my_face(frame)
            self.after(0, lambda: self._handle_presence(face_detected))
        except Exception as e:
            self.after(0, lambda err=e: self._log(f"[warn] check: {err}"))
        finally:
            self._bg_busy = False

    def _check_my_face(self, frame) -> bool:
        boxes = _detect_boxes(frame)
        if not boxes:
            return False
        if self._recognizer is None:
            return True
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for (x, y, w, h) in boxes:
            roi = gray[y:y+h, x:x+w]
            if roi.size == 0:
                continue
            roi = cv2.resize(roi, FACE_SIZE)
            _, conf = self._recognizer.predict(roi)
            if conf < LBPH_THRESHOLD:
                return True
        return False

    def _handle_presence(self, face_detected: bool):
        pos = _cursor_pos()
        mouse_moved = self.use_mouse.get() and not self._jiggling and (pos != self._mouse_pos)
        key_hit = self.use_keyboard.get() and _any_key_pressed()
        if pos != self._mouse_pos:
            self._mouse_pos = pos   # always track pos, only trigger if enabled
        user_present = face_detected or mouse_moved or key_hit

        if user_present:
            self.last_seen = time.time()
            if self.screen_off:
                self._wake("ใบหน้า" if face_detected else
                           "เมาส์"  if mouse_moved   else "คีย์บอร์ด")
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
            label = ("พบใบหน้า"        if face_detected else
                     "พบการเคลื่อนไหว" if mouse_moved   else "พบการพิมพ์")
            self.status_dot.configure(text_color=C_ON)
            self.status_label.configure(text=label, text_color=C_ON)
            self.status_sub.configure(text="กำลังตรวจจับ — จอเปิดอยู่")
            self.face_stat.configure(text="ตรวจพบ", text_color=C_ON)
            self.screen_stat.configure(text="จอเปิด", text_color=C_ON)
            self.time_stat.configure(text="—", text_color=C_DIM)
        else:
            absent    = time.time() - self.last_seen
            remaining = self.timeout.get() - absent
            if remaining > 0:
                self.status_dot.configure(text_color=C_WARN)
                self.status_label.configure(text="ไม่พบใบหน้า", text_color=C_WARN)
                self.status_sub.configure(text=f"ดับจอใน {_fmt_sec(remaining)}")
                self.face_stat.configure(text="ไม่พบ", text_color=C_OFF)
                self.time_stat.configure(text=_fmt_sec(remaining), text_color=C_WARN)
            elif not self.screen_off:
                self._sleep()
            else:
                _reset_idle()
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
                self._heartbeat += 1
                ticks = max(1, int(60 / self.interval.get()))
                if self._heartbeat % ticks == 0:
                    mins = (self._heartbeat * int(self.interval.get())) // 60
                    self._log(f"โปรแกรมทำงาน — จอมืดมา {mins} นาที | idle {_idle_ms()//1000}s")

    # ── Screen control ────────────────────────────────────────────────────────

    def _sleep(self):
        self.screen_off = True
        self._heartbeat = 0
        self._overlay   = _BlackOverlay(self)
        self.status_dot.configure(text_color=C_OFF)
        self.status_label.configure(text="จอดับแล้ว", text_color=C_OFF)
        self.status_sub.configure(text="จอถูกดับ — รอตรวจพบใบหน้า")
        self.face_stat.configure(text="ไม่พบ", text_color=C_OFF)
        self.screen_stat.configure(text="จอดับ", text_color=C_OFF)
        self.time_stat.configure(text="0s", text_color=C_OFF)
        self.screen_badge_dot.configure(text_color=C_OFF)
        self.screen_badge_lbl.configure(text="Screen OFF", text_color=C_OFF)
        _any_key_pressed()          # drain keys pressed before the overlay went up
        self._poll_keyboard()
        self._log("จอมืด — กล้องยังทำงาน ไม่มี lock")

    def _poll_keyboard(self):
        """Wake on a keypress while the overlay is up. No _jiggling guard is
        needed: the jiggle injects mouse moves, never keys."""
        self._key_poll_id = None
        if not self.screen_off:
            return
        if self.use_keyboard.get() and _any_key_pressed():
            self._wake("คีย์บอร์ด")
            return
        self._key_poll_id = self.after(KEY_POLL_MS, self._poll_keyboard)

    def _cancel_key_poll(self):
        if self._key_poll_id:
            self.after_cancel(self._key_poll_id)
            self._key_poll_id = None

    # ── Mouse circle jiggle ───────────────────────────────────────────────────

    def _circle_move(self, x, y, radius=10, steps=12):
        """Walk the cursor around a small circle and back — background thread.

        Every step goes through SendInput, not SetCursorPos, so the movement
        registers as real input and holds off an enforced lock. Pointer
        acceleration makes relative steps land imprecisely, hence the final
        SetCursorPos to put the pointer back exactly where it started.
        """
        cx, cy = x, y
        for i in range(steps + 1):
            a  = 2 * math.pi * i / steps
            tx = x + int(radius * math.cos(a))
            ty = y + int(radius * math.sin(a))
            _send_move(tx - cx, ty - cy)
            cx, cy = tx, ty
            time.sleep(0.03)
        ctypes.windll.user32.SetCursorPos(x, y)
        time.sleep(0.03)

    def _sched(self, attr_id, attr_var, attr_sec, callback, label):
        aid = getattr(self, attr_id)
        if aid:
            self.after_cancel(aid)
            setattr(self, attr_id, None)
        var_val  = getattr(self, attr_var).get()
        if not self.running or not var_val:
            self._log(f"[jiggle-{label}] sched ยกเลิก: running={self.running} var={var_val}")
            return
        ms = max(1000, getattr(self, attr_sec).get() * 1000)
        self._log(f"[jiggle-{label}] จะขยับใน {ms//1000}s")
        setattr(self, attr_id, self.after(ms, callback))

    # ── Screen-ON jiggle ──────────────────────────────────────────────────────

    def _sched_on(self):
        self._sched("_jiggle_on_id", "jiggle_on", "jiggle_on_sec",
                    self._do_jiggle_on, "ON")

    def _do_jiggle_on(self):
        self._jiggle_on_id = None
        self._log(f"[jiggle-ON] fired | screen_off={self.screen_off} running={self.running} var={self.jiggle_on.get()}")
        if not self.running or not self.jiggle_on.get():
            return
        if not self.screen_off:
            self._log("[jiggle-ON] เริ่ม thread วงกลม")
            threading.Thread(target=self._work_on, daemon=True).start()
        else:
            self._log("[jiggle-ON] ข้าม — จอดำอยู่")
        self._sched_on()

    def _work_on(self):
        self._jiggling = True
        try:
            x, y = _cursor_pos()
            idle_before = _idle_ms()
            self.after(0, lambda px=x, py=y, ib=idle_before:
                       self._log(f"[jiggle-ON] วงกลมจาก ({px},{py}) | idle ก่อน {ib//1000}s"))
            self._circle_move(x, y)
            self._mouse_pos = (x, y)
            _reset_idle()
            idle_after = _idle_ms()
            self.after(0, lambda px=x, py=y, ia=idle_after:
                       self._log(f"[jiggle-ON] ✓ เสร็จ ({px},{py}) | idle หลัง {ia}ms"))
        except Exception as e:
            self.after(0, lambda err=e: self._log(f"[jiggle-ON] ERROR: {err}"))
        finally:
            self._jiggling = False

    # ── Screen-OFF jiggle ─────────────────────────────────────────────────────

    def _sched_off(self):
        self._sched("_jiggle_off_id", "jiggle_off", "jiggle_off_sec",
                    self._do_jiggle_off, "OFF")

    def _do_jiggle_off(self):
        self._jiggle_off_id = None
        self._log(f"[jiggle-OFF] fired | screen_off={self.screen_off} running={self.running} var={self.jiggle_off.get()}")
        if not self.running or not self.jiggle_off.get():
            return
        if self.screen_off:
            self._log("[jiggle-OFF] เริ่ม thread วงกลม")
            threading.Thread(target=self._work_off, daemon=True).start()
        else:
            self._log("[jiggle-OFF] ข้าม — จอยังเปิด")
        self._sched_off()

    def _work_off(self):
        self._jiggling = True
        try:
            x, y = _cursor_pos()
            idle_before = _idle_ms()
            self.after(0, lambda px=x, py=y, ib=idle_before:
                       self._log(f"[jiggle-OFF] วงกลมจาก ({px},{py}) | idle ก่อน {ib//1000}s"))
            self._circle_move(x, y)
            self._mouse_pos = (x, y)
            _reset_idle()
            idle_after = _idle_ms()
            self.after(0, lambda px=x, py=y, ia=idle_after:
                       self._log(f"[jiggle-OFF] ✓ เสร็จ ({px},{py}) | idle หลัง {ia}ms"))
        except Exception as e:
            self.after(0, lambda err=e: self._log(f"[jiggle-OFF] ERROR: {err}"))
        finally:
            self._jiggling = False

    def _wake(self, trigger: str = ""):
        if not self.screen_off:
            return
        self._cancel_key_poll()
        if self._overlay:
            self._overlay.destroy()
            self._overlay = None
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
        self._mouse_pos = _cursor_pos()
        self.screen_off = False
        self._heartbeat = 0
        self.screen_badge_dot.configure(text_color=C_ON)
        self.screen_badge_lbl.configure(text="Screen ON", text_color=C_ON)
        self._log(f"เปิดจอ (trigger: {trigger})")

    # ── Tray ──────────────────────────────────────────────────────────────────

    def _to_tray(self):
        self.withdraw()
        menu = (
            item("แสดงหน้าต่าง", self._show, default=True),
            item("ออก",          self._quit),
        )
        self.tray = pystray.Icon("presence_guard", _tray_icon(),
                                  "Screen Presence Guard", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _show(self, *_):
        if self.tray:
            self.tray.stop()
            self.tray = None
        self.after(0, self.deiconify)

    def _quit(self, *_):
        self.after(0, self._close)

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        line_count = int(self.log_box.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            self.log_box.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _close(self):
        # Stop app state immediately (fast, no blocking)
        self.running   = False
        self._reg_mode = False
        self._cancel_key_poll()
        if self._overlay:
            try:
                self._overlay.destroy()
            except Exception:
                pass
            self._overlay = None
        self.screen_off = False
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

        # Release camera + tray in background so UI doesn't freeze
        cap  = self.cap
        tray = self.tray
        self.cap  = None
        self.tray = None

        def _cleanup():
            if cap:
                try:
                    cap.release()
                except Exception:
                    pass
            if tray:
                try:
                    tray.stop()
                except Exception:
                    pass

        threading.Thread(target=_cleanup, daemon=True).start()
        self.after(200, self.destroy)  # destroy window after cleanup starts


if __name__ == "__main__":
    app = App()
    app.mainloop()
