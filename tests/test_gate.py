"""Tests for the presentation gate (tool/gate.py) — the hard rules between
the pipeline and the board."""
from datetime import datetime, timedelta, timezone

from tool import gate

NOW = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)


def _ev(url="", source="", key="ceo_change", days_ago=35):
    return {"trigger_key": key, "trigger_label": key.replace("_", " "),
            "url": url, "source": source,
            "published": (NOW - timedelta(days=days_ago)).isoformat()}


def _lead(action="call_today", **kw):
    base = {
        "action": action, "conflict": False, "anti_triggers": [],
        "premature": False, "contradictions": [],
        "fresh_hold_days": 21, "freshest_age_days": 35.0,
        "who_to_call": "Incoming CEO's office / CHRO",
        "access_text": "A new leader has just landed, so the supplier "
                       "relationship is open.",
        "triggers": [
            {"key": "ceo_change", "label": "CEO change", "recency_mult": 0.9,
             "age_days": 35.0, "url": "https://investegate.co.uk/x"},
            {"key": "funding", "label": "Funding round", "recency_mult": 0.8,
             "age_days": 20.0, "url": "https://techcrunch.com/y"},
        ],
    }
    base.update(kw)
    return base


def _full_events():
    return [
        _ev("https://www.investegate.co.uk/announcement/1", "LSE RNS (Investegate)"),
        _ev("https://techcrunch.com/2", "TechCrunch", key="funding", days_ago=20),
        _ev("https://www.ft.com/3", "Financial Times", key="funding", days_ago=18),
    ]


# ====================================================================
# Evidence independence
# ====================================================================
def test_three_families_with_primary_is_full():
    ev = gate.source_evidence(_full_events())
    assert ev == {"families": 3, "primary": 1, "credible": 2, "level": "full"}


def test_same_host_counts_once_and_thin_detected():
    ev = gate.source_evidence([
        _ev("https://techcrunch.com/a"), _ev("https://techcrunch.com/b")])
    assert ev["families"] == 1 and ev["level"] == "thin"


def test_two_families_with_credible_is_partial():
    ev = gate.source_evidence([
        _ev("https://www.bbc.co.uk/news/1", "BBC"),
        _ev("", "Google News RSS")])
    assert ev["families"] == 2 and ev["level"] == "partial"


def test_source_label_fallback_when_no_url():
    ev = gate.source_evidence([_ev("", "Companies House officer filing")])
    assert ev["families"] == 1 and ev["primary"] == 1


# ====================================================================
# Window state
# ====================================================================
def test_window_open_then_lapsed():
    # ceo_change lead time max is 12 weeks.
    assert gate.window_state([_ev(days_ago=35)], now=NOW)[0] == "open"
    assert gate.window_state([_ev(days_ago=12 * 7 + 10)], now=NOW)[0] == "lapsed"


def test_window_unknown_never_blocks():
    state, _ = gate.window_state([{"trigger_key": "nonexistent"}], now=NOW)
    assert state == "unknown"


# ====================================================================
# Acceptance + auto-throttle
# ====================================================================
def _verdicts(n_accept, n_reject, days_ago=1):
    d = (NOW - timedelta(days=days_ago)).isoformat()
    return ([{"date": d, "verdict": "call_today"}] * n_accept
            + [{"date": d, "verdict": "reject"}] * n_reject)


def test_throttle_trips_below_floor_with_sample():
    a = gate.acceptance(_verdicts(4, 8), now=NOW)
    assert a["n"] == 12 and a["throttled"] and a["cap"] == gate.THROTTLED_CAP


def test_no_throttle_under_min_sample_or_above_floor():
    assert not gate.acceptance(_verdicts(2, 6), now=NOW)["throttled"]  # n=8
    assert not gate.acceptance(_verdicts(8, 4), now=NOW)["throttled"]  # 66%


def test_old_verdicts_age_out():
    a = gate.acceptance(_verdicts(0, 20, days_ago=10), now=NOW)
    assert a["n"] == 0 and not a["throttled"]


