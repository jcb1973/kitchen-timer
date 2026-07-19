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

- **SET** — rotate = duration in **15 s steps** (capped 60:00, floored 0:00).
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

## The beep

**buzzerd** (its own repo, `127.0.0.1:8084` on kitchen-pi) owns the piezo; timerd
is just a client. At zero `BuzzerClient` POSTs `{"pattern": "done"}` and re-sends
it every 3 s until the knob is pressed — it does *not* call `/stop` on acknowledge,
which is why buzzerd's patterns are all finite. Wiring was config-only (`[buzzer]
url` + `token` in `.creds`, done 2026-07-17). With no url set — any dev machine —
the same call just logs what it would play.

**Which device makes the DONE sound is a config choice** — `beep_sink` in
`[timer]`: `buzzer` (default, the piezo), `audio` (a richer chime on the USB
speaker via **audiod**, `127.0.0.1:8085`), or `both`. audiod is buzzerd's sibling
(one owner per device) and speaks the same sound *names*, so `AudioClient` POSTs
`{"sound": "done"}` — the same house name, a different wire key. The default stays
`buzzer` so the piezo remains timerd's beeper unless you opt in; `audio` needs
`[audio] url` set (empty → logged, like the buzzer).

**What the DONE sound is, and how long it rings**, are also `[timer]` config —
`done_sound` and `done_timeout_s`. `done_sound=timer` plays audiod's spoken
"Timer done!" clip (speaker-only, so pair it with `beep_sink=audio`), and
`done_timeout_s=0` makes the alarm **loop every 3 s until the knob acknowledges
it** — a timer that rings until you turn it off — instead of the default 60 s
auto-dismiss. The state machine stays generic: it emits `Beep(done_sound)` and,
when looping, re-Renders each interval so matrixd's DONE slot never expires.

## Who started it (best-effort attribution)

When a timer **starts** (the press that confirms `SET → RUNNING`), timerd asks
**recogniserd** (`:8088`, `GET /who?frames=5&window=2s`) who's at the knob and
binds that name to the timer as its `owner`. The principle is **capture at the
interaction moment** — never re-recognise at completion, because by then the
person has walked away. It rides out on the DONE `Beep` and the completion can
use it.

Everything here is **best-effort and off the critical path**:

- The recogniser call runs on a **background thread**, so the start press paints
  `RUNNING` and returns instantly — the knob never waits on a camera burst.
- `RecogniserClient` never raises: no url → disabled (logged), and any failure
  (unreachable, timeout, nobody recognised) → `owner = None`. A recognition miss
  can't break a timer.
- With `owner = None` the DONE announcement is **exactly today's behaviour**.

The **completion announcement** is owner-aware via `[owner_sounds]` in `.creds`
(owner name → an audiod sound *name*). audiod plays pre-built named clips (it has
**no runtime TTS**), so a spoken per-person greeting like *"Done, John!"* is a
Piper-built asset added to audiod's vocabulary and then mapped here; until that
asset exists the map is empty and everyone gets `done_sound`. The owner is always
logged regardless. Configured in `[recogniser]` + `[owner_sounds]`; see
`.creds.example`.

> **Recognition reality:** only **John** is reliably recognised today (Maja's and
> Gosia's classifier thresholds aren't tuned yet), so attribution is John-only
> until recogniserd is tuned. See the kitchen-recogniser repo.

## The knob

**encoderd** (kitchen-sign repo) drives this and is live: **TIMER** on the knob
menu is a local action, not a summond producer — it POSTs `/start`, forwards
rotate/press to `/input`, and reclaims the menu when `/status` reports
`focus: false`. Nothing in this repo talks to the encoder directly.

The full chain — knob → timerd → matrixd (paint) + buzzerd (beep) — has been
verified live on the sign.

## Deploy (kitchen-pi)

Runs as **`timerd.service`** (systemd, user `jcb1973`, no privileges — no GPIO of
its own; the buzzer is reached over HTTP via buzzerd). The unit is symlinked
into the checkout, matching matrixd/encoderd:

```sh
# deploy: git push here, git pull on the Pi. The Pi's ~/kitchen-timer is a
# git checkout of this public repo (https remote, no deploy key needed):
ssh jcb1973@kitchen-pi.local 'cd ~/kitchen-timer && git pull --ff-only && sudo systemctl restart timerd'
# one-time install (unit symlinked from the checkout, like matrixd/encoderd):
sudo ln -sf /home/jcb1973/kitchen-timer/timerd.service /etc/systemd/system/timerd.service
sudo systemctl daemon-reload && sudo systemctl enable --now timerd
```

`.creds` on the Pi binds `127.0.0.1` (the knob/encoderd is the only client), so
`timerctl` must run **on the Pi** (its default host `kitchen-pi.local` won't reach
the loopback socket — use `TIMER_HOST=127.0.0.1`, or bind `0.0.0.0` temporarily
to drive it from the Mac). Recorded in `infra/README.md` under kitchen-pi.
