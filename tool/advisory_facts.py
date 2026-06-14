"""Resolve a lead's SPONSOR / ACCESS facts from the existing contacts store.

The advisory gate scores SPONSOR (a named owner) and ACCESS (a route to
them) from a `facts` dict; without it, every advisory lead stays DEVELOP —
honest, but the console should show the leads where a buyer IS already on
file as call-ready. This connects the advisory lane to the platform's
existing contact infrastructure (`tool.contacts.store`), the same roster
the hiring side resolves — no new fetches, no new store.

`facts_resolver()` loads the roster ONCE and returns a `signal -> facts`
function for `originate(facts_for=…)`. A company with a confident named
comms/people contact yields {sponsor_name, sponsor_title, who_to_call},
which (with the deal's mandate + in-window timing) moves a verified lead
from DEVELOP to PURSUE. Never raises; an unresolved company returns {}.
"""
from __future__ import annotations

import logging

log = logging.getLogger("brief.advisory.facts")


def _best_named(card):
    """The most confident named, sufficiently-verified contact on a card."""
    best = None
    for e in (getattr(card, "entries", {}) or {}).values():
        try:
            if e and getattr(e, "name", "") and e.meets_named_confidence():
                if best is None or e.confidence > best.confidence:
                    best = e
        except Exception:
            continue
    return best


def facts_resolver(contacts=None):
    """Return a `signal -> facts` callable backed by the contacts roster
    (loaded once). Degrades to a no-op resolver ({}) if the store is
    unavailable."""
    if contacts is None:
        try:
            from tool.contacts.store import load_contacts
            contacts = load_contacts()
        except Exception as e:
            log.info("advisory facts: contacts unavailable (%s)", e)
            contacts = {}

    def resolve(signal) -> dict:
        try:
            from tool.contacts.store import get_contact
            card = get_contact(contacts, getattr(signal, "company", ""))
            best = _best_named(card) if card else None
            if best:
                return {"sponsor_name": best.name,
                        "sponsor_title": getattr(best, "role_title", ""),
                        "who_to_call": best.name}
        except Exception:
            pass
        return {}

    return resolve
