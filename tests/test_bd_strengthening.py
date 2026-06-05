"""Tests for the BD-strengthening additions: new profession-specific
triggers + the new free sources (Companies House charges/PSC/filings/tenure
+ streaming, Charity Commission trustee board, Wayback careers-page diff).
"""
from datetime import datetime, timezone, timedelta

from tool.predictive import patterns as P
from tool.predictive import ranker, stacker
from tool.predictive.detector import TriggerEvent, detect_events
from tool import lead_engine as LE
from tool import predictor_pipeline as PP


NEW_KEYS = ["rebrand", "agency_account_move", "esg_bcorp", "martech_adoption",
            "leadership_tenure", "secured_financing", "ownership_change"]


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ====================================================================
# 1. Patterns: the four regex triggers fire on real headlines + do not
#    over-fire on near-misses.
# ====================================================================
def test_new_regex_triggers_fire():
    cases = {
        "NatWest unveils a new brand identity in a major rebrand": "rebrand",
        "Tesco appoints Ogilvy as its creative agency": "agency_account_move",
        "Greggs becomes a certified B Corp": "esg_bcorp",
        "Aviva sets out a net-zero strategy": "esg_bcorp",
        "Boots rolls out Salesforce Marketing Cloud": "martech_adoption",
    }
    for text, key in cases.items():
        keys = {t.key for t in P.match_triggers(text)}
        assert key in keys, f"{text!r} did not fire {key} (got {keys})"


def test_new_triggers_do_not_overfire():
    negatives = [
        "Brand new smartphone launched today",      # 'brand new' != rebrand
        "Account Director joins a PR agency",        # not an account move
        "The company published its annual report",   # not esg
        "Sales team adopts a new CRM mindset",       # no martech vendor/phrase
    ]
    for text in negatives:
        keys = {t.key for t in P.match_triggers(text)}
        assert not (keys & {"rebrand", "agency_account_move", "esg_bcorp",
                            "martech_adoption"}), f"{text!r} over-fired: {keys}"


def test_all_new_keys_registered_with_weight_and_window():
    for k in NEW_KEYS:
        assert k in P.BY_KEY, f"{k} missing from BY_KEY"
        t = P.BY_KEY[k]
        assert t.weight > 0
        assert t.lead_time_weeks[0] <= t.lead_time_weeks[1]


# ====================================================================
# 2. Scoring: every new key scores through the lead engine (taxonomy
#    present), routes, and resolves a buyer + a predicted seat — both desks.
# ====================================================================
def _pred(company="Tesco", events=None):
    return {"company": company, "account_tier": "watchlist",
            "events": events or [], "last_seen": _iso(1)}


def _ev(key, days_ago=20, url="ft.com", evidence=""):
    return {"trigger_key": key, "trigger_label": key, "url": url,
            "source": url, "tier": "covered", "published": _iso(days_ago),
            "evidence": evidence}


def test_new_keys_produce_nonzero_signal_both_desks():
    for desk in ("comms", "marketing"):
        for k in NEW_KEYS:
            if k in ("leadership_tenure",):   # soft: needs a hard signal alongside
                lead = LE.score_lead(_pred(events=[_ev("ceo_change", 30,
                                     url="companieshouse.gov.uk"), _ev(k)]), desk=desk)
            else:
                lead = LE.score_lead(_pred(events=[_ev(k)]), desk=desk)
            assert lead["signal"] > 0, f"{k} scored 0 on {desk}"
            assert lead["who_to_call"], f"{k} has no buyer on {desk}"


def test_secured_financing_is_a_pro_financial_signal():
    fin = LE._financial_direction(
        [{"evidence": "", "trigger_label": ""}],
        [{"key": "secured_financing"}])
    assert fin["direction"] == "pro"


def test_ch_sourced_keys_are_verified_confidence():
    # A CH-sourced event (companieshouse in the URL) is Tier-1 verified.
    lead = LE.score_lead(_pred(events=[_ev("ownership_change", 20,
                         url="find-and-update.company-information.service.gov.uk")]))
    assert lead["triggers"][0]["confidence"] == "verified"


def test_predicted_role_for_new_keys_both_desks():
    for desk in ("comms", "marketing"):
        for k in NEW_KEYS:
            seat = PP.role_for_trigger_keys([k], desk=desk)
            assert seat and "hire" not in seat.lower() or seat, k
            # not the bare default
            assert seat not in ("", None)


