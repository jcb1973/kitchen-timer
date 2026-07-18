"""Config for timerd.

Reads a per-project `.creds` (INI, gitignored, mode 600) following the house
convention, and falls back to environment variables + sane defaults so the
daemon also runs on a dev machine with no `.creds` present.
"""
import configparser
import os
from dataclasses import dataclass


@dataclass
class Config:
    # matrixd — where timer screens are painted
    matrix_url: str = "http://127.0.0.1:8081"
    matrix_token: str = ""
    # buzzerd — the beep at zero. Empty url -> beeps are logged, not played
    # (dev machines); kitchen-pi points this at buzzerd on 127.0.0.1:8084.
    buzzer_url: str = ""
    buzzer_token: str = ""
    # audiod — optional richer DONE chime on the USB speaker. Empty url -> the
    # play is logged, not sounded; kitchen-pi points this at audiod on :8085.
    audio_url: str = ""
    audio_token: str = ""
    # which sink plays the DONE beep: buzzer (default; the piezo, honouring
    # audiod's "piezo stays" rule) | audio (the chime) | both (redundant).
    beep_sink: str = "buzzer"
    # timerd's own HTTP listener (encoderd POSTs events here)
    listen_host: str = "127.0.0.1"
    listen_port: int = 8083          # matrixd=8081, summond=8082, timerd=8083
    listen_token: str = ""           # optional; if set, POSTs need X-Auth-Token
    # behaviour
    default_set_s: int = 300         # SET opens at 5:00
    # DONE alarm. done_sound is the sound name sent to the beep sink at zero
    # ("done" is the shared piezo/speaker name; "timer" is a speaker-only clip,
    # so pair done_sound=timer with beep_sink=audio). done_timeout_s <= 0 makes
    # the alarm loop until the knob acknowledges it, instead of auto-dismissing.
    done_sound: str = "done"
    done_timeout_s: int = 60
    # (matrixd slot TTLs are derived per-screen from the timeouts in clients.py)


def load(path: str = ".creds") -> Config:
    cfg = Config()
    cp = configparser.ConfigParser()
    if os.path.exists(path):
        cp.read(path)
        cfg.matrix_url = cp.get("matrix", "url", fallback=cfg.matrix_url)
        cfg.matrix_token = cp.get("matrix", "token", fallback=cfg.matrix_token)
        cfg.buzzer_url = cp.get("buzzer", "url", fallback=cfg.buzzer_url)
        cfg.buzzer_token = cp.get("buzzer", "token", fallback=cfg.buzzer_token)
        cfg.audio_url = cp.get("audio", "url", fallback=cfg.audio_url)
        cfg.audio_token = cp.get("audio", "token", fallback=cfg.audio_token)
        cfg.beep_sink = cp.get("timer", "beep_sink", fallback=cfg.beep_sink)
        cfg.listen_host = cp.get("timer", "listen_host", fallback=cfg.listen_host)
        cfg.listen_port = cp.getint("timer", "listen_port", fallback=cfg.listen_port)
        cfg.listen_token = cp.get("timer", "token", fallback=cfg.listen_token)
        cfg.default_set_s = cp.getint("timer", "default_set_s", fallback=cfg.default_set_s)
        cfg.done_sound = cp.get("timer", "done_sound", fallback=cfg.done_sound)
        cfg.done_timeout_s = cp.getint("timer", "done_timeout_s", fallback=cfg.done_timeout_s)

    # env overrides — convenient for dev/tests
    cfg.matrix_url = os.environ.get("MATRIX_URL", cfg.matrix_url)
    cfg.matrix_token = os.environ.get("MATRIX_TOKEN", cfg.matrix_token)
    cfg.default_set_s = int(os.environ.get("TIMER_DEFAULT_SET_S", cfg.default_set_s))
    cfg.listen_port = int(os.environ.get("TIMER_PORT", cfg.listen_port))
    cfg.beep_sink = os.environ.get("TIMER_BEEP_SINK", cfg.beep_sink)
    return cfg
