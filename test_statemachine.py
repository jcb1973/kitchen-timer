"""Unit tests for the pure timer state machine — no hardware, no network.

Run: python3 -m unittest -v   (or: python3 test_statemachine.py)
"""
import unittest

from statemachine import (
    TimerMachine, State, Event,
    Render, Beep, Clear, ReleaseFocus,
    STEP_S, MAX_S, SET_IDLE_TIMEOUT_S, DONE_TIMEOUT_S, DONE_REBEEP_S,
)


def kinds(effects):
    return [type(e).__name__ for e in effects]


class SetMode(unittest.TestCase):
    def test_start_opens_in_set_with_default(self):
        m = TimerMachine(default_set_s=300)
        eff = m.start()
        self.assertEqual(m.state, State.SET)
        self.assertEqual(eff, [Render("set", 300)])

    def test_rotate_steps_by_15s(self):
        m = TimerMachine(default_set_s=300)
        m.start()
        self.assertEqual(m.handle(Event.ROTATE_CW), [Render("set", 300 + STEP_S)])
        self.assertEqual(m.handle(Event.ROTATE_CCW), [Render("set", 300)])

    def test_dial_floors_at_zero(self):
        m = TimerMachine(default_set_s=STEP_S)
        m.start()
        m.handle(Event.ROTATE_CCW)                 # -> 0
        eff = m.handle(Event.ROTATE_CCW)           # stays 0
        self.assertEqual(m.duration_s, 0)
        self.assertEqual(eff, [Render("set", 0)])

    def test_dial_caps_at_max(self):
        m = TimerMachine(default_set_s=MAX_S)
        m.start()
        m.handle(Event.ROTATE_CW)
        self.assertEqual(m.duration_s, MAX_S)

    def test_press_at_zero_cancels(self):
        m = TimerMachine(default_set_s=0)
        m.start()
        eff = m.handle(Event.PRESS_SHORT)
        self.assertEqual(m.state, State.EXIT)
        self.assertEqual(kinds(eff), ["Clear", "ReleaseFocus"])

    def test_press_starts_running(self):
        m = TimerMachine(default_set_s=60)
        m.start()
        eff = m.handle(Event.PRESS_SHORT)
        self.assertEqual(m.state, State.RUNNING)
        self.assertEqual(m.remaining_s, 60)
        self.assertEqual(eff, [Render("running", 60)])

    def test_long_press_bails(self):
        m = TimerMachine(default_set_s=300)
        m.start()
        eff = m.handle(Event.PRESS_LONG)
        self.assertEqual(m.state, State.EXIT)
        self.assertEqual(kinds(eff), ["Clear", "ReleaseFocus"])

    def test_idle_timeout_releases(self):
        m = TimerMachine(default_set_s=300)
        m.start()
        for _ in range(SET_IDLE_TIMEOUT_S - 1):
            self.assertEqual(m.handle(Event.TICK), [])
            self.assertEqual(m.state, State.SET)
        eff = m.handle(Event.TICK)                 # the Nth idle tick
        self.assertEqual(m.state, State.EXIT)
        self.assertEqual(kinds(eff), ["Clear", "ReleaseFocus"])

    def test_input_resets_idle_timer(self):
        m = TimerMachine(default_set_s=300)
        m.start()
        for _ in range(SET_IDLE_TIMEOUT_S - 1):
            m.handle(Event.TICK)
        m.handle(Event.ROTATE_CW)                  # human input resets idle
        for _ in range(SET_IDLE_TIMEOUT_S - 1):
            m.handle(Event.TICK)
        self.assertEqual(m.state, State.SET)       # not timed out


class Running(unittest.TestCase):
    def test_tick_counts_down(self):
        m = TimerMachine(default_set_s=3)
        m.start(); m.handle(Event.PRESS_SHORT)
        self.assertEqual(m.handle(Event.TICK), [Render("running", 2)])
        self.assertEqual(m.handle(Event.TICK), [Render("running", 1)])

    def test_reaching_zero_goes_done_and_beeps(self):
        m = TimerMachine(default_set_s=1)
        m.start(); m.handle(Event.PRESS_SHORT)
        eff = m.handle(Event.TICK)                 # 1 -> 0
        self.assertEqual(m.state, State.DONE)
        self.assertEqual(kinds(eff), ["Render", "Beep"])
        self.assertEqual(eff[0].kind, "done")
        self.assertEqual(eff[1], Beep("done"))

    def test_press_stops_running(self):
        m = TimerMachine(default_set_s=300)
        m.start(); m.handle(Event.PRESS_SHORT)
        m.handle(Event.TICK)
        eff = m.handle(Event.PRESS_SHORT)
        self.assertEqual(m.state, State.EXIT)
        self.assertEqual(kinds(eff), ["Clear", "ReleaseFocus"])

    def test_rotate_while_running_is_ignored(self):
        m = TimerMachine(default_set_s=300)
        m.start(); m.handle(Event.PRESS_SHORT)
        self.assertEqual(m.handle(Event.ROTATE_CW), [])
        self.assertEqual(m.state, State.RUNNING)


