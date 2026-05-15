"""Distress-signal detection.

In a dead market, comms hires shift away from growth (new function, new
geography, new product launch) toward distress (crisis comms, IR after
share-price shock, restructuring comms, regulatory-investigation comms,
M&A under duress). The morning-brief ranker is biased toward growth
triggers. This module surfaces the distress side using a regex taxonomy
defined in tool.config.DISTRESS_SIGNALS.

It runs as a filter on top of latest_signals.json (no extra API calls),
so a new distress dashboard panel can be populated from whatever the
last scour already returned.
"""
from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Iterable

from tool.config import DISTRESS_SIGNALS

log = logging.getLogger("brief.distress")

STATE_DIR = Path(__file__).resolve().parent / "state"


# Compile once at import.
_COMPILED: list[tuple[re.Pattern, str, float]] = [
    (re.compile(pat, re.IGNORECASE), cat, w) for pat, cat, w in DISTRESS_SIGNALS
]


def classify(text: str) -> list[dict]:
    """Return all distress categories that fire on `text`. Each dict:
    {category, weight, matched_phrase}. Empty list if none."""
    if not text:
        return []
    hits = []
    seen_cats: set[str] = set()
    for rx, cat, w in _COMPILED:
        m = rx.search(text)
        if not m:
            continue
        if cat in seen_cats:
            # First hit per category is enough; avoids duplicates from
            # near-synonym patterns ("profit warning" + "issues a profit
            # warning") double-counting the same event.
            continue
        seen_cats.add(cat)
        hits.append({
            "category":       cat,
            "weight":         w,
            "matched_phrase": m.group(0).strip(),
        })
    hits.sort(key=lambda h: h["weight"], reverse=True)
    return hits


def signal_is_distress(signal: dict) -> tuple[bool, list[dict]]:
    """Apply classify() across title + summary of one signal. Returns
    (is_distress, hits). Defensive against non-string field values
    (some upstream feeds return numbers or lists for title)."""
    parts: list[str] = []
    for field in ("title", "summary"):
        v = signal.get(field)
        if v is None:
            continue
        parts.append(v if isinstance(v, str) else str(v))
    haystack = " ".join(p for p in parts if p)
    hits = classify(haystack)
    return bool(hits), hits


def _safe_weight(v) -> float:
    """Coerce a signal's `weight` field to float. Returns 1.0 on any
    failure (missing, None, non-numeric string, etc.)."""
    if v is None:
        return 1.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 1.0


# --- Account-relevance gate ----------------------------------------------
# Distress Watch is only useful if a row concerns a company Sara works.
# Without this, the FCA/CMA RSS feeds (which publish the regulators' own
# thematic reviews) and generic non-UK news flood the panel: of 28 raw
# distress hits on a real run, ~1 concerned a watchlist account. This
# gate ties every surfaced distress signal to a known account.

# Two distinct buckets — the previous single _AMBIGUOUS_NAMES set wrongly
# binned core accounts (BP/GSK/SSE/ITV/BBC/M&G) the same as common-word
# collisions, so their distress headlines were dropped when the feed
# carried no structured company field (RNS / FCA / CMA RSS always do).
#
# 1. _ENGLISH_WORD_NAMES — watchlist names that are real English words.
#    Matched ONLY via the structured `company` field, never scanned for
#    in free-text titles ("Next quarter", "peace of mind" would collide).
_ENGLISH_WORD_NAMES = {
    "mind", "next", "scope", "saga", "sage", "boots", "drax", "shell",
    "wise", "genus", "visa", "future plc", "reach plc", "rank group",
    "just group", "senior plc", "mace group",
}
#
# 2. Acronym / short proper-noun accounts (BP, GSK, SSE, M&G, ITV, BBC,
#    IAG, IMI, DNV, RELX, EY, KKR, TPG, IBM, DCC, Aon ...) are derived
#    automatically: any single-token watchlist name <= 4 chars that is
#    NOT an English word. These are matched in the ORIGINAL-CASE title
#    case-sensitively (companies appear as "BP" / "GSK" / "M&G", not the
#    lowercase common-word form), which is safe because the account gate
#    only ever runs on items already classified as distress.

