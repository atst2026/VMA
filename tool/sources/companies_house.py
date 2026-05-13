"""Companies House signals — daily officer-change diff for the watchlist.

Polls /company/{n}/officers daily for each watchlist company, compares
against yesterday's snapshot, and emits a TriggerEvent when an officer
with a relevant title (comms, CEO, CFO, Chair, CHRO) has departed.

This closes the UK-private-company blind spot: leadership/comms changes
that don't get RNS coverage (because the company isn't listed) but DO
get filed at Companies House because they involve statutory directors.

Important calibration:
  Most senior in-house comms hires are EMPLOYEES, not statutory
  directors, so they don't appear in CH filings. The comms-titled
  branch only fires for cases where a senior comms person sits on
  the board (~5–10% of senior comms, common at PE-backed mid-caps
  and family-owned firms). The bigger uplift is private-company
  CEO/CFO/Chair/CHRO change detection, which then triggers the
  downstream comms-hire cascade (Track A in the predictor).

Watchlist:
  - peers.SECTOR_PEERS (~140 names, mostly FTSE-350-ish)
  - UK_PRIVATE_MIDCAPS (~60 hand-curated UK private mid-caps)
Total ~200 companies, polled once per morning.

Rate-limit:
  CH free-tier limit: 600 requests / 5 minutes. We send ~200/run
  (one /officers call per company), well under budget. First run
  also sends ~200 /search/companies calls to resolve numbers
  (cached forever), so first-deploy adds ~1.5min before steady-state.
"""
from __future__ import annotations
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from tool.config import COMPANIES_HOUSE_KEY, SOURCES
from tool.predictive import patterns as P
from tool.predictive.detector import TriggerEvent
from tool.sources._http import get, signal_id

log = logging.getLogger("brief.ch")

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
WATCHLIST_FILE = STATE_DIR / "ch_watchlist.json"
SNAPSHOT_FILE = STATE_DIR / "ch_officers_snapshot.json"


# ---- UK private mid-caps (not in peers.py SECTOR_PEERS) -----------------
# Hand-curated. The goal is private-company leadership change coverage —
# names where Sara's comms placements would land and where RNS does not
# cover the moves.
UK_PRIVATE_MIDCAPS = [
    "John Lewis Partnership", "Specsavers Optical Group",
    "Iceland Foods", "Asda Group", "Morrisons Supermarkets",
    "Co-operative Group", "BUPA",
    "Virgin Atlantic", "Virgin Money UK",
    "New Look Retailers", "River Island Clothing", "Selfridges Retail",
    "Harrods", "Fortnum & Mason",
    "Wagamama", "Nando's Chickenland",
    "Compass Group UK", "Bidvest Foodservice UK", "Brakes Brothers",
    "Travis Perkins", "Wickes Group", "Howden Joinery Group",
    "Domino's Pizza Group UK", "Greene King", "JD Wetherspoon",
    "Premier Foods", "Pets at Home Group",
    "British Heart Foundation", "Marie Curie", "RSPCA",
    "Mott MacDonald", "Arup Group", "WSP UK", "Mace Group",
    "ISS UK", "G4S UK", "Securitas UK",
    "Premier Inn", "InterContinental Hotels Group",
    "Tesco Bank", "M&S Bank",
    "Octopus Energy Group", "Good Energy",
    "L&Q Group", "Peabody Trust", "Notting Hill Genesis",
    "Clarion Housing Group",
    "Pret a Manger", "Costa Coffee",
    "Capita Pension Solutions",
]


# ---- Title classifier ----------------------------------------------------
# CH `occupation` is free-text; people fill it however they want. We match
# on the well-known senior titles and ignore generic "director" entries.
COMMS_TITLE_RX = re.compile(
    r"\b(?:communications|comms|corporate affairs|public affairs|"
    r"media relations|public relations)\b.{0,40}\b(?:director|head|officer|lead)\b"
    r"|\b(?:director|head|officer)\b.{0,40}\b(?:communications|comms|"
    r"corporate affairs|public affairs|media relations|public relations)\b"
    r"|\bchief communications officer\b"
    r"|\b(?:pr director|head of pr|director of pr)\b",
    re.IGNORECASE,
)
CEO_RX = re.compile(
    r"\b(?:chief executive(?: officer)?|ceo|group ceo|managing director)\b",
    re.IGNORECASE,
)
CFO_RX = re.compile(
    r"\b(?:chief financial officer|cfo|group cfo|finance director)\b",
    re.IGNORECASE,
)
CHAIR_RX = re.compile(r"\b(?:chair(?:man|person|woman)?)\b", re.IGNORECASE)
CHRO_RX = re.compile(
    r"\b(?:chief people officer|cpo|chief human resources(?: officer)?|"
    r"chro|hr director|people director)\b",
    re.IGNORECASE,
)


