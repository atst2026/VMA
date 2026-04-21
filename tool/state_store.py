"""Tiny JSON dedup store so Sara doesn't see the same signal twice across runs."""
from __future__ import annotations
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
SEEN_FILE = STATE_DIR / "seen.json"
TTL_DAYS = 14


def _load() -> dict:
    if not SEEN_FILE.exists():
        return {}
    try:
        return json.loads(SEEN_FILE.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    SEEN_FILE.write_text(json.dumps(data, indent=0))


def filter_unseen(signals: list[dict]) -> list[dict]:
    seen = _load()
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for s in signals:
        sid = s.get("id", "")
        if not sid:
            continue
        if sid in seen:
            continue
        seen[sid] = now
        out.append(s)
    # TTL prune
    cutoff = datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)
    seen = {k: v for k, v in seen.items()
            if _parse(v) > cutoff}
    _save(seen)
    return out


def _parse(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.now(timezone.utc)
