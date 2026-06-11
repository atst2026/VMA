"""Tests for the edge detectors (tool/edge_detectors.py) — interim-to-perm
watch, follow-on build-out, and stated-intent phrase matching."""
from datetime import datetime, timedelta, timezone

import pytest

from tool import edge_detectors as ed

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Point the watch store at a temp file and silence the repo push so
    tests never touch real state."""
    monkeypatch.setattr(ed, "STATE_DIR", tmp_path)
    monkeypatch.setattr(ed, "WATCH_FILE", tmp_path / "edge_watches.json")
    import tool.github_state as gs
    monkeypatch.setattr(gs, "push_async", lambda *a, **k: None)


def _signal(title, company="Acme Plc", **kw):
    base = {"title": title, "company": company,
            "url": "https://example.com/job", "source": "LinkedIn Jobs",
            "published": (NOW - timedelta(days=1)).isoformat(),
            "lead_id": "abc123", "kind": "job"}
    base.update(kw)
    return base


def _move(new_co="NewCo Ltd", old_co="OldCo Plc", person="Jane Doe",
          role="Director of Communications", **kw):
    base = {"new_company": new_co, "old_company": old_co,
            "person_name": person, "role": role,
            "article_url": "https://prweek.com/move", "source": "PRWeek",
            "article_date": (NOW - timedelta(days=2)).isoformat(),
            "detected_at": (NOW - timedelta(days=2)).isoformat(),
            "event_id": "ev1", "new_co_status": "active"}
    base.update(kw)
    return base


# ====================================================================
# Interim-to-perm watch
# ====================================================================
def test_senior_interim_title_detected():
    rows = ed.detect_interim_covers(
        [_signal("Interim Head of Communications")])
    assert len(rows) == 1
    r = rows[0]
    assert r["_kind"] == "interim_watch"
    assert r["company"] == "Acme Plc"
    assert r["fid"] == "interim_abc123"
    assert r["window_label"] == ed.INTERIM_WINDOW
    assert r["events"][0]["trigger_key"] == "interim_watch"
    assert r["first_seen"]          # persisted anchor stamped on emit


def test_ftc_and_maternity_forms_detected():
    rows = ed.detect_interim_covers([
        _signal("Communications Director (12-month FTC)", lead_id="a"),
        _signal("Head of Marketing - Maternity Cover", lead_id="b"),
        _signal("Director of Corporate Affairs, fixed term", lead_id="c"),
    ])
    assert len(rows) == 3


def test_junior_interim_and_permanent_senior_skipped():
    rows = ed.detect_interim_covers([
        _signal("Interim Communications Officer"),       # not senior
        _signal("Head of Communications"),               # senior, not interim
        _signal("Interim Head of Comms", company=""),    # no company
    ])
    assert rows == []


def test_watch_outlives_its_source_signal():
    sig = _signal("Interim Head of Communications")
    assert len(ed.detect_interim_covers([sig])) == 1
    # The job ad rotates out of latest_signals (7-day rule) — the watch
    # must still emit from the persisted store.
    rows = ed.detect_interim_covers([])
    assert len(rows) == 1 and rows[0]["fid"] == "interim_abc123"


def test_expired_watch_clears(monkeypatch):
    ed.detect_interim_covers([_signal("Interim Head of Communications")])
    from tool import bd_retention
    real_is_expired = bd_retention.is_expired
    monkeypatch.setattr(bd_retention, "is_expired", lambda *a, **k: True)
    assert ed.detect_interim_covers([]) == []
    # and it was pruned from the store, not just hidden
    monkeypatch.setattr(bd_retention, "is_expired", real_is_expired)
    assert ed.detect_interim_covers([]) == []


# ====================================================================
# Follow-on build-out
# ====================================================================
def test_two_sided_move_emits_watch_on_new_company():
    rows = ed.detect_follow_on([_move()])
    assert len(rows) == 1
    r = rows[0]
    assert r["_kind"] == "follow_on"
    assert r["company"] == "NewCo Ltd"
    assert r["fid"] == "followon_ev1"
    assert "Jane Doe" in r["evidence"] and "OldCo Plc" in r["evidence"]
    assert r["events"][0]["trigger_key"] == "follow_on"


def test_one_sided_or_triaged_moves_skipped():
    rows = ed.detect_follow_on([
        _move(old_co=""),                       # cascade row already covers it
        _move(new_co="", event_id="ev2"),       # departure only
        _move(new_co_status="dismissed", event_id="ev3"),
        _move(person="", event_id="ev4"),
    ])
    assert rows == []


# ====================================================================
# Stated intent
# ====================================================================
@pytest.mark.parametrize("text,hit", [
    ("The raise will fund investment in our brand and product.", True),
    ("We are scaling up our marketing team across Europe.", True),
    ("The company plans to strengthen communications ahead of the IPO.", True),
    ("They are searching for a new Head of Corporate Affairs.", True),
    ("The group has kicked off a creative review.", True),
    ("Revenue grew 40% year on year.", False),
    ("", False),
    (None, False),
])
def test_intent_phrases(text, hit):
    got = ed.intent_phrase(text)
    assert bool(got) is hit


def test_intent_returns_the_companys_own_words():
    got = ed.intent_phrase("Proceeds will be used to invest in our "
                           "communications function and open two offices.")
    assert got.lower().startswith("invest in our communications")


# ====================================================================
# Scoring integration — the new trigger keys are first-class citizens
# ====================================================================
def test_keys_in_both_desk_taxonomies():
    from tool import lead_engine
    for key in ("interim_watch", "follow_on"):
        assert key in lead_engine._COMMS_TAXONOMY
        assert key in lead_engine._MKT_TAXONOMY


def test_interim_counts_as_live_seat_in_gate():
    from tool import gate
    assert "interim_watch" in gate.SEAT_LIVE_KEYS
    assert "follow_on" in gate.SEAT_IMMINENT_KEYS


def test_fee_driver_classifies_new_kinds():
    from tool import why_now
    label, _ = why_now.fee_driver(["interim_watch"])
    assert label == "Vacated seat"
    label, _ = why_now.fee_driver(["follow_on"])
    assert label == "Leadership reset"
