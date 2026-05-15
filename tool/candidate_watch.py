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
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("brief.candidate_watch")

STATE_DIR = Path(__file__).resolve().parent / "state"
WATCH_FILE = STATE_DIR / "candidate_watch.json"


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
    WATCH_FILE.parent.mkdir(exist_ok=True, parents=True)
    WATCH_FILE.write_text(json.dumps(data, indent=2))


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


def list_watched(include_snoozed: bool = False) -> list[dict]:
    """Return all watched candidates, scored and sorted by call urgency.

    Score = max(0, days_since_touched - cadence) + 10*restlessness_hits.
    Higher score = call sooner. Snoozed candidates are filtered out
    unless `include_snoozed=True` (so Sara can review who she's
    deliberately deferred)."""
    rows = _load_all()
    today = date.today()
    out: list[dict] = []
    for c in rows:
        snooze = _parse_iso(c.get("snoozed_until", ""))
        if snooze and snooze > today and not include_snoozed:
            continue
        days = _days_since(c.get("last_touched", ""))
        cadence = int(c.get("touch_cadence_days") or 30)
        if days is None:
            overdue = max(cadence, 1)   # never touched → maximally overdue
            label = "never touched"
        else:
            overdue = max(0, days - cadence)
            label = f"touched {days}d ago (cadence {cadence}d)"
        restlessness_hits = _restlessness_score(c.get("last_signal", ""))
        score = overdue + (10 * restlessness_hits)
        decorated = dict(c)
        decorated["_days_since_touched"] = days
        decorated["_overdue_days"]       = overdue
        decorated["_status_label"]       = label
        decorated["_restlessness_hits"]  = restlessness_hits
        decorated["_urgency_score"]      = score
        out.append(decorated)
    out.sort(key=lambda r: r["_urgency_score"], reverse=True)
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
                  touch_cadence_days: int = 30) -> dict:
    """Add a new candidate to the watch list. If a candidate with the
    same name+current_company already exists, it's updated rather
    than duplicated."""
    rows = _load_all()
    key_name = name.strip().lower()
    key_co   = current_company.strip().lower()
    for r in rows:
        if (r.get("name", "").strip().lower() == key_name and
            r.get("current_company", "").strip().lower() == key_co):
            # update in place
            r.update({
                "current_title":      current_title or r.get("current_title", ""),
                "linkedin_url":       linkedin_url  or r.get("linkedin_url", ""),
                "sectors":            sectors       or r.get("sectors", []),
                "notes":              notes         or r.get("notes", ""),
                "touch_cadence_days": touch_cadence_days,
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
    ))
    rows.append(new)
    _save_all(rows)
    return new


def mark_touched(name: str, current_company: str = "",
                 signal: str = "") -> dict | None:
    """Mark a candidate as just-touched. Optionally records a free-text
    signal note Sara observed (drives the restlessness score)."""
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


def snooze_candidate(name: str, current_company: str, days: int) -> dict | None:
    rows = _load_all()
    key_name = name.strip().lower()
    key_co   = current_company.strip().lower()
    for r in rows:
        if r.get("name", "").strip().lower() != key_name:
            continue
        if key_co and r.get("current_company", "").strip().lower() != key_co:
            continue
        r["snoozed_until"] = (date.today() + timedelta(days=max(1, days))).isoformat()
        _save_all(rows)
        return r
    return None


def remove_candidate(name: str, current_company: str = "") -> bool:
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
