"""Gender Pay Gap intelligence — the ED&I advisory angle + the Gartner
resourcing benchmark. Enrichment-only (never creates a lead), powered by
the free GOV.UK CSV, a clean no-op when the host is blocked, never raises.

Architecture under test: refresh() is the ONLY network path (brief-time);
lookup() is read-only (cache/memo only), so a dashboard render can never
touch the network. Nothing here hits the live network.
"""
import pytest

from tool import gender_pay_gap as gpg


# A tiny CSV in the real GOV.UK schema (only the columns the module reads).
_CSV = (
    "EmployerName,CompanyNumber,DiffMeanHourlyPercent,DiffMedianHourlyPercent,"
    "EmployerSize,SubmittedAfterTheDeadline,CompanyLinkToGPGInfo\n"
    # the real service quotes the bands that contain commas
    'Acme Utilities Plc,01234567,30.0,28.4,"5000 to 19,999",False,'
    "https://acme.example/gpg\n"
    "Bright Retail Ltd,07654321,4.0,3.1,250 to 499,True,\n"
    "Even Steven Group,09999999,1.0,0.5,1000 to 4999,False,\n"
    # files under the legal entity, not the trading name "BT Group"
    'British Telecommunications Public Limited Company,01800000,28.0,26.0,'
    '"5000 to 19,999",False,https://bt.example/gpg\n'
)


class _Resp:
    status_code = 200

    def __init__(self, text):
        self.text = text


def _fetch_ok(url, **k):
    return _Resp(_CSV)


def _fetch_blocked(url, **k):
    r = _Resp("Host not in allowlist")
    r.status_code = 403
    return r


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    import tool.state_paths as sp
    monkeypatch.setattr(sp, "state_root", lambda profile_key=None: tmp_path)
    gpg._INDEX = None                       # clear the process memo each test
    gpg._INDEX_MTIME = None
    yield tmp_path
    gpg._INDEX = None
    gpg._INDEX_MTIME = None


def _seed(fetch=_fetch_ok):
    """Build the index in the (tmp) state dir, the way the brief would."""
    gpg.refresh(fetch=fetch, force=True)


# ---- download + index + match ---------------------------------------
def test_lookup_matches_by_name_and_suffix_strip(state):
    _seed()
    rec = gpg.lookup("Acme Utilities")        # no 'Plc' suffix
    assert rec and rec["employer"] == "Acme Utilities Plc"
    assert rec["median"] == 28.4 and rec["size"] == "5000 to 19,999"
    assert gpg.lookup("Bright Retail Ltd")["late"] is True   # exact norm hit


def test_lookup_matches_by_company_number(state):
    _seed()
    rec = gpg.lookup("Totally Different Name", company_number="07654321")
    assert rec and rec["employer"] == "Bright Retail Ltd"


def test_unknown_company_returns_none(state):
    _seed()
    assert gpg.lookup("Nonexistent Co") is None


def test_lookup_without_a_built_index_is_a_clean_noop(state):
    # No refresh() has run → no cache → read-only lookup never fetches.
    assert gpg.lookup("Acme Utilities") is None


def test_blocked_host_is_a_clean_noop(state):
    # 403 (host not on egress allowlist) → no records, no raise, None.
    gpg.refresh(fetch=_fetch_blocked, force=True)
    assert gpg.lookup("Acme Utilities") is None
    # a short-TTL miss marker is cached so we don't re-hit every run
    assert (state / "gender_pay_gap.json").exists()


def test_refresh_respects_the_cache_ttl(state):
    calls = []

    def counting(url, **k):
        calls.append(url)
        return _Resp(_CSV)

    gpg.refresh(fetch=counting, force=True)
    n = len(calls)
    assert n >= 1
    gpg._INDEX = None                       # new process, warm disk cache
    gpg.refresh(fetch=counting)             # cache still fresh → no refetch
    assert len(calls) == n
    assert gpg.lookup("Acme Utilities")     # served from the cache


