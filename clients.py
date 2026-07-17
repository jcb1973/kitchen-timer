"""HTTP clients for the two devices timerd drives, plus the screen renderer.

  MatrixClient  -> matrixd (paints the timer screen; owns nothing itself)
  BuzzerClient  -> buzzerd (the beep at zero); logs instead when no url is set

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
