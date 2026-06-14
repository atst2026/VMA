"""Advisory signals reused from the predictor pipeline — origination from
compelling events the engine already detects (ADVISORY_ENGINE.md §3, B/D/E).

The hiring lane already detects M&A, restructure/redundancy and ESG/B-Corp
events. Each is a dated, evidenced COMPELLING EVENT that originates advisory
demand whether or not anyone is hiring — two comms functions to integrate,
a change-comms programme to run, a sustainability narrative to build. This
detector reads those events and emits first-class `AdvisorySignal`s, routed
to advisory (not just "a seat"), reusing the existing service-fit lens. No
new fetches.

Discipline: only the curated, genuinely-advisory triggers below map across;
a hiring-only signal (a job-ad cluster) does not become an advisory lead.
The deterministic gate then decides KILL/DEVELOP/PURSUE — most land in
DEVELOP until a buyer is resolved, which is correct.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from tool.advisory_signals.base import AdvisorySignal

log = logging.getLogger("brief.advisory.predictors")

# advisory class -> {the predictor trigger keys that map to it, the pain it
# implies, the likely buyer, and how long the advisory window stays live}.
_CLASSES = {
    "PostMergerIntegration": {
        "keys": {"mna", "pe_acquisition", "ownership_change"},
        "pain": ("Two comms/marketing functions to integrate after the deal "
                 "— duplicated structures, unclear ownership and the "
                 "integration-comms load that post-merger failures are most "
                 "often blamed on."),
        "buyer": "Group Corporate Affairs / Comms Director (integration-lead "
                 "or CEO sponsor)",
        "window_days": 270,
    },
    "RestructureRedundancy": {
        "keys": {"restructure", "redundancy"},
        "pain": ("A restructure / redundancy programme driving acute "
                 "change-communications and leadership-coaching demand the "
                 "function rarely has formal capability for."),
        "buyer": "Comms Director + CHRO (the change-programme sponsor)",
        "window_days": 180,
    },
    "ESGCapabilityBuild": {
        "keys": {"esg_bcorp"},
        "pain": ("A public ESG / B-Corp commitment that now needs the "
                 "sustainability-narrative and disclosure-communications "
                 "capability built around it."),
        "buyer": "Corporate Affairs / Sustainability-comms lead (CEO sponsor)",
        "window_days": 365,
    },
}

# Reverse index: predictor trigger key -> advisory class.
_KEY_TO_CLASS = {k: cls for cls, cfg in _CLASSES.items() for k in cfg["keys"]}


def _parse(dt: str):
    try:
        d = datetime.fromisoformat((dt or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def predictor_advisory_signals(entries: list[dict] | None = None,
                               today: date | None = None) -> list[AdvisorySignal]:
    """Originate advisory signals from the predictor pipeline. `entries` is
    injectable for testing; defaults to the live pipeline. Never raises."""
    from tool.advisory import service_fit_for

    today = today or date.today()
    if entries is None:
        try:
            from tool import predictor_pipeline as PP
            entries = PP.all_predictors()
        except Exception as e:
            log.info("predictor advisory read skipped (%s)", e)
            return []

    out: list[AdvisorySignal] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if (entry.get("status") or "active") == "dismissed":
            continue
        company = (entry.get("company") or "").strip()
        if not company:
            continue
        events = [e for e in (entry.get("events") or []) if isinstance(e, dict)]

        # Group the entry's events by the advisory class they map to.
        for cls, cfg in _CLASSES.items():
            matched = [e for e in events
                       if e.get("trigger_key") in cfg["keys"]]
            if not matched:
                continue
            pred_keys = [e.get("trigger_key") for e in matched]
            mix = [s["key"] for s in service_fit_for(pred_keys)["services"]]
            evidence = [{"source": e.get("source") or e.get("trigger_label") or "",
                         "url": e.get("url") or ""} for e in matched]
            latest = max((_parse(e.get("published")) for e in matched
                          if _parse(e.get("published"))), default=None)
            window = None
            if latest:
                end = latest + timedelta(days=cfg["window_days"])
                window = (latest.date().isoformat(), end.date().isoformat())
            label = matched[0].get("trigger_label") or cls
            out.append(AdvisorySignal(
                trigger=cls,
                company=company,
                service_mix=mix,
                pain=cfg["pain"],
                buyer_hint=cfg["buyer"],
                why_now=f"{label} — advisory window live (deal/programme phase).",
                evidence=evidence,
                window=window,
                confidence=0.75 if len({(e.get('source') or '') for e in matched}) > 1
                else 0.6,
                company_number=(entry.get("company_number") or "").strip(),
                extra={"source": "predictor", "predictor_keys": pred_keys,
                       "pid": entry.get("pid"), "mandate": True},
            ))
    return out
