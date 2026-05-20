"""Candidate signal-drift scoring.

Replaces the old time-based "X days overdue" urgency ordering with a
continuous liquidity score that updates whenever a candidate's
external public state moves. The point of the build: stop ranking
candidates by *when Sara last called them*, start ranking by
*whether the world is signalling they're movable right now*.

What feeds the score
====================
All inputs are zero-cost — they come from state files we already
produce, no new scraping required:

  1. Tenure clock — months in current role. Ramps at the published
     24-/36-/48-month thresholds (RRA CFO Turnover Report 2026
     documented the shortening senior-tenure trend; same pattern
     applies across the C-suite).
  2. Cascade proximity — does the candidate's CURRENT company appear
     in a cascade event in the last 30 days? Their employer is in
     flux; team reshapes follow.
  3. News mention — does the candidate's name appear in today's
     latest_signals.json (the morning brief's news output)?
     A senior-comms person showing up in news is a personal-brand
     visibility uptick.
  4. Trade-press mention — same name in the trade-press events file?
     This is the Build A output already running.
  5. Manual restlessness — the legacy `last_signal` keyword score
     (kept; Sara-entered notes are high-precision when present).
  6. Cadence overdue — still factored in, but reduced weight so a
     30-day-overdue cold candidate doesn't outrank a 24-month-tenure
     candidate whose company just hit a cascade event.

Score ranges per signal are tuned so the AUTO signals can dominate
over cadence-only on their own merits — that's the point of the
build.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("brief.candidate_drift")

STATE_DIR = Path(__file__).resolve().parent / "state"


# ----- helpers --------------------------------------------------------
def _parse_iso_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _months_since(start_iso: str) -> int | None:
    d = _parse_iso_date(start_iso)
    if not d:
        return None
    today = date.today()
    months = (today.year - d.year) * 12 + (today.month - d.month)
    if today.day < d.day:
        months -= 1
    return max(0, months)


def _load_json(name: str) -> list | dict:
    """Best-effort read of a state file. Empty if absent or malformed —
    the scorer must never crash because a feeder is empty."""
    p = STATE_DIR / name
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log.info("drift: load %s failed: %s", name, e)
        return []


# ----- per-signal scorers (each returns 0 or a positive int) ----------
def _tenure_score(tenure_months: int | None) -> int:
    """Step function on tenure-in-current-role. The 24/36/48-month
    thresholds match published senior-tenure churn patterns."""
    if tenure_months is None:
        return 0
    if tenure_months < 18:
        return 0
    if tenure_months < 24:
        return 5
    if tenure_months < 30:
        return 15
    if tenure_months < 36:
        return 20
    if tenure_months < 48:
        return 25
    return 30


def _cascade_proximity_score(current_company: str,
                             cascade_events: list) -> tuple[int, str]:
    """Did the candidate's CURRENT company appear in a cascade event
    in the last 30 days? If so, their employer is reshaping the comms
    team — strong liquidity signal. Returns (score, reason)."""
    if not current_company:
        return 0, ""
    co_lc = current_company.strip().lower()
    if not co_lc:
        return 0, ""
    cutoff = datetime.utcnow() - timedelta(days=30)
    best = 0
    reason = ""
    for ev in cascade_events:
        try:
            detected = datetime.fromisoformat(
                (ev.get("detected_at") or "").replace("Z", "+00:00"))
            if detected.tzinfo is not None:
                detected = detected.replace(tzinfo=None)
            if detected < cutoff:
                continue
        except Exception:
            continue
        new_co = (ev.get("new_company") or "").lower()
        old_co = (ev.get("old_company") or "").lower()
        if co_lc == old_co or co_lc in old_co or old_co in co_lc:
            best = max(best, 30)
            reason = f"{ev.get('person_name','')} just left {ev.get('old_company','')}"
        elif co_lc == new_co or co_lc in new_co or new_co in co_lc:
            best = max(best, 20)
            reason = f"{ev.get('new_company','')} just hired a new {ev.get('role','')} — team reshape likely"
    return best, reason


def _news_mention_score(name: str, latest_signals: list) -> tuple[int, str]:
    """Did the candidate's name appear in this morning's news output?
    Cheap check against title + summary fields."""
    if not name:
        return 0, ""
    nm_lc = name.strip().lower()
    if len(nm_lc) < 4:
        return 0, ""
    for s in latest_signals:
        hay = " ".join([
            (s.get("title") or "").lower(),
            (s.get("summary") or "").lower(),
        ])
        if nm_lc in hay:
            return 25, f"named in {s.get('source','news')}: \"{(s.get('title') or '')[:80]}\""
    return 0, ""


def _trade_press_score(name: str, tp_events: list) -> tuple[int, str]:
    """Personal-brand uptick — the candidate appeared in a Build-A
    trade-press event. We already drafted an opener for that; here it
    just lifts the liquidity score."""
    if not name:
        return 0, ""
    nm_lc = name.strip().lower()
    for ev in tp_events:
        if (ev.get("person_name") or "").lower() == nm_lc:
            hook = ev.get("hook_type", "featured")
            return 20, f"{hook} in {ev.get('source_name','trade press')}"
    return 0, ""


_RESTLESSNESS_KEYWORDS = [
    "updated profile", "updated linkedin", "new headline", "new title",
    "posting again", "active again", "posts more",
    "new connections", "added contacts",
    "team restructure", "team cut", "boss left", "new manager", "lost mandate",
    "team reduced", "function moved", "reorg", "made redundant",
    "open to a move", "open to opportunities", "exploring options",
    "wants to leave", "ready to move", "fed up", "burned out",
    "asked for a coffee", "reached out", "messaged me",
]


def _manual_restlessness_score(last_signal: str) -> tuple[int, int]:
    """Returns (score, hit_count). Sara-entered notes are high
    precision when present — keep them as a strong input but bound the
    contribution so a single hit doesn't dominate every auto signal."""
    if not last_signal:
        return 0, 0
    low = last_signal.lower()
    hits = sum(1 for kw in _RESTLESSNESS_KEYWORDS if kw in low)
    return min(50, hits * 12), hits


