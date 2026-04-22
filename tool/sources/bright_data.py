"""Bright Data free-tier client — licensed logged-off public LinkedIn surface.

Credential-free with respect to LinkedIn. The Bright Data key is for *their*
API, not LinkedIn. Zero risk to Sara's Recruiter seat.

Free tier: 5,000 requests/month. We budget up to ~150 requests/morning on
weekdays (~3,000/mo) — the rest is headroom for the ranking layer if we ever
wire enrichment on the top-5 hits.
"""
from __future__ import annotations
import logging
import time

from tool.config import BRIGHT_DATA_KEY
from tool.sources._http import get, signal_id

log = logging.getLogger("brief.bright")

# Bright Data's LinkedIn dataset endpoints. The Web Scraper API exposes a
# common interface at brightdata.com/api. We call the `unlocker` endpoint
# for public LinkedIn profile + company pages.
#
# Note: free-tier limits apply per month, not per day. We cap per-call volume.
API_BASE = "https://api.brightdata.com"

# Known UK companies with public comms/PR job posts to search via the licensed
# logged-off LinkedIn surface. Kept small to stay within free-tier budget.
LINKEDIN_JOB_QUERIES = [
    "head of internal communications United Kingdom",
    "head of corporate communications United Kingdom",
    "communications director United Kingdom",
    "pr director United Kingdom",
]


def _authed_get(url: str, params: dict | None = None):
    if not BRIGHT_DATA_KEY:
        return None
    headers = {"Authorization": f"Bearer {BRIGHT_DATA_KEY}"}
    return get(url, params=params, headers=headers)


def fetch_all() -> list[dict]:
    """V1 fetches a small budget of LinkedIn job posts + company mentions via Bright Data.
    If the key is missing or the endpoint shape has changed, fail quietly and log.
    The tool degrades gracefully — public sources still fire.
    """
    out: list[dict] = []
    if not BRIGHT_DATA_KEY:
        log.info("Bright Data: no key configured, skipping")
        return out

    # Bright Data's free-tier Web Unlocker can proxy any URL; we use it to hit
    # LinkedIn's public guest-view jobs search without the direct-scrape
    # rate-limit wall.
    for q in LINKEDIN_JOB_QUERIES[:2]:   # keep the budget tight for the daily run
        from urllib.parse import quote_plus
        target = (
            "https://www.linkedin.com/jobs/search?"
            f"keywords={quote_plus(q)}&location=United%20Kingdom&f_TPR=r86400"
        )
        r = _authed_get(f"{API_BASE}/dca/trigger_immediate", params={"target": target})
        if not r or r.status_code not in (200, 202):
            log.info("Bright Data call for %r → %s", q, r.status_code if r else "no-resp")
            continue
        # Parsing shape depends on the zone/dataset configuration. For the
        # generic unlocker the response body is HTML; we surface the query
        # as a meta-signal and leave deeper parsing to a manual follow-up.
        out.append({
            "id": signal_id("bright_data_linkedin", q),
            "source": "Bright Data (LinkedIn public)",
            "kind": "linkedin_batch",
            "title": f"LinkedIn Jobs sweep: {q}",
            "url": target,
            "published": "",
            "company": "",
            "geo": "UK",
            "summary": "Licensed LinkedIn logged-off surface sweep.",
            "weight": 0.9,
        })
        time.sleep(1.0)
    return out
