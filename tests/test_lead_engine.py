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
# The "work it" layer — what an AD needs to CONVERT, not just open.
# ====================================================================

# ---- size of the prize -------------------------------------------------
def test_prize_cluster_is_multi_role_with_fee():
    lead = LE.score_lead(_pred(events=[_ev("chro_change", 1),
                                       _ev("job_ad_cluster", 1, url="ft.com")]))
    pz = lead["prize"]
    assert pz["roles"] >= 3                       # 1 senior + 2+ mid-level
    assert "mid-level" in pz["mix"]
    assert pz["fee"].startswith("£") and "k" in pz["fee"]
    assert pz["fee_high"] > pz["fee_low"] > 0
    assert "indicative" in pz["summary"].lower()


def test_prize_single_search_when_no_cluster():
    pz = LE.score_lead(_pred(events=[_ev("ceo_change", 1)]))["prize"]
    assert pz["roles"] == 1
    assert "single senior" in pz["mix"]


def test_prize_uses_desk_noun():
    comms = LE.score_lead(_pred(events=[_ev("job_ad_cluster", 1)]), desk="comms")["prize"]
    mkt = LE.score_lead(_pred(events=[_ev("job_ad_cluster", 1)]), desk="marketing")["prize"]
    assert "comms" in comms["mix"]
    assert "marketing" in mkt["mix"]


# ---- competitive context ----------------------------------------------
def test_competitive_open_and_psl_honestly_unknown():
    c = LE.score_lead(_pred(events=[_ev("ceo_change", 1)]))["competitive"]
    assert c["verdict"] == "open"
    assert "unknown" in c["psl"].lower()          # never asserts PSL it can't know
    assert "confirm" in c["summary"].lower()


def test_competitive_locked_on_incumbent_language():
    c = LE.score_lead(_pred(events=[
        _ev("chro_change", 1, evidence="the search is exclusively retained by a rival agency")
    ]))["competitive"]
    assert c["verdict"] == "locked"
    assert "incumbent" in c["incumbent"].lower()


def test_competitive_contested_on_in_house_team():
    c = LE.score_lead(_pred(events=[
        _ev("funding", 1, url="ft.com", evidence="built a 20-person in-house comms team")
    ]))["competitive"]
    assert c["verdict"] == "contested"
    assert "in-house" in c["internal_ta"].lower()


def test_competitive_reads_seeded_psl_flag():
    item = _pred(events=[_ev("ceo_change", 1)])
    item["psl_status"] = "on"
    c = LE.score_lead(item)["competitive"]
    assert "PSL" in c["psl"] and "on" in c["psl"].lower()


# ---- the proof (why VMA) ----------------------------------------------
def test_proof_prompts_a_comparable_placement_not_a_fabricated_one():
    p = LE.score_lead(_pred(events=[_ev("ceo_change", 1)]))["proof"]
    assert "comparable" in p["angle"].lower()     # prompts, never invents
    assert "comms" in p["angle"].lower()
    assert p["vs_incumbent"]


def test_proof_sharpens_against_an_incumbent():
    p = LE.score_lead(_pred(events=[
        _ev("chro_change", 1, evidence="exclusively retained by a rival agency")
    ]))["proof"]
    assert "off-limits" in p["vs_incumbent"].lower() or "passive" in p["vs_incumbent"].lower()


# ---- the objection it will hit ----------------------------------------
def test_objection_in_house_for_cold_leadership_lead():
    o = LE.score_lead(_pred(events=[_ev("ceo_change", 1)]))["objection"]
    assert "in-house" in o["likely"].lower()
    assert o["counter"]


def test_objection_handles_incumbent_first():
    o = LE.score_lead(_pred(events=[
        _ev("chro_change", 1, evidence="exclusively retained by a rival agency")
    ]))["objection"]
    assert "agency" in o["likely"].lower()


def test_objection_is_desk_aware():
    o = LE.score_lead(_pred(events=[_ev("ceo_change", 1)]), desk="marketing")["objection"]
    assert "marketing" in o["likely"].lower()


# ---- chase-by date -----------------------------------------------------
def test_chase_by_is_within_a_week_for_a_fast_signal():
    cb = LE.score_lead(_pred(events=[_ev("funding", 1, url="ft.com")]))["chase_by"]
    assert cb and 0 < cb["days"] <= 7
    assert cb["label"].startswith("Chase by")
    assert cb["date"]


def test_chase_by_runs_longer_for_a_leadership_signal():
    fast = LE.score_lead(_pred(events=[_ev("funding", 1, url="ft.com")]))["chase_by"]
    slow = LE.score_lead(_pred(events=[_ev("chro_change", 1)]))["chase_by"]
    assert slow["days"] > fast["days"]            # leadership edge holds longer


def test_chase_by_lapsed_signal_still_gets_a_near_term_date():
    cb = LE.score_lead(_pred(events=[_ev("crisis_event", 40, url="ft.com")]))["chase_by"]
    assert cb["days"] >= 1                         # never sits with no follow-up date


# ---- presence + no em dashes (house style) across both desks ----------
def test_work_it_fields_present_for_both_desks():
    for desk in ("comms", "marketing"):
        lead = LE.score_lead(_pred(events=[_ev("chro_change", 1),
                                           _ev("job_ad_cluster", 1, url="ft.com")]), desk=desk)
        for key in ("prize", "competitive", "proof", "objection", "chase_by"):
            assert lead[key], f"{key} missing for {desk}"


def test_work_it_copy_has_no_em_dashes():
    lead = LE.score_lead(_pred(events=[
        _ev("chro_change", 1, evidence="exclusively retained by a rival agency"),
        _ev("job_ad_cluster", 1, url="ft.com")]))
    blob = " ".join([
        lead["prize"]["summary"], lead["prize"]["basis"],
        lead["competitive"]["summary"],
        lead["proof"]["angle"], lead["proof"]["vs_incumbent"],
        lead["objection"]["likely"], lead["objection"]["counter"],
        lead["chase_by"]["rationale"],
    ])
    assert "—" not in blob and "–" not in blob
