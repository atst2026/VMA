"""AD-room item 5: BUYER rescore + warmth, propensity coherence on rival
mandates, freeze dampening scoped to the perm reading, RNS capture."""
from datetime import datetime, timedelta, timezone

import tool.warmth as W
from tool import gate, lead_engine as LE


def _setup_warmth(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "_file", lambda: tmp_path / "warmth.json")


# ---- warmth store ----------------------------------------------------
def test_warmth_store_roundtrip_and_source(tmp_path, monkeypatch):
    _setup_warmth(tmp_path, monkeypatch)
    assert W.set_warm("Acme Co", note="placed their Head of IC in 2023")
    rec = W.get("Acme Co")
    assert rec["warm"] and rec["source"] == "manual"
    assert W.set_warm("Beta Ltd", source="imported")
    assert W.get("Beta Ltd")["source"] == "imported"
    assert W.set_warm("Acme Co", warm=False)
    assert W.get("Acme Co") is None
    item = W.annotate({"company": "Beta Ltd"})
    assert item["warm_route"]["source"] == "imported"


# ---- BUYER rescore ---------------------------------------------------
def _qual(item=None, **lead_extra):
    lead = {"triggers": [], **lead_extra}
    return gate.qualification(lead, item or {}, {}, "open")


def test_buyer_warm_beats_named_cold():
    warm = _qual({"warm_route": {"warm": True, "note": "placed their CCO"}})
    named = _qual({"seeded_contact_name": "Jane Doe"})
    mapped = _qual(None, who_to_call="CFO")
    nothing = _qual()
    assert warm["buyer"] == 2 and "warm route" in warm["buyer_why"]
    assert named["buyer"] == 1 and "cold" in named["buyer_why"]
    assert mapped["buyer"] == 1
    assert nothing["buyer"] == 0


def test_strength_mirrors_buyer_rescore():
    base = {"fit": 5, "signal": 5}
    s_warm = gate.strength_score({**base, "relationship": "warm"}, {},
                                 {"warm_route": {"warm": True}})
    s_named = gate.strength_score(base, {}, {"seeded_contact_name": "Jane"})
    s_mapped = gate.strength_score({**base, "who_to_call": "CFO"}, {}, {})
    s_none = gate.strength_score(base, {}, {})
    assert s_warm - s_none == 15
    assert s_named - s_none == 8
    assert s_mapped == s_named


# ---- rival-mandate propensity coherence -------------------------------
def test_rival_mandate_banks_full_propensity():
    pts, basis = gate.propensity_points({"conflict": True}, {})
    assert pts == gate.PROP_PROVEN and basis == "authoritative"
    # outranks an in-house TA observation: the senior search went
    # external DESPITE the TA team.
    pts2, _ = gate.propensity_points({"conflict": True}, {"internal_ta": True})
    assert pts2 == gate.PROP_PROVEN


# ---- freeze dampening scoped to perm ----------------------------------
def _iso(d):
    return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()


def _frozen_item(with_demand):
    events = [{"trigger_key": "restructure", "trigger_label": "Restructure",
               "url": "https://investegate.co.uk/x", "source": "RNS",
               "tier": "listed", "published": _iso(20),
               "evidence": "restructure announced alongside a hiring freeze"}]
    if with_demand:
        events.append({"trigger_key": "job_ad_cluster",
                       "trigger_label": "Hiring cluster",
                       "url": "https://jobs.example.com", "source": "ATS",
                       "tier": "listed", "published": _iso(3),
                       "evidence": "three senior comms roles live"})
    return {"company": "FrozenCo", "account_tier": "watchlist",
            "last_seen": _iso(1), "events": events}


def test_freeze_with_live_demand_stays_callable():
    cold = LE.score_lead(_frozen_item(False))
    live = LE.score_lead(_frozen_item(True))
    assert "hiring_freeze" in live["anti_triggers"]
    # The dampener hits the perm-only reading; with live demand the
    # interim lead keeps (almost) full signal rather than being buried.
    assert live["signal"] > cold["signal"]
    g = gate.assess(_frozen_item(True), live)
    score = gate.strength_score(live, g, _frozen_item(True))
    assert gate.tier_for(live, g, score) != "blocked"
    assert score > 15


# ---- RNS enquiries capture --------------------------------------------
def test_rns_block_capture(tmp_path, monkeypatch):
    import tool.rns_contacts as RC
    monkeypatch.setattr(RC, "_file", lambda: tmp_path / "blocks.json")
    html = ("<html><p>Results body…</p><h3>Enquiries:</h3>"
            "<p>IMI plc — Erica Lockhart, Group Corporate Communications "
            "Director, +44 20 0000 0000. FTI Consulting — Nick Hasell, "
            "imi@fticonsulting.com</p></html>")
    sigs = [{"kind": "rns", "company": "IMI plc",
             "url": "https://www.investegate.co.uk/announcement/1"},
            {"kind": "job", "company": "IMI plc",
             "url": "https://jobs.example.com/x"}]
    n = RC.capture_from_signals(sigs, fetch=lambda u: html)
    assert n == 1
    store = RC._load()
    rec = store[RC._norm("IMI plc")]
    assert "Erica Lockhart" in rec["blocks"][0]["block"]
    assert "FTI Consulting" in rec["blocks"][0]["block"]
    # Re-running never re-fetches the same URL.
    assert RC.capture_from_signals(sigs, fetch=lambda u: html) == 0


def test_extract_block_rejects_thin_matches():
    import tool.rns_contacts as RC
    assert RC.extract_block("<p>Enquiries: none.</p>") is None
    assert RC.extract_block(None) is None
