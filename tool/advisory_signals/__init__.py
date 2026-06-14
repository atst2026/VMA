"""The Advisory Engine — advisory demand as a FIRST-CLASS originated lead.

The platform's hiring lane originates leads from vacancies and bolts an
advisory reading onto them (`tool.advisory.service_fit_for` — "adds no new
signal and changes no detection"). That is backwards for advisory
*origination*: the strongest advisory opportunities — a function that is
stuck, over-stretched or misfiring — frequently have NO vacancy at all.

This package is the parallel lane. Each detector emits a typed
`AdvisorySignal` (below) that fires independently of job-board / ATS
activity; `tool.advisory_gate` qualifies it on a consulting-adapted
MEDDPICC gate and returns a KILL / DEVELOP / PURSUE verdict;
`tool.evidence_pack` renders the meeting-winning deliverable.

Phase 1 ships one detector — `PayGapActionMandate` (`pay_gap.py`) — reusing
the GOV.UK gender-pay-gap dataset the platform already ingests. The
discipline that keeps it honest (and out of the generic-noise trap): a
standing pay-gap figure is NOT a lead. The COMPELLING EVENT is the
statutory reporting / equality-action-plan window being open — a dated
"why now" the calendar pulses already track. Detectors B–J land in
Phase 2 behind the same gate.

See ADVISORY_ENGINE.md for the full build plan.
"""
from __future__ import annotations

from datetime import date

from tool.advisory_signals.base import AdvisorySignal
from tool.advisory_signals.pay_gap import pay_gap_action_signals
from tool.advisory_signals.from_predictors import predictor_advisory_signals

__all__ = ["AdvisorySignal", "pay_gap_action_signals",
           "predictor_advisory_signals", "originate"]


def _pay_gap(today):
    return pay_gap_action_signals(today=today)


def _from_predictors(today):
    return predictor_advisory_signals(today=today)


def originate(today: date | None = None, *, facts_for=None,
              cap: int | None = None) -> list[dict]:
    """Run the advisory lane end-to-end and return ranked verdict rows.

    Detect (every advisory detector) → qualify + verdict (`advisory_gate`)
    → rank by conviction → cap the PURSUE board (scarcity forces ranking,
    per ADVISORY_ENGINE.md §11). Pure orchestration; never raises.

    `facts_for(signal) -> dict` is an optional hook that supplies the
    SPONSOR / ACCESS facts (named buyer, warm route) the contact layer
    resolves; without it those dimensions score low and a raw signal lands
    in DEVELOP rather than PURSUE — the correct, disciplined default (a
    statutory gap with no reachable buyer is not a call-today lead).

    Each row: {signal: dict, verdict, conviction, qual, evidence, why}.
    """
    from tool import advisory_gate

    today = today or date.today()
    signals: list[AdvisorySignal] = []
    for _detector in (_pay_gap, _from_predictors):
        try:
            signals.extend(_detector(today))
        except Exception:  # a detector failure must not sink the lane
            continue

    rows: list[dict] = []
    for sig in signals:
        try:
            facts = (facts_for(sig) if facts_for else None) or {}
            rows.append(advisory_gate.assess(sig, facts, today=today))
        except Exception:
            continue

    # The auto-throttle (ADVISORY_ENGINE.md §11 #1): when recent human
    # approval of advisory PURSUEs dips below the floor, the cap tightens
    # itself — selective by measurement, not just by design. An explicit
    # `cap` overrides it (e.g. tests).
    if cap is None:
        try:
            from tool.advisory_outcomes import decision_cap
            cap = decision_cap(now=_to_dt(today))
        except Exception:
            cap = None
    return advisory_gate.rank_and_cap(rows, cap=cap)


def _to_dt(d):
    from datetime import datetime, timezone
    try:
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except Exception:
        return None
