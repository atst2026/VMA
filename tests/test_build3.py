"""Demand-side build 3: scheduled auto-investigation + universe-expansion
proposals. All model calls stubbed; zero network."""
from datetime import datetime, timezone

import tool.auto_investigate as AI
import tool.universe_expand as UE
import tool.investigations as INV


# ---- auto-investigate ---------------------------------------------------
def _setup_inv(tmp_path, monkeypatch):
    monkeypatch.setattr(INV, "_dir", lambda: tmp_path / "investigations")
    import tool.predictor_pipeline as PP
    pipeline = {"predictors": {
        "imi": {"pid": "imi", "status": "active", "score": 9.0,
                "company": "IMI", "predicted_role": "Corporate Affairs Director",
                "window_label": "8-24 weeks", "incumbent_status": "unchecked",
                "events": [{"trigger_label": "Share allotment",
                            "published": "2026-06-01T00:00:00+00:00",
                            "evidence": "three filings", "url": "https://x"}]},
        "low": {"pid": "low", "status": "active", "score": 1.0,
                "company": "LowCo", "events": []},
        "dead": {"pid": "dead", "status": "dismissed", "score": 99.0,
                 "company": "DeadCo", "events": []},
    }}
    monkeypatch.setattr(AI, "load_pipeline", None, raising=False)
    import tool.predictor_pipeline as _pp
    monkeypatch.setattr(_pp, "load_pipeline", lambda: pipeline)
    return pipeline


def test_auto_investigate_writes_overlay_through_the_normal_door(tmp_path, monkeypatch):
    _setup_inv(tmp_path, monkeypatch)
    briefs = []
    def runner(brief):
        briefs.append(brief)
        return {"verdict": "confirmed",
                "note": "Capital raise verified via RNS and FT.",
                "kill_reasons": [], "recheck_days": None,
                "economic_buyer": "CFO", "champion_path": "",
                "incumbent_found": "Erica Lockhart, Group Corp Comms Director",
                "agency_user": True, "agency_scope": "comms_marketing",
                "internal_ta": None,
                "propensity_note": "agency-posted comms ad found",
                "sources": ["https://investegate.co.uk/x"]}
    import tool.propensity as PR
    recorded = {}
    monkeypatch.setattr(PR, "record_finding",
                        lambda co, **kw: recorded.update({co: kw}) or True)
    n = AI.run(max_leads=1, runner=runner)
    assert n == 1
    assert "IMI" in briefs[0] and "Corporate Affairs Director" in briefs[0]
    overlays = INV.get_all()
    ov = overlays["imi"]
    assert ov["verdict"] == "confirmed"
    assert "Erica Lockhart" in ov["note"]
    assert recorded["IMI"]["agency_scope"] == "comms_marketing"


def test_auto_investigate_skips_fresh_overlays_and_dismissed(tmp_path, monkeypatch):
    _setup_inv(tmp_path, monkeypatch)
    INV.write_overlay("imi", "recheck", note="already done", recheck_days=14)
    calls = []
    def runner(brief):
        calls.append(brief)
        return {"verdict": "recheck", "note": "x", "kill_reasons": [],
                "recheck_days": 14, "economic_buyer": "", "champion_path": "",
                "incumbent_found": None, "agency_user": None,
                "agency_scope": None, "internal_ta": None,
                "propensity_note": "", "sources": []}
    AI.run(max_leads=5, runner=runner)
    # imi has a fresh overlay, dead is dismissed -> only "low" investigated
    assert len(calls) == 1 and "LowCo" in calls[0]


def test_auto_investigate_rejects_malformed_verdicts(tmp_path, monkeypatch):
    _setup_inv(tmp_path, monkeypatch)
    assert AI.run(max_leads=1, runner=lambda b: {"verdict": "vibes"}) == 0
    assert INV.get_all() == {}


# ---- universe expansion -------------------------------------------------
def _setup_ue(tmp_path, monkeypatch):
    monkeypatch.setattr(UE, "_file", lambda: tmp_path / "universe.json")
    import tool.account_match as AM
    monkeypatch.setattr(
        AM, "classify_account",
        lambda cand, *t: ((cand, "watchlist") if cand == "IMI" else (None, "")))
    monkeypatch.setattr(AM, "_load_watchlist_names", lambda: ["IMI", "Severn Trent"])


def test_universe_proposals_stored_and_fresh(tmp_path, monkeypatch):
    _setup_ue(tmp_path, monkeypatch)
    signals = [
        {"company": "IMI", "title": "IMI raises capital"},          # on-list
        {"company": "Brighthorn Foods", "title": "Brighthorn Foods appoints CEO"},
        {"company": "Brighthorn Foods", "title": "dup"},            # deduped
    ]
    def call(content):
        assert "Brighthorn Foods" in content and "CURRENT WATCHLIST" in content
        assert "IMI raises capital" not in content                  # on-list excluded
        return {"proposals": [{"company": "Brighthorn Foods",
                               "case": "Mid-cap food producer, new CEO."}]}
    assert UE.run(signals, call=call) == 1
    props = UE.fresh_proposals()
    assert props[0]["company"] == "Brighthorn Foods"
    # weekly gate: immediate second run is a no-op
    assert UE.run(signals, call=call) == 0


def test_universe_never_runs_more_than_weekly(tmp_path, monkeypatch):
    _setup_ue(tmp_path, monkeypatch)
    UE._save({"last_run": datetime.now(timezone.utc).isoformat()})
    assert UE.run([{"company": "X Co", "title": "t"}],
                  call=lambda c: {"proposals": []}) == 0
