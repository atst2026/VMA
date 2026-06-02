"""Persistence layer for the contacts table, resolution log, and re-verify
queue.

Files (all under tool/state/):
  hiring_contacts.json          -> single JSON dict, company -> ContactCard
  contact_resolution_log.jsonl  -> append-only newline-delimited JSON
  contact_reverify_queue.json   -> single JSON, list of ReverifyEntry
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from tool.contacts.schema import (
    ContactCard, ContactEntry, ResolutionRecord, ReverifyEntry,
)

log = logging.getLogger("brief.contacts")

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

CONTACTS_FILE = STATE_DIR / "hiring_contacts.json"
RESOLUTION_LOG_FILE = STATE_DIR / "contact_resolution_log.jsonl"
REVERIFY_QUEUE_FILE = STATE_DIR / "contact_reverify_queue.json"


# ---- Contacts table -----------------------------------------------------
def load_contacts() -> dict[str, ContactCard]:
    if not CONTACTS_FILE.exists():
        return {}
    try:
        data = json.loads(CONTACTS_FILE.read_text())
    except Exception as e:
        log.warning("hiring_contacts.json unreadable (%s) — treating as empty", e)
        return {}
    return {
        company: ContactCard.from_jsonable(card_d)
        for company, card_d in data.items()
    }


def save_contacts(contacts: dict[str, ContactCard]) -> None:
    payload = {c: card.to_jsonable() for c, card in contacts.items()}
    CONTACTS_FILE.write_text(json.dumps(payload, indent=2))


def _normalise_company(name: str) -> str:
    return (name or "").strip().lower()


# Legal / region suffixes stripped only for the fallback match below, so a
# lookup for "HSBC UK" still finds the "HSBC" card. Kept deliberately narrow
# (legal forms + region words) to avoid collapsing genuinely different names.
_LEGAL_SUFFIX_RX = re.compile(
    r"\b(plc|p\.l\.c\.|limited|ltd|group|holdings|holding|inc|llp|llc|corp|"
    r"corporation|ag|s\.a\.|sa|n\.v\.|nv|gmbh|b\.v\.|bv|spa|oy|uk|gb)\b\.?",
    re.IGNORECASE,
)


def _core_company(name: str) -> str:
    s = _normalise_company(name)
    s = _LEGAL_SUFFIX_RX.sub("", s)
    s = re.sub(r"[^a-z0-9 &]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def core_company_key(name: str) -> str:
    """Public alias for the core-name normaliser behind get_contact. Lets
    other modules (e.g. auto_update) match companies the SAME lenient way
    the runtime reader does, instead of a divergent exact-string match that
    silently fails to expire a departed person when the card key carries a
    legal suffix the event doesn't ('HSBC Holdings plc' vs 'HSBC')."""
    return _core_company(name)


def get_contact(contacts: dict[str, ContactCard], company: str) -> ContactCard | None:
    """Case-insensitive lookup. Exact (normalised) match wins; if none, fall
    back to a core-name match so e.g. "HSBC UK" resolves to the "HSBC" card."""
    if not company:
        return None
    target = _normalise_company(company)
    for k, v in contacts.items():
        if _normalise_company(k) == target:
            return v
    core = _core_company(company)
    if len(core) >= 3:
        for k, v in contacts.items():
            if _core_company(k) == core:
                return v
    return None


def upsert_contact(contacts: dict[str, ContactCard], company: str,
                   role_slot: str, entry: ContactEntry) -> ContactCard:
    """Insert or update one role-slot for `company`. Returns the card."""
    card = get_contact(contacts, company)
    if card is None:
        card = ContactCard(company=company, last_seeded_at="")
        contacts[company] = card
    card.entries[role_slot] = entry
    return card


# ---- Resolution log (append-only jsonl) ---------------------------------
def append_resolution_log(record: ResolutionRecord) -> None:
    line = json.dumps(record.to_jsonable(), ensure_ascii=False)
    with RESOLUTION_LOG_FILE.open("a") as f:
        f.write(line + "\n")


def iter_resolution_log():
    """Stream the resolution log. Used by analysis scripts."""
    if not RESOLUTION_LOG_FILE.exists():
        return
    with RESOLUTION_LOG_FILE.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ---- Re-verify queue ----------------------------------------------------
def load_reverify_queue() -> list[ReverifyEntry]:
    if not REVERIFY_QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(REVERIFY_QUEUE_FILE.read_text())
    except Exception:
        return []
    out = []
    for item in data:
        try:
            out.append(ReverifyEntry(**item))
        except TypeError:
            continue
    return out


def save_reverify_queue(queue: list[ReverifyEntry]) -> None:
    payload = [
        {
            "company": e.company,
            "role_slot": e.role_slot,
            "queued_at": e.queued_at,
            "attempts": e.attempts,
            "last_attempt_at": e.last_attempt_at,
            "last_failure_reason": e.last_failure_reason,
            "cool_off_until": e.cool_off_until,
        }
        for e in queue
    ]
    REVERIFY_QUEUE_FILE.write_text(json.dumps(payload, indent=2))


def queue_for_reverify(queue: list[ReverifyEntry], company: str,
                       role_slot: str) -> list[ReverifyEntry]:
    """Add (company, role_slot) to the queue if it's not already there.
    Caller is responsible for persisting via save_reverify_queue."""
    company_n = _normalise_company(company)
    for e in queue:
        if _normalise_company(e.company) == company_n and e.role_slot == role_slot:
            return queue
    queue.append(ReverifyEntry(
        company=company,
        role_slot=role_slot,
        queued_at=datetime.now(timezone.utc).isoformat(),
    ))
    return queue


def remove_from_queue(queue: list[ReverifyEntry], company: str,
                      role_slot: str) -> list[ReverifyEntry]:
    company_n = _normalise_company(company)
    return [
        e for e in queue
        if not (_normalise_company(e.company) == company_n
                and e.role_slot == role_slot)
    ]
