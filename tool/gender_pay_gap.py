"""Gender Pay Gap intelligence — the ED&I advisory angle + the comms
resourcing benchmark, from the free GOV.UK service.

Two deterministic outputs, both ENRICHMENT-ONLY — they annotate leads
that already qualified on a real trigger; they never create a lead (a
standing pay-gap figure is not a compelling event, so a gap alone must
not manufacture a card — that is the generic-noise trap):

  1. edi_angle()  — when an employer's median gap is wide, widening or
     filed late, the dated, sourced ED&I entry point for VMA's ED&I
     advisory line (org-design + DEI-comms; RiverRoad). The economic
     buyer for this is the CEO/CHRO, not just the function head.

  2. resourcing_benchmark() — the GPG return carries the employer's
     headcount BAND, which powers the Gartner 2024 comms-FTE ratio
     (~1 per 1,000 staff above £3bn revenue, ~4 per 1,000 below): the
     "do you know if you're over- or under-resourced vs peers?" hook,
     and the wedge into VMA's benchmarking service.

Data: the GOV.UK Gender Pay Gap service publishes a free annual bulk
CSV covering every UK employer with 250+ staff (stable schema since
2017). We download the latest year once, slim it to the fields we use,
index by company number + normalised name, and cache for CACHE_DAYS
(the data is annual). Zero Anthropic credits, zero paid sources.

NETWORK: the host gender-pay-gap.service.gov.uk must be on the
environment's egress allowlist (the same step Companies House needed).
Until it is, every fetch returns a 403 and this module is a clean
no-op — lookups return None and nothing renders. Never raises.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone

from tool.state_paths import state_dir

log = logging.getLogger("brief.gpg")

HOST = "gender-pay-gap.service.gov.uk"
_DOWNLOAD = "https://gender-pay-gap.service.gov.uk/viewing/download-data/{year}"
# Newest first; the first year that returns a valid CSV wins. Bounded so
# a blocked host costs at most len() fast 403s, once per refresh window.
_CANDIDATE_YEARS = (2024, 2023, 2022)
CACHE_DAYS = 30
_MISS_TTL_HOURS = 12          # re-attempt at most twice a day while blocked

# Classification thresholds (median hourly gap, %). The UK all-employer
# median sits ~9-14%, so 15%+ is materially above the norm.
_WIDE = 15.0
_VERY_WIDE = 25.0

# GPG EmployerSize band -> representative headcount (band midpoint, the
# 20,000+ band floored). Drives the Gartner FTE ratio.
_SIZE_MID = {
    "250 to 499": 375,
    "500 to 999": 750,
    "1000 to 4999": 3000,
    "5000 to 19,999": 12000,
    "20,000 or more": 20000,
}

# Company-name suffixes/stopwords dropped for fuzzy matching.
_SUFFIX = {"ltd", "limited", "plc", "llp", "llc", "group", "holdings",
           "holding", "uk", "the", "company", "co", "inc", "international",
           "services", "service"}

_INDEX: dict | None = None      # process memo
_INDEX_MTIME = None             # cache-file mtime the memo was built from


# ---- name normalisation ---------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ",
                  re.sub(r"[^\w& ]+", " ", (s or "").lower())).strip()


def _strip(s: str) -> str:
    toks = [t for t in _norm(s).split() if t and t not in _SUFFIX]
    return " ".join(toks)


# ---- download + index -----------------------------------------------
def _cache_file():
    return state_dir() / "gender_pay_gap.json"


def _download_year(year: int, fetch=None):
    """One year's bulk CSV → list of slim dicts, or None on any failure
    (blocked host, non-200, unparseable). Never raises."""
    try:
        if fetch is None:
            from tool.sources._http import get as fetch
        r = fetch(_DOWNLOAD.format(year=year), timeout=40, tries=1)
        if r is None or getattr(r, "status_code", 0) != 200:
            code = getattr(r, "status_code", "no-response")
            log.info("gpg %s: %s", year, code)
            return None
        text = r.text or ""
        if "EmployerName" not in text[:2000]:
            return None
        rows = []
        for d in csv.DictReader(io.StringIO(text)):
            rows.append({
                "employer": (d.get("EmployerName") or "").strip(),
                "number": (d.get("CompanyNumber") or "").strip(),
                "mean": _f(d.get("DiffMeanHourlyPercent")),
                "median": _f(d.get("DiffMedianHourlyPercent")),
                "size": (d.get("EmployerSize") or "").strip(),
                "late": (d.get("SubmittedAfterTheDeadline") or "").strip().lower()
                in ("true", "1", "yes"),
                "url": (d.get("CompanyLinkToGPGInfo") or "").strip(),
            })
        return rows or None
    except Exception as e:
        log.info("gpg download %s failed (%s)", year, e)
        return None


def _f(v):
    try:
        return float(str(v).strip())
    except Exception:
        return None


def _resolve_get(fetch):
    """The HTTP getter, or raise ImportError if the shared helper (and its
    deps, e.g. lxml) can't be imported — so a missing dependency is
    diagnosed as exactly that, not silently masked as a per-year network
    miss (the brief showed lxml-not-installed looking like a blocked host)."""
    if fetch is not None:
        return fetch
    from tool.sources._http import get
    return get


def _build_index(fetch=None) -> dict:
    """Fetch the newest available year, slim + index it. Returns the
    index dict (possibly empty when the host is blocked); always writes a
    cache marker so a blocked host isn't re-hit every run."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        getter = _resolve_get(fetch)
    except ImportError as e:
        # A missing dependency disables every year — say so once, loudly,
        # and bail (don't retry N years and log N misleading "misses").
        log.warning("gpg: HTTP helper/deps unavailable (%s) — run "
                    "pip install -r requirements.txt; skipping refresh", e)
        getter = None
    if getter is not None:
        for year in _CANDIDATE_YEARS:
            rows = _download_year(year, fetch=getter)
            if not rows:
                continue
            by_norm, by_number, by_strip = {}, {}, {}
            for rec in rows:
                rec["year"] = year
                if rec["employer"]:
                    by_norm.setdefault(_norm(rec["employer"]), rec)
                    by_strip.setdefault(_strip(rec["employer"]), []).append(rec)
                if rec["number"]:
                    by_number.setdefault(
                        rec["number"].lstrip("0") or rec["number"], rec)
            idx = {"fetched_at": now, "year": year, "by_norm": by_norm,
                   "by_number": by_number,
                   # store only single-employer strip buckets (unambiguous)
                   "by_strip": {k: v[0] for k, v in by_strip.items()
                                if len(v) == 1}}
            try:
                _cache_file().write_text(json.dumps(idx))
            except Exception:
                pass
            log.info("gpg index: %s employers (%s)", len(by_norm), year)
            return idx
    # Nothing fetched (blocked / offline / missing dep): short-TTL marker.
    empty = {"fetched_at": now, "year": None, "by_norm": {},
             "by_number": {}, "by_strip": {}, "miss": True}
    try:
        _cache_file().write_text(json.dumps(empty))
    except Exception:
        pass
    return empty


