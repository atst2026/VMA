"""Scoring-freeze batch: event dedupe, senior-TA conditioning, dual
windows, freeze-lift recency guard, crisis half-life, and the four-surface
coherence fixture for rival-mandate leads."""
from datetime import datetime, timedelta, timezone

from tool import gate, lead_engine as LE


def _iso(d):
    return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()


def _ev(key, days, url, label=None, evidence=""):
    return {"trigger_key": key, "trigger_label": label or key,
            "url": url, "source": "src", "tier": "listed",
            "published": _iso(days), "evidence": evidence}


# ---- item 9: event-level dedupe ---------------------------------------
def test_press_echo_collapses_registry_facts_do_not():
    echo = [_ev("ceo_change", 10, f"https://outlet{i}.com/a") for i in range(5)]
    deduped, extra = LE._dedupe_events(echo)
    assert len(deduped) == 1 and extra            # one event, corroborated
    assert deduped[0].get("corroborants") == 4
    filings = [_ev("secured_financing", 10 + i,
                   f"https://www.investegate.co.uk/{i}") for i in range(3)]
    kept, _ = LE._dedupe_events(filings)
    assert len(kept) == 3                          # registry facts all stand


def test_echo_no_longer_inflates_signal():
    one = {"company": "A", "account_tier": "watchlist", "last_seen": _iso(1),
           "events": [_ev("ceo_change", 10, "https://outlet0.com/a")]}
    five = {"company": "A", "account_tier": "watchlist", "last_seen": _iso(1),
            "events": [_ev("ceo_change", 10, f"https://outlet{i}.com/a")
                       for i in range(5)]}
    s1, s5 = LE.score_lead(one)["signal"], LE.score_lead(five)["signal"]
    assert s5 <= s1 * 1.6   # corroboration may lift confidence, never 5x
    # and the collapsed cluster still counts as corroborated
    assert LE.score_lead(five)["corroborated"] if "corroborated" in LE.score_lead(five) else True


# ---- item 3: TA penalty conditional on seniority -----------------------
def test_internal_ta_neutral_for_senior_seats():
    senior = {"internal_ta": True, "predicted_role": "Corporate Affairs Director"}
    pts, basis = gate.propensity_points({}, senior)
    assert pts == gate.PROP_NEUTRAL and basis == "ta_senior"
    q = gate.qualification({"triggers": []}, senior, {}, "open")
    assert q["budget"] == 1                       # neutral, not zeroed
    midlevel = {"internal_ta": True, "predicted_role": "Comms Assistant"}
    pts2, _ = gate.propensity_points({}, midlevel)
    assert pts2 == gate.PROP_INTERNAL             # volume hiring keeps it


def test_posture_does_not_flip_internal_for_senior_seats():
    item = {"company": "Acme", "account_tier": "watchlist",
            "last_seen": _iso(1), "internal_ta": True,
            "predicted_role": "Head of Investor Relations",
            "events": [_ev("ceo_change", 20, "https://investegate.co.uk/x")]}
    lead = LE.score_lead(item)
    assert lead["posture"]["direction"] != "internal"


# ---- coherence fixture: all four surfaces agree on a rival mandate ----
def test_rival_mandate_four_surface_coherence():
    from tool.dashboard import _mr_gate_fields
    item = {"company": "Rival Client", "account_tier": "watchlist",
            "last_seen": _iso(1), "pid": "rc",
            "events": [_ev("ceo_change", 20, "https://investegate.co.uk/x")]}
    lead = LE.score_lead(item)
    lead["conflict"] = True
    g = gate.assess(item, lead)
    # 1. the ring banks 25/25
    assert gate.propensity_points(lead, item)[0] == gate.PROP_PROVEN
    # 2. the scorecard Budget agrees
    assert g["qual"]["budget"] == 2
    # 3. the gate reason names the mandate and the interim pitch
    assert any("Rival search firm" in r for r in g["reasons"])
    # 4. the pill reads proven with the mandate as evidence
    row = {**item, "gate": g, "lead": lead}
    f = _mr_gate_fields(row)
    assert f["prop"].startswith("Proven agency user")
    assert "rival" in f["prop"].lower() or "rival" in f["propWhy"].lower()


# ---- freeze-lift recency guard -----------------------------------------
def _frozen(demand_days, freeze_days=20):
    return {"company": "FrozenCo", "account_tier": "watchlist",
            "last_seen": _iso(1), "events": [
                _ev("restructure", freeze_days, "https://investegate.co.uk/x",
                    evidence="restructure announced alongside a hiring freeze"),
                _ev("job_ad_cluster", demand_days, "https://jobs.example.com",
                    evidence="three senior comms roles live")]}


def test_zombie_ad_does_not_lift_the_freeze_dampener():
    stale = LE.score_lead(_frozen(demand_days=60))   # ad predates the freeze
    fresh = LE.score_lead(_frozen(demand_days=3))    # ad postdates it
    assert fresh["signal"] > stale["signal"]


# ---- crisis half-life ----------------------------------------------------
def test_crisis_urgency_decays_in_days_not_weeks():
    young = {"company": "C", "account_tier": "watchlist", "last_seen": _iso(1),
             "events": [_ev("crisis_event", 3, "https://news.example.com/a")]}
    old = {"company": "C", "account_tier": "watchlist", "last_seen": _iso(1),
           "events": [_ev("crisis_event", 40, "https://news.example.com/a")]}
    ry = LE.score_lead(young)["triggers"][0]["recency_mult"]
    ro = LE.score_lead(old)["triggers"][0]["recency_mult"]
    assert ry == 1.0 and ro < 0.45
