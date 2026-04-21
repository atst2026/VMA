"""SEC EDGAR — 8-K filings (US parents often signal UK-sub leadership changes)."""
from __future__ import annotations
import logging
import re

from tool.sources._http import get, signal_id

log = logging.getLogger("brief.sec")

# EDGAR "latest filings" atom feed
EDGAR_LATEST = "https://www.sec.gov/cgi-bin/browse-edgar"


def fetch_all() -> list[dict]:
    out: list[dict] = []
    # Pull last 40 8-Ks (Item 5.02 covers officer changes; we pick those up in title)
    r = get(EDGAR_LATEST, params={
        "action": "getcurrent", "type": "8-K", "company": "",
        "dateb": "", "owner": "include", "count": "40", "output": "atom",
    }, headers={"User-Agent": "VMAMorningBrief stehrani@vmagroup.com"})
    if not r or r.status_code != 200 or not r.content:
        return out
    from tool.sources._http import parse_rss
    items = parse_rss(r.content)
    for it in items:
        title = it.get("title", "")
        # title format: "8-K - <COMPANY> (CIK)"
        m = re.search(r"8-K\s*-\s*(.+?)\s*\(", title)
        company = m.group(1).strip() if m else ""
        out.append({
            "id": signal_id("sec_edgar", it.get("link", title)),
            "source": "SEC EDGAR",
            "kind": "filing",
            "title": title,
            "url": it.get("link", ""),
            "published": it.get("published", ""),
            "company": company,
            "geo": "US",
            "summary": it.get("summary", "")[:600],
            "weight": 0.7,
        })
    return out
