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
# Cache file renamed from ch_watchlist.json -> ch_watchlist_v2.json in
# May 2026 to bust stale cached resolutions. The previous version cached
# the WRONG CH entity for some watchlist names (e.g. "HSBC" -> some
# subsidiary filing exemption accounts, not HSBC HOLDINGS PLC) because
# the picker was naive. Renaming forces a fresh resolution on the next
# run using the holdings/group/PLC priority logic in _pick_canonical_hit.
WATCHLIST_FILE = STATE_DIR / "ch_watchlist_v2.json"
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


def search_company(name: str) -> list[dict] | None:
    """Search Companies House for a company by name. Returns top 5 candidates,
    OR None on network/timeout (distinct from 'API responded with 0 hits',
    so the caller knows not to cache the failure)."""
    if not COMPANIES_HOUSE_KEY:
        log.warning("search_company(%r): COMPANIES_HOUSE_KEY env var is empty — "
                    "secret not propagated to this workflow?", name)
        return []
    url = f"{SOURCES['companies_house_api']}/search/companies"
    r = get(url, params={"q": name, "items_per_page": 5},
            auth=(COMPANIES_HOUSE_KEY, ""))
    if not r:
        log.warning("search_company(%r): no HTTP response (network/timeout)", name)
        return None   # sentinel: don't cache; retry next run
    if r.status_code != 200:
        log.warning("search_company(%r): HTTP %s body=%s",
                    name, r.status_code, (r.text or "")[:200])
        return None   # sentinel for non-200 — could be rate-limit; retry next run
    items = r.json().get("items", []) or []
    log.info("search_company(%r): %d hits", name, len(items))
    return items


def _pick_canonical_hit(hits: list[dict], query_name: str) -> dict | None:
    """Among CH search hits for `query_name`, pick the parent / canonical
    entity. UK company structure ranks parent holding companies in this
    order:
      1. "<NAME> HOLDINGS PLC"  (e.g. HSBC HOLDINGS PLC)
      2. "<NAME> GROUP PLC"     (e.g. BT GROUP PLC)
      3. shortest "<NAME> ... PLC"  (e.g. UNILEVER PLC, SEVERN TRENT PLC)
    Subsidiaries (e.g. HSBC BANK PLC, UNILEVER UK LIMITED, HSBC PRIVATE
    BANK (UK) LIMITED) fall through. Returns None if hits is empty.

    This is the SINGLE point of truth for "which CH entity does a query
    name resolve to". Both company_events and resolve_company_number
    delegate to it - keeps the pitch pack's annual report extraction
    and the contacts auto-update logic aligned on the same parent."""
    if not hits:
        return None
    name_lower = query_name.strip().lower()
    active = [it for it in hits if it.get("company_status") == "active"]
    prefix_active = [
        it for it in active
        if (it.get("title") or "").lower().startswith(name_lower)
    ]

    def _title(it: dict) -> str:
        return (it.get("title") or "").upper().strip()

    holdings_active = sorted(
        [it for it in prefix_active if " HOLDINGS PLC" in _title(it)],
        key=lambda it: len(_title(it)),
    )
    group_active = sorted(
        [it for it in prefix_active
         if " GROUP PLC" in _title(it) and " HOLDINGS PLC" not in _title(it)],
        key=lambda it: len(_title(it)),
    )
    other_plc_active = sorted(
        [it for it in prefix_active
         if _title(it).endswith(" PLC")
         and " HOLDINGS PLC" not in _title(it)
         and " GROUP PLC" not in _title(it)],
        key=lambda it: len(_title(it)),
    )

    if holdings_active:
        return holdings_active[0]
    if group_active:
        return group_active[0]
    if other_plc_active:
        return other_plc_active[0]
    if prefix_active:
        return prefix_active[0]
    if active:
        return active[0]
    return hits[0]


