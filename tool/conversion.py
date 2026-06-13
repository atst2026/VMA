"""The CONVERSION layer — what the AD needs to win the call, derived
deterministically from facts the engine has already verified.

The signal engine answers "who is worth watching"; this module answers
the four questions an AD asks before dialling, in VMA's own commercial
language, with ZERO model calls (the whole layer must run on the free
nightly pipeline):

  1. Should I call?      -> call_verdict()   (a relabel of the gate —
                            the gate already decided; say it plainly)
  2. What's going on?    -> phase_for()      (trigger keys -> function
                            phase: rebuild / scale / pressure /
                            misalignment, with the implication)
  3. How do I win?       -> strategy_for()   (a curated playbook keyed
                            by phase, written in VMA's service
                            vocabulary: Executive Search's pre-brief
                            benchmarking, Interim's 48-hour shortlists,
                            Advisory's Organisation Design, the Insight
                            reports + roundtables as door-openers. The
                            GOAL is always the client BD meeting — the
                            desk's conversion currency.)
  4. What's it worth?    -> deal_profile()   (house fee economics: the
                            pitch pack's 18.5% retained rate to the
                            22% perm negotiation point, on the desk's
                            £50k-£120k+ salary range)

Plus access_profile(): the route-in facts (named contact, in-house TA,
incumbent agency, rival mandate) stated as FACTS — deliberately NOT
folded into the rank. An "access penalty" would bury exactly the leads
the contact engine hasn't reached yet, and contact resolution spends
its budget in score order, so the penalty would also stop those leads
ever being resolved: a death spiral. Deal value likewise stays OUT of
the rank (it's a chip and a sort option); rank changes belong to the
/learn outcome process, not a formula.
"""
from __future__ import annotations

import logging

log = logging.getLogger("brief.conversion")

# ---- House fee economics (sources: the pitch pack's fee table quotes
# 18.5% retained on base salary; the desk business plan holds a 20%
# average fee rate; 22% is the live perm negotiation point) -----------
FEE_RATE_RETAINED = 0.185
FEE_RATE_TYPICAL = 0.20
FEE_RATE_TOP = 0.22

# ---- Phase taxonomy: every trigger key the detectors emit, mapped to
# the function's situation. Keys deliberately exhaustive — an unmapped
# key falls back to the fee-class framing rather than guessing. -------
PHASES = {
    "rebuild": {
        "ceo_change", "cfo_change", "chro_change", "chair_change",
        "cmo_change", "comms_leader_departure", "mishire_reversal",
        "cascade", "restructure", "ir_director_change",
        "ownership_change", "mna", "pe_acquisition",
    },
    "scale": {
        "funding", "secured_financing", "ipo_listing", "market_entry",
        "hiring_restart", "job_ad_cluster", "budget_flush",
        "hiring_gap", "seniority_gap", "follow_on",
    },
    "pressure": {
        "crisis_event", "regulator_action", "regulator_probe_early",
        "profit_warning", "redundancy", "contract_loss", "water_sar",
        "press_velocity_spike",
    },
    "misalignment": {
        "inhouse_search_failing", "interim_watch", "ic_platform_rfp",
        "stale_mandate", "contract_end", "framework_award",
        "framework_displacement",
    },
}

_PHASE_BLURB = {
    "rebuild": ("Leadership has changed (or failed) above or inside the "
                "function — the new owner will reshape the team in their "
                "first months, and briefs are written in that window."),
    "scale": ("Money or mandate is expanding the function — roles get "
              "created here, and speed matters to whoever funded it."),
    "pressure": ("External pressure is exposing the function — the work "
                 "is forced, often confidential, and cannot wait for a "
                 "hiring cycle."),
    "misalignment": ("What they have isn't matching what they need — a "
                     "stalled search, interim cover, or a route to "
                     "market going stale. The cost of the gap is "
                     "already being paid."),
}


