"""Tests for the Lead Strength score and board tiering (tool/gate.py)."""
from tool import gate


def _lead(**kw):
    base = {"fit": 8, "signal": 6.0, "n_pro": 2, "premature": False,
            "contradictions": [], "conflict": False, "anti_triggers": [],
            "who_to_call": "CEO office"}
    base.update(kw)
    return base


def _g(presented=True, families=3, primary=1, reasons=None):
    return {"presented": presented,
            "reasons": reasons or ([] if presented else ["Watch-grade"]),
            "evidence": {"families": families, "primary": primary,
                         "credible": 1, "level": "full"}}


def test_score_bounds_and_strong_lead_reads_high():
    # fit 20 + signal 30 + propensity 10 (unknown) + buyer 8 (mapped
    # seat) + timing 10 = 78. Source volume scores NOTHING.
    s = gate.strength_score(_lead(fit=10, signal=10, n_pro=3),
                            _g(families=3, primary=1))
    assert s == 78
    # Proven fee-payer (+15) with a WARM route (+7 over the cold routes)
    # maxes the scale; a named-but-cold contact alone does not.
    full = gate.strength_score(_lead(fit=10, signal=10, n_pro=3),
                               _g(families=3, primary=1),
                               {"psl_status": "on",
                                "warm_route": {"warm": True}})
    assert full == 100
    named = gate.strength_score(_lead(fit=10, signal=10, n_pro=3),
                                _g(families=3, primary=1),
                                {"psl_status": "on",
                                 "seeded_contact_name": "Jane Doe"})
    assert named == 93
    # Degenerate input never raises and lands at the floor of the scale
    # (neutral propensity + open-window timing only).
    assert gate.strength_score(None, None) == gate.strength_score({}, {}) <= 25


def test_propensity_axis_orders_the_score():
    lead, g = _lead(), _g()
    neutral = gate.strength_score(lead, g)
    proven = gate.strength_score(lead, g, {"psl_status": "on"})
    inhouse = gate.strength_score(lead, g, {"internal_ta": True})
    external = gate.strength_score(
        dict(lead, posture={"direction": "external", "reasons": []}), g)
    assert proven - neutral == gate.PROP_PROVEN - gate.PROP_NEUTRAL
    assert neutral - inhouse == gate.PROP_NEUTRAL - gate.PROP_INTERNAL
    assert external - neutral == gate.PROP_EXTERNAL - gate.PROP_NEUTRAL
    assert proven > external > neutral > inhouse


def test_authoritative_flags_beat_inferred_posture():
    lead = _lead(posture={"direction": "external", "reasons": []})
    pts, basis = gate.propensity_points(lead, {"internal_ta": True})
    assert pts == gate.PROP_INTERNAL and basis == "authoritative"


def test_source_volume_no_longer_scores_but_contradictions_do():
    full = gate.strength_score(_lead(), _g())
    thin = gate.strength_score(_lead(), _g(presented=False, families=1,
                                           primary=0))
    # The design change: source counting is a verification tag, not value.
    assert full == thin
    contradicted = gate.strength_score(
        _lead(contradictions=["cuts point the other way"]), _g())
    assert full - contradicted == 8


def test_warm_route_outranks_every_cold_route():
    # AD-room rescore: named-cold and mapped-seat are the same 8 (the gap
    # between them is minutes of research); warm is the different animal.
    base = gate.strength_score(_lead(), _g())                 # mapped seat
    named = gate.strength_score(_lead(), _g(), {"seeded_contact_name": "J"})
    warm = gate.strength_score(dict(_lead(), relationship="warm"), _g())
    tagged = gate.strength_score(_lead(), _g(), {"warm_route": {"warm": True}})
    assert named == base
    assert warm - named == 7 and tagged == warm


def test_premature_scores_below_in_window():
    in_window = gate.strength_score(_lead(), _g())
    fresh = gate.strength_score(_lead(premature=True), _g(presented=False))
    assert in_window > fresh


def test_only_administration_floors_the_score():
    # Rival mandates and freezes convert the play (timed watch / interim);
    # they rank on their merits instead of being floored.
    s = gate.strength_score(_lead(fit=10, signal=10, conflict=True), _g())
    assert s > 15
    sf = gate.strength_score(_lead(fit=10, signal=10,
                                   anti_triggers=["hiring_freeze"]), _g())
    assert sf > 15
    s2 = gate.strength_score(_lead(anti_triggers=["administration"]), _g())
    assert s2 <= 15


def test_tiers():
    assert gate.tier_for(_lead(), _g(presented=True), 80) == "ready"
    # Rival mandate -> Watch (timed), never blocked or hidden.
    assert gate.tier_for(_lead(conflict=True), _g(presented=False), 10) == "early"
    # A freeze is an interim play — it tiers normally on score/gate.
    assert gate.tier_for(_lead(anti_triggers=["hiring_freeze"]),
                         _g(presented=False), 60) == "dev"
    assert gate.tier_for(_lead(anti_triggers=["administration"]),
                         _g(presented=False), 60) == "blocked"
    assert gate.tier_for(_lead(), _g(presented=False),
                         gate.SCORE_DEVELOPING) == "dev"
    assert gate.tier_for(_lead(), _g(presented=False),
                         gate.SCORE_DEVELOPING - 1) == "early"