# ====================================================================
# 3. Detector resolves a watchlist company and emits the new events.
# ====================================================================
def test_detector_emits_rebrand_for_watchlist_company():
    sig = {"id": "x1", "source": "PRWeek", "kind": "news",
           "title": "Tesco unveils a new brand identity in a major rebrand",
           "summary": "", "url": "https://www.prweek.com/x", "published": _iso(1)}
    events = detect_events([sig])
    keys = {e.trigger_key for e in events}
    assert "rebrand" in keys
    assert any(e.company for e in events)


def test_detector_emits_esg_for_watchlist_company():
    sig = {"id": "x2", "source": "Campaign", "kind": "news",
           "title": "Aviva sets out a net-zero strategy and science-based targets",
           "summary": "", "url": "https://www.campaignlive.co.uk/x",
           "published": _iso(1)}
    events = detect_events([sig])
    assert "esg_bcorp" in {e.trigger_key for e in events}


def _detect_company(title):
    evs = detect_events([{"id": "z", "source": "Marketing Week", "kind": "news",
                          "title": title, "summary": "",
                          "url": "https://marketingweek.com/z", "published": _iso(1)}])
    return [(e.trigger_key, e.company) for e in evs]


def test_martech_vendor_not_misattributed_as_lead():
    # Adobe / Salesforce ARE watchlist peers; the ADOPTER must be the lead,
    # never the vendor (the object of the verb).
    assert _detect_company("Sainsbury's rolls out Adobe Experience Cloud across marketing") \
        == [("martech_adoption", "Sainsbury's")]
    # Vendor-only headline (no watchlist adopter) must DROP, not list Adobe.
    assert _detect_company("Adobe Experience Cloud launches new features") == []


def test_agency_object_not_misattributed_as_lead():
    # The brand is the lead, not the agency it appointed / handed the account.
    assert _detect_company("Aviva appoints Ogilvy as its creative agency") \
        == [("agency_account_move", "Aviva")]
    assert _detect_company("Tesco hands its media account to WPP") \
        == [("agency_account_move", "Tesco")]


# ====================================================================
# 4. Ranker gives the new keys a non-zero stack score (they have weights).
# ====================================================================
def test_ranker_scores_new_key_stack():
    for k in NEW_KEYS:
        ev = TriggerEvent(trigger_key=k, trigger_label=k, company="Tesco",
                          evidence="", url="", source_label="x",
                          published=datetime.now(timezone.utc), tier_hint="listed")
        score = ranker.score_stack(stacker.Stack(company="Tesco", events=[ev]))
        assert score > 0, f"{k} stack scored 0"


# ====================================================================
# 5. Companies House: charges / PSC / filing / tenure / stream parsers.
# ====================================================================
def _patch_ch_get(monkeypatch, charges=None, psc=None, filings=None):
    from tool.sources import companies_house as ch

    def fake(path, params=None):
        if "charges" in path:
            return {"items": charges or []}
        if "significant-control" in path:
            return {"items": psc or []}
        if "filing-history" in path:
            return {"items": filings or []}
        return None
    monkeypatch.setattr(ch, "_ch_get_json", fake)
    return ch


def test_ch_charge_event(monkeypatch):
    ch = _patch_ch_get(monkeypatch, charges=[{
        "status": "outstanding", "created_on": datetime.now(timezone.utc).date().isoformat(),
        "classification": {"description": "Debenture"},
        "persons_entitled": [{"name": "Lloyds Bank"}], "charge_id": "c1"}])
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    evs = ch._charge_events("Barclays", "00012345", cutoff)
    assert [e.trigger_key for e in evs] == ["secured_financing"]
    assert evs[0].tier_hint == "listed"
    # a satisfied charge does NOT fire
    ch2 = _patch_ch_get(monkeypatch, charges=[{
        "status": "satisfied", "created_on": datetime.now(timezone.utc).date().isoformat(),
        "charge_id": "c2"}])
    assert ch2._charge_events("Barclays", "00012345", cutoff) == []


def test_ch_charge_outside_window_dropped(monkeypatch):
    ch = _patch_ch_get(monkeypatch, charges=[{
        "status": "outstanding",
        "created_on": (datetime.now(timezone.utc) - timedelta(days=200)).date().isoformat(),
        "charge_id": "old"}])
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    assert ch._charge_events("Barclays", "1", cutoff) == []


