"""HTTP clients for the devices timerd drives, plus the screen renderer.

  MatrixClient      -> matrixd     (paints the timer screen; owns nothing itself)
  BuzzerClient      -> buzzerd     (the piezo beep at zero); logs when no url set
  AudioClient       -> audiod      (a richer DONE chime on the USB speaker); logs
                                    when no url is set
  RecogniserClient  -> recogniserd (who started the timer, at the start press);
                                    best-effort, returns None on any miss

BuzzerClient and AudioClient share one `beep(pattern)` interface, so the daemon
can route the DONE Beep effect to either or both, chosen in config (the
`beep_sink` setting). Both speak the same house sound *names* (`done`, ...) --
buzzerd takes `{"pattern": name}`, audiod takes `{"sound": name}`; the transport
is a deployment choice, not a code one.

Stdlib only (urllib), so the daemon has zero third-party deps.
"""
import json
import logging
import urllib.error
import urllib.request

from statemachine import DONE_TIMEOUT_S, SET_IDLE_TIMEOUT_S

log = logging.getLogger("timerd.clients")


# --- screen rendering -------------------------------------------------------
#
# render_screen() maps a Render effect to matrixd's POST /screen payload. The
# schema (confirmed against matrixd.py in kitchen-sign): mode static|flash, a
# `text` picture ("|" splits lines), `font` (a name, or a per-line list),
# palette `color`, and `ttl`. This is the one place that knows the matrixd wire
# format; the state machine and daemon stay format-free.
#
# We drive a precise MM:SS by repainting each second (RUNNING), rather than
# matrixd's native `countdown` mode, whose fmt_remaining() is coarse ("4m" until
# the final minute). DONE is posted once as mode "flash" so matrixd blinks it.

# TTLs are derived from the state-machine timeouts so a slot can never expire
# out from under the state it represents. RUNNING is re-posted every second, so
# it only needs to outlive a single missed tick.
SET_TTL = SET_IDLE_TIMEOUT_S + 5
RUNNING_TTL = 5
DONE_TTL = DONE_TIMEOUT_S + 5


def _mmss(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def render_screen(effect) -> dict:
    """Map a Render(kind, seconds) effect -> matrixd draw payload (mode, text,
    font, color, ttl). name + layer are added by the daemon."""
    kind = effect.kind
    if kind == "done":
        return {"mode": "flash", "text": "DONE", "font": "huge", "color": "red", "ttl": DONE_TTL}
    if kind == "set":
        if effect.seconds == 0:                                   # 0:00 sentinel
            return {"mode": "static", "text": "CANCEL", "font": "big", "color": "red", "ttl": SET_TTL}
        return {"mode": "static", "text": f"SET|{_mmss(effect.seconds)}",
                "font": ["small", "huge"], "color": "amber", "ttl": SET_TTL}
    # running: big precise time, repainted every second
    return {"mode": "static", "text": _mmss(effect.seconds), "font": "huge", "color": "amber", "ttl": RUNNING_TTL}


# --- matrixd ----------------------------------------------------------------

class MatrixClient:
    def __init__(self, url: str, token: str = "", timeout: float = 2.0):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _post(self, path: str, payload: dict):
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Auth-Token"] = self.token   # matrixd guards writes with this
        req = urllib.request.Request(self.url + path, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status
        except (urllib.error.URLError, OSError) as e:
            # best-effort: a sign that won't paint must never crash the timer
            log.warning("matrixd %s failed: %s", path, e)
            return None

    def screen(self, name: str, layer: str, draw: dict):
        # draw already carries mode/text/font/color/ttl from render_screen()
        return self._post("/screen", {"name": name, "layer": layer, **draw})

    def clear(self, name: str):
        return self._post("/clear", {"name": name})


# --- buzzerd (the seam) -----------------------------------------------------

class BuzzerClient:
    """Calls buzzerd's POST /beep. With no url configured it only logs what it
    *would* play, which is how timerd runs on a dev machine; on kitchen-pi
    [buzzer] url points at buzzerd (127.0.0.1:8084) and the same call makes the
    noise. buzzerd owns the pin -- never drive the buzzer GPIO from here.

    The request shape below is the contract buzzerd accepts; it was built to
    match this client, so don't reshape it without changing buzzerd too."""

    def __init__(self, url: str = "", token: str = "", timeout: float = 2.0):
        self.url = (url or "").rstrip("/")
        self.token = token
        self.timeout = timeout

    def beep(self, pattern: str):
        if not self.url:
            log.info("buzzer STUB: would beep pattern=%r", pattern)
            return None
        data = json.dumps({"pattern": pattern}).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Auth-Token"] = self.token
        req = urllib.request.Request(self.url + "/beep", data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status
        except (urllib.error.URLError, OSError) as e:
            log.warning("buzzerd /beep failed: %s", e)
            return None


# --- audiod (the richer DONE sound) -----------------------------------------

class AudioClient:
    """Calls audiod's POST /play to play a named house sound on the USB speaker
    (a real chime, not a piezo squawk). Same shape and no-url-logs-instead
    fallback as BuzzerClient, and the same `beep(pattern)` interface so the
    daemon treats the two as interchangeable beep sinks.

    audiod owns the sound card -- never open it from here. The wire key differs
    from buzzerd (`sound`, not `pattern`) but the *name* is the shared one, so a
    Beep("done") reaches audiod's `done` chime. On kitchen-pi [audio] url points
    at audiod (127.0.0.1:8085); with no url set it only logs (dev machines)."""

    def __init__(self, url: str = "", token: str = "", timeout: float = 2.0):
        self.url = (url or "").rstrip("/")
        self.token = token
        self.timeout = timeout

    def beep(self, pattern: str):
        if not self.url:
            log.info("audio STUB: would play sound=%r", pattern)
            return None
        data = json.dumps({"sound": pattern}).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Auth-Token"] = self.token
        req = urllib.request.Request(self.url + "/play", data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status
        except (urllib.error.URLError, OSError) as e:
            log.warning("audiod /play failed: %s", e)
            return None


# --- recogniserd (who started the timer) ------------------------------------

class RecogniserClient:
    """GETs recogniserd's /who at the moment a timer STARTS and returns the top
    named person (a name string) or None. This is best-effort attribution: like
    the buzzer/audio clients, no url configured means it's disabled (it only
    logs), and *any* failure -- unreachable, bad JSON, a timeout, nobody
    recognised -- returns None. It never raises, so a recognition miss can never
    break a timer.

    The burst form (`frames`/`window`) is recogniserd's: it grabs several frames
    over a short window and returns the single best detection, because a person
    at the knob often glances down and a lone frame misses the frontal moment.
    recogniserd owns the camera seam; this is a pure loopback consumer of it.

    Only used at the START transition, off the knob's critical path (the daemon
    fires it on a background thread), so a generous timeout that outlives the
    burst window is fine."""

    def __init__(self, url: str = "", token: str = "", frames: int = 5,
                 window: str = "2s", timeout: float = 5.0):
        self.url = (url or "").rstrip("/")
        self.token = token
        self.frames = frames
        self.window = window
        self.timeout = timeout

    def who(self):
        if not self.url:
            log.info("recogniser STUB: no url; owner attribution disabled")
            return None
        url = f"{self.url}/who?frames={self.frames}&window={self.window}"
        req = urllib.request.Request(url, method="GET")
        if self.token:
            req.add_header("X-Auth-Token", self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.warning("recogniserd /who failed: %s", e)
            return None
        named = data.get("named") or []
        return named[0] if named else None
