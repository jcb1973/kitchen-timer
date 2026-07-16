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
        self.assertEqual(m.screens[-1]["text"], "SET|5:30")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
