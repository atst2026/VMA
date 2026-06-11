"""Tests for the fee-propensity store (tool/propensity.py), the TA-title
counting in the ATS layer, and the end-to-end effect through the posture
layer and Lead Strength score."""
from datetime import datetime, timedelta, timezone

import tool.propensity as PR
from tool.sources.jobs import _TA_TITLE_RX, _ta_count


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(PR, "_store_path", lambda: tmp_path / "propensity.json")
    monkeypatch.setattr(PR, "_seeds_path", lambda: tmp_path / "seeds.json")


# ====================================================================
# TA-title detection (ATS layer)
# ====================================================================
def test_ta_titles_match_and_comms_titles_do_not():
    hits = ["Talent Acquisition Partner", "Senior Recruiter",
            "Recruitment Manager", "In-House Recruitment Lead",
            "Head of Talent"]
    misses = ["Director of Communications", "Head of Corporate Affairs",
              "Marketing Manager", "Software Engineer"]
    for t in hits:
        assert _TA_TITLE_RX.search(t), t
    for t in misses:
        assert not _TA_TITLE_RX.search(t), t
    items = [{"title": t} for t in hits + misses] + [None, {}]
    assert _ta_count(items, "title") == len(hits)


# ====================================================================
# Store: ATS ingest, expiry, clearing
# ====================================================================
def test_ats_ingest_sets_and_clears_internal_ta(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert PR.ingest_ats_counts({"acme-co": (20, 0, 3)}) == 1
    f = PR.flags_for("Acme Co")
    assert f.get("internal_ta") is True
    assert "3 talent-acquisition/recruiter roles" in f["internal_ta_evidence"]
    # Board now shows zero TA roles: the flag clears.
    PR.ingest_ats_counts({"acme-co": (18, 1, 0)})
    assert PR.flags_for("Acme Co") == {}


def test_ta_flag_expires(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    PR.ingest_ats_counts({"acme-co": (20, 0, 2)})
    later = datetime.now(timezone.utc) + timedelta(days=PR.TA_EXPIRE_DAYS + 1)
    assert PR.flags_for("Acme Co", now=later) == {}


# ====================================================================
# Store: procurement award scan (proven fee-payer)
# ====================================================================
def test_award_scan_records_watchlist_buyer(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    signals = [
        {"title": "Contract award notice: Executive Search Services",
         "summary": "Severn Trent has awarded a contract for executive "
                    "search services to an external supplier.",
         "url": "https://www.find-tender.service.gov.uk/Notice/1"},
        # Recruitment terms but no award verb — ignored.
        {"title": "Tender: recruitment services framework consultation",
         "summary": "Market engagement only."},
        # Award but nothing to do with recruitment — ignored.
        {"title": "Contract award: grounds maintenance", "summary": "x"},
    ]
    assert PR.scan_signals_for_agency_awards(signals) == 1
    f = PR.flags_for("Severn Trent")
    assert f.get("agency_user") is True
    assert f.get("agency_scope") == PR.SCOPE_GENERAL
    assert "proven fee-payer" in f["agency_evidence"]


# ====================================================================
# Agency-use SCOPE: function-aware tiers
# ====================================================================
def test_award_scope_classification():
    assert PR._award_scope(
        "Contract award: executive search for a Director of "
        "Communications") == PR.SCOPE_COMMS_MKT
    assert PR._award_scope(
        "Contract award: temporary staffing services") == PR.SCOPE_TEMP
    assert PR._award_scope(
        "Contract award: interim management services and temporary "
        "staff") == PR.SCOPE_TEMP
    assert PR._award_scope(
        "Contract award: executive search services") == PR.SCOPE_GENERAL


def test_temp_only_award_does_not_make_a_proven_fee_payer(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    PR.scan_signals_for_agency_awards([
        {"title": "Contract award notice: temporary staffing",
         "summary": "Severn Trent awarded a temporary staffing contract."}])
    f = PR.flags_for("Severn Trent")
    assert f.get("agency_scope") == PR.SCOPE_TEMP
    item = PR.annotate({"company": "Severn Trent"})
    # Temp supply must NOT light the proven-fee-payer input…
    assert item.get("psl_status") != "on"
    # …but the caveat is surfaced.
    assert "NOT evidence" in item.get("_propensity_note", "")


def test_temp_award_never_downgrades_comms_evidence(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    PR.scan_signals_for_agency_awards([
        {"title": "Contract award: executive search, Head of Communications",
         "summary": "Severn Trent awarded an executive search contract for "
                    "its communications leadership."}])
    PR.scan_signals_for_agency_awards([
        {"title": "Contract award notice: temporary staffing",
         "summary": "Severn Trent awarded a temporary staffing contract."}])
    assert PR.flags_for("Severn Trent")["agency_scope"] == PR.SCOPE_COMMS_MKT


def test_agency_posted_job_ad_records_comms_scope(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    import tool.account_match as AM
    monkeypatch.setattr(
        AM, "classify_account",
        lambda _c, text: (("Severn Trent", "watchlist")
                          if "severn trent" in (text or "").lower()
                          else (None, None)))
    signals = [
        # Agency-posted comms ad naming a watchlist client — recorded.
        {"kind": "job", "company": "Hanson Search",
         "title": "Head of Communications — Severn Trent",
         "summary": "Our client Severn Trent seeks a Head of Communications.",
         "url": "https://example.com/ad"},
        # Direct employer posting — poster isn't an agency; ignored.
        {"kind": "job", "company": "Severn Trent",
         "title": "Head of Communications",
         "summary": "Join Severn Trent."},
        # Agency poster but no identifiable client — ignored.
        {"kind": "job", "company": "Premier Resourcing",
         "title": "PR Account Director", "summary": "Our client, a fintech."},
    ]
    assert PR.scan_job_signals_for_agency_posted_ads(signals) == 1
    f = PR.flags_for("Severn Trent")
    assert f.get("agency_user") is True
    assert f.get("agency_scope") == PR.SCOPE_COMMS_MKT
    assert "Hanson Search" in f["agency_evidence"]
    item = PR.annotate({"company": "Severn Trent"})
    assert item.get("psl_status") == "on"
    assert item.get("agency_scope") == PR.SCOPE_COMMS_MKT


def test_record_finding_carries_scope(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert PR.record_finding("Acme Co", agency_user=True,
                             agency_scope="comms_marketing",
                             note="PRWeek: appointment via search firm",
                             source_url="https://prweek.com/x")
    f = PR.flags_for("Acme Co")
    assert f["agency_scope"] == PR.SCOPE_COMMS_MKT
    # An unknown scope string degrades to general, not an error.
    PR.record_finding("Beta Ltd", agency_user=True, agency_scope="bogus")
    assert PR.flags_for("Beta Ltd")["agency_scope"] == PR.SCOPE_GENERAL


# ====================================================================
# Seeds outrank machine observations
# ====================================================================
def test_seeds_win(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    PR.ingest_ats_counts({"acme-co": (20, 0, 4)})   # machine: in-house
    (tmp_path / "seeds.json").write_text(
        '{"acme co": {"internal_ta": false, "agency_user": true, '
        '"note": "VMA placed their Head of Comms in 2024"}}')
    f = PR.flags_for("Acme Co")
    assert f["internal_ta"] is False and f["agency_user"] is True
    assert "VMA placed" in f["agency_evidence"]


# ====================================================================
# annotate() lights up the posture layer end-to-end
# ====================================================================
def test_annotate_drives_posture_and_score(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from tool import lead_engine as LE, gate

    def _iso(d):
        return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()

    def _item(company):
        return {"company": company, "account_tier": "watchlist",
                "last_seen": _iso(1), "events": [
                    {"trigger_key": "ceo_change", "trigger_label": "CEO change",
                     "url": "https://investegate.co.uk/x", "source": "RNS",
                     "tier": "listed", "published": _iso(40), "evidence": ""}]}

    # Proven agency user: posture flips external-authoritative, score +15.
    PR.scan_signals_for_agency_awards([
        {"title": "Contract award notice: recruitment services",
         "summary": "Severn Trent awarded a permanent recruitment contract."}])
    base_item, prop_item = _item("Tesco"), PR.annotate(_item("Severn Trent"))
    assert prop_item.get("psl_status") == "on"
    lead = LE.score_lead(prop_item)
    assert lead["posture"]["direction"] == "external"
    assert any("agency use" in r for r in lead["posture"]["reasons"])
    g = gate.assess(prop_item, lead)
    base_lead = LE.score_lead(base_item)
    base_g = gate.assess(base_item, base_lead)
    assert (gate.strength_score(lead, g, prop_item)
            - gate.strength_score(base_lead, base_g, base_item)) == \
        gate.PROP_PROVEN - gate.PROP_NEUTRAL

    # TA-hiring company: posture internal -> contradiction -> never ready.
    PR.ingest_ats_counts({"unilever": (30, 0, 5)})
    ta_item = PR.annotate(_item("Unilever"))
    assert ta_item.get("internal_ta") is True
    ta_lead = LE.score_lead(ta_item)
    assert ta_lead["posture"]["direction"] == "internal"
    assert any("in-house" in c for c in ta_lead["contradictions"])
    ta_g = gate.assess(ta_item, ta_lead)
    assert not ta_g["presented"]
    assert gate.tier_for(ta_lead, ta_g,
                         gate.strength_score(ta_lead, ta_g, ta_item)) != "ready"
