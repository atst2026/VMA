"""Tests for the conviction layer: red-team overlay passthrough, the
bronze-alone corroboration rule, tier-1 re-weighting, the tenure window,
and the card projection."""
from datetime import datetime, timedelta, timezone

import tool.investigations as INV
from tool import gate
from tool.dashboard import _mr_gate_fields

NOW = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)


def _ev(key, days_ago=35, url="https://www.investegate.co.uk/x"):
    return {"trigger_key": key, "trigger_label": key, "url": url,
            "source": "RNS",
            "published": (NOW - timedelta(days=days_ago)).isoformat()}


def _lead(keys=("ceo_change",), action="call_today"):
    return {"action": action, "conflict": False, "anti_triggers": [],
            "premature": False, "contradictions": [],
            "who_to_call": "CEO office", "access_text": "",
            "triggers": [{"key": k, "label": k, "recency_mult": 0.9,
                          "age_days": 35.0} for k in keys]}


def _full_events(*keys):
    return [_ev(keys[0] if keys else "ceo_change"),
            _ev(keys[1] if len(keys) > 1 else "funding",
                days_ago=20, url="https://techcrunch.com/y"),
            _ev(keys[1] if len(keys) > 1 else "funding",
                days_ago=18, url="https://www.ft.com/z")]


# ====================================================================
# Bronze-alone corroboration rule
# ====================================================================
def test_bronze_trigger_never_presents_alone():
    g = gate.assess({"company": "Acme", "events": _full_events("rebrand")},
                    _lead(keys=("rebrand",)), now=NOW)
    assert not g["presented"] and g["investigate"]
    assert "Bronze trigger alone" in g["reasons"][0]


def test_bronze_plus_amplifier_still_queues():
    lead = _lead(keys=("rebrand", "press_velocity_spike"))
    g = gate.assess({"company": "Acme", "events": _full_events("rebrand")},
                    lead, now=NOW)
    assert not g["presented"] and "Bronze trigger alone" in g["reasons"][0]


def test_bronze_with_tier1_corroboration_presents():
    lead = _lead(keys=("rebrand", "ceo_change"))
    g = gate.assess({"company": "Acme", "events": _full_events("rebrand")},
                    lead, now=NOW)
    assert g["presented"]


def test_confirmed_overlay_clears_bronze():
    g = gate.assess({"company": "Acme", "events": _full_events("rebrand")},
                    _lead(keys=("rebrand",)),
                    investigation={"verdict": "confirmed"}, now=NOW)
    assert g["presented"] and g["confidence"] == "High"


# ====================================================================
# Red-team overlay passthrough
# ====================================================================
def _rt_overlay(verdict="confirmed"):
    return {"verdict": verdict, "red_team": True, "conviction": 82,
            "business_case": "New external CEO; comms seat vacant per "
                             "archived team page.",
            "warm_opening": "Your new CEO joined six weeks ago…",
            "economic_buyer": "Jane Doe, CEO",
            "champion_path": "none known - cold open",
            "kill_reasons": [], "note": "survived"}


def test_red_team_fields_ride_onto_the_decision():
    g = gate.assess({"company": "Acme", "events": _full_events()},
                    _lead(), investigation=_rt_overlay(), now=NOW)
    assert g["presented"] and g["confidence"] == "High"
    assert g["red_team"] and g["conviction"] == 82
    assert "external CEO" in g["case"]
    assert g["opening"].startswith("Your new CEO")
    assert g["buyer"] == "Jane Doe, CEO"


def test_red_team_kill_shows_reasons():
    ov = _rt_overlay("killed")
    ov["kill_reasons"] = ["interim cover, not a rebuild"]
    g = gate.assess({"company": "Acme", "events": _full_events()},
                    _lead(), investigation=ov, now=NOW)
    assert not g["presented"]
    assert "Killed by red-team: interim cover" in g["reasons"][0]


def test_overlay_clamps_and_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr(INV, "_dir", lambda: tmp_path)
    assert INV.write_overlay("acme", "confirmed", red_team=True,
                             conviction=150, business_case="x" * 1000,
                             kill_reasons=[f"r{i}" for i in range(9)])
    ov = INV.get_all()["acme"]
    assert ov["conviction"] == 100
    assert len(ov["business_case"]) == 600
    assert len(ov["kill_reasons"]) == 5
    # Plain /investigate overlays carry no conviction baggage.
    INV.write_overlay("plain", "confirmed")
    assert "red_team" not in INV.get_all()["plain"]


# ====================================================================
# Card projection
# ====================================================================
def test_projection_carries_conviction_fields():
    row = {"company": "Acme", "pid": "acme", "verdict": "",
           "events": _full_events(), "lead": _lead(),
           "gate": gate.assess({"company": "Acme",
                                "events": _full_events()},
                               _lead(), investigation=_rt_overlay(),
                               now=NOW)}
    f = _mr_gate_fields(row)
    assert f["rt"] == 1 and f["conviction"] == 82
    assert f["bizCase"] and f["opening"] and f["buyer"]
    # Rows without an overlay stay clean.
    row2 = dict(row, gate=gate.assess({"company": "Acme",
                                       "events": _full_events()},
                                      _lead(), now=NOW))
    f2 = _mr_gate_fields(row2)
    assert f2["rt"] == 0 and f2["bizCase"] == ""


# ====================================================================
# Re-weighting + tenure window
# ====================================================================
def test_tier1_weights_and_tenure_window():
    from tool.predictive import patterns as P
    assert P.BY_KEY["crisis_event"].weight == 0.9
    assert P.BY_KEY["pe_acquisition"].weight == 1.0
    from tool.sources import companies_house as CH
    assert CH._TENURE_FLIGHT_RISK_DAYS == int(365 * 3.5)
    assert CH._TENURE_PEAK_MAX_DAYS == int(365 * 4.5)