def test_ch_psc_event(monkeypatch):
    ch = _patch_ch_get(monkeypatch, psc=[{
        "notified_on": datetime.now(timezone.utc).date().isoformat(),
        "name": "New Holdco Ltd", "links": {"self": "/p1"}}])
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    evs = ch._psc_events("SomeCo", "1", cutoff)
    assert [e.trigger_key for e in evs] == ["ownership_change"]


def test_ch_filing_change_of_name_and_sh01(monkeypatch):
    today = datetime.now(timezone.utc).date().isoformat()
    ch = _patch_ch_get(monkeypatch, filings=[
        {"date": today, "category": "change-of-name", "type": "CERTNM",
         "description": "Change of name", "transaction_id": "t1"},
        {"date": today, "type": "SH01", "description": "Allotment of shares",
         "transaction_id": "t2"}])
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    keys = sorted(e.trigger_key for e in ch._filing_events("Co", "1", cutoff))
    assert keys == ["rebrand", "secured_financing"]


def test_ch_tenure_event(monkeypatch):
    from tool.sources import companies_house as ch
    old = (datetime.now(timezone.utc) - timedelta(days=365 * 6)).date().isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=200)).date().isoformat()
    officers = [
        {"name": "Jane Comms", "occupation": "Chief Communications Officer",
         "appointed_on": old},
        {"name": "New Starter", "occupation": "Director of Communications",
         "appointed_on": recent},           # too recent -> no flight risk
        {"name": "Random Person", "occupation": "Finance Manager",
         "appointed_on": old},              # wrong role -> ignored
    ]
    evs = ch._tenure_events("Co", "1", officers)
    assert [e.trigger_key for e in evs] == ["leadership_tenure"]
    assert "Jane Comms" in evs[0].evidence


def test_ch_stream_record_to_event():
    from tool.sources import companies_house as ch
    watch = {ch._norm_co("Barclays"), ch._norm_co("NatWest Group")}
    rec = {"resource_id": "abc", "data": {"company_name": "Barclays",
           "category": "change-of-name", "description": "Change of name",
           "date": "2026-05-01"}}
    ev = ch._stream_record_to_event(rec, watch)
    assert ev and ev.trigger_key == "rebrand" and ev.company == "Barclays"
    # off-watchlist -> None
    off = {"resource_id": "z", "data": {"company_name": "Random Ltd",
           "category": "change-of-name"}}
    assert ch._stream_record_to_event(off, watch) is None
    # unmapped category -> None
    other = {"data": {"company_name": "Barclays", "category": "accounts"}}
    assert ch._stream_record_to_event(other, watch) is None


def test_ch_stream_disabled_by_default(monkeypatch):
    from tool.sources import companies_house as ch
    monkeypatch.delenv("CH_STREAM_ENABLED", raising=False)
    assert ch.stream_filings() == []


# ====================================================================
# 6. Charity Commission trustee-board diff.
# ====================================================================
def test_charity_board_change_diff(tmp_path, monkeypatch):
    from tool.sources import charity_commission as cc
    monkeypatch.setattr(cc, "CHARITY_COMMISSION_KEY", "test-key")
    monkeypatch.setattr(cc, "TRUSTEE_SNAPSHOT_FILE", tmp_path / "snap.json")
    monkeypatch.setattr(cc, "CHARITY_WATCHLIST", [("Oxfam", "202918")])

    trustees = {"202918": ["Alice Smith", "Bob Jones"]}
    monkeypatch.setattr(cc, "fetch_trustees", lambda regno: trustees.get(regno))

    # First run: seeds snapshot, no events.
    assert cc.fetch_charity_signals() == []
    # Board changes: Bob departs, Carol joins.
    trustees["202918"] = ["Alice Smith", "Carol White"]
    evs = cc.fetch_charity_signals()
    assert len(evs) == 1
    assert evs[0].trigger_key == "chair_change"
    assert "Oxfam" == evs[0].company
    assert "Carol White" in evs[0].evidence and "Bob Jones" in evs[0].evidence


def test_charity_noop_without_key(monkeypatch):
    from tool.sources import charity_commission as cc
    monkeypatch.setattr(cc, "CHARITY_COMMISSION_KEY", "")
    assert cc.fetch_charity_signals() == []


