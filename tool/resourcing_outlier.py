"""Resourcing-benchmark outlier scan — the proactive benchmarking lead list.

tool.gender_pay_gap.resourcing_benchmark answers "are you right-sized vs
peers?" REACTIVELY, one lead at a time, from the employer's headcount
band. This module runs the same Gartner-anchored maths PROACTIVELY across
the universe the brief already indexes, and productises the result as a
ranked benchmarking lead list — the Network Rail / L'Oréal "what does a
peer comms function look like at this size?" engagement, turned into a hit
list Sara can work before anyone advertises.

Deterministic, free, no model, no network — it reads the GPG index the
nightly brief already builds (a size band per employer) and the same
co-signal (a wide or late pay gap) the ED&I angle uses.

HONEST LIMITS (no invented claims): the engine classifies a company as
materially UNDER- or OVER-resourced only when given a real OBSERVED comms
headcount to compare against the expected range. We do not currently hold
a reliable total-comms-FTE figure per employer, so the GPG-fed scan leaves
`observed` unset and every entry is a BENCHMARK opportunity, ranked by the
size of the expected function (biggest = highest-value engagement) and
amplified by a co-occurring pay-gap signal. The over/under classification
goes live the moment a headcount feed is supplied (the core already takes
it, and the tests exercise it); the data feed is the only thing gating it,
and that is stated here, not hidden behind an invented number.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from tool.gender_pay_gap import expected_comms_fte
from tool.state_paths import state_dir

log = logging.getLogger("brief.resourcing")

_DEFAULT_LIMIT = 25

# Outliers first: a measured under-resource is the clearest benchmarking
# case, then over-resource, then in-line, then benchmark-only (no measured
# headcount). Within a tier, a co-occurring pay-gap signal lifts the lead,
# then the largest expected function, then the name for determinism.
_STATUS_RANK = {"under": 0, "over": 1, "matched": 2, "benchmark": 3}


def _line(company: str, band: str, fn: str, rng: str,
          status: str, observed, co_signal: bool) -> str:
    if status == "under":
        line = (f"{company} ({band} staff): about {observed} {fn} "
                f"professionals against an expected {rng} — materially "
                f"under-resourced vs peers. The clearest benchmarking case: "
                f"quantify the gap and pitch the structure review.")
    elif status == "over":
        line = (f"{company} ({band} staff): about {observed} {fn} "
                f"professionals against an expected {rng} — above the peer "
                f"band. Benchmark to evidence the spend or right-size.")
    elif status == "matched":
        line = (f"{company} ({band} staff): about {observed} {fn} "
                f"professionals, in line with the expected {rng}. A benchmark "
                f"validates the structure and opens the senior conversation.")
    else:  # benchmark-only — no measured headcount
        line = (f"{company} ({band} staff): a peer {fn} function runs roughly "
                f"{rng} professionals (Gartner 2024). Most leaders can't say "
                f"where they sit — the productised benchmark is the "
                f"lower-barrier opener to the senior buyer.")
    if co_signal:
        line += (" A wide or late pay gap here stacks an ED&I reading onto "
                 "the benchmarking sell.")
    return line


def assess(company: str, band: str | None, observed: int | None = None,
           co_signal: bool = False, *, marketing: bool = False) -> dict | None:
    """One company's resourcing read. Returns None when the band is unknown
    (no defensible expectation to benchmark against). With `observed` set,
    classifies under/over/matched against the expected range; without it,
    the entry is a benchmark opportunity. Pure; never raises."""
    exp = expected_comms_fte(band)
    if not exp:
        return None
    lo, hi, mid, rng = exp
    fn = "marketing" if marketing else "comms"
    if observed is None:
        status = "benchmark"
    elif observed < lo:
        status = "under"
    elif observed > hi:
        status = "over"
    else:
        status = "matched"
    return {
        "company": company,
        "band": band,
        "expected_lo": lo,
        "expected_hi": hi,
        "expected_label": rng,
        "observed": observed,
        "status": status,
        "co_signal": bool(co_signal),
        "mid": mid,
        "line": _line(company, band, fn, rng, status, observed, co_signal),
        "label": "RESOURCING BENCHMARK",
        "short": "BENCHMARK",
        "sell": "Benchmarking",
    }


def resourcing_outliers(universe, *, marketing: bool = False,
                        limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """Rank a universe of companies into a benchmarking lead list.

    `universe` is any iterable of dicts with `company` and `band` (or
    `size`); optional `observed` (measured comms headcount) and `co_signal`
    (a wide/late pay gap). Outliers rank first, then benchmark-only leads by
    expected function size. Never raises; returns [] on bad input.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for item in universe or []:
        if not isinstance(item, dict):
            continue
        company = (item.get("company") or "").strip()
        if not company or company.lower() in seen:
            continue
        band = item.get("band") or item.get("size") or ""
        row = assess(company, band, item.get("observed"),
                     bool(item.get("co_signal")), marketing=marketing)
        if row:
            seen.add(company.lower())
            rows.append(row)
    rows.sort(key=lambda r: (_STATUS_RANK.get(r["status"], 9),
                             0 if r["co_signal"] else 1,
                             -r["mid"], r["company"].lower()))
    return rows[:max(1, int(limit))] if limit else rows


def _co_signal(rec: dict) -> bool:
    """A wide or late gender pay gap on the employer's GPG record — the same
    evidence the ED&I angle fires on. Makes the benchmarking lead a stacked
    benchmarking+ED&I opportunity."""
    from tool import gender_pay_gap as gpg
    med = rec.get("median")
    return bool(rec.get("late") or (med is not None and med >= gpg._WIDE))


def scan_from_gpg(limit: int = _DEFAULT_LIMIT,
                  marketing: bool = False) -> list[dict]:
    """Build the universe from the GPG index (size bands we already hold)
    and rank it. `observed` is unset (no reliable total-FTE feed), so leads
    are benchmark opportunities amplified by the pay-gap co-signal. []
    when the index is empty (host blocked / not yet refreshed)."""
    try:
        from tool import gender_pay_gap as gpg
        recs = gpg.all_records()
    except Exception as e:
        log.info("resourcing-outlier: GPG records unavailable (%s)", e)
        return []
    universe = [{
        "company": rec.get("employer") or "",
        "band": rec.get("size") or "",
        "observed": None,
        "co_signal": _co_signal(rec),
    } for rec in recs if isinstance(rec, dict)]
    return resourcing_outliers(universe, marketing=marketing, limit=limit)


def _store_path():
    return state_dir() / "resourcing_outliers.json"


def scan_and_store(limit: int = _DEFAULT_LIMIT) -> int:
    """Morning-pipeline entry point: build and persist the ranked
    benchmarking lead list. Returns the number of leads. Never raises."""
    try:
        from tool.profiles import active_profile
        marketing = active_profile().key == "marketing"
    except Exception:
        marketing = False
    try:
        rows = scan_from_gpg(limit=limit, marketing=marketing)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "leads": rows,
        }
        path = _store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=0, default=str))
        log.info("Resourcing-outlier scan: %d benchmarking leads", len(rows))
        return len(rows)
    except Exception as e:
        log.info("resourcing-outlier scan failed: %s", e)
        return 0


def load_resourcing_outliers() -> list[dict]:
    """Read-only accessor for the persisted benchmarking lead list. [] if
    the scan hasn't run. Never raises."""
    try:
        raw = json.loads(_store_path().read_text())
        return raw.get("leads", []) if isinstance(raw, dict) else []
    except Exception:
        return []
