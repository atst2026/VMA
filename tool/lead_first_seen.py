"""Per-lead first-seen timestamps + 7-day retention.

The morning brief drops fresh leads into latest_signals.json every day.
Without retention, an old lead Sara never actioned would stay on the
dashboard indefinitely. This module records the first date each lead_id
was surfaced and filters out anything older than RETENTION_DAYS at
load time, so the Today's Leads panel naturally clears itself.

Mirrors the lead_status / cascade modules: atomic file write, fcntl
lock, background push to the dashboard-state branch.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent / "state"
SEEN_FILE = STATE_DIR / "lead_first_seen.json"
RETENTION_DAYS = 7

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = SEEN_FILE.with_suffix(".lock")
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


def _read() -> dict:
    if not SEEN_FILE.exists():
        return {}
    try:
        d = json.loads(SEEN_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(data: dict) -> None:
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
        os.replace(tmp.name, str(SEEN_FILE))
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    try:
        from tool import github_state
        github_state.push_async(
            "tool/state/lead_first_seen.json", payload,
            "state: lead first-seen retention map")
    except Exception:
        pass


def record_and_filter(lead_ids: list[str],
                      today: date | None = None) -> set[str]:
    """For every lead_id passed in:
      - if not previously seen, record today as its first-seen date
      - keep it if (today - first_seen) < RETENTION_DAYS
      - drop it from the seen-map entirely once expired (so it never
        comes back through some other path)

    Returns the SET of lead_ids that pass retention. Also drops any
    legacy / orphan entries whose dates have aged out.
    """
    if today is None:
        today = date.today()
    today_iso = today.isoformat()

    with _locked():
        data = _read()
        changed = False

        # Record newly seen ids
        for lid in lead_ids:
            if not lid:
                continue
            if lid not in data:
                data[lid] = today_iso
                changed = True

        # Drop expired entries entirely
        for lid in list(data.keys()):
            try:
                first = date.fromisoformat(str(data[lid])[:10])
            except (ValueError, TypeError):
                # Malformed — treat as if first seen today (don't lose
                # it; gives Sara the full retention window)
                data[lid] = today_iso
                changed = True
                continue
            if (today - first).days >= RETENTION_DAYS:
                del data[lid]
                changed = True

        if changed:
            _write(data)

        kept = {lid for lid, iso in data.items() if lid in set(lead_ids)}
        return kept
