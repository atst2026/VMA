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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tool.config import COMPANIES_HOUSE_KEY, SOURCES
from tool.profiles import active_profile
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
    # --- May 2026 expansion -------------------------------------------
    # Widens BOTH the CH officer-change scan (housing associations /
    # private cos file at CH as Registered Providers / CLG / Ltd) AND
    # the account-relevance gate used by the predictor + following +
    # the new job/news feeds (so a "University of Manchester appoints
    # Director of Comms" Guardian-Jobs/Google-News item now resolves).
    # Non-CH bodies (universities, NHS trusts — Royal Charter / statutory)
    # just cache None in the CH resolver and are skipped gracefully;
    # their value here is the account gate. Multiword names only — keeps
    # the distinctive-name matcher safe from common-word collisions.
    # Housing associations (RNS-dark, core Sara private market)
    "Sanctuary Housing", "Places for People", "Sovereign Network Group",
    "The Riverside Group", "Anchor Hanover", "Home Group",
    "Southern Housing Group", "Platform Housing Group", "Aster Group",
    "Stonewater", "Orbit Group", "Great Places Housing Group",
    # Large universities (account-gate value via jobs.ac.uk / news)
    "University of Oxford", "University of Cambridge",
    "University of Manchester", "University College London",
    "King's College London", "University of Edinburgh",
    "University of Leeds", "University of Birmingham",
    "Imperial College London", "University of Bristol",
    "University of Glasgow", "Durham University",
    # Large NHS trusts / bodies (account-gate value via news / jobs)
    "Barts Health NHS Trust",
    "Guy's and St Thomas' NHS Foundation Trust",
    "Manchester University NHS Foundation Trust",
    "Leeds Teaching Hospitals NHS Trust",
    "University College London Hospitals NHS Foundation Trust",
    "NHS Scotland", "NHS Wales",
    # Private / PE-backed / family-owned (CH-resolvable, RNS-dark)
    "INEOS", "JCB", "Dyson", "Bestway Group", "Arnold Clark Automobiles",
    "Boparan Holdings", "Swire Group", "Laing O'Rourke",
    "BAM Construct UK", "Dentsu UK", "Reed Global", "Pentland Group",
    "Bibby Line Group", "Matalan Retail", "The Very Group",
    "EG Group", "Stagecoach Group", "Liberty Global UK",
    # Large charities / national bodies
    "National Trust", "Wellcome Trust", "Nuffield Health",
    "Barnardo's", "The Salvation Army UK", "Citizens Advice",
    "Leonard Cheshire",
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

# FIRST DRAFT — senior marketing/brand officer titles (review with the
# marketing team).
_MARKETING_TITLE_RX = re.compile(
    r"\b(?:marketing|brand|growth|e-?commerce|digital marketing)\b.{0,40}"
    r"\b(?:director|head|officer|lead|vp|chief)\b"
    r"|\b(?:director|head|officer|chief|vp)\b.{0,40}"
    r"\b(?:marketing|brand|growth|e-?commerce)\b"
    r"|\bchief marketing officer\b|\bchief brand officer\b",
    re.IGNORECASE,
)