def classify_title(occupation: str, role_text: str = "") -> str | None:
    """Return a trigger_key match, or None. Order matters: comms first
    (since 'Chief Communications Officer' includes 'Officer' which CEO_RX
    is wary of), then CFO/CHRO/CEO/Chair.

    Only the `occupation` field is matched against — it's the free-text
    job title filed by the officer. `role_text` (CH's officer_role) is
    almost always just 'director' or 'secretary' and would cause false
    positives if mixed in (e.g. 'Public Relations Manager' + ' director'
    accidentally satisfies a comms+director pattern)."""
    s = (occupation or "").strip()
    if not s:
        return None
    if COMMS_TITLE_RX.search(s):
        return "comms_leader_departure"
    if CFO_RX.search(s):
        return "cfo_change"
    if CHRO_RX.search(s):
        return "chro_change"
    if CEO_RX.search(s):
        return "ceo_change"
    if CHAIR_RX.search(s):
        return "chair_change"
    return None


# ---- Watchlist management ------------------------------------------------
def _all_watchlist_names() -> list[str]:
    """Flattened, deduplicated list of names from peers.SECTOR_PEERS plus
    UK_PRIVATE_MIDCAPS."""
    from tool.peers import SECTOR_PEERS
    seen, out = set(), []
    for names in SECTOR_PEERS.values():
        for n in names:
            key = n.lower().strip()
            if key not in seen:
                seen.add(key)
                out.append(n)
    for n in UK_PRIVATE_MIDCAPS:
        key = n.lower().strip()
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _load_watchlist() -> dict:
    if not WATCHLIST_FILE.exists():
        return {}
    try:
        return json.loads(WATCHLIST_FILE.read_text())
    except Exception:
        return {}


def _save_watchlist(d: dict) -> None:
    WATCHLIST_FILE.write_text(json.dumps(d, indent=0))


def search_company(name: str) -> list[dict]:
    """Search Companies House for a company by name. Returns top 5 candidates."""
    if not COMPANIES_HOUSE_KEY:
        return []
    url = f"{SOURCES['companies_house_api']}/search/companies"
    r = get(url, params={"q": name, "items_per_page": 5},
            auth=(COMPANIES_HOUSE_KEY, ""))
    if not r or r.status_code != 200:
        return []
    return r.json().get("items", [])


