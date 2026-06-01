"""Per-lead retention for the Live Jobs panel — expire 7 days after the
job's PUBLISHED / POSTED date.

The morning brief drops the full ranked lead set into latest_signals.json
every day. Without retention, an old role Sara never actioned would sit on
the dashboard forever. This module decides, per lead, whether it is still
inside its retention window and filters the rest out at load time, so the
Live Jobs panel (every sub-filter: active / new today / followed up /
dismissed / all) clears itself.

Rules
-----
* A lead is kept while ``(today - published_date) < RETENTION_DAYS``.
* ``published_date`` is the lead's real post date, parsed from the
  ``published`` field (formats vary by source; dateutil normalises them).
* Some sources don't give a usable post date: LinkedIn stamps ``published``
  with the *scrape* time (so it drifts forward every refresh) and Lever
  leaves it blank. For those we anchor to the date the lead was FIRST SEEN
  instead — the best honest proxy — and we never let a drifting date push
  the anchor later (we keep the earliest date ever seen).
* Once a lead has expired it is TOMBSTONED: it stays in state and stays
  filtered out, so the same posting can't cycle back onto the board. It
  only revives if it reappears with a genuinely newer real post date (a
  true re-post). A re-post that gets a new URL becomes a new lead id and
  shows up fresh on its own.

State file: ``state/lead_first_seen.json`` (name kept for continuity).
Format (per lead id)::

    {"anchor": "2026-05-20", "first_seen": "2026-05-21", "expired": false}

Legacy ``"<id>": "2026-05-21"`` string values (old first-seen map) are
migrated transparently on read.

Mirrors the lead_status / cascade modules: atomic write, fcntl lock,
background push to the dashboard-state branch.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from dateutil import parser as dateparse

STATE_DIR = Path(__file__).resolve().parent / "state"
SEEN_FILE = STATE_DIR / "lead_first_seen.json"
RETENTION_DAYS = 7

# Sources whose ``published`` is the scrape time, not the real post date —
# their date drifts forward on every refresh, so it must never be trusted
# to *extend* a lead's life (matched case-insensitively as a substring of
# the lead's "source" label).
DRIFT_SOURCES = ("linkedin",)

# Tombstones for leads that have been gone from the feed this long are
# pruned, to stop the state file growing without bound. Re-appearance after
# this window is re-evaluated from its published date on the next load, so a
# genuinely-old posting still can't slip back onto the board.
TOMBSTONE_PRUNE_DAYS = 60

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


def _parse_pub_date(published: str | None) -> date | None:
    """Parse a lead's ``published`` value (any source format) to a UTC date.
    Returns None when it's missing or unparseable."""
    if not published:
        return None
    try:
        dt = dateparse.parse(str(published))
    except (ValueError, OverflowError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.date()


def _is_drift_source(source: str | None) -> bool:
    s = (source or "").lower()
    return any(tag in s for tag in DRIFT_SOURCES)


def _migrate(rec, today_iso: str) -> dict:
    """Coerce a legacy string value (first-seen date) into the dict shape."""
    if isinstance(rec, dict):
        rec.setdefault("first_seen", rec.get("anchor", today_iso))
        rec.setdefault("anchor", rec.get("first_seen", today_iso))
        rec.setdefault("expired", False)
        return rec
    iso = str(rec)[:10]
    try:
        date.fromisoformat(iso)
    except (ValueError, TypeError):
        iso = today_iso
    return {"anchor": iso, "first_seen": iso, "expired": False}


def record_and_filter(leads, today: date | None = None) -> set[str]:
    """Decide which Live-Jobs leads are still inside the retention window.

    ``leads`` is the list of lead dicts currently in the feed; each must
    carry ``lead_id`` and should carry ``published`` and ``source``.

    For every lead:
      * establish/maintain an ``anchor`` date — the real post date where we
        trust it, otherwise the date first seen — never drifting later;
      * keep it while ``(today - anchor) < RETENTION_DAYS``;
      * once aged out, tombstone it so it can't return unless it reappears
        with a genuinely newer real post date (a true re-post).

    Returns the SET of lead_ids that pass retention (callers filter the feed
    to this set). Also prunes long-gone tombstones.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()

    def _aged_out(anchor_iso: str) -> bool:
        try:
            anchor = date.fromisoformat(anchor_iso)
        except (ValueError, TypeError):
            return False
        return (today - anchor).days >= RETENTION_DAYS

    with _locked():
        raw = _read()
        data = {k: _migrate(v, today_iso) for k, v in raw.items()}
        changed = raw != data  # migration counts as a change to persist
        kept: set[str] = set()
        seen_now: set[str] = set()

        for lead in leads:
            lid = (lead.get("lead_id") or "").strip() if isinstance(lead, dict) else ""
            if not lid:
                continue
            seen_now.add(lid)
            pub = _parse_pub_date(lead.get("published")) if isinstance(lead, dict) else None
            trustworthy = pub is not None and not _is_drift_source(lead.get("source"))

            rec = data.get(lid)
            if rec is None:
                # First sighting: anchor to the real post date when we have
                # one, else to today (first-seen proxy).
                anchor = (pub.isoformat() if pub else today_iso)
                rec = {"anchor": anchor, "first_seen": today_iso, "expired": False}
                data[lid] = rec
                changed = True
            else:
                if rec.get("expired"):
                    # Only a trustworthy, genuinely newer post date revives a
                    # tombstoned lead (a real re-post on the same URL/id).
                    if trustworthy and pub.isoformat() > rec["anchor"]:
                        rec["anchor"] = pub.isoformat()
                        rec["first_seen"] = today_iso
                        rec["expired"] = False
                        changed = True
                else:
                    # Active: let a trustworthy EARLIER real date pull the
                    # anchor back (stricter expiry); never push it later, so
                    # scrape-time drift can't extend a lead's life.
                    if pub is not None and pub.isoformat() < rec["anchor"]:
                        rec["anchor"] = pub.isoformat()
                        changed = True

            # Re-evaluate expiry from the anchor on every load.
            if not rec.get("expired") and _aged_out(rec["anchor"]):
                rec["expired"] = True
                changed = True

            if not rec.get("expired"):
                kept.add(lid)

        # Entries absent from today's feed: still re-evaluate expiry from
        # their anchor (so state stays truthful), then prune long-gone
        # tombstones to keep the file bounded.
        for lid in list(data.keys()):
            if lid in seen_now:
                continue
            rec = data[lid]
            if not rec.get("expired") and _aged_out(rec["anchor"]):
                rec["expired"] = True
                changed = True
            if not rec.get("expired"):
                continue
            try:
                fs = date.fromisoformat(str(rec.get("first_seen", ""))[:10])
            except (ValueError, TypeError):
                continue
            if (today - fs).days >= TOMBSTONE_PRUNE_DAYS:
                del data[lid]
                changed = True

        if changed:
            _write(data)

        return kept
