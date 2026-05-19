"""Durable predictor triage overlay (followed_up / dismissed).

The predictor pipeline (predictor_pipeline.json) is rebuilt by the
morning brief and is ephemeral on Render, so the user's followed-up /
dismissed decisions made in the dashboard were lost on redeploy. This
stores those decisions as a small {pid: status} overlay — the same
durable pattern as lead_status — and load_latest_predictive applies it
on top of whatever the pipeline produced.

Only non-active statuses are stored; absence == active.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent / "state"
STATUS_FILE = STATE_DIR / "predictor_status.json"
VALID = {"active", "followed_up", "dismissed"}

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked():
    """Serialise read-modify-write across threads and processes."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = STATUS_FILE.with_suffix(".lock")
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


def get_statuses() -> dict:
    if not STATUS_FILE.exists():
        return {}
    try:
        d = json.loads(STATUS_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def set_status(pid: str, status: str) -> bool:
    if status not in VALID or not pid:
        return False
    with _locked():
        data = get_statuses()
        if status == "active":
            data.pop(pid, None)
        else:
            data[pid] = status
        payload = json.dumps(data, indent=2)
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
            os.replace(tmp.name, str(STATUS_FILE))
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
    # Persist to the repo (background; never blocks the request).
    try:
        from tool import github_state
        github_state.push_async("tool/state/predictor_status.json", payload,
                                "state: update predictor triage status")
    except Exception:
        pass
    return True
