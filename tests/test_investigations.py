"""Tests for the investigation overlay store (tool/investigations.py) and
its end-to-end effect through the gate."""
from datetime import datetime, timedelta, timezone

import tool.investigations as INV


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(INV, "_dir", lambda: tmp_path)


def test_overlay_roundtrip_and_validation(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert INV.write_overlay("tesco", "confirmed", note="External CEO verified")
    assert not INV.write_overlay("tesco", "maybe")     # invalid verdict
    assert not INV.write_overlay("", "killed")          # no pid
    out = INV.get_all()
    assert out["tesco"]["verdict"] == "confirmed"
    assert out["tesco"]["note"] == "External CEO verified"


def test_new_verdict_replaces_old(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    INV.write_overlay("tesco", "confirmed")
    INV.write_overlay("tesco", "killed", note="interim cover")
    assert INV.get_all()["tesco"]["verdict"] == "killed"


def test_overlays_expire(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    INV.write_overlay("tesco", "confirmed")
    later = datetime.now(timezone.utc) + timedelta(days=INV.EXPIRE_DAYS + 1)
    assert INV.get_all(now=later) == {}


def test_garbage_files_are_skipped(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "junk.json").write_text("{not json")
    (tmp_path / "bad.json").write_text('{"pid": "x", "verdict": "nope"}')
    INV.write_overlay("good", "recheck", recheck_days=5)
    out = INV.get_all()
    assert list(out) == ["good"] and out["good"]["recheck_days"] == 5


def test_overlay_drives_gate_end_to_end(tmp_path, monkeypatch):
    """A killed overlay suppresses a fully-evidenced lead; a confirmed
    overlay presents a thin one — the manual /investigate loop closes."""
    _setup(tmp_path, monkeypatch)
    from tool import gate
    item = {"company": "Tesco", "events": [
        {"trigger_key": "ceo_change", "trigger_label": "CEO change",
         "url": "https://www.investegate.co.uk/1", "source": "RNS",
         "published": datetime.now(timezone.utc).isoformat()}]}
    lead = {"action": "investigate", "conflict": False, "anti_triggers": [],
            "premature": False, "contradictions": [], "triggers": [
                {"key": "ceo_change", "label": "CEO change",
                 "recency_mult": 0.9, "age_days": 30.0}],
            "who_to_call": "CEO office", "access_text": ""}
    INV.write_overlay("tesco", "confirmed", note="verified")
    g = gate.assess(item, lead, investigation=INV.get_all().get("tesco"))
    assert g["presented"] and g["confidence"] == "High"
    INV.write_overlay("tesco", "killed", note="interim cover")
    g2 = gate.assess(item, lead, investigation=INV.get_all().get("tesco"))
    assert not g2["presented"] and "interim cover" in g2["reasons"][0]
