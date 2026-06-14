"""The Outside-In Function Diagnostic — variable structure + the novelty
gate (ADVISORY_ENGINE.md §11 #2).

Locks: the diagnostic leads with the single sharpest anomaly for THIS
company (not a fixed script); the novelty gate asserts the insight rests
on the non-public comparison (peer cohort / resourcing benchmark), not the
company's own published figures.
"""
import pytest

from tool import advisory_diagnostic as D
from tool.advisory_signals.base import AdvisorySignal


@pytest.fixture(autouse=True)
def _comms_default(monkeypatch):
    monkeypatch.delenv("VMA_PROFILE", raising=False)


def _sig(**extra):
    base = {"size_band": "1000 to 4999", "median": 22.0, "late": False}
    base.update(extra)
    return AdvisorySignal(trigger="PayGapActionMandate", company="Acme Plc",
                          service_mix=["edi", "benchmarking", "coaching"],
                          extra=base)


def test_assemble_context_carries_the_nonpublic_comparison():
    ctx = D.assemble_context(_sig())
    assert ctx["size_band"] == "1000 to 4999"
    assert ctx["expected_comms_fte"] is not None      # the benchmark
    assert "peers" in ctx                              # the cohort key exists


# --------------------------------------------- variable structure (#2)

def test_very_wide_gap_leads_with_pay_gap_exposure():
    d = D.diagnose(_sig(median=30.0))
    assert d["lead_anomaly"] == "pay_gap_exposure"
    assert "board-level" in d["lead_line"].lower()


def test_late_filing_leads_with_governance():
    d = D.diagnose(_sig(median=10.0, late=True))
    assert d["lead_anomaly"] == "governance_process"


def test_modest_gap_with_band_leads_with_resourcing():
    d = D.diagnose(_sig(median=16.0, late=False))
    assert d["lead_anomaly"] == "under_resourcing"
    assert "typically" in d["lead_line"].lower()       # hypothesis language


# --------------------------------------------- the novelty gate (#2)

def test_novelty_gate_passes_on_benchmark_or_peers():
    d = D.diagnose(_sig())
    assert d["novel"] is True                           # has the FTE benchmark


def test_novelty_gate_fails_without_a_nonpublic_comparison():
    # No size band → no benchmark; an unknown company → no peer cohort. The
    # insight would rest only on the company's own figure: not novel.
    bare = AdvisorySignal(trigger="PayGapActionMandate",
                          company="Zzqq Unknownco Ltd",
                          service_mix=["edi"], extra={"median": 20.0})
    d = D.diagnose(bare)
    assert d["novel"] is False


def test_render_is_a_labelled_hypothesis():
    md = D.render(D.diagnose(_sig()))
    assert "hypothesis" in md.lower()
    assert "Network Rail" in md                         # the proof method