def company_events(name: str) -> dict:
    """Snapshot + officer list + filing history for one company.
    Used by pitch_pack (Section 1 account snapshot + Section 2 annual
    report quote source). Returns {company, found, resolved, officers,
    filings} — keep this shape stable, downstream renders depend on it."""
    hits = search_company(name)
    # Retry with " PLC" suffix if the bare name returned nothing. CH's
    # relevance algorithm sometimes ranks subsidiaries above the parent
    # plc for short names ("Unilever" -> UNILEVER UK LIMITED before
    # UNILEVER PLC); the explicit suffix forces the parent to the top.
    if not hits and not re.search(r"\b(plc|limited|ltd|group|holdings|llp)\b",
                                   name, re.IGNORECASE):
        hits = search_company(f"{name} PLC")
        if not hits:
            hits = search_company(f"{name} GROUP PLC") or hits
    if not hits:
        return {"company": name, "found": False}
    top = _pick_canonical_hit(hits, name)
    if top is None:
        return {"company": name, "found": False}
    num = top.get("company_number", "")
    officers = company_officers(num) if num else []
    filings: list[dict] = []
    if num and COMPANIES_HOUSE_KEY:
        url = f"{SOURCES['companies_house_api']}/company/{num}/filing-history"
        r = get(url, params={"items_per_page": 20},
                auth=(COMPANIES_HOUSE_KEY, ""))
        if r and r.status_code == 200:
            filings = r.json().get("items", []) or []
    return {
        "company": name,
        "found": True,
        "resolved": top,
        "officers": officers,
        "filings": filings,
    }


def resolve_company_number(name: str) -> str | None:
    """One-time-per-name. Caches result in WATCHLIST_FILE.
    Only caches 'permanent' results — actual API responses (incl. zero hits).
    Network failures / rate-limit timeouts are NOT cached, so they retry
    on subsequent runs until they succeed."""
    cache = _load_watchlist()
    entry = cache.get(name)
    if entry is not None:
        return entry.get("number")
    items = search_company(name)
    if items is None:
        # Network failure / rate limit. Do NOT cache — retry next run.
        return None
    # API actually responded — cache the result (incl. zero-hits result).
    # Use the SAME canonical picker as company_events so the cache hits
    # the parent / holding company, not a subsidiary that happens to
    # rank higher in CH's relevance algorithm. Without this, "HSBC" was
    # caching the number of HSBC PRIVATE BANK (UK) LIMITED and the
    # annual report extraction tried to parse 37KB exemption filings.
    number = None
    if items:
        picked = _pick_canonical_hit(items, name)
        if picked is not None:
            number = picked.get("company_number")
    cache[name] = {
        "number": number,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_watchlist(cache)
    # Slow down: CH rate-limit is 600/5min = 2/sec. 0.5s sleep keeps us
    # comfortably under that with bursty fetches across hundreds of new
    # watchlist names.
    time.sleep(0.5)
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

        # PASS A: historical resigned-officer backfill.
        # Process officers whose resigned_on falls in the last 90 days.
        # The CH /officers endpoint returns BOTH active and resigned, so
        # this adds zero API cost — we just look at the data differently.
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        for o in officers:
            resigned_str = o.get("resigned_on")
            if not resigned_str:
                continue
            try:
                resigned_dt = datetime.fromisoformat(resigned_str).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if resigned_dt < cutoff:
                continue
            occ = o.get("occupation") or ""
            role = o.get("officer_role") or ""
            trigger_key = classify_title(occ, role)
            if not trigger_key:
                continue
            officer_name = o.get("name") or "Unknown officer"
            trigger = P.BY_KEY.get(trigger_key)
            if trigger is None:
                continue
            title_display = occ or role or trigger.label
            events.append(TriggerEvent(
                trigger_key=trigger_key,
                trigger_label=trigger.label,
                company=name,
                evidence=(f"Companies House (historical): {officer_name} "
                          f"resigned as {title_display} at {name} on {resigned_str}."),
                url=(f"https://find-and-update.company-information.service.gov.uk"
                     f"/company/{number}/officers"),
                source_label="Companies House (historical termination)",
                published=resigned_dt,
                raw_signal_id=signal_id("ch_hist", f"{number}|{officer_name}|{resigned_str}"),
                tier_hint="covered",
            ))

        # PASS B: daily-diff (existing logic).
        today_ids: dict[str, dict] = {}
        for o in officers:
            # Skip already-resigned officers — PASS A handled those above
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
