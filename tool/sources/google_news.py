"""Google News RSS — a redundant predictive news lane.

The predictor's recall is hostage to GDELT: the free DOC API rate-limits
hard (a recent run lost 12 of 24 queries to 'no-resp'), so trigger news
that GDELT drops never reaches the pipeline. Google News RSS is free,
key-less, RSS 2.0, and has no per-query rate-limit wall — so running the
same trigger phrasing here in parallel gives the predictor a second,
independent path to the same upstream events (CEO/CFO/Chair change, M&A,
IPO, regulator action, restructure, profit warning, comms departures).

Precision is unchanged: these are kind='news' signals; the predictor's
regex triggers + account-relevance gate (resolve_account, now with
subsidiary suppression) decide what survives — identical to the GDELT
predictive feed. This widens RECALL only; the gate still owns precision.

UK-scoped (hl=en-GB / gl=GB / ceid=GB:en). One HTTP call per query;
graceful — any failed query is skipped, never raises.
"""
from __future__ import annotations

import logging

import re

from tool.sources._http import get, parse_rss, signal_id

log = logging.getLogger("brief.gnews")

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

# Google News appends " - <Publisher>" to every title. That publisher is
# itself frequently a watchlist name (BBC, Sky, ITV ...), which would
# manufacture the SAME fake-mega-stack as the description chrome did,
# just via the title instead. Strip a trailing " - <Publisher>" /
# " – <Publisher>" / " — <Publisher>" (publisher 2-60 chars, no internal
# spaced dash) so the headline alone drives resolution.
_PUB_SUFFIX_RX = re.compile(r"\s+[-–—]\s+[^-–—]{2,60}\s*$")


def _strip_publisher(title: str) -> str:
    cleaned = _PUB_SUFFIX_RX.sub("", title or "").strip()
    return cleaned or (title or "").strip()

# Mirrors the high-cascade subset of gdelt.PREDICTIVE_TRIGGER_QUERIES.
# Google News query syntax supports quoted phrases, OR, and a trailing
# when:<n>d recency window (added at fetch time from the sweep window).
_TRIGGER_QUERIES = [
    '"chief executive" (appointed OR "steps down" OR resigns OR departs OR succession)',
    '"new CEO" OR "incoming chief executive" OR "appointed CEO"',
    '"chief financial officer" (appointed OR "steps down" OR resigns OR departs)',
    'chairman (appointed OR "steps down" OR resigns OR "to step down")',
    '"chief people officer" OR "chief human resources officer" (appointed OR resigns OR new)',
    '"head of investor relations" (appointed OR resigns OR departs)',
    '"recommended cash offer" OR "agreed to acquire" OR "all-share merger"',
    '"intention to float" OR "initial public offering" OR "admission to AIM"',
    '"profit warning" OR "issues profit warning" OR "warns on profit"',
    '"FCA fines" OR "Ofwat fines" OR "Ofcom fines" OR "Ofgem fines"',
    '"FCA investigation" OR "CMA investigation" OR "Ofgem investigation"',
    '"strategic review" OR "restructuring" OR "operating model review"',
    '"redundancies" OR "job cuts" announced',
    '"director of communications" (departs OR "steps down" OR resigns OR appointed)',
    '"head of communications" (departs OR "steps down" OR resigns OR appointed)',
    '"corporate affairs director" (departs OR resigns OR appointed)',
    '"chief communications officer" (departs OR resigns OR appointed)',
]


def fetch_predictive_signals(when_days: int | None = None) -> list[dict]:
    """Parallel trigger-news pull via Google News RSS. Returns kind='news'
    signals (same shape as gdelt.fetch_predictive_signals) for the
    predictive detector. Recency follows the sweep window."""
    from tool.config import sweep_days

    if when_days is None:
        when_days = max(2, sweep_days())
    when = f" when:{when_days}d"

    out: list[dict] = []
    seen: set[str] = set()
    failed = 0
    for q in _TRIGGER_QUERIES:
        r = get(
            GOOGLE_NEWS_RSS,
            params={
                "q": q + when,
                "hl": "en-GB",
                "gl": "GB",
                "ceid": "GB:en",
            },
            timeout=15,
        )
        if not r or getattr(r, "status_code", 0) != 200 or not r.content:
            failed += 1
            continue
        try:
            items = parse_rss(r.content)
        except Exception as e:
            log.info("google_news parse failed: %s", e)
            continue
        for it in items:
            url = it.get("link", "")
            title = _strip_publisher(it.get("title", ""))
            if not url or not title or url in seen:
                continue
            seen.add(url)
            out.append({
                "id": signal_id("gnews", url),
                "source": "Google News",
                "kind": "news",
                "title": title,
                # IMPORTANT: do NOT carry the RSS <description>. Google
                # News descriptions are aggregator CHROME (news.google.com
                # links + "Google News" attribution), not article text —
                # normalising it yields the token "google", and "Google"
                # is a watchlist name, so the text-first account gate
                # resolved ~every Google-News signal to a fake "Google"
                # mega-stack. Title-only is correct + sufficient (the
                # trigger language lives in the headline); this exactly
                # mirrors gdelt.fetch_predictive_signals (summary="").
                "summary": "",
                "url": url,
                "published": it.get("published", ""),
                "company": "",
                "geo": "UK",
                "weight": 1.0,
            })
    log.info("Google News predictive: %d unique articles across %d queries "
             "(%d failed)", len(out), len(_TRIGGER_QUERIES), failed)
    return out
