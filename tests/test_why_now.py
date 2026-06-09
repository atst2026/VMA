"""Tests for the v2 Why-Now composer + fee-driver classification."""
from tool.why_now import compose_why_now, fee_driver


def test_fee_driver_priority_strongest_class_wins():
    # A mishire stacked with a funding round is forced & confidential,
    # not growth — the strongest fee case leads the pitch.
    label, tip = fee_driver(["funding", "mishire_reversal"])
    assert label == "Forced & confidential"
    assert "confidential" in tip


def test_fee_driver_v2_classes():
    assert fee_driver(["inhouse_search_failing"])[0] == "Failed DIY"
    assert fee_driver(["hiring_restart"])[0] == "Budget thaw"
    assert fee_driver(["comms_leader_departure"])[0] == "Vacated seat"
    assert fee_driver(["contract_end"])[0] == "Deadline-driven"
    assert fee_driver(["funding"])[0] == "Growth demand"
    assert fee_driver(["ceo_change"])[0] == "Leadership reset"


def test_fee_driver_unknown_key_gets_default():
    label, tip = fee_driver(["something_new", None])
    assert label == "Live signal"
    assert tip


def test_compose_stacks_chronologically_with_dates():
    events = [
        {"trigger_key": "chro_change", "trigger_label": "CHRO change",
         "published": "2026-05-28T09:00:00+00:00", "evidence": "x"},
        {"trigger_key": "profit_warning", "trigger_label": "Profit warning",
         "published": "2026-05-12T09:00:00+00:00", "evidence": "y"},
    ]
    label, tip = fee_driver([e["trigger_key"] for e in events])
    out = compose_why_now(events, "A comms reset usually follows", tip)
    # Earliest first, dates rendered, count stated.
    assert out.index("Profit warning (12 May)") < out.index("CHRO change (28 May)")
    assert "2 independent signals" in out
    assert "A comms reset usually follows." in out
    assert out.endswith("Fee case: " + tip)


def test_compose_single_event_and_empty_degrade_gracefully():
    one = compose_why_now(
        [{"trigger_label": "CEO change",
          "published": "2026-06-01T00:00:00+00:00"}],
        "Base line.", "tip here")
    assert one.startswith("Signal: CEO change (1 Jun).")
    assert "Base line." in one and one.endswith("Fee case: tip here")
    # No events, no fee tip → exactly the base line (never worse than v1).
    assert compose_why_now([], "Base line.") == "Base line."


def test_compose_ignores_malformed_events():
    out = compose_why_now([None, {}, {"trigger_label": "  "}],
                          "Base.", "t")
    assert out == "Base. Fee case: t"
