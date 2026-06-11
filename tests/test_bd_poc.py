"""BD Point of Contact — the senior comms/marketing/HR owner of future
hires, never the C-suite statutory seats.

What must never regress:
  1. CEO/CFO/chair/GC/IR roster entries NEVER surface as a BD point of
     contact, even when they're the only names on the card.
  2. The desk decides the family: comms slots on the comms desk,
     marketing slots on the marketing desk; HR (chro) rides along on
     both, after the function owner.
  3. Verified-or-fallback: no named owner -> precise Recruiter
     role-searches, never an empty section.
  4. LinkedIn only — the items carry profile/search URLs, no email.
"""
from datetime import datetime, timedelta, timezone

import pytest

from tool.contacts.schema import ContactEntry
from tool.contacts.store import upsert_contact
from tool.hiring_manager import bd_points_of_contact


def _iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def state(tmp_path, monkeypatch):
    import tool.state_paths as sp
    monkeypatch.setattr(sp, "state_root", lambda profile_key=None: tmp_path)
    return tmp_path


def _entry(name, slot, title, days=5, conf=0.85, linkedin=None):
    return ContactEntry(name=name, role_title=title, role_slot=slot,
                        linkedin_url=linkedin, verified_at=_iso(days),
                        confidence=conf)


def test_function_owner_first_then_hr_never_csuite(state):
    contacts = {}
    upsert_contact(contacts, "Acme", "ceo",
                   _entry("Carl Exec", "ceo", "Chief Executive"))
    upsert_contact(contacts, "Acme", "cfo",
                   _entry("Fin Chief", "cfo", "CFO"))
    upsert_contact(contacts, "Acme", "chro",
                   _entry("Holly Rae", "chro", "Chief People Officer"))
    upsert_contact(contacts, "Acme", "head_of_comms",
                   _entry("Jane Smith", "head_of_comms",
                          "Director of Communications",
                          linkedin="https://linkedin.com/in/janesmith"))
    poc = bd_points_of_contact("Acme", desk="comms", contacts=contacts)
    names = [p["name"] for p in poc]
    assert names[0] == "Jane Smith"          # function owner leads
    assert "Holly Rae" in names              # HR rides along
    assert "Carl Exec" not in names and "Fin Chief" not in names
    assert poc[0]["url"] == "https://linkedin.com/in/janesmith"
    # No roster profile -> name+company Recruiter search, never a dead end.
    hr = next(p for p in poc if p["name"] == "Holly Rae")
    assert "linkedin.com/talent/search" in hr["url"]
    assert "Holly%20Rae" in hr["url"] or "Holly+Rae" in hr["url"]


def test_csuite_only_card_yields_nothing_not_the_ceo(state):
    """No generic fallback rows: with no named function owner the
    section is empty (hidden), and the CEO never leaks through."""
    contacts = {}
    upsert_contact(contacts, "Acme", "ceo",
                   _entry("Carl Exec", "ceo", "Chief Executive"))
    assert bd_points_of_contact("Acme", desk="comms",
                                contacts=contacts) == []
    assert bd_points_of_contact("Nocard Ltd", desk="comms",
                                contacts={}) == []


def test_marketing_desk_uses_marketing_family(state):
    contacts = {}
    upsert_contact(contacts, "Brandco", "cmo",
                   _entry("Mark Eter", "cmo", "Chief Marketing Officer"))
    upsert_contact(contacts, "Brandco", "head_of_comms",
                   _entry("Jane Smith", "head_of_comms", "Comms Director"))
    poc = bd_points_of_contact("Brandco", desk="marketing",
                               contacts=contacts)
    assert poc[0]["name"] == "Mark Eter"
    assert all(p["name"] != "Jane Smith" for p in poc)


