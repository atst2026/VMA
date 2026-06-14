"""The Advisory Qualification Gate — a consulting-adapted MEDDPICC.

Distinct from the hiring gate (`tool.gate`, SEAT/BUDGET/URGENCY/BUYER,
tuned for "is this a fillable role"). Advisory qualifies on six
dimensions, each 0-2, evidenced from collated company data:

  PAIN     a concrete, evidenced functional pain
  SPONSOR  an identifiable function leader who'd own/champion the work
  MANDATE  a plausible mandate or budget route (advisory budgets are less
           visible than headcount, so "mandate" — a regulatory deadline,
           board pressure, a new-leader remit — is the realistic proxy)
  TIMING   inside a live window (deadline, 100 days, integration phase)
  ACCESS   can a named VMA person reach the buyer
  PROOF    a defensible outside-in hypothesis + benchmark/case anchor

The dimensions are INPUTS TO A REASONED VERDICT, not an additive score
(consistent with the platform's move away from additive scoring). This
module ships the deterministic Phase-1 verdict; the Opus Conviction
Verdict pass (ADVISORY_ENGINE.md §5) replaces `_verdict` in Phase 2
without changing this interface.

The three failure-mode defences (ADVISORY_ENGINE.md §11 #1) live here:
  * a hard daily cap on PURSUE (scarcity forces ranking) — rank_and_cap;
  * source-independence as a GATE: PURSUE needs a registry-attested fact
    OR >=2 independent sources — registry-blind advisory triggers can't
    lean on the "one filing is enough" shortcut the hiring gate allows;
  * amplifier/bronze tiering: low-precision detectors never PURSUE alone.

Pure functions of their inputs; never raise.
"""
from __future__ import annotations

from datetime import date, datetime

# Board sizing — the advisory analogue of the hiring board's ~7 cap.
# Advisory leads are scarcer and higher-touch; a tighter cap forces the
# verdict to be stingy (the discipline that survives a real inbox).
ADVISORY_DAILY_CAP = 5

# PURSUE needs a strong, multi-dimensional case: > 4 of 6 dimensions
# present (the report's ">4/6") AND a meaningful total.
PURSUE_TOTAL = 8        # of 12
PURSUE_MIN_DIMS = 4     # at least four of six dimensions scoring >= 1
DEVELOP_TOTAL = 4       # below this, KILL

# Low-precision advisory detectors: they corroborate a Tier-1/2 advisory
# trigger but never originate a PURSUE alone (mirrors tool.gate's
# AMPLIFIER_ONLY / BRONZE_KEYS). Phase 2 detectors slot in here.
AMPLIFIER_ADVISORY = {"ThoughtLeadershipVelocity"}
BRONZE_ADVISORY = {"EmployeeSentimentDeterioration", "SkillsGapDisclosure"}

# Triggers carrying a statutory / regulatory mandate (MANDATE = 2).
_STATUTORY_TRIGGERS = {"PayGapActionMandate", "PublicSectorReorg",
                       "ESGCapabilityBuild"}


def _timing(window, today: date) -> tuple[int, str]:
    """TIMING from the action window. In-window=2, closing-soon still 2,
    no window=1 (unknown, not penalised), lapsed=0."""
    if not window:
        return 1, "no dated window — timing unknown"
    try:
        end = datetime.fromisoformat(window[1]).date()
    except Exception:
        return 1, "window unparseable — timing unknown"
    days_left = (end - today).days
    if days_left < 0:
        return 0, f"the action window closed {-days_left}d ago"
    return 2, f"inside the live action window ({days_left}d left)"