# ---- ED&I angle: fires only on a real, evidenced problem ------------
def test_edi_angle_fires_on_a_wide_gap(state):
    _seed()
    a = gpg.edi_angle(gpg.lookup("Acme Utilities"))
    assert a and a["cls"] == "edi-bad"        # 28.4% is very wide
    assert "28.4%" in a["line"] and "ED&I advisory" in a["line"]
    assert "2027" in a["line"]                # the mandatory-action-plan stakes
    assert a["url"] == "https://acme.example/gpg"


def test_edi_angle_fires_on_a_late_filing_even_if_gap_is_small(state):
    _seed()
    a = gpg.edi_angle(gpg.lookup("Bright Retail Ltd"))   # 3.1%, but late
    assert a and a["cls"] == "edi-bad" and "deadline" in a["line"]


def test_edi_angle_silent_on_a_small_ontime_gap(state):
    _seed()
    assert gpg.edi_angle(gpg.lookup("Even Steven Group")) is None
    assert gpg.edi_angle(None) is None


def test_edi_angle_marketing_desk_language(state):
    _seed()
    a = gpg.edi_angle(gpg.lookup("Acme Utilities"), marketing=True)
    assert "inclusive-marketing" in a["line"]


# ---- resourcing benchmark: the Gartner ratio -----------------------
def test_benchmark_applies_gartner_ratio_to_the_size_band(state):
    _seed()
    b = gpg.resourcing_benchmark(gpg.lookup("Acme Utilities"))   # 5000-19,999
    # band midpoint 12,000 × {1,4}/1000 → 12–48 comms professionals
    assert b and "12–48" in b["line"] and b["band"] == "5000 to 19,999"
    assert "Gartner 2024" in b["line"] and "benchmarking" in b["line"]


def test_benchmark_smallest_band_floors_at_one(state):
    _seed()
    b = gpg.resourcing_benchmark(gpg.lookup("Bright Retail Ltd"))  # 250-499→375
    assert b and "1–2" in b["line"]           # max(1, round(0.375))–round(1.5)


def test_benchmark_marketing_desk_language(state):
    _seed()
    b = gpg.resourcing_benchmark(gpg.lookup("Even Steven Group"), marketing=True)
    assert "marketing functions" in b["line"]


def test_benchmark_none_when_size_unknown(state):
    assert gpg.resourcing_benchmark({"size": "Not Provided"}) is None
    assert gpg.resourcing_benchmark(None) is None


# ---- wiring: the console rows carry the enrichment ------------------
def test_build_mr_rows_attaches_edi_and_benchmark(state):
    from tool.dashboard import _build_mr_rows
    _seed()                                   # build the index in tmp state
    g = {"presented": True, "confidence": "High", "reasons": [],
         "recheck_days": None, "investigate": False,
         "evidence": {"families": 3, "primary": 1, "credible": 1,
                      "level": "full"},
         "kill": "", "move": "", "cap": 7, "throttled": False}
    rows = [{"_kind": "predictor", "company": "Acme Utilities",
             "pid": "acme", "strength": "high", "window_label": "~6-12 wks",
             "predicted_role": "Head of Comms", "gate": g, "verdict": "",
             "events": [{"trigger_key": "ceo_change",
                         "trigger_label": "CEO change",
                         "published": "2026-05-01T00:00:00+00:00",
                         "evidence": "x", "url": "http://a"}]}]
    bd, _ = _build_mr_rows(rows, [], "Head of Communications", cap=7)
    r = bd[0]
    assert r["edi"]["cls"] == "edi-bad"
    assert r["benchmark"]["band"] == "5000 to 19,999"


def test_render_does_not_fetch_when_no_cache(state):
    # The critical guard: _build_mr_rows must NOT trigger a network build.
    import tool.gender_pay_gap as _g
    called = {"n": 0}
    import tool.sources._http as _http
    monkey = pytest.MonkeyPatch()
    monkey.setattr(_http, "get", lambda *a, **k: called.__setitem__(
        "n", called["n"] + 1) or _Resp(_CSV))
    try:
        from tool.dashboard import _build_mr_rows
        g = {"presented": True, "reasons": [], "evidence": {}, "cap": 7}
        rows = [{"_kind": "predictor", "company": "Acme Utilities",
                 "pid": "acme", "strength": "high", "gate": g,
                 "events": [{"trigger_key": "ceo_change",
                             "trigger_label": "CEO change", "evidence": "x"}]}]
        _build_mr_rows(rows, [], "Head of Communications", cap=7)
        assert called["n"] == 0               # render never fetched
    finally:
        monkey.undo()


