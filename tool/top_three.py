"""Top-3 Action Surface — the daily forced-priority layer.

Rolls up every other event source on the dashboard (Today's Leads,
Predicted Briefs, Candidate Watch, Trade-Press Triggers, Cascade-Hire
Watch, Funding-Round Pre-Hire Window) into a single ranked list of
the three highest-leverage actions Sara should do RIGHT NOW.

Why this exists
===============
The dashboard is signal-rich; the constraint in a dead market is
consistent execution, not opportunity volume. Without a single
forced-priority surface, Sara browses six panels and picks something
that catches her eye. With one, she's pushed to act on the 3 things
the system says matter most today.

Architecture
============
* This module is read-only over the other modules' state files.
  Every event already exists in some panel; we just normalise +
  score + rank them into one common Action shape.
* User state (done / dismissed) is stored here keyed by a stable
  action_id derived from the underlying event. Recomputing Top-3
  each render preserves Sara's triage across signal updates.
* Diversity rule: max 2 of any one action_type in the final 3, so
  three fresh leads can't crowd out a cascade or a candidate float.

Scoring
=======
EV-ish heuristic, deliberately tunable. Per-action-type base scores
plus signal-specific boosts. The numbers below are starting points
that produce a sensible ordering against the live signal mix —
they're not calibrated to a fee dataset (which doesn't exist), so
expect to tune them once Sara's used the surface for a week.

  Cascade old-co  : 70 base (retained-search opportunity, strongest)
  Lead fresh new  : 55 + freshness boost
  Trade-press     : 45 + recency boost
  Cascade new-co  : 35 (slow-burn relationship)
  Predictor high% : 30 + probability boost + window boost
  Candidate (high drift) : 30 + drift contribution
  Funding         : 30 + window-proximity boost

Everything else (specialist signals, placement windows, events)
deliberately excluded — they're context, not actions you'd describe
in one sentence.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger("brief.top_three")

from tool.state_paths import state_dir, state_root
STATE_DIR = state_dir()
STATE_FILE = STATE_DIR / "top_three_state.json"

# How many actions to surface. Three is deliberate (forces ranking).
TOP_N = 3

# Diversity rule — at most this many of any one action type in the
# final Top-N. Prevents three fresh leads from monopolising the
# surface when a cascade or candidate move is also live.
MAX_PER_TYPE = 2


# ----- locking + atomic write (same pattern the other modules use) ---
try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked(path: Path):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
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


def _atomic_write(path: Path, content: str) -> None:
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


# ----- data model -----------------------------------------------------
@dataclass
class Action:
    action_id: str          # stable hash of underlying event
    action_type: str        # lead | predictor | trade_press | cascade_old |
                            # cascade_new | candidate | funding
    score: float            # higher = act sooner
    title: str              # short headline ("Severn Trent — Carla Sherry")
    why_now: str            # one-line reason
    opener: str             # pre-drafted outreach text (empty if N/A)
    detail_url: str = ""    # deep-link to the source row / article
    type_badge: str = ""    # display label for the type (e.g. "CASCADE")
    secondary: str = ""     # optional second-line context


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----- state (per-action done/dismissed overlay) ---------------------
def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        d = json.loads(STATE_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    payload = json.dumps(state, indent=2)
    _atomic_write(STATE_FILE, payload)
    try:
        from tool import github_state
        github_state.push_async(
            "tool/state/top_three_state.json", payload,
            "state: update Top-3 action triage")
    except Exception:
        pass


def mark(action_id: str, status: str) -> bool:
    """Set per-action state. status ∈ {active, done, dismissed}."""
    if status not in {"active", "done", "dismissed"} or not action_id:
        return False
    with _locked(STATE_FILE):
        state = _load_state()
        if status == "active":
            state.pop(action_id, None)
        else:
            state[action_id] = {"status": status, "set_at": _now_iso()}
        _save_state(state)
    return True


def _is_suppressed(action_id: str, state: dict) -> bool:
    entry = state.get(action_id)
    if not entry:
        return False
    if entry.get("status") in {"done", "dismissed"}:
        return True
    return False


# ----- source readers (each returns a list of Action candidates) ------
def _safe_load(name: str):
    p = STATE_DIR / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _candidates_from_leads() -> list[Action]:
    """Today's Leads → reactive 'call this contact about this news'
    actions. Only active (not-yet-followed-up, not-dismissed) leads."""
    raw = _safe_load("latest_signals.json")
    if not isinstance(raw, list):
        return []
    # The dashboard applies the same filters in load_latest_signals.
    leads = [s for s in raw
             if (s.get("kind") or "").strip().lower() != "leadership_change"
             and (s.get("company") or "").strip()]

    # Apply Sara's triage from lead_status — done leads aren't actions.
    try:
        from tool.lead_status import get_statuses
        triage = get_statuses()
    except Exception:
        triage = {}

    actions: list[Action] = []
    for s in leads:
        lead_id = s.get("lead_id") or s.get("id")
        if not lead_id:
            continue
        if triage.get(lead_id) in {"followed_up", "dismissed"}:
            continue

        company = (s.get("company") or "").strip()
        title = (s.get("title") or "").strip()
        is_new = bool(s.get("is_new"))
        contact = s.get("contact") or {}
        contact_name = (contact.get("name") or "").strip()
        contact_role = (contact.get("title") or "").strip()

        score = 55.0
        if is_new:
            score += 20.0
        # Lead has a resolved named contact → easier to act on.
        if contact_name:
            score += 8.0

        display_title = (f"{company} — {contact_name}" if contact_name
                         else company)
        why_now = (title[:140] +
                   ("…" if len(title) > 140 else ""))
        secondary = (f"{contact_role} · confidence "
                     f"{int((contact.get('confidence') or 0) * 100)}%"
                     if contact_role else "")

        actions.append(Action(
            action_id=f"lead:{lead_id}",
            action_type="lead",
            score=score,
            title=display_title,
            why_now=why_now,
            opener=(s.get("outreach") or "").strip(),
            detail_url=s.get("url") or "",
            type_badge="LEAD",
            secondary=secondary,
        ))
    return actions


def _candidates_from_predictors() -> list[Action]:
    """Predicted Briefs → forward-looking 'reach out before they hit
    market' actions. Only active predictors, weighted by probability +
    window proximity."""
    raw = _safe_load("latest_predictive.json")
    # latest_predictive.json may be a dict with .predictors or just a list.
    if isinstance(raw, dict):
        preds = raw.get("predictors") or raw.get("rows") or []
    elif isinstance(raw, list):
        preds = raw
    else:
        preds = []

    try:
        from tool.predictor_status import get_statuses as get_pstatuses
        pstatus = get_pstatuses()
    except Exception:
        pstatus = {}

    actions: list[Action] = []
    for p in preds:
        pid = p.get("pid") or p.get("id")
        if not pid:
            continue
        if pstatus.get(pid) in {"followed_up", "dismissed"}:
            continue
        if (p.get("status") or "active") != "active":
            continue
        company = (p.get("company") or "").strip()
        if not company:
            continue
        role = (p.get("predicted_role") or "").strip()
        prob = float(p.get("probability") or 0)
        # window label like "≈30 days" or "≈60 days" — pull the number
        # if present for a proximity boost.
        window_lbl = (p.get("window_label") or "").lower()
        window_days = 999
        for tok in window_lbl.replace("≈", "").split():
            try:
                window_days = int(tok)
                break
            except ValueError:
                pass

        score = 30.0
        score += min(40.0, prob * 0.5)   # 80% → +40
        if window_days <= 30:
            score += 20.0
        elif window_days <= 60:
            score += 10.0
        elif window_days <= 90:
            score += 5.0

        title = (f"{company} — {role}" if role else company)
        why_now = ""
        events = p.get("events") or []
        if events:
            why_now = (events[0].get("trigger_label") or "")
            ev_text = (events[0].get("evidence") or "")
            if ev_text:
                why_now = (why_now + ": " + ev_text)[:140]
        actions.append(Action(
            action_id=f"predictor:{pid}",
            action_type="predictor",
            score=score,
            title=title,
            why_now=why_now,
            opener=(p.get("outreach") or "").strip(),
            type_badge=f"PREDICTOR · {int(prob)}%",
            secondary=window_lbl or "",
        ))
    return actions


def _candidates_from_trade_press() -> list[Action]:
    """Build A output. Active triggers only."""
    try:
        from tool import trade_press
        events = trade_press.list_active()
    except Exception:
        events = []
    actions: list[Action] = []
    now = datetime.now(timezone.utc)
    for e in events:
        ev_id = e.get("event_id")
        if not ev_id:
            continue
        score = 45.0
        # Recency boost — articles in the last 48h convert higher.
        try:
            detected = datetime.fromisoformat(
                (e.get("detected_at") or "").replace("Z", "+00:00"))
            age_hours = (now - detected).total_seconds() / 3600.0
            if age_hours <= 48:
                score += 20.0
            elif age_hours <= 96:
                score += 10.0
        except Exception:
            pass

        actions.append(Action(
            action_id=f"trade_press:{ev_id}",
            action_type="trade_press",
            score=score,
            title=f"{e.get('person_name','')}" +
                  (f" ({e.get('person_company','')})"
                   if e.get('person_company') else ""),
            why_now=f"{e.get('hook_type','featured')} in "
                    f"{e.get('source_name','trade press')}: "
                    f"\"{(e.get('article_title') or '')[:100]}\"",
            opener=e.get("opener") or "",
            detail_url=e.get("article_url") or "",
            type_badge="TRADE PRESS",
            secondary=e.get("source_name") or "",
        ))
    return actions


def _candidates_from_cascade() -> list[Action]:
    """Build B output. Each cascade event produces UP TO TWO actions —
    the old-company replacement-search angle and the new-company
    re-org angle — surfaced independently with different base scores."""
    try:
        from tool import cascade
        events = cascade.list_active()
    except Exception:
        events = []
    actions: list[Action] = []
    for e in events:
        ev_id = e.get("event_id")
        if not ev_id:
            continue
        person = e.get("person_name", "")
        role = e.get("role", "")

        # Old-company side: replacement search. Highest-EV cascade
        # angle because it's a fast retained-search opportunity.
        if (e.get("old_co_status", "active") == "active"
                and e.get("old_company") and e.get("old_co_opener")):
            actions.append(Action(
                action_id=f"cascade:{ev_id}:old_co",
                action_type="cascade_old",
                score=70.0,
                title=f"{e.get('old_company')} — replacement search",
                why_now=f"{person} just left as {role}; team reshape likely",
                opener=e.get("old_co_opener") or "",
                detail_url=e.get("article_url") or "",
                type_badge="CASCADE · OLD CO",
                secondary=f"left {e.get('old_company','')}",
            ))

        # New-company side: 6-12 month re-org watch. Slower burn,
        # lower base score — but still worth surfacing when nothing
        # higher is on the board.
        if e.get("new_co_status", "active") == "active":
            actions.append(Action(
                action_id=f"cascade:{ev_id}:new_co",
                action_type="cascade_new",
                score=35.0,
                title=f"{e.get('new_company','')} — re-org watch",
                why_now=f"{person} just hired as {role}; "
                        f"6–12mo team reshape window",
                opener=e.get("new_co_opener") or "",
                detail_url=e.get("article_url") or "",
                type_badge="CASCADE · NEW CO",
                secondary=f"joined {e.get('new_company','')}",
            ))
    return actions


def _candidates_from_candidate_watch() -> list[Action]:
    """Build C output (signal-drift scoring on Candidate Watch). Each
    high-drift candidate becomes a 'float them today' action — but
    only when the drift score clears a threshold, so a cold watch
    list doesn't fill Top-3 with low-value reminders."""
    try:
        from tool.candidate_watch import list_watched
        rows = list_watched()
    except Exception:
        rows = []

    actions: list[Action] = []
    for c in rows[:10]:    # don't even consider beyond top 10 by drift
        drift = float(c.get("_drift_score") or 0)
        if drift < 35:
            continue
        name = c.get("name", "")
        if not name:
            continue
        # action_id stable on name+company so dismissing once doesn't
        # mean the same candidate re-surfaces tomorrow with a fresh id.
        key = hashlib.sha1(
            f"{name.lower()}|{(c.get('current_company') or '').lower()}"
            .encode("utf-8")).hexdigest()[:12]
        score = 30.0 + min(40.0, drift * 0.4)
        reasons = c.get("_drift_reasons") or []
        why_now = ("Liquidity signals: " + "; ".join(reasons[:3])
                   if reasons else "High liquidity score")
        actions.append(Action(
            action_id=f"candidate:{key}",
            action_type="candidate",
            score=score,
            title=(f"Float {name}" +
                   (f" ({c.get('current_company','')})"
                    if c.get('current_company') else "")),
            why_now=why_now,
            opener="",
            detail_url=c.get("linkedin_url") or "",
            type_badge="CANDIDATE",
            secondary=c.get("current_title") or "",
        ))
    return actions


