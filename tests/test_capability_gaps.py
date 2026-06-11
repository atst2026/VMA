"""Tests for the capability-gap closers:

  1. cmo_change trigger (new CMO appointments / departures, with the
     Chief Medical / Manufacturing Officer collision guarded out).
  2. market_entry trigger (UK / European launches, first local office).
  3. Agency-relationship ledger (tool/agency_relationships.py) — actual
     relationship history from detected account moves, not job-ad-age
     inference.
  4. Living team maps (tool/team_map.py) — current roster + observed
     joiners/leavers from the leadership-page fetches.
"""
from datetime import datetime, timezone, timedelta

from tool.predictive import patterns as P
from tool.predictive.detector import detect_events


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ====================================================================
# 1. cmo_change
# ====================================================================
def test_cmo_change_fires_on_appointment_and_departure():
    cases = [
        "Burberry appoints Jane Smith as chief marketing officer",
        "Tesco names new CMO to lead brand revival",
        "Sainsbury's marketing director steps down after five years",
        "Diageo's chief marketing officer departs",
        "Emma Reed joins Aviva as head of marketing",
        "Departure of group marketing director announced",
    ]
    for text in cases:
        keys = {t.key for t in P.match_triggers(text)}
        assert "cmo_change" in keys, f"{text!r} did not fire cmo_change ({keys})"


def test_cmo_change_does_not_fire_on_spelled_out_medical_cmo():
    # "chief medical officer" never matches the marketing title alternation.
    keys = {t.key for t in P.match_triggers(
        "AstraZeneca appoints new chief medical officer")}
    assert "cmo_change" not in keys


def test_detector_drops_bare_cmo_in_medical_context():
    # Bare "CMO" hits the pattern, but the clinical context guard kills it.
    sig = {"id": "cg1", "source": "GDELT", "kind": "news",
           "title": "AstraZeneca appoints new CMO to lead clinical strategy",
           "summary": "The chief medical officer will oversee trials.",
           "url": "https://example.com/az", "published": _iso(1)}
    events = detect_events([sig])
    assert "cmo_change" not in {e.trigger_key for e in events}


def test_detector_emits_cmo_change_for_watchlist_company():
    sig = {"id": "cg2", "source": "Campaign", "kind": "news",
           "title": "Tesco appoints Sarah Lee as chief marketing officer",
           "summary": "", "url": "https://www.campaignlive.co.uk/x",
           "published": _iso(1)}
    events = detect_events([sig])
    assert "cmo_change" in {e.trigger_key for e in events}


# ====================================================================
# 2. market_entry
# ====================================================================
def test_market_entry_fires_on_uk_launch_language():
    cases = [
        "Klarna enters the UK market with a London hub",
        "US fintech opens its first UK office",
        "Shein plans UK launch ahead of Christmas",
        "Stripe establishes a European headquarters in Dublin",
        "Temu expands into the UK",
    ]
    for text in cases:
        keys = {t.key for t in P.match_triggers(text)}
        assert "market_entry" in keys, f"{text!r} did not fire market_entry ({keys})"


def test_market_entry_does_not_overfire():
    negatives = [
        "Tesco reports market share gains in groceries",
        "The company enters the market for corporate bonds",
        "UK launches new trade policy consultation",
        "Barclays opens new branch in Leeds",
    ]
    for text in negatives:
        keys = {t.key for t in P.match_triggers(text)}
        assert "market_entry" not in keys, f"{text!r} over-fired market_entry"


# ====================================================================
# Registration: both keys wired through every trigger-key registry.
# ====================================================================
def test_new_keys_registered_everywhere():
    from tool import lead_engine as LE
    from tool import gate as G
    from tool import why_now as W
    from tool import predictor_pipeline as PP

    for k in ("cmo_change", "market_entry"):
        assert k in P.BY_KEY, f"{k} missing from BY_KEY"
        t = P.BY_KEY[k]
        assert t.weight > 0 and t.lead_time_weeks[0] <= t.lead_time_weeks[1]
        assert k in LE._COMMS_TAXONOMY and k in LE._MKT_TAXONOMY
        assert k in G.SEAT_IMMINENT_KEYS
        assert k in G._KILL
        assert W.fee_driver([k])[0] != "Live signal", f"{k} has no fee class"
        for desk in ("comms", "marketing"):
            seat = PP.role_for_trigger_keys([k], desk=desk)
            assert seat not in ("Senior Comms hire", "Senior Marketing hire"), \
                f"{k} resolves only the default seat on {desk}"


