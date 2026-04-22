#!/usr/bin/env python3
"""Assemble raw intelligence on a target (company or person).

Usage:
    python3 -m tool.deep_dive "Unilever"
    python3 -m tool.deep_dive "Jane Smith"

Outputs JSON to stdout with everything found. The Claude Code /deep-dive
slash command runs this, then synthesises the human-readable brief from the
JSON plus any ad-hoc WebSearch it wants to do.
"""
from __future__ import annotations
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool.config import SOURCES
from tool.sources import companies_house, gdelt, rss_feeds, sec_edgar
from tool.sources._http import get

log = logging.getLogger("deep_dive")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


def looks_like_person(s: str) -> bool:
    """Very rough: 2–4 title-case tokens with no Ltd/PLC/Group/Inc keywords.
    Claude is free to override by inspecting the output and deciding differently."""
    tokens = s.split()
    if not (2 <= len(tokens) <= 4):
        return False
    corp_tokens = {"ltd", "limited", "plc", "inc", "incorporated", "group",
                   "holdings", "llp", "llc", "corp", "corporation", "gmbh",
                   "ag", "sa", "srl", "&", "and"}
    low = s.lower()
    if any(t in low for t in corp_tokens):
        return False
    return all(t[:1].isupper() for t in tokens if t)


def company_snapshot(target: str) -> dict:
    """Companies House lookup + filings + officers."""
    try:
        ev = companies_house.company_events(target)
    except Exception as e:
        ev = {"error": str(e)}
    return ev


def news_for(target: str, hours_back: int = 24 * 30 * 12) -> list[dict]:
    """GDELT query for news mentioning the target in the last 12 months."""
    r = get(SOURCES["gdelt_doc"], params={
        "query": f'"{target}"',
        "mode": "ArtList",
        "format": "json",
        "timespan": f"{hours_back}h",
        "maxrecords": 50,
        "sort": "datedesc",
    })
    if not r or r.status_code != 200:
        return []
    try:
        return (r.json().get("articles") or [])[:50]
    except Exception:
        return []


def sec_filings_for(target: str) -> list[dict]:
    """Scan SEC EDGAR's latest atom for 8-Ks mentioning the target."""
    try:
        items = sec_edgar.fetch_all()
    except Exception:
        return []
    low = target.lower()
    return [i for i in items if low in (i.get("title") or "").lower()
            or low in (i.get("company") or "").lower()]


def rss_hits_for(target: str) -> list[dict]:
    """Today's RSS (regulator + trade press + procurement) filtered to hits
    mentioning the target anywhere in the title or summary."""
    try:
        feeds = rss_feeds.fetch_all()
    except Exception:
        return []
    low = target.lower()
    return [i for i in feeds
            if low in (i.get("title") or "").lower()
            or low in (i.get("summary") or "").lower()]


def linkedin_pointer(target: str) -> dict:
    """Return search URLs Sara or Claude can open / fetch. We don't crawl
    LinkedIn from here — her Recruiter does that manually. Bright Data is a
    separate automated path we invoke via the morning brief, not per request.
    """
    from urllib.parse import quote_plus
    q = quote_plus(target)
    return {
        "people_search": f"https://www.linkedin.com/search/results/people/?keywords={q}",
        "company_page":  f"https://www.linkedin.com/company/{q.replace('%20','-').lower()}/",
        "jobs_at":       f"https://www.linkedin.com/jobs/search/?keywords={q}",
    }


def build(target: str) -> dict:
    assert target.strip(), "empty target"
    as_person = looks_like_person(target)
    log.info("Target: %r (looks like %s)", target, "person" if as_person else "company")

    out: dict = {
        "target": target,
        "as_person": as_person,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {},
    }

    # Company path: Companies House lookup, regulator hits, SEC filings
    if not as_person:
        log.info("Companies House lookup…")
        out["sources"]["companies_house"] = company_snapshot(target)
        log.info("SEC EDGAR scan for %r…", target)
        out["sources"]["sec_edgar"] = sec_filings_for(target)

    log.info("RSS scan for %r…", target)
    out["sources"]["rss"] = rss_hits_for(target)
    log.info("GDELT scan for %r…", target)
    out["sources"]["gdelt"] = news_for(target)
    out["sources"]["linkedin_urls"] = linkedin_pointer(target)

    # Counts summary — helpful for Claude to decide where the signal is
    out["counts"] = {
        "companies_house_filings": len((out["sources"].get("companies_house") or {}).get("filings") or []),
        "companies_house_officers": len((out["sources"].get("companies_house") or {}).get("officers") or []),
        "rss_hits": len(out["sources"].get("rss") or []),
        "gdelt_articles": len(out["sources"].get("gdelt") or []),
        "sec_filings": len(out["sources"].get("sec_edgar") or []),
    }
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m tool.deep_dive \"<company or person>\"", file=sys.stderr)
        return 2
    target = " ".join(sys.argv[1:]).strip()
    data = build(target)
    print(json.dumps(data, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
