"""Persistent rolling-window predictor pipeline.

The daily morning brief used to render the predictors section as a
SNAPSHOT — only items that fired in that day's scan. For low-volume
signals (which is what predictive triggers are, by nature) that meant
most mornings showed 0–2 items and Sara had no continuous pipeline.

This module persists every predictor that fires, with status tracking
(active / followed_up / dismissed). The morning email then renders the
daily DELTA (newly first-seen items since yesterday), while the
dashboard renders the full pipeline.

Each predictor is keyed by normalised company name, so the same
company that fires multiple days in a row updates last_seen instead
of duplicating.

Retention follows the shared BD-Leads dashboard rule (tool.bd_retention):
a predictor drops 30 days after it was first presented (first_seen), or
90 days if Sara has marked it followed-up. The durable triage overlay
(predictor_status) is authoritative for that followed-up carve-out, so a
followed-up lead is never aged out on the shorter clock.
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from tool import bd_retention
from tool.predictive.render import window_for_stack
from tool.predictive.stacker import Stack

log = logging.getLogger("brief.pipeline")

from tool.state_paths import state_dir, state_root
STATE_DIR = state_dir()
STATE_DIR.mkdir(parents=True, exist_ok=True)
PIPELINE_FILE = STATE_DIR / "predictor_pipeline.json"


def _status_overlay() -> dict:
    """The durable triage overlay (predictor_status.json) — authoritative
    over the ephemeral pipeline ``status`` field, so a followed-up /
    dismissed decision survives a redeploy and drives retention correctly.
    Fail-safe to empty on any error: a degraded overlay must never change
    a retention decision (we then fall back to the pipeline status)."""
    try:
        from tool import predictor_status
        return predictor_status.get_statuses() or {}
    except Exception:
        return {}


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
    being hired. Profile-aware: comms desk -> comms seats, marketing desk
    -> marketing seats. Resolved per call (not import) so it's correct in
    the single dashboard process and in each desk's brief."""
    keys = {e.trigger_key for e in stk.events}
    # Order = priority; first match wins
    comms_map = [
        ("comms_leader_departure", "Head of Communications"),
        ("ic_platform_rfp",        "Head of Internal Communications"),
        ("ipo_listing",            "Corporate Affairs Director"),
        ("ceo_change",             "Head of Communications"),
        ("pe_acquisition",         "Corporate Affairs Director (new ownership)"),
        ("activist_stake",         "Crisis / Corporate Affairs Director"),
        ("mna",                    "Corporate Affairs Director"),
        ("regulator_action",       "Crisis / Head of Comms"),
        ("regulator_probe_early",  "Crisis / Head of Comms"),
        ("crisis_event",           "Crisis / Head of Comms"),
        ("profit_warning",         "IR Director / Head of Corporate Affairs"),
        ("contract_loss",          "Head of Communications"),
        ("chair_change",           "Head of Communications"),
        ("cfo_change",             "Head of Investor Relations"),
        ("ir_director_change",     "Head of Investor Relations"),
        ("chro_change",            "Head of Internal Communications"),
        ("restructure",            "Head of Internal Communications"),
        ("press_velocity_spike",   "Head of Communications"),
        ("ned_trustee_appointment","Head of Communications (succession watch)"),
        ("personal_brand_velocity","Head of Communications (succession watch)"),
        ("job_ad_cluster",         "Head of Internal Communications"),
    ]
    marketing_map = [
        ("comms_leader_departure", "Chief Marketing Officer"),
        ("ic_platform_rfp",        "Head of Marketing"),
        ("ipo_listing",            "Brand / Corporate Marketing Director"),
        ("ceo_change",             "Chief Marketing Officer"),
        ("pe_acquisition",         "Brand Director (new ownership)"),
        ("activist_stake",         "Crisis / Brand-Trust Director"),
        ("mna",                    "Brand-Integration Director"),
        ("regulator_action",       "Brand-Trust / Customer Marketing Lead"),
        ("regulator_probe_early",  "Brand-Trust / Customer Marketing Lead"),
        ("crisis_event",           "Brand-Trust / Customer Marketing Lead"),
        ("profit_warning",         "Head of Demand Generation"),
        ("contract_loss",          "Head of Marketing"),
        ("chair_change",           "Chief Marketing Officer"),
        ("cfo_change",             "Head of Marketing (ROI/efficiency)"),
        ("ir_director_change",     "Brand / Investor Marketing"),
        ("chro_change",            "Head of Marketing"),
        ("restructure",            "Chief Marketing Officer"),
        ("press_velocity_spike",   "Head of Brand"),
        ("ned_trustee_appointment","Head of Marketing (succession watch)"),
        ("personal_brand_velocity","Head of Marketing (succession watch)"),
        ("job_ad_cluster",         "Head of Marketing"),
    ]
    try:
        from tool.profiles import active_profile
        is_marketing = active_profile().key == "marketing"
    except Exception:
        is_marketing = False
    role_map = marketing_map if is_marketing else comms_map
    default = "Senior Marketing hire" if is_marketing else "Senior Comms hire"
    for key, role in role_map:
        if key in keys:
            return role
    return default