def test_stale_flagged_weak_and_duplicate_entries(state):
    from tool import contact_flags
    contacts = {}
    upsert_contact(contacts, "Acme", "cco",
                   _entry("Old Hand", "cco", "CCO", days=400))
    upsert_contact(contacts, "Acme", "head_of_comms",
                   _entry("Fresh Face", "head_of_comms", "Comms Director"))
    upsert_contact(contacts, "Acme", "head_of_ic",
                   _entry("Fresh Face", "head_of_ic", "Head of IC"))
    upsert_contact(contacts, "Acme", "head_of_corporate_affairs",
                   _entry("Weak Guess", "head_of_corporate_affairs",
                          "Corp Affairs", conf=0.5))
    upsert_contact(contacts, "Acme", "chro",
                   _entry("Wrong Person", "chro", "CHRO"))
    contact_flags.flag("Acme", "chro", "Wrong Person")
    poc = bd_points_of_contact("Acme", desk="comms", contacts=contacts)
    names = [p["name"] for p in poc]
    assert names[0] == "Fresh Face"          # fresh outranks stale
    assert names.count("Fresh Face") == 1    # deduped across slots
    assert "Weak Guess" not in names         # confidence floor holds
    assert "Wrong Person" not in names       # Sara's flag respected
    old = next(p for p in poc if p["name"] == "Old Hand")
    assert old["stale"] is True              # surfaced, marked verify


def test_fill_resolves_names_and_attaches_profiles(state):
    """The nightly fill: resolver hit -> roster entry with a real /in/
    profile attached by name; companies with an owner never spend."""
    from tool.contacts import bd_poc_fill
    from tool.contacts.schema import ContactEntry as CE
    from tool.contacts.store import load_contacts

    seen_calls = []

    def fake_resolver(company, slot, fetch=None):
        from tool.contacts.schema import ResolutionRecord
        seen_calls.append((company, slot))
        rec = ResolutionRecord(timestamp=_iso(), company=company,
                               role_slot=slot, role_title_query=slot,
                               outcome="resolved_verified")
        if company == "Acme" and slot == "cco":
            return CE(name="Jane Smith", role_title="Group CCO",
                      role_slot="cco", verified_at=_iso(),
                      confidence=0.85), rec
        rec.outcome = "resolved_no_match"
        return None, rec

    profiles = {"Acme|Jane Smith":
                {"url": "https://uk.linkedin.com/in/janesmith"}}
    stats = bd_poc_fill.run(
        ["Acme", "Emptyco"], desk="comms",
        resolver=fake_resolver, fetch=lambda u: None,
        profile_resolver=lambda c, n: profiles.get(f"{c}|{n}"))
    assert stats["resolved"] == 1 and stats["profile_links"] == 1, \
        (stats, seen_calls)
    contacts = load_contacts()
    poc = bd_points_of_contact("Acme", desk="comms", contacts=contacts)
    assert poc[0]["name"] == "Jane Smith"
    assert poc[0]["url"] == "https://uk.linkedin.com/in/janesmith"
    # Acme now has its owner — never re-spent. Emptyco's untried slots
    # are probed progressively (2/run); once every slot is inside the
    # 7-day attempt ledger, runs make zero resolver calls.
    calls = {"n": 0}

    def counting_resolver(company, slot, fetch=None):
        calls["n"] += 1
        return fake_resolver(company, slot, fetch)

    bd_poc_fill.run(["Acme", "Emptyco"], desk="comms",
                    resolver=counting_resolver, fetch=lambda u: None,
                    profile_resolver=lambda c, n: None)
    assert calls["n"] == 2          # only Emptyco's two remaining slots
    assert all(c == "Emptyco" for c, _ in seen_calls[-2:])
    calls["n"] = 0
    bd_poc_fill.run(["Acme", "Emptyco"], desk="comms",
                    resolver=counting_resolver, fetch=lambda u: None,
                    profile_resolver=lambda c, n: None)
    assert calls["n"] == 0          # everything ledgered — fully quiet


def test_fill_budget_caps_resolver_calls(state):
    from tool.contacts import bd_poc_fill
    calls = {"n": 0}

    def miss_resolver(company, slot, fetch=None):
        from tool.contacts.schema import ResolutionRecord
        calls["n"] += 1
        return None, ResolutionRecord(
            timestamp=_iso(), company=company, role_slot=slot,
            role_title_query=slot, outcome="resolved_no_match")

    bd_poc_fill.run([f"Co {i}" for i in range(20)], desk="comms",
                    max_resolutions=5, resolver=miss_resolver,
                    fetch=lambda u: None,
                    profile_resolver=lambda c, n: None)
    assert calls["n"] == 5


def test_no_emails_anywhere(state):
    contacts = {}
    upsert_contact(contacts, "Acme", "cco", _entry(
        "Jane Smith", "cco", "CCO"))
    contacts["Acme"].entries["cco"].email = "jane@acme.com"
    contacts["Acme"].entries["cco"].email_status = "verified"
    poc = bd_points_of_contact("Acme", desk="comms", contacts=contacts)
    assert all("email" not in p for p in poc)
