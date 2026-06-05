"""Auto-ingest the Market State macro coefficient (was hand-set monthly).

Layer 3 of the lead engine raises the stacking bar in a cold hiring market.
That read used to be a constant someone had to remember to edit every month
(lead_engine.MARKET_STATE, "UPDATE MONTHLY"). This module refreshes it from
the public UK indices the comment already names — the KPMG/REC Report on
Jobs **Permanent Placements Index** (PPI) and the **IPA Bellwether**
marketing-budget net balance — and persists them to
tool/state/market_state.json, which lead_engine overlays on the hand-set
constant.

Safety first: the hand-set MARKET_STATE is the fallback. A refresh only
writes an override when it parses a PLAUSIBLE PPI (30–70); on any failure it
writes nothing, so the coefficient is never worse than today's hand-set
value. Monthly cadence (skips if the stored value is < 25 days old). The
number-extraction parsers are pure functions, unit-tested against fixture
text — no network needed to verify them.
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from tool.sources._http import get, parse_rss

log = logging.getLogger("brief.market")

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
MARKET_FILE = STATE_DIR / "market_state.json"

REFRESH_EVERY_DAYS = 25

# Best-effort public landing pages that quote the figures in body text.
# Unverifiable from the sandbox (egress filtered) — graceful per the parser
# fallback, exactly like the RSS feed lanes. A page that 404s / changes
# layout simply yields no number and the hand-set value stands.
_PPI_SOURCES = [
    "https://www.rec.uk.com/our-view/research/recruitment-industry-trends/report-jobs",
    "https://kpmg.com/uk/en/home/insights/2020/02/report-on-jobs.html",
]
_BELLWETHER_SOURCES = [
    "https://ipa.co.uk/news/bellwether-report",
]
# Redundant, low-friction lane: Google News RSS summaries frequently quote
# the index value in the standfirst.
_GNEWS_RSS = "https://news.google.com/rss/search"


# ---- pure parsers (unit-tested) ----------------------------------------
def parse_ppi(text: str) -> float | None:
    """Extract the Permanent Placements Index value (a 2-digit-with-decimal
    diffusion index, ~30–70) from page/summary text. Looks for a float near
    the phrase 'permanent placements'. Returns None if nothing plausible."""
    if not text:
        return None
    low = text.lower()
    for m in re.finditer(r"permanent placements?", low):
        window = low[max(0, m.start() - 160):m.end() + 160]
        for num in re.findall(r"\b(\d{2}\.\d)\b", window):
            try:
                v = float(num)
            except ValueError:
                continue
            if 30.0 <= v <= 70.0:
                return v
    return None


def parse_bellwether(text: str) -> float | None:
    """Extract the IPA Bellwether marketing-budget NET BALANCE (a percentage,
    roughly -100..100) from page/summary text. Returns None if not found."""
    if not text:
        return None
    low = text.lower()
    for anchor in ("net balance", "marketing budgets", "budgets"):
        for m in re.finditer(re.escape(anchor), low):
            window = low[m.start():m.end() + 140]
            mm = re.search(r"([+\-]?\d{1,2}(?:\.\d)?)\s?(?:per\s?cent|%)", window)
            if mm:
                try:
                    v = float(mm.group(1))
                except ValueError:
                    continue
                if -100.0 <= v <= 100.0:
                    return v
    return None


def _fetch_text(url: str) -> str:
    r = get(url, tries=1)
    if not r or getattr(r, "status_code", 0) != 200 or not r.text:
        return ""
    # crude tag strip so the parsers see readable text
    return re.sub(r"<[^>]+>", " ", r.text)


def _gnews_text(query: str) -> str:
    r = get(_GNEWS_RSS, params={"q": query, "hl": "en-GB", "gl": "GB",
                                "ceid": "GB:en"}, tries=1)
    if not r or getattr(r, "status_code", 0) != 200 or not r.content:
        return ""
    try:
        items = parse_rss(r.content)
    except Exception:
        return ""
    return " . ".join((it.get("title", "") + " " + it.get("summary", ""))
                      for it in items[:8])


# ---- refresh orchestration ---------------------------------------------
def _load() -> dict:
    if not MARKET_FILE.exists():
        return {}
    try:
        return json.loads(MARKET_FILE.read_text())
    except Exception:
        return {}


def _is_fresh(data: dict) -> bool:
    ts = data.get("ingested_at")
    if not ts:
        return False
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days < REFRESH_EVERY_DAYS
    except Exception:
        return False


def refresh_market_state(force: bool = False) -> dict | None:
    """Refresh the persisted macro coefficient. Returns the override dict
    written, or None if nothing plausible was parsed (hand-set value stands).
    Skips the network if the stored value is still fresh."""
    existing = _load()
    if not force and _is_fresh(existing):
        log.info("market_state: stored value is fresh (<%dd) — skipping refresh",
                 REFRESH_EVERY_DAYS)
        return existing or None

    # Gather text from the landing pages + a Google-News fallback.
    ppi = None
    for url in _PPI_SOURCES:
        ppi = parse_ppi(_fetch_text(url))
        if ppi is not None:
            break
    if ppi is None:
        ppi = parse_ppi(_gnews_text(
            '"permanent placements" index REC "Report on Jobs"'))

    bellwether = None
    for url in _BELLWETHER_SOURCES:
        bellwether = parse_bellwether(_fetch_text(url))
        if bellwether is not None:
            break
    if bellwether is None:
        bellwether = parse_bellwether(_gnews_text(
            'IPA Bellwether marketing budgets "net balance"'))

    if ppi is None:
        log.info("market_state: could not parse a plausible PPI — keeping the "
                 "hand-set value (no override written)")
        return None

    override = {
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m"),
        "source": "KPMG/REC Report on Jobs PPI; IPA Bellwether",
        "default_ppi": ppi,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    if bellwether is not None:
        override["marketing_budget_balance"] = bellwether
    elif existing.get("marketing_budget_balance") is not None:
        override["marketing_budget_balance"] = existing["marketing_budget_balance"]

    try:
        MARKET_FILE.write_text(json.dumps(override, indent=2))
    except Exception as e:
        log.info("market_state: could not persist override: %s", e)
        return None
    log.info("market_state: ingested PPI=%.1f, budget-balance=%s (as of %s)",
             ppi, override.get("marketing_budget_balance"), override["as_of"])
    return override
