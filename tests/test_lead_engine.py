"""Tests for the two-axis (Fit x Signal) lead engine."""
from datetime import datetime, timezone, timedelta

from tool import lead_engine as LE


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _pred(company="Tesco", account_tier="watchlist", events=None):
    return {"company": company, "account_tier": account_tier,
            "events": events or [], "last_seen": _iso(1)}


def _ev(key, days_ago=1, url="prweek.com", label=None, tier="covered", evidence=""):
    return {"trigger_key": key, "trigger_label": label or key,
            "url": url, "source": url, "tier": tier,
            "published": _iso(days_ago), "evidence": evidence}


# ---- FIT axis ----
def test_fit_watchlist_uk_onpatch_is_core():
    pts, band, why = LE.fit_score("Tesco", "watchlist")
    assert pts >= 7 and band == "core"
    assert why.startswith("Core")


def test_fit_off_watchlist_is_not_core():
    pts, band, why = LE.fit_score("Some Tiny Startup Ltd", "off_watchlist")
    assert band in ("adjacent", "out")
    assert pts < 7


# ---- SIGNAL: recency decay ----
def test_leadership_slow_decay_keeps_value_to_90d():
    fresh = LE.score_lead(_pred(events=[_ev("chro_change", 2)]))
    aged = LE.score_lead(_pred(events=[_ev("chro_change", 80)]))
    very_old = LE.score_lead(_pred(events=[_ev("chro_change", 200)]))
    assert fresh["signal"] == aged["signal"]          # both inside 90d -> x1.0
    assert very_old["signal"] < fresh["signal"]       # decayed


def test_fast_signal_decays_to_near_zero():
    fresh = LE.score_lead(_pred(events=[_ev("crisis_event", 2)]))
    stale = LE.score_lead(_pred(events=[_ev("crisis_event", 60)]))
    assert stale["signal"] < fresh["signal"]
    assert stale["signal"] <= 1.0


# ---- SIGNAL: confidence tiers ----
def test_verified_source_scores_above_single_source():
    verified = LE.score_lead(_pred(events=[_ev("ceo_change", 2, url="companieshouse.gov.uk")]))
    single = LE.score_lead(_pred(events=[_ev("ceo_change", 2, url="randomblog.com")]))
    assert verified["signal"] > single["signal"]
    assert verified["triggers"][0]["confidence"] == "verified"
    assert single["triggers"][0]["confidence"] == "single-source"


# ---- SIGNAL: soft modifiers cannot trigger alone ----
def test_soft_only_does_not_create_signal():
    soft = LE.score_lead(_pred(events=[_ev("press_velocity_spike", 1)]))
    assert soft["signal"] == 0.0
    # but a soft modifier lifts a real signal (capped)
    with_real = LE.score_lead(_pred(events=[_ev("chro_change", 1), _ev("press_velocity_spike", 1)]))
    real_only = LE.score_lead(_pred(events=[_ev("chro_change", 1)]))
    assert with_real["signal"] > real_only["signal"]


# ---- ROUTING ----
def test_high_fit_high_signal_calls_today():
    # two fresh hard signals on a watchlist UK account
    lead = LE.score_lead(_pred(events=[_ev("chro_change", 1, url="companieshouse.gov.uk"),
                                       _ev("job_ad_cluster", 1, url="ft.com")]))
    assert lead["fit_band"] == "core"
    assert lead["signal"] >= LE._SIGNAL_HIGH
    assert lead["action"] == "call_today"


def test_high_fit_low_signal_nurtures():
    lead = LE.score_lead(_pred(events=[_ev("cfo_change", 60)]))  # weak + decayed
    assert lead["fit_band"] == "core"
    assert lead["signal"] < LE._SIGNAL_HIGH
    assert lead["action"] == "nurture"


def test_low_fit_high_signal_investigates():
    lead = LE.score_lead(_pred(company="Obscure Co", account_tier="off_watchlist",
                               events=[_ev("funding", 1, url="ft.com"),
                                       _ev("job_ad_cluster", 1, url="indeed.com")]))
    assert lead["fit_band"] != "core"
    assert lead["signal"] >= LE._SIGNAL_HIGH
    assert lead["action"] == "investigate"


# ---- ANTI-TRIGGERS ----
def test_layoffs_suppress_signal():
    clean = LE.score_lead(_pred(events=[_ev("restructure", 1, evidence="reorganisation announced")]))
    cut = LE.score_lead(_pred(events=[_ev("restructure", 1, evidence="redundancies and job cuts announced")]))
    assert "layoffs" in cut["anti_triggers"]
    assert cut["signal"] < clean["signal"]


