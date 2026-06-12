"""BD Build v7 — roster-free contacts + the AD-grade account thesis.

What must never regress:
  1. Contact research is universal: a BD company the FREE chain can't
     name an owner at falls through to the model-research layer; results
     land in the shared store under the same acceptance rules.
  2. Failure-aware retry: a research/resolve miss frees up for retry in
     FAILURE_RETRY_DAYS, a hit holds the full TTL.
  3. Verification-first email economics: a free format guess verified
     valid becomes a SENDABLE address before any finder search is spent;
     an unverifiable guess stays "pattern" and unsendable.
  4. Team-map fallback adds only NAMED function-family people (never
     generic role rows, never finance/ops titles, never C-suite).
  5. The account thesis is schema-locked to the service catalogue,
     expires, invalidates on event-set change, and renders in the
     dossier (with the static service-fit as the fallback).
  6. Nothing is silent: capability warnings + per-lead diagnosis exist.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tool.contacts.schema import ContactEntry
from tool.contacts.store import load_contacts, save_contacts, upsert_contact


def _iso(days_ago=0):
    return (datetime.now(timezone.utc)
            - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def state(tmp_path, monkeypatch):
    import tool.state_paths as sp
    monkeypatch.setattr(sp, "state_root", lambda profile_key=None: tmp_path)
    monkeypatch.delenv("VMA_PROFILE", raising=False)
    return tmp_path


# ------------------------------------------------- 1. universal research

def test_bd_poc_research_layer_fires_for_unresolved_company(state):
    from tool.contacts import bd_poc_fill

    def no_resolve(company, slot, fetch=None):
        return None, {"company": company, "slot": slot, "outcome": "miss"}

    answer = {
        "found": True, "name": "Jane Doe",
        "role_title": "Group Communications Director",
        "role_slot": "head_of_comms",
        "evidence": [{"url": "https://acme.example/leadership",
                      "what": "leadership page"}],
        "newest_evidence_date": _iso(10),
        "confidence": 0.82,
    }
    briefs = []

    def stub_runner(brief):
        briefs.append(brief)
        return answer

    stats = bd_poc_fill.run(
        ["Acme plc"], desk="comms", resolver=no_resolve,
        profile_resolver=lambda c, n: None,
        research_runner=stub_runner,
        context_for=lambda c: "CEO change: new chief executive announced")
    assert stats["researched"] == 1
    assert stats["research_resolved"] == 1
    # Context (trigger evidence) reached the researcher as search anchors.
    assert "CEO change" in briefs[0]
    card = load_contacts().get("Acme plc")
    e = card.entries["head_of_comms"]
    assert e.name == "Jane Doe"
    assert e.source_label == "AI web research (BD board)"
    assert e.meets_named_confidence()


def test_bd_poc_research_not_spent_when_free_chain_succeeds(state):
    from tool.contacts import bd_poc_fill

    def resolves(company, slot, fetch=None):
        return (ContactEntry(name="Free Chain", role_title="CCO",
                             role_slot=slot, verified_at=_iso(0),
                             confidence=0.9),
                {"company": company, "slot": slot, "outcome": "hit"})

    def explode(brief):   # research must never be reached
        raise AssertionError("research layer spent unnecessarily")

    stats = bd_poc_fill.run(["Acme plc"], desk="comms", resolver=resolves,
                            profile_resolver=lambda c, n: None,
                            research_runner=explode)
    assert stats["resolved"] >= 1 and stats["researched"] == 0


def test_research_company_owner_rejects_below_floor_and_bad_slot(state):
    from tool.contacts.job_researcher import research_company_owner
    contacts = {}
    low = {"found": True, "name": "Weak Match", "role_slot": "head_of_comms",
           "newest_evidence_date": _iso(5), "confidence": 0.5}
    assert not research_company_owner("Acme plc", ("head_of_comms",),
                                      contacts, runner=lambda b: low)
    bad_slot = {"found": True, "name": "Wrong Slot", "role_slot": "astronaut",
                "newest_evidence_date": _iso(5), "confidence": 0.9}
    assert not research_company_owner("Beta plc", ("head_of_comms",),
                                      contacts, runner=lambda b: bad_slot)
    assert not contacts


# ------------------------------------------------ 2. failure-aware retry

def test_research_ledger_retries_misses_sooner_than_hits(state):
    from tool.contacts import job_researcher as jr
    led = {}
    old_miss = (datetime.now(timezone.utc)
                - timedelta(days=jr.FAILURE_RETRY_DAYS + 1)).isoformat()
    fresh_miss = _iso(1)
    old_hit = (datetime.now(timezone.utc)
               - timedelta(days=jr.FAILURE_RETRY_DAYS + 1)).isoformat()
    led[jr._ledger_key("OldMiss", "head_of_comms")] = {
        "at": old_miss, "found": False}
    led[jr._ledger_key("FreshMiss", "head_of_comms")] = {
        "at": fresh_miss, "found": False}
    led[jr._ledger_key("OldHit", "head_of_comms")] = {
        "at": old_hit, "found": True}
    assert not jr._recently_researched(led, "OldMiss", "head_of_comms")
    assert jr._recently_researched(led, "FreshMiss", "head_of_comms")
    # A hit inside the full TTL stays blocked even past the failure window.
    assert jr._recently_researched(led, "OldHit", "head_of_comms")


# --------------------------------------- 3. verification-first economics

def test_format_guess_verified_valid_becomes_sendable(state, monkeypatch):
    from tool.contacts import email_resolver as er
    monkeypatch.setattr(
        er, "format_guess",
        lambda company, name: {"email": "jane.doe@acme.example",
                               "source_url": "https://acme.example/press"})
    monkeypatch.setattr(er, "hunter_verify", lambda email: "valid")
    monkeypatch.setattr(er, "_published_for_person", lambda c, n: None)
    e = ContactEntry(name="Jane Doe", role_title="CCO",
                     role_slot="cco", verified_at=_iso(0), confidence=0.9)
    assert er.resolve_email("Acme plc", e, domain="acme.example")
    assert e.email == "jane.doe@acme.example"
    assert e.email_status == "verified"
    assert e.email_is_sendable()


def test_unverifiable_guess_stays_pattern_and_unsendable(state, monkeypatch):
    from tool.contacts import email_resolver as er
    monkeypatch.setattr(
        er, "format_guess",
        lambda company, name: {"email": "jane.doe@acme.example",
                               "source_url": ""})
    monkeypatch.setattr(er, "hunter_verify", lambda email: "unknown")
    monkeypatch.setattr(er, "_published_for_person", lambda c, n: None)
    monkeypatch.setattr(er, "hunter_find", lambda d, n: None)
    e = ContactEntry(name="Jane Doe", role_title="CCO",
                     role_slot="cco", verified_at=_iso(0), confidence=0.9)
    assert er.resolve_email("Acme plc", e, domain="acme.example")
    assert e.email_status == "pattern"
    assert not e.email_is_sendable()


# ------------------------------------------------- 4. team-map fallback

def test_team_map_fallback_adds_named_comms_people_only(state):
    from tool import team_map
    from tool.hiring_manager import bd_points_of_contact
    team_map.update_roster("Acme plc", "https://acme.example/leadership", {
        "Sam Patel": "Director of Corporate Communications",
        "Pat Jones": "Chief Financial Officer",
        "Lee Wong": "Head of Procurement",
    })
    pocs = bd_points_of_contact("Acme plc", desk="comms", contacts={})
    names = [p["name"] for p in pocs]
    assert "Sam Patel" in names
    assert "Pat Jones" not in names and "Lee Wong" not in names
    sam = next(p for p in pocs if p["name"] == "Sam Patel")
    assert sam["stale"] is True            # page-observed — verify first
    assert "linkedin.com/talent/search" in sam["url"]


def test_no_team_map_and_no_roster_still_yields_nothing(state):
    from tool.hiring_manager import bd_points_of_contact
    assert bd_points_of_contact("Ghost Ltd", desk="comms", contacts={}) == []


def test_roster_entries_outrank_team_map_rows(state):
    from tool import team_map
    from tool.hiring_manager import bd_points_of_contact
    team_map.update_roster("Acme plc", "https://acme.example/leadership", {
        "Sam Patel": "Director of Corporate Communications"})
    contacts = {}
    upsert_contact(contacts, "Acme plc", "cco", ContactEntry(
        name="Rostered Owner", role_title="CCO", role_slot="cco",
        verified_at=_iso(1), confidence=0.9))
    pocs = bd_points_of_contact("Acme plc", desk="comms", contacts=contacts)
    assert pocs[0]["name"] == "Rostered Owner"
    assert any(p["name"] == "Sam Patel" for p in pocs)


# ----------------------------------------------- 5. advisory services

def test_dossier_renders_advisory_services_from_signals(state):
    from tool import dossier
    rec = {"company": "Acme plc", "last_seen": "2026-06-12",
           "status": "active",
           "events": [{"date": "2026-06-01", "key": "restructure",
                       "label": "Restructure", "evidence": "x",
                       "source": "RNS", "url": ""}]}
    md = dossier._render_md("acme", rec, [])
    assert "## Advisory services" in md


# --------------------------------------------------- 6. never silent

def test_contact_capabilities_reports_missing_keys(state, monkeypatch):
    from tool.contacts.measure import contact_capabilities
    for k in ("ANTHROPIC_API_KEY", "HUNTER_API_KEY",
              "BRIGHT_DATA_KEY", "BRIGHT_DATA_ZONE"):
        monkeypatch.delenv(k, raising=False)
    caps = contact_capabilities()
    assert not caps["anthropic"] and not caps["hunter"]
    joined = " ".join(caps["warnings"])
    assert "ANTHROPIC_API_KEY" in joined and "HUNTER_API_KEY" in joined


def test_research_status_diagnoses_the_unnamed(state, monkeypatch):
    from tool.contacts import job_researcher as jr
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    led = {jr._ledger_key("Acme plc", "head_of_comms"):
           {"at": _iso(1), "found": False}}
    msg = jr.research_status("Acme plc", "head_of_comms", ledger=led)
    assert "no defensible answer" in msg
    assert jr.research_status("Never Tried Ltd", "head_of_comms",
                              ledger=led).startswith("not yet researched")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert "ANTHROPIC_API_KEY" in jr.research_status("X", "head_of_comms")


def test_billing_exhaustion_is_reported_not_misattributed(state, monkeypatch):
    """When CI hit 'credit balance too low', the synced ledger carries the
    truth — the chip and the banner must say CREDITS, not 'no key' (the
    live dashboard's env legitimately has no research key)."""
    from tool.contacts import job_researcher as jr
    from tool.contacts.measure import contact_capabilities
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    led = {jr._ledger_key("Acme plc", "head_of_comms"):
           {"at": _iso(0), "found": False}}
    jr._mark_billing(led)
    msg = jr.research_status("Acme plc", "head_of_comms", ledger=led)
    assert "credits exhausted" in msg.lower()
    assert "ANTHROPIC_API_KEY" not in msg
    jr._save_ledger(led)
    caps = contact_capabilities()
    assert any("EXHAUSTED" in w for w in caps["warnings"])
    # A successful pass clears the marker.
    answer = {"found": True, "name": "Jane Doe",
              "role_slot": "head_of_comms",
              "evidence": [{"url": "https://x.example", "what": "page"}],
              "newest_evidence_date": _iso(2), "confidence": 0.8}
    contacts = {}
    assert jr.research_company_owner("Beta plc", ("head_of_comms",),
                                     contacts, runner=lambda b: answer,
                                     ledger=led)
    assert "::billing" not in led


# ------------------------------------------- 7. contacts-only spend mode

def test_contacts_only_mode_gates_optional_passes(state, monkeypatch):
    """VMA_MODEL_SPEND=contacts: ONLY the two funded contact-research
    lanes may call the API; the five optional model passes no-op."""
    monkeypatch.setenv("VMA_MODEL_SPEND", "contacts")

    def boom(*a, **k):
        raise AssertionError("optional pass spent credits")

    from tool import auto_investigate, universe_expand
    from tool import semantic_scan
    from tool.outreach import ai_draft
    assert auto_investigate.run(runner=boom) == 0
    assert universe_expand.run([], call=boom) == 0
    assert semantic_scan.detect([{"title": "x", "summary": "y"}],
                                call=boom) == []
    assert ai_draft({"company": "X", "title": "Y"}) is None
    # The funded lane still runs.
    from tool.contacts.job_researcher import research_company_owner
    answer = {"found": True, "name": "Jane Doe",
              "role_slot": "head_of_comms",
              "evidence": [{"url": "https://x.example", "what": "page"}],
              "newest_evidence_date": _iso(2), "confidence": 0.8}
    contacts = {}
    assert research_company_owner("Acme plc", ("head_of_comms",), contacts,
                                  runner=lambda b: answer)
    # Default mode: nothing gated.
    monkeypatch.delenv("VMA_MODEL_SPEND")
    from tool.config import model_spend_allowed
    assert model_spend_allowed("optional")
