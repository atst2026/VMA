"""The Advisory Engine (Phase 1) — detection discipline, the 6-dimension
MEDDPICC gate, the source-independence gate, amplifier/bronze tiering, the
PURSUE cap, and the facts-only Evidence Pack.

Locks the three failure-mode defences (ADVISORY_ENGINE.md §11):
  #1 weak-signal flooding — a standing gap is not a lead (no window → no
     signal); source-independence is a hard PURSUE gate; low-precision
     triggers never pursue alone; a daily cap forces ranking.
  #2 generic output — the pack carries a real benchmark anchor + buyer.
  #3 no clear action — a raw signal stays DEVELOP until a reachable buyer.
"""
from datetime import date

import pytest

from tool import advisory_gate as G
from tool import evidence_pack as EP
from tool.advisory_signals import pay_gap as PG
from tool.advisory_signals.base import AdvisorySignal

TODAY = date(2026, 6, 14)
WINDOW = ("2026-03-01", "2026-07-31")          # an open statutory window
LAPSED = ("2025-03-01", "2025-07-31")          # a closed one
EQ_KEYS = {"equality_pay_reporting_2026"}


@pytest.fixture(autouse=True)
def _comms_default(monkeypatch):
    monkeypatch.delenv("VMA_PROFILE", raising=False)


def _gpg(employer, median, *, size="1000 to 4999", late=False, number="123"):
    return {"employer": employer, "median": median, "size": size,
            "late": late, "number": number,
            "url": "https://gender-pay-gap.service.gov.uk/Employer/x"}


def _registry_signal(**over):
    """A wide-gap PayGapActionMandate signal in an open window."""
    base = dict(
        trigger="PayGapActionMandate", company="Widgety Ltd",
        service_mix=["edi", "benchmarking", "coaching"],
        pain="A board-level ED&I exposure: median gender pay gap 22.0%.",
        buyer_hint="CHRO / People Director", why_now="window open",
        evidence=[{"source": "GOV.UK Gender Pay Gap Service",
                   "url": "https://gender-pay-gap.service.gov.uk/Employer/x"}],
        window=WINDOW, confidence=0.8,
        extra={"size_band": "1000 to 4999", "pulse_key":
               "equality_pay_reporting_2026", "median": 22.0})
    base.update(over)
    return AdvisorySignal(**base)


# ---------------------------------------------------- AdvisorySignal

def test_signal_source_families_and_serialisation():
    s = _registry_signal()
    assert s.n_source_families == 1
    d = s.to_dict()
    import json
    assert json.loads(json.dumps(d)) == d


# ---------------------------------------------- detection discipline (#1)

def test_no_window_means_no_lead():
    # The compelling event is the statutory clock; with no window open a
    # standing gap must NOT manufacture a lead.
    out = PG.pay_gap_action_signals(today=TODAY, active_keys=set(),
                                    records=[_gpg("Wide Plc", 22.0)])
    assert out == []


def test_clean_gap_in_window_is_not_a_lead():
    # A small, on-time gap is enrichment, never a card (edi_angle = None).
    out = PG.pay_gap_action_signals(today=TODAY, active_keys=EQ_KEYS,
                                    records=[_gpg("Tidy Ltd", 4.0)])
    assert out == []


def test_wide_gap_in_window_originates_an_edi_lead():
    out = PG.pay_gap_action_signals(today=TODAY, active_keys=EQ_KEYS,
                                    records=[_gpg("Wide Plc", 22.0)])
    assert len(out) == 1
    sig = out[0]
    assert sig.trigger == "PayGapActionMandate"
    assert sig.company == "Wide Plc"
    assert sig.service_mix[0] == "edi"        # routes to ED&I associates
    assert "gov.uk" in sig.evidence[0]["url"]
    assert sig.n_source_families == 1


def test_late_filing_is_a_lead_even_if_gap_modest():
    out = PG.pay_gap_action_signals(today=TODAY, active_keys=EQ_KEYS,
                                    records=[_gpg("Tardy Ltd", 8.0, late=True)])
    assert len(out) == 1 and out[0].extra["late"] is True


# ------------------------------------------------ the 6-dimension gate

def test_qualification_dimensions_for_registry_signal():
    q = G.qualification(_registry_signal(), facts={}, today=TODAY)
    assert q["pain"] == 2          # registry-attested
    assert q["mandate"] == 2       # statutory trigger
    assert q["timing"] == 2        # in-window
    assert q["proof"] == 2         # benchmark anchor from the size band
    # SPONSOR=1: the owning seat (CHRO/CEO) is mapped from the buyer hint,
    # but no PERSON is named and there is no route yet — so ACCESS=0 and the
    # lead cannot pursue (a clear action must be a reachable one).
    assert q["sponsor"] == 1 and q["access"] == 0
    assert q["total"] == 9 and q["present"] == 5


def test_raw_registry_signal_is_develop_not_pursue():
    # #3: a strong, verified pain with no reachable buyer is DEVELOP — a
    # clear action must also be a reachable one.
    out = G.assess(_registry_signal(), facts={}, today=TODAY)
    assert out["verdict"] == "DEVELOP"
    assert "owner" in out["why"].lower() or "buyer" in out["why"].lower()


