"""Persistent rolling-window pipeline for the BD-Calendar tools.

The three BD-Calendar tools (Placement Windows, Events & Networking,
Framework Eligibility) used to be HAND-CURATED static lists, recomputed
from today's date on each page load. They never *discovered* anything new
— a new framework re-procurement, a newly announced comms conference, or a
freshly published regulatory reporting deadline only appeared if someone
edited the Python and committed.

This module makes them auto-update exactly like BD Leads (predictor
pipeline) and Live Jobs: a daily scour finds fresh items from real public
sources, this pipeline persists them across a rolling window with
first_seen / last_seen / status, the morning brief drives the scour on
cron, and the dashboard renders the live pipeline with the usual triage
lifecycle (active / new today / followed up / dismissed).

It is GENERIC across the three tool "kinds":

    kind ∈ {"windows", "events", "frameworks"}

Each kind gets its own rolling window and its own state file, but shares
this one upsert / age-out / triage / read implementation — mirroring
predictor_pipeline.py so the behaviour is identical to the panels Sara
already trusts.

State files (pushed to the dashboard-state branch like every other state):
    state/calendar_pipeline_windows.json
    state/calendar_pipeline_events.json
    state/calendar_pipeline_frameworks.json

Each item dict carries at minimum a stable "key"; the discovery layer
(calendar_discovery.py) and the hand-curated seeds (calendar_pulses.py /
framework_watch.py) both produce items in this shape. Everything else in
the dict is opaque to the pipeline and passed through to the dashboard.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("brief.calpipeline")

from tool.state_paths import state_root
STATE_DIR = state_root()
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Rolling-window horizon per kind. A discovered item stays live this long
# from first_seen unless it's refreshed (last_seen bumped) or followed up
# (kept indefinitely as Sara's record). Frameworks and placement windows
# are long-lead BD groundwork, so they get a generous window; events are
# time-boxed to their action window and naturally drop once past.
WINDOW_DAYS = {
    "windows": 120,
    "events": 120,
    "frameworks": 400,   # frameworks re-let on multi-year cycles
}

VALID_KINDS = set(WINDOW_DAYS)
VALID_STATUS = {"active", "followed_up", "dismissed"}

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


def _state_file(kind: str) -> Path:
    return STATE_DIR / f"calendar_pipeline_{kind}.json"


def repo_state_path(kind: str) -> str:
    return f"tool/state/calendar_pipeline_{kind}.json"


@contextmanager
def _locked(kind: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _state_file(kind).with_suffix(".lock")
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


def load_pipeline(kind: str) -> dict:
    f = _state_file(kind)
    if not f.exists():
        return {"items": {}, "updated_at": None}
    try:
        d = json.loads(f.read_text())
        d.setdefault("items", {})
        return d
    except Exception as e:
        log.exception("calendar pipeline load failed (%s): %s", kind, e)
        return {"items": {}, "updated_at": None}


def _write(kind: str, pipeline: dict) -> None:
    pipeline["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(pipeline, indent=2, default=str)
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
        os.replace(tmp.name, str(_state_file(kind)))
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    try:
        from tool import github_state
        github_state.push_async(
            repo_state_path(kind), payload,
            f"state: calendar pipeline ({kind})")
    except Exception:
        pass


def age_out(pipeline: dict, max_days: int) -> int:
    """Drop items whose first_seen is older than max_days, UNLESS they are
    status=followed_up (kept indefinitely as Sara's record). Mirrors
    predictor_pipeline.age_out."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    removed = 0
    items = pipeline.get("items") or {}
    for key, entry in list(items.items()):
        if entry.get("status") == "followed_up":
            continue
        try:
            first_seen = datetime.fromisoformat(
                entry.get("first_seen") or entry.get("last_seen")
                or "1970-01-01T00:00:00+00:00")
        except Exception:
            continue
        if first_seen < cutoff:
            del items[key]
            removed += 1
    return removed


def upsert(kind: str, found: list[dict]) -> dict:
    """Merge today's freshly-found items into the persistent pipeline.

    `found` is a list of item dicts, each with a stable "key". Same key on
    a later day refreshes last_seen + the item payload (keeping the
    original first_seen and Sara's triage status) instead of duplicating —
    exactly like predictor_pipeline.upsert keyed on company.

    Returns {"new": [...], "updated": N, "total_active": N, "aged_out": N}.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown calendar kind: {kind}")
    now_iso = datetime.now(timezone.utc).isoformat()

    with _locked(kind):
        pipeline = load_pipeline(kind)
        items = pipeline.setdefault("items", {})

        new_items: list[dict] = []
        updated = 0
        for item in found:
            key = (item.get("key") or "").strip()
            if not key:
                continue
            existing = items.get(key)
            if existing is None:
                entry = dict(item)
                entry["key"] = key
                entry["first_seen"] = now_iso
                entry["last_seen"] = now_iso
                entry["status"] = "active"
                entry["followed_up_at"] = None
                entry["dismissed_at"] = None
                items[key] = entry
                new_items.append(entry)
            else:
                # Refresh the payload but preserve lifecycle fields.
                preserved = {
                    "first_seen": existing.get("first_seen", now_iso),
                    "status": existing.get("status", "active"),
                    "followed_up_at": existing.get("followed_up_at"),
                    "dismissed_at": existing.get("dismissed_at"),
                }
                entry = dict(item)
                entry["key"] = key
                entry.update(preserved)
                entry["last_seen"] = now_iso
                items[key] = entry
                updated += 1

        aged = age_out(pipeline, WINDOW_DAYS[kind])
        _write(kind, pipeline)

    total_active = sum(1 for e in items.values() if e.get("status") == "active")
    log.info("calendar pipeline %s: %d new, %d updated, %d aged out, %d active",
             kind, len(new_items), updated, aged, total_active)
    return {
        "new": new_items,
        "updated": updated,
        "total_active": total_active,
        "aged_out": aged,
    }


def set_status(kind: str, key: str, status: str) -> bool:
    """Triage a pipeline item (active / followed_up / dismissed)."""
    if kind not in VALID_KINDS or status not in VALID_STATUS or not key:
        return False
    with _locked(kind):
        pipeline = load_pipeline(kind)
        entry = (pipeline.get("items") or {}).get(key)
        if not entry:
            return False
        now = datetime.now(timezone.utc).isoformat()
        entry["status"] = status
        if status == "followed_up":
            entry["followed_up_at"] = now
        elif status == "dismissed":
            entry["dismissed_at"] = now
        else:
            entry["followed_up_at"] = None
            entry["dismissed_at"] = None
        _write(kind, pipeline)
    return True


def all_items(kind: str, include_dismissed: bool = True) -> list[dict]:
    """Every item in the window for this kind. Dashboard decorates + sorts;
    we just return the persisted entries (optionally hiding dismissed)."""
    if kind not in VALID_KINDS:
        return []
    pipeline = load_pipeline(kind)
    items = list((pipeline.get("items") or {}).values())
    if not include_dismissed:
        items = [e for e in items if e.get("status") != "dismissed"]
    return items


def is_new_today(entry: dict, today_iso_date: str | None = None) -> bool:
    if today_iso_date is None:
        today_iso_date = datetime.now(timezone.utc).date().isoformat()
    return (entry.get("first_seen") or "").startswith(today_iso_date)
