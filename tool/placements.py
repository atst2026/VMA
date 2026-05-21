"""Anonymised placements registry — social proof for the Pitch Pack.

Per the Retrained Search evidence, relevant track record in the client's
own framing materially lifts retained-brief conversion. The Pitch Pack
surfaces up to a few *relevant* anonymised placements (matched to the
target's sector / role) as a "Recent relevant placements" block.

HONESTY RULE — real entries only. This ships SEEDED EMPTY. We never
fabricate a placement: a client-facing pitch claiming track record we
don't have is both dishonest and a reputational risk. The Pitch Pack only
renders the block when this registry contains entries that match the
target, so until it's populated the section simply doesn't appear.

To populate: append real, anonymised placements below. Each entry:
    role        the seat placed (e.g. "Head of Internal Communications")
    sector      a peers.SECTOR_PEERS key for matching (e.g. "financial_services")
    descriptor  anonymised employer description ("a FTSE 250 insurer")
    outcome     one-line result ("retained; shortlist in 3 weeks, hired in 6")
"""
from __future__ import annotations

import re

# Real, anonymised placements only. Seeded empty — see HONESTY RULE above.
PLACEMENTS: list[dict] = [
    # Example shape (commented, NOT shown — delete the comment and add real
    # entries to activate the Pitch Pack block):
    # {"role": "Head of Internal Communications",
    #  "sector": "financial_services",
    #  "descriptor": "a FTSE 250 financial-services firm",
    #  "outcome": "retained; shortlist of 6 in 3 weeks, hired in 6"},
]


def _role_tokens(role: str) -> set[str]:
    """Significant role words for loose family matching (drop stopwords)."""
    stop = {"of", "and", "the", "a", "head", "director", "lead", "manager",
            "senior", "group", "global", "chief", "officer"}
    return {w for w in re.findall(r"[a-z]+", (role or "").lower())
            if w not in stop and len(w) >= 3}


def relevant_placements(sector: str | None, role: str,
                        limit: int = 3) -> list[dict]:
    """Return the most relevant anonymised placements for a pitch.

    Scored: +2 for a sector match, +1 for role-family overlap. Only
    entries scoring > 0 are returned (no irrelevant track record), newest
    first by registry order, capped at `limit`. Empty registry -> []."""
    if not PLACEMENTS:
        return []
    want_tokens = _role_tokens(role)
    scored: list[tuple[int, dict]] = []
    for p in PLACEMENTS:
        score = 0
        if sector and p.get("sector") == sector:
            score += 2
        if want_tokens & _role_tokens(p.get("role", "")):
            score += 1
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [p for _, p in scored[:limit]]
