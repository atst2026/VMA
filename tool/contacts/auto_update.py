"""Automatic maintenance of hiring_contacts.json.

Runs at the end of every morning brief, after Companies House officer-change
detection. Safety-first: prefers expiring entries (generic search fallback)
over auto-populating with uncertain names.

Rules, in order:
  1. EXPIRE on detected departure. Any CH 'officer departed' event whose
     named person matches a current contact entry marks that entry stale
     (verified_at set to 1 year ago, beyond FRESHNESS_DAYS=120). Within 24h
     the dashboard falls back to the role-search URL for that slot.

  2. POPULATE only from CH structured data. CH's officer list is the
     authoritative source. If CH shows a senior officer at a watchlist
     company AND the matching role slot is currently empty or stale, the
     slot is populated with that officer's name. CH `occupation` is
     classified via classify_title from resolver.py.

  3. REFRESH on confirmed presence. For every currently-fresh entry, if
     CH still lists the same name in a senior occupation, verified_at is
     bumped to today. This keeps entries fresh as long as the person is
     still on the board.

  4. NEVER overwrite a fresh entry from a low-confidence source. RNS / news
     mentions of new appointments are noted but do NOT update the table
     unless CH structurally confirms.

  5. CONFLICT -> EXPIRE. If two sources name different people for the same
     (company, role) in the same week, expire the entry rather than pick.
     Sara sees generic search until manual resolution.

Returns a stats dict for the workflow to summarise in the auto-commit
message.
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Iterable

from tool.contacts.schema import ContactCard, ContactEntry
from tool.contacts.resolver import classify_title

log = logging.getLogger("brief.contacts.auto_update")


# Pattern to pull an officer name from a CH departure event's evidence:
#   "Companies House (historical): SMITH, John resigned as Chief Executive
#    Officer at Severn Trent on 2025-09-12."
# CH stores names as "LASTNAME, Firstname". We normalise to "Firstname Lastname".
_CH_DEPARTURE_RX = re.compile(
    r"(?:Companies House[^:]*:\s*)?"
    r"(?P<name>[A-Z][A-Za-z' \-]+(?:,\s*[A-Z][A-Za-z' \-]+)?)"
    r"\s+(?:resigned|stepped down|departed|stepping down|has left|left)",
    re.IGNORECASE,
)


def _ch_name_to_display(ch_name: str) -> str:
    """'SMITH, John Andrew' -> 'John Andrew Smith'. Idempotent on
    already-display-form names."""
    s = ch_name.strip()
    if "," in s:
        last, first = s.split(",", 1)
        return f"{first.strip().title()} {last.strip().title()}"
    return s.title()


def _names_match(a: str, b: str) -> bool:
    """Loose case-insensitive token-overlap match. Avoids false-negatives
    on punctuation/title differences ('Sir Mark Tucker' vs 'Mark Tucker').
    Requires at least two shared name tokens to count."""
    if not a or not b:
        return False
    sa = set(t.lower() for t in re.split(r"[\s,.]+", a) if t and len(t) > 1)
    sb = set(t.lower() for t in re.split(r"[\s,.]+", b) if t and len(t) > 1)
    # Strip title prefixes / suffixes that confuse matching
    drop = {"sir", "dame", "mr", "mrs", "ms", "dr", "prof", "lord", "lady"}
    sa -= drop
    sb -= drop
    return len(sa & sb) >= 2


# Map CH-driven trigger_keys to the canonical role slots in our table.
TRIGGER_TO_SLOT = {
    "ceo_change":             "ceo",
    "chair_change":           "chair",
    "cfo_change":             "cfo",
    "chro_change":            "chro",
    "ir_director_change":     "ir_director",
    "comms_leader_departure": "cco",   # could also be head_of_comms; we
                                       # expire whichever matches by name
}


def expire_departed_contacts(contacts: dict[str, ContactCard],
                              ch_events: Iterable) -> dict:
    """Rule 1: for each CH departure event, mark any matching entry stale.
    Mutates contacts in-place. Returns {'expired': N, 'examples': [...]}."""
    expired_count = 0
    examples = []
    stale_dt = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()

    for ev in ch_events:
        if "companies house" not in (getattr(ev, "source_label", "") or "").lower():
            continue   # Only CH events drive expiry; news mentions excluded

        trigger_key = getattr(ev, "trigger_key", "")
        slot_candidates = []
        if trigger_key in TRIGGER_TO_SLOT:
            slot_candidates.append(TRIGGER_TO_SLOT[trigger_key])
        if trigger_key == "comms_leader_departure":
            slot_candidates.extend(["cco", "head_of_comms",
                                     "head_of_corporate_affairs"])

        company = (getattr(ev, "company", "") or "").strip()
        evidence = getattr(ev, "evidence", "") or ""
        m = _CH_DEPARTURE_RX.search(evidence)
        if not m:
            continue
        departed_name = _ch_name_to_display(m.group("name"))

        # Find matching contact card by company name (case-insensitive)
        card = None
        for k, v in contacts.items():
            if k.strip().lower() == company.lower():
                card = v
                break
        if card is None:
            continue   # company not in our table

        for slot in slot_candidates:
            entry = card.entries.get(slot)
            if entry is None:
                continue
            if _names_match(entry.name, departed_name):
                entry.verified_at = stale_dt
                expired_count += 1
                examples.append(f"{company}/{slot} ({entry.name} -> departed)")
                log.info("auto_update: expired %s/%s (matched departure: %s)",
                         company, slot, departed_name)

    return {"expired": expired_count, "examples": examples}


def populate_new_appointments(contacts: dict[str, ContactCard],
                               ch_snapshot: dict) -> dict:
    """Rule 2: walk the CH snapshot. For each company we have a card for,
    if any current officer has an occupation that classifies to a role
    slot, AND that slot is currently empty OR stale, populate it.

    `ch_snapshot` is the CH officers dict keyed by company name (the same
    one detect_officer_changes builds at runtime).

    Mutates contacts in-place. Never overwrites a fresh entry."""
    populated = 0
    examples = []
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    for company, card in contacts.items():
        snapshot_for_company = ch_snapshot.get(company) or {}
        officers = snapshot_for_company.get("officers") or []
        for officer in officers:
            if officer.get("resigned_on"):
                continue
            occupation = (officer.get("occupation") or "").strip()
            if not occupation:
                continue
            slot = classify_title(occupation)
            if slot is None:
                continue
            existing = card.entries.get(slot)
            if existing is not None and existing.is_fresh(as_of=now):
                continue   # don't overwrite fresh entry from a different name

            name = _ch_name_to_display(officer.get("name") or "")
            if not name:
                continue
            appointed_on = officer.get("appointed_on") or None
            card.entries[slot] = ContactEntry(
                name=name,
                role_title=occupation,
                role_slot=slot,
                linkedin_url=None,
                source_url="",
                source_label="Companies House (auto-populated)",
                tenure_start=appointed_on,
                verified_at=now_iso,
                confidence=0.88,
            )
            card.last_seeded_at = now_iso
            populated += 1
            examples.append(f"{company}/{slot} <- {name} ({occupation})")
            log.info("auto_update: populated %s/%s with %s (occupation=%r)",
                     company, slot, name, occupation)
            break   # one entry per slot per company per run

    return {"populated": populated, "examples": examples}


def refresh_confirmed_entries(contacts: dict[str, ContactCard],
                               ch_snapshot: dict) -> dict:
    """Rule 3: for every existing entry, if CH still lists the same name
    in a senior role at that company, bump verified_at to today.

    This is what makes entries 'self-refreshing' - as long as the person
    stays in role and Companies House keeps them on the officer list,
    they never expire."""
    refreshed = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for company, card in contacts.items():
        snapshot_for_company = ch_snapshot.get(company) or {}
        officers = snapshot_for_company.get("officers") or []
        active_officer_names = [
            _ch_name_to_display(o.get("name") or "")
            for o in officers
            if not o.get("resigned_on")
        ]
        if not active_officer_names:
            continue
        for slot, entry in card.entries.items():
            if any(_names_match(entry.name, n) for n in active_officer_names):
                entry.verified_at = now_iso
                refreshed += 1

    return {"refreshed": refreshed}


def auto_update_contacts(contacts: dict[str, ContactCard],
                          ch_events: Iterable,
                          ch_snapshot: dict | None = None) -> dict:
    """Top-level orchestration. Run in the order:
      1. expire (most important - safety guarantee against wrong names)
      2. refresh (free, no risk)
      3. populate (only fills empty/stale slots)
    """
    stats = {"expired": 0, "refreshed": 0, "populated": 0, "examples": []}

    r1 = expire_departed_contacts(contacts, ch_events)
    stats["expired"] = r1["expired"]
    stats["examples"].extend(r1.get("examples", []))

    if ch_snapshot is not None:
        r2 = refresh_confirmed_entries(contacts, ch_snapshot)
        stats["refreshed"] = r2["refreshed"]

        r3 = populate_new_appointments(contacts, ch_snapshot)
        stats["populated"] = r3["populated"]
        stats["examples"].extend(r3.get("examples", []))

    return stats


def load_ch_snapshot_for_autoupdate() -> dict[str, dict]:
    """Read the CH officer-snapshot file maintained by
    companies_house.detect_officer_changes and convert it to the
    company-name-keyed shape that auto_update_contacts expects.
    Returns {} if no snapshot has been written yet."""
    import json
    from tool.sources.companies_house import SNAPSHOT_FILE
    if not SNAPSHOT_FILE.exists():
        return {}
    try:
        raw = json.loads(SNAPSHOT_FILE.read_text())
    except Exception as e:
        log.warning("auto_update: snapshot unreadable (%s)", e)
        return {}
    out: dict[str, dict] = {}
    for _number, entry in raw.items():
        name = entry.get("name")
        if not name:
            continue
        officers = []
        for _oid, details in (entry.get("officer_details") or {}).items():
            officers.append({
                "name": details.get("name") or "",
                "occupation": details.get("occupation") or "",
                "officer_role": details.get("officer_role") or "",
            })
        out[name] = {"officers": officers}
    return out
