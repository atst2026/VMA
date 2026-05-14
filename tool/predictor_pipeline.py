"""Persistent rolling-window predictor pipeline.

The daily morning brief used to render the predictors section as a
SNAPSHOT — only items that fired in that day's scan. For low-volume
signals (which is what predictive triggers are, by nature) that meant
most mornings showed 0–2 items and Sara had no continuous pipeline.

This module persists every predictor that fires across a rolling 30-day
window, with status tracking (active / followed_up / dismissed). The
morning email then renders the daily DELTA (newly first-seen items
since yesterday), while the dashboard renders the full active pipeline.

Each predictor is keyed by normalised company name, so the same
company that fires multiple days in a row updates last_seen instead
of duplicating.

Followed-up entries are kept indefinitely as Sara's record; everything
else ages out 30 days after last_seen.
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tool.predictive.render import window_for_stack
from tool.predictive.stacker import Stack

log = logging.getLogger("brief.pipeline")

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
PIPELINE_FILE = STATE_DIR / "predictor_pipeline.json"

# 90-day forward-prediction horizon (matches "PREDICTED BRIEFS (next
# 90 days)" in the spec). Predictors stay live for 90 days from
# first_seen — long enough for a 6-12wk CEO-cascade hire to land or
# a 3-month M&A integration hire to fire.
ROLLING_WINDOW_DAYS = 90


def _pid(company: str) -> str:
    """Stable predictor ID = normalised company name. Same company on
    different days collapses to one entry."""
    s = re.sub(r"[^a-z0-9]+", "_", (company or "").lower()).strip("_")
    return s or "unknown"


def load_pipeline() -> dict:
    if not PIPELINE_FILE.exists():
        return {"predictors": {}, "updated_at": None}
    try:
        return json.loads(PIPELINE_FILE.read_text())
    except Exception as e:
        log.exception("pipeline load failed: %s", e)
        return {"predictors": {}, "updated_at": None}


def save_pipeline(pipeline: dict) -> None:
    pipeline["updated_at"] = datetime.now(timezone.utc).isoformat()
    PIPELINE_FILE.write_text(json.dumps(pipeline, indent=2, default=str))


def _predicted_role_for(stk: Stack) -> str:
    """Map the stack's strongest trigger to the senior role most likely
    being hired. Mirrors linkedin_resolver.role_for_predictor but kept
    here to avoid a circular import."""
    keys = {e.trigger_key for e in stk.events}
    # Order = priority; first match wins
    role_map = [
        ("comms_leader_departure", "Head of Communications"),
        ("ic_platform_rfp",        "Head of Internal Communications"),
        ("ipo_listing",            "Corporate Affairs Director"),
        ("ceo_change",             "Head of Communications"),
        ("mna",                    "Corporate Affairs Director"),
        ("regulator_action",       "Crisis / Head of Comms"),
        ("contract_loss",          "Head of Communications"),
        ("chair_change",           "Head of Communications"),
        ("cfo_change",             "Head of Investor Relations"),
        ("ir_director_change",     "Head of Investor Relations"),
        ("chro_change",            "Head of Internal Communications"),
        ("restructure",            "Head of Internal Communications"),
        ("press_velocity_spike",   "Head of Communications"),
        ("job_ad_cluster",         "Head of Internal Communications"),
    ]
    for key, role in role_map:
        if key in keys:
            return role
    return "Senior Comms hire"


def _probability_for(score: float, depth: int) -> int:
    """Convert raw stack score into a calibrated probability % for the
    'PREDICTED BRIEFS — next 90 days' display. Loose calibration based
    on observed stacker output ranges:
      depth=1 single trigger  ~score 0.6-1.5 → 45-65%
      depth=2 stacked         ~score 1.5-3.0 → 65-82%
      depth=3+ heavy stack    ~score 3.0+    → 82-92%
    Capped 35-92% so we never overclaim or underclaim."""
    base = 40 + score * 10
    if depth >= 2:
        base += 8
    if depth >= 3:
        base += 6
    return int(max(35, min(92, base)))


def _serialise_stack(stk: Stack, score: float, now_iso: str) -> dict:
    w = window_for_stack(stk)
    return {
        "company": stk.company,
        "score": score,
        "depth": stk.depth,
        "probability": _probability_for(score, stk.depth),
        "predicted_role": _predicted_role_for(stk),
        "window_weeks_min": w[0] if w else None,
        "window_weeks_max": w[1] if w else None,
        "window_label": f"{w[0]}–{w[1]} weeks" if w else None,
        "last_seen": now_iso,
        "linkedin_profile_url": getattr(stk, "_resolved_profile_url", None),
        "linkedin_profile_role": getattr(stk, "_resolved_profile_role", None),
        "linkedin_profile_name": getattr(stk, "_resolved_profile_name", None),
        "seeded_contact_name": getattr(stk, "_seeded_contact_name", None),
        "seeded_contact_role": getattr(stk, "_seeded_contact_role", None),
        "events": [
            {
                "trigger_key": e.trigger_key,
                "trigger_label": e.trigger_label,
                "evidence": e.evidence,
                "url": e.url,
                "source": e.source_label,
                "published": e.published.isoformat(),
                "tier": e.tier_hint,
            }
            for e in stk.events
        ],
    }


def upsert(ranked_stacks: list[tuple[Stack, float]]) -> dict:
    """Merge today's ranked stacks into the persistent pipeline.

    Returns {"new": [...], "updated": [...], "total_active": N,
             "new_pids": set, "aged_out": N}.
    Caller uses "new" to render the email delta.
    """
    pipeline = load_pipeline()
    predictors = pipeline.setdefault("predictors", {})
    now_iso = datetime.now(timezone.utc).isoformat()

    new_items: list[dict] = []
    updated_items: list[dict] = []
    new_pids: set[str] = set()

    for stk, score in ranked_stacks:
        pid = _pid(stk.company)
        existing = predictors.get(pid)
        entry = _serialise_stack(stk, score, now_iso)
        entry["pid"] = pid
        if existing is None:
            entry["first_seen"] = now_iso
            entry["status"] = "active"
            entry["followed_up_at"] = None
            entry["dismissed_at"] = None
            predictors[pid] = entry
            new_items.append(entry)
            new_pids.add(pid)
        else:
            entry["first_seen"] = existing.get("first_seen", now_iso)
            entry["status"] = existing.get("status", "active")
            entry["followed_up_at"] = existing.get("followed_up_at")
            entry["dismissed_at"] = existing.get("dismissed_at")
            predictors[pid] = entry
            updated_items.append(entry)

    aged = age_out(pipeline, ROLLING_WINDOW_DAYS)
    save_pipeline(pipeline)

    total_active = sum(1 for p in predictors.values() if p.get("status") == "active")
    log.info("pipeline: %d new, %d updated, %d aged out, %d active total",
             len(new_items), len(updated_items), aged, total_active)
    return {
        "new": new_items,
        "updated": updated_items,
        "new_pids": new_pids,
        "total_active": total_active,
        "aged_out": aged,
    }


def age_out(pipeline: dict, max_days: int = ROLLING_WINDOW_DAYS) -> int:
    """Remove predictors whose FIRST_SEEN is older than max_days, UNLESS
    they're status=followed_up (kept indefinitely as Sara's record).

    Using first_seen (not last_seen) means a predictor stays live for
    the full 90-day forward-prediction window from when it first fired
    — even if the underlying signal stops generating fresh evidence.
    A CEO change reported 60 days ago is still active because the
    cascade hire is typically 6-12 weeks out.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    removed = 0
    predictors = pipeline.get("predictors") or {}
    for pid, entry in list(predictors.items()):
        if entry.get("status") == "followed_up":
            continue
        try:
            first_seen = datetime.fromisoformat(
                entry.get("first_seen")
                or entry.get("last_seen")
                or "1970-01-01T00:00:00+00:00"
            )
        except Exception:
            continue
        if first_seen < cutoff:
            del predictors[pid]
            removed += 1
    return removed


def set_status(pid: str, status: str) -> bool:
    """Dashboard endpoint: update a predictor's status. Returns True if
    the predictor existed and was updated."""
    if status not in ("active", "followed_up", "dismissed"):
        return False
    pipeline = load_pipeline()
    predictors = pipeline.get("predictors") or {}
    entry = predictors.get(pid)
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
    save_pipeline(pipeline)
    return True


def all_predictors() -> list[dict]:
    """Every predictor in the window, regardless of status. Sorted by
    (status priority, then score desc) so active leads sort to the top."""
    pipeline = load_pipeline()
    items = list((pipeline.get("predictors") or {}).values())
    status_rank = {"active": 0, "followed_up": 1, "dismissed": 2}
    items.sort(key=lambda p: (status_rank.get(p.get("status"), 3),
                              -float(p.get("score") or 0)))
    return items


def is_new_today(predictor: dict, today_iso_date: str | None = None) -> bool:
    """True if first_seen falls on today_iso_date (defaults to UTC today)."""
    if today_iso_date is None:
        today_iso_date = datetime.now(timezone.utc).date().isoformat()
    first_seen = predictor.get("first_seen") or ""
    return first_seen.startswith(today_iso_date)