def qualification(signal, facts: dict, today: date) -> dict:
    """The six-dimension MEDDPICC scorecard for one advisory signal.
    `facts` supplies what the contact/dossier layers resolve
    (sponsor_name, sponsor_title, warm_route, who_to_call); absent, the
    SPONSOR/ACCESS dimensions score low and the lead stays in DEVELOP."""
    facts = facts or {}
    s = signal
    trigger = getattr(s, "trigger", "")
    extra = getattr(s, "extra", {}) or {}

    # PAIN — evidenced functional pain. A registry-attested pain (a
    # GOV.UK statutory figure) is concrete; a softer/inferred pain is 1.
    ev = source_grade(s)
    if getattr(s, "pain", "") and ev["primary"] >= 1:
        pain, pain_why = 2, "a concrete, registry-attested functional pain"
    elif getattr(s, "pain", ""):
        pain, pain_why = 1, "an evidenced but non-registry pain"
    else:
        pain, pain_why = 0, "no evidenced pain"

    # SPONSOR — a function leader / board sponsor who'd own the work.
    if facts.get("sponsor_name"):
        sponsor, sponsor_why = 2, (
            f"a named sponsor on file ({facts.get('sponsor_title') or 'owner'})")
    elif facts.get("sponsor_title") or getattr(s, "buyer_hint", ""):
        sponsor, sponsor_why = 1, "the owning seat is mapped; no named person yet"
    else:
        sponsor, sponsor_why = 0, "no identifiable owner"

    # MANDATE — a statutory/regulatory mandate is the strongest proxy.
    if trigger in _STATUTORY_TRIGGERS:
        mandate, mandate_why = 2, "a dated statutory / regulatory mandate"
    elif facts.get("mandate") or extra.get("pulse_key"):
        mandate, mandate_why = 1, "a plausible mandate (board / new-leader remit)"
    else:
        mandate, mandate_why = 0, "no evidenced mandate or budget route"

    # TIMING — the live window.
    timing, timing_why = _timing(getattr(s, "window", None), today)

    # ACCESS — can a named VMA person reach the buyer.
    if facts.get("warm_route"):
        access, access_why = 2, "a warm route to the buyer"
    elif facts.get("sponsor_name") or facts.get("who_to_call"):
        access, access_why = 1, "a named contact, but cold"
    else:
        access, access_why = 0, "no route to the buyer yet"

    # PROOF — a defensible benchmark/case anchor to teach with.
    if extra.get("size_band") or facts.get("benchmark_anchor"):
        proof, proof_why = 2, ("a defensible benchmark anchor (resourcing "
                               "ratio + Network Rail methodology)")
    elif getattr(s, "service_mix", None):
        proof, proof_why = 1, "a service hypothesis, benchmark anchor thin"
    else:
        proof, proof_why = 0, "no defensible proof to teach with"

    dims = {
        "pain": (pain, pain_why), "sponsor": (sponsor, sponsor_why),
        "mandate": (mandate, mandate_why), "timing": (timing, timing_why),
        "access": (access, access_why), "proof": (proof, proof_why),
    }
    total = sum(v for v, _ in dims.values())
    present = sum(1 for v, _ in dims.values() if v >= 1)
    weakest = min(dims.items(), key=lambda kv: kv[1][0])
    return {
        **{k: v for k, (v, _) in dims.items()},
        **{f"{k}_why": why for k, (_, why) in dims.items()},
        "total": total, "present": present,
        "weakest": weakest[0], "weakest_why": weakest[1][1],
    }


def source_grade(signal) -> dict:
    """Independent-source count + grade, via the house grader in tool.gate
    (gov.uk / Companies House / RNS / regulator = primary). Reused so
    advisory and hiring judge evidence identically."""
    try:
        from tool.gate import source_evidence
        return source_evidence(signal.as_events())
    except Exception:
        n = getattr(signal, "n_source_families", 0)
        return {"families": n, "primary": 0, "credible": 0, "level": "thin"}


def _verified(ev: dict) -> bool:
    """A fact is verified when a registry source attests it OR >=2
    independent sources corroborate it. For registry-blind advisory
    triggers (primary == 0) this becomes the >=2-sources hard gate."""
    return (ev.get("primary") or 0) >= 1 or (ev.get("families") or 0) >= 2


def assess(signal, facts: dict | None = None, *, today: date | None = None) -> dict:
    """The full gate decision for one advisory signal:
      {signal, verdict: PURSUE|DEVELOP|KILL, conviction 0-100, why,
       qual, evidence, service_mix}. Never raises."""
    try:
        today = today or date.today()
        facts = facts or {}
        q = qualification(signal, facts, today)
        ev = source_grade(signal)
        trigger = getattr(signal, "trigger", "")

        verdict, why = _verdict(signal, q, ev, trigger, facts)
        conviction = _conviction(q, ev, verdict, getattr(signal, "confidence", 0.5))
        mix = list(getattr(signal, "service_mix", []) or [])
        return {
            "signal": signal.to_dict(),
            "company": getattr(signal, "company", ""),
            "trigger": trigger,
            "verdict": verdict,
            "conviction": conviction,
            "why": why,
            "qual": q,
            "evidence": ev,
            "service_mix": mix,
            "owner": _route(mix, trigger),
        }
    except Exception as e:  # pragma: no cover - safety net
        return {"signal": getattr(signal, "to_dict", lambda: {})(),
                "verdict": "DEVELOP", "conviction": 0,
                "why": f"gate error ({type(e).__name__}) — held for safety",
                "qual": {}, "evidence": {}, "service_mix": []}


