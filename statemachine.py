"""
Pure countdown-timer state machine for kitchen-timer (timerd).

No I/O, no hardware, no network: it consumes semantic input events plus a 1 Hz
TICK and emits semantic *effects* (render this screen / beep / clear the slot /
release the knob). The daemon (timerd.py) is what actually executes those
effects against matrixd and, later, buzzerd. Keeping the logic pure makes the
whole interaction unit-testable on any machine, with no encoder, panel, or
buzzer present.

Interaction model (see README). The rotary encoder is owned by encoderd; when a
person selects TIMER from the knob menu, encoderd forwards its events here:

    ROTATE_CW / ROTATE_CCW   dial the duration in 30 s steps
    PRESS_SHORT              start  (or, when the dial reads 0:00, cancel)
    PRESS_LONG               bail out from anywhere
    TICK                     1 Hz, supplied by the daemon

States: SET -> RUNNING -> DONE, with EXIT as the terminal "release the knob".
The buzzer is not built yet, so DONE emits a Beep effect that the daemon
currently only logs; the visual DONE screen is the real, permanent behaviour and
the buzzerd call slots in beside it later.
"""
from dataclasses import dataclass
from enum import Enum


class State(Enum):
    SET = "set"
    RUNNING = "running"
    DONE = "done"
    EXIT = "exit"


class Event(Enum):
    ROTATE_CW = "rotate_cw"
    ROTATE_CCW = "rotate_ccw"
    PRESS_SHORT = "press_short"
    PRESS_LONG = "press_long"
    TICK = "tick"


# --- effects the daemon executes -------------------------------------------

@dataclass(frozen=True)
class Render:
    """Paint a timer screen. `kind` is set|running|done; `seconds` is the value
    to show (the candidate duration in SET, the remaining time in RUNNING). DONE
    is posted once and matrixd's own `flash` mode blinks it, so there's no
    per-frame flashing to drive from here."""
    kind: str
    seconds: int = 0


@dataclass(frozen=True)
class Beep:
    """Ask buzzerd for a named beep pattern. The buzzer isn't wired yet, so the
    daemon only logs this for now; it becomes a buzzerd POST later."""
    pattern: str = "done"


@dataclass(frozen=True)
class Clear:
    """Pop the timer's matrixd slot, revealing whatever is beneath (the clock)."""


@dataclass(frozen=True)
class ReleaseFocus:
    """Timer is finished with the knob; encoderd takes its menu back."""


# --- tunables ---------------------------------------------------------------

STEP_S = 30                 # one rotary detent = 30 seconds
MAX_S = 60 * 60            # cap the dial at 60:00 so a fast spin can't run away
DEFAULT_SET_S = 5 * 60     # SET opens showing 5:00
SET_IDLE_TIMEOUT_S = 20    # abandon SET after 20 s with no human input
DONE_TIMEOUT_S = 60        # auto-dismiss the DONE screen after 60 s
DONE_REBEEP_S = 3          # re-beep every 3 s while DONE is unacknowledged


def _clamp(seconds: int) -> int:
    return max(0, min(MAX_S, seconds))


class TimerMachine:
    """One knob session's worth of timer. Construct, call start() to get the
    opening SET render, then feed handle(event) and act on the returned effects.
    """

    def __init__(self, default_set_s: int = DEFAULT_SET_S):
        self.state = State.SET
        self.duration_s = _clamp(default_set_s)
        self.remaining_s = 0
        self._idle_s = 0     # seconds since last human input, while in SET
        self._done_s = 0     # seconds since DONE was entered

    def start(self):
        """Effects for entering the timer (encoderd just handed us the knob)."""
        self.state = State.SET
        self._idle_s = 0
        return [Render("set", self.duration_s)]

    def handle(self, event: Event):
        if self.state == State.SET:
            return self._on_set(event)
        if self.state == State.RUNNING:
            return self._on_running(event)
        if self.state == State.DONE:
            return self._on_done(event)
        return []  # EXIT is inert; the daemon should have stopped feeding us

    # -- SET: dial the duration -------------------------------------------
    def _on_set(self, event: Event):
        if event == Event.ROTATE_CW:
            self._idle_s = 0
            self.duration_s = _clamp(self.duration_s + STEP_S)
            return [Render("set", self.duration_s)]
        if event == Event.ROTATE_CCW:
            self._idle_s = 0
            self.duration_s = _clamp(self.duration_s - STEP_S)
            return [Render("set", self.duration_s)]
        if event == Event.PRESS_SHORT:
            if self.duration_s == 0:
                return self._exit()            # 0:00 sentinel = cancel
            self.state = State.RUNNING
            self.remaining_s = self.duration_s
            return [Render("running", self.remaining_s)]
        if event == Event.PRESS_LONG:
            return self._exit()                # bail
        if event == Event.TICK:
            self._idle_s += 1
            if self._idle_s >= SET_IDLE_TIMEOUT_S:
                return self._exit()            # wandered off, give the knob back
            return []
        return []

    # -- RUNNING: count down ----------------------------------------------
    def _on_running(self, event: Event):
        if event == Event.TICK:
            self.remaining_s -= 1
            if self.remaining_s <= 0:
                self.remaining_s = 0
                self.state = State.DONE
                self._done_s = 0
                return [Render("done"), Beep("done")]
            return [Render("running", self.remaining_s)]
        if event in (Event.PRESS_SHORT, Event.PRESS_LONG):
            return self._exit()                # stop / dismiss a running timer
        # rotate while running is ignored for now (add-time is a v2 idea)
        return []

    # -- DONE: flash + beep until acknowledged or timed out ---------------
    def _on_done(self, event: Event):
        if event in (Event.PRESS_SHORT, Event.PRESS_LONG):
            return self._exit()                # acknowledge -> silence + clear
        if event == Event.TICK:
            self._done_s += 1
            if self._done_s >= DONE_TIMEOUT_S:
                return self._exit()            # give up waiting, reveal the clock
            if self._done_s % DONE_REBEEP_S == 0:
                return [Beep("done")]          # matrixd owns the flash; we re-beep
            return []
        return []

    def _exit(self):
        self.state = State.EXIT
        return [Clear(), ReleaseFocus()]
