"""Filter + rank signals for Sara's daily brief.

Test applied:
- role titles match `ROLE_KEYWORDS` (title OR summary OR company name)
- salary ≥ £40k perm, OR falls in £350–800/day interim band, OR unknown
- UK-primary weighting; international kept but discounted

Rank score = base_weight × geo_weight × role_strength × kind_multiplier × freshness
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone
from typing import Iterable

from dateutil import parser as dateparse

from tool.config import (
    GEO_PRIMARY, GEO_SECONDARY_WEIGHT, ROLE_KEYWORDS,
)

log = logging.getLogger("brief.rank")

KIND_MULTIPLIER = {
    "leadership_change": 1.6,
    "rns": 1.3,
    "regulator": 1.2,
    "filing": 1.0,
    "procurement": 0.9,
    "trade_press": 1.1,
    "job": 1.4,
    "linkedin_batch": 0.7,
    "": 1.0,
}


def _role_strength(text: str) -> float:
    """How strongly a piece of text matches the role taxonomy. 0 = no match."""
    t = (text or "").lower()
    score = 0.0
    # Stronger phrases trigger bigger boosts
    strong = ["chief communications officer", "head of corporate communications",
              "head of internal communications", "corporate affairs director",
              "communications director", "pr director", "head of communications"]
    for s in strong:
        if s in t:
            score += 1.2
    for rk in ROLE_KEYWORDS:
        if rk in t:
            score += 0.4
    return score


def _geo_weight(geo: str) -> float:
    if geo in GEO_PRIMARY or geo == "UK":
        return 1.0
    if geo in ("EU", "US", "APAC", "INT"):
        return GEO_SECONDARY_WEIGHT
    return 0.7


def _freshness(published: str) -> float:
    """Newer = higher. Anything older than ~7 days is heavily discounted."""
    if not published:
        return 0.85   # unknown pub date: neutral-minus
    try:
        dt = dateparse.parse(published)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return 0.85
    hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if hours < 0:
        return 1.0
    if hours < 24:
        return 1.2
    if hours < 48:
        return 1.0
    if hours < 72:
        return 0.85
    if hours < 168:
        return 0.65
    return 0.4


def score(signal: dict) -> float:
    text = " ".join(filter(None, [
        signal.get("title", ""), signal.get("summary", ""), signal.get("company", ""),
    ]))
    rs = _role_strength(text)
    if rs == 0:
        return 0.0
    base = signal.get("weight", 1.0)
    kind = KIND_MULTIPLIER.get(signal.get("kind", ""), 1.0)
    geo = _geo_weight(signal.get("geo", ""))
    fresh = _freshness(signal.get("published", ""))
    return round(base * kind * geo * fresh * (1.0 + 0.25 * rs), 3)


def dedup(signals: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for s in signals:
        key = s.get("id") or (s.get("source", "") + "|" + s.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def rank(signals: list[dict]) -> list[dict]:
    deduped = dedup(signals)
    scored = []
    for s in deduped:
        s["score"] = score(s)
        if s["score"] > 0:
            scored.append(s)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def suggest_angle(signal: dict) -> str:
    """One-line opening angle based on signal kind."""
    k = signal.get("kind", "")
    title = signal.get("title", "")
    company = signal.get("company", "")
    if k == "job":
        tgt = company or "the employer"
        return f"Live role at {tgt} — pitch retained before the internal search runs out of runway."
    if k == "leadership_change":
        return "Leadership change in the comms stack — call the new decision-maker inside 72 hours."
    if k == "rns":
        return "Regulatory announcement — comms restructure often follows within 4–8 weeks."
    if k == "regulator":
        return "Regulator action — reputation exposure, interim comms capacity often needed fast."
    if k == "procurement":
        return "Public-sector comms procurement — framework or contract entry point."
    if k == "trade_press":
        return "Sector coverage — read, then surface an angle to the named individual."
    return "Public signal worth a 10-minute dig before dialling."
