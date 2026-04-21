"""Public job-board sources: Adzuna, Greenhouse, Lever, Ashby, Workable, LinkedIn Jobs (logged-off)."""
from __future__ import annotations
import logging
import os
import re
import time
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from tool.config import (
    ATS_SEEDS, DAY_RATE_CEILING_GBP, DAY_RATE_FLOOR_GBP, EXCLUDE_TITLE_TERMS,
    ROLE_KEYWORDS, SALARY_FLOOR_PERM_GBP, SOURCES,
)
from tool.sources._http import get, signal_id

log = logging.getLogger("brief.jobs")

ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")

UK_LOCATION_TOKENS = (
    "london", "manchester", "birmingham", "leeds", "bristol", "edinburgh",
    "glasgow", "cardiff", "belfast", "liverpool", "sheffield", "newcastle",
    "reading", "oxford", "cambridge", "brighton", "milton keynes", "leicester",
    "nottingham", "southampton", "portsmouth", "united kingdom", " uk",
    "england", "scotland", "wales", "northern ireland",
)


def _is_uk(location: str) -> bool:
    if not location:
        return False
    low = location.lower()
    return any(tok in low for tok in UK_LOCATION_TOKENS)


_ROLE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in ROLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_EXCLUDE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in EXCLUDE_TITLE_TERMS) + r")\b",
    re.IGNORECASE,
)


def _has_role_match(text: str) -> bool:
    if not text:
        return False
    # Hard exclude first (word-boundary): keeps agency/sales roles out of
    # the pipeline entirely so they don't use up per-source caps.
    if _EXCLUDE_RE.search(text):
        return False
    return bool(_ROLE_RE.search(text))


def _salary_ok(minimum: float | None, maximum: float | None) -> bool:
    """Accept if max (or min) clears £40k perm, or falls in £350–800/day interim range."""
    if not minimum and not maximum:
        return True  # unknown salary — don't filter out
    value = maximum or minimum
    if value and value >= SALARY_FLOOR_PERM_GBP:
        return True
    # interim day-rate band (crude): 350–800 falls within
    if value and DAY_RATE_FLOOR_GBP <= value <= DAY_RATE_CEILING_GBP:
        return True
    return False


def fetch_adzuna() -> list[dict]:
    """Adzuna UK: aggregator covering Indeed + 10+ boards."""
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        # Adzuna requires free registration. Without it we skip — the other
        # sources (Greenhouse/Lever/Ashby public feeds, LinkedIn Jobs logged-off)
        # still give us job-side coverage.
        return []
    out: list[dict] = []
    queries = [
        "internal communications", "corporate communications",
        "head of communications", "communications director",
        "pr director", "head of media relations",
    ]
    for q in queries:
        r = get(SOURCES["adzuna_gb"], params={
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_APP_KEY,
            "what": q,
            "results_per_page": 25,
            "sort_by": "date",
            "max_days_old": 3,
        })
        if not r or r.status_code != 200:
            continue
        for h in r.json().get("results", []):
            if not _has_role_match(h.get("title", "")):
                continue
            if not _salary_ok(h.get("salary_min"), h.get("salary_max")):
                continue
            out.append({
                "id": signal_id("adzuna", str(h.get("id"))),
                "source": "Adzuna (Indeed + aggregators)",
                "kind": "job",
                "title": h.get("title", ""),
                "url": h.get("redirect_url", ""),
                "published": h.get("created", ""),
                "company": (h.get("company") or {}).get("display_name", ""),
                "geo": "UK",
                "summary": h.get("description", "")[:800],
                "weight": 1.0,
            })
    return out


def fetch_greenhouse() -> list[dict]:
    out: list[dict] = []
    for slug in ATS_SEEDS.get("greenhouse", []):
        url = SOURCES["greenhouse"].format(slug=slug)
        r = get(url)
        if not r or r.status_code != 200:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        for j in data.get("jobs", []):
            title = j.get("title", "")
            if not _has_role_match(title):
                continue
            loc = (j.get("location") or {}).get("name", "")
            out.append({
                "id": signal_id("greenhouse", str(j.get("id"))),
                "source": f"Greenhouse ({slug})",
                "kind": "job",
                "title": title,
                "url": j.get("absolute_url", ""),
                "published": j.get("updated_at", ""),
                "company": slug,
                "geo": "UK" if _is_uk(loc) else "INT",
                "summary": loc,
                "weight": 1.0,
            })
    return out