# The active profile picks which leader-departure classifier is used. The
# emitted trigger key stays the legacy internal name ("comms_leader_departure")
# for routing compatibility — re-tuning routing to marketing contact roles is
# a later step.
LEADER_TITLE_RX = (
    _MARKETING_TITLE_RX if active_profile().key == "marketing" else COMMS_TITLE_RX
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
    if LEADER_TITLE_RX.search(s):
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


def _resolve_to_canonical(name: str) -> dict | None:
    """Top-level resolver: turn a query name into the parent / canonical
    Companies House hit (or None if nothing found). Does up to 3 API
    searches if needed:

      1. Bare name (always).
      2. If first search returned zero hits AND name lacks a corporate
         suffix, retry with ' PLC' then ' GROUP PLC'.
      3. After picking from those hits, if the picked entity is NOT a
         HOLDINGS PLC or GROUP PLC variant, do an explicit search for
         '<name> HOLDINGS PLC' to surface the parent. CH's relevance
         algorithm ranks subsidiaries above the parent for queries
         like 'HSBC' (HSBC HOLDINGS PLC files annually so subsidiaries
         with more recent filings push it past items_per_page=5).
         Same again with ' GROUP PLC'. Combined results re-picked.

    The cache (resolve_company_number's WATCHLIST_FILE) sits on top of
    this so each name resolves at most once per workflow run.
    """
    # Check what variants the name already contains
    has_holdings = bool(re.search(r"\bholdings\b", name, re.IGNORECASE))
    has_group = bool(re.search(r"\bgroup\b", name, re.IGNORECASE))
    has_suffix = bool(re.search(r"\b(plc|limited|ltd|llp)\b",
                                  name, re.IGNORECASE))

    hits = search_company(name) or []

    # Retry with PLC suffix if bare name returned nothing
    if not hits and not has_suffix:
        hits = search_company(f"{name} PLC") or []
    if not hits and not has_suffix and not has_group:
        hits = search_company(f"{name} GROUP PLC") or []
    if not hits:
        return None

    top = _pick_canonical_hit(hits, name)
    if top is None:
        return None

    # If we already picked a HOLDINGS or GROUP PLC, we're done
    title_upper = (top.get("title") or "").upper()
    if " HOLDINGS PLC" in title_upper or " GROUP PLC" in title_upper:
        return top

    # Otherwise, surface the parent by explicit secondary searches
    extra = []
    if not has_holdings:
        extra.extend(search_company(f"{name} HOLDINGS PLC") or [])
    if not has_group:
        extra.extend(search_company(f"{name} GROUP PLC") or [])

    if extra:
        seen_numbers = {h.get("company_number") for h in hits}
        merged = list(hits) + [
            h for h in extra
            if h.get("company_number") not in seen_numbers
        ]
        new_top = _pick_canonical_hit(merged, name)
        if new_top is not None:
            new_title = (new_top.get("title") or "").upper()
            # Only switch if the new pick is genuinely a HOLDINGS/GROUP
            # variant - never downgrade.
            if " HOLDINGS PLC" in new_title or " GROUP PLC" in new_title:
                return new_top

    return top


def company_events(name: str) -> dict:
    """Snapshot + officer list + filing history for one company.
    Used by pitch_pack (Section 1 account snapshot + Section 2 annual
    report quote source). Returns {company, found, resolved, officers,
    filings} — keep this shape stable, downstream renders depend on it."""
    top = _resolve_to_canonical(name)
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
    on subsequent runs until they succeed.

    Uses _resolve_to_canonical so the cached number is for the parent /
    holding company, not a subsidiary that happens to rank higher in
    CH's relevance algorithm. Without this, 'HSBC' was caching the
    number of an HSBC subsidiary that files exemption accounts and the
    annual report extraction tried (and failed) to parse those.
    """
    cache = _load_watchlist()
    entry = cache.get(name)
    if entry is not None:
        return entry.get("number")
    top = _resolve_to_canonical(name)
    # If the resolver returned a hit, cache it (incl. None for zero-hits).
    # Network failures inside _resolve_to_canonical bubble through as
    # None too, but we don't distinguish here - the caller sees None
    # and the next run retries.
    number = top.get("company_number") if top else None
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


_SCAN_CURSOR_FILE = STATE_DIR / "ch_scan_cursor.json"


def _load_scan_cursor() -> int:
    try:
        return int(json.loads(_SCAN_CURSOR_FILE.read_text()).get("cursor", 0))
    except Exception:
        return 0


def _save_scan_cursor(cursor: int) -> None:
    try:
        _SCAN_CURSOR_FILE.write_text(json.dumps({"cursor": cursor}))
    except Exception as e:
        log.info("CH: could not persist scan cursor: %s", e)


# ---- Main entry point ---------------------------------------------------
def detect_officer_changes(max_companies: int | None = None,
                           time_budget_s: float | None = None) -> list[TriggerEvent]:
    """For each watchlist company, fetch today's officer list and compare
    against yesterday's snapshot. Emit a TriggerEvent for each departed
    officer whose title matches a known trigger key.

    The watchlist is ~550 companies and resolving an uncached name costs
    up to 3 Companies House searches. Doing all of them every run blew
    past the job's time budget, so the job never completed, so the
    resolver cache (ch_watchlist_v2.json, persisted only via the Actions
    cache on SUCCESS) was never saved — a vicious cycle that made every
    run slow forever.

    Fix: this scan is now bounded by BOTH a per-run company cap and a
    wall-clock `time_budget_s`, and it ROTATES through the watchlist via
    a persisted cursor so successive runs cover different slices. Each
    resolved number is cached as it goes, so within a few runs the whole
    watchlist is cached and full coverage resumes automatically (cached
    lookups cost zero API calls).

    Snapshot is MERGED, not replaced — capping/rotation must not wipe
    day-over-day history for companies not visited this run.
    """
    if not COMPANIES_HOUSE_KEY:
        log.info("CH: no COMPANIES_HOUSE_KEY, skipping officer-change scan")
        return []

    snapshot = _load_snapshot()
    # Start from the existing snapshot and update in place so unvisited
    # companies keep their prior officer set for future diffing.
    new_snapshot: dict[str, dict] = dict(snapshot)
    events: list[TriggerEvent] = []
    first_snapshot = not snapshot

    all_names = _all_watchlist_names()
    total = len(all_names)
    if total == 0:
        return []

    # Rotate: begin at the persisted cursor so each run covers a fresh
    # slice rather than always re-doing the first N companies.
    cursor = _load_scan_cursor() % total
    rotated = all_names[cursor:] + all_names[:cursor]

    cap = max_companies if max_companies is not None else total
    deadline = (time.monotonic() + time_budget_s) if time_budget_s is not None else None

    log.info("CH: officer-change scan — %d/%d companies from cursor %d "
             "(budget=%ss, first_snapshot=%s)",
             min(cap, total), total, cursor,
             time_budget_s or "none", first_snapshot)

    processed = 0
    for name in rotated:
        if processed >= cap:
            break
        if deadline is not None and time.monotonic() >= deadline:
            log.info("CH: time budget reached after %d companies — "
                     "remaining will be covered next run", processed)
            break
        processed += 1
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
                f"{officer_name} departed as {title_display} at {name} "
                f"(Companies House filing)."
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

    # new_snapshot started as a copy of the prior snapshot and was
    # updated in place for visited companies, so this is a merge — no
    # history is lost for companies skipped by the cap / time budget.
    _save_snapshot(new_snapshot)
    # Advance the rotation cursor so the next run picks up where this
    # one stopped (wrapping around the watchlist).
    _save_scan_cursor((cursor + processed) % total)
    log.info("CH: emitted %d officer-change trigger events "
             "(processed %d companies, next cursor=%d)",
             len(events), processed, (cursor + processed) % total)
    return events


# ========================================================================
# BD-strengthening: exploit Companies House beyond /search + /officers.
# The free API also exposes charges (financing events), persons with
# significant control (ownership changes), filing history (change-of-name
# = rebrand, SH01 = share allotment), and officers' appointed_on dates
# (leadership tenure / flight-risk). All Tier-1 verified by construction.
#
# These are emitted DATE-WINDOW based (an event is emitted when its own
# CH-supplied date falls inside the look-back window) rather than via a
# day-over-day snapshot diff: idempotent, no snapshot file to corrupt, and
# downstream dedup (stable signal_id) + the rolling pipeline (keyed by
# company, first_seen preserved) absorb the daily re-emission exactly as
# the historical resigned-officer backfill above already does.
# ========================================================================

# Look-back for filing/charge/PSC events. Matches the predictor's 90-day
# stacking window and the historical-officer backfill cutoff above.
_CH_EVENT_WINDOW_DAYS = 90
# Long-tenure threshold for the flight-risk signal. CMO/CCO tenure is among
# the shortest in the C-suite (~4–5 years); a comms/marketing board officer
# past this is a soft succession-watch signal.
# Spencer Stuart 2025: mean CMO tenure is 4.1 years (shortest in the
# C-suite), so the APPROACH to four years is the succession-watch window
# — fire from 3.5y, with 3.5-4.5y flagged as the peak-churn band.
_TENURE_FLIGHT_RISK_DAYS = int(365 * 3.5)
_TENURE_PEAK_MAX_DAYS = int(365 * 4.5)


def _ch_get_json(path: str, params: dict | None = None) -> dict | None:
    """GET a Companies House API path, return parsed JSON or None. Central
    so charges/PSC/filing-history share one auth + error path."""
    if not COMPANIES_HOUSE_KEY:
        return None
    url = f"{SOURCES['companies_house_api']}{path}"
    r = get(url, params=params or {"items_per_page": 100},
            auth=(COMPANIES_HOUSE_KEY, ""))
    if not r or r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _parse_ch_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _within_window(d: datetime | None, cutoff: datetime) -> bool:
    return d is not None and d >= cutoff


def _ch_officers_url(number: str) -> str:
    return (f"https://find-and-update.company-information.service.gov.uk"
            f"/company/{number}/officers")


def _charge_events(name: str, number: str, cutoff: datetime) -> list[TriggerEvent]:
    """A newly registered charge (debenture / mortgage) = a secured-financing
    event. Only outstanding charges created inside the window count."""
    data = _ch_get_json(f"/company/{number}/charges")
    if not data:
        return []
    out: list[TriggerEvent] = []
    for ch in (data.get("items") or []):
        if not isinstance(ch, dict):
            continue
        # 'satisfied' charges are settled debt — not a live financing signal.
        if (ch.get("status") or "").lower() == "satisfied":
            continue
        created = _parse_ch_date(ch.get("created_on") or ch.get("delivered_on"))
        if not _within_window(created, cutoff):
            continue
        persons = ", ".join(
            (p.get("name") or "") for p in (ch.get("persons_entitled") or [])
            if isinstance(p, dict)
        )[:120]
        cid = ch.get("charge_id") or ch.get("id") or (ch.get("links") or {}).get("self", "")
        ev = (f"{name} registered a charge at Companies House "
              f"({ch.get('classification', {}).get('description', 'secured financing')}"
              f"{(' to ' + persons) if persons else ''}) on "
              f"{created.date().isoformat()}.")
        out.append(TriggerEvent(
            trigger_key="secured_financing",
            trigger_label="Secured financing / charge registered",
            company=name,
            evidence=ev,
            url=f"https://find-and-update.company-information.service.gov.uk/company/{number}/charges",
            source_label="Companies House (charges)",
            published=created,
            raw_signal_id=signal_id("ch_charge", f"{number}|{cid}"),
            tier_hint="listed",
        ))
    return out


def _psc_events(name: str, number: str, cutoff: datetime) -> list[TriggerEvent]:
    """A PSC notified (or ceased) inside the window = an ownership change at a
    long-established watchlist company. A founding PSC's old notified_on is
    outside the window, so this only fires on genuine recent control changes."""
    data = _ch_get_json(f"/company/{number}/persons-with-significant-control")
    if not data:
        return []
    out: list[TriggerEvent] = []
    for psc in (data.get("items") or []):
        if not isinstance(psc, dict):
            continue
        notified = _parse_ch_date(psc.get("notified_on"))
        ceased = _parse_ch_date(psc.get("ceased_on"))
        recent = notified if _within_window(notified, cutoff) else (
            ceased if _within_window(ceased, cutoff) else None)
        if recent is None:
            continue
        who = psc.get("name") or "a new controlling party"
        verb = "ceased as" if (ceased and recent == ceased) else "filed as"
        ev = (f"{who} {verb} a person with significant control of {name} "
              f"on {recent.date().isoformat()} (Companies House filing).")
        pid = (psc.get("links") or {}).get("self", "") or who
        out.append(TriggerEvent(
            trigger_key="ownership_change",
            trigger_label="Ownership change (new significant control)",
            company=name,
            evidence=ev,
            url=f"https://find-and-update.company-information.service.gov.uk/company/{number}/persons-with-significant-control",
            source_label="Companies House (PSC)",
            published=recent,
            raw_signal_id=signal_id("ch_psc", f"{number}|{pid}|{recent.date()}"),
            tier_hint="listed",
        ))
    return out


def _filing_events(name: str, number: str, cutoff: datetime) -> list[TriggerEvent]:
    """Change-of-name filings (= rebrand) and share allotments / SH01
    (= secured financing / capital raise) from the filing history."""
    data = _ch_get_json(f"/company/{number}/filing-history",
                        params={"items_per_page": 50})
    if not data:
        return []
    out: list[TriggerEvent] = []
    for f in (data.get("items") or []):
        if not isinstance(f, dict):
            continue
        fdate = _parse_ch_date(f.get("date"))
        if not _within_window(fdate, cutoff):
            continue
        category = (f.get("category") or "").lower()
        ftype = (f.get("type") or "").upper()
        desc = (f.get("description") or "").replace("-", " ")
        tid = f.get("transaction_id") or (f.get("links") or {}).get("self", "")
        if category == "change-of-name" or ftype in ("CERTNM", "NM01", "NM04"):
            out.append(TriggerEvent(
                trigger_key="rebrand",
                trigger_label="Rebrand / change of name",
                company=name,
                evidence=(f"{name} filed a change of name on {fdate.date().isoformat()} "
                          f"— a corporate rebrand (Companies House filing)."),
                url=f"https://find-and-update.company-information.service.gov.uk/company/{number}/filing-history",
                source_label="Companies House (change of name)",
                published=fdate,
                raw_signal_id=signal_id("ch_name", f"{number}|{tid}"),
                tier_hint="listed",
            ))
        elif ftype == "SH01" or "allotment of shares" in desc:
            out.append(TriggerEvent(
                trigger_key="secured_financing",
                trigger_label="Share allotment (capital raise)",
                company=name,
                evidence=(f"{name} filed an allotment of shares (SH01) on "
                          f"{fdate.date().isoformat()} — fresh equity capital "
                          f"(Companies House filing)."),
                url=f"https://find-and-update.company-information.service.gov.uk/company/{number}/filing-history",
                source_label="Companies House (share allotment)",
                published=fdate,
                raw_signal_id=signal_id("ch_sh01", f"{number}|{tid}"),
                tier_hint="listed",
            ))
    return out


def _tenure_events(name: str, number: str, officers: list[dict]) -> list[TriggerEvent]:
    """A board-level comms / marketing officer past the long-tenure
    threshold is a soft flight-risk / succession-watch signal, keyed on
    their CURRENT employer. Reuses officers already fetched (zero extra
    API cost). Active (non-resigned) officers only."""
    out: list[TriggerEvent] = []
    now = datetime.now(timezone.utc)
    for o in officers:
        if o.get("resigned_on"):
            continue
        occ = o.get("occupation") or ""
        # Tenure flight-risk is meaningful only for the comms/marketing
        # leadership seat (the one VMA backfills), not generic directors.
        if not LEADER_TITLE_RX.search(occ):
            continue
        appointed = _parse_ch_date(o.get("appointed_on"))
        if appointed is None:
            continue
        tenure_days = (now - appointed).total_seconds() / 86400.0
        if tenure_days < _TENURE_FLIGHT_RISK_DAYS:
            continue
        officer_name = o.get("name") or "A senior officer"
        years = round(tenure_days / 365.0, 1)
        in_peak = tenure_days <= _TENURE_PEAK_MAX_DAYS
        out.append(TriggerEvent(
            trigger_key="leadership_tenure",
            trigger_label="Leadership tenure (flight-risk / succession watch)",
            company=name,
            evidence=(f"{officer_name} has held {occ} at {name} "
                      f"for ~{years} years (appointed {appointed.date().isoformat()}) "
                      + ("— inside the 3.5-4.5 year peak-churn window "
                         "for senior marketing/comms seats."
                         if in_peak else
                         "— a long tenure in a short-tenure seat.")),
            url=_ch_officers_url(number),
            source_label="Companies House (tenure / appointed_on)",
            # Soft, slow-decaying restlessness signal; date it to now so it
            # stays live in the 90-day window like the velocity spike.
            published=now,
            raw_signal_id=signal_id("ch_tenure", f"{number}|{officer_name}|{o.get('appointed_on')}"),
            tier_hint="covered",
        ))
    return out


def detect_filing_events(max_companies: int | None = None,
                         time_budget_s: float | None = None) -> list[TriggerEvent]:
    """Charges + PSC + change-of-name/SH01 filings + leadership tenure for
    the watchlist. Shares the resolver cache and the rotating cursor model
    with detect_officer_changes (its OWN cursor so the two scans cover
    different slices), so the free 600-req/5-min budget is respected and
    full coverage rolls round within a few runs.

    Cost per visited company: charges(1) + PSC(1) + filing-history(1) +
    officers(1, reused for tenure) ≈ 4 calls. Bounded by both the company
    cap and the wall-clock budget. Fully non-fatal: any company that errors
    is skipped."""
    if not COMPANIES_HOUSE_KEY:
        log.info("CH: no COMPANIES_HOUSE_KEY, skipping filing-event scan")
        return []

    all_names = _all_watchlist_names()
    total = len(all_names)
    if total == 0:
        return []

    cursor = _load_filing_cursor() % total
    rotated = all_names[cursor:] + all_names[:cursor]
    cap = max_companies if max_companies is not None else total
    deadline = (time.monotonic() + time_budget_s) if time_budget_s is not None else None
    cutoff = datetime.now(timezone.utc) - timedelta(days=_CH_EVENT_WINDOW_DAYS)

    log.info("CH: filing-event scan — up to %d/%d companies from cursor %d (budget=%ss)",
             min(cap, total), total, cursor, time_budget_s or "none")

    events: list[TriggerEvent] = []
    processed = 0
    for name in rotated:
        if processed >= cap:
            break
        if deadline is not None and time.monotonic() >= deadline:
            log.info("CH: filing-event time budget reached after %d companies", processed)
            break
        processed += 1
        try:
            number = resolve_company_number(name)
            if not number:
                continue
            events.extend(_charge_events(name, number, cutoff))
            events.extend(_psc_events(name, number, cutoff))
            events.extend(_filing_events(name, number, cutoff))
            officers = company_officers(number)
            if officers:
                events.extend(_tenure_events(name, number, officers))
            time.sleep(0.15)
        except Exception as e:
            log.info("CH filing-event scan: %s failed (%s) — skipped", name, e)
            continue

    _save_filing_cursor((cursor + processed) % total)
    log.info("CH: emitted %d filing/charge/PSC/tenure events (processed %d, next cursor=%d)",
             len(events), processed, (cursor + processed) % total)
    return events


_FILING_CURSOR_FILE = STATE_DIR / "ch_filing_cursor.json"


def _load_filing_cursor() -> int:
    try:
        return int(json.loads(_FILING_CURSOR_FILE.read_text()).get("cursor", 0))
    except Exception:
        return 0


def _save_filing_cursor(cursor: int) -> None:
    try:
        _FILING_CURSOR_FILE.write_text(json.dumps({"cursor": cursor}))
    except Exception as e:
        log.info("CH: could not persist filing cursor: %s", e)


def stream_filings(duration_s: float = 25.0,
                   max_events: int = 200) -> list[TriggerEvent]:
    """Companies House Streaming API reader (near-real-time filing firehose).

    The Streaming API pushes EVERY company's filings as newline-delimited
    JSON over a long-lived HTTPS connection. A daily cron can't hold an
    always-on socket, so this is a BOUNDED reader: it connects, consumes the
    stream for `duration_s` seconds (or `max_events` records), filters to the
    watchlist, maps filing categories to trigger keys, and returns. It is
    OFF by default in the cron and only runs when CH_STREAM_ENABLED is set —
    an always-on worker is the right home for it. Fully non-fatal.

    Auth uses the same free API key as Basic auth (the streaming key). The
    record parser (_stream_record_to_event) is unit-tested without a live
    socket."""
    import os
    if (os.environ.get("CH_STREAM_ENABLED") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return []
    if not COMPANIES_HOUSE_KEY:
        return []
    watch = {_norm_co(n) for n in _all_watchlist_names()}
    out: list[TriggerEvent] = []
    try:
        import requests
        deadline = time.monotonic() + duration_s
        with requests.get(SOURCES["companies_house_stream"],
                          auth=(COMPANIES_HOUSE_KEY, ""),
                          stream=True, timeout=(10, duration_s + 5)) as r:
            if r.status_code != 200:
                log.info("CH stream: HTTP %s", r.status_code)
                return []
            for line in r.iter_lines(decode_unicode=True):
                if time.monotonic() >= deadline or len(out) >= max_events:
                    break
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ev = _stream_record_to_event(rec, watch)
                if ev:
                    out.append(ev)
    except Exception as e:
        log.info("CH stream reader stopped: %s", e)
    log.info("CH stream: %d watchlist filing events", len(out))
    return out


def _norm_co(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


# Streaming filing-category → trigger-key map (the categories worth a lead).
_STREAM_CATEGORY_TRIGGER = {
    "mortgage": "secured_financing",          # a charge registered
    "persons-with-significant-control": "ownership_change",
    "change-of-name": "rebrand",
    "capital": "secured_financing",           # SH01 allotments live here
}


def _stream_record_to_event(rec: dict, watch: set[str]) -> TriggerEvent | None:
    """Map one Streaming-API filing record to a TriggerEvent if its company
    is on the watchlist and its category is one we lead on. Pure function —
    unit-tested without a socket."""
    if not isinstance(rec, dict):
        return None
    data = rec.get("data") or {}
    company_name = (data.get("company_name")
                    or (rec.get("resource_kind") and "")
                    or "")
    # The filings stream keys company by number in resource_uri; the name is
    # not always present, so accept either a matched name OR a watchlist
    # number lookup is out of scope here — require a name match for precision.
    if not company_name or _norm_co(company_name) not in watch:
        return None
    category = (data.get("category") or "").lower()
    key = _STREAM_CATEGORY_TRIGGER.get(category)
    if not key:
        return None
    trig = P.BY_KEY.get(key)
    if trig is None:
        return None
    when = _parse_ch_date(data.get("date")) or datetime.now(timezone.utc)
    return TriggerEvent(
        trigger_key=key,
        trigger_label=trig.label,
        company=company_name,
        evidence=(f"Companies House stream: {company_name} filed "
                  f"{data.get('description') or category} on {when.date().isoformat()}."),
        url="https://find-and-update.company-information.service.gov.uk",
        source_label="Companies House (streaming)",
        published=when,
        raw_signal_id=signal_id("ch_stream", str(rec.get("resource_id") or rec.get("event", {}))),
        tier_hint="listed",
    )


# ---- Back-compat -------------------------------------------------------
def to_signals(days: int = 3) -> list[dict]:
    """Kept for backwards compatibility with morning_brief's signal-stream
    pipeline. CH integration now emits TriggerEvents directly via
    detect_officer_changes()."""
    return []
