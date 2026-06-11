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
  7. The qualification scorecard — the four dimensions an elite AD
     evidences before spending an hour: a live-or-imminent senior SEAT,
     BUDGET/fundability, URGENCY, and a reachable BUYER (each 0-2, from
     the collated company data). Present needs seat>=1 and total>=5 of 8.
     Source-counting is demoted to per-fact VERIFICATION: a registry-
     attested fact (Companies House / RNS / regulator) is true on its
     own — quiet companies with thin press are not less qualified; only
     a lone NON-registry source still queues for /investigate. The
     auto-throttle (acceptance <50% over 7 days, min 10 verdicts) raises
     the qualification bar to 6 and drops the daily cap.

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

# Bronze-tier triggers: genuinely predictive only as corroboration of a
# Tier-1/2 trigger (leadership change, M&A, PE, crisis, funding...), per
# the research tiering. Deliberately EXCLUDES job_ad_cluster and
# hiring_gap — a live cluster with no internal recruiter is direct
# evidence of external hiring happening now, our strongest call route.
BRONZE_KEYS = {"rebrand", "agency_account_move", "framework_award",
               "esg_bcorp", "martech_adoption", "market_entry"}

# ---- Qualification dimensions (the AD scorecard) -------------------------
# (a) SEAT: a live/structurally-open seat beats an imminent one beats none.
SEAT_LIVE_KEYS = {"comms_leader_departure", "mishire_reversal",
                  "inhouse_search_failing", "job_ad_cluster",
                  "ic_platform_rfp", "hiring_gap", "seniority_gap",
                  # An advertised interim cover evidences a structurally
                  # open senior seat — the perm search follows it.
                  "interim_watch"}
SEAT_IMMINENT_KEYS = {"ceo_change", "chair_change", "chro_change",
                      "cfo_change", "cmo_change", "market_entry",
                      "ir_director_change", "mna",
                      "pe_acquisition", "ipo_listing", "ownership_change",
                      "crisis_event", "regulator_action",
                      "regulator_probe_early", "restructure", "redundancy",
                      "profit_warning", "contract_loss", "hiring_restart",
                      "funding", "secured_financing", "water_sar",
                      "contract_end",
                      # A landed senior leader creates seats 1-2 quarters out.
                      "follow_on"}
# (b) BUDGET: capital events that fund an external build.
BUDGET_KEYS = {"funding", "secured_financing", "ipo_listing",
               "pe_acquisition", "ownership_change"}
# (c) URGENCY: forced, deadline- or failure-driven situations.
URGENT_KEYS = {"crisis_event", "regulator_action", "water_sar",
               "mishire_reversal", "inhouse_search_failing", "redundancy",
               "contract_end"}


def qualification(lead: dict, item: dict, ev: dict, wstate: str) -> dict:
    """The four-dimension AD scorecard, each 0-2, computed from the
    collated company data (events, posture/propensity, financial
    direction, contacts, window). This — not source volume — is what
    decides presentation."""
    lead, item = lead or {}, item or {}
    live = [t for t in (lead.get("triggers") or [])
            if isinstance(t, dict) and (t.get("recency_mult") or 0) >= 0.3]
    keys = {t.get("key") for t in live} - {None}

    # (a) Seat.
    if keys & SEAT_LIVE_KEYS:
        seat, seat_why = 2, "a live seat / structural gap is evidenced"
    elif keys & SEAT_IMMINENT_KEYS:
        seat, seat_why = 1, ("a seat-creating trigger is live; the role is "
                             "imminent, not yet advertised")
    else:
        seat, seat_why = 0, "no trigger that opens a senior seat"

    # (b) Budget / fundability.
    prop_pts, _ = propensity_points(lead, item)
    fin = (lead.get("financial") or {}).get("direction")
    contras = lead.get("contradictions") or []
    if fin in ("anti", "conflicting") or prop_pts == PROP_INTERNAL or any(
            "budget" in c or "cuts" in c for c in contras):
        budget, budget_why = 0, "budget direction points the wrong way"
    elif keys & BUDGET_KEYS or prop_pts >= PROP_PROVEN or fin == "pro":
        budget, budget_why = 2, ("fresh capital / proven fee-payer / growth "
                                 "funding the build")
    else:
        budget, budget_why = 1, "no budget evidence either way"

    # (c) Urgency.
    demand_now = any(t.get("key") in ("job_ad_cluster", "ic_platform_rfp")
                     and (t.get("recency_mult") or 0) >= 0.6 for t in live)
    if keys & URGENT_KEYS or demand_now:
        urgency, urgency_why = 2, "forced / failure-driven / live-demand timing"
    elif lead.get("premature") or wstate == "lapsed":
        urgency, urgency_why = 0, "outside the actionable window"
    else:
        urgency, urgency_why = 1, "inside the predicted window"

    # (d) Reachable buyer WITH a personal reason to engage (Savage: the
    # research that moves a cold call to a warm one). Named contact or a
    # warm relationship scores 2; a mapped buying seat plus a concrete
    # access angle scores 1; nothing scores 0.
    named = bool(item.get("seeded_contact_name")
                 or item.get("linkedin_profile_name"))
    warm = (lead.get("relationship") == "warm") or bool(item.get("contact_on_file"))
    angle = bool((lead.get("access_text") or "").strip())
    if named or warm:
        buyer = 2
        buyer_why = ("a named decision-maker is on file"
                     if named else "an existing relationship to open with")
        if angle:
            buyer_why += " + a concrete access angle"
    elif (lead.get("who_to_call") or "").strip():
        buyer, buyer_why = 1, ("the buying seat is mapped"
                               + (" with an access angle" if angle
                                  else "; no named contact yet"))
    else:
        buyer, buyer_why = 0, "no route to a decision-maker"

    dims = [("seat", seat, seat_why), ("budget", budget, budget_why),
            ("urgency", urgency, urgency_why), ("buyer", buyer, buyer_why)]
    weakest = min(dims, key=lambda d: d[1])
    return {"seat": seat, "seat_why": seat_why,
            "budget": budget, "budget_why": budget_why,
            "urgency": urgency, "urgency_why": urgency_why,
            "buyer": buyer, "buyer_why": buyer_why,
            "total": seat + budget + urgency + buyer,
            "weakest_why": weakest[2]}

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
    "cmo_change": "An internal promotion with the team inherited intact; the incoming CMO brings their own bench from a previous employer.",
    "market_entry": "The entry is distribution-only or runs through a local partner; in-country marketing stays at regional HQ.",
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
    if (evidence.get("primary") or 0) == 0 and (evidence.get("families") or 0) < 2:
        parts.append("Weakest link: the fact rests on a single non-registry "
                     "source — a filing or second outlet would confirm or "
                     "kill it.")
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


