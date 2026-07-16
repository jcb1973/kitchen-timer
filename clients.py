"""HTTP clients for the two devices timerd drives, plus the screen renderer.

  MatrixClient  -> matrixd (paints the timer screen; owns nothing itself)
  BuzzerClient  -> buzzerd (the beep at zero) -- STUBBED until buzzerd exists

Stdlib only (urllib), so the daemon has zero third-party deps.
"""
import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("timerd.clients")


# --- screen rendering -------------------------------------------------------
#
# INTEGRATION POINT. render_screen() turns a Render effect into the `draw`
# payload matrixd's POST /screen expects. The exact content schema (does
# matrixd take draw-ops with a font, or a raw framebuffer?) still needs
# confirming against matrixd's source -- see kitchen-sign. When it's confirmed,
# THIS is the only function that changes; the state machine and daemon don't.
# Until then it emits a self-describing semantic dict.

def _mmss(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def render_screen(effect) -> dict:
    """Map a Render(kind, seconds, phase) effect -> matrixd draw payload."""
    kind = effect.kind
    if kind == "done":
        # flash: phase 1 shows DONE, phase 0 blanks it
        return {"text": "DONE" if effect.phase else "", "style": "alert", "big": True}
    if kind == "set":
        if effect.seconds == 0:
            return {"text": "CANCEL", "label": "timer", "style": "dim"}   # 0:00 sentinel
        return {"text": _mmss(effect.seconds), "label": "SET", "style": "set", "big": True}
    # running
    return {"text": _mmss(effect.seconds), "style": "run", "big": True}


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

    def screen(self, name: str, layer: str, ttl: int, draw: dict):
        return self._post("/screen", {"name": name, "layer": layer, "ttl": ttl, **draw})

    def clear(self, name: str):
        return self._post("/clear", {"name": name})


# --- buzzerd (the seam) -----------------------------------------------------

class BuzzerClient:
    """Calls buzzerd's POST /beep. buzzerd isn't built yet, so with no url
    configured this only logs what it *would* play. When buzzerd lands, set
    [buzzer] url in .creds and the same call starts making noise -- no other
    code changes."""

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
