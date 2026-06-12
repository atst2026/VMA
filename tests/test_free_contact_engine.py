"""The free contact engine — direct company-site harvesting, the
resolver's site source, email-format inference, and the live-jobs
deterministic fill. None of it may touch the network in tests, and none
of it may ever mark an inferred address sendable.
"""
from datetime import datetime, timedelta, timezone

import pytest

from tool.contacts.schema import ContactEntry


def _iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def state(tmp_path, monkeypatch):
    import tool.state_paths as sp
    monkeypatch.setattr(sp, "state_root", lambda profile_key=None: tmp_path)
    for var in ("HUNTER_API_KEY", "ANTHROPIC_API_KEY", "BRIGHT_DATA_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


HOME = ('<html><nav><a href="/about/leadership">Our leadership</a>'
        '<a href="/media-centre">Media centre</a></nav></html>')
LEADERSHIP = ('<html><h3>Jane Smith - Chief Communications Officer</h3>'
              '<h3>Bob Ray - Chief Financial Officer</h3>'
              '<h3>Amy Long - Director of Marketing and Brand</h3></html>')
MEDIA = ('<html><p>Media enquiries: Tom Brown, Senior Press Officer, '
         'tom.brown@acme.com or newsdesk@acme.com. '
         'Applications to jobs@acme.com.</p></html>')


def _stub_fetch(pages):
    calls = []

    def fetch(url):
        calls.append(url)
        for frag, html in pages.items():
            if frag in url:
                return html
        return None
    fetch.calls = calls
    return fetch


def test_harvest_people_emails_and_cache(state, monkeypatch):
    from tool.contacts import site_pages
    from tool import company_domain
    monkeypatch.setattr(company_domain, "resolve_domain",
                        lambda name: "acme.com")
    fetch = _stub_fetch({"acme.com/about/leadership": LEADERSHIP,
                         "acme.com/media-centre": MEDIA,
                         "https://acme.com": HOME})
    got = site_pages.harvest("Acme", fetch=fetch)
    names = {p["name"]: p for p in got["people"]}
    assert "Jane Smith" in names and names["Jane Smith"]["slot"] == "cco"
    assert "Bob Ray" in names                  # parsed; slot=cfo (excluded later)
    emails = {e["email"]: e for e in got["emails"]}
    assert emails["tom.brown@acme.com"]["name"] == "Tom Brown"
    assert "newsdesk@acme.com" in emails       # generic kept, unattributed
    assert emails["newsdesk@acme.com"]["name"] == ""
    assert "jobs@acme.com" not in emails       # application inbox excluded
    # Cached: a second harvest performs zero fetches.
    n = len(fetch.calls)
    again = site_pages.harvest("Acme", fetch=fetch)
    assert len(fetch.calls) == n
    assert {p["name"] for p in again["people"]} == set(names)


SIBLING = ('<div class="card"><h3>Jane Smith</h3>'
           '<p>Chief Communications Officer</p></div>'
           '<div class="card"><h3>Tom O\'Brien</h3>'
           '<p>Group Director of Corporate Affairs</p></div>'
           '<div class="card"><p>Chief Marketing Officer</p>'
           '<h3>Amy Long</h3></div>'
           '<div class="card"><h3>Read More</h3><p>Our leadership</p></div>')


def test_harvest_pairs_sibling_element_cards(state, monkeypatch):
    """Real leadership pages put name and title in adjacent tags — the
    layout that made live runs resolve nothing. Both name-first and
    title-first cards must pair; nav junk must not."""
    from tool.contacts import site_pages
    from tool import company_domain
    monkeypatch.setattr(company_domain, "resolve_domain",
                        lambda name: "acme.com")
    fetch = _stub_fetch({"acme.com/about/leadership": SIBLING,
                         "https://acme.com": HOME})
    got = site_pages.harvest("Acme", fetch=fetch)
    by = {p["name"]: p for p in got["people"]}
    assert by["Jane Smith"]["slot"] == "cco"
    assert by["Tom O'Brien"]["slot"] == "head_of_corporate_affairs"
    assert by["Amy Long"]["slot"] == "cmo"        # title-first card
    assert "Read More" not in by                  # junk filtered


def test_resolver_site_source_needs_no_bright_data(state, monkeypatch):
    from tool.contacts import resolver, site_pages
    monkeypatch.setattr(site_pages, "harvest", lambda c, **k: {
        "domain": "acme.com",
        "people": [{"name": "Jane Smith",
                    "title": "Chief Communications Officer",
                    "slot": "cco", "url": "https://acme.com/leadership"}],
        "emails": [], "pages": [], "at": _iso()})
    monkeypatch.setattr(resolver, "_query_companies_house",
                        lambda c, s: ([], resolver.SourceQuery(
                            source="companies_house", returned_data=False)))
    monkeypatch.setattr(resolver, "_query_rns",
                        lambda c, s: ([], resolver.SourceQuery(
                            source="rns_announcements", returned_data=False)))
    entry, record = resolver.resolve("Acme", "cco", fetch=None)
    assert entry is not None and entry.name == "Jane Smith"
    assert entry.source_label == "site_leadership"
    assert entry.confidence >= 0.70            # clears the named floor
    assert any(s.source == "site_leadership" and s.used
               for s in record.sources_queried)


def test_format_detection_and_guess(state, monkeypatch):
    from tool.contacts import email_resolver as er
    assert er._detect_format("Jane Smith", "jane.smith@x.com") == "first.last"
    assert er._detect_format("Jane Smith", "jsmith@x.com") == "flast"
    assert er._detect_format("Tom O'Brien", "tom.obrien@x.com") == "first.last"
    assert er._detect_format("Jane Smith", "press@x.com") is None
    monkeypatch.setattr(er, "observed_pairs", lambda c: [
        {"name": "Tom Brown", "email": "tom.brown@acme.com", "url": "u1",
         "in_house": True},
        {"name": "Amy Long", "email": "amy.long@acme.com", "url": "u2",
         "in_house": True},
    ])
    g = er.format_guess("Acme", "Jane Smith")
    assert g == {"email": "jane.smith@acme.com", "status": "pattern",
                 "source_url": "u1"}
    assert er.format_guess("Acme", "Cher") is None     # single name


def test_resolve_email_falls_to_pattern_and_stays_unsendable(
        state, monkeypatch):
    from tool.contacts import email_resolver as er
    from tool import rns_contacts
    from tool.contacts import site_pages
    monkeypatch.setattr(rns_contacts, "published_emails", lambda c: [])
    monkeypatch.setattr(site_pages, "harvest", lambda c, **k: {
        "domain": "acme.com", "people": [], "pages": [], "at": _iso(),
        "emails": [{"email": "tom.brown@acme.com", "name": "Tom Brown",
                    "url": "u1"}]})
    e = ContactEntry(name="Jane Smith", role_title="CCO", role_slot="cco",
                     verified_at=_iso(2), confidence=0.85)
    assert er.resolve_email("Acme", e)
    assert e.email == "jane.smith@acme.com"
    assert e.email_status == "pattern"
    assert e.email_source == "format_inference"
    assert not e.email_is_sendable()           # policy: never one-click
    # ...but it IS visible to the AD through the contact dict:
    from tool.hiring_manager import best_named_contact
    from tool.contacts.store import upsert_contact
    contacts = {}
    upsert_contact(contacts, "Acme", "cco", e)
    nc = best_named_contact("Acme", ("cco",), contacts=contacts)
    assert nc["email"] == "jane.smith@acme.com"
    assert nc["email_status"] == "pattern"
    from tool.outreach import sendable_state
    ok, why = sendable_state({**nc, "title": "CCO"})
    assert not ok and "pattern" in why


def test_site_published_beats_format_guess(state, monkeypatch):
    from tool.contacts import email_resolver as er
    from tool import rns_contacts
    from tool.contacts import site_pages
    monkeypatch.setattr(rns_contacts, "published_emails", lambda c: [])
    monkeypatch.setattr(site_pages, "harvest", lambda c, **k: {
        "domain": "acme.com", "people": [], "pages": [], "at": _iso(),
        "emails": [{"email": "jane.smith@acme.com", "name": "Jane Smith",
                    "url": "https://acme.com/media"}]})
    found = er.find_for_person("Acme", "Jane Smith")
    assert found["status"] == "published"      # printed on their own site
    assert found["source_url"] == "https://acme.com/media"


def test_fill_for_signals_names_live_job_companies(state, monkeypatch):
    from tool.contacts import bd_poc_fill
    from tool.contacts.schema import ContactEntry as CE, ResolutionRecord
    from tool.contacts.store import load_contacts
    from tool.hiring_manager import resolve_lead_contact

    def fake_resolver(company, slot, fetch=None):
        rec = ResolutionRecord(timestamp=_iso(), company=company,
                               role_slot=slot, role_title_query=slot,
                               outcome="resolved_verified")
        if company == "Acme Utilities" and slot == "head_of_ic":
            return CE(name="Iva Note", role_title="Head of Internal Comms",
                      role_slot=slot, verified_at=_iso(),
                      confidence=0.72), rec
        rec.outcome = "resolved_no_match"
        return None, rec

    job = {"kind": "job", "title": "Internal Communications Manager",
           "company": "Acme Utilities", "url": "https://j/1",
           "summary": "", "source": "Adzuna", "geo": "UK"}
    stats = bd_poc_fill.fill_for_signals(
        [job, {"kind": "rns", "title": "x", "company": "Skip Co"}],
        desk="comms", resolver=fake_resolver,
        profile_resolver=lambda c, n: None)
    assert stats["resolved"] == 1
    c = resolve_lead_contact(job, contacts=load_contacts())
    assert c["name"] == "Iva Note"             # the live job is now named
    assert c["confidence"] > 0.5


def test_capability_line_reports_bright_data_state(state, monkeypatch):
    from tool.contacts import bd_poc_fill
    line = bd_poc_fill.capability_line()
    assert "bright_data=" in line and "anthropic_key=" in line


# ====================================================================
# Final-audit fixes: press-title taxonomy + in-house-only inference
# ====================================================================
def test_press_facing_titles_classify_into_the_comms_family():
    from tool.contacts.resolver import classify_title
    assert classify_title("Head of Media Relations") == "head_of_comms"
    assert classify_title("Director of Media Relations") == "head_of_comms"
    assert classify_title("Head of Press Office") == "head_of_comms"
    assert classify_title("Head of Public Relations") == "head_of_comms"
    assert classify_title("Communications Director") == "head_of_comms"
    assert classify_title("Corporate Communications Director") == "head_of_comms"
    assert classify_title("Director of External Communications") == "head_of_comms"
    # Non-regression: the seniors keep their existing slots.
    assert classify_title("Group Communications Director") == "cco"
    assert classify_title("Internal Communications Director") == "head_of_ic"
    assert classify_title("Director of Corporate Affairs") == "head_of_corporate_affairs"
    # Juniors still classify to nothing.
    assert classify_title("Press Officer") is None
    assert classify_title("Media Relations Manager") is None


def test_format_inference_ignores_agency_pairs(state, monkeypatch):
    from tool.contacts import email_resolver as er
    # Only agency pairings observed -> NO guess (never build on the
    # agency's domain).
    monkeypatch.setattr(er, "observed_pairs", lambda c: [
        {"name": "Tom Jones", "email": "tjones@buchanan.uk.com",
         "url": "u1", "in_house": False}])
    assert er.format_guess("Acme", "Jane Smith") is None
    # An in-house pairing alongside agency noise -> in-house wins.
    monkeypatch.setattr(er, "observed_pairs", lambda c: [
        {"name": "Tom Jones", "email": "tjones@buchanan.uk.com",
         "url": "u1", "in_house": False},
        {"name": "Amy Long", "email": "amy.long@acme.com",
         "url": "u2", "in_house": True}])
    g = er.format_guess("Acme", "Jane Smith")
    assert g["email"] == "jane.smith@acme.com"