# ---- Lead Strength (the board's 0-100 ordering score) --------------------
# Weighted the way a recruitment AD qualifies: fit 20 + trigger signal
# 30 + fee-propensity 25 + reachable buyer 15 + timing 10 = 100, with
# contradictions subtracting and hard blocks flooring. Source-counting
# does not score at all — verification is a gate concern (a registry
# fact is true on its own), shown on the card as a tag. The card shows
# the evidence behind every component; the number is for scanning.
SCORE_READY = 70      # >= this renders green
SCORE_DEVELOPING = 45  # >= this renders amber; below renders grey

# Fee-propensity points by posture; authoritative facts beat inference.
PROP_PROVEN = 25      # award notice / AD seed: a proven fee-payer
PROP_EXTERNAL = 16    # inferred external lean (cluster w/o recruiter, text)
PROP_NEUTRAL = 10     # nothing known either way
PROP_INTERNAL = 0     # building / running the in-house route


def propensity_points(lead: dict, item: dict | None = None) -> tuple[int, str]:
    """(points, basis) for the will-they-pay axis. Authoritative flags on
    the item (propensity store / AD seeds, via tool.propensity.annotate)
    outrank the engine's inferred posture direction."""
    item = item or {}
    if item.get("internal_ta") is True:
        return PROP_INTERNAL, "authoritative"
    if item.get("psl_status") in ("on", "yes", True):
        return PROP_PROVEN, "authoritative"
    direction = ((lead or {}).get("posture") or {}).get("direction")
    if direction == "internal":
        return PROP_INTERNAL, "inferred"
    if direction == "external":
        return PROP_EXTERNAL, "inferred"
    return PROP_NEUTRAL, "unknown"


def strength_score(lead: dict, g: dict, item: dict | None = None) -> int:
    """0-100 Lead Strength. Deterministic; never raises."""
    try:
        lead, g = lead or {}, g or {}
        ev = g.get("evidence") or {}
        # Fit (0-10 -> 0-20): is this even VMA's buyer?
        score = min(max(float(lead.get("fit") or 0), 0), 10) * 2.0
        # Signal (0-~10 soft-capped -> 0-30): trigger weight x recency x
        # confidence — multi-event stacks already accumulate here.
        score += min(max(float(lead.get("signal") or 0), 0), 10) * 3.0
        # Fee-propensity (0-25): the will-they-pay axis.
        score += propensity_points(lead, item)[0]
        # Reachable buyer (0-15): a named, resolved decision-maker beats
        # a mapped buying seat beats nothing. (Source-counting no longer
        # scores — verification is a gate concern, shown on the card.)
        if ((item or {}).get("seeded_contact_name")
                or (item or {}).get("linkedin_profile_name")
                or lead.get("relationship") == "warm"
                or (item or {}).get("contact_on_file")):
            score += 15
        elif (lead.get("who_to_call") or "").strip():
            score += 8
        # Timing (0-10): in-window beats premature beats lapsed.
        reasons = " ".join(g.get("reasons") or [])
        if lead.get("premature"):
            score += 3
        elif "lapsed" in reasons:
            score += 0
        else:
            score += 10
        # Contradictions pull hard (-8 each, max -16).
        score -= min(len(lead.get("contradictions") or []), 2) * 8
        # Hard blocks floor the score into the Blocked band.
        anti = set(lead.get("anti_triggers") or [])
        if lead.get("conflict") or anti & {"administration", "hiring_freeze"}:
            score = min(score, 15)
        return int(round(min(max(score, 0), 100)))
    except Exception:
        return 0


