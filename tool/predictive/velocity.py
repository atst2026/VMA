"""Press release velocity detector — flags companies whose press output
has tripled vs their rolling baseline.

From the PDF spec: 'Companies whose press output doubles over a quarter
are building Corp Comms.' This module persists per-company per-day RNS
counts in state, computes a 30-day-vs-90-day-baseline ratio, and emits
a TriggerEvent when the ratio crosses 3x.

No new API calls — operates on the same RSS signals the brief already
fetches.
"""
from __future__ import annotations
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tool.predictive.detector import TriggerEvent, extract_company
from tool.predictive import patterns as P
from tool.sources._http import signal_id

log = logging.getLogger("brief.velocity")

from tool.state_paths import state_root
STATE_DIR = state_root()
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "rns_velocity_state.json"

# Tunable thresholds. 90-day baseline. Recent window = last 30 days.
# Spike = recent average daily rate / baseline average daily rate >= 3.
BASELINE_DAYS = 90
RECENT_DAYS = 30
SPIKE_RATIO = 3.0
# Filter out companies with insufficient history (< this many days of obs)
MIN_BASELINE_OBS = 14
# Require at least this many press items in the baseline period — otherwise
# we'd flag brand-new companies as "infinite spike" the first time they
# issue any press releases.
MIN_BASELINE_COUNT = 4


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"counts": {}}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"counts": {}}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=0))


def _norm_company(name: str) -> str:
    """Same normalisation as ranking/stacker so velocity matches lead/predictor companies."""
    import re
    s = (name or "").lower().strip()
    s = re.sub(r"\b(plc|p\.l\.c\.|limited|ltd|group|holdings|inc|incorporated|llp|uk)\b\.?", "", s)
    s = re.sub(r"[^a-z0-9 &]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _is_rns_signal(s: dict) -> bool:
    """True if signal came from an RNS-style source (LSE, Investegate, FCA, etc).
    These are the press-release-type signals we want to count for velocity."""
    src = (s.get("source") or "").lower()
    return any(k in src for k in ("rns", "investegate", "fca", "ofwat", "ofcom",
                                   "ofgem", "ico", "cma"))


def ingest_signals(signals: list[dict]) -> None:
    """Add today's signals to per-company per-day counters in state.
    Idempotent across same-day runs (recounts today's bucket)."""
    state = _load_state()
    counts = state.setdefault("counts", {})
    today_key = date.today().isoformat()

    # Reset today's bucket so re-runs don't double-count
    for company_key in list(counts.keys()):
        if today_key in counts[company_key]:
            del counts[company_key][today_key]

    for s in signals:
        if not _is_rns_signal(s):
            continue
        company = s.get("company") or extract_company(s.get("title", ""))
        if not company:
            continue
        ckey = _norm_company(company)
        if not ckey:
            continue
        per_day = counts.setdefault(ckey, {})
        per_day[today_key] = per_day.get(today_key, 0) + 1

    # Prune buckets older than BASELINE_DAYS + a buffer
    cutoff = date.today() - timedelta(days=BASELINE_DAYS + 7)
    cutoff_key = cutoff.isoformat()
    for ckey in list(counts.keys()):
        counts[ckey] = {d: c for d, c in counts[ckey].items() if d >= cutoff_key}
        if not counts[ckey]:
            del counts[ckey]

    _save_state(state)


def detect_velocity_spikes(min_recent_count: int = 4) -> list[TriggerEvent]:
    """Detect companies with a 3x-or-greater press velocity spike.
    Compares recent-30-day daily rate to 60-day-prior daily rate.

    min_recent_count: require at least N press items in the recent window
    so we don't flag noise from companies that issue 1 press release a year.
    """
    state = _load_state()
    counts = state.get("counts") or {}
    today = date.today()
    recent_start = today - timedelta(days=RECENT_DAYS)
    baseline_start = today - timedelta(days=BASELINE_DAYS)

    events: list[TriggerEvent] = []
    for ckey, per_day in counts.items():
        recent_count = 0
        baseline_count = 0
        baseline_obs_days = 0
        for d, c in per_day.items():
            try:
                d_obj = date.fromisoformat(d)
            except Exception:
                continue
            if d_obj > today:
                continue
            if d_obj >= recent_start:
                recent_count += c
            elif d_obj >= baseline_start:
                baseline_count += c
                baseline_obs_days += 1

        if recent_count < min_recent_count:
            continue
        if baseline_obs_days < MIN_BASELINE_OBS:
            continue
        if baseline_count < MIN_BASELINE_COUNT:
            # Brand-new companies with no baseline press history would
            # otherwise register as "infinite spike". Skip.
            continue

        recent_rate = recent_count / RECENT_DAYS
        baseline_rate = baseline_count / (BASELINE_DAYS - RECENT_DAYS)
        ratio = recent_rate / baseline_rate

        if ratio < SPIKE_RATIO:
            continue

        # Reconstruct display name from the normalised key — best effort
        display = " ".join(w.capitalize() for w in ckey.split())
        trigger = P.BY_KEY.get("press_velocity_spike")
        # Trigger type might not be registered yet — emit using generic key
        # that the renderer/pipeline will tolerate.
        if trigger is None:
            label = "Press release velocity spike"
            trigger_key = "press_velocity_spike"
        else:
            label = trigger.label
            trigger_key = trigger.key

        events.append(TriggerEvent(
            trigger_key=trigger_key,
            trigger_label=label,
            company=display,
            evidence=(f"Press release volume at {display} ran at "
                      f"{recent_rate:.2f}/day over the last {RECENT_DAYS} "
                      f"days vs baseline of {baseline_rate:.2f}/day "
                      f"({ratio:.1f}x spike). Often precedes a Corp Comms "
                      f"or IR hire."),
            url="",
            source_label="Press release velocity tracker",
            published=datetime.now(timezone.utc),
            raw_signal_id=signal_id("velocity", ckey),
            tier_hint="covered",
        ))

    log.info("velocity: %d companies tracked, %d spike events emitted",
             len(counts), len(events))
    return events
