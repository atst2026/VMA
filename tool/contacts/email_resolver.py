"""Work-email resolution for named hiring contacts — the ground-truth
layer of SEND OUTREACH.

Finding the right person is reasoning (tool/contacts/resolver.py + the
per-job researcher); finding their ADDRESS is a data problem, so this
module is a waterfall over evidence, strongest first:

  1. published   — the address printed verbatim in a source we archived
                   (RNS enquiries blocks via tool/rns_contacts). Citable
                   URL, no guessing. Verified on top when Hunter is
                   configured; an explicit "invalid" verdict discards it.
  2. hunter find — Hunter's email-finder for (domain, first, last). Its
                   own verification decides the status: "valid" stores
                   as verified (sendable), anything weaker stores as
                   pattern (visible to the human, never one-click sent).

Statuses and the sendable gate live in schema.py (EMAIL_SENDABLE_STATUSES):
verified/published may be sent to; pattern may not. No key, no network,
no problem — every step is a graceful no-op and the lead simply stays
on the LinkedIn/copy-paste route.

Budgets: Hunter's free tier is 25 finds / 50 verifies a MONTH, so both
call types are capped per run and an address checked within
EMAIL_FRESHNESS_DAYS is never re-spent.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

from tool.contacts.schema import ContactEntry

log = logging.getLogger("brief.contacts.email")

HUNTER_BASE = "https://api.hunter.io/v2"
FINDER_CAP_PER_RUN = 10
VERIFIER_CAP_PER_RUN = 15
# Hunter email-finder score (0-100) below which a found address isn't
# worth storing even as a pattern guess.
MIN_FINDER_SCORE = 60

_BUDGET = {"find": 0, "verify": 0}


def _key() -> str:
    return (os.environ.get("HUNTER_API_KEY") or "").strip()


def reset_budget() -> None:
    _BUDGET["find"] = 0
    _BUDGET["verify"] = 0


def _get(path: str, params: dict) -> dict | None:
    try:
        import requests
        r = requests.get(f"{HUNTER_BASE}/{path}",
                         params={**params, "api_key": _key()}, timeout=15)
        if r.status_code != 200:
            log.info("hunter %s -> HTTP %s", path, r.status_code)
            return None
        return (r.json() or {}).get("data") or {}
    except Exception as e:
        log.info("hunter %s skipped (%s)", path, e)
        return None


def hunter_verify(email: str) -> str:
    """Hunter's verdict for one address: 'valid' / 'invalid' /
    'accept_all' / 'unknown' / '' (no key, budget spent, or error)."""
    if not _key() or not email or _BUDGET["verify"] >= VERIFIER_CAP_PER_RUN:
        return ""
    _BUDGET["verify"] += 1
    data = _get("email-verifier", {"email": email})
    status = (data or {}).get("status") or ""
    # Hunter reports webmail/disposable as their own statuses; for our
    # purposes a personal or throwaway inbox is not a work address.
    if status in ("webmail", "disposable"):
        return "invalid"
    return status


def hunter_find(domain: str, full_name: str) -> dict | None:
    """Hunter email-finder for one person at one domain.
    Returns {email, score, verification_status} or None."""
    if not _key() or not domain or _BUDGET["find"] >= FINDER_CAP_PER_RUN:
        return None
    parts = [p for p in re.split(r"\s+", (full_name or "").strip()) if p]
    if len(parts) < 2:
        return None
    _BUDGET["find"] += 1
    data = _get("email-finder", {"domain": domain,
                                 "first_name": parts[0],
                                 "last_name": parts[-1]})
    if not data or not data.get("email"):
        return None
    return {
        "email": data["email"],
        "score": data.get("score") or 0,
        "verification_status": ((data.get("verification") or {})
                                .get("status") or ""),
    }


def _surname(full_name: str) -> str:
    parts = [p for p in re.split(r"\s+", (full_name or "").strip()) if p]
    return parts[-1].lower() if len(parts) >= 2 else ""


def _published_for_person(company: str, full_name: str) -> dict | None:
    """A published address attributable to THIS person: the name hint
    next to the address matches, or their surname is in the local part.
    In-house domains outrank the issuer's PR agency's."""
    try:
        from tool import rns_contacts
        cands = rns_contacts.published_emails(company)
    except Exception:
        return None
    if not cands:
        return None
    sur = _surname(full_name)
    if not sur:
        return None
    name_low = (full_name or "").strip().lower()

    def _matches(c: dict) -> bool:
        if c.get("generic"):
            return False
        hint = (c.get("name_hint") or "").strip().lower()
        if hint and (hint == name_low or sur in hint.split()):
            return True
        local = c["email"].split("@", 1)[0].lower()
        return sur in re.split(r"[._\-]", local)

    matched = [c for c in cands if _matches(c)]
    # Newest sighting first, then stable-sort in-house domains ahead of
    # the issuer's PR agency.
    matched.sort(key=lambda c: c.get("at") or "", reverse=True)
    matched.sort(key=lambda c: not c.get("in_house"))
    return matched[0] if matched else None


def resolve_email(company: str, entry: ContactEntry,
                  domain: str | None = None) -> bool:
    """Fill the email fields on `entry` in place. Returns True if the
    entry changed. Skips entries whose address was checked recently
    (EMAIL_FRESHNESS_DAYS) — re-verification is the nightly queue's
    job, not every caller's."""
    if not entry or not entry.name:
        return False
    if entry.email_is_sendable():
        return False
    now = datetime.now(timezone.utc).isoformat()

    # 1. Published source (RNS enquiries archive) — citable, no guess.
    pub = _published_for_person(company, entry.name)
    if pub:
        verdict = hunter_verify(pub["email"])
        if verdict != "invalid":
            entry.email = pub["email"]
            entry.email_status = "verified" if verdict == "valid" else "published"
            entry.email_source = "rns_enquiries"
            entry.email_source_url = pub.get("url") or ""
            entry.email_checked_at = now
            log.info("email %s @ %s: published (%s)%s", entry.name,
                     company, entry.email_status,
                     " + verified" if verdict == "valid" else "")
            return True
        log.info("email %s @ %s: published address failed verification — "
                 "dropped", entry.name, company)

    # 2. Hunter finder — needs the company's real domain.
    if not domain:
        try:
            from tool.company_domain import resolve_domain
            domain = resolve_domain(company)
        except Exception:
            domain = None
    if domain:
        found = hunter_find(domain, entry.name)
        if found and (found["score"] or 0) >= MIN_FINDER_SCORE:
            status = ("verified" if found["verification_status"] == "valid"
                      else "pattern")
            if status == "pattern" and found["verification_status"] == "":
                # Finder gave no verdict — spend one verifier call to
                # try to upgrade; "invalid" kills it outright.
                verdict = hunter_verify(found["email"])
                if verdict == "valid":
                    status = "verified"
                elif verdict == "invalid":
                    return False
            entry.email = found["email"]
            entry.email_status = status
            entry.email_source = "hunter"
            entry.email_source_url = ""
            entry.email_checked_at = now
            log.info("email %s @ %s: hunter %s (score %s)", entry.name,
                     company, status, found["score"])
            return True
    return False