def test_new_keys_score_through_lead_engine_both_desks():
    from tool import lead_engine as LE
    for desk in ("comms", "marketing"):
        for k in ("cmo_change", "market_entry"):
            lead = LE.score_lead(
                {"company": "Tesco", "account_tier": "watchlist",
                 "last_seen": _iso(1),
                 "events": [{"trigger_key": k, "trigger_label": k,
                             "url": "ft.com", "source": "ft.com",
                             "tier": "covered", "published": _iso(20),
                             "evidence": ""}]},
                desk=desk)
            assert lead["signal"] > 0, f"{k} scored 0 on {desk}"
            assert lead["who_to_call"], f"{k} has no buyer on {desk}"


# ====================================================================
# 3. Agency-relationship ledger
# ====================================================================
def _setup_ar(tmp_path, monkeypatch):
    import tool.agency_relationships as AR
    monkeypatch.setattr(AR, "_path", lambda: tmp_path / "agency_relationships.json")
    return AR


def _move(company="Tesco", evidence="Tesco appoints Ogilvy as its creative agency",
          rid="m1", url="https://www.campaignlive.co.uk/x", days_ago=2):
    return {"trigger_key": "agency_account_move", "company": company,
            "evidence": evidence, "raw_signal_id": rid, "url": url,
            "source": "Campaign", "published": _iso(days_ago)}


def test_ledger_records_and_reads_history(tmp_path, monkeypatch):
    AR = _setup_ar(tmp_path, monkeypatch)
    assert AR.record_moves([_move()]) == 1
    hist = AR.history("Tesco")
    assert len(hist) == 1
    h = hist[0]
    assert h["agency"] == "Ogilvy"
    assert h["discipline"] == "creative"
    assert h["direction"] == "appointed"
    last = AR.last_relationship("Tesco")
    assert last and last["agency"] == "Ogilvy"
    lines = AR.summary_lines("Tesco")
    assert any("Ogilvy" in ln for ln in lines)


def test_ledger_dedupes_and_ignores_other_triggers(tmp_path, monkeypatch):
    AR = _setup_ar(tmp_path, monkeypatch)
    AR.record_moves([_move()])
    # Same signal id again + a non-move event: nothing new recorded.
    assert AR.record_moves([_move(), {"trigger_key": "ceo_change",
                                      "company": "Tesco", "evidence": "x",
                                      "raw_signal_id": "c1",
                                      "published": _iso(1)}]) == 0
    assert len(AR.history("Tesco")) == 1


def test_ledger_classifies_a_loss_and_captures_unlisted_agency(tmp_path, monkeypatch):
    AR = _setup_ar(tmp_path, monkeypatch)
    AR.record_moves([
        _move(evidence="Aviva loses its PR account as it parts ways with Edelman",
              company="Aviva", rid="m2"),
        _move(evidence="Greggs appoints Fox Hare as its media agency",
              company="Greggs", rid="m3"),
    ])
    assert AR.history("Aviva")[0]["direction"] == "ended"
    assert AR.history("Aviva")[0]["agency"] == "Edelman"
    assert AR.history("Greggs")[0]["agency"] == "Fox Hare"


def test_ledger_accepts_trigger_event_objects(tmp_path, monkeypatch):
    from tool.predictive.detector import TriggerEvent
    AR = _setup_ar(tmp_path, monkeypatch)
    ev = TriggerEvent(
        trigger_key="agency_account_move", trigger_label="Agency account move",
        company="Boots", evidence="Boots hands its advertising account to VCCP",
        url="https://www.prweek.com/y", source_label="PRWeek UK",
        published=datetime.now(timezone.utc), raw_signal_id="m4")
    assert AR.record_moves([ev]) == 1
    assert AR.history("Boots")[0]["agency"] == "VCCP"


