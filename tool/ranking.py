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
from functools import lru_cache
from typing import Iterable

from dateutil import parser as dateparse

from tool.config import (
    EXCLUDE_TITLE_TERMS, GEO_PRIMARY, GEO_SECONDARY_WEIGHT, ROLE_KEYWORDS,
)


# Word-boundary regex patterns for every keyword. Using boundaries avoids
# substring false-positives like "cco" matching inside "account".
@lru_cache(maxsize=None)
def _compile_patterns(keywords: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(r"\b" + re.escape(k) + r"\b", re.IGNORECASE) for k in keywords)


_ROLE_PATTERNS = _compile_patterns(tuple(ROLE_KEYWORDS))
_EXCLUDE_PATTERNS = _compile_patterns(tuple(EXCLUDE_TITLE_TERMS))
_STRONG_PATTERNS = _compile_patterns(tuple([
    "chief communications officer", "head of corporate communications",
    "head of internal communications", "corporate affairs director",
    "communications director", "pr director", "head of communications",
]))

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

# Verbs / phrases that mark a trade-press item as actual news rather than
# editorial / thought leadership / trends pieces. If a trade_press signal
# does NOT contain one of these, it's dropped — Sara wants BD signals, not
# reading.
NEWS_VERBS = frozenset([
    "appoint", "appointed", "appoints", "appointment",
    "hire", "hired", "hires", "hiring",
    "join", "joined", "joins", "joining",
    "depart", "departed", "departs", "departure", "departing",
    "leave", "leaves", "leaving",
    "step down", "steps down", "stepping down",
    "quit", "quits",
    "resign", "resigned", "resigns",
    "promote", "promoted", "promotes", "promotion",
    "replace", "replaced", "replaces", "replacement",
    "restructure", "restructured", "restructures", "restructuring",
    "layoff", "layoffs", "laid off",
    "exit", "exits", "exited", "exiting",
    "retire", "retires", "retired",
    "named", "names new", "taps",
    "announce", "announces", "announced",
    "to lead", "moves to", "moves from",
    # explicit role-addition phrases
    "new head of", "new director of", "new chief",
    "new cco", "new cmo", "new cpo", "new chro", "new ceo",
])


def _looks_like_news(title: str) -> bool:
    t = (title or "").lower()
    return any(v in t for v in NEWS_VERBS)


def _role_strength(text: str) -> float:
    """How strongly a piece of text matches the role taxonomy. 0 = no match.
    Word-boundary-aware so 'cco' matches 'CCO' but not 'account'.
    """
    if not text:
        return 0.0
    score = 0.0
    for p in _STRONG_PATTERNS:
        if p.search(text):
            score += 1.2
    for p in _ROLE_PATTERNS:
        if p.search(text):
            score += 0.4
    return score


def _geo_weight(geo: str) -> float:
    if geo in GEO_PRIMARY or geo == "UK":
        return 1.0
    if geo in ("EU", "US", "APAC", "INT"):
        return GEO_SECONDARY_WEIGHT
    return 0.7


def _freshness(published: str) -> float:
    """Newer = higher. Daily mode: heavily discount >7 days. Sweep mode
    (VMA_SWEEP_DAYS>1): flatter curve so older items aren't crushed."""
    from tool.config import sweep_days
    sweep = sweep_days() > 1
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
    if sweep:
        # Sweep: gentle decay across 14 days
        if hours < 48:
            return 1.1
        if hours < 24 * 7:
            return 1.0
        if hours < 24 * 14:
            return 0.9
        return 0.6
    # Daily mode (original)
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
    title = signal.get("title") or ""
    # Hard exclusion: agency/sales client-service roles are out regardless of
    # what else matches. Word-boundary matched to avoid clobbering legit
    # in-house titles that happen to share a word.
    for p in _EXCLUDE_PATTERNS:
        if p.search(title):
            return 0.0

    # Trade press: only let actual news through (appointments, departures,
    # restructures). Editorial / thought leadership / trend pieces all drop.
    if signal.get("kind") == "trade_press" and not _looks_like_news(title):
        return 0.0

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


def _norm_title(t: str) -> str:
    """Collapse whitespace, lowercase, strip 'maternity cover' / 'contract' /
    trailing punctuation so two near-identical listings dedup cleanly."""
    t = (t or "").lower()
    for junk in ("(12 month contract)", "(maternity cover)", "(mat cover)",
                 "- mat cover", "- maternity cover", "- 12 month contract",
                 "12 month mat cover", "12-month mat cover"):
        t = t.replace(junk, "")
    t = " ".join(t.split())
    return t.strip(" -,·")


def dedup(signals: list[dict]) -> list[dict]:
    """Dedup by signal ID (same source+guid) AND by normalised title+company
    (catches LinkedIn returning the same job across multiple queries)."""
    seen_ids = set()
    seen_title_company = set()
    out = []
    for s in signals:
        sid = s.get("id") or (s.get("source", "") + "|" + s.get("title", ""))
        key2 = (_norm_title(s.get("title", "")),
                (s.get("company") or "").strip().lower())
        if sid in seen_ids:
            continue
        if key2[0] and key2 in seen_title_company:
            continue
        seen_ids.add(sid)
        if key2[0]:
            seen_title_company.add(key2)
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
