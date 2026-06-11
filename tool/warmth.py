"""Warm-route tags — the relationship layer scraping can't see.

The AD room's verdict: a warm route to the buyer (direct, or one
credible hop — including "we placed someone in this team") converts at
a different order of magnitude from a scraped name, so warmth alone
earns BUYER 2/2 and the full 15 strength points. Warmth lives in
people's heads and VMA's CRM, not in public data — so this store is
written by the AD (one click on the card) and, later, by a one-off CRM
import. Every record carries a `source` field (manual | imported) so
that bulk import needs no schema change.

Shape: {company_key: {"warm": bool, "type": str, "note": str,
                      "source": "manual"|"imported", "at": iso}}
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from tool.state_paths import state_dir


def _file():
    return state_dir() / "warmth.json"


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _load() -> dict:
    try:
        f = _file()
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, indent=1, sort_keys=True))


def set_warm(company: str | None, warm: bool = True, *,
             rel_type: str = "", note: str = "",
             source: str = "manual") -> bool:
    """Tag (or untag, warm=False) a company as having a warm route.
    Returns True on a valid write. Never raises."""
    try:
        key = _norm(company)
        if not key:
            return False
        d = _load()
        if not warm:
            d.pop(key, None)
        else:
            d[key] = {"company": (company or "").strip(), "warm": True,
                      "type": (rel_type or "").strip()[:80],
                      "note": (note or "").strip()[:300],
                      "source": ("imported" if source == "imported"
                                 else "manual"),
                      "at": datetime.now(timezone.utc).isoformat()}
        _save(d)
        return True
    except Exception:
        return False


def get(company: str | None) -> dict | None:
    """The warmth record for a company, or None."""
    return _load().get(_norm(company))


def annotate(item: dict) -> dict:
    """Project the warm-route flag onto a pipeline row as the
    `warm_route` field the engine and gate read. Never raises."""
    try:
        rec = get((item or {}).get("company"))
        if rec:
            item["warm_route"] = rec
    except Exception:
        pass
    return item
