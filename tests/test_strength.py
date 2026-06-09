"""Tests for the Lead Strength score and board tiering (tool/gate.py)."""
from tool import gate


def _lead(**kw):
    base = {"fit": 8, "signal": 6.0, "n_pro": 2, "premature": False,
            "contradictions": [], "conflict": False, "anti_triggers": []}
    base.update(kw)
    return base


def _g(presented=True, families=3, primary=1, reasons=None):
    return {"presented": presented,
            "reasons": reasons or ([] if presented else ["Watch-grade"]),
            "evidence": {"families": families, "primary": primary,
                         "credible": 1, "level": "full"}}


def test_score_bounds_and_strong_lead_reads_high():
    s = gate.strength_score(_lead(fit=10, signal=10, n_pro=3),
                            _g(families=3, primary=1))
    assert 90 <= s <= 100
    # Degenerate input never raises and lands at the floor of the scale
    # (only the open-window timing credit, no fit/signal/evidence).
    assert gate.strength_score(None, None) == gate.strength_score({}, {}) <= 10


def test_score_orders_evidence_and_contradictions():
    full = gate.strength_score(_lead(), _g())
    thin = gate.strength_score(_lead(), _g(presented=False, families=1,
                                           primary=0))
    contradicted = gate.strength_score(
        _lead(contradictions=["cuts point the other way"]), _g())
    assert full > thin
    assert full - contradicted == 8


def test_premature_scores_below_in_window():
    in_window = gate.strength_score(_lead(), _g())
    fresh = gate.strength_score(_lead(premature=True), _g(presented=False))
    assert in_window > fresh


def test_hard_blocks_floor_into_blocked_band():
    s = gate.strength_score(_lead(fit=10, signal=10, conflict=True), _g())
    assert s <= 15
    s2 = gate.strength_score(_lead(anti_triggers=["administration"]), _g())
    assert s2 <= 15


def test_tiers():
    assert gate.tier_for(_lead(), _g(presented=True), 80) == "ready"
    assert gate.tier_for(_lead(conflict=True), _g(presented=False), 10) == "blocked"
    assert gate.tier_for(_lead(anti_triggers=["hiring_freeze"]),
                         _g(presented=False), 60) == "blocked"
    assert gate.tier_for(_lead(), _g(presented=False),
                         gate.SCORE_DEVELOPING) == "dev"
    assert gate.tier_for(_lead(), _g(presented=False),
                         gate.SCORE_DEVELOPING - 1) == "early"
