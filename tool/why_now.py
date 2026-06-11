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


# What kind of hire each fee-driver class actually signals. Used as the
# card's seat line when no incumbent has been verified: the signal earns
# a direction ("a build-out", "a replacement", "a reshuffle"), not a
# precise chair nobody has confirmed. Deliberately broader than one
# function where the trigger genuinely is broader (growth events fund
# builds across comms AND marketing).
_HIRE_HINT = {
    "Forced & confidential": "Crisis-response {func} leadership",
    "Failed DIY":            "External search for a stalled senior hire",
    "Budget thaw":           "Senior {func} hiring (post-freeze restart)",
    "Vacated seat":          "Replacement for an open senior {func} seat",
    "Deadline-driven":       "Senior {func} capability to a fixed deadline",
    "Leadership reset":      "Senior {func} reshuffle under new leadership",
}


def hire_hint(trigger_keys, marketing: bool = False) -> str:
    """The kind of hire the stack's strongest trigger indicates."""
    label, _tip = fee_driver(list(trigger_keys or []))
    if label == "Growth demand":
        # Growth/transaction events fund team builds across the desks —
        # honestly broader than any single function.
        return ("Marketing & brand team build-out (growth or "
                "transaction funded)" if marketing
                else "Comms & marketing team build-out (growth or "
                     "transaction funded)")
    func = "marketing" if marketing else "comms"
    t = _HIRE_HINT.get(label)
    return t.format(func=func) if t else f"Senior {func} hire likely"


def compose_why_now(events: list[dict], base_line: str,
                    fee_tip: str = "") -> str:
    """Compose the Why-Now: the desk-correct implication (base_line, built
    by the caller so desk copy is preserved) + the closing fee case. The
    old dated stack-chronology preamble ("Stacked and corroborated: …" /
    "Signal: …") was dropped — it repeated the trigger labels the
    narrative already covers and read as noise on the card. `events` is
    kept in the signature so call sites are untouched."""
    parts: list[str] = []
    base = (base_line or "").strip()
    if base:
        parts.append(base if base.endswith((".", "!", "?")) else base + ".")
    if fee_tip:
        parts.append(f"Fee case: {fee_tip}")
    return " ".join(parts).strip()
