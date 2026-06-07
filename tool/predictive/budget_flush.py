"""Budget flush / 'use it or lose it' fiscal year-end detector.

When a company enters the final quarter of its fiscal year, any
unspent headcount or agency budget is at risk of being clawed back
by finance.  Hiring managers are highly incentivised to sign contracts
before year-end to protect next year's allocation.

This isn't a standalone trigger — it's an overlay that tags existing
BD Leads rows with a "Q4 budget window" flag so the user can
prioritise and frame the conversation around budget urgency.

Data source: Companies House free API `/company/{number}` endpoint,
which returns `accounting_reference_date` (month + day).  Zero extra
cost — one cached API call per company, reused across runs.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from tool.config import COMPANIES_HOUSE_KEY, SOURCES
from tool.sources._http import get

log = logging.getLogger("brief.budget_flush")

_STATE_DIR = Path(__file__).resolve().parent.parent / "state"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_FILE = _STATE_DIR / "ch_fye_cache.json"

_FLUSH_WINDOW_DAYS = 90


def _load_cache() -> dict[str, dict]:
    try:
        return json.loads(_CACHE_FILE.read_text()) if _CACHE_FILE.exists() else {}
    except Exception:
        return {}


def _save_cache(cache: dict[str, dict]) -> None:
    try:
        _CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass


def _fetch_fye(company_number: str) -> Optional[tuple[int, int]]:
    """Fetch accounting reference date (month, day) from CH API."""
    if not COMPANIES_HOUSE_KEY or not company_number:
        return None
    url = f"{SOURCES['companies_house_api']}/company/{company_number}"
    r = get(url, auth=(COMPANIES_HOUSE_KEY, ""))
    if not r or r.status_code != 200:
        return None
    try:
        ard = r.json().get("accounting_reference_date") or {}
        month = int(ard.get("month", 0))
        day = int(ard.get("day", 0))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return (month, day)
    except Exception:
        pass
    return None


def _days_until_fye(today: date, fye_month: int, fye_day: int) -> int:
    """Days from today until the next fiscal year-end date."""
    import calendar
    max_day = min(fye_day, calendar.monthrange(today.year, fye_month)[1])
    fye_this_year = date(today.year, fye_month, max_day)
    if fye_this_year >= today:
        return (fye_this_year - today).days
    max_day_next = min(fye_day, calendar.monthrange(today.year + 1, fye_month)[1])
    fye_next_year = date(today.year + 1, fye_month, max_day_next)
    return (fye_next_year - today).days


def get_budget_flush_flags(
    companies: list[dict],
    window_days: int = _FLUSH_WINDOW_DAYS,
) -> dict[str, dict]:
    """Check which companies are in their fiscal year-end flush window.

    `companies` is a list of dicts, each with at least 'company' (name)
    and optionally 'ch_number' (Companies House number).

    Returns {company_name_lower: {"days_left": N, "fye": "31 Mar", ...}}
    for companies within `window_days` of their year-end.
    """
    from tool.sources.companies_house import resolve_company_number

    cache = _load_cache()
    today = date.today()
    results: dict[str, dict] = {}

    for row in companies:
        co = (row.get("company") or "").strip()
        if not co:
            continue
        key = co.lower()
        if key in results:
            continue

        fye_month, fye_day = 0, 0
        if key in cache:
            fye_month = cache[key].get("month", 0)
            fye_day = cache[key].get("day", 0)
        else:
            ch_num = row.get("ch_number") or ""
            if not ch_num:
                ch_num = resolve_company_number(co) or ""
            if ch_num:
                result = _fetch_fye(ch_num)
                if result:
                    fye_month, fye_day = result
                    cache[key] = {"month": fye_month, "day": fye_day,
                                  "ch_number": ch_num}

        if not (1 <= fye_month <= 12 and 1 <= fye_day <= 31):
            continue

        days_left = _days_until_fye(today, fye_month, fye_day)
        if days_left <= window_days:
            import calendar
            month_name = calendar.month_abbr[fye_month]
            results[key] = {
                "days_left": days_left,
                "fye": f"{fye_day} {month_name}",
                "fye_month": fye_month,
                "fye_day": fye_day,
            }

    _save_cache(cache)
    log.info("Budget flush: %d of %d companies in Q4 window",
             len(results), len(companies))
    return results