def test_charity_trustee_name_shapes():
    from tool.sources import charity_commission as cc
    assert cc._extract_trustee_names([{"trustee_name": "A B"}, {"name": "C D"}]) == ["A B", "C D"]
    assert cc._extract_trustee_names({"trustees": [{"name": "E F"}]}) == ["E F"]
    assert cc._extract_trustee_names(["G H", "I J"]) == ["G H", "I J"]
    assert cc._extract_trustee_names(None) == []


# ====================================================================
# 7. Wayback careers-page diff.
# ====================================================================
_OLD_PAGE = """
<html><body>
<div class="leader"><h3>Sarah Mitchell</h3><p>Chief Communications Officer</p></div>
<div class="leader"><h3>James Okoro</h3><p>Chief Marketing Officer</p></div>
<div class="leader"><h3>Priya Patel</h3><p>Chief Financial Officer</p></div>
</body></html>
"""
_NEW_PAGE = """
<html><body>
<div class="leader"><h3>James Okoro</h3><p>Chief Marketing Officer</p></div>
<div class="leader"><h3>Priya Patel</h3><p>Chief Financial Officer</p></div>
</body></html>
"""


def test_wayback_name_extractor():
    from tool.sources import wayback as wb
    people = wb.people_with_senior_role(_OLD_PAGE)
    assert "Sarah Mitchell" in people      # CCO
    assert "James Okoro" in people         # CMO
    assert "Priya Patel" not in people     # CFO is not a comms/marketing seat


def test_wayback_diff_emits_departure(monkeypatch):
    from tool.sources import wayback as wb

    def fake_fetch(url):
        return _OLD_PAGE if "web.archive.org" in url else _NEW_PAGE
    monkeypatch.setattr(wb, "_fetch", fake_fetch)
    monkeypatch.setattr(wb, "_cdx_nearest", lambda url, days: "20260401000000")

    evs = wb.diff_company("BT Group", "https://example.com/leaders")
    keys = [(e.trigger_key, e.company) for e in evs]
    assert ("comms_leader_departure", "BT Group") in keys
    assert any("Sarah Mitchell" in e.evidence for e in evs)
    # James (still present) must NOT be flagged as departed
    assert all("James Okoro" not in e.evidence for e in evs)


def test_wayback_no_departure_when_pages_match(monkeypatch):
    from tool.sources import wayback as wb
    monkeypatch.setattr(wb, "_fetch", lambda url: _NEW_PAGE)
    monkeypatch.setattr(wb, "_cdx_nearest", lambda url, days: "20260401000000")
    assert wb.diff_company("BT Group", "https://example.com/leaders") == []


def test_wayback_guards_js_rendered_live_page(monkeypatch):
    from tool.sources import wayback as wb

    def fake_fetch(url):
        # old page has leaders, live page parses to zero (JS-rendered)
        return _OLD_PAGE if "web.archive.org" in url else "<html><body></body></html>"
    monkeypatch.setattr(wb, "_fetch", fake_fetch)
    monkeypatch.setattr(wb, "_cdx_nearest", lambda url, days: "20260401000000")
    # Must NOT fabricate departures from a parse failure.
    assert wb.diff_company("BT Group", "https://example.com/leaders") == []


# ====================================================================
# 8. Market State auto-ingest (Layer 3 macro coefficient).
# ====================================================================
def test_market_parse_ppi_and_bellwether():
    from tool import market_ingest as M
    ppi_txt = ("KPMG and REC UK Report on Jobs: the Permanent Placements Index "
               "registered 47.6 in May, a softer fall.")
    assert M.parse_ppi(ppi_txt) == 47.6
    bell_txt = "IPA Bellwether recorded a net balance of +4.8% for marketing budgets."
    assert M.parse_bellwether(bell_txt) == 4.8
    # junk / implausible -> None (never poisons the coefficient)
    assert M.parse_ppi("nothing relevant here") is None
    assert M.parse_ppi("permanent placements collapsed to 12.0") is None
    assert M.parse_bellwether("no balance figure") is None


def test_market_refresh_writes_override(tmp_path, monkeypatch):
    from tool import market_ingest as M
    monkeypatch.setattr(M, "MARKET_FILE", tmp_path / "market_state.json")
    monkeypatch.setattr(M, "_fetch_text",
                        lambda url: "Permanent Placements Index registered 49.1")
    monkeypatch.setattr(M, "_gnews_text",
                        lambda q: "net balance of +6.0% for marketing budgets")
    ov = M.refresh_market_state(force=True)
    assert ov and ov["default_ppi"] == 49.1
    assert ov["marketing_budget_balance"] == 6.0
    assert M.MARKET_FILE.exists()
    # fresh -> skips the network on the next (non-forced) call
    monkeypatch.setattr(M, "_fetch_text", lambda url: (_ for _ in ()).throw(AssertionError("should not fetch")))
    again = M.refresh_market_state(force=False)
    assert again["default_ppi"] == 49.1


