"""AD-room outcome ladder + the monthly weight-review report."""
import tool.lead_outcomes as LO


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(LO, "_file", lambda: tmp_path / "outcomes.json")


def test_fine_grained_outcomes_and_conversion(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert LO.record("a", "no_answer", {"score": 72, "tier": "ready", "prop": "Unknown"})
    assert LO.record("b", "meeting", {"score": 81, "tier": "ready", "prop": "Proven agency user"})
    assert LO.record("c", "placement", {"score": 40, "tier": "dev", "prop": "Unknown"})
    assert LO.record("d", "converted")          # legacy value still accepted
    assert not LO.record("e", "ghosted")        # unknown rung rejected
    cal = LO.calibration()
    assert cal["logged"] == 4 and cal["converted"] == 3


def test_outcome_report_aggregates_against_snapshots(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    LO.record("a", "no_answer", {"score": 72, "tier": "ready", "prop": "Unknown"})
    LO.record("b", "meeting", {"score": 81, "tier": "ready", "prop": "Proven agency user"})
    LO.record("c", "placement", {"score": 40, "tier": "dev", "prop": "Unknown"})
    r = LO.outcome_report()
    assert r["by_outcome"] == {"no_answer": 1, "meeting": 1, "placement": 1}
    assert r["by_score_band"]["70+"] == {"n": 2, "converted": 1}
    assert r["by_score_band"]["<45"] == {"n": 1, "converted": 1}
    assert r["by_tier"]["ready"]["n"] == 2
    assert r["by_propensity"]["Unknown"]["converted"] == 1
