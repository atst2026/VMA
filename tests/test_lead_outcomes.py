"""Tests for the lead outcome / calibration store."""
import importlib

from tool import lead_outcomes as LO


def _fresh(tmp_path, monkeypatch):
    # point the store at a temp file
    f = tmp_path / "lead_outcomes.json"
    monkeypatch.setattr(LO, "_file", lambda: f)
    return f


def test_record_and_get(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert LO.record("tesco", "called", {"signal": 8.0, "action": "call_today"})
    assert LO.get("tesco") == "called"
    assert LO.get("unknown") is None


def test_snapshot_is_preserved(tmp_path, monkeypatch):
    f = _fresh(tmp_path, monkeypatch)
    LO.record("tesco", "converted", {"signal": 8.0, "fit": 10, "triggers": ["chro_change"]})
    import json
    raw = json.loads(f.read_text())["tesco"]
    assert raw["snapshot"]["signal"] == 8.0
    assert raw["outcome"] == "converted"
    assert raw["history"]  # history appended


def test_invalid_outcome_rejected(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert LO.record("x", "maybe") is False
    assert LO.record("", "called") is False


def test_clear_outcome(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    LO.record("x", "dead")
    assert LO.get("x") == "dead"
    assert LO.record("x", "")  # clear
    assert LO.get("x") is None


def test_calibration_counts(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    cal0 = LO.calibration()
    assert cal0["logged"] == 0 and cal0["calibrating"] is True
    LO.record("a", "called")
    LO.record("b", "converted")
    LO.record("c", "dead")
    cal = LO.calibration()
    assert cal["logged"] == 3
    assert cal["converted"] == 1
    assert cal["calibrating"] is True
    assert cal["target"] == LO.CALIBRATION_TARGET


def test_get_all_only_returns_outcomed(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    LO.record("a", "called")
    LO.record("b", "")  # cleared
    allo = LO.get_all()
    assert allo == {"a": "called"}