def test_administration_caps_to_monitor():
    lead = LE.score_lead(_pred(events=[_ev("crisis_event", 1, url="companieshouse.gov.uk",
                                           evidence="company enters administration")]))
    assert lead["action"] == "monitor"


def test_in_house_team_suppresses_and_demotes():
    """The false-positive case: funded + hiring, but they just built it in-house."""
    clean = LE.score_lead(_pred(events=[_ev("funding", 1, url="companieshouse.gov.uk"),
                                        _ev("job_ad_cluster", 1, url="ft.com")]))
    inhouse = LE.score_lead(_pred(events=[
        _ev("funding", 1, url="companieshouse.gov.uk",
            evidence="raise; grown a 25-person in-house comms team"),
        _ev("job_ad_cluster", 1, url="ft.com")]))
    assert "in_house_team" in inhouse["anti_triggers"]
    assert inhouse["signal"] < clean["signal"]
    assert clean["action"] == "call_today"
    assert inhouse["action"] != "call_today"


# ---- CORROBORATION as a gate ----
def test_single_source_cannot_call_today():
    same = LE.score_lead(_pred(events=[_ev("chro_change", 1, url="ft.com"),
                                       _ev("job_ad_cluster", 1, url="ft.com")]))
    assert same["corroborated"] is False
    assert same["action"] != "call_today"


def test_route_gate_blocks_uncorroborated_high_signal():
    assert LE._route(9, 8.0, False, corroborated=False) == "investigate"
    assert LE._route(9, 8.0, False, corroborated=True) == "call_today"


# ---- decay is behavioural, not just a formula (re-scored each render) ----
def test_aged_lead_slides_down():
    fresh = LE.score_lead(_pred(events=[_ev("chro_change", 2, url="companieshouse.gov.uk"),
                                        _ev("job_ad_cluster", 2, url="ft.com")]))
    aged = LE.score_lead(_pred(events=[_ev("chro_change", 200, url="companieshouse.gov.uk"),
                                       _ev("job_ad_cluster", 200, url="ft.com")]))
    assert fresh["action"] == "call_today"
    assert aged["action"] != "call_today"
    assert aged["signal"] < fresh["signal"]


# ---- dossier fields ----
def test_dossier_fields_present():
    lead = LE.score_lead(_pred(events=[_ev("ceo_change", 1)]))
    assert lead["who_to_call"]
    assert lead["fit_why"].startswith(("Core", "Adjacent", "Out"))


# ---- MARKETING desk port ----
def test_marketing_funding_outweighs_comms():
    ev = [_ev("funding", 1, url="ft.com")]
    comms = LE.score_lead(_pred(events=ev), desk="comms")
    mkt = LE.score_lead(_pred(events=ev), desk="marketing")
    assert mkt["signal"] > comms["signal"]   # funding = 6 for marketing vs 5 comms


def test_marketing_who_to_call_is_marketing_buyer():
    lead = LE.score_lead(_pred(events=[_ev("job_ad_cluster", 1)]), desk="marketing")
    assert "CMO" in lead["who_to_call"] or "Marketing" in lead["who_to_call"]


def test_marketing_anti_triggers_still_apply():
    inhouse = LE.score_lead(_pred(events=[
        _ev("funding", 1, url="companieshouse.gov.uk", evidence="grew a 30-person in-house marketing team"),
        _ev("job_ad_cluster", 1, url="ft.com")]), desk="marketing")
    assert "in_house_team" in inhouse["anti_triggers"]


def test_marketing_corroboration_gate_still_applies():
    same = LE.score_lead(_pred(events=[_ev("funding", 1, url="ft.com"),
                                       _ev("job_ad_cluster", 1, url="ft.com")]), desk="marketing")
    assert same["corroborated"] is False
    assert same["action"] != "call_today"


# ---- FUNDING kind ----
def test_funding_event_synthesises_demand_trigger():
    lead = LE.score_lead({"company": "Monzo", "amount": "£430m", "round": "Series I",
                          "evidence": "raise to fund expansion", "url": "ft.com",
                          "first_seen": _iso(2)}, kind="funding")
    assert lead["corroboration"] == 1
    assert lead["triggers"][0]["key"] == "funding"
    assert lead["signal"] > 0


# ---- ACCESS angle ----
def test_access_contact_known_when_seeded():
    item = _pred(events=[_ev("chro_change", 1)])
    item["seeded_contact_name"] = "Jane Doe"
    lead = LE.score_lead(item)
    assert lead["access"] == "contact_known"


def test_access_new_supplier_on_leadership():
    lead = LE.score_lead(_pred(events=[_ev("ceo_change", 1)]))
    assert lead["access"] == "new_supplier"
