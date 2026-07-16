# kitchen-timer

A rotary-knob **countdown timer** for the kitchen LED sign. Runs as `timerd` on
**kitchen-pi**, next to `matrixd`. The knob (an EC11 encoder, owned by
`encoderd`) sets a duration in 30-second steps; the countdown shows on the LED
panel; at zero it flashes `DONE` and beeps.

Part of the kitchen-pi *device brain* (see `infra/README.md` and
`infra/CONVENTIONS.md`): jcb-pi does the *summoning* (producers make content),
kitchen-pi owns the *physical surfaces* and the latency-sensitive interactions.
The timer is one of those, so it lives entirely on kitchen-pi with no jcb-pi
round-trip.

## How it behaves

```
Menu (encoderd) ──pick TIMER──▶ SET ──press──▶ RUNNING ──hits 0──▶ DONE ──press/timeout──▶ Clock
```

- **SET** — rotate = duration in **30 s steps** (capped 60:00, floored 0:00).
  Panel shows the candidate (`5:00`). **Short-press = start.**
  Dial down to **`0:00` = CANCEL** (a sentinel, mirroring matrixd's
  "empty → clear to baseline" menu entry). **Long-press = bail** from anywhere.
  Wander off and SET **auto-releases after 20 s**.
- **RUNNING** — panel ticks `MM:SS`. Short/long press stops and dismisses.
  (Rotate is ignored for now; add-time is a v2 idea.)
- **DONE** — flashes `DONE` and **beeps**, re-beeping every 3 s until a press
  acknowledges it or it **auto-dismisses after 60 s**, revealing the clock.

## Design

Two pieces, split so the interaction is testable with no hardware:

- **`statemachine.py`** — a *pure* state machine: semantic events in
  (`rotate_cw/ccw`, `press_short/long`, `tick`), semantic **effects** out
  (`Render`, `Beep`, `Clear`, `ReleaseFocus`). No I/O. Fully unit-tested.
- **`timerd.py`** — the daemon: holds one session, drives a 1 Hz tick, and
  executes effects against **matrixd** (paint) and **buzzerd** (beep) via
  `clients.py`.

The knob stays **dumb**: `encoderd` just forwards raw events to timerd's HTTP
API and takes its menu back when timerd releases focus. All timer intelligence
lives here.

## HTTP API (localhost)

| Method + path | Purpose |
|---|---|
| `POST /start` | Begin a session — `encoderd` calls this when TIMER is picked. |
| `POST /input` `{"event": "..."}` | Feed one event: `rotate_cw`, `rotate_ccw`, `press_short`, `press_long`. |
| `GET /status` | `{active, focus, state, seconds}` — how encoderd learns a tick-driven exit (idle / auto-dismiss) handed the knob back. |
| `GET /health` | Liveness. |

Writes are optionally guarded by `X-Auth-Token` (set `[timer] token` in `.creds`).
Binds `127.0.0.1` by default.

## Run

```sh
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt   # stdlib only; nothing to install
cp .creds.example .creds && chmod 600 .creds        # set the matrix token
python3 timerd.py
```

Tests (no hardware, no network): `python3 -m unittest -v`

The **matrixd `/screen` schema is confirmed** (against matrixd.py in
kitchen-sign): `render_screen()` emits real fields — `mode: static` MM:SS
repainted every second, and `mode: flash` for DONE so matrixd blinks it. We
deliberately do *not* use matrixd's native `countdown` mode: its `fmt_remaining`
is coarse (`4m` until the last minute), and a kitchen timer wants true seconds.

## What's left to wire

1. **buzzerd** — the beep. `BuzzerClient` is stubbed: with no `[buzzer] url` it
   logs what it *would* play. When the shared **buzzerd** beep service exists
   (owns the GPIO-9 piezo, `POST /beep {pattern}`), set its url in `.creds` and
   the DONE beep starts working — no code change.
2. **encoderd** (in the kitchen-sign repo): add a TIMER menu entry that POSTs
   `/start`, forwards knob events to `/input`, and reclaims the menu when
   `/status` reports `focus: false`. Not in this repo.

A live on-panel test (writing to the real sign) is the natural next check once
one of those is in place — it changes what the kitchen sign shows, so it needs
John's go-ahead.

## Deploy (kitchen-pi)

Not deployed yet. When ready: `git pull` on kitchen-pi + a systemd unit
(`timerd.service`, user with no special privileges — timerd has no GPIO of its
own). Then add the `timerd` line to `infra/README.md` under kitchen-pi.
