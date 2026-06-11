"""Is the predicted seat senior/niche — the level in-house TA teams
routinely fail at and outsource?

The AD-room correction: TA teams exist to remove agency fees from
MID-LEVEL VOLUME hiring; head-of/director/niche searches still go
external, so an internal-TA observation must not zero the budget axis
for the seats VMA actually sells. One shared predicate so the gate,
the posture layer and the card all agree on what "senior" means.
"""
from __future__ import annotations

import re

_SENIOR_RX = re.compile(
    r"\b(director|head of|chief|c[a-z]o\b|vp|vice[- ]president|partner|"
    r"officer|leadership|senior|group)\b|investor relations|"
    r"corporate affairs|crisis", re.I)


def role_is_senior(role: str | None) -> bool:
    return bool(_SENIOR_RX.search(role or ""))