def phase_for(trigger_keys) -> tuple[str | None, str]:
    """(phase, blurb) for a stack of trigger keys; strongest wins on
    ties in PHASES order. (None, '') when nothing maps."""
    keys = {k for k in (trigger_keys or []) if k}
    for phase, members in PHASES.items():
        if keys & members:
            return phase, _PHASE_BLURB[phase]
    return None, ""


# ---- 1. CALL: the gate's decision, said plainly ----------------------
def call_verdict(tier: str | None, gate_why: str = "",
                 conflict: bool = False) -> dict:
    """The gate already decided; the card should say it in one word.
    {call: YES|WAIT|NO, why, cls(css)}."""
    tier = (tier or "ready").lower()
    if tier == "blocked":
        return {"call": "NO", "cls": "no",
                "why": gate_why or "Hard blocker on file."}
    if conflict:
        return {"call": "WAIT", "cls": "wait",
                "why": "Rival mandate live — timed watch; the interim-"
                       "cover pitch is the only safe approach now."}
    if tier == "ready":
        return {"call": "YES", "cls": "yes",
                "why": "Cleared the evidence gate — corroborated, "
                       "in-window, no blockers."}
    return {"call": "WAIT", "cls": "wait",
            "why": gate_why or "Needs more corroboration before the "
                               "call spends its one first impression."}


# ---- 4. COMMERCIAL: deal shape at house rates ------------------------
# Salary anchors sit on the desk's range (£50k-£120k+ perm; the pitch
# pack's own worked example is a £130k salary).
_DEAL_RULES = (
    # (deal type, salary band lo-hi OR explicit fee band, trigger keys)
    ("Leadership search", (110_000, 160_000), {
        "ceo_change", "cfo_change", "chro_change", "chair_change",
        "cmo_change", "comms_leader_departure", "mishire_reversal",
        "crisis_event", "regulator_action", "profit_warning", "mna",
        "pe_acquisition", "ownership_change", "ipo_listing",
        "restructure", "cascade", "ir_director_change"}),
    ("Team build", None, {            # 3-5 roles, mixed seniority
        "job_ad_cluster", "market_entry", "hiring_restart",
        "funding", "secured_financing", "budget_flush", "follow_on"}),
    ("Senior hire", (75_000, 110_000), {
        "inhouse_search_failing", "hiring_gap", "seniority_gap",
        "stale_mandate", "ic_platform_rfp", "contract_end",
        "framework_award", "framework_displacement", "water_sar",
        "press_velocity_spike", "redundancy", "contract_loss",
        "regulator_probe_early"}),
    ("Interim-first", None, {"interim_watch"}),
)
_TEAM_BUILD_BAND = (55_000 * 3, 85_000 * 5)   # 3-5 roles


def _fee_range(lo_salary: int, hi_salary: int) -> tuple[int, int]:
    return (round(lo_salary * FEE_RATE_RETAINED / 500) * 500,
            round(hi_salary * FEE_RATE_TOP / 500) * 500)


def _fmt(lo: int, hi: int) -> str:
    return f"£{lo // 1000}k–£{hi // 1000}k"


def deal_profile(trigger_keys, presented: bool = False,
                 q_total=None, score=None) -> dict:
    """{type, value, low, high, conf, basis} — deterministic, at house
    rates (18.5% retained floor to the 22% negotiation point). The
    retained framing matters commercially: retained captures 2-3x the
    value of contingent on the same seat."""
    keys = {k for k in (trigger_keys or []) if k}
    dtype, band = "Single senior role", (60_000, 90_000)
    for name, salary_band, members in _DEAL_RULES:
        if keys & members:
            dtype = name
            band = salary_band
            break
    if dtype == "Team build":
        lo, hi = _fee_range(*_TEAM_BUILD_BAND)
        basis = (f"3–5 roles at £55k–£85k base × {FEE_RATE_RETAINED:.1%}"
                 f"–{FEE_RATE_TOP:.0%} house rates")
    elif dtype == "Interim-first":
        lo, hi = 8_000, 25_000
        basis = ("Interim margin on £350–£800/day cover, plus the "
                 "perm conversion the cover usually becomes")
    else:
        lo, hi = _fee_range(*band)
        basis = (f"£{band[0] // 1000}k–£{band[1] // 1000}k base × "
                 f"{FEE_RATE_RETAINED:.1%}–{FEE_RATE_TOP:.0%} house rates")
    try:
        qt = int(q_total) if q_total is not None else None
    except Exception:
        qt = None
    if presented and ((qt or 0) >= 5 or (score or 0) >= 70):
        conf = "High"
    elif presented or (score or 0) >= 45:
        conf = "Medium"
    else:
        conf = "Low"
    return {"type": dtype, "value": _fmt(lo, hi), "low": lo, "high": hi,
            "conf": conf, "basis": basis}


