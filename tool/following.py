"""Vacated-seat / backfill detector (watchlist-gated extraction layer).

Consumed by tool.cascade to build the unified "Vacated Seats & Senior
Moves" panel — this is no longer a standalone dashboard section. It
provides the precise, watchlist-resolved previous-employer extraction
(detect_following) that gives cascade its departure coverage + gate.

When a senior IC/CorpComms person is publicly announced *moving* to a
new job, the seat they just *left* has become a live (or imminent)
brief — often before the previous employer has advertised it. The
external opportunity is the vacated seat, not the named successor's
role (Axios/Patino Feb 2026: ~60% of CCO successions are internal).

Scope (deliberate, per the detection-engine report): this works
cleanly only where the move is publicly announced and the *previous
employer* is identifiable from the announcement text — i.e. board-
adjacent / listed-company level. The unlisted long tail (Heads of
Comms at private firms, no public record of the move) is already
covered by the Companies House officer-change detector; this is the
"follow the named person" layer on top of it.

Precision-by-construction: we never trust a regex-captured company
string on its own. The captured "previous employer" span is run
through tool.account_match.resolve_account — we only emit a record if
the previous employer resolves to a watchlist company (that vacated
seat is then a brief Sara can actually work). Crucially we resolve the
PREVIOUS-employer span only, never the whole headline, so
"X joins <WatchlistCo> from <OtherCo>" does not wrongly surface the
new employer's (already-filled) seat.

No external calls. Runs over the raw scoured signals, like the
predictor.
"""
from __future__ import annotations
import logging
import re
from typing import Iterable

from tool.profiles import active_profile

log = logging.getLogger("brief.following")

# Senior IC/CorpComms role tokens (the seat that becomes the brief).
_COMMS_ROLE = (
    r"(?:group |chief |deputy |interim |global |acting )?"
    r"(?:head of (?:internal |corporate |external |group )?communications?"
    r"|head of (?:internal |corporate |external )?comms"
    r"|director of (?:internal |corporate |external |group )?communications?"
    r"|(?:internal |external |corporate |group )?communications director"
    r"|(?:chief|corporate) communications officer"
    r"|chief comms officer"
    r"|director of corporate affairs|head of corporate affairs"
    r"|corporate affairs director"
    r"|director of (?:public affairs|media relations|external affairs)"
    r"|head of (?:public affairs|media relations|external affairs)"
    r"|vp communications|vice president,? communications"
    r"|head of investor relations|director of investor relations"
    r"|head of (?:employee engagement|internal engagement))"
)

# FIRST DRAFT — senior marketing/brand role tokens (review with the
# marketing team). Same shape as the comms pattern: an optional seniority
# prefix + a senior marketing seat.
_MARKETING_ROLE = (
    r"(?:group |chief |deputy |interim |global |acting )?"
    r"(?:chief marketing officer"
    r"|chief brand officer|chief growth officer|chief customer officer"
    r"|(?:group |global )?marketing director|director of marketing"
    r"|head of marketing"
    r"|vp marketing|vice president,? marketing"
    r"|brand director|director of brand|head of brand|head of brand marketing"
    r"|marketing and communications director"
    r"|head of growth|vp growth"
    r"|head of digital marketing|digital marketing director"
    r"|head of performance marketing|performance marketing director"
    r"|head of product marketing|product marketing director"
    r"|head of ecommerce|ecommerce director|director of ecommerce"
    r"|head of demand generation|head of customer marketing|crm director)"
)

# The active profile picks which seat taxonomy this detector watches.
_ROLE = _MARKETING_ROLE if active_profile().key == "marketing" else _COMMS_ROLE
_ROLE_RX = re.compile(_ROLE, re.IGNORECASE)

# A move is happening — EITHER an arrival ("joins … from PrevCo") OR a
# departure ("PrevCo's outgoing Head of Comms steps down"). Both
# create the vacated seat; restricting to arrivals missed pure-
# departure announcements, which are the cleanest backfill signal.
_MOVE_RX = re.compile(
    r"\b(?:joins?|joining|to join|appointed|named|names|hires?|hired|"
    r"recruits?|recruited|moves? to|moving to|appointment of|"
    r"welcomes?|has joined|set to join|incoming|"
    r"steps? down|stepping down|to step down|leaves?|leaving|"
    r"departs?|departing|departed|resigns?|resigned|exits?|exited|"
    r"to leave|outgoing|retires?|retiring|to retire|has left)\b",
    re.IGNORECASE,
)

# "Previous employer" extraction. Each pattern has one capture group =
# the previous-employer phrase. Bounded (no catastrophic backtracking).
# Trailing boundary accepts an IMMEDIATE comma/period/semicolon/paren
# ("at HSBC, takes…") OR whitespace+stopword OR end-of-string.
_CO = r"([A-Z][\w&.\-' ]{2,50}?)"
_END = r"(?=[,.;:)]|\s+(?:as\b|where\b|after\b|to\b|and\b|in\b|for\b|—|–|-\s)|$)"
_PREV_EMPLOYER_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        # The reliable one: "… from <PrevCo>" — in a move announcement,
        # "from" precedes the previous employer regardless of the verb
        # (joins/hires/recruits/moves …).
        r"\bfrom\s+" + _CO + _END,
        r"\bpreviously\b[^.,;]{0,40}?\bat\s+" + _CO + _END,
        r"\b(?:formerly|previously)\s+(?:of|with)\s+" + _CO + _END,
        r"\b(?:leaves?|departs?|departed|exits?|exited)\s+" + _CO + r"\s+(?:to join|to take|after|for\b)",
        # "<Co>'s (former|outgoing|ex) <role>" — possessive + adjective.
        r"\b" + _CO + r"'s?\s+(?:former|outgoing|ex[- ]?)\s+(?:head of|director of|chief|group head of|vp|chief communications)\b",
        # "<Co>'s <comms role> (steps down|leaves|departs|to step down…)"
        # — possessive + the seat + a departure verb. The cleanest pure-
        # departure backfill phrasing; previously uncaught.
        r"\b" + _CO + r"'s?\s+" + _ROLE + r"\b[^.,;]{0,30}?\b(?:steps? down|stepping down|to step down|leaves?|leaving|departs?|departing|to leave|resigns?|resigned|retires?|retiring|to retire|has left|exits?)\b",
    )
]


