"""All RSS-based sources: RNS (Investegate), UK regulators, trade press."""
from __future__ import annotations
import logging
from typing import Iterable

from tool.config import SOURCES
from tool.sources._http import get, parse_rss, signal_id

log = logging.getLogger("brief.rss")


DEFAULT_ITEM_CAP = 60

RSS_SOURCES = [
    # (key_in_config, source_label, kind, geo, weight[, item_cap])
    # item_cap is optional (defaults to DEFAULT_ITEM_CAP=60). RNS is the
    # densest, highest-precision UK feed and 60 was throttling the
    # predictor + Mandates-Worth-Following + Water-SAR/Contract-End
    # detectors at once on a 90-day sweep — so it gets a much larger
    # window; the high-volume job feeds get a wider window too.
    # Trimmed in May 2026 - sources removed because the publisher killed
    # their RSS feed (PRWeek, Campaign, HR Magazine, People Management,
    # Provoke Media, ICO, Contracts Finder). The code skips missing
    # config keys silently; this list is kept in sync to keep the log
    # honest about what's actually being scoured.
    ("investegate_rns", "LSE RNS (Investegate)", "rns", "UK", 1.1, 300),
    ("fca_news",        "FCA News",              "regulator", "UK", 1.0, 100),
    ("ofcom_news",      "Ofcom News",            "regulator", "UK", 0.9),
    ("ofgem_news",      "Ofgem News",            "regulator", "UK", 0.9),
    ("ofwat_news",      "Ofwat News",            "regulator", "UK", 0.9),
    ("cma_news",        "CMA News",              "regulator", "UK", 1.0),
    ("find_a_tender",   "UK Find a Tender",      "procurement", "UK", 0.8, 150),
    ("civil_service_jobs","Civil Service Jobs",  "job", "UK", 0.9, 150),
    ("corpcomms",       "CorpComms Magazine",    "trade_press", "UK", 1.0),
    ("ragan",           "Ragan",                 "trade_press", "US", 0.6),
    ("prmoment",        "PRmoment",              "trade_press", "UK", 1.0),
    ("cipr_influence",  "CIPR Influence",        "trade_press", "UK", 1.0),
    # Phase 3.9 — sector trade feeds (hot-sector depth). trade_press
    # kind means ranking.py only lets actual BD news through
    # (appoint/hire/depart/restructure/etc.) — editorial is dropped by
    # the existing precision filter, so these add signal not noise.
    # Missing/dead keys are skipped silently by the loop below.
    ("inside_housing",  "Inside Housing",        "trade_press", "UK", 1.0),
    ("utility_week",    "Utility Week",          "trade_press", "UK", 1.0),
    ("pharmaphorum",    "pharmaphorum",          "trade_press", "UK", 0.9),
    ("fierce_biotech",  "FierceBiotech",         "trade_press", "US", 0.7),
    # Public-sector / HE / charity / media comms JOB feeds (kind=job ->
    # KIND_MULTIPLIER 1.4; flows to Today's Leads + Mandates Worth
    # Stealing). Opens the hot dark sectors the FTSE-skewed lanes miss.
    ("jobs_ac_uk",      "jobs.ac.uk",            "job", "UK", 1.0, 150),
    ("guardian_jobs",   "Guardian Jobs",         "job", "UK", 1.0, 150),
    # Funding / scale-up news — feeds the Funding-Round detector (was
    # GDELT-only) and the predictor. kind=news.
    ("uktn",            "UKTN",                  "news", "UK", 0.9, 100),
    ("businesscloud",   "BusinessCloud",         "news", "UK", 0.9, 100),
    ("tech_eu",         "Tech.eu",               "news", "EU", 0.7, 100),
]


def fetch_all() -> list[dict]:
    out: list[dict] = []
    for row in RSS_SOURCES:
        key, label, kind, geo, weight = row[:5]
        cap = row[5] if len(row) > 5 else DEFAULT_ITEM_CAP
        url = SOURCES.get(key)
        if not url:
            continue
        r = get(url)
        if not r or not r.content:
            continue
        try:
            items = parse_rss(r.content)
        except Exception as e:
            log.info("parse %s failed: %s", label, e)
            continue
        for it in items[:cap]:
            title = it.get("title", "")
            if not title:
                continue
            out.append({
                "id": signal_id(key, it.get("guid") or it.get("link") or title),
                "source": label,
                "kind": kind,
                "title": title,
                "url": it.get("link", ""),
                "published": it.get("published", ""),
                "company": "",
                "geo": geo,
                "summary": (it.get("summary") or "")[:1200],
                "weight": weight,
            })
    return out
