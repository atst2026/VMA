"""Job-ad cluster detection (Track B — the Thames Water mechanic).

When 2+ mid-level comms/PR roles appear at the same employer within a
30-day window AND no senior Head-of-Comms role is currently posted,
that's a ~60% base rate for a senior hire within 90 days (per the
ceiling PDF and the VMA TOOL BUILD research transcript).

State is persisted across runs in tool/state/predictive_jobs.json so the
30-day window spans multiple mornings.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dateutil import parser as dateparse

from tool.predictive import patterns as P
from tool.predictive.detector import TriggerEvent, _parse_date

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
JOBS_LOG = STATE_DIR / "predictive_jobs.json"

WINDOW_DAYS = 30


@dataclass
class JobRecord:
    company: str
    title: str
    url: str
    published: str         # ISO
    source: str
    seniority: str         # "senior" | "mid" | "other"


def _load_log() -> list[dict]:
    if not JOBS_LOG.exists():
        return []
    try:
        return json.loads(JOBS_LOG.read_text())
    except Exception:
        return []


def _save_log(data: list[dict]) -> None:
    JOBS_LOG.write_text(json.dumps(data, indent=0, default=str))


def ingest_jobs(signals: list[dict]) -> None:
    """Persist every job signal we've seen into the rolling 30-day log.
    Called once per run with the raw job-source output. Dedups by URL."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    log = _load_log()
    # Prune old
    log = [r for r in log if _parse_date(r.get("published", "")) >= cutoff]
    seen_urls = {r.get("url", "") for r in log if r.get("url")}
    for s in signals:
        if s.get("kind") != "job":
            continue
        url = s.get("url", "")
        if not url or url in seen_urls:
            continue
        title = s.get("title", "")
        seniority = (
            "senior" if P.is_senior_comms(title)
            else "mid" if P.is_midlevel_comms(title)
            else "other"
        )
        log.append({
            "company": (s.get("company") or "").strip(),
            "title": title,
            "url": url,
            "published": s.get("published", ""),
            "source": s.get("source", ""),
            "seniority": seniority,
        })
        seen_urls.add(url)
    _save_log(log)


def detect_clusters() -> list[TriggerEvent]:
    """From the persistent log, surface 'cluster' events: companies with
    2+ mid-level comms/PR roles in the last 30 days AND no senior role."""
    log = _load_log()
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)

    by_company: dict[str, list[dict]] = {}
    for r in log:
        co = (r.get("company") or "").strip().lower()
        if not co:
            continue
        pub = _parse_date(r.get("published", ""))
        if pub < cutoff:
            continue
        by_company.setdefault(co, []).append(r)

    events: list[TriggerEvent] = []
    for co_lower, rows in by_company.items():
        mid = [r for r in rows if r.get("seniority") == "mid"]
        seniors = [r for r in rows if r.get("seniority") == "senior"]
        if len(mid) < 2 or seniors:
            continue
        # Use the most recent mid-level record's published date + display name
        mid_sorted = sorted(mid, key=lambda r: _parse_date(r.get("published", "")), reverse=True)
        latest = mid_sorted[0]
        display_company = latest.get("company") or co_lower.title()
        urls = [r.get("url", "") for r in mid_sorted[:3]]
        evidence = (
            f"{len(mid)} mid-level comms/PR roles posted at {display_company} in the "
            f"last {WINDOW_DAYS} days; no senior Head-of-Comms role currently posted. "
            f"Titles: {'; '.join(r.get('title','') for r in mid_sorted[:3])}"
        )
        events.append(TriggerEvent(
            trigger_key="job_ad_cluster",
            trigger_label="Job-ad cluster (2+ mid-level comms, no senior yet)",
            company=display_company,
            evidence=evidence,
            url=urls[0] if urls else "",
            source_label=latest.get("source", "Job boards"),
            published=_parse_date(latest.get("published", "")),
            tier_hint="covered",
        ))
    return events
