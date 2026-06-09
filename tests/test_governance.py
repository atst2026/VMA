"""Tests for the acceptance-governance layer: the verdict log, the gate
field projection onto console rows, and the daily-cap demotion."""
import json

import tool.verdict_log as VL
from tool.dashboard import _build_mr_rows, _mr_gate_fields


# ====================================================================
# Verdict log (durable acceptance ground truth)
# ====================================================================
def test_verdict_log_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(VL, "STATE_DIR", tmp_path)
    monkeypatch.setattr(VL, "LOG_FILE", tmp_path / "verdict_log.json")
    assert VL.get_all() == []
    assert VL.record("tesco", "predictor", "call_today", "Tesco")
    assert VL.record("tesco", "predictor", "reject", "Tesco")
    recs = VL.get_all()
    assert len(recs) == 2 and recs[0]["verdict"] == "call_today"
    assert VL.latest_for("tesco") == "reject"          # latest wins
    assert not VL.record("", "predictor", "call_today")  # no rid
    assert not VL.record("x", "predictor", "maybe")      # invalid verdict
    # File is valid JSON on disk.
    data = json.loads((tmp_path / "verdict_log.json").read_text())
    assert isinstance(data, list) and len(data) == 2


def test_verdict_log_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(VL, "STATE_DIR", tmp_path)
    monkeypatch.setattr(VL, "LOG_FILE", tmp_path / "verdict_log.json")
    monkeypatch.setattr(VL, "MAX_RECORDS", 5)
    for i in range(8):
        VL.record(f"r{i}", "predictor", "nurture")
    recs = VL.get_all()
    assert len(recs) == 5 and recs[0]["rid"] == "r3"


# ====================================================================
# Gate-field projection
# ====================================================================
def _row(presented=True, **gate_extra):
    g = {"presented": presented, "confidence": "High" if presented else None,
         "reasons": [] if presented else ["Watch-grade"],
         "recheck_days": None if presented else 7, "investigate": False,
         "evidence": {"families": 3, "primary": 1, "credible": 1,
                      "level": "full"},
         "kill": "An interim appointment.", "move": "Ring the CCO.",
         "cap": 7, "throttled": False}
    g.update(gate_extra)
    return {"_kind": "predictor", "company": "Tesco", "pid": "tesco",
            "strength": "high", "window_label": "~6-12 wks",
            "predicted_role": "Head of Comms", "gate": g, "verdict": "",
            "events": [{"trigger_key": "ceo_change",
                        "trigger_label": "CEO change",
                        "published": "2026-05-01T00:00:00+00:00",
                        "evidence": "x", "url": "http://a"}]}


def test_gate_fields_flatten_onto_row():
    f = _mr_gate_fields(_row())
    assert f["presented"] == 1 and f["conf"] == "High"
    assert f["evFams"] == 3 and f["evPrim"] == 1
    assert f["kill"] and f["move"]
    q = _mr_gate_fields(_row(presented=False))
    assert q["presented"] == 0 and q["gateWhy"] == "Watch-grade"
    assert q["recheck"] == 7


def test_rows_without_gate_present_by_default():
    # Defensive: the gate can narrow the board, never error it empty.
    assert _mr_gate_fields({})["presented"] == 1


# ====================================================================
# Daily cap: over-cap presented rows demote to the queue, visibly
# ====================================================================
def test_cap_demotes_overflow_to_queue():
    rows = [_row() for _ in range(10)]
    bd, _ = _build_mr_rows(rows, [], "Head of Communications", cap=7)
    presented = [r for r in bd if r["presented"]]
    queued = [r for r in bd if not r["presented"]]
    assert len(presented) == 7 and len(queued) == 3
    assert all("cap of 7" in r["gateWhy"] for r in queued)
    assert all(not r["conf"] for r in queued)


def test_cap_does_not_touch_already_queued_rows():
    rows = [_row(), _row(presented=False), _row()]
    bd, _ = _build_mr_rows(rows, [], "Head of Communications", cap=7)
    assert [r["presented"] for r in bd] == [1, 0, 1]
    assert "Watch-grade" in bd[1]["gateWhy"]
