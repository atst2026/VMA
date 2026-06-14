"""The Outside-In Function Diagnostic — VMA's proprietary instrument.

This is the analogue of Korn Ferry's Hay assessment / Heidrick's culture
profile: a repeatable, defensible, outside-in HYPOTHESIS about the shape
and likely weaknesses of a target's comms/marketing function, anchored to
the resourcing benchmark and a peer cohort the buyer cannot see for
themselves. It is the origination engine and — per ADVISORY_ENGINE.md §11
#2 — the actual product: output quality is bounded by the depth of the
peer-comparison corpus, so the corpus (peers + benchmark) is the moat.

Two responsibilities, both deterministic and £0 here:

  * assemble_context(signal) — gather EVERYTHING the engine already knows
    about the function (gap, headcount band, expected FTE, sector peers,
    service mix) into the grounded brief the Opus pass reasons over. The
    Opus Outside-In Diagnostic (Phase 2) consumes this; in Claude Code the
    model runs free under subscription, so no API spend is introduced.
  * diagnose(signal) — the deterministic v0 structured diagnostic the
    Evidence Pack renders today, with the Opus prose a swap-in.

Two §11-#2 guardrails are wired here:
  * VARIABLE STRUCTURE — lead with the single sharpest ANOMALY for THIS
    company (pay-gap exposure / governance / under-resourcing), not a fixed
    three-move script a senior buyer spots by sentence three.
  * NOVELTY GATE — is_novel() asserts the insight rests on the non-public
    peer/benchmark comparison, not on the company's own published figures.
"""
from __future__ import annotations

from tool import gender_pay_gap as gpg


def assemble_context(signal, facts: dict | None = None) -> dict:
    """The grounded brief for the diagnostic — the non-public comparison
    set (peers + resourcing benchmark) plus the company's own figures.
    Pure; never raises; degrades gracefully when a source is unavailable."""
    facts = facts or {}
    company = getattr(signal, "company", "") or ""
    extra = getattr(signal, "extra", {}) or {}
    band = extra.get("size_band") or ""

    peers, sector = [], None
    try:
        from tool.peers import peers_for
        peers, sector = peers_for(company, k=8)
    except Exception:
        pass

    bench = None
    try:
        bench = gpg.resourcing_benchmark({"size": band}) if band else None
    except Exception:
        bench = None
    expected = None
    try:
        expected = gpg.expected_comms_fte(band) if band else None
    except Exception:
        expected = None

    return {
        "company": company,
        "sector": sector,
        "peers": peers,                     # the non-public comparison set
        "size_band": band,
        "expected_comms_fte": expected,     # (lo, hi, mid, label) or None
        "resourcing_benchmark": bench,
        "median_gap": extra.get("median"),
        "late": bool(extra.get("late")),
        "widened_pp": extra.get("widened_pp"),
        "service_mix": list(getattr(signal, "service_mix", []) or []),
        "dossier_facts": facts.get("dossier_facts") or {},
    }


def _sharpest_anomaly(ctx: dict) -> tuple[str, str]:
    """The single dimension to lead with (variable structure, §11 #2):
    (anomaly_key, the one-line reframe that opens on it)."""
    med = ctx.get("median_gap")
    company = ctx.get("company") or "the organisation"
    if med is not None and med >= gpg._VERY_WIDE:
        return ("pay_gap_exposure",
                f"A {med:.0f}% median gap is a board-level reputation and "
                "retention exposure now that statutory action plans demand "
                "named, evidenced actions — the question is whether the "
                "function has the capability to build and land one.")
    if ctx.get("late"):
        return ("governance_process",
                "Filing after the statutory deadline points to a process and "
                "ownership gap in how the function turns an obligation into "
                "a board-ready, on-time response — exactly what an operating-"
                "model review fixes.")
    if ctx.get("expected_comms_fte"):
        _lo, _hi, _mid, rng = ctx["expected_comms_fte"]
        return ("under_resourcing",
                f"Functions at {company}'s scale typically run ~{rng} comms "
                "professionals; most leaders can't say where they sit, and an "
                "under-resourced function is exactly what shows up as a "
                "widening gap with no action plan behind it.")
    return ("action_plan_maturity",
            "Statutory equality action plans now demand named, evidenced "
            "actions — the functions that struggle are those without the "
            "comms and org-design capability to build and land them.")


