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


def test_assess_gate_blocks_uncorroborated_active_lead():
    # An active hiring cluster only reaches Call today once corroborated.
    base = dict(fit_band="core", demand_now=True, n_dim=1, cap=False, conflict=False,
                contradiction=False, too_fresh=False, quality_trigger=True, stack_req=3)
    assert LE._assess(**{**base, "corroborated": False})[1] != "call_today"
    assert LE._assess(**{**base, "corroborated": True}) == ("strong", "call_today")


# ---- decay is behavioural, not just a formula (re-scored each render) ----
def test_aged_lead_slides_down():
    fresh = LE.score_lead(_pred(events=[_ev("chro_change", 2, url="companieshouse.gov.uk"),
                                        _ev("job_ad_cluster", 2, url="ft.com")]))
    aged = LE.score_lead(_pred(events=[_ev("chro_change", 200, url="companieshouse.gov.uk"),
                                       _ev("job_ad_cluster", 200, url="ft.com")]))
    assert fresh["action"] == "call_today"
    assert aged["action"] != "call_today"
    assert aged["signal"] < fresh["signal"]


# ---- confidence calibration: credible / event-grade signals aren't crushed ----
def test_funding_from_credible_outlet_is_not_single_source():
    lead = LE.score_lead({"company": "NewCo", "amount": "£260m", "round": "Series C",
                          "url": "https://www.ft.com/x", "source": "FT",
                          "first_seen": _iso(1)}, "funding", "comms")
    assert lead["triggers"][0]["confidence"] in ("verified", "corroborated")
    assert lead["signal"] >= 3.0           # medium, not the old 1.5
    assert lead["signal_band"] in ("medium", "high")


def test_event_grade_funding_corroborated_even_obscure_source():
    lead = LE.score_lead({"company": "NewCo", "amount": "£260m", "round": "Series C",
                          "url": "https://obscureblog.example/x", "source": "blog",
                          "first_seen": _iso(1)}, "funding", "comms")
    assert lead["triggers"][0]["confidence"] == "corroborated"
    assert lead["signal"] >= 3.0


# ---- competing-recruiter conflict ----
def test_competing_recruiter_flagged_and_parked():
    for name in ("Gill Cooke Personnel", "The Recruitment Group",
                 "Government Recruitment Service", "Hays Staffing"):
        assert LE._is_recruiter(name), name
    assert not LE._is_recruiter("Tesco")
    assert not LE._is_recruiter("Department for Work and Pensions")
    lead = LE.score_lead(_pred(company="Gill Cooke Personnel",
                               events=[_ev("job_ad_cluster", 1, url="ft.com"),
                                       _ev("chro_change", 1, url="companieshouse.gov.uk")]))
    assert lead["conflict"] is True
    assert lead["fit_band"] == "out"
    assert "competing recruiter" in lead["fit_why"]
    assert lead["action"] == "monitor"          # capped, never above Monitor
    assert "competing_recruiter" in lead["anti_triggers"]


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


# ---- ACCESS: warm / cold relationship + who-to-call ----
def test_access_warm_when_contact_on_file():
    item = _pred(events=[_ev("chro_change", 1)])
    item["seeded_contact_name"] = "Jane Doe"
    item["seeded_contact_role"] = "Group HRD"
    lead = LE.score_lead(item)
    assert lead["relationship"] == "warm"
    assert lead["access"] == "warm"
    assert "contact on file" in lead["access_text"]
    # who-to-call resolves to the NAME, not just the role
    assert lead["who_to_call"].startswith("Jane Doe")


def test_access_cold_when_no_relationship():
    lead = LE.score_lead(_pred(events=[_ev("ceo_change", 1)]))
    assert lead["relationship"] == "cold"
    assert lead["access"] == "cold"
    assert "new leader has just landed" in lead["access_text"]


def test_warm_via_contact_on_file_flag():
    item = _pred(events=[_ev("ceo_change", 1)])
    item["contact_on_file"] = True
    assert LE.score_lead(item)["relationship"] == "warm"


def test_scale_build_out_on_cluster():
    lead = LE.score_lead(_pred(events=[_ev("chro_change", 1), _ev("job_ad_cluster", 1, url="ft.com")]))
    assert "build-out" in lead["scale"]
    single = LE.score_lead(_pred(events=[_ev("ceo_change", 1)]))
    assert single["scale"] == "single senior search"


def test_chro_buyer_is_comms_owner_not_just_chro():
    lead = LE.score_lead(_pred(events=[_ev("chro_change", 1)]))
    assert "CCO" in lead["who_to_call"]  # comms mandate owner, CHRO is the door


# ====================================================================
# The CONJUNCTION model (v3) — strength is a stack, not a single trigger.
# ====================================================================

# ---- active hiring NOW is the strongest pre-contact signal ----
def test_active_corroborated_cluster_is_strong_call_today():
    lead = LE.score_lead(_pred(events=[
        _ev("ceo_change", 4, url="companieshouse.gov.uk"),
        _ev("job_ad_cluster", 5, url="ft.com")]))
    assert lead["strength"] == "strong"
    assert lead["action"] == "call_today"
    assert lead["stack"]                       # the narrative stack is populated


