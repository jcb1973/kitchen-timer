# CLAUDE.md

Guidance for Claude Code working in this repo. Read `README.md` first for what
the timer does and its HTTP API.

## What this is

`timerd` — a rotary-knob countdown timer for the kitchen LED sign, running on
kitchen-pi next to `matrixd`. Pure-Python, **stdlib only** (no third-party deps).

## Architecture (keep this shape)

- **`statemachine.py` is pure** — events in, effect objects out, no I/O. All
  timer logic lives here and is unit-tested without hardware. Do not add
  network/GPIO/time calls to it; if behaviour changes, add a test.
- **`timerd.py`** owns the session + 1 Hz tick and executes effects.
- **`clients.py`** is the only place that talks to matrixd/buzzerd.
- The **encoder stays dumb**: encoderd (kitchen-sign repo) forwards raw events
  to `POST /input`; all intelligence is here. Don't push timer logic into encoderd.
- The **buzzer is a shared resource with its own owner** (`buzzerd`), not owned
  by the timer — timerd is just a client. Never drive the buzzer GPIO from here.

## Both seams are wired now

- `BuzzerClient.beep()` is live on kitchen-pi (**buzzerd**, `:8084`, wired
  2026-07-17 — config-only, no code change). With no `[buzzer] url` — dev
  machines — it logs instead of beeping; that fallback is intended, not a stub
  to remove, and the `STUB` wording in that log line is historical.
  `{"pattern": "done"}` is the contract buzzerd was built to accept: buzzerd
  follows this client, not the reverse.
- **encoderd** (kitchen-sign repo) has its TIMER menu entry: it POSTs `/start`,
  forwards events to `/input`, and reclaims the menu on `focus: false`.

## The DONE sound is a config-selected sink

`beep_sink` (`[timer]`) picks who plays the DONE beep: `buzzer` (default, the
piezo via buzzerd), `audio` (a richer chime via **audiod**, `:8085`), or `both`.
`BuzzerClient` and `AudioClient` share one `beep(pattern)` interface, so the
daemon just fans the `Beep` effect out to the selected sinks (`_select_beep_sinks`
in `timerd.py`); an unknown sink, or `audio` with no client, falls back to the
buzzer rather than going silent. Keep the default `buzzer`: it honours audiod's
"the piezo stays timerd's beeper" rule, with audio strictly opt-in. audiod owns
the USB card — POST to it, never open the card here (same discipline as never
driving the buzzer GPIO). `AudioClient` sends `{"sound": name}`; the *name* is
the shared house vocabulary, only the wire key differs from buzzerd's
`{"pattern": name}`.

The DONE **sound name and ring duration are also config**, kept out of the pure
state machine: `TimerMachine(done_sound=..., done_timeout_s=...)`. It emits
`Beep(self.done_sound)` (default `"done"`), and `done_timeout_s <= 0` turns the
DONE beep into a **loop that rings until the knob acknowledges it** — no
auto-dismiss — re-Rendering each `DONE_REBEEP_S` interval so matrixd's DONE slot
never expires. kitchen-pi runs `done_sound=timer` (audiod's speaker-only spoken
"Timer done!" clip, ~1.9 s < the 3 s re-beep so it never self-truncates) +
`beep_sink=audio` + `done_timeout_s=0`. That is an audio-only alarm: `timer` has
no buzzerd counterpart, so `done_sound=timer` must go with `beep_sink=audio`.
Changing this behaviour → add a test (the pure machine is the seam).

The matrixd `/screen` schema is **confirmed** and `render_screen()` emits real
fields (`static` MM:SS repainted per second; `flash` for DONE). We intentionally
avoid matrixd's native `countdown` mode — its remaining-time format is coarse.
`render_screen()` is still the one place that knows the wire format.

## Owner attribution (best-effort, capture-at-interaction)

The timer records **who started it**. At the START transition only (the press
confirming `SET → RUNNING`, detected in `TimerDaemon.feed` — not each dial tick,
not completion), timerd asks **recogniserd** (`RecogniserClient`, `:8088`) who's
at the knob and stamps `machine.owner`. Principle: recognise at the *interaction
moment*, never re-capture at completion (the person has gone by then).

Keep this shape:
- **The pure machine stays pure.** `statemachine.py` only *stores* `owner` (a
  string or None) and passes it out on the DONE `Beep(owner=...)`; it attaches no
  meaning. The recogniser **call is I/O and lives in the daemon**, never in the
  machine. Don't move the network call into `statemachine.py`.
- **Off the knob's critical path.** Capture runs on a background thread
  (`_begin_owner_capture`), so the start press paints RUNNING and returns at once.
  Owner is only needed at completion, seconds later.
- **Wholly best-effort.** `RecogniserClient.who()` never raises (no url → disabled
  like buzzer/audio; any failure → None), and `_capture_owner` guards on top. A
  recognition miss must never touch timer logic. `owner=None` → completion is
  unchanged.
- **Completion is owner-aware but audiod-agnostic.** `_beep_pattern` maps
  `owner → sound name` via `cfg.owner_sounds` (`[owner_sounds]`, case-insensitive).
  audiod has **no runtime TTS** — it plays pre-built named clips — so a spoken
  per-person greeting is a Piper asset added to audiod's vocabulary and mapped
  here, not synthesised at runtime. Empty map (default) → `done_sound` for all.
- **Recognition reality:** only John is reliably recognised today; Maja/Gosia
  thresholds are untuned (kitchen-recogniser), so attribution is John-only for now.

## Conventions (shared across the Pi ecosystem)

- Secrets/config in `.creds` (INI, gitignored, mode 600); see `.creds.example`.
- Per-project `.venv` on the Pi; cron/systemd call it explicitly (not that
  timerd needs deps today, but keep the pattern).
- **Deploy** = `git push` from the Mac, `git pull` on kitchen-pi. No CI/CD.
- Live servers: read-only inspection is fine, but **ask before changing state**
  on kitchen-pi (deploy, restart, config edits).
- When timerd lands on kitchen-pi, update the kitchen-pi section of
  `infra/README.md` in the same session.

## Test / run

- `python3 -m unittest -v` — 24 tests, no hardware, no network.
- `TIMER_PORT=8099 TIMER_DEFAULT_SET_S=60 python3 timerd.py` then curl the API
  (matrixd unreachable just logs best-effort warnings; the timer still runs).
