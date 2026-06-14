"""The Advisory Evidence Pack (v0) — the meeting-winning deliverable.

The advisory analogue of the Pitch Pack: it sells a CONSULTATIVE MEETING,
not a retained search. v0 assembles the seven parts deterministically from
facts the engine already holds (no model calls, no fee figures — facts
only, per the locked Phase-1 decisions in ADVISORY_ENGINE.md §14). The
Opus Evidence Pack Composer (§6) replaces the prose in Phase 2 and adds
the novelty gate; this interface stays the same.

Operationalises Challenger (Teach → Tailor → Take-Control): the seven
parts are the Reframe, the Outside-In Diagnostic hypothesis, the
Benchmarking Teaser, the Named Buyer + Inferred Pain, the Value Give-Away,
the Recommended Service + Proof Anchor, and the Take-Control Ask.

Credibility guardrail (ADVISORY_ENGINE.md §9): every outside-in claim is a
benchmark-anchored HYPOTHESIS ("functions of your size typically…"), never
a cold factual assertion about a named employer's failings, and only
published GOV.UK figures are quoted.
"""
from __future__ import annotations

# The proof anchor for org-design / benchmarking work.
_NETWORK_RAIL = ("Network Rail — VMA's Advisory team reviewed and "
                 "benchmarked the comms function to inform a restructure "
                 "(2020), then refreshed the benchmark in 2024.")

_SERVICE_LABELS = {
    "edi": "ED&I Consulting (RiverRoad / neuroinclusion)",
    "benchmarking": "Benchmarking (structure / headcount / remuneration)",
    "org_design": "Strategy & Organisation Design",
    "coaching": "Professional Development & Coaching",
}


def compose(signal, facts: dict | None = None) -> dict:
    """Build the seven-part Evidence Pack for an advisory signal.

    Returns a dict of the seven parts plus metadata. Pure function of the
    signal + resolved facts; never raises, never invents a fee figure.
    """
    facts = facts or {}
    company = getattr(signal, "company", "") or "the organisation"
    extra = getattr(signal, "extra", {}) or {}
    mix = list(getattr(signal, "service_mix", []) or [])
    lead_service = mix[0] if mix else "benchmarking"

    benchmark = _benchmark_teaser(extra)
    buyer = (facts.get("sponsor_name")
             or getattr(signal, "buyer_hint", "") or "the function owner")

    pack = {
        "company": company,
        "trigger": getattr(signal, "trigger", ""),
        "reframe": _reframe(company, benchmark, extra),
        "diagnostic": _diagnostic(company, benchmark, signal),
        "benchmark_teaser": (benchmark or {}).get(
            "line", "A peer benchmark of structure, headcount and "
                    "remuneration vs comparable organisations."),
        "named_buyer": {
            "buyer": buyer,
            "inferred_pain": getattr(signal, "pain", ""),
            "tailoring": ("The CEO hears board-level ED&I risk and reputation; "
                          "the CHRO hears capability and a defensible action "
                          "plan."),
        },
        "value_give_away": _give_away(extra),
        "recommended_service": {
            "service": _SERVICE_LABELS.get(lead_service, lead_service),
            "proof_anchor": _NETWORK_RAIL,
            "full_mix": [_SERVICE_LABELS.get(m, m) for m in mix],
        },
        "take_control_ask": (
            f"“Can I take 30 minutes to show you the full benchmark for "
            f"{company} against your closest peers, and the two actions the "
            f"better-performing ones took?”"),
        "why_now": getattr(signal, "why_now", ""),
        # Phase 1 is facts-only: no fee figure until Lucy signs off bands.
        "deal_value": None,
    }
    return pack


def _benchmark_teaser(extra: dict) -> dict | None:
    """A partial, credible resourcing benchmark from the employer's
    headcount band (the data asset that creates the 'show me the full
    picture' pull)."""
    band = (extra or {}).get("size_band") or ""
    try:
        from tool.gender_pay_gap import resourcing_benchmark
        return resourcing_benchmark({"size": band}) if band else None
    except Exception:
        return None


def _reframe(company: str, benchmark: dict | None, extra: dict) -> str:
    """The single sharp Commercial Insight — a benchmark-anchored
    HYPOTHESIS, never an insulting assertion."""
    if benchmark:
        return (f"Functions at {company}'s scale typically carry a comms "
                "headcount in a predictable band; most leaders can't say "
                "where they actually sit, and an under-resourced function is "
                "exactly what shows up as a widening pay gap with no "
                "evidenced action plan behind it.")
    return ("Statutory equality action plans now demand named, evidenced "
            "actions — the functions that struggle are the ones without the "
            "comms and org-design capability to build and land them.")


def _diagnostic(company: str, benchmark: dict | None, signal) -> str:
    """The 1-page reasoned hypothesis (v0 stub of the Opus Outside-In
    Function Diagnostic). Clearly labelled as a hypothesis."""
    parts = [f"Hypothesis (outside-in, to test together) on {company}'s "
             "comms/ED&I capability:"]
    if benchmark:
        parts.append(benchmark.get("line", ""))
    parts.append(getattr(signal, "pain", ""))
    parts.append("Likely gap: capacity and process to turn a statutory "
                 "obligation into a credible, board-ready action plan — the "
                 "Consultation → Benchmarking → Design methodology VMA "
                 "ran for Network Rail.")
    return "  ".join(p for p in parts if p)


def _give_away(extra: dict) -> str:
    """The genuine, free, useful artefact that justifies the meeting."""
    return ("A one-page peer comparison: how this median gap and action-plan "
            "maturity sit against the closest sector peers, with the two "
            "highest-impact actions the better performers took — using only "
            "published GOV.UK figures.")


def render_markdown(pack: dict) -> str:
    """Human-readable Evidence Pack — what an AD reads before the call."""
    p = pack or {}
    nb = p.get("named_buyer", {})
    rs = p.get("recommended_service", {})
    lines = [
        f"# Evidence Pack — {p.get('company', '')}",
        f"*Trigger: {p.get('trigger', '')} · {p.get('why_now', '')}*",
        "",
        "## 1. The Reframe", p.get("reframe", ""), "",
        "## 2. Outside-In Diagnostic (hypothesis)", p.get("diagnostic", ""), "",
        "## 3. Benchmarking Teaser", p.get("benchmark_teaser", ""), "",
        "## 4. Named Buyer + Inferred Pain",
        f"**Buyer:** {nb.get('buyer', '')}",
        f"**Pain:** {nb.get('inferred_pain', '')}",
        f"**Tailor:** {nb.get('tailoring', '')}", "",
        "## 5. Value Give-Away", p.get("value_give_away", ""), "",
        "## 6. Recommended Service + Proof Anchor",
        f"**Lead with:** {rs.get('service', '')}",
        f"**Proof:** {rs.get('proof_anchor', '')}", "",
        "## 7. Take-Control Ask", p.get("take_control_ask", ""),
    ]
    return "\n".join(lines)
