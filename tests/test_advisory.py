"""Service-fit lens (tool/advisory.py) — the Talent-Consultancy upgrade.

Locks down: full trigger-taxonomy coverage (a new trigger can't ship
without a service mix), multi-signal vote combination, the budget-strain
steer toward project-fee routes, per-desk re-tuning, JSON-serialisability
for row dicts, and advisory_for's unchanged behaviour.
"""
import json

import pytest

from tool import advisory as A


@pytest.fixture(autouse=True)
def _comms_default(monkeypatch):
    """Tests run on the comms desk unless they opt into marketing."""
    monkeypatch.delenv("VMA_PROFILE", raising=False)


# ---------------------------------------------------------------- legacy

def test_advisory_for_known_trigger_unchanged():
    assert A.advisory_for("mna").startswith("Advisory:")
    assert "integration" in A.advisory_for("mna").lower()


def test_advisory_for_unknown_and_none_fall_back():
    assert A.advisory_for("no_such_trigger") == A._DEFAULT
    assert A.advisory_for(None) == A._DEFAULT


# ------------------------------------------------------- catalogue shape

def test_catalogue_entries_complete():
    assert set(A.SERVICES) == {
        "search", "interim", "org_design", "benchmarking", "coaching",
        "edi", "agency_referral", "engagement_platform"}
    for key, cat in A.SERVICES.items():
        for field in ("short", "label", "family", "blurb"):
            assert cat.get(field), f"{key} missing {field}"
        assert cat["family"] in ("hire", "advisory", "referral")


def test_fit_tables_reference_known_services_with_reasons():
    tables = [A._SERVICE_FIT, A._SERVICE_FIT_MARKETING,
              {"_default": A._DEFAULT_FIT}]
    for table in tables:
        for trig, fit in table.items():
            assert fit, f"{trig} has an empty mix"
            for svc, reason in fit:
                assert svc in A.SERVICES, f"{trig} -> unknown service {svc}"
                assert reason and len(reason) > 20, f"{trig}/{svc} reason thin"


def test_budget_strained_keys_are_mapped_triggers():
    assert A._BUDGET_STRAINED <= set(A._SERVICE_FIT)


# ------------------------------------------------- taxonomy coverage

def test_every_predictor_trigger_has_a_service_mix():
    from tool.predictive import patterns as P
    missing = set(P.BY_KEY) - set(A._SERVICE_FIT)
    assert not missing, f"predictor triggers without a service mix: {missing}"


def test_programmatic_and_standalone_contexts_have_a_service_mix():
    programmatic = {"job_ad_cluster", "hiring_gap", "seniority_gap",
                    "framework_displacement"}
    standalone = {"water_sar", "contract_end", "funding", "following",
                  "interim_watch", "follow_on", "cascade", "stale_mandate"}
    missing = (programmatic | standalone) - set(A._SERVICE_FIT)
    assert not missing, f"contexts without a service mix: {missing}"


def test_every_calendar_pulse_has_a_service_mix():
    from tool import calendar_pulses as cp
    keys = {p["key"] for p in cp._COMMS_PULSES + cp._MARKETING_PULSES}
    missing = keys - set(A._SERVICE_FIT)
    assert not missing, f"calendar pulses without a service mix: {missing}"


# ------------------------------------------------------- single trigger

def test_ic_platform_rfp_leads_with_engagement_platform():
    fit = A.service_fit_for(["ic_platform_rfp"])
    assert fit["services"][0]["key"] == "engagement_platform"
    assert "Workvivo" in fit["services"][0]["reason"]


def test_gender_pay_gap_pulse_sells_edi_and_benchmarking():
    keys = [s["key"] for s in A.service_fit_for(["gender_pay_gap_2026"])["services"]]
    assert keys[0] == "edi"
    assert "benchmarking" in keys


def test_equality_pay_reporting_pulse_sells_edi_and_benchmarking():
    keys = [s["key"]
            for s in A.service_fit_for(["equality_pay_reporting_2026"])["services"]]
    assert keys[0] == "edi"
    assert "benchmarking" in keys
    assert A.advisory_for("equality_pay_reporting_2026").startswith("Advisory:")