def _candidates_from_funding() -> list[Action]:
    """Funding-Round Pre-Hire Window → 'pitch the recently-funded firm
    on their incoming comms hire' actions."""
    raw = _safe_load("latest_funding.json")
    if not isinstance(raw, list):
        return []
    actions: list[Action] = []
    for f in raw[:20]:
        company = (f.get("company") or "").strip()
        if not company:
            continue
        key = hashlib.sha1(company.lower().encode("utf-8")).hexdigest()[:12]
        score = 30.0
        # Window-proximity boost — comms hire ~3-6mo after close.
        days_since_i: int | None = None
        try:
            days_since_i = int(f.get("days_since_announce"))
            if 30 <= days_since_i <= 120:
                score += 18.0   # sweet spot
            elif 0 <= days_since_i < 30:
                score += 8.0
        except (TypeError, ValueError):
            pass
        amount = (f.get("raise_label") or f.get("amount") or "").strip()
        stage = (f.get("stage") or "").strip()
        why_bits = [b for b in [amount, stage,
                                f"{days_since_i}d ago"
                                if days_since_i is not None else ""]
                    if b]
        actions.append(Action(
            action_id=f"funding:{key}",
            action_type="funding",
            score=score,
            title=f"{company} — pre-hire window",
            why_now=" · ".join(why_bits) or "recent funding",
            opener="",
            detail_url=f.get("url") or "",
            type_badge="FUNDING",
            secondary=amount,
        ))
    return actions