def test_resolved_buyer_promotes_to_pursue():
    facts = {"sponsor_name": "Dana Okoye", "sponsor_title": "CHRO",
             "warm_route": {"note": "placed her deputy in 2024"}}
    out = G.assess(_registry_signal(), facts=facts, today=TODAY)
    assert out["verdict"] == "PURSUE"
    assert out["conviction"] >= 70


def test_lapsed_window_is_killed():
    out = G.assess(_registry_signal(window=LAPSED), facts={}, today=TODAY)
    assert out["verdict"] == "KILL"


def test_no_pain_is_killed():
    out = G.assess(_registry_signal(pain=""), facts={}, today=TODAY)
    assert out["verdict"] == "KILL"


# -------------------------------------- source-independence as a GATE (#1)

def test_single_nonregistry_source_cannot_pursue():
    # A registry-blind advisory signal on one outlet, even with a named &
    # reachable buyer, stays DEVELOP — the >=2-source gate.
    sig = _registry_signal(
        evidence=[{"source": "PRWeek", "url": "https://prweek.com/a"}])
    facts = {"sponsor_name": "X", "warm_route": {"note": "y"}}
    out = G.assess(sig, facts=facts, today=TODAY)
    assert out["verdict"] == "DEVELOP"
    assert "second independent source" in out["why"]


def test_two_independent_sources_clear_the_gate():
    sig = _registry_signal(evidence=[
        {"source": "PRWeek", "url": "https://prweek.com/a"},
        {"source": "FT", "url": "https://ft.com/b"}])
    facts = {"sponsor_name": "X", "warm_route": {"note": "y"}}
    assert G.assess(sig, facts=facts, today=TODAY)["verdict"] == "PURSUE"


# ------------------------------------ amplifier / bronze tiering (#1)

def test_bronze_trigger_never_pursues_alone():
    sig = _registry_signal(trigger="EmployeeSentimentDeterioration")
    facts = {"sponsor_name": "X", "warm_route": {"note": "y"}, "mandate": True}
    out = G.assess(sig, facts=facts, today=TODAY)
    assert out["verdict"] == "DEVELOP"
    assert "corroborate" in out["why"]


def test_bronze_trigger_pursues_when_corroborated():
    sig = _registry_signal(trigger="EmployeeSentimentDeterioration")
    facts = {"sponsor_name": "X", "warm_route": {"note": "y"},
             "mandate": True, "corroborating_trigger": "restructure"}
    assert G.assess(sig, facts=facts, today=TODAY)["verdict"] == "PURSUE"


# --------------------------------------------- the PURSUE cap (#1)

def test_pursue_cap_demotes_the_overflow():
    rows = [{"verdict": "PURSUE", "conviction": c} for c in
            (95, 90, 85, 80, 75, 70)]                # six PURSUE, cap 5
    out = G.rank_and_cap(rows, cap=5)
    pursued = [r for r in out if r["verdict"] == "PURSUE"]
    demoted = [r for r in out if r.get("capped")]
    assert len(pursued) == 5 and len(demoted) == 1
    assert demoted[0]["conviction"] == 70           # the weakest ranked out


# --------------------------------------------- the Evidence Pack (#2, facts-only)

def test_evidence_pack_has_seven_parts_and_no_fee_figures():
    pack = EP.compose(_registry_signal(),
                      facts={"sponsor_name": "Dana Okoye"})
    for part in ("reframe", "diagnostic", "benchmark_teaser", "named_buyer",
                 "value_give_away", "recommended_service", "take_control_ask"):
        assert pack.get(part), f"missing {part}"
    assert pack["deal_value"] is None               # facts-only (decision #4)
    assert pack["named_buyer"]["buyer"] == "Dana Okoye"
    md = EP.render_markdown(pack)
    # Facts-only: no VMA fee/price quoted (the "£3bn revenue" benchmark
    # threshold is legitimate context, not a fee — so check for pricing
    # language, not the £ glyph).
    low = md.lower()
    assert not any(t in low for t in ("fee", "retainer", "day rate",
                                      "/day", "per day"))
    assert md.count("## ") == 7                      # seven labelled sections
    assert "Network Rail" in md                      # the proof anchor


def test_evidence_pack_reframe_is_a_hypothesis_not_an_assertion():
    # Credibility guardrail (§9): benchmark-anchored hypothesis language.
    pack = EP.compose(_registry_signal())
    assert "typically" in pack["reframe"].lower()


# --------------------------------------------- orchestration

def test_originate_ranks_and_caps(monkeypatch):
    import tool.advisory_signals as AS
    sigs = [_registry_signal(company=f"Co{i}") for i in range(7)]
    monkeypatch.setattr(AS, "pay_gap_action_signals", lambda **k: sigs)
    facts = {"sponsor_name": "X", "warm_route": {"note": "y"}}
    rows = AS.originate(today=TODAY, facts_for=lambda s: facts, cap=5)
    assert len(rows) == 7
    assert sum(1 for r in rows if r["verdict"] == "PURSUE") == 5
