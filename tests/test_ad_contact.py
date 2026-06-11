"""The ad's own named contact — extraction precision and its priority
inside resolve_lead_contact.

What must never regress:
  1. A named contact printed in the ad outranks every inference and
     roster entry, and a printed address is sendable "published".
  2. Application inboxes (jobs@/recruitment@) are never the contact.
  3. Agency-posted ads ("our client") never surface the agency's
     recruiter as the hiring contact.
"""
from tool.contacts import ad_contact


def _sig(summary, title="Head of Internal Communications",
         company="Leeds Teaching Hospitals", url="https://jobs/x"):
    return {"kind": "job", "title": title, "company": company,
            "summary": summary, "url": url, "source": "NHS Jobs",
            "geo": "UK"}


NHS_AD = (
    "An exciting opportunity to join our team. The role reports to the "
    "Director of Communications. For further details or an informal "
    "discussion please contact Jane Smith, Head of Communications and "
    "Engagement, on 0113 243 0000 or email jane.smith@leedsth.nhs.uk. "
    "To apply, send your CV to recruitment@leedsth.nhs.uk."
)


def test_extracts_name_title_email_and_skips_application_inbox():
    ad = ad_contact.extract(_sig(NHS_AD))
    assert ad["name"] == "Jane Smith"
    assert "Head of Communications" in ad["title"]
    assert ad["email"] == "jane.smith@leedsth.nhs.uk"
    assert ad["source_url"] == "https://jobs/x"
    assert ad["phone"].startswith("0113")


def test_name_without_email_still_extracts():
    ad = ad_contact.extract(_sig(
        "For an informal conversation about the role please contact "
        "Tom O'Brien, Director of External Affairs, via the switchboard."))
    assert ad["name"] == "Tom O'Brien"
    assert ad["email"] == ""


def test_application_inbox_alone_is_not_a_contact():
    ad = ad_contact.extract(_sig(
        "A great role in our busy press office. Apply with CV and "
        "covering letter to jobs@acme.org.uk by 30 June. " + "x " * 20))
    assert ad is None
    assert ad_contact.is_application_inbox("recruitment@x.com")
    assert ad_contact.is_application_inbox("jobs.london@x.com")
    assert not ad_contact.is_application_inbox("jane.smith@x.com")


def test_agency_ads_are_skipped():
    ad = ad_contact.extract(_sig(
        "We are delighted to be working with our client, a FTSE 250 "
        "utility, to appoint a Head of Communications. For details "
        "contact Sarah Jones, Recruitment Consultant, on 020 7000 0000."))
    assert ad is None


def test_role_words_never_become_a_name():
    ad = ad_contact.extract(_sig(
        "Please contact Human Resources for an application pack. The "
        "post-holder will join the Communications Team. " + "pad " * 15))
    assert ad is None


def test_resolve_lead_contact_prefers_ad_contact(tmp_path, monkeypatch):
    import tool.state_paths as sp
    monkeypatch.setattr(sp, "state_root", lambda profile_key=None: tmp_path)
    from tool.hiring_manager import resolve_lead_contact
    from tool.outreach import sendable_state

    c = resolve_lead_contact(_sig(NHS_AD), contacts={})
    assert c["basis"] == "ad_named_contact"
    assert c["name"] == "Jane Smith"
    assert c["email"] == "jane.smith@leedsth.nhs.uk"
    assert c["email_status"] == "published"
    assert c["email_source_url"] == "https://jobs/x"
    assert c["confidence"] == 0.88
    ok, _ = sendable_state(c)
    assert ok

    # Enrichment-found address (stored on the signal) is honoured too.
    s = _sig("For an informal discussion please contact Amy Long, "
             "Head of Communications, via our switchboard.")
    s["ad_contact_email"] = "amy.long@acme.com"
    s["ad_contact_email_status"] = "verified"
    s["ad_contact_email_source"] = "https://acme.com/press"
    c2 = resolve_lead_contact(s, contacts={})
    assert c2["name"] == "Amy Long"
    assert c2["email"] == "amy.long@acme.com"
    assert c2["email_status"] == "verified"


def test_non_job_signals_never_get_ad_contacts(tmp_path, monkeypatch):
    import tool.state_paths as sp
    monkeypatch.setattr(sp, "state_root", lambda profile_key=None: tmp_path)
    from tool.hiring_manager import resolve_lead_contact
    c = resolve_lead_contact({
        "kind": "rns", "title": "Acme plc appoints advisers",
        "company": "Acme", "summary": NHS_AD}, contacts={})
    assert c.get("basis") != "ad_named_contact"
