"""Daemon wiring tests — fake clients, no network, no threads, no sleep.

Drives TimerDaemon.start_session / feed / _tick_once directly and asserts the
right calls reach matrixd and buzzerd. Run: python3 -m unittest -v
"""
import unittest

import config
from statemachine import Event, State
from timerd import TimerDaemon


class FakeMatrix:
    def __init__(self):
        self.screens = []   # list of draw dicts
        self.clears = []

    def screen(self, name, layer, draw):
        self.screens.append(draw)

    def clear(self, name):
        self.clears.append(name)


class FakeBuzzer:
    def __init__(self):
        self.beeps = []

    def beep(self, pattern):
        self.beeps.append(pattern)


class FakeRecogniser:
    """Stands in for RecogniserClient. Returns a preset name (or None), counting
    who() calls; `raises=True` simulates a client that blows up mid-call."""
    def __init__(self, name=None, raises=False):
        self.name = name
        self.raises = raises
        self.calls = 0

    def who(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("recogniser exploded")
        return self.name


class SyncCaptureDaemon(TimerDaemon):
    """TimerDaemon whose owner capture runs synchronously (no thread), so tests
    are deterministic. Exercises the real feed -> _capture_owner wiring."""
    def _begin_owner_capture(self, machine):
        if self.recogniser is None:
            return
        self._capture_owner(machine)


def make(default_set_s=60):
    cfg = config.Config(default_set_s=default_set_s)
    m, b = FakeMatrix(), FakeBuzzer()
    return TimerDaemon(cfg, m, b), m, b


class DaemonWiring(unittest.TestCase):
    def test_start_paints_set_screen(self):
        d, m, _ = make(default_set_s=300)
        st = d.start_session()
        self.assertEqual(st, {"active": True, "focus": True, "state": "set", "seconds": 300})
        self.assertEqual(m.screens[-1]["text"], "SET|5:00")

    def test_rotate_repaints(self):
        d, m, _ = make(default_set_s=300)
        d.start_session()
        d.feed(Event.ROTATE_CW)
        self.assertEqual(m.screens[-1]["text"], "SET|5:15")

    def test_start_then_run_to_done_beeps(self):
        d, m, b = make(default_set_s=2)
        d.start_session()
        d.feed(Event.PRESS_SHORT)                 # SET -> RUNNING (2s)
        self.assertEqual(m.screens[-1]["text"], "0:02")
        d._tick_once()                            # 2 -> 1
        self.assertEqual(m.screens[-1]["text"], "0:01")
        d._tick_once()                            # 1 -> 0 -> DONE
        self.assertEqual(b.beeps, ["done"])       # the seam fired
        self.assertEqual(m.screens[-1]["text"], "DONE")

    def test_cancel_at_zero_clears_and_releases(self):
        d, m, _ = make(default_set_s=0)
        d.start_session()
        st = d.feed(Event.PRESS_SHORT)            # 0:00 sentinel -> cancel
        self.assertEqual(m.clears, ["timer"])
        self.assertFalse(st["active"])

    def test_feed_without_session_is_none(self):
        d, _, _ = make()
        self.assertIsNone(d.feed(Event.ROTATE_CW))

    def test_press_stops_running_and_clears(self):
        d, m, _ = make(default_set_s=300)
        d.start_session()
        d.feed(Event.PRESS_SHORT)                 # running
        st = d.feed(Event.PRESS_SHORT)            # stop
        self.assertEqual(m.clears, ["timer"])
        self.assertFalse(st["active"])

    def test_done_rebeeps_while_unacknowledged(self):
        d, m, b = make(default_set_s=1)
        d.start_session()
        d.feed(Event.PRESS_SHORT)
        d._tick_once()                            # -> DONE (+1 beep)
        for _ in range(3):
            d._tick_once()                        # 3 s in DONE -> one more beep
        self.assertEqual(len(b.beeps), 2)


class BeepSinkRouting(unittest.TestCase):
    """beep_sink picks which client(s) a DONE Beep drives. FakeBuzzer stands in
    for both BuzzerClient and AudioClient -- they share the beep() interface."""

    def _run_to_done(self, sink):
        cfg = config.Config(default_set_s=1, beep_sink=sink)
        m, buzzer, audio = FakeMatrix(), FakeBuzzer(), FakeBuzzer()
        d = TimerDaemon(cfg, m, buzzer, audio)
        d.start_session()
        d.feed(Event.PRESS_SHORT)                 # SET -> RUNNING (1s)
        d._tick_once()                            # 1 -> 0 -> DONE (+1 beep)
        return buzzer, audio

    def test_default_is_buzzer_only(self):
        buzzer, audio = self._run_to_done("buzzer")
        self.assertEqual(buzzer.beeps, ["done"])
        self.assertEqual(audio.beeps, [])

    def test_audio_routes_to_audiod_only(self):
        buzzer, audio = self._run_to_done("audio")
        self.assertEqual(buzzer.beeps, [])
        self.assertEqual(audio.beeps, ["done"])

    def test_both_fires_each(self):
        buzzer, audio = self._run_to_done("both")
        self.assertEqual(buzzer.beeps, ["done"])
        self.assertEqual(audio.beeps, ["done"])

    def test_unknown_sink_falls_back_to_buzzer(self):
        buzzer, audio = self._run_to_done("trumpet")
        self.assertEqual(buzzer.beeps, ["done"])
        self.assertEqual(audio.beeps, [])

    def test_audio_sink_without_client_falls_back_to_buzzer(self):
        # beep_sink=audio but no AudioClient supplied -> don't go silent.
        cfg = config.Config(default_set_s=1, beep_sink="audio")
        m, buzzer = FakeMatrix(), FakeBuzzer()
        d = TimerDaemon(cfg, m, buzzer)           # no audio arg
        d.start_session()
        d.feed(Event.PRESS_SHORT)
        d._tick_once()
        self.assertEqual(buzzer.beeps, ["done"])


class OwnerAttribution(unittest.TestCase):
    """Best-effort owner capture: recognise WHO at the start press, bind it to
    the timer, use it at completion. A miss or a failure must never break the
    timer (the countdown runs exactly as it does today)."""

    def _daemon(self, recog, default_set_s=1, owner_sounds=None):
        cfg = config.Config(default_set_s=default_set_s,
                            owner_sounds=owner_sounds or {})
        m, b = FakeMatrix(), FakeBuzzer()
        d = SyncCaptureDaemon(cfg, m, b, recogniser=recog)
        return d, m, b

    def _run_to_done(self, d):
        d.start_session()
        d.feed(Event.PRESS_SHORT)     # SET -> RUNNING (start transition)
        d._tick_once()                # 1 -> 0 -> DONE (+beep)

    def test_owner_is_captured_at_the_start_press(self):
        recog = FakeRecogniser(name="John")
        d, _, _ = self._daemon(recog)
        d.start_session()
        self.assertEqual(recog.calls, 0)              # SET does not capture
        d.feed(Event.ROTATE_CW)
        self.assertEqual(recog.calls, 0)              # dialing does not capture
        d.feed(Event.PRESS_SHORT)                     # the confirming press
        self.assertEqual(recog.calls, 1)              # captured exactly once
        self.assertEqual(d.machine.owner, "John")     # stamped on the timer state

    def test_owner_selects_a_per_person_done_sound(self):
        recog = FakeRecogniser(name="John")
        d, _, b = self._daemon(recog, owner_sounds={"John": "timer_john"})
        self._run_to_done(d)
        self.assertEqual(b.beeps, ["timer_john"])     # owner-aware DONE sound

    def test_owner_lookup_is_case_insensitive(self):
        recog = FakeRecogniser(name="John")
        d, _, b = self._daemon(recog, owner_sounds={"john": "timer_john"})
        self._run_to_done(d)
        self.assertEqual(b.beeps, ["timer_john"])

    def test_owner_without_a_configured_sound_uses_the_default(self):
        recog = FakeRecogniser(name="Gosia")          # recognised, but no mapping
        d, _, b = self._daemon(recog, owner_sounds={"John": "timer_john"})
        self._run_to_done(d)
        self.assertEqual(b.beeps, ["done"])           # falls back to done_sound

    def test_no_recognition_falls_back_unchanged(self):
        recog = FakeRecogniser(name=None)             # nobody recognised
        d, _, b = self._daemon(recog, owner_sounds={"John": "timer_john"})
        self._run_to_done(d)
        self.assertIsNone(d.machine.owner if d.machine else None)
        self.assertEqual(b.beeps, ["done"])

    def test_a_recogniser_exception_never_aborts_the_timer(self):
        recog = FakeRecogniser(raises=True)
        d, m, b = self._daemon(recog, owner_sounds={"John": "timer_john"})
        self._run_to_done(d)                          # must not raise
        self.assertEqual(b.beeps, ["done"])           # timer still fired
        self.assertEqual(m.screens[-1]["text"], "DONE")

    def test_no_recogniser_client_disables_capture(self):
        # the default daemon (no recogniser arg) never attributes and runs as before.
        d, _, b = make(default_set_s=1)
        self.assertIsNone(d.recogniser)
        d.start_session(); d.feed(Event.PRESS_SHORT); d._tick_once()
        self.assertEqual(b.beeps, ["done"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
