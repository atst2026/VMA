"""Durable AD verdict log — the acceptance-rate ground truth.

Every presented lead card carries three verdict buttons: Call today /
Nurture / Reject. Each press appends one record here. The trailing-window
acceptance rate computed from this log (tool/gate.acceptance) is the
board's governing metric — the share of presented leads an AD judged
real — and drives the auto-throttle: if acceptance drops below 50% over
7 days the gate raises its evidence bar and cuts the daily cap before a
human has to notice the board went noisy.

Same durable pattern as predictor_status: local JSON under the profile
state dir, atomic replace under a lock, best-effort async push to the
repo so Render redeploys don't lose the history.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from tool.state_paths import state_dir

STATE_DIR = state_dir()
LOG_FILE = STATE_DIR / "verdict_log.json"
VALID = {"call_today", "nurture", "reject"}
# Bound the file: the throttle only reads a 7-day window; a year of
# history is ample for any later re-weighting work.
MAX_RECORDS = 2000

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOG_FILE.with_suffix(".lock")
    with _LOCK:
        fd = None
        if _HAVE_FCNTL:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


def get_all() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    try:
        d = json.loads(LOG_FILE.read_text())
        return d if isinstance(d, list) else []
    except Exception:
        return []


def latest_for(rid: str) -> str | None:
    """The most recent verdict recorded for a lead id (cards re-render
    showing the standing verdict)."""
    for rec in reversed(get_all()):
        if rec.get("rid") == rid:
            return rec.get("verdict")
    return None


def record(rid: str, idtype: str, verdict: str, company: str = "") -> bool:
    """Append one verdict. Returns False on invalid input, never raises."""
    if verdict not in VALID or not rid:
        return False
    rec = {"rid": rid, "idtype": idtype or "predictor", "verdict": verdict,
           "company": company or "",
           "date": datetime.now(timezone.utc).isoformat()}
    with _locked():
        data = get_all()
        data.append(rec)
        data = data[-MAX_RECORDS:]
        payload = json.dumps(data, indent=1)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".tmp",
            dir=str(STATE_DIR), delete=False,
        )
        try:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, str(LOG_FILE))
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
    try:
        from tool import github_state
        github_state.push_async("tool/state/verdict_log.json", payload,
                                "state: record AD lead verdict")
    except Exception:
        pass
    return True
