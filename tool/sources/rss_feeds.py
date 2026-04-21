"""All RSS-based sources: RNS (Investegate), UK regulators, trade press."""
from __future__ import annotations
import logging
from typing import Iterable

from tool.config import SOURCES
from tool.sources._http import get, parse_rss, signal_id

log = logging.getLogger("brief.rss")


RSS_SOURCES = [
    # (key_in_config, source_label, kind, geo, weight)
    ("investegate_rns", "LSE RNS (Investegate)", "rns", "UK", 1.1),
    ("fca_news",        "FCA News",              "regulator", "UK", 1.0),
    ("ofcom_news",      "Ofcom News",            "regulator", "UK", 0.9),
    ("ofgem_news",      "Ofgem News",            "regulator", "UK", 0.9),
    ("ofwat_news",      "Ofwat News",            "regulator", "UK", 0.9),
    ("ico_news",        "ICO News",              "regulator", "UK", 0.9),
    ("cma_news",        "CMA News",              "regulator", "UK", 1.0),
    ("contracts_finder","UK Contracts Finder",   "procurement", "UK", 0.8),
    ("find_a_tender",   "UK Find a Tender",      "procurement", "UK", 0.8),
    ("civil_service_jobs","Civil Service Jobs",  "job", "UK", 0.9),
    ("prweek_uk",       "PRWeek UK",             "trade_press", "UK", 1.0),
    ("prweek_us",       "PRWeek US",             "trade_press", "US", 0.7),
    ("prweek_asia",     "PRWeek Asia",           "trade_press", "APAC", 0.6),
    ("campaign",        "Campaign",              "trade_press", "UK", 0.9),
    ("campaign_asia",   "Campaign Asia",         "trade_press", "APAC", 0.6),
    ("corpcomms",       "CorpComms Magazine",    "trade_press", "UK", 1.0),
    ("hr_magazine",     "HR Magazine",           "trade_press", "UK", 0.8),
    ("people_management","People Management",    "trade_press", "UK", 0.8),
    ("ragan",           "Ragan",                 "trade_press", "US", 0.6),
    ("holmes_report",   "Provoke Media",         "trade_press", "INT", 0.7),
]


def fetch_all() -> list[dict]:
    out: list[dict] = []
    for key, label, kind, geo, weight in RSS_SOURCES:
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
        for it in items[:60]:
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
