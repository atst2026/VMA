"""Divisional contacts for Tier-A conglomerates.

Many UK conglomerates (HSBC, GSK, Diageo, Unilever, etc.) have material
divisional structure where each division has its own comms leader,
distinct from the group-level CCO. A lead's title or JD will typically
name the division ("Global Commercial Organization", "UK Retail Bank",
"Vaccines Business Unit"). When that happens, group-level contact
resolution lands on the wrong person.

This sidecar — populated by hand for the ~10–15 Tier-A conglomerates
that matter — holds a small {parent: {division: {keywords, slot:
entry}}} map. resolve_lead_contact consults it before falling back to
parent-company contacts.

When the parent has known divisions and the lead has divisional
language but no exact keyword match, the caller can surface
"divisional role — verify the divisional comms leader" instead of
confidently routing to the group seat.
"""
from __future__ import annotations

import json
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent / "state"
DIVISIONAL_FILE = STATE_DIR / "divisional_contacts.json"

# Phrases in a lead's title/summary that signal a divisional role even
# when we can't match a specific division. Kept conservative; better to
# flag uncertainty than to incorrectly trigger it.
_DIVISIONAL_HINT_TOKENS = (
    " division", "business unit", " bu ", "operating company",
    "global commercial", "global business services", "category lead",
    "regional", "business segment", "business group",
)


def load_divisions() -> dict:
    """{parent: {division_name: {keywords:[...], slot:{...entry...}}}}."""
    if not DIVISIONAL_FILE.exists():
        return {}
    try:
        d = json.loads(DIVISIONAL_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _norm(s: str) -> str:
    return (s or "").lower()


def match_division(parent: str, lead_text: str,
                    divisions: dict | None = None) -> tuple[str | None, bool]:
    """Return (division_name, parent_has_divisions). division_name is the
    exact match if any keyword in any of `parent`'s divisions hits
    `lead_text`; None otherwise. parent_has_divisions == True even when
    we couldn't pick one, so the caller can surface uncertainty.
    """
    if not parent or not lead_text:
        return None, False
    divisions = load_divisions() if divisions is None else divisions
    # Case-insensitive parent lookup.
    plow = _norm(parent)
    parent_divs = None
    for k, v in divisions.items():
        if _norm(k) == plow:
            parent_divs = v
            break
    if not parent_divs:
        return None, False
    text = _norm(lead_text)
    for div_name, div in parent_divs.items():
        for kw in (div.get("keywords") or []):
            if kw and _norm(kw) in text:
                return div_name, True
    return None, True


def lookup_division_entry(parent: str, division: str, slots: tuple,
                           divisions: dict | None = None) -> dict | None:
    """Pick the first available named entry across `slots` for this
    parent::division. Returns {name, role_title, linkedin_url,
    confidence, verified_at} or None."""
    divisions = load_divisions() if divisions is None else divisions
    plow = _norm(parent)
    parent_divs = None
    for k, v in divisions.items():
        if _norm(k) == plow:
            parent_divs = v
            break
    if not parent_divs or division not in parent_divs:
        return None
    div = parent_divs[division]
    for slot in slots:
        e = div.get(slot)
        if e and e.get("name"):
            return {
                "name": e.get("name", ""),
                "role_title": e.get("role_title", ""),
                "linkedin_url": e.get("linkedin_url"),
                "confidence": float(e.get("confidence", 0.0) or 0.0),
                "verified_at": e.get("verified_at", ""),
            }
    return None


def has_divisional_hint(lead_text: str) -> bool:
    """Cheap test for divisional language in a lead, used to flag
    uncertainty when the parent has divisions but no keyword matched."""
    t = _norm(lead_text)
    return any(tok in t for tok in _DIVISIONAL_HINT_TOKENS)
