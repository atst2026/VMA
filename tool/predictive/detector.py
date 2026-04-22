"""Scan fetched signals for predictive trigger events.

A `TriggerEvent` represents one publicly-attested event at one company
that empirically precedes a senior comms hire. Multiple events at the
same company within the same 30-day window get combined into a stack
downstream (see stacker.py).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from dateutil import parser as dateparse

from tool.predictive import patterns as P


@dataclass
class TriggerEvent:
    trigger_key: str                        # patterns.BY_KEY id (e.g. "ceo_change")
    trigger_label: str                      # human label
    company: str                            # best-effort company name
    evidence: str                           # 1-line extract from the source
    url: str                                # source URL
    source_label: str                       # "LSE RNS (Investegate)" etc
    published: datetime                     # when the event was published
    raw_signal_id: str = ""                 # provenance
    tier_hint: str = "listed"               # "listed" | "covered" | "other"


# ---- Company extraction from signal titles -----------------------------
# RNS titles often look like:
#   "NatWest Group plc — Directorate Change"
#   "Barclays PLC - Appointment of Chief Executive"
#   "NWG.L NatWest Group - Directorate Change"
#   "XYZ Limited: Board Changes"
# GDELT and trade-press titles are less structured.

_RNS_SEPARATORS = re.compile(r"\s*[-—–:|]\s*", re.UNICODE)
_LSE_TICKER = re.compile(r"^[A-Z0-9]{2,6}\.[A-Z]\s+", re.IGNORECASE)
_CO_SUFFIX_RX = re.compile(
    r"\b(plc|p\.l\.c\.|plc\.|limited|ltd|ltd\.|group|holdings|inc|incorporated)\b",
    re.IGNORECASE,
)


def extract_company(title: str, summary: str = "") -> str:
    """Best-effort company name extraction from an RSS item title."""
    if not title:
        return ""
    t = title.strip()
    # Strip a leading LSE ticker like "NWG.L "
    t = _LSE_TICKER.sub("", t).strip()
    # Split on common title separators
    parts = _RNS_SEPARATORS.split(t, maxsplit=1)
    candidate = parts[0].strip()
    # If the candidate ends in a company suffix, keep it
    if _CO_SUFFIX_RX.search(candidate):
        return candidate
    # If it's short (<= 6 words) and Title-Cased, it's likely a company name
    if candidate and len(candidate.split()) <= 6:
        # Drop trailing punctuation
        return candidate.rstrip(",.;:")
    return ""


def _tier_from_source_label(label: str) -> str:
    low = (label or "").lower()
    if "rns" in low or "investegate" in low:
        return "listed"
    if any(k in low for k in ("gdelt", "prweek", "campaign", "corpcomms",
                              "provoke", "hr magazine", "people management",
                              "fca", "ofwat", "ofgem", "ofcom", "ico", "cma")):
        return "covered"
    return "other"


def _parse_date(s: str) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        d = dateparse.parse(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return datetime.now(timezone.utc)


def detect_events(signals: Iterable[dict]) -> list[TriggerEvent]:
    """Scan raw fetched signals for trigger patterns. Each signal can emit
    multiple events (e.g. an article mentioning both CEO change AND
    restructure produces two events on the same company).
    """
    events: list[TriggerEvent] = []
    for s in signals:
        title = s.get("title") or ""
        summary = s.get("summary") or ""
        body = f"{title} . {summary}"
        hits = P.match_triggers(body)
        if not hits:
            continue
        company = (s.get("company") or "").strip() or extract_company(title, summary)
        if not company:
            continue
        for trigger in hits:
            # Extra filter for regulator actions — must be material (>= £5m)
            if trigger.key == "regulator_action":
                amt = P.extract_gbp_amount_millions(body)
                if amt is None or amt < 5:
                    continue
            # Evidence: the matching sentence, trimmed
            ev = _evidence_sentence(body, trigger.patterns)
            events.append(TriggerEvent(
                trigger_key=trigger.key,
                trigger_label=trigger.label,
                company=company,
                evidence=ev,
                url=s.get("url", ""),
                source_label=s.get("source", ""),
                published=_parse_date(s.get("published", "")),
                raw_signal_id=s.get("id", ""),
                tier_hint=_tier_from_source_label(s.get("source", "")),
            ))
    return events


def _evidence_sentence(text: str, patterns: list) -> str:
    """Return the first sentence that contains a pattern hit, trimmed to ~200 chars."""
    # Split on full-stops, keep context
    for sent in re.split(r"(?<=[\.!?])\s+", text):
        if any(p.search(sent) for p in patterns):
            s = sent.strip()
            if len(s) > 220:
                s = s[:217] + "..."
            return s
    return (text[:200] + "...") if len(text) > 200 else text
