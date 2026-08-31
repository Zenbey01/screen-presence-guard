"""Shared fixtures.

main.py is a single-file app with no package structure, so it gets loaded by
path. Every fixture redirects the face-data paths into a tmp dir: the app writes
and *deletes* those files, and a test run must never touch the developer's own
registration.

Tests mock at the Win32 boundary (`_send_move`, `_cursor_pos`, `_any_key_pressed`)
rather than driving the real mouse. Asserting on a real cursor position fails
whenever a human happens to move the mouse mid-run, which made every early
version of these checks unreliable.
"""
import gc
import importlib.util
import os
import sys
import time
import tkinter

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

APP_BUILD_RETRIES = 5


def _resilient_app(App):
    """Wrap the App class so constructing a root retries on a transient TclError.

    Every test builds a full CustomTkinter App (a Tk root) and destroys it on
    teardown. Cycling roots in a single interpreter intermittently leaves the
    next `Tk()` unable to source its own library -- it dies inside
    `_tkinter.create` with `invalid command name "tcl_findLibrary"` or
    `couldn't read file ".../ttk/scrollbar.tcl"`, even though the file is there
    and the very same test passes when run alone. It is not deterministic and
    not ordering-related: which test trips it moves from run to run, and it can
    strike between two plain App()+destroy() cycles that start no app threads,
    so it is CustomTkinter/Tk teardown state, not this app's code.

    It is also transient -- later roots in the same process build fine. So a
    failed construction is retried after collecting the half-built root and
    letting Tk settle, which is enough to get a clean interpreter. A genuinely
    persistent failure still raises after the retries are spent.
    """
    def build(*args, **kwargs):
        last = None
        for _ in range(APP_BUILD_RETRIES):
            try:
                return App(*args, **kwargs)
            except tkinter.TclError as e:
                last = e
                gc.collect()
                time.sleep(0.15)
        raise last
    return build


def _load():
    # main.py imports its platform backend as a plain sibling module
    # (`import _platform_win`/`_platform_mac`), the same way it would if
    # launched directly with `python main.py` -- in that case Python adds the
    # script's own directory to sys.path[0] automatically. Loading main.py by
    # path via spec_from_file_location does not get that for free, since
    # sys.path[0] is this test process's own directory instead, so the
    # sibling import fails unless REPO is put on sys.path here first.
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    spec = importlib.util.spec_from_file_location(
        "spg", os.path.join(REPO, "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def spg_module():
    """The app module, imported once. Import alone must not raise."""
    cwd = os.getcwd()
    os.chdir(REPO)
    try:
        mod = _load()
        # Every `spg.App()` -- in this file's fixtures and in tests that build a
        # root directly -- goes through the retry wrapper. See _resilient_app.
        mod.App = _resilient_app(mod.App)
        yield mod
    finally:
        os.chdir(cwd)


@pytest.fixture
def spg(spg_module, tmp_path):
    """Module with face-data paths redirected into tmp_path."""
    m = spg_module
    saved = (m.FACE_MODEL_FILE, m.FACE_IMGS_FILE, m._DATA_DIR)
    m._DATA_DIR = str(tmp_path)
    m.FACE_MODEL_FILE = str(tmp_path / "face_model.yml")
    m.FACE_IMGS_FILE = str(tmp_path / "face_imgs.pkl")
    try:
        yield m
    finally:
        m.FACE_MODEL_FILE, m.FACE_IMGS_FILE, m._DATA_DIR = saved


@pytest.fixture
def app(spg):
    """A live App with every timer cancelled on teardown.

    Tearing down without cancelling leaves `_poll_keyboard` / jiggle callbacks
    queued against a destroyed interpreter, which Tk reports as
    `invalid command name ..._poll_keyboard`.
    """
    a = spg.App()
    a.update()
    try:
        yield a
    finally:
        a.running = False
        a._reg_mode = False
        for cancel in (a._cancel_key_poll, a._cancel_jiggle_on,
                       a._cancel_jiggle_off):
            try:
                cancel()
            except Exception:
                pass
        if a._overlay is not None:
            try:
                a._overlay.destroy()
            except Exception:
                pass
            a._overlay = None
        a.screen_off = False
        try:
            a.destroy()
        except Exception:
            pass


@pytest.fixture
def quiet_app(app, spg, monkeypatch):
    """App that cannot be disturbed by the machine's real mouse or keyboard.

    Both wake sources start off and the Win32 readers are pinned, so presence is
    driven only by what a test passes to `_handle_presence`.
    """
    monkeypatch.setattr(spg, "_cursor_pos", lambda: (500, 500))
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: False)
    monkeypatch.setattr(spg, "_send_move", lambda dx, dy: None)
    app.use_mouse.set(False)
    app.use_keyboard.set(False)
    app.interval.set(1.0)
    app._mouse_pos = (500, 500)
    return app


def pump(app, seconds, until=None):
    """Run the Tk event loop for `seconds`, stopping early when `until()` is true.

    `app.update()` in a plain loop is not enough for anything relying on worker
    threads: `_work_on` calls `self.after()` from a daemon thread, which only
    completes while the main thread is actually servicing the event loop.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        if until is not None and until():
            return True
        app.update()
        time.sleep(0.02)
    return until() if until is not None else None


def log_text(app):
    return app.log_box.get("1.0", "end")