class Done(unittest.TestCase):
    def _to_done(self):
        m = TimerMachine(default_set_s=1)
        m.start(); m.handle(Event.PRESS_SHORT); m.handle(Event.TICK)
        assert m.state == State.DONE
        return m

    def test_press_acknowledges(self):
        m = self._to_done()
        eff = m.handle(Event.PRESS_SHORT)
        self.assertEqual(m.state, State.EXIT)
        self.assertEqual(kinds(eff), ["Clear", "ReleaseFocus"])

    def test_rebeeps_on_interval(self):
        m = self._to_done()
        beeps = 0
        for _ in range(DONE_REBEEP_S):
            eff = m.handle(Event.TICK)
            beeps += sum(1 for e in eff if isinstance(e, Beep))
        self.assertEqual(beeps, 1)                 # exactly one re-beep per interval

    def test_done_ticks_dont_repaint(self):
        # non-interval DONE ticks emit nothing (the re-Render+beep lands only on
        # the DONE_REBEEP_S boundary; matrixd's flash owns the blink between)
        m = self._to_done()
        for _ in range(DONE_REBEEP_S - 1):
            self.assertEqual(m.handle(Event.TICK), [])

    def test_auto_dismiss(self):
        m = self._to_done()
        for _ in range(DONE_TIMEOUT_S - 1):
            m.handle(Event.TICK)
            self.assertEqual(m.state, State.DONE)
        eff = m.handle(Event.TICK)
        self.assertEqual(m.state, State.EXIT)
        self.assertEqual(kinds(eff), ["Clear", "ReleaseFocus"])


class DoneAlarm(unittest.TestCase):
    """DONE with a custom sound name and no timeout: the looping timer alarm."""

    def _to_done(self, **kw):
        m = TimerMachine(default_set_s=1, **kw)
        m.start(); m.handle(Event.PRESS_SHORT); m.handle(Event.TICK)
        assert m.state == State.DONE
        return m

    def test_done_sound_is_configurable(self):
        m = TimerMachine(default_set_s=1, done_sound="timer")
        m.start(); m.handle(Event.PRESS_SHORT)
        eff = m.handle(Event.TICK)                 # RUNNING -> DONE
        self.assertEqual(eff[1], Beep("timer"))

    def test_zero_timeout_never_auto_dismisses(self):
        m = self._to_done(done_sound="timer", done_timeout_s=0)
        for _ in range(DONE_TIMEOUT_S * 3):        # well past the old 60 s cap
            m.handle(Event.TICK)
            self.assertEqual(m.state, State.DONE)  # still ringing

    def test_looping_alarm_repaints_and_resounds_each_interval(self):
        # with no timeout, each re-beep also re-Renders to keep the slot alive
        m = self._to_done(done_sound="timer", done_timeout_s=0)
        for _ in range(DONE_REBEEP_S - 1):
            self.assertEqual(m.handle(Event.TICK), [])
        eff = m.handle(Event.TICK)                 # the interval tick
        self.assertEqual(kinds(eff), ["Render", "Beep"])
        self.assertEqual(eff[1], Beep("timer"))

    def test_press_stops_the_looping_alarm(self):
        m = self._to_done(done_sound="timer", done_timeout_s=0)
        m.handle(Event.TICK); m.handle(Event.TICK)
        eff = m.handle(Event.PRESS_SHORT)          # encoder cancel
        self.assertEqual(m.state, State.EXIT)
        self.assertEqual(kinds(eff), ["Clear", "ReleaseFocus"])

    def test_timed_alarm_repaints_each_interval_then_dismisses(self):
        # a positive timeout still repaints each interval (so the DONE slot
        # outlives its TTL for the whole ring), then auto-dismisses at the cap
        m = self._to_done(done_sound="timer", done_timeout_s=9)
        for _ in range(DONE_REBEEP_S - 1):
            m.handle(Event.TICK)
        self.assertEqual(kinds(m.handle(Event.TICK)), ["Render", "Beep"])
        while m.state == State.DONE:
            eff = m.handle(Event.TICK)
        self.assertEqual(kinds(eff), ["Clear", "ReleaseFocus"])


class Owner(unittest.TestCase):
    """The owner rides out on the DONE Beep. The pure machine just carries the
    string the daemon stamped -- it attaches no meaning and default is None."""

    def _to_done(self, m):
        m.start(); m.handle(Event.PRESS_SHORT)
        return m.handle(Event.TICK)                # RUNNING -> DONE

    def test_owner_defaults_to_none_on_the_beep(self):
        m = TimerMachine(default_set_s=1)
        eff = self._to_done(m)
        self.assertEqual(eff[1], Beep("done"))     # Beep("done") == Beep("done", owner=None)
        self.assertIsNone(eff[1].owner)

    def test_stamped_owner_travels_on_the_beep(self):
        m = TimerMachine(default_set_s=1)
        m.owner = "John"                           # what the daemon stamps at start
        eff = self._to_done(m)
        self.assertEqual(eff[1], Beep("done", owner="John"))

    def test_owner_also_rides_the_rebeep(self):
        m = TimerMachine(default_set_s=1, done_timeout_s=0)
        m.owner = "Maja"
        self._to_done(m)
        for _ in range(DONE_REBEEP_S - 1):
            m.handle(Event.TICK)
        eff = m.handle(Event.TICK)                 # the interval re-beep
        self.assertEqual(eff[1], Beep("done", owner="Maja"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
