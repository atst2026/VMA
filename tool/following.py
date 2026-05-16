"""Mandates Worth Following — the vacated-seat / backfill detector.

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
import json
import logging
import re
from pathlib import Path
from typing import Iterable

log = logging.getLogger("brief.following")

STATE_DIR = Path(__file__).resolve().parent / "state"

# Senior IC/CorpComms role tokens (the seat that becomes the brief).
_ROLE = (
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
        r"\b" + _CO + r"'s\s+(?:former|outgoing|ex[- ]?)\s+(?:head of|director of|chief|group head of|vp|chief communications)\b",
        # "<Co>'s <comms role> (steps down|leaves|departs|to step down…)"
        # — possessive + the seat + a departure verb. The cleanest pure-
        # departure backfill phrasing; previously uncaught.
        r"\b" + _CO + r"'s\s+" + _ROLE + r"\b[^.,;]{0,30}?\b(?:steps? down|stepping down|to step down|leaves?|leaving|departs?|departing|to leave|resigns?|resigned|retires?|retiring|to retire|has left|exits?)\b",
    )
]


def _confidence(source: str) -> str:
    s = (source or "").lower()
    if "investegate" in s or "rns" in s or "companies house" in s:
        return "high"
    return "medium"


def detect_following(signals: Iterable[dict]) -> list[dict]:
    """Return vacated-seat records, one per detected senior-comms move
    whose PREVIOUS employer resolves to a watchlist account.

    Each record: {company, vacated_role, evidence, url, source,
    sector, confidence}.  `company` is the previous employer (the
    seat that is now the brief).
    """
    from tool.account_match import resolve_account
    from tool.advisory import advisory_for
    try:
        from tool.peers import detect_sector
    except Exception:
        detect_sector = lambda _n: None  # noqa: E731

    out: list[dict] = []
    seen: set[str] = set()
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

        prev_company = None
        for rx in _PREV_EMPLOYER_PATTERNS:
            m = rx.search(text)
            if not m:
                continue
            span = (m.group(1) or "").strip(" .,'-")
            if not span or len(span) < 3:
                continue
            # Resolve ONLY the captured previous-employer span — never
            # the whole headline — so the new employer can't be picked.
            acct = resolve_account(span, span)
            if acct:
                prev_company = acct
                break
        if not prev_company:
            continue

        role = role_m.group(0).strip()
        # De-dupe on (company, role) — one vacated seat per pair.
        key = (prev_company.lower(), re.sub(r"\s+", " ", role.lower()))
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "company":      prev_company,
            "vacated_role": role.title(),
            "evidence":     title[:200] or summary[:200],
            "url":          s.get("url", ""),
            "source":       s.get("source", ""),
            "sector":       detect_sector(prev_company) or "",
            "advisory":     advisory_for("following"),
            "confidence":   _confidence(s.get("source", "")),
        })

    # High-confidence (RNS/CH) first, then by company name for stability.
    out.sort(key=lambda r: (r["confidence"] != "high", r["company"]))
    return out


def load_following(limit: int = 30) -> list[dict]:
    """Read latest_following.json for the dashboard. No external calls."""
    path = STATE_DIR / "latest_following.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        log.info("latest_following.json parse failed: %s", e)
        return []
    return data[:limit] if isinstance(data, list) else []
