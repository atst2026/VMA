"""Advisory origination reused from the predictor pipeline (§3 B/D/E) —
M&A → PostMergerIntegration, restructure/redundancy → RestructureRedundancy,
ESG/B-Corp → ESGCapabilityBuild, routed to advisory not just "a seat"."""
from datetime import date

import pytest

from tool import advisory_gate as G
from tool.advisory_signals.from_predictors import predictor_advisory_signals

TODAY = date(2026, 6, 14)


@pytest.fixture(autouse=True)
def _comms_default(monkeypatch):
    monkeypatch.delenv("VMA_PROFILE", raising=False)


def _entry(company, trigger_key, source="Investegate RNS",
           url="https://www.investegate.co.uk/x", published="2026-06-01T00:00:00+00:00"):
    return {"company": company, "pid": company.lower().replace(" ", "-"),
            "status": "active",
            "events": [{"trigger_key": trigger_key, "trigger_label": trigger_key,
                        "evidence": "…", "url": url, "source": source,
                        "published": published, "tier": "gold"}]}


def test_mna_becomes_post_merger_integration():
    sigs = predictor_advisory_signals(
        entries=[_entry("Acme Group", "mna")], today=TODAY)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.trigger == "PostMergerIntegration"
    assert s.company == "Acme Group"
    assert "org_design" in s.service_mix          # routes to advisory, not a hire
    assert s.window is not None
    assert s.extra["mandate"] is True


def test_restructure_and_esg_map_to_their_classes():
    sigs = predictor_advisory_signals(entries=[
        _entry("Beta Plc", "restructure"),
        _entry("Gamma Ltd", "esg_bcorp", source="B Lab UK",
               url="https://bcorporation.uk/x")], today=TODAY)
    by = {s.trigger for s in sigs}
    assert {"RestructureRedundancy", "ESGCapabilityBuild"} <= by


def test_non_advisory_trigger_is_ignored():
    # A pure hiring signal (job-ad cluster) is not an advisory origination.
    assert predictor_advisory_signals(
        entries=[_entry("Delta Plc", "job_ad_cluster")], today=TODAY) == []


def test_dismissed_entries_are_skipped():
    e = _entry("Eta Plc", "mna")
    e["status"] = "dismissed"
    assert predictor_advisory_signals(entries=[e], today=TODAY) == []


def test_predictor_signal_qualifies_through_the_gate():
    # RNS-sourced M&A: a registry-attested pain + a mandate proxy + an
    # in-window timing → a real DEVELOP lead (PURSUE once a buyer resolves).
    sig = predictor_advisory_signals(
        entries=[_entry("Acme Group", "mna")], today=TODAY)[0]
    out = G.assess(sig, facts={}, today=TODAY)
    assert out["qual"]["pain"] == 2          # RNS is primary/registry-grade
    assert out["qual"]["mandate"] == 1       # the board/transformation proxy
    assert out["qual"]["timing"] == 2        # inside the integration window
    assert out["verdict"] == "DEVELOP"       # no reachable buyer yet
    # With a named, reachable buyer it becomes call-ready.
    out2 = G.assess(sig, facts={"sponsor_name": "Jo Lee",
                                "warm_route": {"note": "ex-colleague"}},
                    today=TODAY)
    assert out2["verdict"] == "PURSUE"
    assert out2["owner"]["owner"] == "Lucy Cairncross"   # advisory-led routing
