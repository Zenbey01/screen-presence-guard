#!/usr/bin/env python3
"""Screen Presence Guard — keeps screen on when you're here, dark when you leave."""

import cv2
import ctypes
import tkinter as tk
import time
import threading
import os
import pickle
import numpy as np
import customtkinter as ctk
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item

try:
    import mediapipe as mp
    _mp_detect = mp.solutions.face_detection.FaceDetection(
        model_selection=0, min_detection_confidence=0.5
    )
    _mp_lock = threading.Lock()
    _HAS_MP = True
except Exception:
    _HAS_MP = False

_HAS_LBPH = hasattr(cv2, "face")

_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

ES_CONTINUOUS       = 0x80000000
ES_SYSTEM_REQUIRED  = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_DIR            = os.path.dirname(os.path.abspath(__file__))
FACE_MODEL_FILE = os.path.join(_DIR, "face_model.yml")
FACE_IMGS_FILE  = os.path.join(_DIR, "face_imgs.pkl")
FACE_SIZE       = (100, 100)
LBPH_THRESHOLD  = 90.0
REG_SAMPLES     = 40

# ── Color palette (VS-style: purple + white + green) ─────────────────────────
C_BG      = "#f5f5f5"
C_SIDEBAR = "#3c1f52"
C_CARD    = "#ffffff"
C_BANNER  = "#68217a"
C_BORDER  = "#e0e0e0"
C_TEXT1   = "#1e1e1e"
C_TEXT2   = "#6d6d6d"
C_TEXT3   = "#b0b0b0"
C_ON      = "#107c10"
C_WARN    = "#d87500"
C_OFF     = "#c50500"
C_DIM     = "#cccccc"
C_ACCENT  = "#68217a"
C_ACCENT2 = "#f0e6f5"
FONT      = "Segoe UI"
FONT_MONO = "Consolas"


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


def _reset_idle():
    li = _LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    li.dwTime = ctypes.windll.kernel32.GetTickCount()
    ctypes.windll.user32.SetLastInputInfo(ctypes.byref(li))