def _overdue_score(last_touched: str, cadence: int) -> tuple[int, int]:
    """Cadence still matters — being overdue is a signal — just no
    longer the dominant one. Linear up to a 20-point cap."""
    d = _parse_iso_date(last_touched)
    if not d:
        return 20, -1   # never touched → max overdue
    days_since = (date.today() - d).days
    overdue = max(0, days_since - cadence)
    return min(20, int(overdue * 0.5)), days_since


# ----- public API -----------------------------------------------------
def compute_drift(candidate: dict) -> dict:
    """Returns a dict ready to merge onto a candidate row:
        _drift_score, _drift_breakdown, _drift_reasons,
        _tenure_months (if computable), _days_since_touched,
        _overdue_days
    Pure-functional — never mutates the candidate dict.
    """
    cascade_events = _load_json("cascade_events.json")
    tp_events = _load_json("trade_press_events.json")
    latest_signals = _load_json("latest_signals.json")
    if not isinstance(cascade_events, list):
        cascade_events = []
    if not isinstance(tp_events, list):
        tp_events = []
    if not isinstance(latest_signals, list):
        latest_signals = []

    tenure_months = _months_since(candidate.get("tenure_start", ""))
    s_tenure = _tenure_score(tenure_months)

    s_cascade, r_cascade = _cascade_proximity_score(
        candidate.get("current_company", ""), cascade_events)
    s_news, r_news = _news_mention_score(
        candidate.get("name", ""), latest_signals)
    s_tp, r_tp = _trade_press_score(
        candidate.get("name", ""), tp_events)
    s_manual, manual_hits = _manual_restlessness_score(
        candidate.get("last_signal", ""))

    cadence = int(candidate.get("touch_cadence_days") or 30)
    s_overdue, days_since = _overdue_score(
        candidate.get("last_touched", ""), cadence)
    overdue_days = -1 if days_since == -1 else max(0, days_since - cadence)

    total = s_tenure + s_cascade + s_news + s_tp + s_manual + s_overdue

    breakdown = {
        "tenure":     s_tenure,
        "cascade":    s_cascade,
        "news":       s_news,
        "trade_press": s_tp,
        "manual":     s_manual,
        "overdue":    s_overdue,
    }

    reasons: list[str] = []
    if tenure_months is not None and s_tenure > 0:
        reasons.append(f"{tenure_months}mo in role")
    if r_cascade:
        reasons.append(r_cascade)
    if r_news:
        reasons.append(r_news)
    if r_tp:
        reasons.append(r_tp)
    if manual_hits > 0:
        reasons.append(f"{manual_hits} restlessness note(s)")
    if overdue_days > 0:
        reasons.append(f"{overdue_days}d overdue")
    elif days_since == -1:
        reasons.append("never touched")

    return {
        "_drift_score":     total,
        "_drift_breakdown": breakdown,
        "_drift_reasons":   reasons,
        "_tenure_months":   tenure_months,
        "_days_since_touched": None if days_since == -1 else days_since,
        "_overdue_days":    0 if overdue_days < 0 else overdue_days,
        # Legacy fields kept for backward compat with existing UI/JS
        # that still reads them. New code should use _drift_score.
        "_urgency_score":      total,
        "_restlessness_hits":  manual_hits,
        "_status_label":       _legacy_label(days_since, cadence),
    }


def _legacy_label(days_since: int, cadence: int) -> str:
    if days_since == -1:
        return "never touched"
    return f"touched {days_since}d ago (cadence {cadence}d)"