# ---- a lone, fresh anticipatory trigger is PREMATURE (8-12 week hold) ----
def test_lone_fresh_leadership_is_premature_not_call_today():
    lead = LE.score_lead(_pred(events=[_ev("ceo_change", 4, url="companieshouse.gov.uk")]))
    assert lead["premature"] is True
    assert lead["strength"] == "premature"
    assert lead["action"] != "call_today"
    assert "8 to 12 weeks" in lead["why_now"]


def test_lone_fresh_funding_is_premature():
    lead = LE.score_lead({"company": "NewCo", "amount": "£260m", "round": "Series C",
                          "url": "https://www.ft.com/x", "first_seen": _iso(4)}, "funding")
    assert lead["premature"] is True
    assert lead["action"] != "call_today"


# ---- a MATURED anticipatory stack reaches strong ----
def test_matured_leadership_plus_funding_stacks_to_strong():
    lead = LE.score_lead(_pred(events=[
        _ev("ceo_change", 30, url="companieshouse.gov.uk"),
        _ev("funding", 28, url="ft.com")]))
    assert lead["premature"] is False
    assert lead["n_pro"] >= 3
    assert lead["strength"] == "strong"
    assert lead["action"] == "call_today"


# ---- negative scoring: contradictions force a WATCH ----
def test_funding_and_layoffs_conflict_is_a_watch():
    lead = LE.score_lead(_pred(events=[
        _ev("funding", 3, url="ft.com", evidence="£40m raise"),
        _ev("job_ad_cluster", 3, url="indeed.com", evidence="redundancies announced")]))
    assert lead["financial"]["direction"] == "conflicting"
    assert lead["strength"] == "watch"
    assert lead["action"] == "monitor"
    assert any("cuts" in c for c in lead["contradictions"])


def test_in_house_posture_blocks_an_otherwise_active_lead():
    lead = LE.score_lead(_pred(events=[
        _ev("funding", 3, url="companieshouse.gov.uk", evidence="£40m raise"),
        _ev("job_ad_cluster", 3, url="ft.com", evidence="grown a 25-person in-house comms team")]))
    assert lead["posture"]["direction"] == "internal"
    assert lead["action"] != "call_today"


# ---- decayed signals drop out of the stack ----
def test_decayed_signals_do_not_count_toward_the_stack():
    aged = LE.score_lead(_pred(events=[
        _ev("ceo_change", 200, url="companieshouse.gov.uk"),
        _ev("job_ad_cluster", 200, url="ft.com")]))
    assert aged["n_pro"] == 0
    assert aged["action"] != "call_today"


# ---- financial direction detection (the stem-regex bug fix) ----
def test_financial_direction_reads_cuts_and_growth():
    cut = LE._financial_direction(
        [{"evidence": "redundancies and a cost-cutting programme", "trigger_label": ""}], [])
    assert cut["has_anti"] and cut["direction"] == "anti"
    grow = LE._financial_direction(
        [{"evidence": "£40m raise to fund expansion", "trigger_label": ""}], [])
    assert grow["has_pro"] and grow["direction"] == "pro"


# ---- posture detection ----
def test_posture_internal_on_talent_acquisition_language():
    p = LE._posture({}, [{"key": "job_ad_cluster",
                          "label": "hiring a Head of Talent Acquisition", "evidence": ""}], [])
    assert p["direction"] == "internal"


def test_posture_external_on_active_cluster():
    p = LE._posture({}, [{"key": "job_ad_cluster", "label": "3 comms roles", "evidence": "", "age_days": 5}], [])
    assert p["direction"] == "external"


def test_posture_seeded_psl_off_is_internal():
    p = LE._posture({"psl_status": "closed"}, [{"key": "job_ad_cluster", "label": "", "evidence": ""}], [])
    assert p["direction"] == "internal"


# ---- market state raises the bar in a contraction ----
def test_market_state_is_contracting_now():
    ms = LE._market_state()
    assert ms["state"] == "contracting"
    assert ms["stack_req"] == 3              # lone triggers must stack harder


# ---- the why-now narrative articulates the conjunction, both desks ----
def test_why_now_names_the_stack_for_a_strong_lead():
    for desk in ("comms", "marketing"):
        lead = LE.score_lead(_pred(events=[
            _ev("ceo_change", 4, url="companieshouse.gov.uk"),
            _ev("job_ad_cluster", 5, url="ft.com")]), desk=desk)
        wn = lead["why_now"]
        assert wn and "—" not in wn and "–" not in wn       # house style
        assert "stacks" in wn or "team build" in wn


def test_assess_pure_router_branches():
    base = dict(fit_band="core", demand_now=False, n_dim=0, corroborated=True,
                cap=False, conflict=False, contradiction=False, too_fresh=False,
                quality_trigger=True, stack_req=3)
    assert LE._assess(**{**base, "cap": True})[0] == "parked"
    assert LE._assess(**{**base, "contradiction": True})[0] == "watch"
    assert LE._assess(**{**base, "demand_now": True}) == ("strong", "call_today")
    assert LE._assess(**{**base, "too_fresh": True})[0] == "premature"
    assert LE._assess(**{**base, "n_dim": 2}) == ("strong", "call_today")  # matured stack
    assert LE._assess(**base)[0] == "promising"                            # lone core trigger
