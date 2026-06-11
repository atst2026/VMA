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

Budgets: Hunter's free tier is ~25 searches / 50 verifications a MONTH
(finder and domain-search calls both consume a "search"). A persistent
per-calendar-month ledger (state/hunter_ledger.json) hard-caps spend
BELOW the free allowance — overridable via HUNTER_MONTHLY_SEARCH_BUDGET
/ HUNTER_MONTHLY_VERIFY_BUDGET once the account is paid — plus small
per-run caps so a single run can't drain the month. An address checked
within EMAIL_FRESHNESS_DAYS is never re-spent. Free sources (the ad
itself, RNS enquiries blocks, model web research) always run first;
Hunter is the last resort.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from tool.contacts.schema import ContactEntry
from tool.state_paths import state_dir

log = logging.getLogger("brief.contacts.email")

HUNTER_BASE = "https://api.hunter.io/v2"
# Per-run caps (spread the monthly allowance across the month).
SEARCH_CAP_PER_RUN = 5
VERIFIER_CAP_PER_RUN = 10
# Monthly defaults sized for the FREE tier with headroom left for the
# account owner's own manual searches on hunter.io.
DEFAULT_MONTHLY_SEARCHES = 20
DEFAULT_MONTHLY_VERIFICATIONS = 40
# Hunter email-finder score (0-100) below which a found address isn't
# worth storing even as a pattern guess.
MIN_FINDER_SCORE = 60

_RUN = {"search": 0, "verify": 0}


def _key() -> str:
    return (os.environ.get("HUNTER_API_KEY") or "").strip()


def reset_budget() -> None:
    _RUN["search"] = 0
    _RUN["verify"] = 0


# ---- Monthly ledger (persists across runs; survives in the Actions
# state cache like every other state file) ------------------------------
def _ledger_file():
    return state_dir() / "hunter_ledger.json"


def _month_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load_ledger() -> dict:
    try:
        f = _ledger_file()
        d = json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        d = {}
    if d.get("month") != _month_now():
        d = {"month": _month_now(), "searches": 0, "verifications": 0}
    d.setdefault("searches", 0)
    d.setdefault("verifications", 0)
    return d


def _save_ledger(d: dict) -> None:
    try:
        _ledger_file().write_text(json.dumps(d, indent=1))
    except Exception:
        pass


def _monthly_cap(kind: str) -> int:
    env = ("HUNTER_MONTHLY_SEARCH_BUDGET" if kind == "search"
           else "HUNTER_MONTHLY_VERIFY_BUDGET")
    default = (DEFAULT_MONTHLY_SEARCHES if kind == "search"
               else DEFAULT_MONTHLY_VERIFICATIONS)
    try:
        return max(0, int(os.environ.get(env) or default))
    except Exception:
        return default


def _spend(kind: str) -> bool:
    """Reserve one Hunter call of `kind` ('search' | 'verify') against
    both the per-run cap and the persistent monthly ledger. False means
    DON'T make the call."""
    run_cap = SEARCH_CAP_PER_RUN if kind == "search" else VERIFIER_CAP_PER_RUN
    if _RUN[kind if kind == "search" else "verify"] >= run_cap:
        return False
    ledger = _load_ledger()
    field = "searches" if kind == "search" else "verifications"
    if ledger[field] >= _monthly_cap(kind):
        log.info("hunter %s budget for %s exhausted (%d/%d) — skipping",
                 kind, ledger["month"], ledger[field], _monthly_cap(kind))
        return False
    ledger[field] += 1
    _save_ledger(ledger)
    _RUN["search" if kind == "search" else "verify"] += 1
    return True


def budget_remaining() -> dict:
    """For logs/UI: what's left this month."""
    ledger = _load_ledger()
    return {
        "month": ledger["month"],
        "searches_left": max(0, _monthly_cap("search") - ledger["searches"]),
        "verifications_left": max(
            0, _monthly_cap("verify") - ledger["verifications"]),
    }


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
    if not _key() or not email or not _spend("verify"):
        return ""
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
    if not _key() or not domain:
        return None
    parts = [p for p in re.split(r"\s+", (full_name or "").strip()) if p]
    if len(parts) < 2 or not _spend("search"):
        return None
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


