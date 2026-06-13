"""Exclusionary job-spec scan (tool/job_spec_scan.py) — the deterministic
ED&I conversation-opener.

Locks down: the coded-word lexicon scan, the two independent hooks
(masculine skew vs. missing accessibility language), the conversation-
opener framing (never a verdict), the thresholds that keep it quiet on
clean / title-only input, per-company aggregation, the store/load
round-trip, and the never-raises contract.
"""
import pytest

from tool import job_spec_scan as J


@pytest.fixture(autouse=True)
def _comms_default(monkeypatch):
    monkeypatch.delenv("VMA_PROFILE", raising=False)


def _job(company, title, summary="", url="https://jobs.example/x"):
    return {"kind": "job", "company": company, "title": title,
            "summary": summary, "url": url}


# ----------------------------------------------------------- lexicon scan

def test_scan_text_flags_masculine_and_feminine_and_inclusion():
    s = J.scan_text("An aggressive, dominant, competitive leader. We support "
                    "a collaborative team and welcome flexible working.")
    assert s["masculine_count"] >= 3          # aggress, domina, compet, lead
    assert s["feminine_count"] >= 2            # support, collab
    assert s["has_inclusion_language"] is True  # "flexible working"


def test_scan_text_empty_is_safe():
    s = J.scan_text("")
    assert s == {"masculine": [], "feminine": [], "masculine_count": 0,
                 "feminine_count": 0, "has_inclusion_language": False}


# --------------------------------------------------------- masculine skew

def test_masculine_skew_emits_opener_framed_as_opener():
    # Strong masculine lean, no feminine words, short body (so the
    # no-inclusion hook can't also fire) — isolates the skew path.
    sig = _job("Acme Plc", "Comms Lead",
               "We want an aggressive, dominant, competitive, fearless, "
               "driven, decisive operator.")
    out = J.edi_job_spec_angle([sig])
    assert "Acme Plc" in out
    ang = out["Acme Plc"]
    # Conversation-opener, never a verdict.
    assert ang["line"].startswith("Conversation-opener")
    assert "verify against the live ad" in ang["line"]
    assert "skews masculine-coded" in ang["line"]
    assert "no accessibility" not in ang["line"]   # skew hook only
    assert ang["short"] == "ED&I"
    assert "job spec" in ang["label"]
    assert ang["masculine_terms"]                  # the matched terms surface


def test_one_masculine_word_does_not_trip():
    # A single coded word is below threshold — no opener.
    sig = _job("Quiet Co", "Communications Manager",
               "A driven communications manager for a friendly team.")
    assert J.edi_job_spec_angle([sig]) == {}


# ------------------------------------------------------ no inclusion hook

_NEUTRAL_BODY = (
    "The successful candidate will manage the press office, write briefings, "
    "build relationships with journalists, oversee the editorial calendar, "
    "and report to the corporate affairs team. You will coordinate output, "
    "manage agency partners, and ensure consistent messaging across "
    "channels. Previous experience in a corporate or agency setting is "
    "expected. This is a full-time permanent role based in our central "
    "office with occasional travel between regional sites."
)


def test_missing_accessibility_language_emits_opener():
    assert len(_NEUTRAL_BODY) >= J._MIN_BODY_CHARS   # real body to judge on
    out = J.edi_job_spec_angle([_job("Beta Ltd", "Head of Comms",
                                     _NEUTRAL_BODY)])
    assert "Beta Ltd" in out
    line = out["Beta Ltd"]["line"]
    assert "no accessibility / accommodation statement" in line
    assert out["Beta Ltd"]["has_inclusion_language"] is False


def test_inclusive_ad_is_silent():
    body = (_NEUTRAL_BODY + " We welcome applications from under-represented "
            "groups and offer flexible working and reasonable adjustments.")
    assert J.edi_job_spec_angle([_job("Gamma Plc", "Head of Comms", body)]) == {}


def test_title_only_sources_cannot_trip_no_inclusion():
    # ATS sources carry the LOCATION in `summary`, not the body — short, so
    # body_chars never reaches the gate and a clean title is silent.
    sigs = [_job("Delta Co", "Communications Manager", "London, UK"),
            _job("Delta Co", "Senior Communications Officer", "Leeds, UK")]
    assert J.edi_job_spec_angle(sigs) == {}


# ------------------------------------------------------------ aggregation

def test_ads_are_aggregated_per_company():
    sigs = [_job("Echo Plc", "Comms Lead", "aggressive dominant"),
            _job("Echo Plc", "Head of PR",
                 "competitive fearless driven decisive")]
    out = J.edi_job_spec_angle(sigs)
    assert "Echo Plc" in out
    assert out["Echo Plc"]["ads"] == 2


# ------------------------------------------------------------- robustness

def test_non_jobs_blank_companies_and_bad_input_are_ignored():
    assert J.edi_job_spec_angle(None) == {}
    assert J.edi_job_spec_angle([]) == {}
    assert J.edi_job_spec_angle([
        {"kind": "news", "company": "Acme", "title": "aggressive dominant"},
        _job("", "aggressive dominant competitive fearless"),   # no company
        "not-a-dict",
    ]) == {}


# -------------------------------------------------------- store / load

def test_store_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_store_path", lambda: tmp_path / "job_spec.json")
    assert J.load_job_spec_flags() == {}          # nothing written yet
    n = J.scan_and_store([_job("Acme Plc", "Comms Lead",
                               "aggressive dominant competitive fearless "
                               "driven decisive")])
    assert n == 1
    flags = J.load_job_spec_flags()
    assert "Acme Plc" in flags
    assert flags["Acme Plc"]["short"] == "ED&I"


def test_store_is_noop_safe_on_bad_input(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_store_path", lambda: tmp_path / "job_spec.json")
    assert J.scan_and_store(None) == 0
    assert J.load_job_spec_flags() == {}
