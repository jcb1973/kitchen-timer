#!/usr/bin/env python3
"""timerd — the kitchen-timer daemon.

Owns the whole timer: it holds one TimerMachine session, drives a 1 Hz tick, and
executes the machine's effects against matrixd (paint) and the configured beep
sink -- buzzerd (piezo) and/or audiod (USB chime), chosen by `beep_sink`. The
rotary knob stays dumb -- encoderd just forwards events to this HTTP API:

    POST /start          begin a session (encoderd calls this when TIMER is picked)
    POST /input {event}  feed one event: rotate_cw|rotate_ccw|press_short|press_long
    GET  /status         {active, focus, state, seconds} -- how encoderd learns
                         when a tick-driven exit (idle/auto-dismiss) handed the knob back
    GET  /health

Runs on kitchen-pi next to matrixd; no GPIO of its own (the buzzer is reached
over HTTP via buzzerd), so it develops and tests fine on any machine.
"""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
from clients import AudioClient, BuzzerClient, MatrixClient, render_screen
from statemachine import (
    Beep, Clear, Event, Render, ReleaseFocus, State, TimerMachine,
)

log = logging.getLogger("timerd")


class TimerDaemon:
    NAME = "timer"        # matrixd slot owner name
    LAYER = "invoked"     # a person opened it -> invoked layer

    def __init__(self, cfg, matrix, buzzer, audio=None):
        self.cfg = cfg
        self.matrix = matrix
        self.buzzer = buzzer
        self.audio = audio
        # DONE beep routing, chosen in config. Default "buzzer" keeps the piezo
        # as timerd's beeper (audiod's "the piezo stays" rule); "audio" swaps in
        # the USB chime; "both" fires each for redundancy.
        self.beep_sinks = self._select_beep_sinks(cfg, buzzer, audio)
        self.lock = threading.Lock()       # guards self.machine
        self.machine = None                # active TimerMachine, or None
        self._stop = threading.Event()
        self._tick = threading.Thread(target=self._tick_loop, name="tick", daemon=True)

    @staticmethod
    def _select_beep_sinks(cfg, buzzer, audio):
        """Map cfg.beep_sink -> the list of clients a Beep effect drives. Each
        is a client exposing beep(pattern) (BuzzerClient / AudioClient). Unknown
        values fall back to the buzzer so a config typo can't go silent."""
        sink = (getattr(cfg, "beep_sink", "buzzer") or "buzzer").lower()
        table = {"buzzer": [buzzer], "audio": [audio], "both": [buzzer, audio]}
        chosen = table.get(sink)
        if chosen is None:
            log.warning("unknown beep_sink %r; using buzzer", sink)
            chosen = [buzzer]
        sinks = [s for s in chosen if s is not None]
        if not sinks:                       # e.g. beep_sink=audio with no audio client
            log.warning("beep_sink %r has no usable client; using buzzer", sink)
            sinks = [buzzer]
        return sinks

    # -- lifecycle --------------------------------------------------------
    def start_background(self):
        self._tick.start()

    def stop(self):
        self._stop.set()

    def _tick_loop(self):
        # wait() returns True when stopped, False on the 1 s timeout -> a tick
        while not self._stop.wait(1.0):
            self._tick_once()

    # -- session control (also the unit-test surface) --------------------
    def start_session(self):
        with self.lock:
            self.machine = TimerMachine(self.cfg.default_set_s)
            effects = self.machine.start()
        self._run_effects(effects)
        return self.status()

    def feed(self, event: Event):
        """Apply one input event. Returns status, or None if no session."""
        with self.lock:
            if self.machine is None:
                return None
            effects = self.machine.handle(event)
            exited = self.machine.state == State.EXIT
            if exited:
                self.machine = None
        self._run_effects(effects)
        return self.status()

    def _tick_once(self):
        with self.lock:
            if self.machine is None:
                return
            effects = self.machine.handle(Event.TICK)
            if self.machine.state == State.EXIT:
                self.machine = None
        self._run_effects(effects)

    def status(self):
        with self.lock:
            m = self.machine
            if m is None:
                return {"active": False, "focus": False, "state": "idle"}
            seconds = m.remaining_s if m.state == State.RUNNING else m.duration_s
            return {"active": True, "focus": True, "state": m.state.value, "seconds": seconds}

    # -- effect execution (network I/O, done outside the lock) -----------
    def _run_effects(self, effects):
        for e in effects:
            if isinstance(e, Render):
                self.matrix.screen(self.NAME, self.LAYER, render_screen(e))
            elif isinstance(e, Beep):
                for sink in self.beep_sinks:         # buzzerd and/or audiod; logs if no url set
                    sink.beep(e.pattern)
            elif isinstance(e, Clear):
                self.matrix.clear(self.NAME)
            elif isinstance(e, ReleaseFocus):
                pass  # reflected by machine=None; encoderd sees it via /status or the /input reply


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        token = self.server.daemon.cfg.listen_token
        return not token or self.headers.get("X-Auth-Token") == token

    def _json_body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def do_GET(self):
        d = self.server.daemon
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "timerd"})
        elif self.path == "/status":
            self._send(200, d.status())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        d = self.server.daemon
        if not self._authed():
            return self._send(401, {"error": "bad or missing X-Auth-Token"})
        if self.path == "/start":
            self._send(200, d.start_session())
        elif self.path == "/input":
            body = self._json_body()
            if body is None or "event" not in body:
                return self._send(400, {"error": 'expected {"event": ...}'})
            try:
                event = Event(body["event"])
            except ValueError:
                return self._send(400, {"error": "unknown event", "event": body["event"]})
            status = d.feed(event)
            if status is None:
                return self._send(409, {"error": "no active session; POST /start first"})
            self._send(200, status)
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass  # keep stdlib's per-request stderr noise out of the log


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s timerd %(levelname)s %(message)s")
    cfg = config.load()
    daemon = TimerDaemon(
        cfg,
        MatrixClient(cfg.matrix_url, cfg.matrix_token),
        BuzzerClient(cfg.buzzer_url, cfg.buzzer_token),
        AudioClient(cfg.audio_url, cfg.audio_token),
    )
    daemon.start_background()
    server = ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), Handler)
    server.daemon = daemon
    log.info("timerd on %s:%d  matrix=%s  buzzer=%s  audio=%s  beep_sink=%s",
             cfg.listen_host, cfg.listen_port, cfg.matrix_url,
             cfg.buzzer_url or "STUB", cfg.audio_url or "STUB", cfg.beep_sink)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop()
        server.server_close()


if __name__ == "__main__":
    main()
