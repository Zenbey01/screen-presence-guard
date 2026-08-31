"""Presence decisions: what keeps the screen lit, and what lets it go dark."""
import time

from conftest import log_text, pump


def test_typing_prevents_dimming(quiet_app, spg, monkeypatch):
    """Keyboard is a presence signal, not only a wake trigger.

    Before this, typing with your face turned away dimmed the screen under you:
    reproduced at 6.4s of continuous typing against a 5s timeout.
    """
    app = quiet_app
    app.running = True
    app.screen_off = False
    app.timeout.set(3)
    app.use_keyboard.set(True)
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: True)   # user is typing
    app.last_seen = time.time()

    for _ in range(8):                       # ~8x the timeout worth of cycles
        app._handle_presence(False)          # camera sees no face
        app.update()
    assert app.screen_off is False


def test_dims_when_nothing_happens(quiet_app):
    """The negative control. Without it, a test that never dims proves nothing."""
    app = quiet_app
    app.running = True
    app.screen_off = False
    app.timeout.set(3)
    app.last_seen = time.time() - 10         # timeout already elapsed

    app._handle_presence(False)
    app.update()
    assert app.screen_off is True


def test_keyboard_off_does_not_hold_the_screen(quiet_app, spg, monkeypatch):
    """A keypress must be ignored entirely when the toggle is off."""
    app = quiet_app
    app.running = True
    app.screen_off = False
    app.timeout.set(3)
    app.use_keyboard.set(False)
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: True)
    app.last_seen = time.time() - 10

    app._handle_presence(False)
    app.update()
    assert app.screen_off is True


def test_face_keeps_screen_lit(quiet_app):
    app = quiet_app
    app.running = True
    app.screen_off = False
    app.timeout.set(3)
    app.last_seen = time.time() - 10

    app._handle_presence(True)               # face detected
    app.update()
    assert app.screen_off is False


def test_trigger_labels_distinguish_the_source(quiet_app, spg, monkeypatch):
    """A keyboard wake used to be logged as "เมาส์" because the ternary had
    only two branches."""
    app = quiet_app
    app.running = True
    app.use_keyboard.set(True)
    app.timeout.set(30)
    app.last_seen = time.time() - 100
    app._handle_presence(False)              # dims
    app.update()
    assert app.screen_off is True

    monkeypatch.setattr(spg, "_any_key_pressed", lambda: True)
    app._handle_presence(False)              # wakes, by keyboard
    app.update()
    assert app.screen_off is False
    assert "คีย์บอร์ด" in log_text(app)


def test_idle_timer_is_reset_every_cycle_while_dark(quiet_app, spg, monkeypatch):
    """Instrumented rather than measured.

    Reading GetLastInputInfo here would be meaningless on a developer machine:
    a human touching the mouse pins idle at 0 and the assertion proves nothing.
    Counting calls is deterministic.
    """
    app = quiet_app
    calls = []
    monkeypatch.setattr(spg, "_reset_idle", lambda: calls.append(1))
    app.running = True
    app.timeout.set(3)
    app.last_seen = time.time() - 10
    app._handle_presence(False)               # dims
    app.update()
    assert app.screen_off is True

    for _ in range(5):
        app._handle_presence(False)
        app.update()
    assert len(calls) >= 5, "idle timer not reset while the overlay is up"


def test_camera_failure_logs_and_stops(app, spg):
    """`_tick` used to fall through on ok=False: no log, no status change, and
    presence checking stopped forever."""
    class DeadCam:
        def read(self):
            return False, None
        def release(self):
            pass
        def isOpened(self):
            return True

    app.running = True
    app.screen_off = False
    app.cap = DeadCam()
    app._camera_failed_since = None
    app.last_seen = time.time()
    app._tick()

    stopped = pump(app, spg.CAMERA_FAIL_SEC + 4, until=lambda: not app.running)
    assert stopped, "a dead camera never stopped the app"
    assert app.cap is None
    assert "กล้องไม่ส่งภาพ" in log_text(app)