def tier_for(lead: dict, g: dict, score: int) -> str:
    """Board section: 'ready' (gate-presented), 'blocked' (hard-stopped),
    'dev' (worth developing), 'early' (weak signals)."""
    lead, g = lead or {}, g or {}
    anti = set(lead.get("anti_triggers") or [])
    if lead.get("conflict") or anti & {"administration", "hiring_freeze"}:
        return "blocked"
    if g.get("presented"):
        return "ready"
    return "dev" if score >= SCORE_DEVELOPING else "early"


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

        # 1. Investigation overlay outranks everything. A /red-team run's
        # conviction fields ride along onto the card.
        inv = investigation or {}
        if inv.get("red_team"):
            out["red_team"] = True
            out["conviction"] = inv.get("conviction")
            out["case"] = (inv.get("business_case") or "")[:600]
            out["opening"] = (inv.get("warm_opening") or "")[:400]
            out["buyer"] = (inv.get("economic_buyer") or "")[:200]
            out["champion"] = (inv.get("champion_path") or "")[:200]
        if inv.get("verdict") == "killed":
            reason = "Killed by red-team" if inv.get("red_team") else \
                     "Killed by investigation"
            kr = [k for k in (inv.get("kill_reasons") or []) if k][:2]
            detail = "; ".join(kr) or (inv.get("note") or "")
            out["reasons"].append(reason + (f": {detail}" if detail else ""))
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

        # 3b. Bronze triggers corroborate, never carry a lead alone. The
        # research tiering: rebrands, agency moves, framework awards, ESG
        # badges and martech adoptions are real but weak signals — they
        # present only alongside a Tier-1/2 trigger (a /red-team confirmed
        # verdict also clears them).
        if (live_keys and not confirmed
                and live_keys <= (BRONZE_KEYS | AMPLIFIER_ONLY)
                and live_keys & BRONZE_KEYS):
            out["reasons"].append("Bronze trigger alone (rebrand / framework "
                                  "/ martech) — needs a Tier-1/2 trigger or "
                                  "an investigation to corroborate")
            out["investigate"] = True
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

        # 7. The QUALIFICATION scorecard — the four dimensions an elite
        # recruitment AD evidences before spending an hour (Savage / the
        # MEDDIC-for-recruitment bar): a live-or-imminent senior seat,
        # fundability, urgency, a reachable buyer. Source-counting is
        # demoted to a per-fact VERIFICATION check: a registry-attested
        # fact (Companies House / RNS / regulator) is true on its own —
        # quiet companies with no press coverage are not less qualified.
        qual = qualification(lead, item or {}, ev, wstate)
        out["qual"] = qual
        attested = (ev.get("primary") or 0) >= 1
        verified = confirmed or attested or (ev.get("families") or 0) >= 2
        if not verified:
            out["reasons"].append(
                "Fact unverified: single non-registry source — a registry "
                "filing, a second independent source or an /investigate "
                "pass would clear it")
            out["investigate"] = True
            out["recheck_days"] = 5
            return out
        need_total = 6 if thr["throttled"] else 5
        if qual["seat"] < 1 and not confirmed:
            out["reasons"].append("Not qualified: no live or imminent senior "
                                  "seat evidenced — " + qual["seat_why"])
            out["investigate"] = True
            out["recheck_days"] = 7
            return out
        if qual["total"] < need_total and not confirmed:
            missing = [n for n, k in (("seat", "seat"), ("budget", "budget"),
                                      ("urgency", "urgency"), ("buyer", "buyer"))
                       if qual[k] == 0]
            out["reasons"].append(
                f"Not qualified yet ({qual['total']}/8"
                + (", throttled bar 6" if thr["throttled"] else "")
                + ("; weakest: " + ", ".join(missing) if missing else "")
                + ") — " + qual["weakest_why"])
            out["investigate"] = True
            out["recheck_days"] = 5
            return out

        # PRESENTED.
        out["presented"] = True
        no_contradictions = not (lead.get("contradictions") or [])
        if confirmed or (qual["total"] >= 7 and attested and no_contradictions):
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