# ====================================================================
# 4. Living team maps
# ====================================================================
def _setup_tm(tmp_path, monkeypatch):
    import tool.team_map as TM
    monkeypatch.setattr(TM, "_path", lambda: tmp_path / "team_maps.json")
    return TM


_ROSTER_V1 = {"Sarah Mitchell": "Chief Communications Officer",
              "James Okoro": "Chief Marketing Officer"}
_ROSTER_V2 = {"James Okoro": "Chief Marketing Officer",
              "Aisha Khan": "Director Of Communications"}


def test_team_map_seeds_then_tracks_changes(tmp_path, monkeypatch):
    TM = _setup_tm(tmp_path, monkeypatch)
    # First observation seeds the roster without recording joins.
    out = TM.update_roster("BT Group", "https://example.com/leaders", _ROSTER_V1)
    assert out == {"joined": [], "left": []}
    assert set(TM.team("BT Group")) == set(_ROSTER_V1)
    # Second observation: Sarah left, Aisha joined.
    out = TM.update_roster("BT Group", "https://example.com/leaders", _ROSTER_V2)
    assert out["left"] == ["Sarah Mitchell"]
    assert out["joined"] == ["Aisha Khan"]
    team = TM.team("BT Group")
    assert set(team) == set(_ROSTER_V2)
    assert team["Aisha Khan"]["role"] == "Director Of Communications"
    ch = TM.changes("BT Group")
    assert {(c["person"], c["change"]) for c in ch} == {
        ("Sarah Mitchell", "left"), ("Aisha Khan", "joined")}
    lines = TM.summary_lines("BT Group")
    assert any("James Okoro" in ln for ln in lines)
    assert any("Recent changes" in ln for ln in lines)


def test_team_map_empty_parse_never_fabricates_a_mass_exit(tmp_path, monkeypatch):
    TM = _setup_tm(tmp_path, monkeypatch)
    TM.update_roster("BT Group", "https://example.com/leaders", _ROSTER_V1)
    out = TM.update_roster("BT Group", "https://example.com/leaders", {})
    assert out == {"joined": [], "left": []}
    assert set(TM.team("BT Group")) == set(_ROSTER_V1)


def test_wayback_roster_extracts_names_with_roles():
    from tool.sources import wayback as wb
    page = """
    <html><body>
    <div class="leader"><h3>Sarah Mitchell</h3><p>Chief Communications Officer</p></div>
    <div class="leader"><h3>James Okoro</h3><p>Chief Marketing Officer</p></div>
    <div class="leader"><h3>Priya Patel</h3><p>Chief Financial Officer</p></div>
    </body></html>
    """
    roster = wb.roster_with_roles(page)
    assert roster.get("Sarah Mitchell") == "Chief Communications Officer"
    assert roster.get("James Okoro") == "Chief Marketing Officer"
    assert "Priya Patel" not in roster   # CFO is not a comms/marketing seat


def test_wayback_diff_updates_team_map(tmp_path, monkeypatch):
    from tool.sources import wayback as wb
    TM = _setup_tm(tmp_path, monkeypatch)
    old_page = ("<html><body><h3>Sarah Mitchell</h3>"
                "<p>Chief Communications Officer</p>"
                "<h3>James Okoro</h3><p>Chief Marketing Officer</p></body></html>")
    new_page = ("<html><body><h3>James Okoro</h3>"
                "<p>Chief Marketing Officer</p></body></html>")
    monkeypatch.setattr(wb, "_fetch",
                        lambda url: old_page if "web.archive.org" in url else new_page)
    monkeypatch.setattr(wb, "_cdx_nearest", lambda url, days: "20260401000000")
    evs = wb.diff_company("BT Group", "https://example.com/leaders")
    assert any(e.trigger_key == "comms_leader_departure" for e in evs)
    # The live roster was folded into the team map as a side effect.
    assert set(TM.team("BT Group")) == {"James Okoro"}