def _fresh(idx: dict) -> bool:
    try:
        t = datetime.fromisoformat(idx.get("fetched_at"))
        age = datetime.now(timezone.utc) - t
        ttl = (_MISS_TTL_HOURS * 3600) if idx.get("miss") else (
            CACHE_DAYS * 86400)
        return age.total_seconds() < ttl
    except Exception:
        return False


def _load_cache() -> dict | None:
    try:
        f = _cache_file()
        if f.exists():
            return json.loads(f.read_text())
    except Exception:
        pass
    return None


def _cache_mtime():
    try:
        f = _cache_file()
        return f.stat().st_mtime if f.exists() else None
    except Exception:
        return None


def _index_read() -> dict:
    """Read-only: process memo → disk cache → empty. NEVER fetches, so a
    dashboard render (or a render test) can't touch the network. The
    network build happens only in refresh(), called by the nightly brief.

    The memo is keyed on the cache file's mtime, so the long-running
    dashboard process picks up a freshly DELIVERED index (a new brief
    artifact extracted into STATE_DIR) without waiting for a restart —
    while still parsing the ~3 MB index only when it actually changes."""
    global _INDEX, _INDEX_MTIME
    mt = _cache_mtime()
    if _INDEX is not None and mt == _INDEX_MTIME:
        return _INDEX
    disk = _load_cache() if mt is not None else None
    _INDEX = disk if isinstance(disk, dict) else {}
    _INDEX_MTIME = mt
    return _INDEX


