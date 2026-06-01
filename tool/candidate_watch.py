"""Candidate Watch — warmth maintenance and restlessness detection.

Sara seeds a roster of passive candidates she wants to stay liquid to.
The module:

  1. Stores the roster in tool/state/candidate_watch.json
  2. Tracks `last_touched` (date Sara last spoke to them) and any free-
     text `last_signal` Sara notes
  3. Computes a `cadence_score` — overdue candidates float to the top
     of the dashboard panel so Sara sees who to call this week
  4. Lets Sara mark each as touched, snoozed, or removed
  5. Optionally cross-checks Companies House officer-changes for each
     candidate name (lightweight, opt-in; off by default)

The output is a weekly "call these five this week" list, sorted by
days-since-touched + restlessness signal weight. This is the warmth
side of the candidate-led-BD pitch in the gap critique.

No external scraping. Pure state management + a heuristic.
"""
from __future__ import annotations
import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger("brief.candidate_watch")

from tool.state_paths import state_root
STATE_DIR = state_root()
WATCH_FILE = STATE_DIR / "candidate_watch.json"

# fcntl is POSIX-only. On Windows we fall back to thread-only locking,
# which is enough for the dev server but not for a multi-process WSGI.
# Render runs Linux, so the cross-process lock is the path we need.
try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

# Thread-level lock so concurrent threads in the same Flask worker
# don't race even before fcntl serialises across processes.
_THREAD_LOCK = threading.Lock()


@contextmanager
def _locked_state():
    """Serialise read-modify-write on WATCH_FILE across both threads
    (via _THREAD_LOCK) and processes (via fcntl on a lock file). Without
    this, 20 concurrent /api/candidates/watch/add requests dropped
    ~50% of writes due to the lost-update race."""
    lock_path = WATCH_FILE.with_suffix(".lock")
    WATCH_FILE.parent.mkdir(exist_ok=True, parents=True)
    with _THREAD_LOCK:
        lock_fd = None
        if _HAVE_FCNTL:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if lock_fd is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)


def _atomic_write(path: Path, content: str) -> None:
    """Write content via tempfile-then-rename so a crash mid-write
    leaves the previous valid file intact (rather than a half-written
    truncated JSON that subsequent reads can't parse)."""
    path.parent.mkdir(exist_ok=True, parents=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".tmp",
        dir=str(path.parent), delete=False,
    )
    try:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, str(path))
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


@dataclass
class WatchedCandidate:
    name: str
    current_company: str = ""
    current_title: str = ""
    linkedin_url: str = ""
    sectors: list[str] = field(default_factory=list)
    last_touched: str = ""        # ISO date YYYY-MM-DD
    last_signal: str = ""         # free text Sara notes
    touch_cadence_days: int = 30  # default: call every 30 days
    snoozed_until: str = ""       # ISO date, or empty
    notes: str = ""
    tenure_start: str = ""        # ISO date when they joined current role
                                  # — feeds the tenure-clock liquidity score


def _today_iso() -> str:
    return date.today().isoformat()


def _load_all() -> list[dict]:
    if not WATCH_FILE.exists():
        return []
    try:
        data = json.loads(WATCH_FILE.read_text())
    except Exception as e:
        log.info("candidate_watch load failed: %s", e)
        return []
    return data if isinstance(data, list) else []


def _save_all(data: list[dict]) -> None:
    payload = json.dumps(data, indent=2)
    _atomic_write(WATCH_FILE, payload)
    # Persist to the repo (background; never blocks the request).
    try:
        from tool import github_state
        github_state.push_async("tool/state/candidate_watch.json", payload,
                                "state: update candidate watch roster")
    except Exception as e:
        log.info("candidate_watch github persist skipped: %s", e)


def _parse_iso(s: str) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _days_since(iso: str) -> int | None:
    d = _parse_iso(iso)
    if not d:
        return None
    return (date.today() - d).days


