"""Water Special-Administration Watch — the highest-value single comms
event in UK utilities.

When an England & Wales water monopoly approaches or enters the Special
Administration Regime (SAR, Water Industry Act 1991 s.24 — the Thames
Water 2024-26 path), it triggers an immediate, large, time-critical
senior-comms surge: crisis comms, regulatory/stakeholder comms,
restructuring & change comms — at the company AND at the appointed
special administrator and its advisers. Permanent reputation/corporate-
affairs hires follow stabilisation. Most recruiters react to the
appointment news; the resilience run-up is visible weeks earlier.

Design (per the strict detection-engine filter report):

  * Not a new feed. Runs over the RAW scoured signals (like the
    predictor and tool.following): the Ofwat News RSS, Investegate RNS,
    GDELT and trade press are already fetched every run. This is the
    "small extension of the existing Ofwat feed" the plan scoped — a
    focused reader on top of signals we already have, not a scraper.
  * Precision by construction. The SAR applies ONLY to the ~17 named
    regulated water & sewerage / water-only companies in England &
    Wales. We anchor every record to that fixed set (WATER_COMPANIES).
    A SAR/resilience phrase with no water-company name is dropped, so
    an energy-supplier SAR (Energy Act, e.g. Bulb) cannot false-positive
    into the water panel.
  * Two stages, both placeable:
      - "SAR live / imminent" (high)  — order applied for / made,
        special administrator appointed, Defra/SoS invoking the regime.
        The permanent reputation/stakeholder-comms hire (+ a comms-
        function review) follows; the SAR is the trigger to win the
        retained mandate now.
      - "financial-resilience watch" (medium) — Ofwat resilience action,
        cash lock-up, sub-IG downgrade, going-concern / material-
        uncertainty doubt, failed equity raise, turnaround oversight.
        SAR risk building; comms capacity expands ahead of any step.

No external calls.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

log = logging.getLogger("brief.water_sar")

from tool.state_paths import state_root
STATE_DIR = state_root()
# The fixed universe the Special Administration Regime can apply to:
# regulated water & sewerage (WaSCs) and water-only (WoCs) companies in
# England & Wales. Canonical name -> alias regex alternation. Low base
# rate (~17) is exactly why even a medium-confidence resilience hit is
# worth surfacing.
WATER_COMPANIES: dict[str, str] = {
    "Thames Water":        r"thames water",
    "Southern Water":      r"southern water",
    "Yorkshire Water":     r"yorkshire water",
    "Anglian Water":       r"anglian water",
    "Severn Trent":        r"severn trent",
    "United Utilities":    r"united utilities",
    "South West Water":    r"south west water|pennon",
    "Wessex Water":        r"wessex water",
    "Northumbrian Water":  r"northumbrian water",
    "SES Water":           r"\bses water\b|sutton and east surrey",
    "South East Water":    r"south east water",
    "Affinity Water":      r"affinity water",
    "Portsmouth Water":    r"portsmouth water",
    "South Staffs Water":  r"south staffs(?:hire)? water",
    "Cambridge Water":     r"cambridge water",
    "Bristol Water":       r"bristol water",
    "Hafren Dyfrdwy":      r"hafren dyfrdwy",
    "Welsh Water":         r"welsh water|d[wŵ]r cymru",
}
_COMPANY_RX = {
    name: re.compile(pat, re.IGNORECASE) for name, pat in WATER_COMPANIES.items()
}

# Stage 1 — SAR live / imminent. A formal step toward or into special
# administration. Highest commission value, immediate.
_SAR_LIVE_RX = re.compile(
    r"\bspecial administ(?:ration|rator)\b"
    r"|\bspecial administration regime\b|\bSAR\b"
    r"|\bWater Industry Act\b[^.]{0,40}\b(?:s\.?\s?24|section 24|special)"
    r"|\b(?:appoint(?:s|ed|ment of)?|appointing)\b[^.]{0,40}\bspecial administrator\b"
    r"|\b(?:apply|applies|applied|application)\b[^.]{0,40}\bspecial administration\b"
    r"|\b(?:placed|put|entering|enters|enter)\b[^.]{0,30}\b(?:into |in )?special administration\b"
    r"|\btemporary (?:public )?ownership\b"
    r"|\bnationalis(?:e|ed|ation)\b[^.]{0,30}\b(?:water|utility)\b",
    re.IGNORECASE,
)

# Stage 2 — financial-resilience watch. SAR risk building; the comms
# build-up starts here. Tighter phrasing than a generic annual-report
# "going concern" so it stays high-precision against the small universe.
_RESILIENCE_RX = re.compile(
    r"\bfinancial resilience\b"
    r"|\bcash lock[- ]?up\b|\bdividend lock[- ]?up\b"
    r"|\bgoing concern\b[^.]{0,40}\b(?:doubt|material uncertainty|risk)\b"
    r"|\bmaterial uncertainty\b[^.]{0,40}\bgoing concern\b"
    r"|\b(?:credit )?rating\b[^.]{0,40}\b(?:downgrad|cut to|junk|sub[- ]?investment|below investment grade)\b"
    r"|\bdowngrad(?:e|ed|es)\b[^.]{0,30}\b(?:to junk|below investment grade|sub[- ]?investment)\b"
    r"|\b(?:equity raise|recapitalis(?:e|ation)|rescue (?:deal|funding|equity))\b"
    r"|\b(?:failed|collapse[ds]?|abandon(?:ed|s)?)\b[^.]{0,30}\b(?:equity raise|refinanc|rescue)\b"
    r"|\bturnaround (?:oversight|regime|plan|programme)\b"
    r"|\b(?:liquidity|covenant)\b[^.]{0,30}\b(?:crisis|breach|pressure|crunch|shortfall)\b"
    r"|\bOfwat\b[^.]{0,50}\b(?:enforcement|undertaking|turnaround|cash lock|financial resilience|recovery regime)\b",
    re.IGNORECASE,
)

_COMMS_WHO_TO_CALL = {
    "SAR live / imminent":
        "CCO / GC + the special administrator's adviser bench — the "
        "permanent reputation/stakeholder-comms hire and a comms-function "
        "review follow stabilisation; the SAR is the trigger to secure "
        "the retained search now.",
    "financial-resilience watch":
        "CCO / Director of Corporate Affairs — the permanent change & "
        "stakeholder-comms hire (and a capability review) is reviewed "
        "ahead of any formal step; long retained-search runway.",
}

# Marketing desk (FIRST DRAFT): the same event, the marketing angle —
# brand-trust and customer-marketing capacity through the crisis.
_MARKETING_WHO_TO_CALL = {
    "SAR live / imminent":
        "CMO / Head of Brand — brand-trust repair and customer-marketing "
        "capacity follow stabilisation; the SAR is the trigger to secure the "
        "retained search now.",
    "financial-resilience watch":
        "CMO / Head of Brand — the permanent customer/brand-marketing hire "
        "(and a capability review) is weighed ahead of any formal step; long "
        "retained-search runway.",
}

from tool.profiles import active_profile as _active_profile
_WHO_TO_CALL = (_MARKETING_WHO_TO_CALL
                if _active_profile().key == "marketing" else _COMMS_WHO_TO_CALL)


def _company_in(text: str) -> str | None:
    for name, rx in _COMPANY_RX.items():
        if rx.search(text):
            return name
    return None


def _src_is_authoritative(source: str) -> bool:
    s = (source or "").lower()
    return any(k in s for k in ("ofwat", "rns", "investegate", "gov.uk",
                                "companies house", "defra"))


def detect_water_sar(signals: Iterable[dict]) -> list[dict]:
    """Return SAR / financial-resilience records, anchored to the fixed
    England & Wales regulated-water universe.

    Each record: {company, stage, signal, evidence, url, source,
    sector, who_to_call, confidence}.
    """
    from tool.advisory import advisory_for

    out: list[dict] = []
    seen: set[tuple] = set()
    for s in signals:
        if not isinstance(s, dict):
            continue
        title = s.get("title") if isinstance(s.get("title"), str) else ""
        summary = s.get("summary") if isinstance(s.get("summary"), str) else ""
        text = (title + " . " + summary).strip(" .")
        if not text:
            continue

        company = _company_in(text)
        if not company:
            continue  # SAR applies only to the named water universe

        if _SAR_LIVE_RX.search(text):
            stage = "SAR live / imminent"
            confidence = "high"
        elif _RESILIENCE_RX.search(text):
            stage = "financial-resilience watch"
            confidence = "high" if _src_is_authoritative(
                s.get("source", "")) else "medium"
        else:
            continue

        key = (company.lower(), stage)
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "company":     company,
            "stage":       stage,
            "signal":      stage,
            "evidence":    (title[:200] or summary[:200]),
            "url":         s.get("url", ""),
            "source":      s.get("source", ""),
            "sector":      "energy_utilities",
            "who_to_call": _WHO_TO_CALL[stage],
            "advisory":    advisory_for("water_sar"),
            "confidence":  confidence,
        })

    # SAR-live first, then resilience; high confidence first; then name.
    out.sort(key=lambda r: (
        r["stage"] != "SAR live / imminent",
        r["confidence"] != "high",
        r["company"],
    ))
    return out


def load_water_sar(limit: int = 20) -> list[dict]:
    """Dashboard accessor. Reads latest_water_sar.json. No external calls."""
    path = STATE_DIR / "latest_water_sar.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        log.info("latest_water_sar.json parse failed: %s", e)
        return []
    return data[:limit] if isinstance(data, list) else []
