"""Permanent BD-lead suppression (tombstone).

When Sara "removes a lead entirely" she's done with that account on the
radar for good: it should leave every BD panel now AND never be relisted,
even if a fresh event re-detects the same company on a later morning
brief. The ordinary triage statuses (followed_up / dismissed) only park a
lead inside its retention window; this is the harder, durable suppression.

A lead is tombstoned by NORMALISED company name (so "OQC" and "Oxford
Quantum Circuits" can't slip back in under a variant, and a brand-new
round/seat for the same company stays suppressed). State is a small
{normkey: {"company": <display>, "ts": <iso>}} map, isolated per profile
(see tool.state_paths) exactly like predictor_status, and pushed back to
the repo so it survives a redeploy. The dashboard read path filters BD
rows whose company is tombstoned.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from tool.state_paths import state_dir

STATE_DIR = state_dir()
TOMB_FILE = STATE_DIR / "bd_tombstone.json"

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


def _norm(company: str | None) -> str:
    """Normalise a company name to a stable key (lowercase, alphanumerics
    only). Matches the entity-resolution style used elsewhere."""
    return re.sub(r"[^a-z0-9]+", " ", (company or "").lower()).strip()


@contextmanager
def _locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = TOMB_FILE.with_suffix(".lock")
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


def get_all() -> dict:
    if not TOMB_FILE.exists():
        return {}
    try:
        d = json.loads(TOMB_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def is_tombstoned(company: str | None, data: dict | None = None) -> bool:
    key = _norm(company)
    if not key:
        return False
    return key in (data if data is not None else get_all())


def add(company: str | None) -> bool:
    """Tombstone a company permanently. Returns True if recorded."""
    key = _norm(company)
    if not key:
        return False
    with _locked():
        data = get_all()
        data[key] = {"company": (company or "").strip(),
                     "ts": datetime.now(timezone.utc).isoformat()}
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
            os.replace(tmp.name, str(TOMB_FILE))
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
    try:
        from pathlib import Path
        from tool import github_state
        from tool.state_paths import state_root
        repo_root = Path(__file__).resolve().parent.parent
        rel = str((state_root() / "bd_tombstone.json").relative_to(repo_root))
        github_state.push_async(rel, payload,
                                "state: tombstone BD lead (removed entirely)")
    except Exception:
        pass
    return True
