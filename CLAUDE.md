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

The matrixd `/screen` schema is **confirmed** and `render_screen()` emits real
fields (`static` MM:SS repainted per second; `flash` for DONE). We intentionally
avoid matrixd's native `countdown` mode — its remaining-time format is coarse.
`render_screen()` is still the one place that knows the wire format.

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