def diagnose(signal, facts: dict | None = None) -> dict:
    """The deterministic v0 Outside-In Function Diagnostic — a clearly-
    labelled HYPOTHESIS to test with the buyer, anchored to the benchmark
    and the peer cohort. The Opus pass replaces the prose in Phase 2;
    this shape (and is_novel) is stable across that swap."""
    ctx = assemble_context(signal, facts)
    company = ctx["company"] or "the organisation"
    anomaly_key, lead_line = _sharpest_anomaly(ctx)

    shape = []
    if ctx.get("expected_comms_fte"):
        shape.append(ctx["resourcing_benchmark"]["line"]
                     if ctx.get("resourcing_benchmark") else "")
    # Only present a peer cohort when the sector is CONFIDENTLY detected —
    # peers_for returns a fallback cohort for unknown names, and a fuzzy or
    # irrelevant comparison is worse than none in front of a senior buyer.
    peers = ctx.get("peers") or []
    peer_line = ""
    if peers and ctx.get("sector"):
        shown = ", ".join(peers[:5])
        peer_line = (f"Against the closest comparable {ctx['sector']} "
                     f"functions ({shown}{'…' if len(peers) > 5 else ''}), "
                     "the questions are structure, seniority mix and whether "
                     "ED&I sits inside comms or is owned elsewhere.")

    likely_gaps = ["Capacity and process to turn a statutory obligation into "
                   "a credible, board-ready equality action plan."]
    if anomaly_key == "under_resourcing":
        likely_gaps.append("Headcount and seniority below the peer band for "
                           "the organisation's size.")
    if anomaly_key == "governance_process":
        likely_gaps.append("Clear ownership and a repeatable process for "
                           "statutory reporting deadlines.")

    confirm_refute = ("Confirms if: no published action plan, comms headcount "
                      "below the peer band, or ED&I owned outside comms. "
                      "Refutes if: a current, evidenced plan and a right-sized "
                      "team are already in place — in which case the "
                      "conversation is benchmarking, not rebuild.")

    out = {
        "company": company,
        "label": "Outside-in hypothesis (to test together)",
        "lead_anomaly": anomaly_key,
        "lead_line": lead_line,
        "function_shape": "  ".join(s for s in shape if s),
        "peer_frame": peer_line,
        "likely_gaps": likely_gaps,
        "benchmark_anchor": (ctx.get("resourcing_benchmark") or {}).get("line", ""),
        "confirm_refute": confirm_refute,
        "proof_method": ("The Consultation → Benchmarking → Design "
                         "methodology VMA ran for Network Rail."),
        "context": ctx,
    }
    out["novel"] = is_novel(out)
    return out


def is_novel(diagnostic: dict) -> bool:
    """The novelty gate (§11 #2): the insight must rest on the NON-PUBLIC
    comparison — a peer cohort or the resourcing benchmark — not solely on
    the company's own published figures (which the buyer already owns). A
    diagnostic that fails this is generic however precise its numbers."""
    d = diagnostic or {}
    ctx = d.get("context") or {}
    # A confidently detected peer cohort (not the fallback) or the
    # resourcing benchmark — both are comparisons the buyer can't self-serve.
    has_cohort = bool(ctx.get("sector"))
    has_benchmark = bool(d.get("benchmark_anchor") or ctx.get("expected_comms_fte"))
    return has_cohort or has_benchmark


def render(diagnostic: dict) -> str:
    """The 1-page diagnostic as prose for the Evidence Pack / a brief."""
    d = diagnostic or {}
    parts = [f"{d.get('label', 'Hypothesis')} on {d.get('company', '')}'s "
             "comms/ED&I capability:", d.get("lead_line", "")]
    if d.get("function_shape"):
        parts.append(d["function_shape"])
    if d.get("peer_frame"):
        parts.append(d["peer_frame"])
    gaps = d.get("likely_gaps") or []
    if gaps:
        parts.append("Likely gaps: " + " ".join(gaps))
    if d.get("confirm_refute"):
        parts.append(d["confirm_refute"])
    if d.get("proof_method"):
        parts.append(d["proof_method"])
    return "  ".join(p for p in parts if p)