def list_watched() -> list[dict]:
    """Return all watched candidates, sorted by overdue.

    Drift/liquidity scoring is still computed in the background (it
    feeds Top-3 ranking) but is not surfaced as a confidence number
    on the dashboard, and is no longer the primary sort key. The
    score relies on cross-source name matching that isn't reliable
    enough to show as a confidence figure — overdue cadence is the
    honest, deterministic ordering for the panel.
    """
    from tool.candidate_signal_drift import compute_drift
    rows = _load_all()
    out: list[dict] = []
    for c in rows:
        decorated = dict(c)
        decorated.update(compute_drift(c))
        out.append(decorated)
    # Overdue-first ordering: most overdue at the top, then due-soon,
    # then never-touched (which sorts as a large overdue value).
    def _sort_key(r):
        overdue = r.get("_overdue_days", 0) or 0
        return -overdue
    out.sort(key=_sort_key)
    return out


_RESTLESSNESS_KEYWORDS = [
    # Profile / engagement movement
    "updated profile", "updated linkedin", "new headline", "new title",
    "posting again", "active again", "posts more",
    "new connections", "added contacts",
    # Job / org movement
    "team restructure", "team cut", "boss left", "new manager", "lost mandate",
    "team reduced", "function moved", "reorg", "made redundant",
    # Direct restlessness
    "open to a move", "open to opportunities", "exploring options",
    "wants to leave", "ready to move", "fed up", "burned out",
    # Sara-specific intel
    "asked for a coffee", "reached out", "messaged me",
]


def _restlessness_score(text: str) -> int:
    if not text:
        return 0
    low = text.lower()
    return sum(1 for kw in _RESTLESSNESS_KEYWORDS if kw in low)


def add_candidate(name: str,
                  current_company: str = "",
                  current_title: str = "",
                  linkedin_url: str = "",
                  sectors: list[str] | None = None,
                  notes: str = "",
                  touch_cadence_days: int = 30,
                  tenure_start: str = "") -> dict:
    """Add a new candidate to the watch list. If a candidate with the
    same name+current_company already exists, it's updated rather
    than duplicated. Serialised so concurrent adds can't lose writes."""
    with _locked_state():
        rows = _load_all()
        key_name = name.strip().lower()
        key_co   = current_company.strip().lower()
        for r in rows:
            if (r.get("name", "").strip().lower() == key_name and
                r.get("current_company", "").strip().lower() == key_co):
                r.update({
                    "current_title":      current_title or r.get("current_title", ""),
                    "linkedin_url":       linkedin_url  or r.get("linkedin_url", ""),
                    "sectors":            sectors       or r.get("sectors", []),
                    "notes":              notes         or r.get("notes", ""),
                    "touch_cadence_days": touch_cadence_days,
                    "tenure_start":       tenure_start  or r.get("tenure_start", ""),
                })
                _save_all(rows)
                return r
        new = asdict(WatchedCandidate(
            name=name.strip(),
            current_company=current_company.strip(),
            current_title=current_title.strip(),
            linkedin_url=linkedin_url.strip(),
            sectors=sectors or [],
            notes=notes.strip(),
            touch_cadence_days=touch_cadence_days,
            tenure_start=tenure_start.strip(),
        ))
        rows.append(new)
        _save_all(rows)
        return new


def mark_touched(name: str, current_company: str = "",
                 signal: str = "") -> dict | None:
    """Mark a candidate as just-touched. Optionally records a free-text
    signal note Sara observed (drives the restlessness score)."""
    with _locked_state():
        rows = _load_all()
        key_name = name.strip().lower()
        key_co   = current_company.strip().lower()
        for r in rows:
            if r.get("name", "").strip().lower() != key_name:
                continue
            if key_co and r.get("current_company", "").strip().lower() != key_co:
                continue
            r["last_touched"] = _today_iso()
            if signal:
                r["last_signal"] = signal
            r["snoozed_until"] = ""
            _save_all(rows)
            return r
        return None



def remove_candidate(name: str, current_company: str = "") -> bool:
    with _locked_state():
        rows = _load_all()
        key_name = name.strip().lower()
        key_co   = current_company.strip().lower()
        new_rows = [
            r for r in rows
            if not (r.get("name", "").strip().lower() == key_name
                    and (not key_co or r.get("current_company", "").strip().lower() == key_co))
        ]
        if len(new_rows) == len(rows):
            return False
        _save_all(new_rows)
        return True


def weekly_call_list(top_n: int = 5) -> list[dict]:
    """Convenience: top N most-urgent candidates to call this week."""
    return list_watched()[:top_n]
