"""Tests for the qualification scorecard (the four AD dimensions), the
verification tag semantics, and the propensity research write-back API."""
from datetime import datetime, timedelta, timezone

import tool.propensity as PR
from tool import gate


def _trig(key, rec=0.9):
    return {"key": key, "label": key, "recency_mult": rec, "age_days": 20.0}


def _lead(keys, **kw):
    base = {"action": "call_today", "conflict": False, "anti_triggers": [],
            "premature": False, "contradictions": [],
            "who_to_call": "CEO office", "access_text": "",
            "relationship": "cold", "financial": {"direction": "neutral"},
            "posture": {"direction": "neutral"},
            "triggers": [_trig(k) for k in keys]}
    base.update(kw)
    return base


def _q(lead, item=None, wstate="open"):
    return gate.qualification(lead, item or {}, {}, wstate)


# ====================================================================
# The four dimensions
# ====================================================================
def test_seat_live_beats_imminent_beats_none():
    assert _q(_lead(["mishire_reversal"]))["seat"] == 2
    assert _q(_lead(["ceo_change"]))["seat"] == 1
    assert _q(_lead(["press_velocity_spike"]))["seat"] == 0


def test_budget_dimension():
    assert _q(_lead(["funding"]))["budget"] == 2
    assert _q(_lead(["ceo_change"],
                    financial={"direction": "pro"}))["budget"] == 2
    assert _q(_lead(["ceo_change"]), {"psl_status": "on"})["budget"] == 2
    assert _q(_lead(["ceo_change"]))["budget"] == 1
    cut = _q(_lead(["ceo_change"], financial={"direction": "anti"}))
    assert cut["budget"] == 0 and "wrong way" in cut["budget_why"]
    assert _q(_lead(["ceo_change"]), {"internal_ta": True})["budget"] == 0


def test_urgency_dimension():
    assert _q(_lead(["crisis_event"]))["urgency"] == 2
    assert _q(_lead(["job_ad_cluster"]))["urgency"] == 2   # live demand
    assert _q(_lead(["ceo_change"]))["urgency"] == 1
    assert _q(_lead(["ceo_change"], premature=True))["urgency"] == 0
    assert _q(_lead(["ceo_change"]), wstate="lapsed")["urgency"] == 0


def test_buyer_dimension_warm_beats_named_cold():
    # AD-room rescore: warmth (tagged route / relationship) = 2; a scraped
    # name and a mapped seat are both 1; nothing = 0.
    assert _q(_lead(["ceo_change"], relationship="warm"))["buyer"] == 2
    assert _q(_lead(["ceo_change"]),
              {"warm_route": {"warm": True}})["buyer"] == 2
    named = _q(_lead(["ceo_change"]), {"seeded_contact_name": "Jane"})
    assert named["buyer"] == 1 and "cold" in named["buyer_why"]
    routed = _q(_lead(["ceo_change"],
                      access_text="New leader, supplier relationship open."))
    assert routed["buyer"] == 1
    assert _q(_lead(["ceo_change"], who_to_call=""))["buyer"] == 0


def test_total_and_weakest():
    q = _q(_lead(["mishire_reversal", "funding"]),
           {"warm_route": {"warm": True}})
    assert q["total"] == 8
    # A named-but-cold contact caps the same stack at 7.
    assert _q(_lead(["mishire_reversal", "funding"]),
              {"seeded_contact_name": "Jane"})["total"] == 7
    q2 = _q(_lead(["ceo_change"]))
    assert q2["total"] == 4 and q2["weakest_why"]


# ====================================================================
# Gold-tier weighting (the entirety of the research quote)
# ====================================================================
def test_gold_tier_triggers_carry_top_weight():
    from tool.predictive import patterns as P
    # Leadership change, M&A, crisis, PE — each with a causal chain.
    assert P.BY_KEY["ceo_change"].weight == 1.0
    assert P.BY_KEY["mna"].weight == 1.0
    assert P.BY_KEY["pe_acquisition"].weight == 1.0
    assert P.BY_KEY["crisis_event"].weight == 0.9
    # And the weak tier stays weak.
    assert P.BY_KEY["rebrand"].weight < 0.8
    assert P.BY_KEY["framework_award"].weight < 0.9


# ====================================================================
# Research write-back (no manual admin)
# ====================================================================
def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(PR, "_store_path", lambda: tmp_path / "p.json")
    monkeypatch.setattr(PR, "_seeds_path", lambda: tmp_path / "s.json")


def test_record_finding_sets_flags(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert PR.record_finding("Acme", agency_user=True,
                             note="agency-posted ads found",
                             source_url="https://example.com")
    f = PR.flags_for("Acme")
    assert f["agency_user"] is True and "agency-posted" in f["agency_evidence"]


def test_research_can_clear_a_machine_observation(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    PR.ingest_ats_counts({"acme": (20, 0, 3)})       # machine: in-house
    assert PR.flags_for("Acme").get("internal_ta") is True
    # Research finds those were contractor mislabels — clears the flag.
    PR.record_finding("Acme", internal_ta=False,
                      note="TA ads were for a subsidiary; no central team")
    assert PR.flags_for("Acme").get("internal_ta") is None


def test_research_findings_expire(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    PR.record_finding("Acme", internal_ta=True, note="6-person TA team")
    # record_finding stamps 'seen' with wall-clock time, so the expiry
    # horizon must be computed from wall clock too. A hardcoded NOW
    # constant made this test start failing the morning real time
    # crossed it (the gap dropped to TA_EXPIRE_DAYS days + a few hours,
    # whose .days == TA_EXPIRE_DAYS still counts as fresh).
    later = datetime.now(timezone.utc) + timedelta(days=PR.TA_EXPIRE_DAYS + 1)
    assert PR.flags_for("Acme", now=later) == {}


def test_record_finding_validates(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert not PR.record_finding("")                  # no company
    assert not PR.record_finding("Acme")              # nothing to record
