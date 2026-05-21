"""Dismissed BD-Calendar findings (statutory pulses + comms events).

Pulses and events are deterministic — recomputed live from the
calendar every request — so the only thing we persist is the set of
finding keys the user has removed. A dismissed key is filtered out of
the calendar until restored.

Same atomic-write + fcntl-lock + background github_state push pattern
as lead_status / funding_status.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent / "state"
DISMISS_FILE = STATE_DIR / "pulse_dismissed.json"

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = DISMISS_FILE.with_suffix(".lock")
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


def get_dismissed() -> set:
    if not DISMISS_FILE.exists():
        return set()
    try:
        d = json.loads(DISMISS_FILE.read_text())
        return set(d) if isinstance(d, list) else set()
    except Exception:
        return set()


def set_dismissed(key: str, dismissed: bool) -> bool:
    """Add (dismissed=True) or remove (dismissed=False) a finding key."""
    if not key:
        return False
    with _locked():
        keys = get_dismissed()
        if dismissed:
            keys.add(key)
        else:
            keys.discard(key)
        payload = json.dumps(sorted(keys), indent=2)
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
            os.replace(tmp.name, str(DISMISS_FILE))
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
    try:
        from tool import github_state
        github_state.push_async("tool/state/pulse_dismissed.json", payload,
                                "state: update dismissed BD-calendar findings")
    except Exception:
        pass
    return True
