"""The job ad's own named contact — the highest-precision contact source.

UK public-sector, NHS, charity and many corporate ads print the hiring
contact IN the advert: "For an informal discussion please contact Jane
Smith, Head of Communications, on 0113 ... or jane.smith@example.nhs.uk".
That person is not an inference — the employer attached them to THIS
vacancy. When present, this beats every other source: right entity,
right seat and right person are all answered by the ad itself, and a
printed address is sendable "published" evidence with the ad as the
citable URL.

Deliberately conservative:
  - application inboxes (jobs@/recruitment@/apply@ ...) are never
    treated as the hiring contact — they're where CVs go, not who owns
    the hire;
  - agency-posted ads ("our client", "recruitment consultant") are
    skipped — the named person is the competing recruiter, not the
    buyer;
  - a name must look like a person (two/three capitalised words, no
    role/org words).

Pure functions over the signal dict; no network, no state. Cheap enough
to run inside resolve_lead_contact on every request.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("brief.contacts.ad")

_EMAIL_RX = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._%+\-']*@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Local parts that mean "the application inbox", never a person who owns
# the hire. Matched on the whole local part or its first dotted token.
_APPLICATION_LOCALS = frozenset((
    "jobs", "job", "recruitment", "recruiting", "recruit", "apply",
    "applications", "application", "careers", "career", "vacancies",
    "vacancy", "hr", "resourcing", "talent", "cv", "cvs", "hiring",
    "people", "peopleteam", "enquiries", "info", "admin", "office",
    "hello", "contact", "reception",
))

# Phrases that mark the ad as agency-posted: the named contact would be
# the competing recruiter, not the employer's hiring owner.
_AGENCY_MARKERS = (
    "our client", "on behalf of our client", "the client", "this client",
    "recruitment consultant", "recruiting consultant",
    "recruitment partner at", "managing consultant",
    "executive search firm", "search consultant",
)

# "...contact Jane Smith" / "...please email Jane Smith" /
# "...speak to Jane Smith" / "...informal chat with Jane Smith" —
# capture the capitalised name run after the verb. The word atom
# handles O'Brien, McDonald and Smith-Jones.
_NAME_WORD = r"[A-Z][a-z]*(?:[A-Z][a-z]+|['’\-][A-Za-z]+)*"
_CONTACT_NAME_RX = re.compile(
    r"(?:contact|speak\s+(?:to|with)|talk\s+to|call|email|"
    r"(?:chat|discussion|conversation|visit)\s+(?:with|please\s+contact)?)"
    rf"[,\s]+((?:{_NAME_WORD}\s+){{1,2}}{_NAME_WORD})",
)
# Optional ", Head of Communications" style title straight after the name.
_TITLE_AFTER_RX = re.compile(
    r"^[\s,–—\-(]+((?:[A-Z][A-Za-z&'\-]*|of|and|for|the|&)"
    r"(?:\s+(?:[A-Z][A-Za-z&'\-]*|of|and|for|the|&)){0,6})")
# Words that disqualify a capitalised run from being a person's name.
_NOT_A_PERSON = frozenset(w.lower() for w in (
    "Head", "Director", "Chief", "Manager", "Officer", "Team", "Group",
    "Department", "Service", "Services", "Trust", "Council", "University",
    "Hospital", "School", "College", "Centre", "Center", "Office",
    "Communications", "Marketing", "Resources", "Recruitment", "People",
    "Human", "Further", "Information", "Application", "Applications",
    "Please", "About", "Job", "Description", "Person", "Specification",
))


def _is_person_name(words: list[str]) -> bool:
    return (2 <= len(words) <= 3
            and not any(w.lower() in _NOT_A_PERSON for w in words))


def is_application_inbox(email: str) -> bool:
    """True for jobs@/recruitment@-style addresses — fine for sending a
    CV to, wrong for BD outreach to 'the person in charge of hiring'."""
    local = (email or "").lower().partition("@")[0]
    if local in _APPLICATION_LOCALS:
        return True
    head = re.split(r"[._\-]", local)[0]
    return head in _APPLICATION_LOCALS


def extract(signal: dict) -> dict | None:
    """The ad's own hiring contact, or None.

    {name, title, email, phone, source_url}
      - name may be "" when only a personal (non-application) address is
        printed with no name nearby;
      - email may be "" when the ad names the person without an address
        (the nightly email pass then hunts for it).
    Never raises."""
    try:
        text = " ".join(
            (signal.get(k) or "") for k in ("title", "summary"))
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 40:
            return None
        low = text.lower()
        if any(m in low for m in _AGENCY_MARKERS):
            return None

        name = ""
        title = ""
        name_end = -1
        for m in _CONTACT_NAME_RX.finditer(text):
            words = m.group(1).split()
            if not _is_person_name(words):
                continue
            name = " ".join(words)
            name_end = m.end(1)
            t = _TITLE_AFTER_RX.match(text[name_end:name_end + 90])
            if t:
                cand = t.group(1).strip(" ,-")
                # A real title contains a role word; "Jane Smith, Leeds"
                # must not become title="Leeds".
                if re.search(r"(?i)\b(head|director|chief|manager|lead|"
                             r"officer|partner|controller|vp|president|"
                             r"executive)\b", cand):
                    title = cand
            break

        email = ""
        person_emails = [e.group(0).strip(".,;:'")
                         for e in _EMAIL_RX.finditer(text)
                         if not is_application_inbox(e.group(0))]
        if name and person_emails:
            # Prefer an address that carries the named person's surname;
            # else the address printed nearest after the name.
            sur = name.split()[-1].lower()
            by_sur = [e for e in person_emails
                      if sur in re.split(r"[._\-@]", e.lower())]
            if by_sur:
                email = by_sur[0]
            else:
                after = [e for e in person_emails
                         if text.find(e) > name_end
                         and text.find(e) - name_end < 200]
                email = after[0] if after else ""
        elif person_emails and not name:
            # A bare personal work address with no name — still a direct
            # line to a human at the employer; name stays empty and the
            # named-contact gate keeps it out of one-click send until a
            # person is attached.
            email = person_emails[0]

        if not name and not email:
            return None
        phone_m = re.search(
            r"(?:\+44\s?|\b0)(?:\d[\s\-]?){9,10}\b", text)
        return {
            "name": name,
            "title": title,
            "email": email,
            "phone": (phone_m.group(0).strip() if phone_m else ""),
            "source_url": signal.get("url") or "",
        }
    except Exception as e:
        log.info("ad_contact extract skipped (%s)", e)
        return None
