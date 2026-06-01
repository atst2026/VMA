"""Per-framework triage state (followed_up / dismissed), persisted across
daily refreshes.

Framework Watch rows are recomputed deterministically each load (see
framework_watch.py), so Sara's triage decision is stored here keyed by the
framework's stable `key`. A dismissed / followed-up framework therefore
stays that way on the next load.

Mirrors funding_status.py exactly — same atomic write + fcntl lock +
background github_state push pattern.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from tool.state_paths import state_root
STATE_DIR = state_root()
STATUS_FILE = STATE_DIR / "framework_status.json"
VALID = {"active", "followed_up", "dismissed"}

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked():
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


def set_status(key: str, status: str) -> bool:
    if status not in VALID or not key:
        return False
    with _locked():
        data = get_statuses()
        if status == "active":
            data.pop(key, None)
        else:
            data[key] = status
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
    try:
        from tool import github_state
        github_state.push_async("tool/state/framework_status.json", payload,
                                "state: update framework triage status")
    except Exception:
        pass
    return True
