"""Resourcing-benchmark outlier scan (tool/resourcing_outlier.py) — the
proactive benchmarking lead list.

Locks down: the under/over/matched/benchmark classification against the
expected FTE range, the honest benchmark-only path when no headcount is
measured, outlier-first ranking with the pay-gap co-signal lift, the GPG
adapter (co-signal derived from a wide/late gap), the store/load round-trip
and the never-raises contract.
"""
import pytest

from tool import resourcing_outlier as R


@pytest.fixture(autouse=True)
def _comms_default(monkeypatch):
    monkeypatch.delenv("VMA_PROFILE", raising=False)


# Band "5000 to 19,999" → midpoint 12,000 → expected 12–48 comms FTE.
_BIG = "5000 to 19,999"


# --------------------------------------------------------- classification

def test_assess_classifies_against_the_expected_range():
    assert R.assess("A", _BIG, observed=5)["status"] == "under"      # 5 < 12
    assert R.assess("A", _BIG, observed=100)["status"] == "over"     # > 48
    assert R.assess("A", _BIG, observed=30)["status"] == "matched"   # 12–48
    assert R.assess("A", _BIG, observed=None)["status"] == "benchmark"


def test_assess_unknown_band_is_none():
    assert R.assess("A", "Not Provided") is None
    assert R.assess("A", None) is None


def test_assess_lines_match_status_and_co_signal():
    under = R.assess("Acme", _BIG, observed=4)
    assert "under-resourced" in under["line"]
    assert under["expected_label"] == "12–48"
    bench = R.assess("Beta", _BIG)
    assert "peer comms function" in bench["line"]
    assert "ED&I" not in bench["line"]
    stacked = R.assess("Gamma", _BIG, co_signal=True)
    assert "ED&I" in stacked["line"]


def test_marketing_desk_language():
    assert "marketing" in R.assess("A", _BIG, marketing=True)["line"]


# ------------------------------------------------------------- ranking

def test_outliers_rank_before_benchmark_only():
    rows = R.resourcing_outliers([
        {"company": "BenchCo", "band": _BIG},                 # benchmark
        {"company": "UnderCo", "band": _BIG, "observed": 3},  # under
    ])
    assert [r["company"] for r in rows][0] == "UnderCo"


def test_co_signal_lifts_within_a_tier():
    rows = R.resourcing_outliers([
        {"company": "Quiet", "band": _BIG},
        {"company": "Scrutinised", "band": _BIG, "co_signal": True},
    ])
    assert rows[0]["company"] == "Scrutinised"


def test_larger_expected_function_ranks_first_in_a_tier():
    rows = R.resourcing_outliers([
        {"company": "Small", "band": "250 to 499"},
        {"company": "Large", "band": "20,000 or more"},
    ])
    assert rows[0]["company"] == "Large"


def test_unknown_bands_dropped_and_companies_deduped():
    rows = R.resourcing_outliers([
        {"company": "NoBand", "band": "Not Provided"},
        {"company": "Dup", "band": _BIG},
        {"company": "Dup", "band": _BIG},
        {"company": "", "band": _BIG},
        "not-a-dict",
    ])
    assert [r["company"] for r in rows] == ["Dup"]


def test_empty_and_none_universe_is_safe():
    assert R.resourcing_outliers([]) == []
    assert R.resourcing_outliers(None) == []


# ------------------------------------------------------- GPG adapter

def _recs():
    return [
        {"employer": "Wide Plc", "size": _BIG, "median": 30.0, "late": False},
        {"employer": "OnTime Ltd", "size": _BIG, "median": 5.0, "late": False},
        {"employer": "Late Co", "size": "250 to 499", "median": 4.0,
         "late": True},
        {"employer": "Unknown Inc", "size": "Not Provided", "median": 40.0},
    ]


def test_scan_from_gpg_derives_co_signal_and_ranks(monkeypatch):
    import tool.gender_pay_gap as gpg
    monkeypatch.setattr(gpg, "all_records", lambda: _recs())
    rows = R.scan_from_gpg()
    names = [r["company"] for r in rows]
    assert "Unknown Inc" not in names                  # unknown band dropped
    # Wide Plc (wide gap, big band) leads; OnTime (no co-signal) trails it.
    assert names[0] == "Wide Plc"
    wide = next(r for r in rows if r["company"] == "Wide Plc")
    late = next(r for r in rows if r["company"] == "Late Co")
    assert wide["co_signal"] is True and late["co_signal"] is True
    ontime = next(r for r in rows if r["company"] == "OnTime Ltd")
    assert ontime["co_signal"] is False


def test_scan_from_gpg_empty_when_index_empty(monkeypatch):
    import tool.gender_pay_gap as gpg
    monkeypatch.setattr(gpg, "all_records", lambda: [])
    assert R.scan_from_gpg() == []


# -------------------------------------------------------- store / load

def test_store_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_store_path", lambda: tmp_path / "rout.json")
    import tool.gender_pay_gap as gpg
    monkeypatch.setattr(gpg, "all_records", lambda: _recs())
    assert R.load_resourcing_outliers() == []          # nothing written yet
    n = R.scan_and_store()
    assert n == 3                                      # 3 known-band employers
    leads = R.load_resourcing_outliers()
    assert leads and leads[0]["company"] == "Wide Plc"
    assert leads[0]["short"] == "BENCHMARK"