def test_market_entry_leads_with_the_peer_benchmark():
    fit = A.service_fit_for(["market_entry"])
    assert fit["services"][0]["key"] == "benchmarking"
    # The L'Oréal-style "what do peer functions look like here?" product.
    assert "L'Oréal" in fit["services"][0]["reason"]


def test_max_services_cap_and_headline():
    fit = A.service_fit_for(["mna"])  # mna maps five services
    assert len(fit["services"]) == 3
    assert fit["headline"] == " · ".join(s["short"] for s in fit["services"])
    fit2 = A.service_fit_for(["mna"], max_services=5)
    assert len(fit2["services"]) == 5


# -------------------------------------------------- combination logic

def test_stacked_signals_combine_votes():
    # ceo_change + restructure both vote org_design and benchmarking; the
    # two-vote services must outrank every one-vote service.
    fit = A.service_fit_for(["ceo_change", "restructure"])
    top2 = {s["key"] for s in fit["services"][:2]}
    assert top2 == {"org_design", "benchmarking"}


def test_duplicate_and_falsy_contexts_are_ignored():
    once = A.service_fit_for(["funding"])
    thrice = A.service_fit_for(["funding", "funding", None, "", "FUNDING "])
    assert [s["key"] for s in once["services"]] == \
           [s["key"] for s in thrice["services"]]


# -------------------------------------------------- budget-strain steer

def test_profit_warning_sets_budget_note_and_project_fee_route():
    fit = A.service_fit_for(["profit_warning"])
    assert fit["budget_note"]
    keys = {s["key"] for s in fit["services"]}
    assert keys & {"interim", "agency_referral"}


def test_strained_stack_swaps_in_a_project_fee_route():
    # restructure alone ranks org_design, benchmarking, coaching — the
    # steer must place interim (ranked 4th) into the capped mix.
    fit = A.service_fit_for(["restructure"])
    keys = [s["key"] for s in fit["services"]]
    assert keys[:2] == ["org_design", "benchmarking"]
    assert "interim" in keys
    assert fit["budget_note"]


def test_unstrained_stack_has_no_budget_note():
    assert A.service_fit_for(["ceo_change"])["budget_note"] is None


# ------------------------------------------------------- fallbacks

def test_unknown_none_and_empty_fall_back_to_default_mix():
    for contexts in (["no_such_trigger"], [], None):
        fit = A.service_fit_for(contexts)
        assert fit["services"], f"empty mix for {contexts!r}"
        assert [s["key"] for s in fit["services"]] == \
               [svc for svc, _ in A._DEFAULT_FIT]


def test_service_fit_line_compact_form():
    line = A.service_fit_line(["funding"])
    assert line.startswith("Sell: ")
    assert "Search" in line


# ------------------------------------------------------- per-desk tuning

def test_fn_placeholder_resolved_for_both_desks(monkeypatch):
    all_keys = list(A._SERVICE_FIT)
    for profile in ("comms", "marketing"):
        monkeypatch.setenv("VMA_PROFILE", profile)
        for key in all_keys:
            for s in A.service_fit_for([key], max_services=8)["services"]:
                assert "{fn}" not in s["reason"], f"{profile}/{key}"


def test_marketing_desk_overrides_and_fn_noun(monkeypatch):
    monkeypatch.setenv("VMA_PROFILE", "marketing")
    mna = A.service_fit_for(["mna"], max_services=5)
    assert "agency_referral" in {s["key"] for s in mna["services"]}
    assert "marketing" in mna["services"][0]["reason"]
    # Non-overridden key re-tunes via the {fn} noun.
    ceo = A.service_fit_for(["ceo_change"])
    assert "marketing function" in ceo["services"][0]["reason"]
    monkeypatch.delenv("VMA_PROFILE")
    ceo_comms = A.service_fit_for(["ceo_change"])
    assert "comms function" in ceo_comms["services"][0]["reason"]


# ------------------------------------------------------- serialisation

def test_service_fit_is_json_serialisable():
    fit = A.service_fit_for(["profit_warning", "ceo_change"])
    round_trip = json.loads(json.dumps(fit))
    assert round_trip == fit