def fetch_lever() -> list[dict]:
    out: list[dict] = []
    for slug in ATS_SEEDS.get("lever", []):
        url = SOURCES["lever"].format(slug=slug) + "?mode=json"
        r = get(url)
        if not r or r.status_code != 200:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        for j in data:
            title = j.get("text", "")
            if not _has_role_match(title):
                continue
            loc = (j.get("categories") or {}).get("location") or ""
            out.append({
                "id": signal_id("lever", j.get("id", "")),
                "source": f"Lever ({slug})",
                "kind": "job",
                "title": title,
                "url": j.get("hostedUrl", ""),
                "published": "",
                "company": slug,
                "geo": "UK" if _is_uk(loc) else "INT",
                "summary": loc,
                "weight": 1.0,
            })
    return out


def fetch_ashby() -> list[dict]:
    out: list[dict] = []
    for slug in ATS_SEEDS.get("ashby", []):
        url = SOURCES["ashby"].format(slug=slug)
        r = get(url)
        if not r or r.status_code != 200:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        for j in data.get("jobs", []):
            title = j.get("title", "")
            if not _has_role_match(title):
                continue
            loc = j.get("locationName", "") or ""
            out.append({
                "id": signal_id("ashby", j.get("id", "")),
                "source": f"Ashby ({slug})",
                "kind": "job",
                "title": title,
                "url": j.get("jobUrl", ""),
                "published": j.get("publishedAt", ""),
                "company": slug,
                "geo": "UK" if _is_uk(loc) else "INT",
                "summary": loc,
                "weight": 1.0,
            })
    return out


def fetch_linkedin_jobs_public() -> list[dict]:
    """Logged-off LinkedIn Jobs via public guest-view HTML.
    LinkedIn rate-limits aggressively; one query per morning is the sustainable rhythm.
    For comprehensive LinkedIn post/activity coverage, Bright Data handles it.
    """
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    out: list[dict] = []
    queries = [
        ("head of internal communications", "gb"),
        ("head of corporate communications", "gb"),
        ("communications director", "gb"),
        ("pr director", "gb"),
    ]
    for q, geo in queries:
        url = (
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={quote_plus(q)}&location=United%20Kingdom&f_TPR=r86400&start=0"
        )
        r = get(url, timeout=15, tries=1)
        if not r or r.status_code != 200 or not r.content:
            continue
        hits_before = len(out)
        try:
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select("li div.base-card") or soup.select("li") or []
            for card in cards:
                title_el = (
                    card.select_one("h3.base-search-card__title")
                    or card.select_one(".base-search-card__title")
                    or card.select_one("h3")
                )
                company_el = (
                    card.select_one("h4.base-search-card__subtitle a")
                    or card.select_one("h4.base-search-card__subtitle")
                    or card.select_one(".base-search-card__subtitle")
                )
                link_el = card.select_one("a.base-card__full-link") or card.select_one("a[href*='/jobs/view/']")
                location_el = card.select_one(".job-search-card__location")
                if not (title_el and link_el):
                    continue
                title = title_el.get_text(" ", strip=True)
                if not _has_role_match(title):
                    continue
                company = company_el.get_text(" ", strip=True) if company_el else ""
                location = location_el.get_text(" ", strip=True) if location_el else ""
                link = (link_el.get("href") or "").split("?")[0]
                out.append({
                    "id": signal_id("linkedin_jobs", link or title),
                    "source": "LinkedIn Jobs (public)",
                    "kind": "job",
                    "title": title,
                    "url": link,
                    "published": now_iso,
                    "company": company,
                    "geo": "UK",
                    "summary": location,
                    "weight": 1.1,
                })
        except Exception as e:
            log.info("LinkedIn BS4 parse failed (%s); falling back to regex", e)
        # Fallback: if BS4 found nothing (selectors changed, or error), try the
        # original regex — we lose the company name but keep titles + URLs.
        if len(out) == hits_before:
            for m in re.finditer(
                r'<a[^>]+class="base-card__full-link[^>]+href="([^"]+)"[^>]*>\s*<span[^>]*>\s*([^<]+)</span>',
                r.text,
            ):
                link, title = m.group(1), m.group(2).strip()
                if not _has_role_match(title):
                    continue
                out.append({
                    "id": signal_id("linkedin_jobs", link),
                    "source": "LinkedIn Jobs (public)",
                    "kind": "job",
                    "title": title,
                    "url": link,
                    "published": now_iso,
                    "company": "",
                    "geo": "UK",
                    "summary": "",
                    "weight": 1.1,
                })
        time.sleep(1.5)   # courteous spacing
    return out


def fetch_all() -> list[dict]:
    out: list[dict] = []
    for fn in (fetch_adzuna, fetch_greenhouse, fetch_lever, fetch_ashby, fetch_linkedin_jobs_public):
        try:
            out.extend(fn())
        except Exception as e:
            log.info("%s failed: %s", fn.__name__, e)
    return out
