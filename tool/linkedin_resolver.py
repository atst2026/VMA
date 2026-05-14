#!/usr/bin/env python3
"""Resolve a (company, role) pair to a specific LinkedIn profile URL.

Uses Bright Data's Web Unlocker to query Google for
    "<role>" "<company>" site:linkedin.com/in
and parses the first /in/ URL out of the results.

Cached on disk (90-day TTL) so the same lookup never repeats. Free-tier
budget: 5,000 Web Unlocker requests / month. Each unique resolution
costs 1 request. Cache hits cost zero.

If BRIGHT_DATA_KEY is not set, every call returns None (graceful no-op);
the dashboard falls back to the company-employees URL pattern.
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests

log = logging.getLogger("brief.linkedin_resolver")

STATE_DIR = Path(__file__).resolve().parent / "state"
CACHE_FILE = STATE_DIR / "linkedin_profile_cache.json"
CACHE_TTL_DAYS = 90
STATE_DIR.mkdir(parents=True, exist_ok=True)

BRIGHT_DATA_KEY = os.environ.get("BRIGHT_DATA_KEY", "").strip()
# Zone name configured on the Bright Data account. The free tier
# defaults to a zone named 'web_unlocker' or 'web_unlocker1' — both
# work; this env var lets you override without touching code.
BD_ZONE = os.environ.get("BRIGHT_DATA_ZONE", "web_unlocker").strip()
BD_ENDPOINT = "https://api.brightdata.com/request"


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=0))


def _cache_valid(entry: dict) -> bool:
    """Entries older than CACHE_TTL_DAYS expire (people change jobs)."""
    try:
        ts = datetime.fromisoformat(entry.get("at", ""))
        return (datetime.now(timezone.utc) - ts) < timedelta(days=CACHE_TTL_DAYS)
    except Exception:
        return False


# Matches public LinkedIn profile URLs (any subdomain: www, uk, de, etc.)
_LINKEDIN_PROFILE_RX = re.compile(
    r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[a-zA-Z0-9\-_%~.]+/?',
    re.IGNORECASE,
)


def _parse_first_profile(html: str) -> str | None:
    if not html:
        return None
    for url in _LINKEDIN_PROFILE_RX.findall(html):
        # Skip Google redirects, archived pages, and pulse-article URLs
        u = url.rstrip(').,;"\'>')
        low = u.lower()
        if any(skip in low for skip in ("google.com", "web.archive.org",
                                          "pulse-article", "/jobs/", "/posts/")):
            continue
        return u
    return None


def _bright_data_fetch(url: str, timeout: int = 30) -> str | None:
    if not BRIGHT_DATA_KEY:
        return None
    payload = {"zone": BD_ZONE, "url": url, "format": "raw"}
    headers = {
        "Authorization": f"Bearer {BRIGHT_DATA_KEY}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(BD_ENDPOINT, json=payload, headers=headers, timeout=timeout)
        if r.status_code == 200 and r.text:
            return r.text
        log.info("Bright Data %s -> HTTP %s (%s)", url[:60], r.status_code, r.text[:120])
        return None
    except requests.RequestException as e:
        log.info("Bright Data fetch failed: %s", e)
        return None


def resolve_profile(company: str, role: str) -> dict | None:
    """Return {url, role, company, at} or None if unresolved.
    Cached across runs."""
    company = (company or "").strip()
    role = (role or "").strip()
    if not company or not role:
        return None
    key = f"{role.lower()}|{company.lower()}"

    cache = _load_cache()
    entry = cache.get(key)
    if entry and _cache_valid(entry):
        return entry if entry.get("url") else None

    if not BRIGHT_DATA_KEY:
        return None   # Graceful no-op when BD isn't configured

    query = f'"{role}" "{company}" site:linkedin.com/in'
    google_url = f"https://www.google.com/search?q={quote_plus(query)}"
    log.info("Resolving %r at %r via Bright Data…", role, company)

    html = _bright_data_fetch(google_url)
    profile_url = _parse_first_profile(html or "")

    new_entry = {
        "url": profile_url,
        "role": role,
        "company": company,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    cache[key] = new_entry
    _save_cache(cache)
    log.info("  resolved to %s", profile_url or "(no match)")
    # Modest spacing to be a courteous client (and not burn budget on retries)
    time.sleep(0.8)
    return new_entry if profile_url else None


# ---- Convenience helpers used by morning_brief.py ---------------------
ROLE_FOR_LEAD_KIND = {
    "job":                "Chief People Officer",
    "rns":                "Head of Communications",
    "filing":             "Head of Communications",
    "regulator":          "Head of Communications",
    "procurement":        "Head of Communications",
    "trade_press":        "Head of Communications",
    "leadership_change":  "Head of Communications",
}

ROLE_FOR_PREDICTOR_TRIGGER = {
    "ceo_change":             "Chief Executive Officer",
    "chro_change":            "Chief People Officer",
    "chair_change":           "Chair",
    "cfo_change":             "Chief Financial Officer",
    "ir_director_change":     "Head of Investor Relations",
    "comms_leader_departure": "Chief People Officer",
    "ic_platform_rfp":        "Chief People Officer",
    "ipo_listing":            "Chief Financial Officer",
    "contract_loss":          "Head of Communications",
    "regulator_action":       "Head of Communications",
    "mna":                    "Head of Communications",
    "restructure":            "Chief People Officer",
    "press_velocity_spike":   "Head of Communications",
    "job_ad_cluster":         "Head of HR",
}


def role_for_lead(signal: dict) -> str:
    return ROLE_FOR_LEAD_KIND.get(signal.get("kind", ""), "Head of Communications")


def role_for_predictor(predictor: dict) -> str:
    events = predictor.get("events") or []
    keys = [e.get("trigger_key") for e in events]
    # Pick the highest-priority trigger
    for k in ("comms_leader_departure", "ic_platform_rfp", "ipo_listing",
              "ceo_change", "mna", "regulator_action", "contract_loss",
              "chair_change", "cfo_change", "ir_director_change",
              "chro_change", "restructure", "press_velocity_spike",
              "job_ad_cluster"):
        if k in keys:
            return ROLE_FOR_PREDICTOR_TRIGGER[k]
    return "Head of Communications"


# ---- Hiring-contacts integration -------------------------------------
# Consult the seeded contacts table before falling back to a generic
# Bright Data search. If a fresh, verified contact exists for the role
# the trigger implies, return it directly (no API call). Otherwise the
# existing resolve_profile() path runs.
def resolve_named_contact_for_predictor(predictor: dict) -> dict | None:
    """Return {url, role, name, confidence} if a fresh verified contact is
    available, else None. Picks the role_slot by walking the trigger's
    priority chain (routing.role_priority_for_trigger)."""
    try:
        from tool.contacts.routing import pick_contact_for_trigger, display_title_for_slot
        from tool.contacts.store import load_contacts, get_contact
    except Exception:
        return None
    events = predictor.get("events") or []
    if not events:
        return None
    company = events[0].get("company") or ""
    if not company:
        return None
    keys = [e.get("trigger_key") for e in events if e.get("trigger_key")]
    if not keys:
        return None
    primary = _highest_priority_trigger(keys)
    contacts = load_contacts()
    card = get_contact(contacts, company)
    entry, slot_used = pick_contact_for_trigger(card, primary)
    if entry is None or not entry.linkedin_url:
        return None
    return {
        "url": entry.linkedin_url,
        "role": display_title_for_slot(slot_used),
        "name": entry.name,
        "confidence": entry.confidence,
        "tenure_start": entry.tenure_start,
        "verified_at": entry.verified_at,
    }


def resolve_named_contact_for_lead(signal: dict) -> dict | None:
    """Lead-side equivalent of resolve_named_contact_for_predictor. Maps
    signal kind -> role_slot via LEAD_KIND_TO_SLOT, then walks the
    contacts table for `signal['company']`. Returns the fresh entry's
    LinkedIn URL or None."""
    try:
        from tool.contacts.routing import display_title_for_slot
        from tool.contacts.store import load_contacts, get_contact
    except Exception:
        return None
    company = (signal.get("company") or "").strip()
    if not company:
        return None
    kind = signal.get("kind", "")
    slot = LEAD_KIND_TO_SLOT.get(kind, "cco")
    contacts = load_contacts()
    card = get_contact(contacts, company)
    if card is None:
        return None
    entry = card.get(slot)
    # Lead-side: walk a short fallback if the primary slot is missing
    if entry is None or not entry.is_fresh():
        for fallback in ("cco", "head_of_comms", "chro", "ceo"):
            cand = card.get(fallback)
            if cand and cand.is_fresh():
                entry = cand
                slot = fallback
                break
        else:
            entry = None
    if entry is None or not entry.linkedin_url:
        return None
    return {
        "url": entry.linkedin_url,
        "role": display_title_for_slot(slot),
        "name": entry.name,
        "confidence": entry.confidence,
        "tenure_start": entry.tenure_start,
        "verified_at": entry.verified_at,
    }


# Lead-kind -> role_slot. Mirrors ROLE_FOR_LEAD_KIND but keyed to the
# canonical role slot vocabulary in tool.contacts.schema.
LEAD_KIND_TO_SLOT = {
    "job":                "chro",
    "rns":                "cco",
    "filing":             "cco",
    "regulator":          "cco",
    "procurement":        "cco",
    "trade_press":        "cco",
    "leadership_change":  "chro",
}


def _highest_priority_trigger(keys: list[str]) -> str:
    for k in ("comms_leader_departure", "ic_platform_rfp", "ipo_listing",
              "ceo_change", "mna", "regulator_action", "contract_loss",
              "chair_change", "cfo_change", "ir_director_change",
              "chro_change", "restructure", "press_velocity_spike",
              "job_ad_cluster"):
        if k in keys:
            return k
    return keys[0]
