"""Advisory Opus overlays — the bridge between an Opus pass and the gate.

The deterministic gate (`tool.advisory_gate`) always runs and is the £0
default. When an Opus pass runs — the Advisory Conviction Verdict + the
Outside-In Function Diagnostic (ADVISORY_ENGINE.md §5) — it writes one
small overlay per lead here, and the gate reads it and lets the reasoned
verdict OVERRIDE the deterministic one (mirroring how `tool.investigations`
overlays outrank the hiring gate). The overlay is written EITHER by:

  * `/advisory-brief`'s deep pass — the model is Claude Code itself, free
    under the subscription (the primary, zero-spend path); or
  * `tool.advisory_llm` — an optional API-backed pass, OFF by default.

One file per (trigger, company) lead id under state/advisory_overlays/.
Overlays expire after EXPIRE_DAYS so a stale verdict can't keep presenting
a company whose facts have moved on. Never raises.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from tool.state_paths import state_dir

log = logging.getLogger("brief.advisory.overlay")

EXPIRE_DAYS = 21
VALID_VERDICTS = {"KILL", "DEVELOP", "PURSUE"}


def lead_id(company: str, trigger: str) -> str:
    """Stable per-lead id: trigger + company, slugified."""
    raw = f"{(trigger or '').strip()}:{(company or '').strip().lower()}"
    return "".join(c if c.isalnum() or c in "-_:" else "-" for c in raw)[:120]


def _dir() -> Path:
    return Path(str(state_dir())) / "advisory_overlays"


def write(company: str, trigger: str, verdict: str, *,
          conviction: int | None = None, named_pain: str = "",
          economic_buyer: str = "", recommended_service: str = "",
          sharpest_insight: str = "", diagnostic: str = "",
          kill_reasons: list[str] | None = None,
          confidence: str = "", source: str = "opus") -> bool:
    """Persist one Opus verdict + diagnostic for a lead. Returns False on
    bad input; never raises on IO failure."""
    if verdict not in VALID_VERDICTS or not (company or trigger):
        return False
    payload = {
        "lead_id": lead_id(company, trigger),
        "company": company or "", "trigger": trigger or "",
        "verdict": verdict, "source": source or "opus",
        "date": datetime.now(timezone.utc).isoformat(),
        "named_pain": str(named_pain or "")[:400],
        "economic_buyer": str(economic_buyer or "")[:200],
        "recommended_service": str(recommended_service or "")[:120],
        "sharpest_insight": str(sharpest_insight or "")[:600],
        "diagnostic": str(diagnostic or "")[:2400],
        "kill_reasons": [str(k)[:200] for k in (kill_reasons or []) if k][:5],
        "confidence": str(confidence or "")[:20],
    }
    if conviction is not None:
        try:
            payload["conviction"] = max(0, min(100, int(conviction)))
        except (TypeError, ValueError):
            pass
    try:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{payload['lead_id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception as e:
        log.info("advisory overlay write failed (%s)", e)
        return False


def _fresh(data: dict, now: datetime) -> bool:
    try:
        dt = datetime.fromisoformat((data.get("date") or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).days <= EXPIRE_DAYS
    except Exception:
        return False


def get(company: str, trigger: str, now: datetime | None = None) -> dict | None:
    """The current (non-expired) overlay for a lead, or None."""
    now = now or datetime.now(timezone.utc)
    try:
        path = _dir() / f"{lead_id(company, trigger)}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if (isinstance(data, dict) and data.get("verdict") in VALID_VERDICTS
                and _fresh(data, now)):
            return data
    except Exception as e:
        log.info("advisory overlay read failed (%s)", e)
    return None


def get_all(now: datetime | None = None) -> dict[str, dict]:
    """{lead_id: overlay} for all current overlays. Never raises."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    d = _dir()
    if not d.is_dir():
        return out
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if (isinstance(data, dict) and data.get("verdict") in VALID_VERDICTS
                    and _fresh(data, now)):
                out[data.get("lead_id") or f.stem] = data
        except Exception:
            continue
    return out
