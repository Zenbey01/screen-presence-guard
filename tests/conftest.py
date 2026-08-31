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
import importlib.util
import os
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
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
        yield _load()
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
