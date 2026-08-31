"""Waking the black overlay.

The overlay is `overrideredirect(True)`, so Windows never makes it the
foreground window. `focus_set()` gives it Tk-internal focus only: while the user
sits in another app their keystrokes go *there* and `<KeyPress>` never fires.
That is why the keyboard is polled with GetAsyncKeyState, never bound.
"""
import time

from conftest import log_text, pump


def _dark(app, spg, monkeypatch):
    """Put the overlay up without depending on the real mouse or camera."""
    monkeypatch.setattr(spg, "_cursor_pos", lambda: (500, 500))
    monkeypatch.setattr(spg, "_send_move", lambda dx, dy: None)
    monkeypatch.setattr(spg, "_reset_idle", lambda: None)
    monkeypatch.setattr(spg, "_idle_ms", lambda: 0)
    app.running = True
    app.interval.set(1.0)
    app.timeout.set(30)
    app.use_mouse.set(False)
    app._mouse_pos = (500, 500)
    app.last_seen = time.time() - 100
    app._handle_presence(False)
    app.update()
    assert app.screen_off is True
    return app


def test_keyboard_poll_starts_with_the_overlay(app, spg, monkeypatch):
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: False)
    _dark(app, spg, monkeypatch)
    assert app._key_poll_id is not None, "no keyboard poll while the screen is dark"


def test_keypress_wakes_the_overlay(app, spg, monkeypatch):
    pressed = {"v": False}
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: pressed["v"])
    monkeypatch.setattr(app, "use_keyboard", app.use_keyboard)
    app.use_keyboard.set(True)
    _dark(app, spg, monkeypatch)

    assert pump(app, 1.0, until=lambda: not app.screen_off) in (False, None), \
        "woke before any key was pressed"

    pressed["v"] = True
    woke = pump(app, 2.0, until=lambda: not app.screen_off)
    assert woke, "a keypress did not wake the overlay"
    assert "คีย์บอร์ด" in log_text(app)


def test_keyboard_disabled_does_not_wake(app, spg, monkeypatch):
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: True)
    app.use_keyboard.set(False)
    _dark(app, spg, monkeypatch)

    pump(app, 1.0)
    assert app.screen_off is True


def test_poll_stops_after_waking(app, spg, monkeypatch):
    # Must reach dark with the key NOT already pressed: _dark() itself calls
    # _handle_presence, and since keyboard now counts as a presence signal
    # (not just a wake trigger), an always-True mock here would stop the
    # screen from ever dimming and _dark()'s own assertion would fail first.
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: False)
    app.use_keyboard.set(True)
    _dark(app, spg, monkeypatch)
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: True)
    pump(app, 2.0, until=lambda: not app.screen_off)
    assert app.screen_off is False
    assert app._key_poll_id is None, "keyboard poll left running after wake"


def test_reset_idle_does_not_wake_the_overlay(app, spg, monkeypatch):
    """`_reset_idle` injects a (0,0) mouse move every interval while dark.

    A non-zero move would generate <Motion> and the overlay would wake itself
    every cycle. Verified against the real SendInput here, not a mock.
    """
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: False)
    app.use_mouse.set(True)
    monkeypatch.setattr(spg, "_cursor_pos", lambda: (500, 500))
    app.running = True
    app.interval.set(1.0)
    app.timeout.set(30)
    app._mouse_pos = (500, 500)
    app.last_seen = time.time() - 100
    app._handle_presence(False)
    app.update()
    assert app.screen_off is True

    for _ in range(8):
        spg._reset_idle()          # the real thing
        pump(app, 0.15)
    assert app.screen_off is True, "the idle-timer reset woke the screen"


def test_wake_is_idempotent(app, spg, monkeypatch):
    """`_wake` has a `if not self.screen_off: return` guard; keep it working."""
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: False)
    _dark(app, spg, monkeypatch)
    app._wake("test")
    assert app.screen_off is False
    app._wake("test")              # must not raise or double-destroy
    assert app.screen_off is False
    assert app._overlay is None


def test_stop_clears_overlay_and_screen_off(app, spg, monkeypatch):
    monkeypatch.setattr(spg, "_any_key_pressed", lambda: False)
    _dark(app, spg, monkeypatch)
    app.cap = None
    app._stop()
    assert app.screen_off is False
    assert app._overlay is None
    assert app._key_poll_id is None