def test_engine_template_renders_edi_and_benchmark_rows():
    from tool.engine_page import ENGINE_TEMPLATE as T
    for token in ("ED&amp;I ANGLE", "RESOURCING BENCHMARK",
                  "svcchip.edi-bad", "svcchip.benchmark", "l.edi", "l.benchmark"):
        assert token in T, token


# ---- Change A: a missing dependency is diagnosed, not masked --------
def test_missing_dependency_is_diagnosed_not_masked(state, monkeypatch, caplog):
    import logging

    def _boom(fetch):
        raise ImportError("No module named 'lxml'")
    monkeypatch.setattr(gpg, "_resolve_get", _boom)
    with caplog.at_level(logging.WARNING, logger="brief.gpg"):
        idx = gpg.refresh(force=True)
    assert not idx.get("by_norm")             # no index built, no raise
    assert gpg.lookup("Acme Utilities") is None
    # logged DISTINCTLY (a dependency hint), not as a generic per-year miss
    assert any("pip install" in r.getMessage() for r in caplog.records)


# ---- Change B: CH number matching recovers a legal-entity name miss -
def test_number_matching_recovers_a_legal_entity_name_miss(state, monkeypatch):
    from tool.dashboard import _build_mr_rows
    from tool.sources import companies_house as ch
    _seed()
    # "BT Group" never name-matches the filed legal entity on its own…
    assert gpg.lookup("BT Group") is None
    # …but the cached CH number bridges it (read-only, no API call).
    monkeypatch.setattr(ch, "cached_number_map",
                        lambda: {"BT Group": "01800000"})
    g = {"presented": True, "confidence": "High", "reasons": [],
         "recheck_days": None, "investigate": False,
         "evidence": {"families": 3, "primary": 1, "credible": 1,
                      "level": "full"},
         "kill": "", "move": "", "cap": 7, "throttled": False}
    rows = [{"_kind": "predictor", "company": "BT Group", "pid": "bt",
             "strength": "high", "window_label": "~6-12 wks",
             "predicted_role": "Head of Comms", "gate": g, "verdict": "",
             "events": [{"trigger_key": "ceo_change",
                         "trigger_label": "CEO change",
                         "published": "2026-05-01T00:00:00+00:00",
                         "evidence": "x", "url": "http://a"}]}]
    bd, _ = _build_mr_rows(rows, [], "Head of Communications", cap=7)
    r = bd[0]
    assert r["edi"]["cls"] == "edi-bad"       # 26% via the number match
    assert r["benchmark"]["band"] == "5000 to 19,999"


def test_cached_number_map_is_read_only_and_never_calls_the_api(
        state, monkeypatch):
    from tool.sources import companies_house as ch
    # _load_watchlist is the cache read; _resolve_to_canonical is the
    # network path. The map must use ONLY the former.
    monkeypatch.setattr(ch, "_load_watchlist",
                        lambda: {"Tesco": {"number": "00445790"},
                                 "Ghost": {"number": None}})
    monkeypatch.setattr(ch, "_resolve_to_canonical",
                        lambda n: (_ for _ in ()).throw(
                            AssertionError("network must not be touched")))
    m = ch.cached_number_map()
    assert m == {"Tesco": "00445790"}         # None-number entry dropped


# ---- the delivery fix: the brief must ship the state to the dashboard
def test_brief_artifacts_deliver_gpg_state_to_the_dashboard():
    from pathlib import Path
    import tool
    root = Path(tool.__file__).resolve().parent.parent
    comms = (root / ".github/workflows/morning-brief.yml").read_text()
    assert "tool/state/gender_pay_gap.json" in comms
    assert "tool/state/ch_watchlist_v2.json" in comms
    mkt = (root / ".github/workflows/marketing-brief.yml").read_text()
    assert "tool/state/marketing/gender_pay_gap.json" in mkt
    assert "tool/state/marketing/ch_watchlist_v2.json" in mkt
