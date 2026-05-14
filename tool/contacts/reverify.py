"""Re-verification queue management.

Key invariant: `attempts` increments ONLY when a real resolution call
fires and returns an unverified outcome (no_match / stale / unmatchable).
Queue-and-skip events — rate-limited, budget-capped, or in cool-off —
do NOT count. Otherwise an entity could burn its 3-attempt cap during
a single throttled day and lock itself into a 30-day cool-off without
ever actually being queried.

Run order at the top of every nightly job:
    1. process_queue() walks the queue, calling resolve() per entry.
    2. On RESOLVED_VERIFIED -> entry is removed from queue, contact saved.
    3. On counted-failure -> attempts += 1; if >= 3, set cool_off_until.
    4. On skipped -> entry untouched.

The downstream nightly run (morning brief, predictor pipeline) then sees
the up-to-date contacts table when surfacing per-trigger contacts.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable

from tool.contacts.schema import (
    ReverifyEntry, ResolutionStatus, ContactEntry,
    COOL_OFF_DAYS, MAX_ATTEMPTS_BEFORE_COOL_OFF,
)
from tool.contacts.store import (
    load_contacts, save_contacts, load_reverify_queue, save_reverify_queue,
    append_resolution_log, upsert_contact, queue_for_reverify, remove_from_queue,
)
from tool.contacts.resolver import resolve

log = logging.getLogger("brief.contacts.reverify")


def _is_in_cool_off(entry: ReverifyEntry, as_of: datetime) -> bool:
    if not entry.cool_off_until:
        return False
    try:
        until = datetime.fromisoformat(entry.cool_off_until)
    except Exception:
        return False
    return as_of < until


def scan_stale_contacts(contacts: dict, queue: list[ReverifyEntry]) -> list[ReverifyEntry]:
    """Walk the contacts table; for every stale entry, ensure there's a
    queue item. Returns the updated queue (no I/O)."""
    now = datetime.now(timezone.utc)
    for company, card in contacts.items():
        for slot, entry in card.entries.items():
            if not entry.is_fresh(as_of=now):
                queue = queue_for_reverify(queue, company, slot)
    return queue


def process_queue(*, fetch: Callable[[str], str | None] | None,
                  max_calls: int | None = None,
                  ) -> dict[str, int]:
    """Walk the re-verify queue. Returns stats dict.

    `fetch` is the Bright Data callable; when None, leadership-page +
    linkedin sources are skipped (so CH-only verification still works).
    `max_calls` caps actual resolution calls per run (budget guard);
    entries beyond the cap are left in-queue with attempts unchanged.
    """
    contacts = load_contacts()
    queue = load_reverify_queue()
    now = datetime.now(timezone.utc)
    stats = {
        "queue_size_start": len(queue),
        "verified": 0, "attempts_logged": 0,
        "skipped_cool_off": 0, "skipped_budget": 0,
        "removed": 0,
    }
    calls_made = 0

    surviving: list[ReverifyEntry] = []
    for entry in queue:
        if _is_in_cool_off(entry, now):
            stats["skipped_cool_off"] += 1
            surviving.append(entry)
            continue
        if max_calls is not None and calls_made >= max_calls:
            stats["skipped_budget"] += 1
            surviving.append(entry)
            continue

        result_entry, record = resolve(
            entry.company, entry.role_slot, fetch=fetch,
        )
        calls_made += 1
        append_resolution_log(record)

        if result_entry is not None and record.outcome == ResolutionStatus.RESOLVED_VERIFIED:
            upsert_contact(contacts, entry.company, entry.role_slot, result_entry)
            stats["verified"] += 1
            stats["removed"] += 1
            continue   # do not re-add to surviving queue

        if record.outcome in ResolutionStatus.COUNTS_AS_ATTEMPT:
            entry.attempts += 1
            entry.last_attempt_at = now.isoformat()
            entry.last_failure_reason = record.outcome
            stats["attempts_logged"] += 1
            if entry.attempts >= MAX_ATTEMPTS_BEFORE_COOL_OFF:
                entry.cool_off_until = (now + timedelta(days=COOL_OFF_DAYS)).isoformat()
        surviving.append(entry)

    save_reverify_queue(surviving)
    save_contacts(contacts)
    stats["queue_size_end"] = len(surviving)
    stats["calls_made"] = calls_made
    return stats