# ---- 3. STRATEGY: the curated playbook (VMA service vocabulary) ------
# Each play answers: lead with WHICH service, framed HOW, avoiding the
# known failure mode, through WHICH door, to get THE MEETING (the
# desk's weekly conversion currency), with a give-away OFFER from the
# Insight & Events shelf so the call trades value instead of asking
# for it.
_PLAYS = {
    "rebuild": {
        "lead": "Executive Search, pre-brief: market benchmarking, role "
                "definition and internal-candidate assessment — the work "
                "VMA does before a role is even defined.",
        "position": "A new leader reshapes their function in the first "
                    "90 days; offer the market map BEFORE the brief "
                    "exists, so VMA is in the room when it's written.",
        "avoid": "Pitching candidates or 'have you got any roles?' — "
                 "the brief doesn't exist yet; selling CVs now reads "
                 "as noise and burns the account.",
        "goal": "A meeting inside the new leader's first 90 days.",
        "offer": "Salary benchmarking for the function + the relevant "
                 "Insight report; a roundtable seat for the new leader.",
    },
    "scale": {
        "lead": "Retained build-out: one exclusive multi-role mandate "
                "rather than role-by-role contingency.",
        "position": "Speed protects the investment case — a planned "
                    "build with benchmarked salaries beats sequential "
                    "hiring, and retained is how it stays on schedule.",
        "avoid": "Joining a contingency race on single roles — it "
                 "concedes the build-out and caps the fee at a third "
                 "of its retained value.",
        "goal": "A meeting to scope the whole build, not one vacancy.",
        "offer": "A build-out salary benchmark across the planned "
                 "seats; intros at the next practice roundtable.",
    },
    "pressure": {
        "lead": "Interim & Contract first — the 48-hour shortlist — "
                "with confidential search behind it.",
        "position": "Cover the gap this week while the permanent "
                    "answer is found quietly; forced work can't wait "
                    "for a hiring cycle and can't be advertised.",
        "avoid": "Referencing the crisis on the call or anything that "
                 "feels public — discretion IS the pitch.",
        "goal": "A confidential conversation this week — interim brief "
                "in days, search mandate behind it.",
        "offer": "Two or three interim profiles sight-unseen within "
                 "48 hours, no commitment.",
    },
    "misalignment": {
        "lead": "Retained takeover of the stalled route — or interim "
                "cover where the seat is already being bridged.",
        "position": "The cost of the DIY route is already paid; a "
                    "retained search with a defined shortlist date "
                    "ends the drift.",
        "avoid": "Re-running their own approach (same JD, same boards) "
                 "— diagnose why it stalled before proposing anything.",
        "goal": "A meeting to re-scope the brief, not to take the old "
                "one as-is.",
        "offer": "A candid read on why the search stalled (salary vs "
                 "market, title, spec) backed by benchmark data.",
    },
    None: {
        "lead": "Advisory-led opener: benchmarking or Organisation "
                "Design conversation, with search held back.",
        "position": "Trade insight for the meeting — the function-"
                    "level view they can't get internally.",
        "avoid": "A generic 'agency intro' call with no specific "
                 "observation attached.",
        "goal": "A first client meeting on the strength of the insight.",
        "offer": "The relevant Insight report + a benchmarking teaser.",
    },
}