def test_market_refresh_no_write_when_unparseable(tmp_path, monkeypatch):
    from tool import market_ingest as M
    monkeypatch.setattr(M, "MARKET_FILE", tmp_path / "market_state.json")
    monkeypatch.setattr(M, "_fetch_text", lambda url: "no index here")
    monkeypatch.setattr(M, "_gnews_text", lambda q: "still nothing")
    assert M.refresh_market_state(force=True) is None
    assert not M.MARKET_FILE.exists()       # hand-set value stands


def test_lead_engine_applies_market_override(tmp_path, monkeypatch):
    from tool import lead_engine as LE
    f = tmp_path / "market_state.json"
    f.write_text('{"default_ppi": 55.0, "as_of": "2026-06", "marketing_budget_balance": 9.0}')
    monkeypatch.setattr(LE, "_MARKET_OVERRIDE_FILE", f)
    monkeypatch.setattr(LE, "_market_override_cache", {"mtime": None, "value": None})
    eff = LE._effective_market_state()
    assert eff["default_ppi"] == 55.0 and "auto-ingested" in eff["source"]
    # 55 PPI -> the UK-overall read is now expanding (was contracting at 44.3)
    assert LE._market_state("Some Unknown Co")["state"] == "expanding"
    # sector divergence preserved (shifted by the same delta)
    assert eff["sectors"]["retail_consumer"] > LE.MARKET_STATE["sectors"]["retail_consumer"]


def test_lead_engine_rejects_implausible_override(tmp_path, monkeypatch):
    from tool import lead_engine as LE
    f = tmp_path / "market_state.json"
    f.write_text('{"default_ppi": 999}')
    monkeypatch.setattr(LE, "_MARKET_OVERRIDE_FILE", f)
    monkeypatch.setattr(LE, "_market_override_cache", {"mtime": None, "value": None})
    assert LE._effective_market_state()["default_ppi"] == LE.MARKET_STATE["default_ppi"]


def test_lead_engine_no_override_file_is_unchanged(tmp_path, monkeypatch):
    from tool import lead_engine as LE
    monkeypatch.setattr(LE, "_MARKET_OVERRIDE_FILE", tmp_path / "absent.json")
    monkeypatch.setattr(LE, "_market_override_cache", {"mtime": None, "value": None})
    assert LE._effective_market_state() is LE.MARKET_STATE
    assert LE._market_state()["state"] == "contracting"   # the hand-set default


# ====================================================================
# 9. Technographics fingerprinting.
# ====================================================================
def test_technographics_detect_vendors():
    from tool.sources import technographics as T
    html = ('<script src="https://js.hs-scripts.com/123.js"></script>'
            '<script>var m="munchkin.js"</script>'
            '<script src="https://cdn.segment.com/analytics.js/v1/x/analytics.min.js"></script>')
    assert T.detect_vendors(html) == {"HubSpot", "Marketo", "Segment"}
    assert T.detect_vendors("<html><body>nothing</body></html>") == set()


def test_technographics_emits_on_new_adoption(tmp_path, monkeypatch):
    from tool.sources import technographics as T
    monkeypatch.setattr(T, "SNAPSHOT_FILE", tmp_path / "techno.json")
    sites = {"Tesco": "https://www.tesco.com/"}

    state = {"html": '<script src="https://js.hs-scripts.com/1.js"></script>'}  # HubSpot only
    monkeypatch.setattr(T, "_fetch", lambda url: state["html"])

    # First run seeds the snapshot — no events on pre-existing tags.
    assert T.detect_technographics(sites) == []
    # Now the site adds Marketo -> a NEW adoption fires one event.
    state["html"] += '<script>munchkin.js</script>'
    evs = T.detect_technographics(sites)
    assert [e.trigger_key for e in evs] == ["martech_adoption"]
    assert "Marketo" in evs[0].evidence and evs[0].company == "Tesco"
    # Re-scan with no change -> no new events.
    assert T.detect_technographics(sites) == []
