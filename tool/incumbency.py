"""Incumbency check — is anyone already sitting in the seat a predictor
claims the trigger will create?

The IMI lesson: a capital raise printed "Corporate Affairs Director" on
the card while Erica Lockhart sat at IMI as Group Corporate
Communications Director. Two failures compounded: nobody ever looked for
an incumbent, and the one LinkedIn search the pipeline does runs the
predicted title as an EXACT quoted phrase, so the real holder — same
function, different words — is invisible.

This module fixes both. The predicted seat is mapped to a TITLE FAMILY
(corporate affairs ≈ corporate communications ≈ external affairs …) and
one Google query ORs the whole family against the company, so the search
finds the function, not the phrase. The result is reported honestly:

  found       a public profile matches the family at this company. That
              person may be current or recent — the card reframes ("the
              build likely happens UNDER them; they may be the buyer")
              rather than claiming the seat is filled.
  none_found  no public profile matched. Weak evidence of an open or
              absent seat (LinkedIn is not a register) — stated as such.
  unchecked   Bright Data isn't configured; no claim is made either way.

Cached on disk (45-day TTL — shorter than the contact resolver's 90
because seat occupancy is exactly what triggers change). One Web
Unlocker request per (company, family) per TTL window.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

log = logging.getLogger("brief.incumbency")

STATE_DIR = Path(__file__).resolve().parent / "state"
CACHE_FILE = STATE_DIR / "incumbency_cache.json"
CACHE_TTL_DAYS = 45

# ---- Title families ---------------------------------------------------
# Ordered (family_key, detector regex on the predicted seat, title
# variants searched as one OR query). First match wins, so the most
# specific functions sit above the generic comms/marketing catch-alls.
_FAMILIES: list[tuple[str, re.Pattern, list[str]]] = [
    ("investor_relations", re.compile(r"investor relations|\bIR\b", re.I), [
        "Head of Investor Relations", "Investor Relations Director",
        "Director of Investor Relations", "Group Investor Relations Director",
    ]),
    ("internal_comms", re.compile(r"internal communications", re.I), [
        "Head of Internal Communications", "Internal Communications Director",
        "Director of Internal Communications",
        "Head of Employee Communications",
    ]),
    ("sustainability", re.compile(r"sustainability|esg", re.I), [
        "Head of Sustainability Communications", "Head of Sustainability",
        "Sustainability Communications Director", "Head of ESG Communications",
    ]),
    ("digital_comms", re.compile(r"digital communications", re.I), [
        "Head of Digital Communications", "Digital Communications Director",
        "Head of Digital and Social Media",
    ]),
    ("corporate_affairs", re.compile(r"corporate affairs", re.I), [
        "Corporate Affairs Director", "Group Corporate Affairs Director",
        "Director of Corporate Affairs", "Head of Corporate Affairs",
        "Corporate Communications Director",
        "Group Corporate Communications Director",
        "Director of Corporate Communications", "External Affairs Director",
    ]),
    ("marketing_brand", re.compile(
        r"marketing|\bbrand\b|\bCMO\b|demand generation|martech", re.I), [
        "Chief Marketing Officer", "Marketing Director", "Head of Marketing",
        "Group Marketing Director", "Brand Director", "Head of Brand",
        "VP Marketing",
    ]),
    ("communications", re.compile(r"communications|comms|crisis", re.I), [
        "Head of Communications", "Communications Director",
        "Director of Communications", "Group Communications Director",
        "Head of Corporate Communications", "Chief Communications Officer",
        "Head of External Communications",
    ]),
]
_DEFAULT_FAMILY = "communications"


def family_for_seat(predicted_role: str | None) -> tuple[str, list[str]]:
    """(family_key, title variants) for a predicted seat string."""
    seat = (predicted_role or "").strip()
    for key, rx, titles in _FAMILIES:
        if rx.search(seat):
            return key, titles
    for key, _rx, titles in _FAMILIES:
        if key == _DEFAULT_FAMILY:
            return key, titles
    return _DEFAULT_FAMILY, []


# ---- SERP parsing ------------------------------------------------------
# Google result titles for profiles read "Erica Lockhart - Group
# Corporate Communications Director - IMI plc | LinkedIn". Best-effort:
# a missed parse degrades to "profile found" with the URL, never a wrong
# confident name.
_RESULT_RX = re.compile(
    r"([A-Z][\w'’.\-]+(?:\s+[A-Z][\w'’.\-]+){1,3})\s*[-–—|]\s*"
    r"([^<>|]{4,80}?)\s*[-–—|]", re.UNICODE)
_PROFILE_RX = re.compile(
    r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[a-zA-Z0-9\-_%~.]+/?',
    re.IGNORECASE)


def _parse_hit(html: str, titles: list[str]) -> dict | None:
    """First /in/ profile URL plus, where parsable, the person's name and
    the family title they carry."""
    if not html:
        return None
    url = None
    for u in _PROFILE_RX.findall(html):
        u = u.rstrip(').,;"\'>')
        low = u.lower()
        if any(skip in low for skip in ("google.com", "web.archive.org",
                                        "pulse-article", "/jobs/", "/posts/")):
            continue
        url = u
        break
    if not url:
        return None
    name = title = None
    low_titles = [t.lower() for t in titles]
    for m in _RESULT_RX.finditer(html):
        cand_title = m.group(2).strip()
        if any(t in cand_title.lower() or cand_title.lower() in t
               for t in low_titles):
            name, title = m.group(1).strip(), cand_title
            break
    return {"url": url, "name": name, "title": title}


# ---- Cache -------------------------------------------------------------
def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=0))


def _cache_valid(entry: dict) -> bool:
    try:
        ts = datetime.fromisoformat(entry.get("at", ""))
        return (datetime.now(timezone.utc) - ts) < timedelta(days=CACHE_TTL_DAYS)
    except Exception:
        return False


# ---- Public API --------------------------------------------------------
def check_incumbent(company: str | None, predicted_role: str | None) -> dict:
    """Search the seat's title family at this company. Returns
    {status, family, name, title, url, note} where status is
    "found" | "none_found" | "unchecked". Never raises."""
    try:
        from tool.linkedin_resolver import (BRIGHT_DATA_KEY, BD_ZONE,
                                            _bright_data_fetch)
        company = (company or "").strip()
        family, titles = family_for_seat(predicted_role)
        if not company or not titles:
            return _result("unchecked", family, None, predicted_role, company)
        key = f"{family}|{company.lower()}"
        cache = _load_cache()
        hit = cache.get(key)
        if hit and _cache_valid(hit):
            return _result(hit.get("status", "unchecked"), family, hit,
                           predicted_role, company)
        if not (BRIGHT_DATA_KEY and BD_ZONE):
            return _result("unchecked", family, None, predicted_role, company)

        ors = " OR ".join(f'"{t}"' for t in titles)
        query = f'({ors}) "{company}" site:linkedin.com/in'
        google_url = f"https://www.google.com/search?q={quote_plus(query)}"
        log.info("Incumbency check: %s family at %r…", family, company)
        html = _bright_data_fetch(google_url)
        parsed = _parse_hit(html or "", titles) if html else None
        status = ("found" if parsed
                  else "none_found" if html is not None
                  else "unchecked")
        entry = {"status": status,
                 "at": datetime.now(timezone.utc).isoformat(),
                 **(parsed or {})}
        cache[key] = entry
        _save_cache(cache)
        time.sleep(0.8)
        return _result(status, family, entry, predicted_role, company)
    except Exception as e:
        log.info("incumbency check skipped (%s)", e)
        return _result("unchecked", _DEFAULT_FAMILY, None,
                       predicted_role, company)


def _result(status: str, family: str, hit: dict | None,
            predicted_role: str | None, company: str | None) -> dict:
    hit = hit or {}
    name, title, url = hit.get("name"), hit.get("title"), hit.get("url")
    if status == "found":
        who = name or "a profile"
        held = f" ({title})" if title else ""
        note = (f"Incumbent check: {who}{held} publicly matches the "
                f"{(predicted_role or 'predicted').strip()} seat family at "
                f"{company} — verify tenure; if current, the build likely "
                f"happens UNDER them and they may be the buyer, not the "
                f"vacancy.")
    elif status == "none_found":
        note = (f"Incumbent check: no public profile found in the seat's "
                f"title family at {company} — consistent with an open or "
                f"absent seat, but weak evidence; confirm on the team page.")
    else:
        note = ""
    return {"status": status, "family": family, "name": name,
            "title": title, "url": url, "note": note}


# Short function label per family, used to rewrite the predicted seat
# when an incumbent is found: the opportunity is no longer the director
# chair but the build UNDERNEATH it.
_FUNCTION_LABEL = {
    "investor_relations": "IR",
    "internal_comms": "internal comms",
    "sustainability": "sustainability comms",
    "digital_comms": "digital comms",
    "corporate_affairs": "corporate affairs",
    "marketing_brand": "marketing",
    "communications": "comms",
}


def build_seat(predicted_role: str | None, incumbent_name: str | None) -> str:
    """The seat actually for sale when the predicted seat's family already
    has a public incumbent: senior hires under them. Replaces the
    predicted seat on the card so it never contradicts its own
    incumbency verdict."""
    family, _titles = family_for_seat(predicted_role)
    func = _FUNCTION_LABEL.get(family, "comms")
    who = (incumbent_name or "").strip() or "the incumbent"
    return f"Senior {func} hires under {who}"


def annotate_entry(entry: dict) -> dict:
    """Project the incumbency verdict onto a pipeline entry as
    incumbent_* fields the dashboard reads. Mutates and returns `entry`;
    never raises."""
    try:
        res = check_incumbent(entry.get("company"),
                              entry.get("predicted_role"))
        entry["incumbent_status"] = res["status"]
        entry["incumbent_name"] = res["name"]
        entry["incumbent_title"] = res["title"]
        entry["incumbent_url"] = res["url"]
        entry["incumbent_note"] = res["note"]
    except Exception as e:
        log.info("incumbency annotate skipped (%s)", e)
    return entry
