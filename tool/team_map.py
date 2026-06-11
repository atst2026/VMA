"""Living team maps — the senior comms/marketing roster per company.

Built from the same leadership-page fetches the Wayback diff already
makes (tool/sources/wayback.py): every time a company's live page is
read, the parsed (name, role) roster is folded in here — who is on the
team NOW, since when each name has been listed, and every joiner/leaver
observed — so "what does their current team look like?" is answered from
accumulated observation rather than a one-off page diff that only ever
reported departures.

State: <state_dir>/team_maps.json — path resolved per call so the desk
namespace (comms vs marketing) is honoured. Non-fatal everywhere; a
team-map failure can never cost a brief or a Wayback diff.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from tool.state_paths import state_dir

log = logging.getLogger("brief.team_map")

CHANGES_CAP = 60          # joiner/leaver entries kept per company


def _path() -> Path:
    return Path(str(state_dir())) / "team_maps.json"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text())
        if isinstance(d, dict) and isinstance(d.get("companies"), dict):
            return d
    except FileNotFoundError:
        pass
    except Exception as e:
        log.info("team map state unreadable (%s) — starting fresh", e)
    return {"version": 1, "companies": {}}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _key(company: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (company or "").lower()).strip("_")
    return s or "unknown"


def update_roster(company: str, url: str, roster: dict[str, str]) -> dict:
    """Fold a freshly parsed live roster ({name: role}) into the map.
    Returns {"joined": [...], "left": [...]}. The first observation of a
    company seeds the roster without recording joins (we didn't see them
    arrive). An empty parse against a non-empty stored roster is treated
    as a page failure (JS-rendered layout change) and skipped — never
    fabricate a mass exit. Never raises."""
    out = {"joined": [], "left": []}
    try:
        if not company:
            return out
        data = _load()
        companies = data["companies"]
        today = datetime.now(timezone.utc).date().isoformat()
        rec = companies.get(_key(company))
        if rec is None:
            companies[_key(company)] = {
                "company": company,
                "url": url,
                "as_of": today,
                "roster": {nm: {"role": role, "since": today}
                           for nm, role in (roster or {}).items()},
                "changes": [],
            }
            _save(data)
            return out
        stored: dict = rec.get("roster") or {}
        if not roster and stored:
            log.info("team map: %s parsed 0 leaders — keeping last roster", company)
            return out
        changes = rec.setdefault("changes", [])
        for nm, role in (roster or {}).items():
            if nm in stored:
                stored[nm]["role"] = role          # keep `since`, refresh role
            else:
                stored[nm] = {"role": role, "since": today}
                changes.append({"date": today, "person": nm,
                                "role": role, "change": "joined"})
                out["joined"].append(nm)
        for nm in [n for n in stored if n not in (roster or {})]:
            changes.append({"date": today, "person": nm,
                            "role": stored[nm].get("role") or "",
                            "change": "left"})
            out["left"].append(nm)
            stored.pop(nm, None)
        del changes[:-CHANGES_CAP]
        rec.update({"company": company, "url": url or rec.get("url") or "",
                    "as_of": today, "roster": stored})
        _save(data)
        return out
    except Exception as e:
        log.info("team map update skipped for %s (%s)", company, e)
        return out


def team(company: str) -> dict:
    """The current known roster: {name: {"role", "since"}}. Empty when the
    company has never had a parseable leadership page."""
    try:
        rec = _load()["companies"].get(_key(company)) or {}
        return dict(rec.get("roster") or {})
    except Exception:
        return {}


def changes(company: str, limit: int = 10) -> list[dict]:
    """Recent observed joiners/leavers, newest first."""
    try:
        rec = _load()["companies"].get(_key(company)) or {}
        ch = list(rec.get("changes") or [])
        ch.sort(key=lambda c: c.get("date") or "", reverse=True)
        return ch[:limit]
    except Exception:
        return []


def summary_lines(company: str) -> list[str]:
    """Markdown lines for the dossier's Team map section. Empty list when
    nothing is on file."""
    try:
        rec = _load()["companies"].get(_key(company)) or {}
    except Exception:
        return []
    roster = rec.get("roster") or {}
    if not roster and not rec.get("changes"):
        return []
    lines = [f"_As of {rec.get('as_of') or '?'} (leadership page)_", ""]
    for nm in sorted(roster):
        info = roster[nm] or {}
        since = f" (listed since {info['since']})" if info.get("since") else ""
        lines.append(f"- **{nm}** — {info.get('role') or 'senior role'}{since}")
    recent = changes(company, limit=6)
    if recent:
        lines += ["", "Recent changes:"]
        for c in recent:
            lines.append(f"- {c.get('date')}: {c.get('person')} "
                         f"({c.get('role') or 'senior role'}) {c.get('change')}")
    return lines
