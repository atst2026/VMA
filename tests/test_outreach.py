"""SEND OUTREACH — the email layer, the per-job contact researcher, and
the guarded send.

What must never regress:
  1. Pattern-guess emails are stored but NEVER one-click sendable.
  2. Test mode reroutes every send to the test inbox and is the default.
  3. The suppression list blocks a send outright (the opt-out promise).
  4. A live send can't repeat for the same lead / same address+company.
  5. The researcher only writes answers it can defend (confidence floor,
     dated evidence, never silently overwriting a stronger fresh entry).
"""
from datetime import datetime, timedelta, timezone

import pytest

from tool.contacts.schema import ContactCard, ContactEntry


def _iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Point every state file at tmp, scrub the env knobs, and stub the
    direct-site harvester so no test can reach the network."""
    import tool.state_paths as sp
    from tool.contacts import site_pages
    monkeypatch.setattr(sp, "state_root", lambda profile_key=None: tmp_path)
    monkeypatch.setattr(site_pages, "harvest", lambda c, **k: {
        "domain": "", "people": [], "emails": [], "pages": [], "at": ""})
    for var in ("HUNTER_API_KEY", "ANTHROPIC_API_KEY", "OUTREACH_TEST_MODE",
                "OUTREACH_FROM_NAME"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _entry(**kw):
    base = dict(name="Jane Smith", role_title="Director of Communications",
                role_slot="head_of_comms", verified_at=_iso(5),
                confidence=0.85)
    base.update(kw)
    return ContactEntry(**base)


# ====================================================================
# 1. Schema: email fields + the sendable gate
# ====================================================================
def test_email_fields_roundtrip_and_old_state_still_loads():
    card = ContactCard(company="Acme")
    card.entries["head_of_comms"] = _entry(
        email="jane.smith@acme.com", email_status="verified",
        email_source="hunter", email_checked_at=_iso(1))
    back = ContactCard.from_jsonable(card.to_jsonable())
    e = back.get("head_of_comms")
    assert e.email == "jane.smith@acme.com"
    assert e.email_status == "verified"
    # Entries saved BEFORE this build (no email keys) must still load.
    legacy = {"company": "Old Co", "entries": {"cco": {
        "name": "Bob", "role_title": "CCO", "role_slot": "cco"}}}
    assert ContactCard.from_jsonable(legacy).get("cco").email == ""


def test_email_sendable_gate():
    ok = _entry(email="j@acme.com", email_status="verified",
                email_checked_at=_iso(1))
    assert ok.email_is_sendable()
    published = _entry(email="j@acme.com", email_status="published",
                       email_checked_at=_iso(1))
    assert published.email_is_sendable()
    pattern = _entry(email="j@acme.com", email_status="pattern",
                     email_checked_at=_iso(1))
    assert not pattern.email_is_sendable()
    stale = _entry(email="j@acme.com", email_status="verified",
                   email_checked_at=_iso(200))
    assert not stale.email_is_sendable()
    assert not _entry().email_is_sendable()


# ====================================================================
# 2. RNS enquiries-block parsing
# ====================================================================
def test_published_emails_parse_names_generics_and_agencies(monkeypatch):
    from tool import rns_contacts
    block = ("Enquiries: Severn Trent Plc — Jane Smith, Head of "
             "Communications jane.smith@severntrent.co.uk +44 20 1234; "
             "Media enquiries press@severntrent.co.uk; Buchanan (financial "
             "PR) Tom Jones tjones@buchanan.uk.com")
    monkeypatch.setattr(rns_contacts, "_load", lambda: {
        rns_contacts._norm("Severn Trent"): {
            "company": "Severn Trent",
            "blocks": [{"at": _iso(3), "url": "https://x/rns1",
                        "block": block}]}})
    out = rns_contacts.published_emails("Severn Trent")
    by_email = {o["email"]: o for o in out}
    jane = by_email["jane.smith@severntrent.co.uk"]
    assert jane["name_hint"] == "Jane Smith"
    assert jane["in_house"] and not jane["generic"]
    assert by_email["press@severntrent.co.uk"]["generic"]
    agency = by_email["tjones@buchanan.uk.com"]
    assert not agency["in_house"]
    assert rns_contacts.published_emails("Unknown Co") == []


# ====================================================================
# 3. Email resolver waterfall
# ====================================================================
def test_resolve_email_uses_published_source_without_hunter(state, monkeypatch):
    from tool.contacts import email_resolver
    from tool import rns_contacts
    monkeypatch.setattr(rns_contacts, "published_emails", lambda c: [
        {"email": "jane.smith@acme.com", "name_hint": "Jane Smith",
         "generic": False, "in_house": True, "url": "https://x/rns",
         "at": _iso(2)}])
    e = _entry()
    assert email_resolver.resolve_email("Acme", e)
    assert e.email == "jane.smith@acme.com"
    assert e.email_status == "published"        # no Hunter -> no upgrade
    assert e.email_source == "rns_enquiries"
    assert e.email_source_url == "https://x/rns"
    assert e.email_is_sendable()


def test_resolve_email_prefers_in_house_and_matches_surname(state, monkeypatch):
    from tool.contacts import email_resolver
    from tool import rns_contacts
    monkeypatch.setattr(rns_contacts, "published_emails", lambda c: [
        {"email": "jsmith@agency-pr.com", "name_hint": "Jane Smith",
         "generic": False, "in_house": False, "url": "u1", "at": _iso(1)},
        {"email": "jane.smith@acme.com", "name_hint": "",
         "generic": False, "in_house": True, "url": "u2", "at": _iso(9)},
        {"email": "press@acme.com", "name_hint": "", "generic": True,
         "in_house": True, "url": "u3", "at": _iso(1)},
    ])
    e = _entry()
    assert email_resolver.resolve_email("Acme", e)
    assert e.email == "jane.smith@acme.com"     # in-house beats agency


def test_resolve_email_skips_already_sendable_and_no_key_means_no_hunter(
        state, monkeypatch):
    from tool.contacts import email_resolver
    from tool import rns_contacts
    fresh = _entry(email="ok@acme.com", email_status="verified",
                   email_checked_at=_iso(1))
    assert not email_resolver.resolve_email("Acme", fresh)
    # No published source + no HUNTER_API_KEY -> nothing happens, no
    # network call attempted (requests would blow up the test if so).
    monkeypatch.setattr(rns_contacts, "published_emails", lambda c: [])
    bare = _entry()
    assert not email_resolver.resolve_email("Acme", bare,
                                            domain="acme.com")
    assert bare.email == ""
    assert email_resolver.hunter_verify("x@y.com") == ""


# ====================================================================
# 4. The per-job contact researcher
# ====================================================================
_SIGNAL = {
    "kind": "job",
    "title": "Internal Communications Manager",
    "company": "Acme Utilities",
    "summary": "An exciting role reporting to the Director of "
               "Communications, based in Birmingham.",
    "source": "Adzuna", "url": "https://jobs/x", "geo": "UK",
}


def _good_answer(**kw):
    base = {
        "found": True, "name": "Jane Doe",
        "role_title": "Director of Communications",
        "role_slot": "head_of_comms",
        "evidence": [{"url": "https://acme.com/leadership",
                      "date": _iso(20), "what": "leadership page"}],
        "newest_evidence_date": _iso(20),
        "confidence": 0.85,
    }
    base.update(kw)
    return base


def test_researcher_writes_defensible_answer_and_respects_ttl(state):
    from tool.contacts import job_researcher
    from tool.contacts.store import get_contact
    calls = {"n": 0}

    def runner(brief):
        calls["n"] += 1
        assert "Internal Communications Manager" in brief
        assert "Acme Utilities" in brief
        return _good_answer()

    contacts = {}
    assert job_researcher.research_job_contact(_SIGNAL, contacts,
                                               runner=runner)
    e = get_contact(contacts, "Acme Utilities").get("head_of_comms")
    assert e.name == "Jane Doe"
    assert e.confidence == 0.85
    assert e.source_label == "AI web research (live job)"
    # Within the TTL the same (company, slot) never re-spends a pass.
    assert not job_researcher.research_job_contact(_SIGNAL, contacts,
                                                   runner=runner)
    assert calls["n"] == 1


def test_researcher_rejects_weak_answers(state):
    from tool.contacts import job_researcher
    for bad in (
        None,
        {"found": False, "confidence": 0.9},
        _good_answer(confidence=0.5),                       # below floor
        _good_answer(newest_evidence_date=_iso(500)),       # too old
        _good_answer(role_slot="other"),                    # not a slot
        _good_answer(name=""),
    ):
        contacts = {}
        changed = job_researcher.research_job_contact(
            _SIGNAL, contacts, runner=lambda b, _bad=bad: _bad,
            ledger={})
        assert not changed and contacts == {}


def test_researcher_never_downgrades_a_stronger_fresh_entry(state):
    from tool.contacts import job_researcher
    from tool.contacts.store import upsert_contact, get_contact
    contacts = {}
    upsert_contact(contacts, "Acme Utilities", "head_of_comms",
                   _entry(name="Existing Strong", confidence=0.92))
    job_researcher.research_job_contact(
        _SIGNAL, contacts, runner=lambda b: _good_answer(confidence=0.75),
        ledger={})
    e = get_contact(contacts, "Acme Utilities").get("head_of_comms")
    assert e.name == "Existing Strong"


def test_researcher_confidence_is_capped_below_registry_grade(state):
    from tool.contacts import job_researcher
    from tool.contacts.store import get_contact
    contacts = {}
    job_researcher.research_job_contact(
        _SIGNAL, contacts, runner=lambda b: _good_answer(confidence=0.99),
        ledger={})
    e = get_contact(contacts, "Acme Utilities").get("head_of_comms")
    assert e.confidence == job_researcher.STORE_CONFIDENCE_CAP


def test_researcher_noop_without_api_key(state):
    from tool.contacts import job_researcher
    contacts = {}
    # default runner (_run_model) with no ANTHROPIC_API_KEY -> None
    assert not job_researcher.research_job_contact(_SIGNAL, contacts,
                                                   ledger={})
    assert contacts == {}


# ====================================================================
# 5. The contact dict carries the email only while sendable
# ====================================================================
def test_best_named_contact_carries_email_with_status(state):
    from tool.hiring_manager import best_named_contact
    contacts = {}
    from tool.contacts.store import upsert_contact
    upsert_contact(contacts, "Acme", "head_of_comms", _entry(
        email="jane@acme.com", email_status="verified",
        email_checked_at=_iso(1)))
    nc = best_named_contact("Acme", ("head_of_comms",), contacts=contacts)
    assert nc["email"] == "jane@acme.com"
    assert nc["email_status"] == "verified"
    upsert_contact(contacts, "Patterny", "head_of_comms", _entry(
        email="guess@patterny.com", email_status="pattern",
        email_checked_at=_iso(1)))
    nc2 = best_named_contact("Patterny", ("head_of_comms",),
                             contacts=contacts)
    # Pattern guesses are SHOWN (red chip, manual use) — the send gate
    # blocks them; only a stale/absent address vanishes entirely.
    assert nc2["email"] == "guess@patterny.com"
    assert nc2["email_status"] == "pattern"
    from tool.outreach import sendable_state
    ok, why = sendable_state({**nc2, "title": "x"})
    assert not ok and "pattern" in why


# ====================================================================
# 6. Send gates + the send itself
# ====================================================================
def _lead(**kw):
    base = {
        "lead_id": "lead-1", "title": "Head of Communications — Acme",
        "company": "Acme", "status": "active",
        "outreach": "Hi Jane,\n\ndraft body.\n\nBest,\nSara",
        "contact": {"name": "Jane Smith",
                    "title": "Director of Communications",
                    "confidence": 0.85, "stale": False,
                    "email": "jane@acme.com", "email_status": "verified",
                    "slot": "head_of_comms"},
    }
    base.update(kw)
    return base


def test_sendable_state_gates(state):
    from tool.outreach import sendable_state
    ok, _ = sendable_state(_lead()["contact"])
    assert ok
    cases = [
        ({}, "no named contact"),
        ({**_lead()["contact"], "stale": True}, "re-verifying"),
        ({**_lead()["contact"], "confidence": 0.5}, "below the send floor"),
        ({**_lead()["contact"], "email": ""}, "no work email"),
        ({**_lead()["contact"], "email_status": "pattern"}, "pattern guess"),
    ]
    for contact, why in cases:
        ok, reason = sendable_state(contact)
        assert not ok and why in reason


def test_send_defaults_to_test_mode_and_reroutes(state, monkeypatch):
    from tool import outreach, email_send, config
    sent = {}

    def fake_send(to, subject, html, text=None, bcc=None,
                  attachments=None, from_name=None):
        sent.update(to=to, subject=subject, text=text, from_name=from_name)
        return {"ok": True, "provider": "gmail", "detail": "stub"}

    monkeypatch.setattr(email_send, "send", fake_send)
    res = outreach.send_outreach(_lead())
    assert res["ok"] and res["mode"] == "test"
    assert sent["to"] == config.TEST_RECIPIENT          # rerouted
    assert res["to_email"] == "jane@acme.com"           # real target kept
    assert "[TEST → Jane Smith <jane@acme.com>]" in sent["subject"]
    assert "TEST MODE" in sent["text"]
    assert "VMA Group" in sent["text"]                  # identification
    assert "no thanks" in sent["text"]                  # opt-out promise
    assert sent["from_name"] == "Sara Tehrani"
    # Garbage in the env var must fail SAFE (still test mode).
    monkeypatch.setenv("OUTREACH_TEST_MODE", "banana")
    assert outreach.test_mode()


def test_live_send_blocks_duplicates_and_suppressed(state, monkeypatch):
    from tool import outreach, email_send
    monkeypatch.setenv("OUTREACH_TEST_MODE", "0")
    monkeypatch.setattr(email_send, "send",
                        lambda *a, **k: {"ok": True, "provider": "gmail",
                                         "detail": "stub"})
    assert outreach.send_outreach(_lead())["ok"]
    dup = outreach.send_outreach(_lead())
    assert not dup["ok"] and "already sent" in dup["detail"]
    # Same address + company on a DIFFERENT lead inside the cool-off.
    dup2 = outreach.send_outreach(_lead(lead_id="lead-2"))
    assert not dup2["ok"] and "already emailed" in dup2["detail"]
    # Suppression beats everything.
    outreach.suppress("bob@other.com", "opt-out")
    blocked = outreach.send_outreach(_lead(
        lead_id="lead-3", company="Other",
        contact={**_lead()["contact"], "email": "bob@other.com"}))
    assert not blocked["ok"] and "opted out" in blocked["detail"]
    # Domain-level suppression too.
    outreach.suppress("megacorp.com", "client - never prospect")
    blocked2 = outreach.send_outreach(_lead(
        lead_id="lead-4", company="MegaCorp",
        contact={**_lead()["contact"], "email": "x@megacorp.com"}))
    assert not blocked2["ok"]


def test_send_log_is_append_only_record(state, monkeypatch):
    from tool import outreach, email_send
    monkeypatch.setattr(email_send, "send",
                        lambda *a, **k: {"ok": True, "provider": "gmail",
                                         "detail": "stub"})
    outreach.send_outreach(_lead())
    recs = list(outreach._iter_log())
    assert len(recs) == 1
    assert recs[0]["mode"] == "test"
    assert recs[0]["to_email"] == "jane@acme.com"
    assert recs[0]["ok"] is True


# ====================================================================
# 7. Drafting
# ====================================================================
def test_ai_draft_noop_without_key_and_template_fallback(state):
    from tool import outreach
    assert outreach.ai_draft(_lead()) is None
    text = outreach.draft_outreach_for_lead(_lead())
    assert "Hi Jane," in text and "Acme" in text


def test_enrich_signals_attaches_drafts_with_budget(state, monkeypatch):
    from tool import outreach
    from tool.contacts import bd_poc_fill, job_researcher
    monkeypatch.setattr(job_researcher, "research_signals",
                        lambda *a, **k: 0)
    monkeypatch.setattr(bd_poc_fill, "fill_for_signals",
                        lambda *a, **k: {"resolved": 0})
    monkeypatch.setattr(outreach, "ai_draft",
                        lambda s, c=None: f"draft for {s['company']}")
    signals = [{"kind": "job", "title": f"Head of Communications {i}",
                "company": f"Co {i}", "summary": ""} for i in range(5)]
    stats = outreach.enrich_signals(signals, draft_max=3)
    assert stats["drafts"] == 3
    assert signals[0]["outreach_ai"] == "draft for Co 0"
    assert "outreach_ai" not in signals[4]


# ====================================================================
# 8. Hunter monthly ledger — the free-tier guard
# ====================================================================
def test_hunter_ledger_caps_monthly_spend(state, monkeypatch):
    from tool.contacts import email_resolver as er
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    monkeypatch.setenv("HUNTER_MONTHLY_SEARCH_BUDGET", "2")
    monkeypatch.setenv("HUNTER_MONTHLY_VERIFY_BUDGET", "1")
    calls = {"n": 0}
    monkeypatch.setattr(er, "_get", lambda p, q: (calls.__setitem__(
        "n", calls["n"] + 1) or {"email": "a@b.com", "score": 90,
                                 "verification": {"status": "valid"}}))
    er.reset_budget()
    assert er.hunter_find("b.com", "Jane Smith") is not None
    assert er.hunter_find("b.com", "Tom Jones") is not None
    # Third search this month: blocked BEFORE any network call.
    before = calls["n"]
    assert er.hunter_find("b.com", "Amy Long") is None
    assert calls["n"] == before
    # Verify cap works independently.
    er.reset_budget()
    monkeypatch.setattr(er, "_get", lambda p, q: {"status": "valid"})
    assert er.hunter_verify("x@b.com") == "valid"
    assert er.hunter_verify("y@b.com") == ""      # monthly cap of 1
    left = er.budget_remaining()
    assert left["searches_left"] == 0 and left["verifications_left"] == 0


def test_hunter_ledger_rolls_over_month(state, monkeypatch):
    from tool.contacts import email_resolver as er
    er._save_ledger({"month": "1999-01", "searches": 999,
                     "verifications": 999})
    fresh = er._load_ledger()                      # stale month -> reset
    assert fresh["searches"] == 0 and fresh["month"] != "1999-01"


def test_no_key_spends_nothing(state, monkeypatch):
    from tool.contacts import email_resolver as er
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    er.reset_budget()
    assert er.hunter_find("b.com", "Jane Smith") is None
    assert er.hunter_verify("x@b.com") == ""
    assert er.hunter_domain_contacts("b.com") == []
    assert er._load_ledger()["searches"] == 0      # ledger untouched


# ====================================================================
# 9. Domain-search fill — last-resort named contact, pattern-gated
# ====================================================================
def test_domain_fill_writes_slot_matched_contact(state, monkeypatch):
    from tool.contacts import email_resolver as er
    from tool.contacts.store import get_contact
    from tool import company_domain
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    monkeypatch.setattr(company_domain, "resolve_domain",
                        lambda name: "acme.com")
    monkeypatch.setattr(er, "hunter_domain_contacts", lambda d, desk="comms": [
        {"email": "bob@acme.com", "name": "Bob Ray",
         "position": "Sales Director", "score": 95,
         "verification_status": "valid", "source_url": "u1"},
        {"email": "jane@acme.com", "name": "Jane Smith",
         "position": "Group Communications Director", "score": 80,
         "verification_status": "valid", "source_url": "u2"},
    ])
    contacts = {}
    assert er.fill_from_domain_search(
        "Acme", ("cco", "head_of_comms"), contacts)
    e = get_contact(contacts, "Acme").get("cco")
    assert e.name == "Jane Smith"               # title-pattern gated
    assert e.email == "jane@acme.com" and e.email_status == "verified"
    assert e.confidence == 0.72                 # researcher can override
    # A company that already has a fresh named contact never spends.
    monkeypatch.setattr(er, "hunter_domain_contacts",
                        lambda d, desk="comms": (_ for _ in ()).throw(
                            AssertionError("should not be called")))
    assert not er.fill_from_domain_search(
        "Acme", ("cco", "head_of_comms"), contacts)


def test_find_for_person_published_first(state, monkeypatch):
    from tool.contacts import email_resolver as er
    from tool import rns_contacts
    monkeypatch.setattr(rns_contacts, "published_emails", lambda c: [
        {"email": "jane.smith@acme.com", "name_hint": "Jane Smith",
         "generic": False, "in_house": True, "url": "https://x/rns",
         "at": _iso(2)}])
    found = er.find_for_person("Acme", "Jane Smith")
    assert found["email"] == "jane.smith@acme.com"
    assert found["status"] == "published"
    assert found["source_url"] == "https://x/rns"


# ====================================================================
# 10. The brochure rides on every send
# ====================================================================
def test_send_attaches_brochure(state, monkeypatch, tmp_path):
    from tool import outreach, email_send
    fake = tmp_path / "brochure.pdf"
    fake.write_bytes(b"%PDF-1.6 fake")
    monkeypatch.setattr(outreach, "BROCHURE_PATH", fake)
    outreach._BROCHURE_CACHE.clear()
    sent = {}

    def stub(to, subject, html, text=None, bcc=None, attachments=None,
             from_name=None):
        sent["attachments"] = attachments
        return {"ok": True, "provider": "gmail", "detail": "stub"}

    monkeypatch.setattr(email_send, "send", stub)
    assert outreach.send_outreach(_lead())["ok"]
    (fn, raw, mime), = sent["attachments"]
    assert fn == outreach.BROCHURE_FILENAME
    assert raw == b"%PDF-1.6 fake" and mime == "application/pdf"
    # Kill-switch and missing-file paths send clean, attachment-free.
    monkeypatch.setenv("OUTREACH_ATTACH_BROCHURE", "0")
    outreach.send_outreach(_lead(lead_id="l2"))
    assert sent["attachments"] is None
    monkeypatch.delenv("OUTREACH_ATTACH_BROCHURE")
    monkeypatch.setattr(outreach, "BROCHURE_PATH", tmp_path / "gone.pdf")
    outreach._BROCHURE_CACHE.clear()
    outreach.send_outreach(_lead(lead_id="l3"))
    assert sent["attachments"] is None
    outreach._BROCHURE_CACHE.clear()


def test_real_brochure_asset_is_a_pdf():
    from tool.outreach import BROCHURE_PATH
    raw = BROCHURE_PATH.read_bytes()
    assert raw[:5] == b"%PDF-" and len(raw) > 1_000_000
