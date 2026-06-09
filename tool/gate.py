"""Presentation gate — the hard rules between the pipeline and the board.

The deep-research blueprint's core governance finding: an Account Director
must never be shown a watching brief dressed up as a lead. This module
decides, for every scored row, PRESENTED (it earns a card) vs QUEUED (it
stays a hypothesis with a reason and a recheck date). It sits on top of
lead_engine.score_lead — the engine scores, the gate governs.

Hard rules (each checked in order, first failure queues the row):
  1. Investigation overlay — a /investigate verdict overrides everything:
     killed never presents; confirmed presents at High confidence.
  2. Hard blockers — competing recruiter, administration / hiring freeze.
  3. Amplifier-only stacks — press velocity and person-signals corroborate
     other triggers; alone they are never a lead.
  4. Window lapsed — the predicted hire window has closed.
  5. Too fresh — quality trigger inside its hold; queued with a recheck
     date for the day the presentation window opens.
  6. Action grade — monitor-grade stays queued; investigate-grade is
     queued AND flagged for the /investigate playbook.
  7. Evidence independence — >=3 independent source families with >=1
     primary (registry / RNS / regulator / procurement / SEC) presents at
     full strength; >=2 families with a primary-or-credible presents as
     partial. Thinner is queued. The auto-throttle (acceptance rate <50%
     over the trailing 7 days, min 10 verdicts) raises the bar to full
     only and drops the daily cap.

Everything is a pure function of (row, lead, verdicts, overlay, now) so
the whole gate is unit-testable with injected inputs. It never raises —
on malformed input it queues with a reason rather than guessing.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

# Board sizing (the report's "~7 cards/day"; throttled when acceptance dips).
DAILY_CAP = 7
THROTTLED_CAP = 5
# Auto-throttle thresholds: trailing window, minimum sample, floor.
THROTTLE_WINDOW_DAYS = 7
THROTTLE_MIN_VERDICTS = 10
THROTTLE_ACCEPT_FLOOR = 0.5

# Triggers that AMPLIFY another signal but never present alone.
AMPLIFIER_ONLY = {"press_velocity_spike", "personal_brand_velocity",
                  "leadership_tenure"}

# Primary (registry-grade) and credible (major-outlet) source fingerprints.
_PRIMARY_RX = re.compile(
    r"companies\s*house|companieshouse|investegate|\brns\b|london stock|"
    r"\bfca\b|ofcom|ofgem|ofwat|\bico\b|\bcma\b|regulator|find a tender|"
    r"contracts finder|sell2wales|etendersni|public contracts scotland|"
    r"sec edgar|sec\.gov|gov\.uk|charity commission", re.I)
_CREDIBLE_RX = re.compile(
    r"ft\.com|financial times|bloomberg|reuters|bbc|sky news|sky\.com|"
    r"thetimes|telegraph|guardian|cityam|standard\.co|prweek|campaign|"
    r"marketingweek|techcrunch|sifted|insidermedia|businesslive|"
    r"greenhouse|lever\.co|ashby|workable|adzuna|linkedin", re.I)


def _family(event: dict) -> str:
    """One token per independent source: the registrable host of the URL,
    else the normalised source label. Two RNS items share a family; an RNS
    item and a GDELT echo of it do not — that is the 'independent outlets'
    sense of corroboration the blueprint uses."""
    url = (event.get("url") or "").strip()
    if url:
        host = urlparse(url).netloc.lower()
        host = host[4:] if host.startswith("www.") else host
        if host:
            return host
    return re.sub(r"[^a-z0-9]+", " ", (event.get("source") or "").lower()).strip()


def source_evidence(events: list[dict]) -> dict:
    """Count independent source families and grade them."""
    fams: dict[str, str] = {}
    for e in events or []:
        if not isinstance(e, dict):
            continue
        fam = _family(e)
        if not fam:
            continue
        blob = f"{e.get('source') or ''} {e.get('url') or ''}"
        grade = ("primary" if _PRIMARY_RX.search(blob)
                 else "credible" if _CREDIBLE_RX.search(blob) else "other")
        # Best grade wins per family.
        order = {"primary": 2, "credible": 1, "other": 0}
        if order[grade] > order.get(fams.get(fam, "other"), -1) or fam not in fams:
            fams[fam] = grade
    n = len(fams)
    n_primary = sum(1 for g in fams.values() if g == "primary")
    n_credible = sum(1 for g in fams.values() if g == "credible")
    if n >= 3 and n_primary >= 1:
        level = "full"
    elif n >= 2 and (n_primary + n_credible) >= 1:
        level = "partial"
    else:
        level = "thin"
    return {"families": n, "primary": n_primary, "credible": n_credible,
            "level": level}


def window_state(events: list[dict], now: datetime | None = None) -> tuple[str, int]:
    """('open'|'lapsed'|'unknown', days_left). The window runs from the
    LATEST event to the widest lead_time max across the stack's triggers.
    Missing dates or unknown triggers degrade to 'unknown' (never block on
    absent data)."""
    from tool.predictive import patterns as P
    now = now or datetime.now(timezone.utc)
    latest, max_weeks = None, 0
    for e in events or []:
        if not isinstance(e, dict):
            continue
        t = P.BY_KEY.get(e.get("trigger_key"))
        if t:
            max_weeks = max(max_weeks, t.lead_time_weeks[1])
        try:
            d = datetime.fromisoformat((e.get("published") or "").replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            latest = d if latest is None or d > latest else latest
        except ValueError:
            continue
    if latest is None or not max_weeks:
        return "unknown", 0
    days_left = max_weeks * 7 - (now - latest).days
    return ("open" if days_left >= 0 else "lapsed"), days_left


def acceptance(verdicts: list[dict], now: datetime | None = None) -> dict:
    """Trailing-window acceptance rate. A verdict is a dict with 'date'
    (ISO) and 'verdict' in {'call_today','nurture','reject'}. Accepted =
    call_today + nurture (the sales-accepted-lead definition: the AD judged
    it real, even if not dialled today)."""
    now = now or datetime.now(timezone.utc)
    n = accepted = 0
    for v in verdicts or []:
        try:
            d = datetime.fromisoformat((v.get("date") or "").replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if (now - d).days > THROTTLE_WINDOW_DAYS:
            continue
        verdict = v.get("verdict")
        if verdict not in ("call_today", "nurture", "reject"):
            continue
        n += 1
        accepted += 1 if verdict in ("call_today", "nurture") else 0
    rate = (accepted / n) if n else None
    throttled = (n >= THROTTLE_MIN_VERDICTS and rate is not None
                 and rate < THROTTLE_ACCEPT_FLOOR)
    return {"n": n, "accepted": accepted, "rate": rate, "throttled": throttled,
            "cap": THROTTLED_CAP if throttled else DAILY_CAP}


# "What would kill this" — per strongest trigger, from the playbooks.
_KILL = {
    "ceo_change": "An interim or caretaker appointment; the change sits at a subsidiary VMA cannot access; the comms function is locked to an incumbent.",
    "chro_change": "An internal promotion with no mandate change; HR running a closed PSL process.",
    "cfo_change": "Routine succession with no comms or IR implication.",
    "chair_change": "A governance-only change with no executive reset behind it.",
    "comms_leader_departure": "The seat is being absorbed or backfilled internally; the departure was a planned retirement long flagged.",
    "ir_director_change": "An internal promotion; IR support already retained externally.",
    "mishire_reversal": "The exit was a restructure casualty rather than a failed hire; the function is being dissolved, not refilled.",
    "inhouse_search_failing": "The role was quietly filled or pulled for budget reasons rather than search failure.",
    "hiring_restart": "A one-off replacement rather than a genuine thaw; the post is junior or out of discipline.",
    "funding": "A bridge or down round signalling distress; capital earmarked for R&D with no go-to-market roles; a full marketing team already in place.",
    "secured_financing": "Refinancing of existing debt rather than growth capital.",
    "mna": "The comms function sits with the counterparty VMA cannot serve; integration is being handled by an incumbent agency.",
    "pe_acquisition": "The sponsor brings its own portfolio talent bench.",
    "ipo_listing": "The listing is shelved or moved off-exchange; advisers bring their own comms support.",
    "crisis_event": "The event is procedural rather than reputational; crisis support already retained.",
    "regulator_action": "A trivial or sector-wide notice with no firm-specific reputational exposure.",
    "regulator_probe_early": "The probe closes without action; it never becomes public-facing.",
    "profit_warning": "Cuts absorb the function rather than rebuilding it.",
    "restructure": "The restructure is financial (debt) not organisational.",
    "redundancy": "The programme is already fully supported by an incumbent.",
    "job_ad_cluster": "The cluster is recruiter repostings of one role; roles sit outside VMA's disciplines.",
    "ic_platform_rfp": "The platform decision is IT-led with no comms hire attached.",
    "contract_loss": "The loss is immaterial to the narrative; no reputational response planned.",
    "contract_end": "The incumbent is being rolled over without competition.",
    "water_sar": "The resilience warning is resolved without administration.",
    "framework_award": "The lot is irrelevant to VMA's disciplines.",
    "framework_displacement": "The incumbent's disruption does not touch this client relationship.",
}
_KILL_DEFAULT = ("A second independent source failing to appear; the signal "
                 "being about a different entity with a similar name.")


def kill_text(lead: dict, evidence: dict) -> str:
    """The card's 'What would kill this' — playbook kill-conditions for the
    strongest trigger, plus the evidence-specific weakest link."""
    triggers = (lead or {}).get("triggers") or []
    key = triggers[0].get("key") if triggers and isinstance(triggers[0], dict) else None
    parts = [_KILL.get(key, _KILL_DEFAULT)]
    if evidence.get("families", 0) < 3:
        parts.append("Weakest link: corroboration is still narrow — "
                     "one more independent source would confirm or kill it.")
    for c in (lead or {}).get("contradictions") or []:
        parts.append(f"Live contradiction: {c}.")
    return " ".join(parts)


def first_move(lead: dict, company: str) -> str:
    """The card's 'Suggested first move' — who to ring and the opening
    frame, from fields the engine already resolves."""
    who = (lead or {}).get("who_to_call") or "the hiring owner"
    access = ((lead or {}).get("access_text") or "").strip()
    move = f"Ring {who} at {company or 'the company'}."
    if access:
        move += f" Angle: {access}"
    trigs = (lead or {}).get("triggers") or []
    if trigs and isinstance(trigs[0], dict):
        t = trigs[0]
        age = t.get("age_days")
        when = f"{int(age)}d ago" if isinstance(age, (int, float)) else "recent"
        move += f" Anchor on the {t.get('label') or 'trigger'} ({when})."
    return move


def assess(item: dict, lead: dict, *, verdicts: list[dict] | None = None,
           investigation: dict | None = None,
           now: datetime | None = None) -> dict:
    """The gate decision for one row. Returns:
      presented      bool — earns a card
      confidence     'High' | 'Moderate' | None (queued rows have none)
      reasons        [str] — why queued (empty when presented)
      recheck_days   int|None — when to look again
      investigate    bool — queued specifically pending a playbook run
      evidence       source_evidence() dict
      kill / move    card fields (presented rows)
    Never raises; malformed input queues with a reason."""
    try:
        now = now or datetime.now(timezone.utc)
        lead = lead or {}
        events = [e for e in (item or {}).get("events") or [] if isinstance(e, dict)]
        ev = source_evidence(events)
        thr = acceptance(verdicts or [], now=now)
        out = {"presented": False, "confidence": None, "reasons": [],
               "recheck_days": None, "investigate": False, "evidence": ev,
               "kill": "", "move": "", "cap": thr["cap"],
               "throttled": thr["throttled"]}

        # 1. Investigation overlay outranks everything.
        inv = investigation or {}
        if inv.get("verdict") == "killed":
            out["reasons"].append("Killed by investigation"
                                  + (f": {inv.get('note')}" if inv.get("note") else ""))
            out["recheck_days"] = inv.get("recheck_days")
            return out
        confirmed = inv.get("verdict") == "confirmed"

        # 2. Hard blockers.
        if lead.get("conflict"):
            out["reasons"].append("Competing recruiter — conflict, never presented")
            return out
        anti = set(lead.get("anti_triggers") or [])
        for blocker in ("administration", "hiring_freeze"):
            if blocker in anti:
                out["reasons"].append(f"Hard blocker: {blocker.replace('_', ' ')}")
                out["recheck_days"] = 30
                return out

        # 3. Amplifier-only stacks never present alone.
        live_keys = {t.get("key") for t in (lead.get("triggers") or [])
                     if isinstance(t, dict) and (t.get("recency_mult") or 0) >= 0.3}
        live_keys.discard(None)
        if live_keys and live_keys <= AMPLIFIER_ONLY:
            out["reasons"].append("Amplifier-only signal (velocity / person watch) "
                                  "— corroborates a trigger, never a lead alone")
            out["recheck_days"] = 7
            return out

        # 4. Window lapsed.
        wstate, days_left = window_state(events, now=now)
        if wstate == "lapsed":
            out["reasons"].append(f"Predicted-hire window lapsed "
                                  f"({-days_left}d past)")
            return out

        # 5. Too fresh — present when the window opens.
        if lead.get("premature") and not confirmed:
            hold = int(lead.get("fresh_hold_days") or 21)
            age = lead.get("freshest_age_days")
            wait = max(1, hold - int(age)) if isinstance(age, (int, float)) else 7
            out["reasons"].append(f"Too fresh — presentation window opens in ~{wait}d")
            out["recheck_days"] = wait
            return out

        # 6. Action grade.
        action = lead.get("action")
        if action == "monitor" and not confirmed:
            out["reasons"].append("Watch-grade — needs corroboration before an AD sees it")
            out["recheck_days"] = 7
            return out
        if action == "investigate" and not confirmed:
            out["reasons"].append("Queued for /investigate — promising but unproven")
            out["investigate"] = True
            out["recheck_days"] = 3
            return out

        # 7. Evidence independence (throttle raises the bar to 'full').
        required_full = thr["throttled"]
        if ev["level"] == "thin" and not confirmed:
            out["reasons"].append(
                f"Evidence too thin: {ev['families']} independent source"
                f"{'s' if ev['families'] != 1 else ''}, {ev['primary']} primary "
                f"— needs 2+ with a primary or credible source")
            out["investigate"] = True
            out["recheck_days"] = 5
            return out
        if required_full and ev["level"] != "full" and not confirmed:
            out["reasons"].append("Throttled (acceptance <50% over 7d): "
                                  "only fully corroborated leads present")
            out["investigate"] = True
            out["recheck_days"] = 5
            return out

        # PRESENTED.
        out["presented"] = True
        multi_trigger = len(live_keys - AMPLIFIER_ONLY) >= 2
        no_contradictions = not (lead.get("contradictions") or [])
        if confirmed or (ev["level"] == "full" and multi_trigger and no_contradictions):
            out["confidence"] = "High"
        else:
            out["confidence"] = "Moderate"
        out["kill"] = kill_text(lead, ev)
        out["move"] = first_move(lead, (item or {}).get("company") or "")
        return out
    except Exception as e:  # pragma: no cover - safety net
        return {"presented": False, "confidence": None,
                "reasons": [f"Gate error ({type(e).__name__}) — queued for safety"],
                "recheck_days": 1, "investigate": False,
                "evidence": {"families": 0, "primary": 0, "credible": 0,
                             "level": "thin"},
                "kill": "", "move": "", "cap": DAILY_CAP, "throttled": False}
