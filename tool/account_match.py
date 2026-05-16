"""Account-relevance gate for predictor events.

The predictor's company extractor is imperfect: "Three arrested in FCA
investigation" became a brief for *Three UK*; a Nano Dimension 3D-
printing story became one for *SSE*; an "EQS-News:" wire prefix became
*EQS*. Folding distress into the predictor (Option A) surfaced this
because the predictor — unlike the old distress feed — had no account-
relevance gate.

This module ports the proven matcher from the (retired) distress gate
and resolves an account **text-first**: it scans the actual headline /
evidence for a watchlist company name, rather than trusting the
possibly-garbage extracted `company` string. If no watchlist name
appears in the text, the event is off-universe noise and is dropped.

Fail-open: if the watchlist can't be loaded, the gate is skipped (old
behaviour) rather than silently dropping every prediction.
"""
from __future__ import annotations
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("brief.account_match")

STATE_DIR = Path(__file__).resolve().parent / "state"

# Watchlist names that are real English words — only ever safe via an
# exact structured field, never scanned for in free text ("Next
# quarter", "peace of mind" would collide). Predictor gating is
# text-only, so these are simply never matched loosely.
_ENGLISH_WORD_NAMES = {
    "mind", "next", "scope", "saga", "sage", "boots", "drax", "shell",
    "wise", "genus", "visa", "future plc", "reach plc", "rank group",
    "just group", "senior plc", "mace group",
}

# Regulator bodies. They are Tier-A accounts in their own right, but in
# a probe/enforcement/distress headline the regulator is the ACTOR, not
# the subject ("Three arrested in FCA investigation", "CMA launches
# investigation into Microsoft"). Resolving the account to the regulator
# is the wrong attribution and pure noise for Sara. Excluding them from
# the gate means the *target* company resolves instead (Microsoft,
# Barclays…) and a headline that names only a regulator + no watchlist
# company correctly drops as off-target.
_REGULATOR_EXCLUDE = {
    "fca", "cma", "ofcom", "ofgem", "ofwat", "pra", "sfo", "ico",
    "financial conduct authority", "competition and markets authority",
    "prudential regulation authority", "serious fraud office",
    "information commissioner s office",
}

_WATCHLIST_NAMES: list[str] | None = None
_DISTINCTIVE_PATTERNS: list[tuple[str, re.Pattern]] | None = None
_ACRONYM_PATTERNS: list[tuple[str, re.Pattern]] | None = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w& ]+", " ", (s or "").lower())).strip()


def _load_watchlist_names() -> list[str]:
    """Sara's account universe: the ~550 peer/midcap watchlist plus the
    seeded Tier-A contacts. Loaded once, lazily. Empty list on failure
    (callers fail open)."""
    global _WATCHLIST_NAMES
    if _WATCHLIST_NAMES is not None:
        return _WATCHLIST_NAMES
    names: set[str] = set()
    try:
        from tool.sources.companies_house import _all_watchlist_names
        for n in _all_watchlist_names():
            if n and n.strip():
                names.add(n.strip())
    except Exception as e:
        log.info("watchlist load (peers) failed: %s", e)
    try:
        hc = json.loads((STATE_DIR / "hiring_contacts.json").read_text())
        if isinstance(hc, dict):
            for k in hc:
                if isinstance(k, str) and not k.startswith("_") and k.strip():
                    names.add(k.strip())
    except Exception as e:
        log.info("watchlist load (contacts) failed: %s", e)
    _WATCHLIST_NAMES = sorted(names, key=len, reverse=True)
    return _WATCHLIST_NAMES


def _distinctive_patterns() -> list[tuple[str, re.Pattern]]:
    """(name, case-insensitive word-boundary regex) for DISTINCTIVE
    names: multiword, or single tokens >= 5 chars not English words.
    Longest-first so 'HSBC Holdings' wins over 'HSBC'."""
    global _DISTINCTIVE_PATTERNS
    if _DISTINCTIVE_PATTERNS is not None:
        return _DISTINCTIVE_PATTERNS
    pats: list[tuple[str, re.Pattern]] = []
    for name in _load_watchlist_names():
        norm = _norm(name)
        if not norm or norm in _ENGLISH_WORD_NAMES or norm in _REGULATOR_EXCLUDE:
            continue
        token = norm.replace("&", "").replace(" ", "")
        if " " not in norm and len(token) <= 4:
            continue  # short single token → acronym path
        pats.append((name, re.compile(r"(?<!\w)" + re.escape(norm) + r"(?!\w)")))
    _DISTINCTIVE_PATTERNS = pats
    return _DISTINCTIVE_PATTERNS


def _acronym_patterns() -> list[tuple[str, re.Pattern]]:
    """(name, case-SENSITIVE regex on the ORIGINAL text) for short
    single-token names (BP, GSK, SSE, M&G, ITV, BBC, IAG ...) not
    English words. Companies appear as 'BP'/'GSK'; the lowercase form
    is a common word. '&' allowed so 'M&G' works."""
    global _ACRONYM_PATTERNS
    if _ACRONYM_PATTERNS is not None:
        return _ACRONYM_PATTERNS
    pats: list[tuple[str, re.Pattern]] = []
    for name in _load_watchlist_names():
        norm = _norm(name)
        if not norm or norm in _ENGLISH_WORD_NAMES or norm in _REGULATOR_EXCLUDE:
            continue
        token = norm.replace("&", "").replace(" ", "")
        if " " in norm or len(token) > 4:
            continue
        pats.append((
            name,
            re.compile(r"(?<![A-Za-z0-9])" + re.escape(name.strip()) + r"(?![A-Za-z0-9])"),
        ))
    _ACRONYM_PATTERNS = pats
    return _ACRONYM_PATTERNS


def resolve_account(company: str | None, *texts: str) -> str | None:
    """Return the watchlist account this event genuinely concerns, or
    None. **Text-first**: a watchlist name must actually appear in the
    headline / evidence (distinctive name case-insensitively, or short
    acronym in its canonical casing). The extracted `company` string is
    deliberately NOT trusted on its own — that is exactly what produced
    'Three UK' from "Three arrested…" and 'SSE' from a Nano Dimension
    story.

    Fail-open: if the watchlist is unavailable, returns `company` (the
    gate degrades to old un-gated behaviour rather than dropping every
    prediction)."""
    wl = _load_watchlist_names()
    if not wl:
        return (company or None)  # fail open

    original = " ".join(t for t in texts if t)
    norm_text = _norm(original)

    if norm_text:
        for name, pat in _distinctive_patterns():
            if pat.search(norm_text):
                return name
    if original:
        for name, pat in _acronym_patterns():
            if pat.search(original):
                return name
    return None