# ----- the ranker -----------------------------------------------------
_SOURCE_FUNCS = [
    _candidates_from_leads,
    _candidates_from_predictors,
    _candidates_from_cascade,
    _candidates_from_candidate_watch,
    _candidates_from_funding,
]


def compute_top(n: int = TOP_N) -> list[dict]:
    """Return the top-n actions across every source as dicts ready to
    render. Applies user-state suppression (done/dismissed) and the
    diversity rule (max MAX_PER_TYPE per action_type)."""
    state = _load_state()

    all_actions: list[Action] = []
    for fn in _SOURCE_FUNCS:
        try:
            all_actions.extend(fn())
        except Exception as e:
            log.info("top_three: source %s failed: %s", fn.__name__, e)

    # Drop ones Sara has triaged.
    fresh = [a for a in all_actions if not _is_suppressed(a.action_id, state)]
    # Highest score first.
    fresh.sort(key=lambda a: a.score, reverse=True)

    # Apply the diversity cap.
    picked: list[Action] = []
    by_type: dict[str, int] = {}
    for a in fresh:
        if by_type.get(a.action_type, 0) >= MAX_PER_TYPE:
            continue
        picked.append(a)
        by_type[a.action_type] = by_type.get(a.action_type, 0) + 1
        if len(picked) >= n:
            break

    return [asdict(a) for a in picked]


# ----- CLI ------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    import pprint
    pprint.pprint(compute_top())