_WATCHLIST_NAMES: list[str] | None = None
_WATCHLIST_PATTERNS: list[tuple[str, re.Pattern]] | None = None
_ACRONYM_PATTERNS: list[tuple[str, re.Pattern]] | None = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w& ]+", " ", (s or "").lower())).strip()


def _load_watchlist_names() -> list[str]:
    """Sara's account universe: the ~550 peer/midcap watchlist plus the
    seeded Tier-A contacts. Loaded once, lazily."""
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
        log.info("distress: watchlist load (peers) failed: %s", e)
    try:
        hc = json.loads((STATE_DIR / "hiring_contacts.json").read_text())
        if isinstance(hc, dict):
            for k in hc:
                if isinstance(k, str) and not k.startswith("_") and k.strip():
                    names.add(k.strip())
    except Exception as e:
        log.info("distress: watchlist load (contacts) failed: %s", e)
    _WATCHLIST_NAMES = sorted(names, key=len, reverse=True)
    return _WATCHLIST_NAMES


def _watchlist_patterns() -> list[tuple[str, re.Pattern]]:
    """(display_name, case-insensitive word-boundary regex) for the
    DISTINCTIVE watchlist names: multiword, or single tokens >= 5 chars
    that aren't English words. Longest-first so 'HSBC Holdings' wins
    over 'HSBC'."""
    global _WATCHLIST_PATTERNS
    if _WATCHLIST_PATTERNS is not None:
        return _WATCHLIST_PATTERNS
    pats: list[tuple[str, re.Pattern]] = []
    for name in _load_watchlist_names():
        norm = _norm(name)
        if not norm or norm in _ENGLISH_WORD_NAMES:
            continue
        token = norm.replace("&", "").replace(" ", "")
        if " " not in norm and len(token) <= 4:
            continue  # short single token → handled by _acronym_patterns
        pats.append((name, re.compile(r"(?<!\w)" + re.escape(norm) + r"(?!\w)")))
    _WATCHLIST_PATTERNS = pats
    return _WATCHLIST_PATTERNS


def _acronym_patterns() -> list[tuple[str, re.Pattern]]:
    """(display_name, case-SENSITIVE regex on the ORIGINAL title) for
    short single-token accounts (BP, GSK, SSE, M&G, ITV, BBC, IAG, ...)
    that aren't English words. Companies appear in headlines in their
    canonical casing ('BP issues profit warning'); the lowercase form is
    a common word. Boundary excludes alphanumerics but allows '&' so
    'M&G' works."""
    global _ACRONYM_PATTERNS
    if _ACRONYM_PATTERNS is not None:
        return _ACRONYM_PATTERNS
    pats: list[tuple[str, re.Pattern]] = []
    for name in _load_watchlist_names():
        norm = _norm(name)
        if not norm or norm in _ENGLISH_WORD_NAMES:
            continue
        token = norm.replace("&", "").replace(" ", "")
        if " " in norm or len(token) > 4:
            continue  # distinctive / multiword → handled elsewhere
        disp = name.strip()
        pats.append((
            name,
            re.compile(r"(?<![A-Za-z0-9])" + re.escape(disp) + r"(?![A-Za-z0-9])"),
        ))
    _ACRONYM_PATTERNS = pats
    return _ACRONYM_PATTERNS


def _word_in(needle: str, haystack: str) -> bool:
    """True if `needle` occurs in `haystack` on word boundaries. Avoids
    the false positive where company 'SSE' matched watchlist
    'Liontrust Asset Management' via the raw substring inside 'asset'."""
    if not needle or not haystack:
        return False
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack) is not None