def _imminence_mult(window_weeks: tuple | None) -> float:
    """Sooner predicted hiring window = hotter, more actionable opportunity
    for an AD. Scales the signal strength by how soon the comms hire is
    likely to be needed."""
    if (not window_weeks or window_weeks[0] is None
            or window_weeks[1] is None):
        return 0.85
    mid = (window_weeks[0] + window_weeks[1]) / 2.0
    if mid <= 8:
        return 1.25
    if mid <= 16:
        return 1.05
    if mid <= 26:
        return 0.90
    return 0.80


def opportunity_value(score: float, window_weeks: tuple | None = None) -> float:
    """Raw opportunity value for a predictor = signal strength (the ranker
    score: trigger weight × stacking × company tier × recency × UK) scaled
    by imminence (sooner predicted hiring window = hotter). Fed into the
    relative Low/Med/High tiering computed across the whole Pre-Market panel
    at render time (see dashboard._assign_opportunity_tiers)."""
    return (score or 0.0) * _imminence_mult(window_weeks)


def _serialise_stack(stk: Stack, score: float, now_iso: str) -> dict:
    w = window_for_stack(stk)
    account_tier = (
        "watchlist"
        if any(getattr(e, "account_tier", "watchlist") == "watchlist"
               for e in stk.events)
        else "off_watchlist"
    )
    return {
        "company": stk.company,
        "score": score,
        "depth": stk.depth,
        "account_tier": account_tier,
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

    aged = age_out(pipeline)
    purged = purge_off_watchlist(pipeline)

    # Refresh seeded contact names on EVERY active pipeline entry, not just
    # the ones in today's ranked_stacks. Otherwise an entry that fired e.g.
    # 2 weeks ago keeps its old seeded_contact_name forever (until aged
    # out), even after the contact has departed. Per-entry lookup is an
    # in-memory dict access against the current hiring_contacts.json, so
    # cost is negligible. Setting to None on stale/missing entries makes
    # the dashboard fall back to the generic role-search URL (the
    # safety-first behaviour: better generic than wrong-named).
    try:
        from tool.contacts.store import load_contacts
        from tool.linkedin_resolver import resolve_named_contact_for_predictor
        contacts = load_contacts()
        for _pid_key, entry in predictors.items():
            if entry.get("status") == "dismissed":
                continue
            predictor_dict = {
                "events": [
                    {"trigger_key": e.get("trigger_key"),
                     "company": entry.get("company", "")}
                    for e in (entry.get("events") or [])
                ]
            }
            named = resolve_named_contact_for_predictor(
                predictor_dict, contacts=contacts,
            )
            entry["seeded_contact_name"] = (named or {}).get("name")
            entry["seeded_contact_role"] = (named or {}).get("role")
    except Exception as e:
        log.exception("pipeline: failed to refresh seeded contacts: %s", e)

    save_pipeline(pipeline)

    total_active = sum(1 for p in predictors.values() if p.get("status") == "active")
    log.info("pipeline: %d new, %d updated, %d aged out, %d purged "
             "(off-universe / unnamed), %d active total",
             len(new_items), len(updated_items), aged, purged, total_active)
    return {
        "new": new_items,
        "updated": updated_items,
        "new_pids": new_pids,
        "total_active": total_active,
        "aged_out": aged,
        "purged": purged,
    }


_SENTINEL_FAILOPEN = "\x00__failopen__"

# Poisoned-aggregate guard. A pre-fix bug let hundreds of unrelated
# Google-News items collapse onto one watchlist name ("Google ×9
# +340"). The source fixes stop NEW such stacks, but _regate can't
# evict the EXISTING one: it concatenates every event's evidence into
# one blob, and across 340 headlines the token ("google") inevitably
# appears, so the blob keeps resolving and the entry is never purged
# (and re-merges every run, staying "fresh"). No legitimate single
# UK-comms company accumulates anywhere near this many distinct trigger
# headlines in the 90-day window — the largest genuine stacks observed
# are ~9 events ("+8 more"). A count this high is, by construction, a
# poisoned aggregate. Cap is set ~4-5x the largest legitimate stack so
# it cannot nuke a heavily-covered real company; and if an edge case
# ever did exceed it, the next brief rebuilds a correctly-sized stack
# from fresh signals (the fixes ensure clean aggregation now), so the
# worst case self-heals within one run with nothing false shown.
MAX_EVENTS_PER_ENTRY = 40


def _is_poisoned_aggregate(entry: dict) -> bool:
    evs = entry.get("events")
    return isinstance(evs, list) and len(evs) > MAX_EVENTS_PER_ENTRY


def _regate(entry: dict) -> str | None:
    """Re-validate a persisted predictor against the CURRENT account gate
    over its OWN evidence (the same tiered check detect_events applies to
    fresh signals). Returns the RESOLVED CANONICAL name (so callers can
    also fix a stale display name, e.g. legacy 'Brown' -> 'Brown-Forman'),
    or None if it no longer resolves (caller drops).

    Tier-aware: a broader-market (off_watchlist) entry was admitted off
    the watchlist BY DESIGN, so it is kept as long as it is still a
    well-formed employer name — it must NOT be purged for failing the
    watchlist subject scan. Watchlist (and legacy, tier-less) entries are
    still re-validated text-first, which is what evicts pre-fix garbage
    ('EQS', 'Capita' from 'Capital Signs…', 'Three UK' from 'Three
    arrested…').

    Fail-open: returns a sentinel (truthy, != any real name) if the gate
    can't run / errors, so a degraded watchlist never empties or rewrites
    the pipeline."""
    try:
        from tool.account_match import classify_account, _is_named_employer
    except Exception:
        return _SENTINEL_FAILOPEN
    company = (entry.get("company") or "").strip()
    parts: list[str] = []
    for e in (entry.get("events") or []):
        if isinstance(e, dict):
            ev, tl = e.get("evidence"), e.get("trigger_label")
            if ev:
                parts.append(str(ev))
            if tl:
                parts.append(str(tl))
    text = " . ".join(parts) or company
    try:
        if (entry.get("account_tier") or "watchlist") == "off_watchlist":
            return company if (company and _is_named_employer(company)) else None
        name, _tier = classify_account(company, text)
        return name
    except Exception:
        return _SENTINEL_FAILOPEN  # never nuke the pipeline on a gate error


def purge_off_watchlist(pipeline: dict) -> int:
    """Re-validate persisted predictors against the CURRENT account gate.

    age_out only expires by DATE. Without this, predictors created
    before the gate existed (or under an older watchlist / a buggy
    extractor) survive their full 90-day window — this is exactly why
    'EQS' (a wire prefix), 'Capita' (from "Capital Signs…"), 'Three UK'
    (from "Three arrested…") and foreign-subsidiary mentions kept
    showing on the board long after the gate would reject them.

    Two corrections per entry:
      * drop it if its own evidence no longer resolves;
      * otherwise CANONICALISE it — rewrite a stale display name to the
        resolved name (legacy 'Brown' -> 'Brown-Forman') and re-key to
        the canonical pid, merging onto an existing canonical entry
        (keep the earlier first_seen / higher score) so canonicalising
        can't create a duplicate.

    followed_up entries are preserved untouched (Sara's manual record),
    same carve-out as age_out. Returns the count removed."""
    removed = 0
    predictors = pipeline.get("predictors") or {}
    for pid, entry in list(predictors.items()):
        if entry.get("status") == "followed_up":
            continue
        if pid not in predictors:
            continue  # already merged away by a prior iteration
        if _is_poisoned_aggregate(entry):
            del predictors[pid]
            removed += 1
            continue
        resolved = _regate(entry)
        if resolved is None:
            del predictors[pid]
            removed += 1
            continue
        if resolved == _SENTINEL_FAILOPEN:
            continue  # gate degraded — leave entry untouched
        if resolved == entry.get("company"):
            continue  # already canonical
        # Canonicalise display name + pid.
        entry["company"] = resolved
        new_pid = _pid(resolved)
        entry["pid"] = new_pid
        if new_pid == pid:
            continue
        existing = predictors.get(new_pid)
        del predictors[pid]
        if existing is None:
            predictors[new_pid] = entry
        else:
            # Merge: keep the entry with the earlier first_seen, else the
            # higher score — never surface the same company twice.
            keep = existing
            try:
                if (entry.get("first_seen") or "") < (existing.get("first_seen") or "") \
                   or float(entry.get("score") or 0) > float(existing.get("score") or 0):
                    keep = entry
            except Exception:
                pass
            predictors[new_pid] = keep
    return removed


def age_out(pipeline: dict) -> int:
    """Remove predictors past their BD-Leads dashboard-retention window
    (tool.bd_retention): 30 days after FIRST_SEEN, or 90 days for
    followed-up entries (Sara's working record).

    Using first_seen (not last_seen) anchors the clock to when the lead
    was first presented, so a predictor stays live for its whole window
    even if the underlying signal stops generating fresh evidence — a CEO
    change reported 25 days ago is still actionable because the cascade
    hire is typically 6-12 weeks out. The durable triage overlay is
    authoritative for the followed-up carve-out so a followed-up lead is
    never dropped on the shorter clock just because the ephemeral pipeline
    status lagged a redeploy. Returns the count removed.
    """
    removed = 0
    predictors = pipeline.get("predictors") or {}
    overlay = _status_overlay()
    for pid, entry in list(predictors.items()):
        status = overlay.get(pid) or entry.get("status") or "active"
        anchor = entry.get("first_seen") or entry.get("last_seen")
        if bd_retention.is_expired(anchor, status):
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
    # Defensive re-gate on the READ path so the dashboard never shows an
    # entry the current account gate would reject — and shows the
    # canonical name (legacy 'Brown' -> 'Brown-Forman') — even before
    # the next morning brief persists the purge. In-memory only (no save
    # here); followed_up entries are always kept untouched.
    overlay = _status_overlay()
    kept: list[dict] = []
    for p in items:
        pid = p.get("pid") or _pid(p.get("company", ""))
        status = overlay.get(pid) or p.get("status") or "active"
        # Dashboard retention: hide a lead past its window (30d default /
        # 90d followed-up) on every tab, even before the next brief
        # physically prunes it from the pipeline.
        if bd_retention.is_expired(p.get("first_seen") or p.get("last_seen"),
                                   status):
            continue
        if status == "followed_up":
            kept.append(p)
            continue
        if _is_poisoned_aggregate(p):
            continue  # poisoned aggregate — hide (purged on next brief)
        resolved = _regate(p)
        if resolved is None:
            continue  # off-watchlist — hide
        if resolved != _SENTINEL_FAILOPEN and resolved != p.get("company"):
            p["company"] = resolved  # canonicalise display (not persisted here)
            p["pid"] = _pid(resolved)
        kept.append(p)
    items = kept
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
