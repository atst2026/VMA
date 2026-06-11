"""Lead outcome capture — the calibration instrument.

The two-axis engine's weights, tiers and half-lives are hand-set defaults: a
well-built hypothesis about what predicts a won mandate that has not yet met a
real one. This module records the only thing that can settle that — called /
converted / dead per lead — together with a snapshot of the engine's score at
the moment of the outcome, so Stages 3-4 can correlate which signals and
weights actually predicted conversion.

Until ~50 outcomes exist the surface flags scores as provisional (see
calibration()); the weights earn the right to drop that flag only once the
outcomes say they should.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from tool.state_paths import state_dir

STATE_DIR = state_dir()
# The AD-room ladder: what actually happened to the call. The legacy
# coarse values (called/converted/dead) stay accepted so old records and
# callers keep working; new UI writes the fine-grained rungs.
OUTCOMES = ("no_answer", "wrong_buyer", "conversation", "meeting",
            "brief", "placement",
            "called", "converted", "dead")
# Rungs that count as a conversion for calibration purposes.
CONVERTED = {"meeting", "brief", "placement", "converted"}
CALIBRATION_TARGET = 50


def _file():
    return state_dir() / "lead_outcomes.json"


def _load() -> dict:
    try:
        f = _file()
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d, indent=2, default=str))
    except Exception:
        pass


def record(lead_id: str, outcome: str, snapshot: dict | None = None) -> bool:
    """Record (or clear, with outcome='') an outcome for a lead, stamping the
    engine snapshot so the score that led to the call is preserved for
    calibration. Returns True on a valid write."""
    lead_id = (lead_id or "").strip()
    outcome = (outcome or "").strip().lower()
    if not lead_id or outcome not in OUTCOMES + ("",):
        return False
    d = _load()
    now = datetime.now(timezone.utc).isoformat()
    if outcome == "":
        d.pop(lead_id, None)
    else:
        entry = d.get(lead_id) or {"history": []}
        entry["outcome"] = outcome
        entry["outcome_at"] = now
        if snapshot:
            entry["snapshot"] = snapshot
        entry.setdefault("history", []).append({"outcome": outcome, "at": now})
        d[lead_id] = entry
    _save(d)
    return True


def get(lead_id: str) -> str | None:
    return (_load().get(lead_id) or {}).get("outcome")


def get_all() -> dict:
    """{lead_id: outcome} for leads that have one."""
    return {k: v.get("outcome") for k, v in _load().items() if v.get("outcome")}


def calibration() -> dict:
    """How far through calibration we are. `calibrating` stays True (scores
    flagged provisional) until CALIBRATION_TARGET outcomes are logged."""
    d = _load()
    logged = sum(1 for v in d.values() if v.get("outcome"))
    converted = sum(1 for v in d.values() if v.get("outcome") in CONVERTED)
    return {"logged": logged, "target": CALIBRATION_TARGET,
            "converted": converted, "calibrating": logged < CALIBRATION_TARGET}


def outcome_report() -> dict:
    """The monthly weight-review input: outcomes aggregated against the
    engine snapshot that produced each call. Answers 'did high scores
    actually convert?' per outcome rung, per score band, per tier and per
    fee-propensity reading — the raw material /learn reviews before
    proposing weight changes. Pure read; never raises."""
    try:
        d = _load()
        by_outcome: dict = {}
        by_band: dict = {}
        by_tier: dict = {}
        by_prop: dict = {}
        for v in d.values():
            o = v.get("outcome")
            if not o:
                continue
            conv = o in CONVERTED
            snap = v.get("snapshot") or {}
            by_outcome[o] = by_outcome.get(o, 0) + 1
            score = snap.get("score")
            if isinstance(score, (int, float)):
                band = ("70+" if score >= 70 else
                        "45-69" if score >= 45 else "<45")
                b = by_band.setdefault(band, {"n": 0, "converted": 0})
                b["n"] += 1
                b["converted"] += 1 if conv else 0
            for key, agg in (("tier", by_tier), ("prop", by_prop)):
                k = snap.get(key)
                if k:
                    e = agg.setdefault(str(k), {"n": 0, "converted": 0})
                    e["n"] += 1
                    e["converted"] += 1 if conv else 0
        return {"by_outcome": by_outcome, "by_score_band": by_band,
                "by_tier": by_tier, "by_propensity": by_prop,
                **calibration()}
    except Exception:
        return {"by_outcome": {}, "by_score_band": {}, "by_tier": {},
                "by_propensity": {}}
