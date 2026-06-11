"""Demand-side build 2: the semantic signal reader. No network — the
model call is injected; the no-key path must be a silent no-op."""
from datetime import datetime, timezone

import tool.semantic_scan as SEM


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(SEM, "_seen_file", lambda: tmp_path / "seen.json")
    import tool.account_match as AM
    monkeypatch.setattr(
        AM, "classify_account",
        lambda cand, *texts: ((cand, "watchlist")
                              if cand in ("IMI", "Severn Trent")
                              else (None, "")))


def _sig(i, title, kind="news"):
    return {"id": f"s{i}", "kind": kind, "title": title,
            "source": "Financial Times", "url": f"https://ft.com/{i}",
            "published": "2026-06-10T00:00:00+00:00"}


def test_semantic_finds_map_to_existing_triggers(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    signals = [
        _sig(0, "IMI chief says growth plan will mean significant "
                "investment in brand and reputation"),
        _sig(1, "Severn Trent quarterly water update"),
        _sig(2, "A job ad", kind="job"),          # never sent to the model
    ]
    def fake_call(lines):
        assert len(lines) == 2                     # job signal excluded
        return {"leads": [
            {"index": 0, "company": "IMI", "trigger_key": "restructure",
             "confidence": "high",
             "rationale": "CEO commits to brand investment"},
            {"index": 1, "company": "Severn Trent", "trigger_key": "none",
             "confidence": "high", "rationale": "routine"},
        ]}
    events = SEM.detect(signals, call=fake_call)
    assert len(events) == 1
    e = events[0]
    assert e.trigger_key == "restructure"          # an EXISTING priced key
    assert e.company == "IMI"
    assert e.evidence.startswith("AI read: CEO commits to brand investment")
    assert e.account_tier == "watchlist"


def test_low_confidence_and_unresolved_companies_drop(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    def fake_call(lines):
        return {"leads": [
            {"index": 0, "company": "IMI", "trigger_key": "mna",
             "confidence": "low", "rationale": "maybe"},
            {"index": 1, "company": "Unknown Co", "trigger_key": "mna",
             "confidence": "high", "rationale": "sure"},
        ]}
    events = SEM.detect([_sig(0, "a"), _sig(1, "b")], call=fake_call)
    assert events == []


def test_each_headline_read_once_ever(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    calls = []
    def fake_call(lines):
        calls.append(lines)
        return {"leads": []}
    SEM.detect([_sig(0, "headline")], call=fake_call)
    SEM.detect([_sig(0, "headline")], call=fake_call)   # already seen
    assert len(calls) == 1


def test_no_api_key_is_a_silent_noop(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # default call path returns None without a key; detect returns []
    assert SEM.detect([_sig(0, "headline")]) == []


def test_invented_trigger_keys_are_rejected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    def fake_call(lines):
        return {"leads": [{"index": 0, "company": "IMI",
                           "trigger_key": "brand_vibes_shift",
                           "confidence": "high", "rationale": "x"}]}
    assert SEM.detect([_sig(0, "a")], call=fake_call) == []
