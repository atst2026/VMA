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


# ---- Predictive trigger queries ----------------------------------------
# These widen the GDELT net beyond direct comms-leader moves to cover the
# upstream trigger events that PREDICT a comms hire: CEO/CFO/Chair changes,
# M&A, IPO, regulator action, restructure, contract loss.
#
# Runs alongside the regular fetch and respects VMA_SWEEP_DAYS so on a 90-day
# sweep we backfill 90 days of historical triggers into the pipeline. This
# is what produces non-zero predictors on day 1 of operation.
PREDICTIVE_TRIGGER_QUERIES = [
    # Leadership changes — most reliable cascade trigger
    '"chief executive" (appointed OR "stepping down" OR resigns OR departs OR "to leave" OR succession)',
    '"managing director" (appointed OR "stepping down" OR resigns OR departs)',
    '"new CEO" OR "incoming chief executive" OR "appointed CEO"',
    '"chief financial officer" (appointed OR "stepping down" OR resigns OR departs)',
    '"new CFO" OR "incoming CFO" OR "appointed CFO"',
    'chairman (appointed OR "stepping down" OR resigns OR "to step down" OR "succession")',
    '"head of investor relations" (appointed OR resigns OR departs OR new)',
    '"chief people officer" (appointed OR "stepping down" OR resigns OR new)',
    '"chief human resources officer" (appointed OR new OR resigns)',
    # Promotion / succession patterns
    '"promoted to chief executive" OR "elevated to chief executive"',
    '"to succeed" CEO OR "to succeed" chief',

    # M&A and corporate events
    '"recommended cash offer" OR "firm intention to make an offer"',
    '"agreed acquisition" OR "agreed to acquire" OR "agrees to acquire"',
    '"all-share merger" OR "all-cash takeover"',

    # IPO / listing activity
    '"intention to float" OR "intention to seek admission"',
    '"initial public offering" OR "admission to AIM" OR "admission to the Main Market"',
    '"prospectus published" OR "direct listing"',

    # Regulator material action (UK)
    '"FCA fines" OR "Ofwat fines" OR "Ofcom fines" OR "Ofgem fines" OR "ICO fines"',
    '"enforcement action" million pound',
    '"FCA enforcement" OR "Ofwat enforcement"',

    # Restructure / strategic review
    '"strategic review" announced',
    'restructure announced',
    '"operating model review" OR "business simplification"',
    '"redundancies" announced consultation',

    # Material contract loss
    '"loss of major customer" OR "loss of major contract"',
    '"lost contract worth" OR "contract terminated"',

    # Senior comms departures (direct vacancy signal)
    '"director of communications" (departs OR "stepping down" OR resigns OR "to leave")',
    '"head of communications" (departs OR "stepping down" OR resigns)',
    '"corporate affairs director" (departs OR "stepping down" OR resigns)',
    '"chief communications officer" (departs OR resigns OR "stepping down")',
    '"head of internal communications" (departs OR resigns OR "stepping down")',
    '"PR director" (departs OR "stepping down" OR resigns)',
]


def fetch_predictive_signals(hours_back: int | None = None) -> list[dict]:
    """Wider GDELT pull for the PREDICTIVE detector — covers upstream
    triggers (CEO change, M&A, IPO, regulator action, etc.) that lead
    to comms hires, not direct comms-leader hires.

    Default look-back follows VMA_SWEEP_DAYS so a 90-day sweep covers
    90 days of historical news. Returns 'news' kind signals; the
    detector's regex patterns handle the actual trigger matching.

    Paces requests (0.6s between queries) + retries once on no-response
    to recover from GDELT rate-limit blips. Today's run lost 12 of 24
    queries to 'no-resp'; pacing should recover most.
    """
    import time
    from tool.config import sweep_days
    if hours_back is None:
        hours_back = max(48, 24 * sweep_days())
    out: list[dict] = []
    seen_urls: set[str] = set()
    failed = 0
    for i, query in enumerate(PREDICTIVE_TRIGGER_QUERIES):
        if i > 0:
            time.sleep(0.6)
        params = {
            "query": f"sourcelang:eng {query}",
            "mode": "ArtList",
            "format": "json",
            "timespan": f"{hours_back}h",
            "maxrecords": 75,
            "sort": "datedesc",
        }
        r = get(SOURCES["gdelt_doc"], params=params, timeout=20)
        if not r or r.status_code != 200:
            # Retry once after a short pause
            time.sleep(1.5)
            r = get(SOURCES["gdelt_doc"], params=params, timeout=20)
        if not r or r.status_code != 200:
            failed += 1
            log.info("GDELT predictive query failed after retry (%s): %s",
                     r.status_code if r else "no-resp", query[:60])
            continue
        try:
            articles = r.json().get("articles", []) or []
        except Exception:
            continue
        for a in articles:
            url = a.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            out.append({
                "id": signal_id("gdelt_pred", url),
                "source": "GDELT predictive",
                "kind": "news",
                "title": a.get("title", ""),
                "summary": "",
                "url": url,
                "published": a.get("seendate", ""),
                "company": "",
                "geo": _map_country(a.get("sourcecountry", "")),
                "weight": 1.0,
            })
    log.info("GDELT predictive: %d unique articles across %d queries (%d failed)",
             len(out), len(PREDICTIVE_TRIGGER_QUERIES), failed)
    return out


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
