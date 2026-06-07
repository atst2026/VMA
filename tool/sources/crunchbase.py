"""Crunchbase funding-round source — proactive UK scale-up detection.

Queries the Crunchbase Open Data Map API for recent funding rounds
(Series A–E, £20m+) with a UK location filter, then emits them as
funding signals that feed straight into funding_round.detect_funding.

Optional: requires CRUNCHBASE_API_KEY env var. Without it the module
is a clean no-op (the news-based funding detector still runs).

Rate-limits: Crunchbase Basic (free) allows 200 requests/min.  We make
one search call per run, well within budget.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from tool.sources._http import get

log = logging.getLogger("brief.crunchbase")

CRUNCHBASE_API_KEY = os.environ.get("CRUNCHBASE_API_KEY", "")

_BASE = "https://api.crunchbase.com/api/v4"

_SERIES_TYPES = [
    "series_a", "series_b", "series_c", "series_d", "series_e",
    "private_equity", "corporate_round",
]

_SERIES_LABEL = {
    "series_a": "Series A",
    "series_b": "Series B",
    "series_c": "Series C",
    "series_d": "Series D",
    "series_e": "Series E",
    "private_equity": "Growth round",
    "corporate_round": "funding round",
}

MIN_GBP_M = 20.0
_USD_TO_GBP = 0.79


def fetch_funding_rounds(days_back: int = 14) -> list[dict]:
    """Return funding signals from Crunchbase, shaped identically to the
    news signals that funding_round.detect_funding expects (dict with
    title, summary, url, source keys).

    Graceful no-op when CRUNCHBASE_API_KEY is unset or the API errors.
    """
    if not CRUNCHBASE_API_KEY:
        return []

    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    params = {
        "user_key": CRUNCHBASE_API_KEY,
        "field_ids": "identifier,short_description,money_raised,announced_on,"
                     "funded_organization_identifier,funded_organization_location,"
                     "investment_type,permalink",
        "query": f"announced_on>={since}",
        "location_identifiers": "united-kingdom",
        "funding_type": ",".join(_SERIES_TYPES),
        "order": "announced_on DESC",
        "limit": "50",
    }

    resp = get(f"{_BASE}/searches/funding_rounds", params=params,
               headers={"accept": "application/json"})
    if not resp or resp.status_code != 200:
        log.info("Crunchbase fetch failed (key set=%s, status=%s)",
                 bool(CRUNCHBASE_API_KEY),
                 getattr(resp, "status_code", None))
        return []

    try:
        data = resp.json()
    except Exception:
        log.info("Crunchbase response not JSON")
        return []

    entities = data.get("entities", [])
    if not entities:
        entities = data.get("items", [])
    log.info("Crunchbase: %d funding rounds fetched", len(entities))

    signals: list[dict] = []
    for ent in entities:
        props = ent.get("properties", ent)
        org_id = props.get("funded_organization_identifier", {})
        company = (org_id.get("value") if isinstance(org_id, dict)
                   else str(org_id) if org_id else None)
        if not company:
            continue

        inv_type = props.get("investment_type", "")
        round_label = _SERIES_LABEL.get(inv_type, "funding round")

        money = props.get("money_raised", {})
        if isinstance(money, dict):
            amount_usd = money.get("value") or money.get("value_usd")
            currency = money.get("currency", "USD")
        else:
            amount_usd = money
            currency = "USD"

        try:
            amount_val = float(amount_usd)
        except (TypeError, ValueError):
            continue

        if currency.upper() == "GBP":
            amount_gbp_m = amount_val / 1_000_000
            cur_sym = "£"
        elif currency.upper() == "EUR":
            amount_gbp_m = (amount_val / 1_000_000) * 0.86
            cur_sym = "€"
        else:
            amount_gbp_m = (amount_val / 1_000_000) * _USD_TO_GBP
            cur_sym = "$"

        if amount_gbp_m < MIN_GBP_M:
            continue

        raw_m = amount_val / 1_000_000
        amount_disp = f"{cur_sym}{raw_m:.0f}m" if raw_m < 1000 else f"{cur_sym}{raw_m/1000:.1f}bn"

        announced = props.get("announced_on", "")
        desc = props.get("short_description", "")
        permalink = props.get("permalink", company.lower().replace(" ", "-"))
        url = f"https://www.crunchbase.com/funding_round/{permalink}"

        title = f"{company} raises {amount_disp} {round_label} (UK)"
        summary = desc if desc else title

        signals.append({
            "title": title,
            "summary": summary,
            "url": url,
            "source": "crunchbase",
            "kind": "news",
            "company": company,
        })

    return signals
