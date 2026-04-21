"""Companies House signals — officer appointments/terminations, PSC changes."""
from __future__ import annotations
import logging
from typing import Iterable

from tool.config import COMPANIES_HOUSE_KEY, SOURCES
from tool.sources._http import get, signal_id

log = logging.getLogger("brief.ch")


def search_company(name: str) -> list[dict]:
    """Search Companies House for a company by name. Returns top 10 candidates."""
    if not COMPANIES_HOUSE_KEY:
        return []
    url = f"{SOURCES['companies_house_api']}/search/companies"
    r = get(url, params={"q": name, "items_per_page": 10}, auth=(COMPANIES_HOUSE_KEY, ""))
    if not r or r.status_code != 200:
        return []
    return r.json().get("items", [])


def company_officers(company_number: str) -> list[dict]:
    """Current officers of a given company number."""
    if not COMPANIES_HOUSE_KEY:
        return []
    url = f"{SOURCES['companies_house_api']}/company/{company_number}/officers"
    r = get(url, params={"items_per_page": 100}, auth=(COMPANIES_HOUSE_KEY, ""))
    if not r or r.status_code != 200:
        return []
    return r.json().get("items", [])


def company_events(name: str) -> dict:
    """For deep-dive: snapshot + officer list + filing history for one company."""
    hits = search_company(name)
    if not hits:
        return {"company": name, "found": False}
    top = hits[0]
    num = top.get("company_number", "")
    officers = company_officers(num)
    filings = []
    if num and COMPANIES_HOUSE_KEY:
        url = f"{SOURCES['companies_house_api']}/company/{num}/filing-history"
        r = get(url, params={"items_per_page": 20}, auth=(COMPANIES_HOUSE_KEY, ""))
        if r and r.status_code == 200:
            filings = r.json().get("items", [])
    return {
        "company": name,
        "found": True,
        "resolved": top,
        "officers": officers,
        "filings": filings,
    }


# The CH streaming API is the real-time one but requires a persistent connection.
# For an 08:55 Mon–Fri digest, the advanced-search "appointments for last N days"
# approach is cleaner: we query each of Sara's target company types via search,
# or fall back to scanning trending filings. With no watchlist, the honest
# daily signal here is: pull the most recent officer appointments filed today
# via the /advanced-search endpoint filtered to public companies.
def recent_appointments(days: int = 3) -> list[dict]:
    """Advanced search for officer appointments filed in the last `days` days."""
    if not COMPANIES_HOUSE_KEY:
        return []
    from datetime import date, timedelta
    start = (date.today() - timedelta(days=days)).isoformat()
    end = date.today().isoformat()
    url = f"{SOURCES['companies_house_api']}/advanced-search/companies"
    # The advanced-search endpoint is limited; for MVP we pull trending by
    # querying a generic term and filtering client-side. Real version would
    # use the streaming API or per-company watchlist.
    signals = []
    # With no watchlist in scope, return empty and rely on RNS + trade press
    # for leadership-change signal. Deep-dive still uses the full company lookup.
    return signals


def to_signals(days: int = 3) -> list[dict]:
    out = []
    for ev in recent_appointments(days):
        out.append({
            "id": signal_id("companies_house", ev.get("link", "")),
            "source": "Companies House",
            "kind": "leadership_change",
            "title": ev.get("title", "Officer appointment"),
            "url": ev.get("link", ""),
            "published": ev.get("date", ""),
            "company": ev.get("company_name", ""),
            "geo": "UK",
            "summary": ev.get("summary", ""),
            "weight": 1.0,
        })
    return out
