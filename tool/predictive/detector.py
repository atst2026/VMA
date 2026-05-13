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
    """Best-effort UK company name extraction.

    Strict-UK-only: returns a name ONLY if it's a known UK peer
    (from peers.SECTOR_PEERS) or has an explicit UK company suffix
    (plc / limited / ltd / group / etc). Drops everything else so
    non-UK companies and 'Capital' / 'Brown' / etc. extractions
    don't leak into Sara's brief.

    Order:
      1) RNS-style: separator split + suffix-at-end check
      2) Peer-name scan with word boundaries (so 'Capital' doesn't
         match peer 'Capita', and 'Brown - Forman' doesn't match
         peer 'Brown' from a UK list that doesn't contain it anyway)
      3) Return empty (event drops with 'no company')
    """
    if not title:
        return ""
    t = title.strip()
    t_nopfx = _LSE_TICKER.sub("", t).strip()
    parts = _RNS_SEPARATORS.split(t_nopfx, maxsplit=1)
    candidate = parts[0].strip()

    # 1) RNS-style: short candidate ending in a UK company suffix
    words = candidate.split()
    last3 = " ".join(words[-3:]) if len(words) >= 1 else ""
    if 1 <= len(words) <= 6 and _CO_SUFFIX_RX.search(last3):
        return candidate.rstrip(",.;:")

    # 2) Peer-name scan WITH WORD BOUNDARIES
    haystack = f" {title} {summary} ".lower()
    try:
        from tool.peers import SECTOR_PEERS
        all_peers = [p for names in SECTOR_PEERS.values() for p in names]
        for peer in sorted(all_peers, key=len, reverse=True):
            pat = r"\b" + re.escape(peer.lower()) + r"\b"
            if re.search(pat, haystack):
                return peer
    except Exception:
        pass

    # 3) No match — drop the event rather than guess.
    # (Previously fell back to title-cased prefix which leaked non-UK
    # companies like 'Brown-Forman', 'Apollo Funds', 'Akash Ambani'.)
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

    Emits debug logs for rejected items so we can see WHY nothing fired
    when a morning brief's predictive section is empty.
    """
    import logging
    log = logging.getLogger("brief.predictive.detect")

    events: list[TriggerEvent] = []
    rejected_no_company = 0
    rejected_subthreshold_regulator = 0
    rejected_contract_loss_immaterial = 0
    pattern_hits = 0

    for s in signals:
        title = s.get("title") or ""
        summary = s.get("summary") or ""
        body = f"{title} . {summary}"
        hits = P.match_triggers(body)
        if not hits:
            continue
        pattern_hits += 1
        company = (s.get("company") or "").strip() or extract_company(title, summary)
        if not company:
            rejected_no_company += 1
            log.info("drop (no company): %r [%s]", title[:100], s.get("source", ""))
            continue
        for trigger in hits:
            if trigger.key == "regulator_action":
                amt = P.extract_gbp_amount_millions(body)
                if amt is None or amt < 5:
                    rejected_subthreshold_regulator += 1
                    log.info("drop (regulator <£5m): %s — %r", company, title[:100])
                    continue
            if trigger.key == "ic_platform_rfp":
                # Gate to large UK employers — IC platform purchases at small
                # cos don't predict senior comms hires. Use the curated peers
                # list as the size proxy (~140 FTSE-350-ish employers).
                from tool.peers import detect_sector
                if detect_sector(company) is None:
                    log.info("drop (ic_platform_rfp at small employer): %s", company)
                    continue
            if trigger.key == "contract_loss":
                # Filter false positives: a contract loss only counts if it's
                # (a) reported via RNS (legally material by definition) or
                # (b) explicitly tagged with a £5m+ amount. Otherwise sports/
                # HR/SaaS "contract not renewed" noise floods the brief.
                tier = _tier_from_source_label(s.get("source", ""))
                amt = P.extract_gbp_amount_millions(body)
                material = tier == "listed" or (amt is not None and amt >= 5)
                if not material:
                    rejected_contract_loss_immaterial += 1
                    log.info("drop (contract_loss immaterial): %s — %r", company, title[:100])
                    continue
            ev = _evidence_sentence(body, trigger.patterns)
            log.info("trigger %s: %s — %r", trigger.key, company, ev[:80])
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

    log.info(
        "detect_events summary: %d items matched patterns, %d events emitted, "
        "%d dropped no-company, %d dropped regulator-subthreshold, "
        "%d dropped contract-loss-immaterial",
        pattern_hits, len(events), rejected_no_company,
        rejected_subthreshold_regulator, rejected_contract_loss_immaterial,
    )
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
