"""v2 Why-Now composer + fee-driver classification.

v1 rendered one generic template sentence per lead from its FIRST trigger
only. v2 composes the narrative from the FULL stack: every corroborating
signal in date order with its evidence, the desk-correct implication, and
a closing "fee case" — the structural reason this company pays a fee
rather than running the hire itself. The fee case is the down-market
re-frame: an Account Director opening the card sees not just what
happened but why this lead converts into revenue.

Deterministic and dashboard-agnostic: pure functions over the event dicts
the predictor pipeline already persists (trigger_key / trigger_label /
published / evidence). No fetches, no model calls — unit-tested.
"""
from __future__ import annotations

from datetime import datetime

# Fee-driver classes, strongest first. A stack is classified by the
# highest-priority class any of its triggers belongs to: a mishire
# stacked with a funding round is "Forced & confidential", not "Growth".
_FEE_CLASSES: list[tuple[str, str, set[str]]] = [
    ("Forced & confidential",
     "The replacement or response is forced and usually confidential — "
     "work that cannot be advertised or run in-house, whatever the "
     "budget climate.",
     {"mishire_reversal", "crisis_event", "regulator_action",
      "regulator_probe_early", "water_sar"}),
    ("Failed DIY",
     "They have already tried to fill this without an agency and failed — "
     "the cost of the free route is paid, making this the easiest fee "
     "conversation in a quiet market.",
     {"inhouse_search_failing"}),
    ("Budget thaw",
     "Hiring has visibly restarted after a freeze — budget is moving "
     "again while competitors still treat the account as dormant.",
     {"hiring_restart"}),
    ("Vacated seat",
     "A live seat is open or opening — the need is concrete, not "
     "speculative, and every week unfilled has a visible cost.",
     {"comms_leader_departure", "cascade", "ir_director_change",
      "interim_watch"}),
    ("Deadline-driven",
     "The buying moment runs on the calendar, not the budget cycle — "
     "re-tenders and frameworks must go to market on fixed dates.",
     {"contract_end", "framework_award", "framework_displacement",
      "stale_mandate", "ic_platform_rfp"}),
    ("Growth demand",
     "Fresh capital or expansion funds an external build-out — speed "
     "matters to the investment case, which favours a search firm.",
     {"funding", "secured_financing", "ipo_listing", "pe_acquisition",
      "mna", "ownership_change", "hiring_gap", "seniority_gap",
      "job_ad_cluster", "martech_adoption", "rebrand", "esg_bcorp",
      "press_velocity_spike", "market_entry"}),
    ("Leadership reset",
     "A leadership or strategy reset reopens the supplier relationship — "
     "the window where an incumbent-free pitch lands.",
     {"ceo_change", "cfo_change", "chro_change", "chair_change",
      "cmo_change", "restructure", "redundancy", "activist_stake",
      "profit_warning", "contract_loss", "leadership_tenure", "follow_on"}),
]

_DEFAULT = ("Live signal",
            "A corroborated market signal worth a positioning call ahead "
            "of any brief being written.")


def fee_driver(trigger_keys: list[str | None]) -> tuple[str, str]:
    """Classify a stack by its strongest fee driver. Returns (label, tip)."""
    keys = {k for k in trigger_keys if k}
    for label, tip, members in _FEE_CLASSES:
        if keys & members:
            return label, tip
    return _DEFAULT


def _date(iso: str | None) -> str:
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        return d.strftime("%-d %b")
    except Exception:
        return ""


def compose_why_now(events: list[dict], base_line: str,
                    fee_tip: str = "") -> str:
    """Compose the v2 Why-Now: dated stack chronology + the desk-correct
    implication (base_line, built by the caller so desk copy is
    preserved) + the closing fee case. Degrades gracefully: with no
    usable events it is base_line + fee case — never less than v1."""
    parts: list[str] = []
    evs = [e for e in (events or []) if isinstance(e, dict)
           and (e.get("trigger_label") or "").strip()]
    evs.sort(key=lambda e: e.get("published") or "")
    if len(evs) >= 2:
        steps = []
        for e in evs[:4]:
            d = _date(e.get("published"))
            steps.append(e["trigger_label"].strip()
                         + (f" ({d})" if d else ""))
        parts.append(f"Stacked and corroborated: {' → '.join(steps)} — "
                     f"{len(evs)} independent signals at this company "
                     f"inside one window.")
    elif len(evs) == 1:
        d = _date(evs[0].get("published"))
        parts.append(f"Signal: {evs[0]['trigger_label'].strip()}"
                     + (f" ({d})." if d else "."))
    base = (base_line or "").strip()
    if base:
        parts.append(base if base.endswith((".", "!", "?")) else base + ".")
    if fee_tip:
        parts.append(f"Fee case: {fee_tip}")
    return " ".join(parts).strip()
