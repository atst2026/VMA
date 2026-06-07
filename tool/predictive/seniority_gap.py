"""Seniority-gap detector — the "missing link" signal.

When a company hires a heavy-hitting senior comms leader (CCO, Head of
Comms, etc.) but the rest of the comms team visible on the ATS board
consists entirely of junior/coordinator roles, that's a structural
mismatch. The new leader will build out a middle-management tier
(Directors, Senior Managers) within their first two quarters.

Free: cross-references two data sources we already have:
  - cascade events (senior comms moves detected from news/trade press)
  - ATS job boards (Greenhouse/Lever/Ashby/Workable already fetched)

Fires when: a company appears in recent cascade events (new senior
arrival) AND their ATS board has junior comms roles but no mid-level
or director-level comms roles.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tool.predictive.detector import TriggerEvent
from tool.state_paths import state_dir

log = logging.getLogger("brief.seniority_gap")

STATE_DIR = state_dir()

_JUNIOR_RX = re.compile(
    r"\b(?:communications (?:assistant|coordinator|officer|executive|associate|intern)|"
    r"comms (?:assistant|coordinator|officer|executive|associate)|"
    r"pr (?:assistant|coordinator|officer|executive|associate)|"
    r"junior (?:communications|comms|pr|media)|"
    r"(?:communications|comms|pr|media) (?:intern|trainee|apprentice)|"
    r"content (?:assistant|coordinator|officer|executive)|"
    r"social media (?:assistant|coordinator|officer|executive))\b",
    re.IGNORECASE,
)

_MID_SENIOR_RX = re.compile(
    r"\b(?:communications (?:manager|director|lead)|"
    r"comms (?:manager|director|lead)|"
    r"pr (?:manager|director|lead)|"
    r"head of (?:communications|comms|pr|media|corporate affairs)|"
    r"director of (?:communications|comms|pr|corporate affairs)|"
    r"senior (?:communications|comms|pr) (?:manager|lead|advisor|consultant)|"
    r"(?:communications|comms|pr|media relations) manager|"
    r"(?:internal|external|corporate) (?:communications|comms) manager)\b",
    re.IGNORECASE,
)

LOOKBACK_DAYS = 90


def detect_seniority_gaps() -> list[TriggerEvent]:
    """Cross-reference recent senior arrivals with ATS board composition."""
    events_file = STATE_DIR / "cascade_events.json"
    if not events_file.exists():
        return []

    try:
        raw = json.loads(events_file.read_text())
    except Exception:
        return []

    if not isinstance(raw, list):
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()

    recent_arrivals: dict[str, dict] = {}
    for ev in raw:
        if not isinstance(ev, dict):
            continue
        detected = ev.get("detected_at", "")
        if detected < cutoff:
            continue
        new_co = (ev.get("new_company") or "").strip()
        if not new_co:
            continue
        person = ev.get("person_name", "")
        role = ev.get("role", "")
        recent_arrivals[new_co.lower()] = {
            "company": new_co,
            "person": person,
            "role": role,
        }

    if not recent_arrivals:
        return []

    try:
        from tool.predictive.cluster import _load_log
        job_log = _load_log()
    except Exception:
        job_log = []

    by_company: dict[str, list[dict]] = {}
    for j in job_log:
        co = (j.get("company") or "").strip().lower()
        if co:
            by_company.setdefault(co, []).append(j)

    results: list[TriggerEvent] = []
    now = datetime.now(timezone.utc)

    for co_lower, arrival in recent_arrivals.items():
        jobs = by_company.get(co_lower, [])
        if not jobs:
            continue

        has_junior = any(_JUNIOR_RX.search(j.get("title", "")) for j in jobs)
        has_mid_senior = any(_MID_SENIOR_RX.search(j.get("title", "")) for j in jobs)

        if has_junior and not has_mid_senior:
            company = arrival["company"]
            person = arrival["person"]
            role = arrival["role"]
            junior_titles = [j.get("title", "") for j in jobs
                             if _JUNIOR_RX.search(j.get("title", ""))]

            evidence = (
                f"{person} recently joined {company} as {role}, but the "
                f"team visible on their job board consists of junior roles "
                f"only ({'; '.join(junior_titles[:3])}). A senior leader "
                f"cannot execute strategy with only junior coordinators — "
                f"expect a middle-management build-out (Directors, Senior "
                f"Managers) within the first two quarters."
            )
            results.append(TriggerEvent(
                trigger_key="seniority_gap",
                trigger_label="Seniority gap (senior hire + junior-only team)",
                company=company,
                evidence=evidence,
                url="",
                source_label="Cascade + ATS board analysis",
                published=now,
                tier_hint="covered",
            ))

    log.info("Seniority-gap: %d gaps from %d recent arrivals × %d ATS companies",
             len(results), len(recent_arrivals), len(by_company))
    return results
