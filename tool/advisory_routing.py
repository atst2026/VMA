"""Associate routing — the verdict's recommended service → the right owner.

The Conviction Verdict (and the Phase-1 deterministic verdict) names a
recommended service; this module names WHO at VMA owns the relationship and
WHICH associate delivers it, from the Advisory Services brochure. It closes
the "who do I hand this to?" gap so an advisory lead is actionable, not just
qualified — the routing the unified consultancy console needs
(ADVISORY_ENGINE.md §7).

Routing logic (from the plan):
  * Lucy Cairncross (MD, Advisory) owns the advisory relationship and the
    diagnostic — org design, benchmarking, coaching, ED&I.
  * Sara Tehrani (Account Director) owns the BD motion and any lead with a
    search / interim component, and the referral lanes (she keeps the
    adviser seat).
  * The associate is ATTACHED for delivery by service: coaching → Joss
    Mathieson (Change Oasis) / Famn (Molly & Roger Taylor); ED&I →
    Antoinette Willcocks (RiverRoad) / Kate Isichei (neuroinclusion).
  * Out-of-scope (non comms/marketing) work goes to a referral lane —
    never push VMA into general management consulting.

Pure, deterministic, never raises.
"""
from __future__ import annotations

# VMA relationship owners (brochure contacts).
_OWNERS = {
    "advisory": {"name": "Lucy Cairncross", "role": "MD, Advisory Services",
                 "email": "lcairncross@vmagroup.com"},
    "search": {"name": "Sara Tehrani", "role": "Account Director",
               "email": "stehrani@vmagroup.com"},
}

# Delivery associates, by the service they deliver (brochure bench).
_ASSOCIATES = {
    "coaching": [
        {"name": "Joss Mathieson", "firm": "Change Oasis",
         "for": "culture & leadership change coaching"},
        {"name": "Molly & Roger Taylor", "firm": "Famn",
         "for": "evidence-based psychological & strategic coaching"},
    ],
    "edi": [
        {"name": "Antoinette Willcocks", "firm": "RiverRoad",
         "for": "ED&I strategy, training & inclusive communications"},
        {"name": "Kate Isichei", "firm": "Where To Look Communications",
         "for": "neuroinclusion"},
    ],
}

# Referral partners, when the signal says work WITHOUT headcount budget.
_REFERRAL = {
    "agency_referral": {"name": "partner delivery agency (e.g. Sequel Group)",
                        "for": "delivery without a headcount line"},
    "engagement_platform": {"name": "employee-engagement platform "
                            "(e.g. Workvivo by Zoom, Staffbase)",
                            "for": "channels, not headcount, are the gap"},
}


def _family(service_key: str) -> str:
    """The service family (hire / advisory / referral) without importing at
    module load (keeps this leaf module cheap and circular-safe)."""
    try:
        from tool.advisory import SERVICES
        return SERVICES.get(service_key, {}).get("family", "advisory")
    except Exception:
        if service_key in ("search", "interim"):
            return "hire"
        if service_key in _REFERRAL:
            return "referral"
        return "advisory"


def owner_for(service_mix, trigger: str | None = None) -> dict:
    """Route a lead to its owner + delivery bench.

    Returns {owner, owner_role, owner_email, desk, associate, bench,
             co_owner, referral, why} — JSON-serialisable. `service_mix` is
    the ranked service keys the gate produced; the FIRST is the lead
    service. Never raises; an empty/unknown mix routes to Lucy (advisory).
    """
    mix = [str(s).strip().lower() for s in (service_mix or []) if s]
    lead = mix[0] if mix else "benchmarking"
    fam = _family(lead)

    has_search = any(_family(m) == "hire" for m in mix)
    has_advisory = any(_family(m) == "advisory" for m in mix)

    if fam == "advisory":
        owner, desk = _OWNERS["advisory"], "advisory"
    elif fam == "referral":
        owner, desk = _OWNERS["search"], "referral"
    else:  # hire
        owner, desk = _OWNERS["search"], "search"

    # The associate that DELIVERS the lead service (coaching / ED&I).
    bench: list[dict] = []
    for m in mix:
        for a in _ASSOCIATES.get(m, []):
            if a not in bench:
                bench.append(a)
    associate = (_ASSOCIATES.get(lead) or [None])[0]

    # Co-owner: an advisory-led lead with a search component flags Sara
    # (the retained search follows the review); a search/referral lead with
    # an advisory component flags Lucy (the diagnostic relationship).
    co_owner = None
    if desk == "advisory" and has_search:
        co_owner = _OWNERS["search"]
    elif desk in ("search", "referral") and has_advisory:
        co_owner = _OWNERS["advisory"]

    referral = _REFERRAL.get(lead)

    if desk == "advisory":
        why = (f"{owner['name']} owns the advisory relationship and the "
               f"diagnostic")
        if associate:
            why += f"; {associate['name']} ({associate['firm']}) delivers"
        if co_owner:
            why += f"; {co_owner['name']} picks up the retained search"
    elif desk == "referral":
        why = (f"{owner['name']} keeps the adviser seat and refers the "
               f"{referral['name'] if referral else 'partner'}")
    else:
        why = f"{owner['name']} owns the search / BD motion"
        if co_owner:
            why += f"; {co_owner['name']} owns the advisory wrap"

    return {
        "owner": owner["name"], "owner_role": owner["role"],
        "owner_email": owner["email"], "desk": desk,
        "associate": associate, "bench": bench,
        "co_owner": co_owner["name"] if co_owner else None,
        "referral": referral, "why": why,
    }


def owner_line(service_mix, trigger: str | None = None) -> str:
    """Compact one-line routing for dense surfaces: 'Owner: Lucy Cairncross
    · ED&I via Antoinette Willcocks (RiverRoad)'."""
    try:
        r = owner_for(service_mix, trigger)
        line = f"Owner: {r['owner']}"
        if r.get("associate"):
            a = r["associate"]
            line += f" · delivery {a['name']} ({a['firm']})"
        if r.get("co_owner"):
            line += f" · + {r['co_owner']}"
        return line
    except Exception:
        return ""
