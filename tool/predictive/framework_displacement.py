"""Competitor framework displacement detector.

When an incumbent recruitment or comms agency on a major framework
suffers a disruption — data breach, mass turnover, losing preferred
supplier status, regulatory action, or major client loss — the
framework client still has hiring volume but their primary vehicle
has stalled. That's the window to approach the procurement head.

Free: pure regex over already-fetched news signals (GDELT + trade
press). No extra API calls.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable

from tool.predictive.detector import TriggerEvent

log = logging.getLogger("brief.framework_displacement")

_AGENCY_RX = re.compile(
    r"\b(?:recruitment (?:agency|firm|company|consultancy|group)|"
    r"staffing (?:agency|firm|company|group)|"
    r"executive search (?:firm|company|group)|"
    r"search (?:&|and) selection|"
    r"talent (?:solutions|agency|firm|partners)|"
    r"resourcing (?:company|firm|agency|partner)|"
    r"(?:PR|communications?|comms|digital|creative|media|marketing) agency|"
    r"(?:PR|communications?|comms) (?:firm|consultancy))\b",
    re.IGNORECASE,
)

_DISRUPTION_RX = re.compile(
    r"\b(?:data breach|cyber[\s-]?(?:attack|incident|breach)|"
    r"information commissioner|ico fine|ico enforcement|"
    r"loses? (?:preferred|key|major|framework|public.sector) "
    r"(?:supplier|client|account|contract|status)|"
    r"lost (?:preferred|key|major|framework) (?:supplier|contract|status)|"
    r"stripped of (?:preferred|framework|supplier)|"
    r"removed from (?:framework|preferred supplier|psl)|"
    r"dropped from (?:framework|preferred supplier|psl)|"
    r"suspended from (?:framework|preferred supplier|psl)|"
    r"mass (?:redundanc|layoff|departure|exodus|resign)|"
    r"(?:staff|consultant|employee) exodus|"
    r"significant (?:turnover|attrition|departure)|"
    r"high (?:staff )?turnover|"
    r"employment tribunal|"
    r"(?:fined|penalty|sanction) (?:by|from) (?:fca|ico|hmrc|cma)|"
    r"liquidat(?:ion|ed|ing)|administ(?:ration|ered)|"
    r"(?:enter|entering|gone into) (?:administration|liquidation)|"
    r"winding[- ]?up|cease[sd]? (?:trading|operations))\b",
    re.IGNORECASE,
)

_CO = r"([A-Z][\w&.\-' ]{1,45}?)"
_SUBJECT_RX = re.compile(
    r"^" + _CO + r"\s+(?:has |is |faces?\b|hit by|suffers?\b|loses?\b|"
    r"reports?\b|announces?\b|confirms?\b|stripped|removed|dropped|suspended)",
    re.IGNORECASE,
)


def detect_framework_displacement(signals: Iterable[dict]) -> list[TriggerEvent]:
    """Scan news signals for agency disruption events."""
    results: list[TriggerEvent] = []
    seen: set[str] = set()
    now = datetime.now(timezone.utc)

    for s in signals:
        if not isinstance(s, dict):
            continue
        title = s.get("title") if isinstance(s.get("title"), str) else ""
        summary = s.get("summary") if isinstance(s.get("summary"), str) else ""
        text = (title + " . " + summary).strip(" .")
        if not text:
            continue

        if not _AGENCY_RX.search(text):
            continue
        if not _DISRUPTION_RX.search(text):
            continue

        company = None
        m = _SUBJECT_RX.search(title)
        if m:
            company = m.group(1).strip(" .,'-\"")
        if not company:
            company = (s.get("company") or "").strip()
        if not company or len(company) < 2:
            continue

        key = company.lower()
        if key in seen:
            continue
        seen.add(key)

        evidence = (
            f"Agency disruption at {company}: {title[:200]}. "
            f"When an incumbent agency suffers a disruption, their framework "
            f"clients still have hiring volume but the primary supply vehicle "
            f"has stalled — approach the procurement head now."
        )

        results.append(TriggerEvent(
            trigger_key="framework_displacement",
            trigger_label="Competitor agency disruption",
            company=company,
            evidence=evidence,
            url=s.get("url", ""),
            source_label=s.get("source", "News"),
            published=now,
            tier_hint="broader",
        ))

    log.info("Framework displacement: %d agency disruptions detected", len(results))
    return results
