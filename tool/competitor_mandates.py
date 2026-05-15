"""Competitor-mandate scavenging.

When a comms role brief sits unfilled for >90 days, the client is
typically open to a second agency, an "off-piste" candidate, or a fee
re-negotiation. Detectable signals:

  * Same job-ad URL still live in our jobs source after first-seen N
    days ago
  * Same role (title-normalised) reposted across multiple recruiter-
    facing channels in close succession
  * A direct-employer ad that's been listed >60 days

We don't (and can't) scrape recruiter dashboards. Instead we track
first-seen dates per job-ad signal-id in a small state file. Each
time the morning brief produces a fresh latest_signals.json, this
module reconciles: new IDs get a first-seen timestamp of today;
existing IDs keep theirs; IDs that have disappeared for >2 consecutive
runs are marked closed.

The dashboard panel reads from this state and surfaces ads that have
been live for >= STALE_THRESHOLD_DAYS. Default 60 days.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("brief.competitor_mandates")

STATE_DIR = Path(__file__).resolve().parent / "state"
TRACKER_FILE = STATE_DIR / "competitor_mandates.json"
SIGNALS_FILE = STATE_DIR / "latest_signals.json"


STALE_THRESHOLD_DAYS = 60       # ads live this long are flagged
EVICT_AFTER_MISSED_RUNS = 3     # after N consecutive runs missing, drop


def _today() -> date:
    return date.today()


def _today_iso() -> str:
    return _today().isoformat()


def _parse_iso(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _load_tracker() -> dict:
    if not TRACKER_FILE.exists():
        return {}
    try:
        data = json.loads(TRACKER_FILE.read_text())
    except Exception as e:
        log.info("competitor_mandates tracker load failed: %s", e)
        return {}
    return data if isinstance(data, dict) else {}


def _save_tracker(data: dict) -> None:
    TRACKER_FILE.parent.mkdir(exist_ok=True, parents=True)
    TRACKER_FILE.write_text(json.dumps(data, indent=2))


def _is_job_signal(signal: dict) -> bool:
    kind = (signal.get("kind") or "").lower()
    return kind in ("job", "job_post", "vacancy")


def reconcile(signals_path: Path | None = None,
              tracker_path: Path | None = None) -> dict:
    """Update first-seen tracker from a fresh signals list.

    Called by morning_brief.py at the end of each run. Returns a
    summary dict (added, refreshed, evicted, total_tracked).
    """
    signals_path = signals_path or SIGNALS_FILE
    tracker_path = tracker_path or TRACKER_FILE

    tracker = _load_tracker() if tracker_path == TRACKER_FILE else (
        json.loads(tracker_path.read_text()) if tracker_path.exists() else {}
    )
    fresh_ids: set[str] = set()
    added = refreshed = 0

    if signals_path.exists():
        try:
            data = json.loads(signals_path.read_text())
            if isinstance(data, list):
                for s in data:
                    if not isinstance(s, dict) or not _is_job_signal(s):
                        continue
                    sid = s.get("id") or s.get("url") or s.get("title")
                    if not sid:
                        continue
                    sid = str(sid)
                    fresh_ids.add(sid)
                    if sid in tracker:
                        tracker[sid]["last_seen"]      = _today_iso()
                        tracker[sid]["missed_runs"]    = 0
                        # Keep latest title/url/company seen
                        tracker[sid]["title"]   = s.get("title")   or tracker[sid].get("title")
                        tracker[sid]["url"]     = s.get("url")     or tracker[sid].get("url")
                        tracker[sid]["company"] = s.get("company") or tracker[sid].get("company")
                        tracker[sid]["source"]  = s.get("source")  or tracker[sid].get("source")
                        refreshed += 1
                    else:
                        tracker[sid] = {
                            "first_seen":   _today_iso(),
                            "last_seen":    _today_iso(),
                            "title":        s.get("title", ""),
                            "url":          s.get("url", ""),
                            "company":      s.get("company", ""),
                            "source":       s.get("source", ""),
                            "missed_runs":  0,
                        }
                        added += 1
        except Exception as e:
            log.info("competitor_mandates reconcile failed: %s", e)

    # Mark IDs not in this run as missed; evict after N runs.
    # Guard: if this run produced ZERO job signals at all, the source
    # likely failed (network error, ATS API down) — don't punish tracked
    # ads by ticking their missed_runs counter, which would otherwise
    # evict every tracked ad after EVICT_AFTER_MISSED_RUNS consecutive
    # failed runs.
    to_remove: list[str] = []
    if fresh_ids:
        for sid, row in tracker.items():
            if sid in fresh_ids:
                continue
            row["missed_runs"] = int(row.get("missed_runs", 0)) + 1
            if row["missed_runs"] >= EVICT_AFTER_MISSED_RUNS:
                to_remove.append(sid)
        for sid in to_remove:
            tracker.pop(sid, None)

    if tracker_path == TRACKER_FILE:
        _save_tracker(tracker)
    else:
        tracker_path.write_text(json.dumps(tracker, indent=2))

    return {
        "added": added,
        "refreshed": refreshed,
        "evicted": len(to_remove),
        "total_tracked": len(tracker),
    }


def stale_mandates(min_age_days: int = STALE_THRESHOLD_DAYS,
                   limit: int = 30) -> list[dict]:
    """Return tracked ads older than `min_age_days` (sorted by age
    descending). Each row carries:
      first_seen, last_seen, days_live, title, url, company, source
    """
    tracker = _load_tracker()
    out: list[dict] = []
    today = _today()
    for sid, row in tracker.items():
        first = _parse_iso(row.get("first_seen", ""))
        if not first:
            continue
        days = (today - first).days
        if days < min_age_days:
            continue
        out.append({
            "id":          sid,
            "first_seen":  row.get("first_seen", ""),
            "last_seen":   row.get("last_seen",  ""),
            "days_live":   days,
            "title":       row.get("title", ""),
            "url":         row.get("url", ""),
            "company":     row.get("company", ""),
            "source":      row.get("source", ""),
        })
    out.sort(key=lambda r: r["days_live"], reverse=True)
    return out[:limit]
