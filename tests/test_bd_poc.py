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


def test_csuite_only_card_falls_back_to_role_searches(state):
    contacts = {}
    upsert_contact(contacts, "Acme", "ceo",
                   _entry("Carl Exec", "ceo", "Chief Executive"))
    poc = bd_points_of_contact("Acme", desk="comms", contacts=contacts)
    assert all(not p["name"] for p in poc)
    assert any("Communications" in p["title"] for p in poc)
    assert any("HR" in p["title"] for p in poc)
    assert all("linkedin.com/talent/search" in p["url"] for p in poc)


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
    # And the marketing fallback is marketing-flavoured.
    empty = bd_points_of_contact("Nocard Ltd", desk="marketing",
                                 contacts={})
    assert any("Marketing" in p["title"] for p in empty)


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


def test_no_emails_anywhere(state):
    contacts = {}
    upsert_contact(contacts, "Acme", "cco", _entry(
        "Jane Smith", "cco", "CCO"))
    contacts["Acme"].entries["cco"].email = "jane@acme.com"
    contacts["Acme"].entries["cco"].email_status = "verified"
    poc = bd_points_of_contact("Acme", desk="comms", contacts=contacts)
    assert all("email" not in p for p in poc)
