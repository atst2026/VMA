"""The conversion layer — CALL verdict, phase, deal value at house
rates, the phase play and the route-in facts. All deterministic (zero
model calls), all derived from fields the engine already verified, and
none of it may touch the rank: scoring stays exactly as the gate left
it (the access-penalty death spiral is the documented reason).
"""
import pytest

from tool import conversion as cv


# ====================================================================
# Phase taxonomy
# ====================================================================
def test_every_phase_member_maps_to_its_phase():
    for phase, members in cv.PHASES.items():
        for key in members:
            got, blurb = cv.phase_for([key])
            assert got == phase, key
            assert blurb  # every phase carries its implication


def test_unknown_or_empty_keys_map_to_no_phase():
    assert cv.phase_for(["not_a_trigger"]) == (None, "")
    assert cv.phase_for([]) == (None, "")
    assert cv.phase_for(None) == (None, "")


def test_rebuild_outranks_scale_on_mixed_stacks():
    # ceo_change + funding: the leadership change is the sharper read.
    phase, _ = cv.phase_for(["funding", "ceo_change"])
    assert phase == "rebuild"


# ====================================================================
# CALL verdict (a relabel of the gate — never a new decision)
# ====================================================================
def test_call_verdict_tiers():
    assert cv.call_verdict("ready")["call"] == "YES"
    assert cv.call_verdict("blocked", "In administration.")["call"] == "NO"
    assert "administration" in cv.call_verdict(
        "blocked", "In administration.")["why"]
    w = cv.call_verdict("dev", "Not qualified yet (3/8).")
    assert w["call"] == "WAIT" and "3/8" in w["why"]
    assert cv.call_verdict("early")["call"] == "WAIT"


def test_rival_mandate_forces_wait_even_when_ready():
    v = cv.call_verdict("ready", conflict=True)
    assert v["call"] == "WAIT" and "ival mandate" in v["why"]


# ====================================================================
# Deal economics at house rates (18.5% retained floor -> 22% top)
# ====================================================================
def test_leadership_search_band_at_house_rates():
    d = cv.deal_profile(["ceo_change"])
    # £110k x 18.5% = £20,350 -> £20.5k; £160k x 22% = £35,200 -> £35k
    assert d["type"] == "Leadership search"
    assert d["low"] == 20_500 and d["high"] == 35_000
    assert d["value"] == "£20k–£35k"
    assert "18.5%" in d["basis"] and "22%" in d["basis"]


def test_team_build_and_senior_and_interim_and_default_bands():
    t = cv.deal_profile(["funding"])
    assert t["type"] == "Team build" and t["low"] == 30_500
    assert t["high"] == 93_500 and "3–5 roles" in t["basis"]
    s = cv.deal_profile(["stale_mandate"])
    assert s["type"] == "Senior hire"
    assert s["low"] == 14_000 and s["high"] == 24_000
    i = cv.deal_profile(["interim_watch"])
    assert i["type"] == "Interim-first"
    assert (i["low"], i["high"]) == (8_000, 25_000)
    assert "day" in i["basis"]
    u = cv.deal_profile(["mystery_key"])
    assert u["type"] == "Single senior role"
    assert u["low"] == 11_000 and u["high"] == 20_000


def test_deal_confidence_follows_gate_strength():
    assert cv.deal_profile(["ceo_change"], presented=True,
                           q_total=6)["conf"] == "High"
    assert cv.deal_profile(["ceo_change"], presented=True,
                           score=80)["conf"] == "High"
    assert cv.deal_profile(["ceo_change"], presented=True,
                           q_total=3, score=50)["conf"] == "Medium"
    assert cv.deal_profile(["ceo_change"], score=50)["conf"] == "Medium"
    assert cv.deal_profile(["ceo_change"])["conf"] == "Low"


# ====================================================================
# The phase play (VMA service vocabulary, no generic filler)
# ====================================================================
def test_every_play_is_complete_and_specific():
    for phase in (*cv.PHASES.keys(), None):
        p = cv.strategy_for(phase, {})
        for field in ("lead", "position", "avoid", "entry", "goal",
                      "offer"):
            assert p.get(field), (phase, field)
        assert "meeting" in (p["goal"] + p["lead"]).lower() or \
               "conversation" in p["goal"].lower()


def test_entry_resolves_to_the_named_contact_when_one_exists():
    p = cv.strategy_for("rebuild", {"poc_name": "Dan Timms",
                                    "poc_title": "CCO"})
    assert "Dan Timms" in p["entry"] and "CCO" in p["entry"]


def test_entry_without_a_name_never_promises_a_link_we_dont_render():
    # Generic POC rows were removed from the cards; the play must not
    # reference a search link that no longer exists.
    for phase in (*cv.PHASES.keys(), None):
        e = cv.strategy_for(phase, {})["entry"]
        assert "search link" not in e.lower()


def test_rebuild_play_leads_with_pre_brief_search_work():
    p = cv.strategy_for("rebuild", {})
    assert "benchmark" in p["lead"].lower()
    assert "90 days" in p["position"] or "90 days" in p["goal"]


def test_pressure_play_is_interim_first_and_discreet():
    p = cv.strategy_for("pressure", {})
    assert "48" in p["lead"] or "48" in p["offer"]
    assert "discretion" in p["avoid"].lower() or \
           "crisis" in p["avoid"].lower()


