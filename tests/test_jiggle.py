"""Mouse circle jiggle: which one fires, and what it must not disturb.

Two Win32 facts this rests on, both measured on Win11: SetCursorPos moves the
pointer but does NOT reset the idle timer (7125ms -> 7203ms across a call), so it
cannot hold off an enforced lock. SendInput does (7203ms -> 78ms). Hence every
step goes through `_send_move`.

Injections are counted rather than the real cursor watched: a human moving the
mouse mid-run would otherwise fail the assertions.
"""
import time

from conftest import log_text, pump


def _arm(app, spg, monkeypatch):
    sent = []
    monkeypatch.setattr(spg, "_send_move", lambda dx, dy: sent.append((dx, dy)))
    monkeypatch.setattr(spg, "_reset_idle", lambda: None)
    monkeypatch.setattr(spg, "_idle_ms", lambda: 0)
    monkeypatch.setattr(spg, "_cursor_pos", lambda: (500, 500))
    app.running = True
    return sent


def test_circle_move_injects_one_event_per_step(app, spg, monkeypatch):
    """SetCursorPos alone would look identical on screen and achieve nothing."""
    sent = _arm(app, spg, monkeypatch)
    app._circle_move(500, 500, radius=10, steps=12)
    assert len(sent) == 13, "expected one injection per circle step"
    assert any(dx or dy for dx, dy in sent), "every step was a no-op move"


def test_on_jiggle_does_not_postpone_dimming(app, spg, monkeypatch):
    """The regression that mattered most.

    `_work_on` used to do `self.last_seen = time.time()`, so any ON-jiggle
    interval shorter than the dim timeout reset the countdown forever and the
    screen never dimmed. Measured: 4s timeout with a 3s jiggle stayed lit at
    11.4s while the control dimmed at 4.7s. The ON-jiggle caps at 300s against a
    900s timeout, so the line could not be made safe.
    """
    _arm(app, spg, monkeypatch)
    monkeypatch.setattr(app, "_circle_move", lambda *a, **k: None)
    app.screen_off = False
    before = time.time() - 500
    app.last_seen = before

    app._work_on()
    app.update()
    assert app.last_seen == before, "screen-ON jiggle refreshed last_seen again"


def test_off_jiggle_does_not_postpone_dimming(app, spg, monkeypatch):
    _arm(app, spg, monkeypatch)
    monkeypatch.setattr(app, "_circle_move", lambda *a, **k: None)
    app.screen_off = True
    before = time.time() - 500
    app.last_seen = before

    app._work_off()
    app.update()
    assert app.last_seen == before


def test_jiggling_flag_clears_after_the_worker(app, spg, monkeypatch):
    """A stuck `_jiggling` would leave the overlay permanently unwakeable."""
    _arm(app, spg, monkeypatch)
    monkeypatch.setattr(app, "_circle_move", lambda *a, **k: None)
    app.screen_off = False
    app._work_on()
    assert app._jiggling is False


def test_jiggling_flag_clears_even_when_the_circle_raises(app, spg, monkeypatch):
    _arm(app, spg, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(app, "_circle_move", boom)
    app.screen_off = False
    app._work_on()
    app.update()
    assert app._jiggling is False
    assert "ERROR" in log_text(app)


def test_on_jiggle_skips_while_dark(app, spg, monkeypatch):
    """The two cards are mutually exclusive by screen state."""
    _arm(app, spg, monkeypatch)
    ran = []
    monkeypatch.setattr(app, "_work_on", lambda: ran.append(1))
    app.jiggle_on.set(True)
    app.jiggle_on_sec.set(10)
    app.screen_off = True

    app._do_jiggle_on()
    pump(app, 0.4)
    assert ran == [], "ON-jiggle moved the mouse while the screen was dark"
    assert "ข้าม" in log_text(app)
    assert app._jiggle_on_id is not None, "timer must keep running for later"


def test_off_jiggle_skips_while_lit(app, spg, monkeypatch):
    _arm(app, spg, monkeypatch)
    ran = []
    monkeypatch.setattr(app, "_work_off", lambda: ran.append(1))
    app.jiggle_off.set(True)
    app.jiggle_off_sec.set(10)
    app.screen_off = False

    app._do_jiggle_off()
    pump(app, 0.4)
    assert ran == []
    assert "ข้าม" in log_text(app)


def test_on_jiggle_runs_while_lit(app, spg, monkeypatch):
    _arm(app, spg, monkeypatch)
    ran = []
    monkeypatch.setattr(app, "_work_on", lambda: ran.append(1))
    app.jiggle_on.set(True)
    app.jiggle_on_sec.set(10)
    app.screen_off = False

    app._do_jiggle_on()
    pump(app, 1.0, until=lambda: bool(ran))
    assert ran == [1]


def test_off_jiggle_runs_while_dark(app, spg, monkeypatch):
    _arm(app, spg, monkeypatch)
    ran = []
    monkeypatch.setattr(app, "_work_off", lambda: ran.append(1))
    app.jiggle_off.set(True)
    app.jiggle_off_sec.set(10)
    app.screen_off = True

    app._do_jiggle_off()
    pump(app, 1.0, until=lambda: bool(ran))
    assert ran == [1]


def test_disabled_jiggle_is_not_scheduled(app, spg, monkeypatch):
    _arm(app, spg, monkeypatch)
    app.jiggle_on.set(False)
    app._sched_on()
    assert app._jiggle_on_id is None


def test_stopping_cancels_both_timers(app, spg, monkeypatch):
    _arm(app, spg, monkeypatch)
    app.jiggle_on.set(True)
    app.jiggle_off.set(True)
    app._sched_on()
    app._sched_off()
    assert app._jiggle_on_id is not None and app._jiggle_off_id is not None

    app.cap = None
    app._stop()
    assert app._jiggle_on_id is None
    assert app._jiggle_off_id is None
