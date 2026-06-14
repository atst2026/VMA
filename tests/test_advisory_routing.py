"""Associate routing — the verdict's service → the right owner + bench.

Locks the brochure routing logic (ADVISORY_ENGINE.md §7): Lucy owns the
advisory relationship, Sara owns search/referral, and the delivery
associate is attached by service (coaching → Joss/Famn, ED&I →
Antoinette/Kate).
"""
import json

import pytest

from tool import advisory_routing as R


@pytest.fixture(autouse=True)
def _comms_default(monkeypatch):
    monkeypatch.delenv("VMA_PROFILE", raising=False)


def test_edi_lead_routes_to_lucy_with_riverroad_delivery():
    r = R.owner_for(["edi", "benchmarking", "coaching"], "PayGapActionMandate")
    assert r["owner"] == "Lucy Cairncross"
    assert r["desk"] == "advisory"
    assert r["associate"]["name"] == "Antoinette Willcocks"
    assert r["associate"]["firm"] == "RiverRoad"
    # The bench carries every associate the mix's services can field.
    names = {a["name"] for a in r["bench"]}
    assert {"Antoinette Willcocks", "Kate Isichei",
            "Joss Mathieson", "Molly & Roger Taylor"} <= names


def test_coaching_lead_routes_to_change_oasis():
    r = R.owner_for(["coaching", "benchmarking"])
    assert r["owner"] == "Lucy Cairncross"
    assert r["associate"]["firm"] == "Change Oasis"


def test_benchmarking_lead_is_vma_delivered_no_associate():
    r = R.owner_for(["benchmarking", "org_design"])
    assert r["owner"] == "Lucy Cairncross"
    assert r["desk"] == "advisory"
    assert r["associate"] is None        # VMA's own team delivers


def test_search_lead_routes_to_sara():
    r = R.owner_for(["search", "interim"])
    assert r["owner"] == "Sara Tehrani"
    assert r["desk"] == "search"


def test_advisory_lead_with_search_component_co_owns_sara():
    r = R.owner_for(["org_design", "benchmarking", "search"])
    assert r["owner"] == "Lucy Cairncross"
    assert r["co_owner"] == "Sara Tehrani"


def test_referral_lead_keeps_sara_as_adviser():
    r = R.owner_for(["agency_referral", "org_design"])
    assert r["desk"] == "referral"
    assert r["owner"] == "Sara Tehrani"
    assert r["referral"] is not None
    assert r["co_owner"] == "Lucy Cairncross"   # the advisory wrap


def test_empty_mix_defaults_to_lucy_advisory():
    r = R.owner_for([])
    assert r["owner"] == "Lucy Cairncross" and r["desk"] == "advisory"


def test_owner_line_compact_form():
    line = R.owner_line(["edi", "benchmarking"])
    assert line.startswith("Owner: Lucy Cairncross")
    assert "Antoinette Willcocks" in line


def test_routing_is_json_serialisable():
    r = R.owner_for(["edi", "search"])
    assert json.loads(json.dumps(r)) == r
