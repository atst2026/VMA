"""The Opus layer — the overlay store, the gate/pack override, and the
API path's default-off no-op (so the £0 nightly pipeline is never touched).
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from tool import advisory_gate as G
from tool import advisory_overlay as OV
from tool import evidence_pack as EP
from tool import advisory_llm as LLM
from tool.advisory_signals.base import AdvisorySignal

TODAY = date(2026, 6, 14)
NOW = datetime(2026, 6, 14, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv("VMA_PROFILE", raising=False)
    # Point the overlay store at a temp dir so tests never touch real state.
    monkeypatch.setattr(OV, "_dir", lambda: tmp_path / "advisory_overlays")
    monkeypatch.delenv("ADVISORY_LLM_ENABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _sig():
    return AdvisorySignal(
        trigger="PayGapActionMandate", company="Widgety Ltd",
        service_mix=["edi", "benchmarking", "coaching"],
        pain="median gender pay gap 22.0%", buyer_hint="CHRO",
        evidence=[{"source": "GOV.UK", "url":
                   "https://gender-pay-gap.service.gov.uk/x"}],
        window=("2026-03-01", "2026-07-31"), confidence=0.8,
        extra={"size_band": "1000 to 4999", "median": 22.0})


# ------------------------------------------------ the overlay store

def test_overlay_write_get_roundtrip():
    assert OV.write("Widgety Ltd", "PayGapActionMandate", "PURSUE",
                    conviction=88, named_pain="wide gap, no plan",
                    economic_buyer="Dana Okoye, CHRO",
                    sharpest_insight="You carry fewer comms staff than peers.")
    ov = OV.get("Widgety Ltd", "PayGapActionMandate", now=NOW)
    assert ov and ov["verdict"] == "PURSUE" and ov["conviction"] == 88
    assert ov["economic_buyer"] == "Dana Okoye, CHRO"


def test_overlay_rejects_bad_verdict():
    assert OV.write("X", "T", "MAYBE") is False


def test_overlay_expires():
    # write() stamps the real clock; anchor `later` to it so the test is
    # robust regardless of the container's wall time.
    OV.write("Old Co", "PayGapActionMandate", "PURSUE")
    fresh = OV.get("Old Co", "PayGapActionMandate")          # readable now
    assert fresh is not None
    later = datetime.now(timezone.utc) + timedelta(days=OV.EXPIRE_DAYS + 2)
    assert OV.get("Old Co", "PayGapActionMandate", now=later) is None


# ------------------------------------ the gate override (overlay outranks)

def test_opus_overlay_overrides_the_deterministic_verdict():
    # Deterministic: a raw registry signal with no buyer is DEVELOP.
    base = G.assess(_sig(), facts={}, today=TODAY)
    assert base["verdict"] == "DEVELOP" and not base.get("opus")
    # Opus overlay says PURSUE (it found and named the buyer) → it wins.
    OV.write("Widgety Ltd", "PayGapActionMandate", "PURSUE", conviction=90,
             economic_buyer="Dana Okoye, CHRO",
             sharpest_insight="A 22% gap with no published plan.")
    out = G.assess(_sig(), facts={}, today=TODAY)
    assert out["opus"] is True
    assert out["verdict"] == "PURSUE" and out["conviction"] == 90
    assert out["opus_verdict"]["economic_buyer"] == "Dana Okoye, CHRO"
    # The deterministic scorecard still rides along for the chips.
    assert out["qual"]["total"] >= 8


def test_opus_kill_overlay_suppresses_a_lead():
    OV.write("Widgety Ltd", "PayGapActionMandate", "KILL",
             kill_reasons=["a current action plan is already published"])
    out = G.assess(_sig(), facts={"sponsor_name": "X",
                                  "warm_route": {"note": "y"}}, today=TODAY)
    assert out["verdict"] == "KILL"


# ---------------------------------------- the pack uses the Opus prose

def test_pack_prefers_the_opus_diagnostic_and_insight():
    OV.write("Widgety Ltd", "PayGapActionMandate", "PURSUE",
             sharpest_insight="The sharp Opus reframe.",
             diagnostic="The reasoned Opus 1-page hypothesis.")
    pack = EP.compose(_sig())
    assert pack.get("opus") is True
    assert pack["reframe"] == "The sharp Opus reframe."
    assert pack["diagnostic"] == "The reasoned Opus 1-page hypothesis."


# ---------------------------------------- the API path is off by default

def test_llm_disabled_by_default():
    assert LLM.enabled() is False
    assert LLM.conviction_verdict(_sig(), {}, {}) is None
    assert LLM.run_and_persist(_sig(), {}, {}) is False


def test_llm_runs_with_injected_call_and_persists(monkeypatch):
    # With a stub `call` (no real API, no key needed) the pass persists an
    # overlay the gate then reads — proving the wiring end to end.
    stub = lambda brief: {"verdict": "PURSUE", "conviction": 84,
                          "named_pain": "wide gap, no plan",
                          "economic_buyer": "Dana Okoye, CHRO",
                          "recommended_service": "edi",
                          "sharpest_insight": "You trail peers on comms FTE.",
                          "confidence": "Moderate",
                          "diagnostic": "1-page hypothesis."}
    assert LLM.run_and_persist(_sig(), {"total": 10}, {}, call=stub) is True
    out = G.assess(_sig(), facts={}, today=TODAY)
    assert out["verdict"] == "PURSUE" and out["conviction"] == 84
    assert out["opus_verdict"]["recommended_service"] == "edi"
