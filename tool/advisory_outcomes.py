"""Advisory outcome feedback — the loop that makes the engine selective by
MEASUREMENT, not just by design (ADVISORY_ENGINE.md §11 #1).

Advisory has no clean short-term ground truth ("does this company need an
org review?" is unfalsifiable for months). But the locked human-in-the-loop
decision (decision #1) manufactures a DENSE daily label: every advisory
PURSUE Lucy/Sara approve or spike is a "would I put my name on this?"
judgement. This module logs those judgements and turns the trailing
approval rate into an auto-throttle on the PURSUE cap — exactly as the
hiring board throttles on AD acceptance (tool.gate.acceptance).

Two tiers (the report's two-tier feedback):
  * DENSE / FAST — pursue_approved vs pursue_spiked → the trailing
    acceptance rate → the cap throttle (week-to-week stinginess).
  * SPARSE / TRUE — meeting_booked → the real conversion outcome, logged
    for /learn's quarter-to-quarter recalibration (and counted as a strong
    accept here).

Pure functions over an injected record list (testable); record() is the
only IO, appending one JSON line per decision to the per-profile state dir.
Never raises.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from tool.state_paths import state_dir

log = logging.getLogger("brief.advisory.outcomes")

# Trailing window + throttle thresholds (advisory cycles run longer and at
# lower volume than hiring, so a wider window and a smaller sample).
ACCEPT_WINDOW_DAYS = 14
MIN_VERDICTS = 6
ACCEPT_FLOOR = 0.5
THROTTLED_CAP = 3            # the PURSUE cap when approval dips

# Decisions that count toward the approval rate.
_ACCEPTED = {"pursue_approved", "meeting_booked"}
_REJECTED = {"pursue_spiked"}
_COUNTED = _ACCEPTED | _REJECTED


def _log_file():
    return state_dir() / "advisory_outcomes.jsonl"


def record(company: str, trigger: str, decision: str, *,
           decided_by: str = "", note: str = "", conviction=None) -> bool:
    """Append one human decision on an advisory lead. `decision` is one of
    pursue_approved / pursue_spiked / meeting_booked. Returns True on write.
    Never raises (a logging failure must not break the call flow)."""
    if decision not in (_COUNTED | {"develop", "kill"}):
        return False
    row = {"ts": datetime.now(timezone.utc).isoformat(),
           "company": company or "", "trigger": trigger or "",
           "decision": decision, "by": decided_by or "", "note": note or "",
           "conviction": conviction}
    try:
        with open(_log_file(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return True
    except Exception as e:
        log.info("advisory outcome log skipped (%s)", e)
        return False


def _load() -> list[dict]:
    try:
        f = _log_file()
        if not f.exists():
            return []
        out = []
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        return out
    except Exception:
        return []


def acceptance(records: list[dict] | None = None,
               now: datetime | None = None) -> dict:
    """Trailing-window approval rate + throttle flag. Accepted =
    pursue_approved + meeting_booked; the denominator is accepted +
    pursue_spiked (develop/kill are not human PURSUE decisions)."""
    now = now or datetime.now(timezone.utc)
    recs = _load() if records is None else records
    n = accepted = 0
    for r in recs or []:
        d = r.get("decision")
        if d not in _COUNTED:
            continue
        try:
            ts = datetime.fromisoformat((r.get("ts") or "").replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if (now - ts).days > ACCEPT_WINDOW_DAYS:
            continue
        n += 1
        accepted += 1 if d in _ACCEPTED else 0
    rate = (accepted / n) if n else None
    throttled = (n >= MIN_VERDICTS and rate is not None and rate < ACCEPT_FLOOR)
    return {"n": n, "accepted": accepted, "rate": rate, "throttled": throttled,
            "cap": decision_cap_from(throttled)}


def decision_cap_from(throttled: bool) -> int:
    """The PURSUE cap given the throttle state."""
    from tool.advisory_gate import ADVISORY_DAILY_CAP
    return THROTTLED_CAP if throttled else ADVISORY_DAILY_CAP


def decision_cap(records: list[dict] | None = None,
                 now: datetime | None = None) -> int:
    """Today's PURSUE cap — throttled down when recent approval dips below
    the floor. This is what originate() passes to rank_and_cap, so the
    board tightens itself when the humans stop approving what it surfaces."""
    return acceptance(records=records, now=now)["cap"]