def _cursor_pos() -> tuple[int, int]:
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def _detect_boxes(frame) -> list:
    if _HAS_MP:
        with _mp_lock:
            rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = _mp_detect.process(rgb)
        boxes = []
        if results.detections:
            h, w = frame.shape[:2]
            for det in results.detections:
                bb = det.location_data.relative_bounding_box
                boxes.append((
                    max(0, int(bb.xmin * w)), max(0, int(bb.ymin * h)),
                    int(bb.width * w), int(bb.height * h),
                ))
        return boxes
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
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("green")
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
        self._reg_mode   = False
        self._reg_buffer = []
        self._recognizer = None
        self._known_count = 0

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

        self._load_face_data()
        _ico_path = os.path.join(_DIR, "icon.ico")
        _pil_icon = Image.open(_ico_path) if os.path.exists(_ico_path) else None
        self._icon_lg = ctk.CTkImage(_pil_icon, size=(38, 38)) if _pil_icon else None
        self._icon_sm = ctk.CTkImage(_pil_icon, size=(26, 26)) if _pil_icon else None
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
                self._recognizer = rec
                with open(FACE_IMGS_FILE, "rb") as f:
                    imgs, _ = pickle.load(f)
                self._known_count = len(imgs)
            except Exception:
                pass

    def _face_status_text(self) -> str:
        if not _HAS_LBPH:
            return "MediaPipe detection เท่านั้น (ไม่มี opencv-contrib)"
        if self._known_count == 0:
            return "ยังไม่ได้ลงทะเบียน → จับได้ทุกหน้า"
        return f"ลงทะเบียนแล้ว {self._known_count} ตัวอย่าง"

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, minsize=60)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, minsize=284)
        self._build_sidebar()
        self._build_main()
        self._build_right()

    # ── Sidebar ───────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, fg_color=C_SIDEBAR, corner_radius=0, width=60,
                           border_width=1, border_color=C_BORDER)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.grid_rowconfigure(2, weight=1)
        sb.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(sb, text="" if self._icon_sm else "👤",
                     image=self._icon_sm, font=ctk.CTkFont(size=22)).grid(
            row=0, column=0, pady=(18, 4))

        self.sidebar_dot = ctk.CTkLabel(sb, text="⬤",
                                         font=ctk.CTkFont(size=12),
                                         text_color=C_DIM)
        self.sidebar_dot.grid(row=1, column=0, pady=4)

        ctk.CTkLabel(sb, text="", fg_color="transparent").grid(row=2, column=0)

        ctk.CTkButton(sb, text="✕", width=36, height=36, corner_radius=8,
                       fg_color="transparent", hover_color=C_CARD,
                       text_color=C_TEXT3, font=ctk.CTkFont(size=14),
                       command=self._close).grid(row=3, column=0, pady=(0, 16))

    # ── Main panel ────────────────────────────────────────────────────────────

    def _build_main(self):
        main = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # Welcome banner
        banner = ctk.CTkFrame(main, corner_radius=0, fg_color=C_BANNER, height=82)
        banner.grid(row=0, column=0, sticky="ew")
        banner.grid_propagate(False)
        banner.grid_columnconfigure(1, weight=1)

        icon_box = ctk.CTkFrame(banner, width=50, height=50, corner_radius=12,
                                  fg_color="#68217a")
        icon_box.grid(row=0, column=0, padx=(16, 12), pady=16, sticky="w")
        icon_box.grid_propagate(False)
        ctk.CTkLabel(icon_box, text="" if self._icon_lg else "👤",
                     image=self._icon_lg, fg_color="transparent").place(
            relx=0.5, rely=0.5, anchor="center")

        tx = ctk.CTkFrame(banner, fg_color="transparent")
        tx.grid(row=0, column=1, sticky="w")
        self.banner_title = ctk.CTkLabel(
            tx, text="ยังไม่เริ่มทำงาน",
            font=ctk.CTkFont(family=FONT, size=18, weight="bold"),
            text_color="#ffffff", anchor="w"
        )
        self.banner_title.pack(anchor="w")
        self.banner_sub = ctk.CTkLabel(
            tx, text="กดปุ่ม Start เพื่อเริ่มตรวจจับใบหน้า",
            font=ctk.CTkFont(family=FONT, size=11),
            text_color="#e8d5f0", anchor="w"
        )
        self.banner_sub.pack(anchor="w")

        self.banner_badge = ctk.CTkLabel(
            banner, text="● Screen OFF",
            font=ctk.CTkFont(family=FONT, size=11),
            fg_color="#68217a", corner_radius=20,
            text_color="#e8d5f0", width=110, height=28,
        )
        self.banner_badge.grid(row=0, column=2, padx=16)

        # Content area
        content = ctk.CTkFrame(main, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew", padx=12, pady=10)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        # Camera card
        cam_card = ctk.CTkFrame(content, corner_radius=12, fg_color=C_CARD,
                                  border_width=1, border_color=C_BORDER)
        cam_card.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        cam_card.grid_rowconfigure(0, weight=1)
        cam_card.grid_columnconfigure(0, weight=1)

        self.cam_label = ctk.CTkLabel(
            cam_card, text="กดปุ่ม Start เพื่อเริ่ม",
            fg_color="#f0f0f0", corner_radius=8,
            text_color=C_TEXT3, font=ctk.CTkFont(family=FONT, size=12)
        )
        self.cam_label.grid(row=0, column=0, padx=8, pady=(8, 0), sticky="nsew")

        sr = ctk.CTkFrame(cam_card, fg_color="transparent")
        sr.grid(row=1, column=0, padx=10, pady=(5, 8), sticky="ew")
        sr.grid_columnconfigure(1, weight=1)

        self.dot = ctk.CTkLabel(sr, text="⬤", font=ctk.CTkFont(size=10),
                                 text_color=C_DIM, width=14)
        self.dot.grid(row=0, column=0, padx=(0, 6))
        self.status_lbl = ctk.CTkLabel(
            sr, text="ยังไม่เริ่มทำงาน",
            font=ctk.CTkFont(family=FONT, size=12), text_color=C_TEXT2, anchor="w"
        )
        self.status_lbl.grid(row=0, column=1, sticky="w")
        self.screen_lbl = ctk.CTkLabel(
            sr, text="",
            font=ctk.CTkFont(family=FONT, size=11, weight="bold"),
            text_color=C_TEXT3, anchor="e"
        )
        self.screen_lbl.grid(row=0, column=2, padx=(8, 0))

        # Stat cards
        stats = ctk.CTkFrame(content, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        stats.grid_columnconfigure((0, 1, 2), weight=1)

        self.face_stat  = self._stat_card(stats, 0, "👤", "Face Detection", C_ACCENT2, C_ACCENT)
        self.screen_stat = self._stat_card(stats, 1, "🖥️", "Display State",  "#0d2a1a", C_ON)
        self.time_stat  = self._stat_card(stats, 2, "⏱️", "ดับจอใน",        "#2a1a00", C_WARN)

        # Action buttons
        btns = ctk.CTkFrame(content, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="ew")
        btns.grid_columnconfigure(0, weight=2)
        btns.grid_columnconfigure(1, weight=1)

        self.start_btn = ctk.CTkButton(
            btns, text="▶   Start", height=42,
            font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
            corner_radius=10, fg_color=C_ACCENT, hover_color="#521a63",
            text_color="#ffffff", command=self._toggle
        )
        self.start_btn.grid(row=0, column=0, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            btns, text="⊟   Tray", height=42,
            font=ctk.CTkFont(family=FONT, size=13),
            corner_radius=10, fg_color=C_CARD, hover_color="#e8d5f0",
            border_width=1, border_color=C_BORDER, text_color=C_TEXT3,
            command=self._to_tray
        ).grid(row=0, column=1, sticky="ew")

    def _stat_card(self, parent, col, icon, label, icon_bg, icon_fg):
        card = ctk.CTkFrame(parent, corner_radius=10, fg_color=C_CARD,
                             border_width=1, border_color=C_BORDER)
        card.grid(row=0, column=col, padx=(0, 8) if col < 2 else 0, sticky="ew")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=12, pady=10, fill="x")

        icon_wrap = ctk.CTkFrame(inner, width=36, height=36, corner_radius=10,
                                  fg_color=icon_bg)
        icon_wrap.pack(side="left", padx=(0, 10))
        icon_wrap.pack_propagate(False)
        ctk.CTkLabel(icon_wrap, text=icon, font=ctk.CTkFont(size=16)).place(
            relx=0.5, rely=0.5, anchor="center")

        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.pack(side="left")

        val_lbl = ctk.CTkLabel(right, text="—",
                                font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
                                text_color=C_TEXT1, anchor="w")
        val_lbl.pack(anchor="w")
        ctk.CTkLabel(right, text=label,
                      font=ctk.CTkFont(family=FONT, size=10),
                      text_color=C_TEXT3, anchor="w").pack(anchor="w")
        return val_lbl

    # ── Right panel ───────────────────────────────────────────────────────────

    def _build_right(self):
        rp = ctk.CTkFrame(self, fg_color=C_SIDEBAR, corner_radius=0,
                           border_width=1, border_color=C_BORDER)
        rp.grid(row=0, column=2, sticky="nsew")
        rp.grid_columnconfigure(0, weight=1)

        # Face recognition
        ctk.CTkLabel(rp, text="FACE RECOGNITION",
                     font=ctk.CTkFont(family=FONT, size=9, weight="bold"),
                     text_color=C_TEXT3, anchor="w").grid(
            row=0, column=0, padx=14, pady=(14, 6), sticky="w")

        face_card = ctk.CTkFrame(rp, corner_radius=10, fg_color=C_CARD,
                                  border_width=1, border_color=C_BORDER)
        face_card.grid(row=1, column=0, padx=12, sticky="ew")
        face_card.grid_columnconfigure(0, weight=1)

        self.face_lbl = ctk.CTkLabel(
            face_card, text=self._face_status_text(),
            font=ctk.CTkFont(family=FONT, size=11), text_color=C_TEXT2, anchor="w"
        )
        self.face_lbl.grid(row=0, column=0, padx=12, pady=(10, 6), sticky="w")

        self.reg_btn = ctk.CTkButton(
            face_card, text="+ ลงทะเบียนหน้า", height=32,
            font=ctk.CTkFont(family=FONT, size=12), corner_radius=8,
            fg_color=C_ACCENT2, hover_color="#521a63",
            border_width=1, border_color=C_ACCENT, text_color="#e8d5f0",
            command=self._start_register,
            state="normal" if _HAS_LBPH else "disabled",
        )
        self.reg_btn.grid(row=1, column=0, padx=12, pady=(0, 6), sticky="ew")

        ctk.CTkButton(
            face_card, text="✕ ล้างข้อมูล", height=28,
            font=ctk.CTkFont(family=FONT, size=11), corner_radius=8,
            fg_color="transparent", hover_color=C_BG,
            border_width=1, border_color=C_BORDER, text_color=C_TEXT3,
            command=self._clear_face_data,
        ).grid(row=2, column=0, padx=12, pady=(0, 12), sticky="ew")

        # Divider
        ctk.CTkFrame(rp, height=1, fg_color=C_BORDER).grid(
            row=2, column=0, padx=12, pady=10, sticky="ew")

        # Settings
        ctk.CTkLabel(rp, text="SETTINGS",
                     font=ctk.CTkFont(family=FONT, size=9, weight="bold"),
                     text_color=C_TEXT3, anchor="w").grid(
            row=3, column=0, padx=14, pady=(0, 8), sticky="w")

        sf = ctk.CTkFrame(rp, fg_color="transparent")
        sf.grid(row=4, column=0, padx=12, sticky="ew")
        sf.grid_columnconfigure(1, weight=1)
        self._add_slider(sf, 0, "ดับจอหลังจาก", self.timeout,  5, 120,
                         lambda v: f"{int(v)}s")
        self._add_slider(sf, 1, "ตรวจสอบทุก",   self.interval, 1, 10,
                         lambda v: f"{v:.1f}s")

        # ── ปลุกจอด้วย ──────────────────────────────────────────────────────────
        ctk.CTkLabel(sf, text="ปลุกจอด้วย",
                     font=ctk.CTkFont(family=FONT, size=11),
                     text_color=C_TEXT2, anchor="w").grid(
            row=2, column=0, padx=(0, 8), pady=(10, 2), sticky="w")

        tog_row = ctk.CTkFrame(sf, fg_color="transparent")
        tog_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        self._make_switch(tog_row, "เมาส์",     self.use_mouse,    pack_side="left", padx=(0,16))
        self._make_switch(tog_row, "คีย์บอร์ด", self.use_keyboard, pack_side="left")

        # ── ขยับเมาส์วงกลม ──────────────────────────────────────────────────────
        ctk.CTkLabel(sf, text="ขยับเมาส์วงกลม",
                     font=ctk.CTkFont(family=FONT, size=11),
                     text_color=C_TEXT2, anchor="w").grid(
            row=4, column=0, padx=(0, 8), pady=(10, 2), sticky="w")

        # จอเปิด row
        jon_row = ctk.CTkFrame(sf, fg_color="transparent")
        jon_row.grid(row=5, column=0, columnspan=3, sticky="ew")
        jon_row.grid_columnconfigure(2, weight=1)
        self._make_switch(jon_row, "จอเปิด", self.jiggle_on,
                          command=lambda: self._on_jiggle_toggle("on"),
                          pack_side=None, grid=(0,0))
        ctk.CTkLabel(jon_row, text="ทุก", font=ctk.CTkFont(family=FONT, size=10),
                     text_color=C_TEXT3).grid(row=0, column=1, padx=(10,4))
        self._inline_slider(jon_row, self.jiggle_on_sec, 10, 300,
                            lambda v: f"{int(v)}s", col=2)

        # จอดำ row
        joff_row = ctk.CTkFrame(sf, fg_color="transparent")
        joff_row.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(4,0))
        joff_row.grid_columnconfigure(2, weight=1)
        self._make_switch(joff_row, "จอดำ ", self.jiggle_off,
                          command=lambda: self._on_jiggle_toggle("off"),
                          pack_side=None, grid=(0,0))
        ctk.CTkLabel(joff_row, text="ทุก", font=ctk.CTkFont(family=FONT, size=10),
                     text_color=C_TEXT3).grid(row=0, column=1, padx=(10,4))
        self._inline_slider(joff_row, self.jiggle_off_sec, 10, 300,
                            lambda v: f"{int(v)}s", col=2)

        # Divider
        ctk.CTkFrame(rp, height=1, fg_color=C_BORDER).grid(
            row=5, column=0, padx=12, pady=10, sticky="ew")

        # Log
        ctk.CTkLabel(rp, text="LOG",
                     font=ctk.CTkFont(family=FONT, size=9, weight="bold"),
                     text_color=C_TEXT3, anchor="w").grid(
            row=6, column=0, padx=14, pady=(0, 4), sticky="w")

        log_wrap = ctk.CTkFrame(rp, corner_radius=8, fg_color=C_BG,
                                  border_width=1, border_color=C_BORDER)
        log_wrap.grid(row=7, column=0, padx=12, pady=(0, 10), sticky="nsew")
        log_wrap.grid_columnconfigure(0, weight=1)
        rp.grid_rowconfigure(7, weight=1)

        self.log_box = ctk.CTkTextbox(
            log_wrap, height=130,
            font=ctk.CTkFont(family=FONT_MONO, size=9),
            fg_color="transparent", border_width=0, text_color="#3d4966"
        )
        self.log_box.pack(padx=4, pady=4, fill="both", expand=True)
        self.log_box.configure(state="disabled")

    def _add_slider(self, parent, row, label, var, lo, hi, fmt):
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(family=FONT, size=11),
                     text_color=C_TEXT2, anchor="w").grid(
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
            font=ctk.CTkFont(family=FONT, size=11), text_color=C_TEXT1,
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
            return
        self._reg_mode   = True
        self._reg_buffer = []
        tip = " (เพิ่มตัวอย่างเดิม)" if self._known_count else ""
        self._log(f"ลงทะเบียนหน้า{tip}... หันหน้าตรง ขยับเล็กน้อย")
        self.reg_btn.configure(text="กำลังจับหน้า...", state="disabled",
                                text_color="#f9a825", border_color="#7a4400")

    def _bg_register(self, frame):
        try:
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
            self.after(0, lambda: self._log(f"[warn] register: {e}"))
        finally:
            self._bg_busy = False

    def _finish_register(self):
        existing_imgs, existing_labels = [], []
        if os.path.exists(FACE_IMGS_FILE):
            with open(FACE_IMGS_FILE, "rb") as f:
                existing_imgs, existing_labels = pickle.load(f)
        all_imgs   = existing_imgs + self._reg_buffer
        all_labels = existing_labels + [0] * len(self._reg_buffer)
        rec = cv2.face.LBPHFaceRecognizer_create()
        rec.train(all_imgs, np.array(all_labels, dtype=np.int32))
        rec.write(FACE_MODEL_FILE)
        self._recognizer = rec
        with open(FACE_IMGS_FILE, "wb") as f:
            pickle.dump((all_imgs, all_labels), f)
        self._known_count = len(all_imgs)
        self._reg_mode    = False
        self._reg_buffer  = []
        self._log(f"ลงทะเบียนสำเร็จ! รวม {self._known_count} ตัวอย่าง")
        self.reg_btn.configure(text="+ ลงทะเบียนหน้า", state="normal",
                                text_color="#e8d5f0", border_color=C_ACCENT)
        self.face_lbl.configure(text=self._face_status_text())

    def _clear_face_data(self):
        self._recognizer  = None
        self._known_count = 0
        for f in (FACE_MODEL_FILE, FACE_IMGS_FILE):
            if os.path.exists(f):
                os.remove(f)
        self.face_lbl.configure(text=self._face_status_text())
        self._log("ล้างข้อมูลหน้าแล้ว")

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def _toggle(self):
        (self._stop if self.running else self._start)()

    def _start(self):
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            self._log("ERROR: เปิดกล้องไม่ได้")
            return
        self.running     = True
        self.screen_off  = False
        self.last_seen   = time.time()
        self._last_check = 0.0
        self._heartbeat  = 0
        self._bg_busy    = False
        self._reg_mode   = False
        self._mouse_pos  = _cursor_pos()
        self.start_btn.configure(
            text="⏹   Stop", fg_color="#c50500",
            hover_color="#9a0400", text_color="#ffffff"
        )
        det = "MediaPipe" if _HAS_MP else "Haar"
        rec = f"LBPH ({self._known_count} ตย.)" if self._recognizer else "any-face"
        self.banner_title.configure(text="กำลังทำงาน")
        self.banner_sub.configure(text=f"{det} · {rec} · ตรวจสอบทุก {self.interval.get():.1f}s")
        self.banner_badge.configure(text="● Screen ON", text_color="#ffffff", fg_color="#107c10")
        self.sidebar_dot.configure(text_color=C_ON)
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
        self._reg_mode = False
        self._cancel_jiggle_on()
        self._cancel_jiggle_off()
        try:
            self.use_jiggle.trace_remove("write",
                self.use_jiggle.trace_info()[0][1])
        except Exception:
            pass
        if self._overlay:
            self._overlay.destroy()
            self._overlay = None
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        if self.cap:
            self.cap.release()
            self.cap = None
        self.start_btn.configure(
            text="▶   Start", fg_color=C_ACCENT,
            hover_color="#521a63", text_color="#ffffff"
        )
        self.dot.configure(text_color=C_DIM)
        self.status_lbl.configure(text="หยุดทำงาน", text_color=C_TEXT2)
        self.screen_lbl.configure(text="")
        self.face_stat.configure(text="—")
        self.screen_stat.configure(text="—")
        self.time_stat.configure(text="—")
        self.cam_label.configure(image=None, text="กดปุ่ม Start เพื่อเริ่ม")
        self.banner_title.configure(text="ยังไม่เริ่มทำงาน")
        self.banner_sub.configure(text="กดปุ่ม Start เพื่อเริ่มตรวจจับใบหน้า")
        self.banner_badge.configure(text="● Screen OFF", text_color="#e8d5f0", fg_color="#68217a")
        self.sidebar_dot.configure(text_color=C_DIM)
        self.reg_btn.configure(text="+ ลงทะเบียนหน้า",
                                state="normal" if _HAS_LBPH else "disabled",
                                text_color="#e8d5f0", border_color=C_ACCENT)
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

                now = time.time()
                if now - self._last_check >= self.interval.get() and not self._bg_busy:
                    self._last_check = now
                    self._bg_busy    = True
                    tgt = self._bg_register if self._reg_mode else self._bg_check
                    threading.Thread(target=tgt, args=(frame.copy(),), daemon=True).start()
        except Exception as e:
            self._log(f"[warn] tick: {e}")
        self.after(33, self._tick)

    def _bg_check(self, frame):
        try:
            face_detected = self._check_my_face(frame)
            self.after(0, lambda: self._handle_presence(face_detected))
        except Exception as e:
            self.after(0, lambda: self._log(f"[warn] check: {e}"))
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
        if pos != self._mouse_pos:
            self._mouse_pos = pos   # always track pos, only trigger if enabled
        user_present = face_detected or mouse_moved

        if user_present:
            self.last_seen = time.time()
            if self.screen_off:
                self._wake("ใบหน้า" if face_detected else "เมาส์")
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
            label = "พบใบหน้า" if face_detected else "พบการเคลื่อนไหว"
            self.dot.configure(text_color=C_ON)
            self.status_lbl.configure(text=label, text_color=C_TEXT1)
            self.screen_lbl.configure(text="Screen ON", text_color=C_ON)
            self.face_stat.configure(text="พบหน้า" if face_detected else "เมาส์")
            self.screen_stat.configure(text="ON", text_color=C_ON)
            self.time_stat.configure(text="—", text_color=C_TEXT3)
            self.sidebar_dot.configure(text_color=C_ON)
        else:
            absent    = time.time() - self.last_seen
            remaining = self.timeout.get() - absent
            if remaining > 0:
                self.dot.configure(text_color=C_WARN)
                self.status_lbl.configure(text="ไม่พบใบหน้า", text_color=C_TEXT2)
                self.screen_lbl.configure(text=f"ดับใน {int(remaining)}s", text_color=C_WARN)
                self.face_stat.configure(text="ไม่พบ", text_color=C_TEXT2)
                self.time_stat.configure(text=f"{int(remaining)}s", text_color=C_WARN)
                self.sidebar_dot.configure(text_color=C_WARN)
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
                    self._log(f"โปรแกรมทำงาน — จอมืดมา {mins} นาที")

    # ── Screen control ────────────────────────────────────────────────────────

    def _sleep(self):
        self.screen_off = True
        self._heartbeat = 0
        self._overlay   = _BlackOverlay(self)
        self.dot.configure(text_color=C_OFF)
        self.status_lbl.configure(text="ไม่พบใบหน้า", text_color=C_TEXT2)
        self.screen_lbl.configure(text="Screen OFF", text_color=C_OFF)
        self.face_stat.configure(text="ไม่พบ", text_color=C_TEXT2)
        self.screen_stat.configure(text="OFF", text_color=C_OFF)
        self.time_stat.configure(text="จอมืด", text_color=C_OFF)
        self.banner_badge.configure(text="● Screen OFF", text_color="#ffffff", fg_color="#c50500")
        self.sidebar_dot.configure(text_color=C_OFF)
        self._log("จอมืด — กล้องยังทำงาน ไม่มี lock")

    # ── Mouse circle jiggle ───────────────────────────────────────────────────

    def _circle_move(self, x, y, radius=10, steps=12):
        """Move cursor in a small circle then return — runs in background thread."""
        import math
        for i in range(steps + 1):
            a = 2 * math.pi * i / steps
            ctypes.windll.user32.SetCursorPos(
                x + int(radius * math.cos(a)),
                y + int(radius * math.sin(a)))
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
            self.after(0, lambda px=x, py=y:
                       self._log(f"[jiggle-ON] วงกลมจาก ({px},{py})"))
            self._circle_move(x, y)
            self._mouse_pos = (x, y)
            _reset_idle()
            self.last_seen = time.time()
            self.after(0, lambda px=x, py=y:
                       self._log(f"[jiggle-ON] ✓ เสร็จ ({px},{py})"))
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
            self.after(0, lambda px=x, py=y:
                       self._log(f"[jiggle-OFF] วงกลมจาก ({px},{py})"))
            self._circle_move(x, y)
            self._mouse_pos = (x, y)
            _reset_idle()
            self.after(0, lambda px=x, py=y:
                       self._log(f"[jiggle-OFF] ✓ เสร็จ ({px},{py})"))
        except Exception as e:
            self.after(0, lambda err=e: self._log(f"[jiggle-OFF] ERROR: {err}"))
        finally:
            self._jiggling = False

    def _wake(self, trigger: str = ""):
        if not self.screen_off:
            return
        if self._overlay:
            self._overlay.destroy()
            self._overlay = None
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
        self._mouse_pos = _cursor_pos()
        self.screen_off = False
        self._heartbeat = 0
        self.banner_badge.configure(text="● Screen ON", text_color="#ffffff", fg_color="#107c10")
        self.sidebar_dot.configure(text_color=C_ON)
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
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _close(self):
        # Stop app state immediately (fast, no blocking)
        self.running   = False
        self._reg_mode = False
        if self._overlay:
            try:
                self._overlay.destroy()
            except Exception:
                pass
            self._overlay = None
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
