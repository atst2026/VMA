"""GDELT DOC 2.0 API — global news event graph. Free, 15-min latency."""
from __future__ import annotations
import logging

from tool.config import ROLE_KEYWORDS, SOURCES
from tool.sources._http import get, signal_id

log = logging.getLogger("brief.gdelt")

# Comms-relevant terms that, combined with an executive-move or corporate-event
# term, are high-signal. GDELT's query language uses quotes for phrases and OR.
QUERY_TERMS = [
    '"head of communications"',
    '"director of communications"',
    '"communications director"',
    '"corporate affairs director"',
    '"chief communications officer"',
    '"head of corporate affairs"',
    '"head of internal communications"',
    '"PR director"',
]


def fetch_all(hours_back: int | None = None) -> list[dict]:
    """Default look-back is 48h. Sweep mode (VMA_SWEEP_DAYS=14) widens it
    to cover the full 14-day window."""
    from tool.config import sweep_days
    if hours_back is None:
        hours_back = max(48, 24 * sweep_days())
    out: list[dict] = []
    for term in QUERY_TERMS:
        r = get(SOURCES["gdelt_doc"], params={
            "query": f'{term} (appointed OR "new role" OR joins OR departs OR "stepping down" OR resigns OR promoted)',
            "mode": "ArtList",
            "format": "json",
            "timespan": f"{hours_back}h",
            "maxrecords": 50,
            "sort": "datedesc",
        })
        if not r or r.status_code != 200:
            continue
        try:
            articles = r.json().get("articles", []) or []
        except Exception:
            continue
        for a in articles:
            title = a.get("title", "")
            if not any(rk in (title or "").lower() for rk in ROLE_KEYWORDS):
                # GDELT sometimes returns adjacent hits; tighten with role-match
                continue
            out.append({
                "id": signal_id("gdelt", a.get("url", "")),
                "source": "GDELT",
                "kind": "leadership_change",
                "title": title,
                "url": a.get("url", ""),
                "published": a.get("seendate", ""),
                "company": "",
                "geo": _map_country(a.get("sourcecountry", "")),
                "summary": (a.get("socialimage") or "") and "",
                "weight": 1.0,
            })
    return out


def _map_country(cc: str) -> str:
    cc = (cc or "").upper()
    if cc in ("UK", "UNITED KINGDOM", "GB", "GBR", "ENGLAND", "SCOTLAND", "WALES"):
        return "UK"
    if cc in ("US", "USA", "UNITED STATES"):
        return "US"
    if cc in ("CHINA", "HONG KONG", "JAPAN", "SINGAPORE", "AUSTRALIA"):
        return "APAC"
    if cc in ("GERMANY", "FRANCE", "NETHERLANDS", "SPAIN", "ITALY", "SWEDEN", "IRELAND"):
        return "EU"
    return "INT"
