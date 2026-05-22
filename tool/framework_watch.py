"""Framework Watch — public-sector executive-search framework tracker.

Routed into Pre-Market Signals as a "framework refresh window" signal
(the same way funding rounds are folded in): it surfaces the named
exec-search frameworks Sara could compete on and when each is up for
re-procurement (the window to get appointed) — fully automatic, no manual
upkeep.

Deterministic + public. A curated registry of framework metadata
(buyer, scope, dates, portal) drives a refresh-window engine off today's
date — no restricted integrations, no external calls. Mirrors
calendar_pulses' hand-curated model.

HONESTY NOTE: expiry_date is an ESTIMATE unless date_confidence ==
"verified" (the UI labels it "(est.)"). A per-framework supplier/holder
list was deliberately NOT included — it would require ongoing manual data
entry to stay useful, which won't happen; the portal link is the pointer
for anyone who wants to check current suppliers.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

log = logging.getLogger("brief.frameworks")

# How long before expiry the re-procurement window typically opens — the
# point at which it's worth Sara positioning to get appointed next round.
REFRESH_LEAD_MONTHS = 9


# Each framework:
#   key            stable id
#   title          AD-facing headline: recognisable owner + framework name
#                  (NOT the procurement code — that goes in `code`)
#   code           framework reference (e.g. RM6394); "" if none / not a code
#   buyer          contracting authority / alliance
#   scope          one-line scope (comms relevance)
#   comms_relevant whether senior-comms exec search sits in scope
#   expiry_date    ISO date the current agreement ends (estimate unless
#                  date_confidence == "verified")
#   date_confidence "verified" | "estimate"
#   portal         authoritative source to verify suppliers + dates
#   notes          GP-impact / context
FRAMEWORKS: list[dict] = [
    {
        "key": "ccs_rm6394_exec_search_3",
        "title": "Crown Commercial Service — Executive Search 3",
        "ad_title": "Central government — comms search framework",
        "ad_desc": "The route to bid for senior comms roles across government departments & arm's-length bodies.",
        "code": "RM6394",
        "buyer": "Crown Commercial Service",
        "scope": "Executive search & permanent recruitment for senior public-sector roles (incl. comms/corporate-affairs leadership).",
        "comms_relevant": True,
        "expiry_date": "2028-05-31",
        "date_confidence": "estimate",
        "portal": "https://www.crowncommercial.gov.uk",
        "notes": "GP impact modest (£5–20k/search) but defines where VMA can compete on central-gov exec search.",
    },
    {
        "key": "nhs_rm6380_workforce_alliance",
        "title": "NHS Workforce Alliance — Exec Search & Interim",
        "ad_title": "NHS & health sector — comms search framework",
        "ad_desc": "Get VMA appointed to bid for senior NHS comms-leadership roles.",
        "code": "RM6380",
        "buyer": "NHS Workforce Alliance (via CCS)",
        "scope": "Executive search & interim for NHS / health-sector senior leadership, including comms & corporate affairs.",
        "comms_relevant": True,
        "expiry_date": "2026-12-31",
        "date_confidence": "estimate",
        "portal": "https://www.crowncommercial.gov.uk",
        "notes": "Health-sector comms-leadership searches; verify lot coverage for comms roles.",
    },
    {
        "key": "nda_shared_services_lot6",
        "title": "Nuclear Decommissioning Authority — Shared Services (Lot 6)",
        "ad_title": "Nuclear & energy — comms search framework",
        "ad_desc": "Senior comms & corporate-affairs search for NDA group bodies.",
        "code": "",
        "buyer": "Nuclear Decommissioning Authority Shared Services Alliance",
        "scope": "Recruitment / executive search for NDA group bodies; Lot 6 covers senior / specialist roles.",
        "comms_relevant": True,
        "expiry_date": "2027-03-31",
        "date_confidence": "estimate",
        "portal": "https://www.gov.uk/government/organisations/nuclear-decommissioning-authority",
        "notes": "Niche but high-value; confirm Lot 6 scope covers comms/corporate-affairs leadership.",
    },
    {
        "key": "devolved_gov_exec_search",
        "title": "Devolved Government — Executive Search",
        "ad_title": "Devolved government — comms search framework",
        "ad_desc": "Senior comms search for Scottish, Welsh & NI public bodies.",
        "code": "",
        "buyer": "Scottish Government / Welsh Government / NI bodies",
        "scope": "Executive search frameworks run by the devolved administrations for senior public-appointment & comms roles.",
        "comms_relevant": True,
        "expiry_date": "2027-09-30",
        "date_confidence": "estimate",
        "portal": "https://www.publiccontractsscotland.gov.uk",
        "notes": "Track each administration's own portal; references vary by nation.",
    },
]


def _parse(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return None


def _months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.0


def load_frameworks(today: date | None = None) -> list[dict]:
    """Return the watched frameworks decorated with refresh-window status.

    status: "refresh_window" (re-procurement window open now),
            "live" (running; window not yet open),
            "expired", or "unknown" (no date to compute from).
    Sorted: open refresh windows first, then soonest expiry."""
    today = today or date.today()
    out: list[dict] = []
    for fw in FRAMEWORKS:
        exp = _parse(fw.get("expiry_date"))
        est = fw.get("date_confidence") != "verified"
        days_to_expiry = (exp - today).days if exp else None
        if exp is None:
            status, window_label = "unknown", "Expiry not set — verify on portal"
            window_pill = "CHECK PORTAL"
        elif today > exp:
            status, window_label = "expired", "Agreement expired — verify re-let"
            window_pill = "EXPIRED"
        elif _months_between(today, exp) <= REFRESH_LEAD_MONTHS:
            status = "refresh_window"
            window_label = (f"Refresh window open · expiry ~{exp:%b %Y}"
                            + (" (est.)" if est else ""))
            window_pill = f"OPEN → {exp:%b}".upper() + f" ’{exp:%y}"
        else:
            status = "live"
            # The re-procurement window opens REFRESH_LEAD_MONTHS before expiry —
            # that's the date an AD should watch for. You can't bid until then.
            total = (exp.year * 12 + exp.month - 1) - REFRESH_LEAD_MONTHS
            wo = date(total // 12, total % 12 + 1, 1)
            window_label = (f"Re-procurement window opens ~{wo:%b %Y} — not open to bid yet"
                            + (" (est.)" if est else ""))
            window_pill = f"OPENS ~{wo:%b}".upper() + f" ’{wo:%y}"
        out.append({
            **fw,
            "status": status,
            "window_label": window_label,
            "window_pill": window_pill,
            "days_to_expiry": days_to_expiry,
            "is_estimate": est,
        })

    rank = {"refresh_window": 0, "live": 1, "unknown": 2, "expired": 3}
    out.sort(key=lambda f: (rank.get(f["status"], 9),
                            f["days_to_expiry"] if f["days_to_expiry"] is not None else 1e9))
    return out
