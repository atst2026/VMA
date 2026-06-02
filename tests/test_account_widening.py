"""Tests for the watchlist gate → boost change (recall widening).

Verifies that:
  * genuine off-watchlist employers are now ADMITTED (recall), and
  * the precision guards still drop the noise the watchlist gate was built
    to suppress — the mis-extracted-peer bug ('Three UK' from 'Three
    arrested…'), regulators-as-actor, and bare common words (regression),
  * off-watchlist leads score BELOW an equivalent core-watchlist lead, and
  * the persistence layer KEEPS broader-market predictors instead of
    purging them on the next run.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.account_match import (
    classify_account, _is_named_employer, _is_watchlist_member,
)


# ---- classify_account: watchlist tier ----------------------------------

def test_watchlist_subject_resolves_as_watchlist():
    name, tier = classify_account(
        "Barclays", "Barclays appoints new chief executive")
    assert tier == "watchlist"
    assert name and "barclays" in name.lower()


# ---- classify_account: precision regression guards ---------------------

def test_mis_extracted_peer_not_admitted_off_watchlist():
    # The original bug: 'Three arrested in FCA investigation' had its
    # company mis-extracted as the peer 'Three UK'. It must NOT now slip
    # through the off-watchlist path — peer names are barred from it.
    name, tier = classify_account(
        "Three UK", "Three arrested in FCA investigation")
    assert name is None
    assert tier == ""


def test_capital_signs_not_admitted_as_capita():
    name, tier = classify_account(
        "Capita", "Capital Signs major new deal with bank")
    assert name is None


def test_regulator_is_not_an_employer():
    assert not _is_named_employer("FCA")
    assert not _is_named_employer("Financial Conduct Authority")


def test_bare_common_words_are_not_employers():
    assert not _is_named_employer("Next")
    assert not _is_named_employer("Mind")
    assert not _is_named_employer("EQS")          # wire prefix
    assert not _is_named_employer("x")            # too short


# ---- classify_account: new recall --------------------------------------

def test_off_watchlist_public_body_admitted():
    name, tier = classify_account(
        "Riverside Housing Association",
        "Riverside Housing Association names new chief executive")
    assert tier == "off_watchlist"
    assert name == "Riverside Housing Association"


def test_off_watchlist_listed_company_via_suffix():
    name, tier = classify_account(
        "Smallcap Widgets plc",
        "Smallcap Widgets plc - Appointment of Chief Executive")
    assert tier == "off_watchlist"
    assert name == "Smallcap Widgets plc"


def test_named_employer_markers():
    assert _is_named_employer("Acme Group plc")
    assert _is_named_employer("Brunel University")
    assert _is_named_employer("Peabody Housing Association")
    assert _is_named_employer("Some Borough Council")


def test_watchlist_member_detection():
    assert _is_watchlist_member("Barclays")
    assert _is_watchlist_member("Three UK")       # peer, exact (suffix-stripped)
    assert not _is_watchlist_member("Riverside Housing Association")


# ---- ranker: off-watchlist discount ------------------------------------

def _evt(trigger_key, company, account_tier):
    from tool.predictive.detector import TriggerEvent
    return TriggerEvent(
        trigger_key=trigger_key,
        trigger_label=trigger_key,
        company=company,
        evidence="ev",
        url="",
        source_label="LSE RNS (Investegate)",
        published=datetime.now(timezone.utc),
        tier_hint="listed",
        account_tier=account_tier,
    )


def test_off_watchlist_scores_below_watchlist():
    from tool.predictive.stacker import Stack
    from tool.predictive.ranker import score_stack
    wl = Stack(company="Barclays",
               events=[_evt("ceo_change", "Barclays", "watchlist")])
    off = Stack(company="Acme Group plc",
                events=[_evt("ceo_change", "Acme Group plc", "off_watchlist")])
    s_wl = score_stack(wl)
    s_off = score_stack(off)
    assert s_wl > s_off > 0


# ---- detector end-to-end -----------------------------------------------

def test_detector_admits_off_watchlist_employer():
    from tool.predictive.detector import detect_events
    signals = [{
        "title": "Smallcap Widgets plc - Appointment of Chief Executive",
        "summary": "The board today announces the appointment of a new "
                   "chief executive.",
        "company": "Smallcap Widgets plc",
        "source": "LSE RNS (Investegate)",
        "url": "https://example.com/rns/1",
        "published": datetime.now(timezone.utc).isoformat(),
        "id": "rns|1",
    }]
    events = detect_events(signals)
    assert any(e.account_tier == "off_watchlist"
               and e.trigger_key == "ceo_change" for e in events)


def test_detector_still_drops_three_arrested():
    from tool.predictive.detector import detect_events
    signals = [{
        "title": "Three arrested in FCA investigation",
        "summary": "Three people were arrested as part of an FCA probe.",
        "company": "",
        "source": "GDELT",
        "url": "https://example.com/news/1",
        "published": datetime.now(timezone.utc).isoformat(),
        "id": "gdelt|1",
    }]
    events = detect_events(signals)
    # No ceo_change / regulator event should attribute to 'Three UK'.
    assert not any("three" in (e.company or "").lower() for e in events)


# ---- pipeline persistence: broader-market entries survive re-gate ------

def test_pipeline_keeps_off_watchlist_entry():
    from tool.predictor_pipeline import _regate
    entry = {
        "company": "Riverside Housing Association",
        "account_tier": "off_watchlist",
        "events": [{
            "trigger_key": "ceo_change",
            "trigger_label": "CEO change",
            "evidence": "Riverside Housing Association names new chief executive",
        }],
    }
    assert _regate(entry) == "Riverside Housing Association"


def test_pipeline_still_purges_legacy_garbage():
    from tool.predictor_pipeline import _regate
    # A tier-less (legacy) entry whose evidence no longer resolves to any
    # watchlist subject must still be purged.
    entry = {
        "company": "EQS",
        "events": [{
            "trigger_key": "ceo_change",
            "trigger_label": "CEO change",
            "evidence": "EQS-News: some unrelated wire headline",
        }],
    }
    assert _regate(entry) is None
