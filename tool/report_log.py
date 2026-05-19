"""Log of generated reports, so the dashboard can show Type / Company /
Name for each run.

GitHub artifact metadata only carries the artifact *name*
("pitch-pack"), never what it was run for. The only place the target
is known is the moment of dispatch — so the dashboard records a small
entry here per run. Persisted to the repo (via github_state) so it
survives Render redeploys, same pattern as lead_status / candidate_watch.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent / "state"
LOG_FILE = STATE_DIR / "report_log.json"
_MAX = 100

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


def _load() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    try:
        d = json.loads(LOG_FILE.read_text())
        return d if isinstance(d, list) else []
    except Exception:
        return []


def add(report_type: str, company: str, name: str, artifact: str) -> None:
    """Record a dispatched report. Best-effort; never raises."""
    try:
        with _locked():
            rows = _load()
            rows.insert(0, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": (report_type or "").strip(),
                "company": (company or "").strip(),
                "name": (name or "").strip(),
                "artifact": (artifact or "").strip(),
            })
            rows = rows[:_MAX]
            payload = json.dumps(rows, indent=2)
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp",
                dir=str(STATE_DIR), delete=False)
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
            github_state.push_async("tool/state/report_log.json", payload,
                                    "state: log generated report")
        except Exception:
            pass
    except Exception:
        pass


def recent(hours: int = 48) -> list[dict]:
    """Logged reports from the last `hours`, newest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for r in _load():
        try:
            ts = datetime.fromisoformat(r.get("ts", ""))
        except Exception:
            continue
        if ts >= cutoff:
            out.append(r)
    return out