def _confidence(source: str) -> str:
    s = (source or "").lower()
    if "investegate" in s or "rns" in s or "companies house" in s:
        return "high"
    return "medium"


# Generic / place / sentence words a previous-employer regex can capture
# but that are NOT a real employer. Used only to sanity-check UNRESOLVED
# (non-watchlist) spans before surfacing them as a broader-market seat —
# watchlist spans are already validated by resolve_account.
_NOT_A_COMPANY = {
    "the", "a", "an", "this", "that", "his", "her", "their", "its", "our",
    "uk", "us", "eu", "britain", "british", "england", "scotland", "wales",
    "london", "city", "government", "council", "board", "company", "group",
    "firm", "business", "team", "industry", "sector", "market", "role",
    "post", "office", "department", "ministry", "agency", "trust", "client",
    "following", "meanwhile", "however", "exclusive", "breaking", "new",
    "former", "outgoing", "interim", "acting",
}


def _looks_like_company(span: str) -> bool:
    """Sanity gate for an UNRESOLVED previous-employer span (one that did
    not match the watchlist). Keeps broader-market vacated seats honest:
    the span must look like a proper employer name, not a generic / place /
    sentence fragment."""
    s = (span or "").strip(" .,'-\"")
    if len(s) < 3 or not s[:1].isupper():
        return False
    if any(ch.isdigit() for ch in s):
        return False
    toks = s.split()
    if len(toks) > 6:                       # long span = sentence fragment
        return False
    if all(t.lower().strip(".,'\"") in _NOT_A_COMPANY for t in toks):
        return False                        # all generic words ("The Group")
    return True


def detect_following(signals: Iterable[dict],
                     include_unresolved: bool = False) -> list[dict]:
    """Return vacated-seat records, one per detected senior-comms move.

    The PREVIOUS employer (the seat that is now the brief) is extracted
    and run through resolve_account:
      * resolves to a watchlist account            -> watchlist=True
      * include_unresolved AND looks like a real
        employer (and no pattern resolved)          -> watchlist=False
        (a broader-market seat; the caller decides whether to keep it,
        e.g. gated on UK geo). Off by default so other callers are
        unchanged.

    Each record: {company, watchlist, vacated_role, evidence, url, source,
    sector, geo, confidence}. `company` is the previous employer.
    """
    from tool.account_match import resolve_account
    from tool.advisory import advisory_for
    try:
        from tool.peers import detect_sector
    except Exception:
        detect_sector = lambda _n: None  # noqa: E731

    best: dict[tuple, dict] = {}
    for s in signals:
        if not isinstance(s, dict):
            continue
        title = s.get("title") if isinstance(s.get("title"), str) else ""
        summary = s.get("summary") if isinstance(s.get("summary"), str) else ""
        text = (title + " . " + summary).strip(" .")
        if not text:
            continue
        role_m = _ROLE_RX.search(text)
        if not role_m or not _MOVE_RX.search(text):
            continue

        company = None
        is_watchlist = False
        for rx in _PREV_EMPLOYER_PATTERNS:
            m = rx.search(text)
            if not m:
                continue
            span = (m.group(1) or "").strip(" .,'-")
            if not span or len(span) < 3:
                continue
            # Resolve ONLY the captured previous-employer span — never the
            # whole headline — so the new employer can't be picked.
            acct = resolve_account(span, span)
            if acct:
                company, is_watchlist = acct, True
                break
            # No watchlist match: remember the first plausible employer span
            # but keep scanning in case a later pattern DOES resolve.
            if include_unresolved and company is None and _looks_like_company(span):
                company = span.strip(" .,'-\"")
        if not company:
            continue

        role = role_m.group(0).strip()
        key = (company.lower(), re.sub(r"\s+", " ", role.lower()))
        rec = {
            "company":      company,
            "watchlist":    is_watchlist,
            "vacated_role": role.title(),
            "evidence":     title[:200] or summary[:200],
            "url":          s.get("url", ""),
            "source":       s.get("source", ""),
            "sector":       detect_sector(company) or "",
            "geo":          s.get("geo", ""),
            "advisory":     advisory_for("following"),
            "confidence":   _confidence(s.get("source", "")) if is_watchlist else "medium",
        }
        prev = best.get(key)
        # Prefer the watchlist-resolved record if both tiers appear.
        if prev is None or (rec["watchlist"] and not prev["watchlist"]):
            best[key] = rec

    out = list(best.values())
    # Watchlist first, then high-confidence source, then company name.
    out.sort(key=lambda r: (not r["watchlist"], r["confidence"] != "high", r["company"]))
    return out