def _account_for_signal(signal: dict) -> str | None:
    """Return the watchlist account this distress signal concerns, or
    None. Checks the structured `company` field first (reliable for any
    length incl. ambiguous short names — but word-boundary matched, not
    raw substring), then falls back to scanning the title for
    distinctive watchlist names."""
    company = signal.get("company")
    comp_norm = _norm(company if isinstance(company, str) else "")
    if comp_norm:
        for name in _load_watchlist_names():
            n = _norm(name)
            if not n:
                continue
            if comp_norm == n or _word_in(n, comp_norm) or _word_in(comp_norm, n):
                return name
    title = signal.get("title") if isinstance(signal.get("title"), str) else ""
    if title:
        title_norm = _norm(title)
        if title_norm:
            for name, pat in _watchlist_patterns():
                if pat.search(title_norm):
                    return name
        # Acronym accounts: case-sensitive against the ORIGINAL title.
        for name, pat in _acronym_patterns():
            if pat.search(title):
                return name
    return None


def filter_distress(signals: Iterable[dict],
                    require_account: bool = True) -> list[dict]:
    """Return signals that (a) match a distress category AND (b) concern
    a company in Sara's account universe. Each is annotated with
    `_distress`, `_distress_score`, `_distress_category`, and
    `_distress_account` (the matched watchlist company).

    `require_account=False` disables the account gate (used only by
    callers that have already narrowed to a specific account, e.g. the
    MPC factory's per-account distress lookup).

    Sorted by distress score descending.
    """
    annotated: list[dict] = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        is_d, hits = signal_is_distress(s)
        if not is_d:
            continue
        account = _account_for_signal(s)
        if require_account and not account:
            continue
        copy = dict(s)
        copy["_distress"] = hits
        copy["_distress_score"] = max(h["weight"] for h in hits) * _safe_weight(s.get("weight"))
        copy["_distress_category"] = hits[0]["category"]
        copy["_distress_account"] = account or ""
        annotated.append(copy)
    annotated.sort(key=lambda s: s["_distress_score"], reverse=True)
    return annotated


def load_distress_signals(limit: int = 30) -> list[dict]:
    """Return the distress subset for the dashboard. No external API.

    Primary source: latest_distress.json — written by morning_brief
    from the RAW pre-rank scour, so it contains profit warnings / CMA
    probes / CEO exits that carry no comms-role keyword and are
    therefore absent from latest_signals.json (which rank() filters to
    comms-role matches only). Entries there are already classified by
    filter_distress(), so they carry _distress / _distress_category /
    _distress_score.

    Fallback: if latest_distress.json is missing (fresh deploy before
    the first morning-brief run, or an old artifact predating this
    feed), degrade to filtering latest_signals.json — strictly worse
    (comms-filtered) but better than an empty panel.
    """
    dedicated = STATE_DIR / "latest_distress.json"
    if dedicated.exists():
        try:
            data = json.loads(dedicated.read_text())
            if isinstance(data, list):
                # Already classified upstream. Re-sort defensively in
                # case the artifact was written by an older version.
                annotated = [d for d in data if isinstance(d, dict)]
                if annotated and "_distress_score" not in annotated[0]:
                    annotated = filter_distress(annotated)
                else:
                    annotated.sort(
                        key=lambda s: s.get("_distress_score", 0.0),
                        reverse=True,
                    )
                return annotated[:limit]
        except Exception as e:
            log.info("latest_distress.json parse failed: %s — falling back", e)

    path = STATE_DIR / "latest_signals.json"
    if not path.exists():
        log.info("no distress feed and no latest_signals.json — panel empty")
        return []
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        log.info("latest_signals.json parse failed: %s", e)
        return []
    if not isinstance(data, list):
        return []
    log.info("latest_distress.json absent — degraded fallback to "
             "comms-filtered latest_signals.json")
    return filter_distress(data)[:limit]


# Human-readable category labels for the dashboard badges.
CATEGORY_LABELS = {
    "profit_warning":       "Profit warning",
    "guidance_cut":         "Guidance cut",
    "ratings":              "Ratings downgrade",
    "activist":             "Activist investor",
    "regulatory_probe":     "Regulatory probe",
    "restructuring":        "Restructuring / redundancies",
    "ceo_exit_under_cloud": "CEO exit (under cloud)",
    "m_and_a_distress":     "M&A under duress",
    "share_price_shock":    "Share-price shock",
    "crisis":               "Crisis (cyber / litigation / suspension)",
}


def category_label(category: str | None) -> str:
    if not category:
        return ""
    return CATEGORY_LABELS.get(category, category)
