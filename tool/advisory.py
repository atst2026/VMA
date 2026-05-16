"""Advisory Services lens.

VMA Group's specialism is Executive Search / Permanent Recruitment /
**Advisory** / Communications. The detectors were built to surface
PLACEMENT leads only — half the specialism. Every high-precision signal
they already produce is *also* an Advisory entry point, and advisory
(comms-function capability review, talent & market mapping, leadership
benchmarking, succession advisory, reputation-readiness audit) is the
earlier, lower-barrier sale that frequently opens the door to the
retained search on the same account.

This module is the single place that maps an event class → its advisory
framing, so the advisory pitch language can be tuned centrally rather
than scattered across six detectors. It adds no new signal and changes
no detection — it exposes a second billable action on signals that
already passed the precision gate, fully in line with the strict
detection-engine filter.

Keys are stable predictor trigger keys (tool.predictive.patterns) plus
the standalone detector contexts ("water_sar", "contract_end",
"funding", "following") and the calendar-pulse keys.
"""
from __future__ import annotations

_DEFAULT = ("Advisory: comms-function capability review + talent/market "
            "mapping — the lower-barrier sale that opens the retained search.")

_ADVISORY: dict[str, str] = {
    # ---- predictor trigger keys ----
    "mna":
        "Advisory: integration & transition-comms operating-model review "
        "+ market map of the post-deal comms leadership.",
    "restructure":
        "Advisory: comms target-operating-model & capability review for "
        "the reorganised function.",
    "regulator_action":
        "Advisory: reputation-comms capability audit + crisis-readiness "
        "review ahead of the permanent reputation hire.",
    "regulator_probe_early":
        "Advisory: reputation-comms capability audit + crisis-readiness "
        "review for the live-investigation period.",
    "crisis_event":
        "Advisory: crisis & reputation-readiness audit; comms-function "
        "review ahead of the permanent reputation hire.",
    "profit_warning":
        "Advisory: IR & Corporate Affairs capability review + investor-"
        "narrative readiness.",
    "ceo_change":
        "Advisory: comms-function review aligned to the new CEO + a "
        "succession map of the comms bench.",
    "chair_change":
        "Advisory: board-/governance-comms review aligned to the new "
        "chair + succession map.",
    "cfo_change":
        "Advisory: IR & financial-comms capability review aligned to the "
        "new CFO.",
    "chro_change":
        "Advisory: internal-comms & change-capability review under the "
        "new people leadership.",
    "ir_director_change":
        "Advisory: IR function review + succession map for the "
        "IR/Corporate Affairs bench.",
    "ipo_listing":
        "Advisory: pre-admission IR / Corporate Affairs readiness review "
        "+ market map of the listed-co comms leadership.",
    "contract_loss":
        "Advisory: change & stakeholder-comms capability review post-"
        "loss; market map for the rebuild.",
    "ic_platform_rfp":
        "Advisory: internal-comms operating-model & channel review.",
    "press_velocity_spike":
        "Advisory: reputation-readiness review while coverage is "
        "elevated.",
    # ---- standalone detector contexts ----
    "water_sar":
        "Advisory: crisis/stakeholder-comms capability audit + comms-"
        "function org review + succession map for the permanent "
        "reputation hire.",
    "contract_end":
        "Advisory: change & transition-comms capability review + market "
        "map ahead of the recompete decision.",
    "funding":
        "Advisory: comms-function design-for-scale + benchmarking; build "
        "the senior-comms market map ahead of the ~6-month hire.",
    "following":
        "Advisory: succession & org review of the vacated comms function "
        "+ talent map of the replacement market.",
    # ---- calendar-pulse keys ----
    "fca_consumer_duty_2026":
        "Advisory: regulatory-comms capability & board-reporting "
        "readiness review + peer benchmarking ahead of 31 Jul.",
    "uk_srs_2026":
        "Advisory: sustainability / ESG-comms capability review + peer "
        "benchmarking ahead of the first mandatory reporting cycle.",
    "mog_post_sr_2026":
        "Advisory: GCS comms operating-model & transition-capability "
        "review for the reorganised department.",
    "agm_reporting_2026":
        "Advisory: IR & corporate-reporting comms capability review + "
        "AGM/governance-narrative readiness ahead of results season.",
    "gender_pay_gap_2026":
        "Advisory: internal/DEI-comms capability review + gender-pay "
        "narrative & scrutiny-response readiness.",
    "nhs_planning_2026":
        "Advisory: NHS comms operating-model & change-capability review "
        "for the planning/restructure round.",
    "he_clearing_2026":
        "Advisory: student-recruitment & brand-comms capability review "
        "+ market map ahead of clearing.",
}


def advisory_for(context: str | None) -> str:
    """Return the advisory framing for an event class. Unknown / missing
    context falls back to the generic capability-review + market-map
    line (never empty — every surfaced signal has an advisory path)."""
    if not context:
        return _DEFAULT
    return _ADVISORY.get(str(context).strip().lower(), _DEFAULT)
