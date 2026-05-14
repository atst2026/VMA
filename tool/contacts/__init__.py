"""Hiring-contacts table — directs Sara to a named, current, role-appropriate
decision-maker per watchlist entity. Two confidence tiers: verified within 120
days OR fallback to existing Recruiter search behaviour. No false confidence."""
from tool.contacts.schema import (
    ROLE_SLOTS, ContactEntry, ContactCard, ResolutionRecord,
    ReverifyEntry, ResolutionStatus,
)
from tool.contacts.store import (
    load_contacts, save_contacts, append_resolution_log,
    load_reverify_queue, save_reverify_queue,
    get_contact, upsert_contact,
)
from tool.contacts.routing import role_priority_for_trigger, pick_contact_for_trigger

__all__ = [
    "ROLE_SLOTS", "ContactEntry", "ContactCard", "ResolutionRecord",
    "ReverifyEntry", "ResolutionStatus",
    "load_contacts", "save_contacts", "append_resolution_log",
    "load_reverify_queue", "save_reverify_queue",
    "get_contact", "upsert_contact",
    "role_priority_for_trigger", "pick_contact_for_trigger",
]
