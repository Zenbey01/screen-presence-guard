"""Failures must announce themselves.

Every case here used to look identical to working software: a swallowed
exception, a disabled button, or an unbounded widget.
"""
import os
import pickle

import numpy as np

from conftest import log_text


def test_corrupt_face_model_is_reported(spg):
    """`except Exception: pass` hid this. A bad model silently downgraded the app
    to any-face while the sample count read 0."""
    open(spg.FACE_MODEL_FILE, "wb").write(b"not a yaml model at all")
    open(spg.FACE_IMGS_FILE, "wb").write(b"not a pickle")

    a = spg.App()
    try:
        a.update()
        assert a._face_load_error, "a corrupt model loaded without complaint"
        assert a._recognizer is None, "half-loaded state: filtering on, count 0"
        assert a._known_count == 0
    finally:
        a.destroy()


def test_missing_face_model_is_not_an_error(spg):
    """No registration yet is the normal first run, not a failure."""
    assert not os.path.exists(spg.FACE_MODEL_FILE)
    a = spg.App()
    try:
        a.update()
        assert a._face_load_error == ""
        assert a._recognizer is None
    finally:
        a.destroy()


def test_registration_failure_recovers(app, spg):
    """An unwritable data dir used to leave the button stuck on "cancel"."""
    app.running = True
    app._reg_mode = True
    app._reg_buffer = ["not an image"] * 3       # makes rec.train() raise

    app._finish_register()
    app.update()

    assert app._reg_mode is False, "registration stayed armed after a failure"
    assert app._reg_buffer == []
    assert "ลงทะเบียนใบหน้าใหม่" in app.reg_btn.cget("text")
    assert "ERROR" in log_text(app)


def test_registration_can_be_cancelled(app, spg):
    app.running = True
    app._start_register()
    assert app._reg_mode is True
    app._start_register()                       # same button, now cancels
    assert app._reg_mode is False
    assert app._reg_buffer == []
    assert "ลงทะเบียนใบหน้าใหม่" in app.reg_btn.cget("text")


def test_cancelled_registration_is_not_resurrected(app, spg):
    """A queued worker thread must not finish a run the user cancelled."""
    app.running = True
    app._reg_mode = False
    app._reg_buffer = [np.zeros(spg.FACE_SIZE, dtype=np.uint8)] * spg.REG_SAMPLES

    app._finish_register()
    app.update()
    assert not os.path.exists(spg.FACE_MODEL_FILE), \
        "wrote a model for a cancelled registration"


def test_successful_registration_persists_and_loads_back(app, spg):
    app.running = True
    app._reg_mode = True
    rng = np.random.default_rng(0)
    app._reg_buffer = [rng.integers(0, 255, spg.FACE_SIZE, dtype=np.uint8)
                       for _ in range(spg.REG_SAMPLES)]

    app._finish_register()
    app.update()

    assert app._reg_mode is False
    assert app._known_count == spg.REG_SAMPLES
    assert os.path.exists(spg.FACE_MODEL_FILE)
    assert os.path.exists(spg.FACE_IMGS_FILE)
    imgs, labels = pickle.load(open(spg.FACE_IMGS_FILE, "rb"))
    assert len(imgs) == spg.REG_SAMPLES == len(labels)

    fresh = spg.App()                            # a new run must see it
    try:
        fresh.update()
        assert fresh._face_load_error == ""
        assert fresh._recognizer is not None
        assert fresh._known_count == spg.REG_SAMPLES
    finally:
        fresh.destroy()


def test_log_is_bounded(app, spg):
    """Runs for days; the textbox must not grow forever."""
    for i in range(spg.MAX_LOG_LINES + 250):
        app._log(f"filler {i}")
    lines = int(app.log_box.index("end-1c").split(".")[0])
    assert lines <= spg.MAX_LOG_LINES + 2, f"log grew to {lines} lines"


def test_start_releases_camera_when_it_cannot_open(app, spg, monkeypatch):
    """A failed open used to leak the VideoCapture and leave self.cap set.

    _start() tries the default backend, then explicitly retries with
    CAP_DSHOW (a known real-world fix for a frozen build's default backend
    failing to enumerate devices), so the mock must accept the extra
    positional arg that second call passes and every attempt must release.
    """
    released = []

    class Closed:
        def isOpened(self):
            return False
        def release(self):
            released.append(1)

    monkeypatch.setattr(spg.cv2, "VideoCapture", lambda idx, *a: Closed())
    app._start()
    assert app.running is False
    assert app.cap is None
    assert released == [1, 1]
    assert "ERROR" in log_text(app)


def test_start_falls_back_to_dshow(app, spg, monkeypatch):
    """The default backend can fail to enumerate devices in a frozen build
    even when the camera itself is fine; DSHOW is the working fallback."""
    released = []

    class Closed:
        def isOpened(self):
            return False
        def release(self):
            released.append("closed")

    class Working:
        def isOpened(self):
            return True
        def release(self):
            released.append("working")

    def fake_video_capture(idx, *backend):
        return Working() if backend else Closed()

    monkeypatch.setattr(spg.cv2, "VideoCapture", fake_video_capture)
    app._start()
    assert app.running is True
    assert app.cap is not None
    assert released == ["closed"], "the failed default-backend capture must be released"
    assert "DSHOW" in log_text(app)
