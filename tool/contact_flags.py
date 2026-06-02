"""User-driven "this contact is wrong" flags.

Sara hits a wrong contact (sends a message, gets a bounce, finds out
the person left). Clicking the inline flag stores it here keyed by
company::slot together with the name that was wrong. The resolver
skips that entry while the flagged name is still in the roster — and
the flag is implicitly cleared the moment a CH refresh / manual update
puts a different name in that slot.

Same durable-state pattern as lead_status / predictor_status:
github_state.push_async + boot hydrate, so flags survive redeploys.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from tool.state_paths import state_dir, state_root
STATE_DIR = state_dir()
FLAGS_FILE = STATE_DIR / "contact_flags.json"

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = FLAGS_FILE.with_suffix(".lock")
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


def get_flags() -> dict:
    """{f"{company}::{slot}": {"flagged_at": iso, "name": "..."}}."""
    if not FLAGS_FILE.exists():
        return {}
    try:
        d = json.loads(FLAGS_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def flag(company: str, slot: str, name: str) -> bool:
    if not company or not slot or not name:
        return False
    key = f"{company}::{slot}"
    with _locked():
        data = get_flags()
        data[key] = {
            "flagged_at": datetime.now(timezone.utc).isoformat(),
            "name": name,
        }
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
            os.replace(tmp.name, str(FLAGS_FILE))
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
    try:
        from tool import github_state
        github_state.push_async("tool/state/contact_flags.json", payload,
                                "state: flag wrong contact")
    except Exception:
        pass
    return True