def strategy_for(phase: str | None, access: dict | None = None) -> dict:
    """The play for this phase, with the ENTRY line resolved from the
    live access facts (named contact beats title-guessing)."""
    play = dict(_PLAYS.get(phase) or _PLAYS[None])
    a = access or {}
    if a.get("poc_name"):
        play["entry"] = (f"{a['poc_name']} ({a.get('poc_title') or 'function lead'}) "
                         "— named, current, linked on the card.")
    elif phase == "rebuild":
        play["entry"] = ("The CHRO or the incoming leader — comms/"
                         "marketing reports get rebuilt from above.")
    elif phase == "pressure":
        play["entry"] = ("Most senior comms owner on file; if none, "
                         "the CEO's office routes confidential cover.")
    else:
        play["entry"] = ("The function owner once the resolver names "
                         "them (it re-attempts nightly); don't cold-"
                         "call the switchboard on a senior brief.")
    return play


# ---- ACCESS: route-in facts (never a penalty) ------------------------
def access_profile(company: str, poc: list | None,
                   internal_ta: bool = False, psl_status: str = "",
                   agency_scope: str = "", conflict: bool = False) -> dict:
    """Facts an AD wants stated before dialling. Returns
    {label, cls, facts[], poc_name, poc_title}."""
    facts = []
    poc = poc or []
    named = next((p for p in poc if p.get("name")), None)
    if named:
        facts.append(f"Named contact: {named['name']}"
                     + (f" — {named['title']}" if named.get("title") else ""))
    else:
        facts.append("No named function contact yet — the nightly "
                     "resolver keeps attempting it from free sources.")
    incumbent = None
    try:
        from tool.agency_relationships import last_relationship
        rel = last_relationship(company)
        if rel and rel.get("agency"):
            when = (rel.get("date") or "")[:7]
            incumbent = rel["agency"]
            facts.append(f"Last known agency: {incumbent}"
                         + (f" ({rel.get('discipline')})" if rel.get("discipline") else "")
                         + (f", {when}" if when else ""))
    except Exception:
        pass
    if internal_ta:
        facts.append("In-house talent team active (ATS posting seen) — "
                     "expect a 'we recruit ourselves' objection; the "
                     "counter is specialism + the senior-search gap.")
    if psl_status == "on" or agency_scope:
        facts.append("Proven agency user — fee conversations land here.")
    if conflict:
        facts.append("RIVAL MANDATE LIVE — do not pitch the same seat; "
                     "interim cover or adjacent brief only.")
    if conflict:
        label, cls = "CONTESTED", "acc-bad"
    elif named:
        label, cls = "DOOR OPEN", "acc-good"
    elif internal_ta:
        label, cls = "GUARDED", "acc-mid"
    else:
        label, cls = "UNMAPPED", "acc-mid"
    return {"label": label, "cls": cls, "facts": facts,
            "poc_name": (named or {}).get("name", ""),
            "poc_title": (named or {}).get("title", "")}


# ---- The one call sites use ------------------------------------------
def enrich_row(row: dict) -> dict:
    """Project the conversion layer onto a built console row. Pure
    function of fields already on the row; never raises."""
    try:
        tkeys = row.get("tkeys") or []
        q = row.get("q") or {}
        phase, phase_why = phase_for(tkeys)
        access = access_profile(
            company=row.get("co") or "",
            poc=row.get("poc"),
            internal_ta=bool(row.get("internal_ta")),
            psl_status=row.get("psl_status") or "",
            agency_scope=row.get("agency_scope") or "",
            conflict=bool(row.get("conflict")),
        )
        deal = deal_profile(
            tkeys,
            presented=bool(row.get("presented")),
            q_total=q.get("total") if isinstance(q, dict) else None,
            score=row.get("score"),
        )
        return {
            "callv": call_verdict(row.get("tier"), row.get("gateWhy") or "",
                                  bool(row.get("conflict"))),
            "phase": phase or "",
            "phaseWhy": phase_why,
            "deal": deal,
            "dealMax": deal["high"],
            "strategy": strategy_for(phase, access),
            "access": access,
        }
    except Exception as e:
        log.info("conversion enrich skipped (%s)", e)
        return {}