# ====================================================================
# Access profile: facts, never a penalty
# ====================================================================
def test_named_contact_opens_the_door(monkeypatch):
    from tool import agency_relationships as ar
    monkeypatch.setattr(ar, "last_relationship", lambda c: None)
    a = cv.access_profile("Tesco", [{"name": "Jane Smith",
                                     "title": "CCO", "url": "u"}])
    assert a["label"] == "DOOR OPEN" and a["cls"] == "acc-good"
    assert a["poc_name"] == "Jane Smith"
    assert any("Jane Smith" in f for f in a["facts"])


def test_conflict_marks_contested_and_warns_off_the_seat(monkeypatch):
    from tool import agency_relationships as ar
    monkeypatch.setattr(ar, "last_relationship", lambda c: None)
    a = cv.access_profile("Tesco", [{"name": "Jane Smith", "title": "CCO"}],
                          conflict=True)
    assert a["label"] == "CONTESTED"
    assert any("RIVAL MANDATE" in f for f in a["facts"])


def test_internal_ta_is_guarded_and_unmapped_is_honest(monkeypatch):
    from tool import agency_relationships as ar
    monkeypatch.setattr(ar, "last_relationship", lambda c: None)
    g = cv.access_profile("Tesco", [], internal_ta=True)
    assert g["label"] == "GUARDED"
    assert any("we recruit ourselves" in f for f in g["facts"])
    u = cv.access_profile("Tesco", [])
    assert u["label"] == "UNMAPPED"
    assert any("No named function contact" in f for f in u["facts"])


def test_incumbent_agency_fact_lands_on_the_card(monkeypatch):
    from tool import agency_relationships as ar
    monkeypatch.setattr(ar, "last_relationship", lambda c: {
        "agency": "Hanson Search", "discipline": "comms",
        "date": "2026-02-10"})
    a = cv.access_profile("Tesco", [])
    assert any("Hanson Search" in f and "2026-02" in f for f in a["facts"])


# ====================================================================
# enrich_row: the one projection call sites use
# ====================================================================
def _console_row(**over):
    row = {"co": "Tesco", "tkeys": ["ceo_change"], "tier": "ready",
           "gateWhy": "", "presented": 1, "score": 72,
           "q": {"total": 6}, "poc": [{"name": "Jane Smith",
                                       "title": "CCO", "url": "u"}],
           "conflict": False, "internal_ta": False, "psl_status": "",
           "agency_scope": ""}
    row.update(over)
    return row


def test_enrich_row_projects_the_full_layer(monkeypatch):
    from tool import agency_relationships as ar
    monkeypatch.setattr(ar, "last_relationship", lambda c: None)
    e = cv.enrich_row(_console_row())
    assert e["callv"]["call"] == "YES"
    assert e["phase"] == "rebuild" and e["phaseWhy"]
    assert e["deal"]["type"] == "Leadership search"
    assert e["dealMax"] == 35_000          # the Deal value sort key
    assert "Jane Smith" in e["strategy"]["entry"]
    assert e["access"]["label"] == "DOOR OPEN"


def test_enrich_row_never_raises_on_garbage():
    assert isinstance(cv.enrich_row({}), dict)
    assert isinstance(cv.enrich_row({"tkeys": object()}), dict)


# ====================================================================
# Wiring: the console rows carry the layer end-to-end
# ====================================================================
@pytest.fixture
def state(tmp_path, monkeypatch):
    import tool.state_paths as sp
    monkeypatch.setattr(sp, "state_root", lambda profile_key=None: tmp_path)
    return tmp_path


def test_build_mr_rows_carries_the_conversion_fields(state):
    from tool.dashboard import _build_mr_rows
    g = {"presented": True, "confidence": "High", "reasons": [],
         "recheck_days": None, "investigate": False,
         "evidence": {"families": 3, "primary": 1, "credible": 1,
                      "level": "full"},
         "kill": "", "move": "", "cap": 7, "throttled": False}
    rows = [{"_kind": "predictor", "company": "Tesco", "pid": "tesco",
             "strength": "high", "window_label": "~6-12 wks",
             "predicted_role": "Head of Comms", "gate": g, "verdict": "",
             "events": [{"trigger_key": "ceo_change",
                         "trigger_label": "CEO change",
                         "published": "2026-05-01T00:00:00+00:00",
                         "evidence": "x", "url": "http://a"}]}]
    bd, _ = _build_mr_rows(rows, [], "Head of Communications", cap=7)
    assert len(bd) == 1
    r = bd[0]
    assert r["tkeys"] == ["ceo_change"]
    assert r["callv"]["call"] == "YES"
    assert r["phase"] == "rebuild"
    assert r["deal"]["value"] == "£20k–£35k" and r["dealMax"] == 35_000
    assert r["strategy"]["lead"] and r["access"]["label"]
    # The layer must not have touched the gate's numbers.
    assert r["tier"] == "ready" and isinstance(r["score"], int)


def test_engine_template_renders_the_new_rows_and_sort():
    from tool.engine_page import ENGINE_TEMPLATE as T
    for token in ("callband", "CALL: ", "SITUATION", "ACCESS",
                  "HOW TO WIN", "FEE AT STAKE", "tag-deal",
                  "'value','Deal value'", "dealMax"):
        assert token in T, token
