"""Hiring-gap detector — the "missing headcount" signal.

When a company is on a major hiring push (10+ open roles across its
public ATS board) but has ZERO comms/PR/corporate-affairs roles posted,
they're scaling without comms infrastructure. That's a prime target for
a pitch: "you're growing fast but have nobody to manage your public
profile."

Free: zero extra API calls. Reads the ATS headcount tallies already
collected during the daily job fetch (Greenhouse, Lever, Ashby,
Workable boards we already seed). The role-match filter that normally
drops non-comms jobs also counts them, so we know total vs comms jobs
per company.

Threshold: >=10 total open roles, 0 comms roles. A company with 40
open roles and 1 comms manager is not a gap — they've got someone.
The signal fires only at zero comms headcount against a large hiring
base.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from tool.predictive.detector import TriggerEvent

log = logging.getLogger("brief.hiring_gap")

MIN_TOTAL_JOBS = 10


def detect_hiring_gaps() -> list[TriggerEvent]:
    """Return trigger events for companies hiring heavily with no comms roles."""
    try:
        from tool.sources.jobs import get_ats_headcounts
    except ImportError:
        return []

    counts = get_ats_headcounts()
    if not counts:
        return []

    events: list[TriggerEvent] = []
    now = datetime.now(timezone.utc)

    for slug, (total, comms) in counts.items():
        if total < MIN_TOTAL_JOBS or comms > 0:
            continue

        display = slug.replace("-", " ").replace("_", " ").title()
        evidence = (
            f"{display} has {total} open roles on its public job board but "
            f"zero comms/PR/corporate-affairs positions. A company scaling "
            f"this aggressively without comms infrastructure is a prime "
            f"pitch target for a senior comms hire."
        )
        events.append(TriggerEvent(
            trigger_key="hiring_gap",
            trigger_label="Hiring gap (scaling with no comms)",
            company=display,
            evidence=evidence,
            url="",
            source_label="ATS board analysis",
            published=now,
            tier_hint="broader",
        ))

    log.info("Hiring-gap: %d companies scaling without comms from %d ATS boards",
             len(events), len(counts))
    return events
