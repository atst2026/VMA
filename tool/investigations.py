"""Investigation overlays — the bridge between /investigate runs and the gate.

A manual /investigate session (Claude Code, per-trigger playbook) ends by
writing one small JSON overlay per investigated lead. The presentation
gate reads these and they outrank every other rule:

    {"pid": "tesco", "verdict": "confirmed" | "killed" | "recheck",
     "note": "one-line reason", "date": "2026-06-10T09:00:00+00:00",
     "recheck_days": 7,                  # optional, for killed/recheck
     "evidence_added": ["url", ...]}     # optional, new sources found

confirmed -> presents at High confidence (subject only to hard blockers
             and window lapse); killed -> never presents, with the note
             shown in the queue; recheck -> stays queued until the date.

Overlays expire after EXPIRE_DAYS so a stale verdict can't keep
presenting (or suppressing) a company whose facts have moved on. One file
per lead id under state/investigations/ keeps writes trivially atomic and
the /investigate command's contract simple.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from tool.state_paths import state_dir

log = logging.getLogger("brief.investigations")

EXPIRE_DAYS = 21
VALID_VERDICTS = {"confirmed", "killed", "recheck"}


def _dir() -> Path:
    return Path(str(state_dir())) / "investigations"


def _slug(pid: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in (pid or ""))[:80]


def write_overlay(pid: str, verdict: str, note: str = "",
                  recheck_days: int | None = None,
                  evidence_added: list[str] | None = None,
                  *,
                  red_team: bool = False,
                  conviction: int | None = None,
                  business_case: str = "",
                  warm_opening: str = "",
                  economic_buyer: str = "",
                  champion_path: str = "",
                  kill_reasons: list[str] | None = None) -> bool:
    """Used by /investigate and /red-team. The conviction fields are the
    red-team run's typed verdict; the gate carries them onto the card.
    Returns False on bad input."""
    if verdict not in VALID_VERDICTS or not pid:
        return False
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    payload = {"pid": pid, "verdict": verdict, "note": note,
               "date": datetime.now(timezone.utc).isoformat(),
               "recheck_days": recheck_days,
               "evidence_added": evidence_added or []}
    if red_team:
        payload["red_team"] = True
        if conviction is not None:
            try:
                payload["conviction"] = max(0, min(100, int(conviction)))
            except (TypeError, ValueError):
                pass
        payload["business_case"] = str(business_case or "")[:600]
        payload["warm_opening"] = str(warm_opening or "")[:400]
        payload["economic_buyer"] = str(economic_buyer or "")[:200]
        payload["champion_path"] = str(champion_path or "")[:200]
        payload["kill_reasons"] = [str(k)[:200] for k in (kill_reasons or [])
                                   if k][:5]
    path = d / f"{_slug(pid)}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)
    return True


def get_all(now: datetime | None = None) -> dict[str, dict]:
    """{pid: overlay} for all current (non-expired) overlays. Never raises."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    d = _dir()
    if not d.is_dir():
        return out
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if not isinstance(data, dict):
                continue
            if data.get("verdict") not in VALID_VERDICTS:
                continue
            dt = datetime.fromisoformat((data.get("date") or "").replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if (now - dt).days > EXPIRE_DAYS:
                continue
            pid = data.get("pid") or f.stem
            out[pid] = data
        except Exception as e:
            log.info("investigation overlay %s unreadable (%s) — skipped",
                     f.name, e)
    return out
