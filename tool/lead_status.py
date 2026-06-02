"""Per-lead triage status (followed_up / dismissed), persisted across
daily refreshes.

Leads come from the morning-brief artifact (latest_signals.json) and
have no pipeline of their own like predictors do, so the user's triage
decision is stored here keyed by the lead's stable id. A dismissed or
followed-up lead therefore stays that way after the next refresh.

Only non-active statuses are stored; absence == active.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from tool.state_paths import state_dir, state_root
STATE_DIR = state_dir()
STATUS_FILE = STATE_DIR / "lead_status.json"
VALID = {"active", "followed_up", "dismissed"}

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked():
    """Serialise read-modify-write across threads and processes, the
    same pattern candidate_watch uses (Render runs multi-process WSGI)."""
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


def set_status(lead_id: str, status: str) -> bool:
    if status not in VALID or not lead_id:
        return False
    with _locked():
        data = get_statuses()
        if status == "active":
            data.pop(lead_id, None)
        else:
            data[lead_id] = status
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
        github_state.push_async("tool/state/lead_status.json", payload,
                                "state: update lead triage status")
    except Exception:
        pass
    return True