def resolve_company_number(name: str) -> str | None:
    """One-time-per-name. Caches result in WATCHLIST_FILE (incl. failed
    resolutions, so we don't retry on every run)."""
    cache = _load_watchlist()
    entry = cache.get(name)
    if entry is not None:
        return entry.get("number")
    items = search_company(name)
    number = None
    if items:
        # Prefer active over dissolved
        active = [it for it in items if it.get("company_status") == "active"]
        picked = active[0] if active else items[0]
        number = picked.get("company_number")
    cache[name] = {
        "number": number,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_watchlist(cache)
    time.sleep(0.2)
    return number


# ---- Officer snapshot diff ----------------------------------------------
def company_officers(company_number: str) -> list[dict]:
    """Current officers of a given company number. Returns [] on error."""
    if not COMPANIES_HOUSE_KEY or not company_number:
        return []
    url = f"{SOURCES['companies_house_api']}/company/{company_number}/officers"
    r = get(url, params={"items_per_page": 100},
            auth=(COMPANIES_HOUSE_KEY, ""))
    if not r or r.status_code != 200:
        return []
    return r.json().get("items", [])


def _officer_id(officer: dict) -> str:
    """Stable ID per (officer-link, role, name). CH uses an
    'officer/appointments' link that's stable across calls."""
    links = officer.get("links") or {}
    of_link = (links.get("officer") or {}).get("appointments", "")
    role = (officer.get("officer_role") or "").lower()
    name = (officer.get("name") or "").lower()
    return f"{of_link}|{role}|{name}"


def _load_snapshot() -> dict:
    if not SNAPSHOT_FILE.exists():
        return {}
    try:
        return json.loads(SNAPSHOT_FILE.read_text())
    except Exception:
        return {}


def _save_snapshot(d: dict) -> None:
    SNAPSHOT_FILE.write_text(json.dumps(d, indent=0))


# ---- Main entry point ---------------------------------------------------
def detect_officer_changes(max_companies: int | None = None) -> list[TriggerEvent]:
    """For each watchlist company, fetch today's officer list and compare
    against yesterday's snapshot. Emit a TriggerEvent for each departed
    officer whose title matches a known trigger key.

    On first run the snapshot is empty, so zero events fire — we just
    populate the snapshot. From the second run onwards we get day-over-day
    deltas.
    """
    if not COMPANIES_HOUSE_KEY:
        log.info("CH: no COMPANIES_HOUSE_KEY, skipping officer-change scan")
        return []

    snapshot = _load_snapshot()
    new_snapshot: dict[str, dict] = {}
    events: list[TriggerEvent] = []
    first_snapshot = not snapshot

    names = _all_watchlist_names()
    if max_companies is not None:
        names = names[:max_companies]

    log.info("CH: officer-change scan across %d watchlist companies (first_snapshot=%s)",
             len(names), first_snapshot)

    for name in names:
        number = resolve_company_number(name)
        if not number:
            continue
        officers = company_officers(number)
        if not officers:
            continue
        time.sleep(0.15)

        today_ids: dict[str, dict] = {}
        for o in officers:
            # Skip already-resigned officers (CH returns them historically)
            if o.get("resigned_on"):
                continue
            oid = _officer_id(o)
            today_ids[oid] = o

        new_snapshot[number] = {
            "name": name,
            "officer_ids": list(today_ids.keys()),
            "officer_details": {
                oid: {
                    "name": o.get("name"),
                    "occupation": o.get("occupation"),
                    "officer_role": o.get("officer_role"),
                }
                for oid, o in today_ids.items()
            },
            "at": datetime.now(timezone.utc).isoformat(),
        }

        prior = snapshot.get(number)
        if not prior:
            continue
        prior_ids = set(prior.get("officer_ids") or [])
        today_id_set = set(today_ids.keys())
        departed = prior_ids - today_id_set
        if not departed:
            continue

        prior_details = prior.get("officer_details") or {}
        for oid in departed:
            det = prior_details.get(oid, {})
            occ = det.get("occupation") or ""
            role = det.get("officer_role") or ""
            trigger_key = classify_title(occ, role)
            if not trigger_key:
                continue
            officer_name = det.get("name") or "Unknown officer"
            trigger = P.BY_KEY.get(trigger_key)
            if trigger is None:
                continue
            title_display = occ or role or trigger.label
            evidence = (
                f"Companies House: {officer_name} departed as "
                f"{title_display} at {name}."
            )
            log.info("CH event: %s — %s left %s (%s)",
                     trigger_key, officer_name, name, title_display)
            events.append(TriggerEvent(
                trigger_key=trigger_key,
                trigger_label=trigger.label,
                company=name,
                evidence=evidence,
                url=(f"https://find-and-update.company-information.service.gov.uk"
                     f"/company/{number}/officers"),
                source_label="Companies House (officer termination)",
                published=datetime.now(timezone.utc),
                raw_signal_id=signal_id("ch_officer_term", f"{number}|{oid}"),
                tier_hint="covered",
            ))

    _save_snapshot(new_snapshot)
    log.info("CH: emitted %d officer-change trigger events", len(events))
    return events


# ---- Back-compat -------------------------------------------------------
def to_signals(days: int = 3) -> list[dict]:
    """Kept for backwards compatibility with morning_brief's signal-stream
    pipeline. CH integration now emits TriggerEvents directly via
    detect_officer_changes()."""
    return []