def _route(service_mix, trigger) -> dict:
    """Attach the relationship owner + delivery associate (advisory_routing).
    Never lets a routing failure sink the gate decision."""
    try:
        from tool.advisory_routing import owner_for
        return owner_for(service_mix, trigger)
    except Exception:
        return {}


def _verdict(signal, q, ev, trigger, facts) -> tuple[str, str]:
    """The deterministic KILL / DEVELOP / PURSUE call (Phase 1). Replaced
    by the Opus Conviction Verdict in Phase 2, same return shape."""
    # KILL — no pain, or the window has lapsed, or a barren case.
    if q["pain"] == 0:
        return "KILL", "No evidenced functional pain to lead with."
    if q["timing"] == 0:
        return "KILL", "The action window has closed — no live 'why now'."
    if q["total"] < DEVELOP_TOTAL:
        return "KILL", (f"Too thin to develop ({q['total']}/12) — "
                        f"weakest: {q['weakest']} ({q['weakest_why']}).")

    bronze = trigger in BRONZE_ADVISORY or trigger in AMPLIFIER_ADVISORY
    corroborated = bool(facts.get("corroborating_trigger"))

    # PURSUE — a strong, verified, multi-dimensional, reachable case.
    pursue_ready = (
        q["total"] >= PURSUE_TOTAL
        and q["present"] >= PURSUE_MIN_DIMS
        and q["pain"] >= 1
        and q["sponsor"] >= 1          # someone to own/buy the work
        and q["access"] >= 1           # and a route to reach them
        and _verified(ev)              # the source-independence gate
        and not (bronze and not corroborated)
    )
    if pursue_ready:
        return "PURSUE", (
            f"Call-ready: {q['total']}/12, a {q['pain_why']}, "
            f"{q['sponsor_why']}, {q['access_why']}, and "
            f"{'a registry-attested' if (ev.get('primary') or 0) >= 1 else 'a corroborated'}"
            " evidence base.")

    # DEVELOP — promising, but name the single missing piece.
    if not _verified(ev):
        gap = ("needs a second independent source — a registry-blind "
               "advisory signal can't pursue on one outlet")
    elif q["sponsor"] < 1:
        gap = "needs a named owner — resolve the CHRO/CEO sponsor"
    elif q["access"] < 1:
        gap = "needs a route to the buyer — find the warm intro or contact"
    elif bronze and not corroborated:
        gap = "a low-precision trigger — needs a Tier-1/2 signal to corroborate"
    else:
        gap = f"build the case to {PURSUE_TOTAL}/12 (now {q['total']}); " \
              f"weakest: {q['weakest']} ({q['weakest_why']})"
    return "DEVELOP", f"Worth developing — {gap}."


def _conviction(q, ev, verdict, detector_conf) -> int:
    """A calibrated 0-100 for board ordering (NOT a gate). Blends the
    evidenced case, source grade and detector confidence."""
    base = (q.get("total", 0) / 12.0) * 70.0
    if (ev.get("primary") or 0) >= 1:
        base += 12
    elif (ev.get("families") or 0) >= 2:
        base += 6
    base += float(detector_conf or 0) * 12
    if verdict == "KILL":
        base = min(base, 25)
    elif verdict == "DEVELOP":
        base = min(base, 69)
    return int(round(max(0, min(100, base))))


def rank_and_cap(rows: list[dict], *, cap: int | None = None) -> list[dict]:
    """Order by conviction and cap the PURSUE board (scarcity forces
    ranking). DEVELOP/KILL rows are kept in full below the cap — the gate
    grades, it never hides — but only the top `cap` PURSUE rows stay
    PURSUE; the overflow is demoted to DEVELOP with a reason, exactly as
    the hiring board's daily cap works."""
    cap = ADVISORY_DAILY_CAP if cap is None else cap
    rows = sorted(rows or [], key=lambda r: -int(r.get("conviction", 0)))
    seen = 0
    for r in rows:
        if r.get("verdict") == "PURSUE":
            seen += 1
            if seen > cap:
                r["verdict"] = "DEVELOP"
                r["why"] = (f"Below today's PURSUE cap ({cap}) — strong, but "
                            "ranked out; develop and re-surface tomorrow.")
                r["capped"] = True
    return rows
