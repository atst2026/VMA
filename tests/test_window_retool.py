"""Tests for the v2 too-fresh window re-tool in lead_engine: per-family
holds (leadership 28d, funding/IPO 21d), binding per anticipatory trigger,
with the demand-now bypass intact."""
from datetime import datetime, timedelta, timezone

from tool import lead_engine as LE


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _ev(key, days_ago, url="prweek.com"):
    return {"trigger_key": key, "trigger_label": key, "url": url,
            "source": url, "tier": "covered", "published": _iso(days_ago),
            "evidence": ""}


def _score(events):
    return LE.score_lead({"company": "Tesco", "account_tier": "watchlist",
                          "events": events, "last_seen": _iso(1)})


def test_leadership_hold_is_28_days():
    held = _score([_ev("ceo_change", 25)])
    assert held["premature"] and held["fresh_hold_days"] == LE.LEADERSHIP_HOLD_DAYS
    # 25d would have cleared the old flat 21-day hold — the re-tool keeps
    # it premature until day 28 (the 4-12 week presentation window).
    live = _score([_ev("ceo_change", 30)])
    assert not live["premature"]


def test_funding_hold_stays_21_days():
    held = _score([_ev("funding", 18, url="techcrunch.com")])
    assert held["premature"] and held["fresh_hold_days"] == LE.EVENT_HOLD_DAYS
    assert not _score([_ev("funding", 22, url="techcrunch.com")])["premature"]


def test_hold_binds_per_trigger_not_per_stack():
    # A fresh crisis next to a MATURE leadership change must not re-freeze
    # the stack (the old freshest-of-anything logic did exactly that).
    lead = _score([_ev("ceo_change", 50), _ev("crisis_event", 5)])
    assert not lead["premature"]


def test_demand_now_bypasses_the_hold():
    lead = _score([_ev("ceo_change", 5),
                   _ev("job_ad_cluster", 2, url="greenhouse.io")])
    assert not lead["premature"]


def test_gate_recheck_uses_exposed_hold_fields():
    held = _score([_ev("ceo_change", 25)])
    assert held["freshest_age_days"] <= 26
    from tool import gate
    g = gate.assess({"company": "Tesco", "events": [_ev("ceo_change", 25)]},
                    held)
    assert not g["presented"]
    assert g["recheck_days"] is not None and 1 <= g["recheck_days"] <= 4
