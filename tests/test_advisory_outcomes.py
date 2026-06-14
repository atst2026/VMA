"""Advisory outcome feedback — the dense approve/spike loop and the
auto-throttle that makes the engine selective by MEASUREMENT (§11 #1)."""
from datetime import datetime, timedelta, timezone

import pytest

from tool import advisory_outcomes as O
from tool.advisory_gate import ADVISORY_DAILY_CAP

NOW = datetime(2026, 6, 14, tzinfo=timezone.utc)


def _rec(decision, days_ago=0):
    return {"ts": (NOW - timedelta(days=days_ago)).isoformat(),
            "decision": decision}


def test_empty_log_is_not_throttled_full_cap():
    a = O.acceptance(records=[], now=NOW)
    assert a["n"] == 0 and a["rate"] is None and a["throttled"] is False
    assert a["cap"] == ADVISORY_DAILY_CAP


def test_high_approval_is_not_throttled():
    recs = [_rec("pursue_approved")] * 4 + [_rec("pursue_spiked")] * 2
    a = O.acceptance(records=recs, now=NOW)
    assert a["rate"] == pytest.approx(4 / 6)
    assert a["throttled"] is False and a["cap"] == ADVISORY_DAILY_CAP


def test_low_approval_throttles_the_cap():
    recs = [_rec("pursue_approved")] * 2 + [_rec("pursue_spiked")] * 4
    a = O.acceptance(records=recs, now=NOW)
    assert a["rate"] == pytest.approx(2 / 6)
    assert a["throttled"] is True
    assert a["cap"] == O.THROTTLED_CAP < ADVISORY_DAILY_CAP


def test_small_sample_never_throttles():
    # Below MIN_VERDICTS, even all-spiked must not throttle (too little data).
    recs = [_rec("pursue_spiked")] * (O.MIN_VERDICTS - 1)
    assert O.acceptance(records=recs, now=NOW)["throttled"] is False


def test_stale_decisions_fall_out_of_the_window():
    recs = [_rec("pursue_spiked", days_ago=30)] * 8     # all too old
    a = O.acceptance(records=recs, now=NOW)
    assert a["n"] == 0 and a["throttled"] is False


def test_meeting_booked_counts_as_a_strong_accept():
    recs = [_rec("meeting_booked")] * 3 + [_rec("pursue_spiked")] * 3
    a = O.acceptance(records=recs, now=NOW)
    assert a["accepted"] == 3 and a["rate"] == pytest.approx(0.5)
    assert a["throttled"] is False                      # 0.5 is not < floor


def test_record_writes_and_reloads(monkeypatch, tmp_path):
    f = tmp_path / "advisory_outcomes.jsonl"
    monkeypatch.setattr(O, "_log_file", lambda: f)
    assert O.record("Acme", "PayGapActionMandate", "pursue_approved",
                    decided_by="Lucy") is True
    assert O.record("Beta", "PayGapActionMandate", "not_a_decision") is False
    a = O.acceptance(now=NOW + timedelta(seconds=1))    # reads the file
    assert a["accepted"] == 1


def test_originate_uses_the_throttled_cap(monkeypatch):
    import tool.advisory_signals as AS
    from tool.advisory_signals.base import AdvisorySignal
    from datetime import date

    sigs = [AdvisorySignal(
        trigger="PayGapActionMandate", company=f"Co{i}",
        service_mix=["edi", "benchmarking"],
        pain="median gender pay gap 22%", buyer_hint="CHRO",
        evidence=[{"source": "GOV.UK", "url":
                   "https://gender-pay-gap.service.gov.uk/x"}],
        window=("2026-03-01", "2026-07-31"), confidence=0.8,
        extra={"size_band": "1000 to 4999", "median": 22.0}) for i in range(6)]
    monkeypatch.setattr(AS, "pay_gap_action_signals", lambda **k: sigs)
    monkeypatch.setattr(AS, "predictor_advisory_signals", lambda **k: [])
    monkeypatch.setattr("tool.advisory_outcomes.decision_cap",
                        lambda **k: O.THROTTLED_CAP)
    facts = {"sponsor_name": "X", "warm_route": {"note": "y"}}
    rows = AS.originate(today=date(2026, 6, 14), facts_for=lambda s: facts)
    assert sum(1 for r in rows if r["verdict"] == "PURSUE") == O.THROTTLED_CAP