# ====================================================================
# The gate decision, rule by rule
# ====================================================================
def test_presented_high_with_full_stack():
    g = gate.assess({"company": "Acme", "events": _full_events()},
                    _lead(), now=NOW)
    assert g["presented"] and g["confidence"] == "High"
    assert g["reasons"] == []
    assert "interim" in g["kill"].lower()
    assert "Ring Incoming CEO" in g["move"]


def test_partial_evidence_presents_moderate():
    events = [_ev("https://www.investegate.co.uk/1", "RNS"),
              _ev("https://techcrunch.com/2", key="funding", days_ago=20)]
    g = gate.assess({"company": "Acme", "events": events}, _lead(), now=NOW)
    assert g["presented"] and g["confidence"] == "Moderate"


def test_conflict_never_presents():
    g = gate.assess({"company": "Rival Search", "events": _full_events()},
                    _lead(conflict=True), now=NOW)
    assert not g["presented"] and "Competing recruiter" in g["reasons"][0]


def test_hard_blocker_queues_with_recheck():
    g = gate.assess({"company": "Acme", "events": _full_events()},
                    _lead(anti_triggers=["administration"]), now=NOW)
    assert not g["presented"] and g["recheck_days"] == 30
    assert "administration" in g["reasons"][0]


def test_amplifier_only_stack_never_presents():
    lead = _lead(triggers=[{"key": "press_velocity_spike", "label": "Velocity",
                            "recency_mult": 0.9, "age_days": 3.0}])
    g = gate.assess({"company": "Acme", "events": _full_events()}, lead, now=NOW)
    assert not g["presented"] and "Amplifier-only" in g["reasons"][0]


def test_lapsed_window_queues():
    events = [_ev(days_ago=12 * 7 + 20)]
    g = gate.assess({"company": "Acme", "events": events}, _lead(), now=NOW)
    assert not g["presented"] and "lapsed" in g["reasons"][0]


def test_too_fresh_gets_window_opening_recheck():
    lead = _lead(premature=True, fresh_hold_days=28, freshest_age_days=10.0)
    g = gate.assess({"company": "Acme", "events": _full_events()}, lead, now=NOW)
    assert not g["presented"] and g["recheck_days"] == 18
    assert "window opens" in g["reasons"][0]


def test_monitor_grade_hidden_and_investigate_flagged():
    g1 = gate.assess({"company": "A", "events": _full_events()},
                     _lead(action="monitor"), now=NOW)
    assert not g1["presented"] and not g1["investigate"]
    g2 = gate.assess({"company": "A", "events": _full_events()},
                     _lead(action="investigate"), now=NOW)
    assert not g2["presented"] and g2["investigate"]


def test_thin_evidence_queues_for_investigation():
    g = gate.assess({"company": "A", "events": [_ev()]}, _lead(), now=NOW)
    assert not g["presented"] and g["investigate"]
    assert "thin" in g["reasons"][0].lower()


def test_throttle_raises_bar_to_full():
    events = [_ev("https://www.investegate.co.uk/1", "RNS"),
              _ev("https://techcrunch.com/2", key="funding", days_ago=20)]
    ok = gate.assess({"company": "A", "events": events}, _lead(), now=NOW)
    assert ok["presented"]  # partial passes when healthy
    g = gate.assess({"company": "A", "events": events}, _lead(),
                    verdicts=_verdicts(4, 8), now=NOW)
    assert not g["presented"] and g["throttled"]
    assert g["cap"] == gate.THROTTLED_CAP


def test_investigation_overlay_overrides_both_ways():
    killed = gate.assess({"company": "A", "events": _full_events()}, _lead(),
                         investigation={"verdict": "killed", "note": "interim cover"},
                         now=NOW)
    assert not killed["presented"] and "Killed by investigation" in killed["reasons"][0]
    confirmed = gate.assess({"company": "A", "events": [_ev()]},
                            _lead(action="investigate"),
                            investigation={"verdict": "confirmed"}, now=NOW)
    assert confirmed["presented"] and confirmed["confidence"] == "High"


def test_malformed_input_queues_instead_of_raising():
    g = gate.assess({}, {}, now=NOW)
    assert not g["presented"] and g["reasons"]