def refresh(fetch=None, force: bool = False) -> dict:
    """Brief-time (re)build of the index from the GOV.UK CSV, when the
    cache is missing or older than the TTL. The only network path in this
    module. Called once per morning-brief run. Never raises."""
    global _INDEX, _INDEX_MTIME
    disk = _load_cache()
    if disk and not force and _fresh(disk):
        _INDEX, _INDEX_MTIME = disk, _cache_mtime()
        return _INDEX
    _INDEX = _build_index(fetch=fetch)
    _INDEX_MTIME = _cache_mtime()
    return _INDEX


# ---- the lookups call sites use -------------------------------------
def lookup(company: str, company_number: str | None = None):
    """The slim GPG record for a company, or None — READ-ONLY (cache only).
    Match order: company number → exact normalised name → unambiguous
    suffix-stripped name."""
    try:
        idx = _index_read()
        if not idx or not idx.get("by_norm"):
            return None
        if company_number:
            key = company_number.lstrip("0") or company_number
            r = idx["by_number"].get(key)
            if r:
                return r
        fn = _norm(company)
        r = idx["by_norm"].get(fn)
        if r:
            return r
        return idx["by_strip"].get(_strip(company))
    except Exception as e:
        log.info("gpg lookup skipped (%s)", e)
        return None


def edi_angle(record: dict | None, marketing: bool = False) -> dict | None:
    """The dated ED&I advisory entry point — only when there is a REAL,
    evidenced gap problem (wide, very wide or filed late). None otherwise:
    a small, on-time gap is not a pitch. {label, cls, line, short, url}."""
    if not record:
        return None
    med = record.get("median")
    if med is None:
        return None
    late = bool(record.get("late"))
    reasons = []
    if med >= _VERY_WIDE:
        reasons.append("a very wide median gap")
    elif med >= _WIDE:
        reasons.append("a wide median gap")
    if late:
        reasons.append("a late statutory filing")
    if not reasons:
        return None
    yr = record.get("year")
    span = f"{yr}/{str((yr or 0) + 1)[-2:]}" if yr else "latest"
    bits = [f"Median gender pay gap {med:.1f}% ({span})"]
    if late:
        bits.append("filed after the deadline")
    line = (", ".join(bits)
            + ". Statutory equality action plans become mandatory for "
              "250+ employers (voluntary Apr 2026, mandatory 2027) — a "
              "board-level ED&I risk now, not a 2027 one. "
            + ("VMA ED&I advisory: systemic-barrier review + inclusive-"
               "marketing capability." if marketing else
               "VMA ED&I advisory: systemic-barrier review + DEI-comms "
               "capability (RiverRoad / neuroinclusion)."))
    cls = "edi-bad" if (med >= _VERY_WIDE or late) else "edi-mid"
    return {"label": "ED&I ANGLE", "cls": cls, "line": line,
            "short": "ED&I", "url": record.get("url") or ""}


def resourcing_benchmark(record: dict | None,
                         marketing: bool = False) -> dict | None:
    """The Gartner-anchored 'are you right-sized vs peers?' hook, from the
    employer's headcount band. None when the band is unknown. {band, line}."""
    if not record:
        return None
    band = record.get("size") or ""
    mid = _SIZE_MID.get(band)
    if not mid:
        return None
    fn = "marketing" if marketing else "comms"
    lo = max(1, round(mid / 1000 * 1))
    hi = round(mid / 1000 * 4)
    rng = f"{lo}–{hi}" + ("+" if mid >= 20000 else "")
    line = (f"At {band} staff, comparable {fn} functions run roughly "
            f"{rng} professionals (Gartner 2024: ~1 per 1,000 above "
            f"£3bn revenue, ~4 below). Most leaders can't say where they "
            f"sit — VMA's benchmarking answers it precisely, and it's the "
            f"lower-barrier opener to the senior buyer.")
    return {"label": "RESOURCING BENCHMARK", "band": band, "line": line,
            "short": "BENCHMARK"}