def hunter_domain_contacts(domain: str, desk: str = "comms") -> list[dict]:
    """Hunter domain-search: ONE search credit returns up to 10 named
    people in the right department WITH their addresses, positions and
    per-address verification — by far the most contact-per-credit call
    on the free tier. Returns [{email, name, position, score,
    verification_status, source_url}] or []."""
    if not _key() or not domain or not _spend("search"):
        return []
    data = _get("domain-search", {
        "domain": domain,
        "department": ("marketing" if desk == "marketing"
                       else "communication"),
        "seniority": "senior,executive",
        "limit": 10,
    })
    out = []
    for e in (data or {}).get("emails") or []:
        if not isinstance(e, dict) or not e.get("value"):
            continue
        if (e.get("type") or "") == "generic":
            continue
        name = " ".join(p for p in (e.get("first_name"),
                                    e.get("last_name")) if p)
        srcs = e.get("sources") or []
        out.append({
            "email": e["value"],
            "name": name,
            "position": e.get("position") or "",
            "score": e.get("confidence") or 0,
            "verification_status": ((e.get("verification") or {})
                                    .get("status") or ""),
            "source_url": (srcs[0].get("uri") if srcs
                           and isinstance(srcs[0], dict) else "") or "",
        })
    return out


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


def find_for_person(company: str, full_name: str,
                    domain: str | None = None) -> dict | None:
    """The waterfall for a person who is NOT in the roster — e.g. the
    contact a job ad names. Published sources first, Hunter last.
    Returns {email, status, source_url} or None."""
    if not (full_name or "").strip():
        return None
    pub = _published_for_person(company, full_name)
    if pub:
        verdict = hunter_verify(pub["email"])
        if verdict != "invalid":
            return {"email": pub["email"],
                    "status": ("verified" if verdict == "valid"
                               else "published"),
                    "source_url": pub.get("url") or ""}
    if not domain:
        try:
            from tool.company_domain import resolve_domain
            domain = resolve_domain(company)
        except Exception:
            domain = None
    if domain:
        found = hunter_find(domain, full_name)
        if found and (found["score"] or 0) >= MIN_FINDER_SCORE:
            status = ("verified" if found["verification_status"] == "valid"
                      else "pattern")
            if status == "pattern" and found["verification_status"] == "":
                verdict = hunter_verify(found["email"])
                if verdict == "valid":
                    status = "verified"
                elif verdict == "invalid":
                    return None
            return {"email": found["email"], "status": status,
                    "source_url": ""}
    return None


def fill_from_domain_search(company: str, slots: tuple,
                            contacts: dict, desk: str = "comms") -> bool:
    """Last-resort NAMED-CONTACT fill for a company where the ad, the
    roster and the researcher all came up empty: one domain-search
    credit buys the senior comms/marketing people Hunter has on file.
    A hit must match one of the hypothesis slots' title patterns (the
    same patterns the deterministic resolver trusts) before it's
    stored, at conservative confidence — the researcher and registry
    sources outrank and can overwrite it later. Returns True if the
    roster changed; caller saves."""
    try:
        from tool.company_domain import resolve_domain
        from tool.contacts.resolver import ROLE_TITLE_PATTERNS
        from tool.contacts.store import upsert_contact, get_contact

        card = get_contact(contacts, company)
        if card:
            for s in slots:
                e = card.get(s)
                if e and e.name and e.is_fresh() and e.meets_named_confidence():
                    return False   # already have someone — don't spend
        domain = resolve_domain(company)
        if not domain:
            return False
        people = hunter_domain_contacts(domain, desk=desk)
        if not people:
            return False
        for slot in slots:
            pattern = ROLE_TITLE_PATTERNS.get(slot)
            if pattern is None:
                continue
            matches = [p for p in people
                       if p["name"] and pattern.search(p["position"] or "")]
            if not matches:
                continue
            best = max(matches, key=lambda p: p.get("score") or 0)
            now = datetime.now(timezone.utc).isoformat()
            status = ("verified" if best["verification_status"] == "valid"
                      else "pattern")
            upsert_contact(contacts, company, slot, ContactEntry(
                name=best["name"],
                role_title=best["position"] or "",
                role_slot=slot,
                source_url=best["source_url"],
                source_label="Hunter domain search",
                verified_at=now,
                confidence=0.72,
                email=best["email"],
                email_status=status,
                email_source="hunter",
                email_source_url=best["source_url"],
                email_checked_at=now,
            ))
            log.info("domain-search fill %s/%s: %s — %s (email %s)",
                     company, slot, best["name"], best["position"], status)
            return True
        return False
    except Exception as e:
        log.info("domain-search fill skipped (%s)", e)
        return False
