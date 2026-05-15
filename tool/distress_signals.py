"""Distress-signal detection.

In a dead market, comms hires shift away from growth (new function, new
geography, new product launch) toward distress (crisis comms, IR after
share-price shock, restructuring comms, regulatory-investigation comms,
M&A under duress). The morning-brief ranker is biased toward growth
triggers. This module surfaces the distress side using a regex taxonomy
defined in tool.config.DISTRESS_SIGNALS.

It runs as a filter on top of latest_signals.json (no extra API calls),
so a new distress dashboard panel can be populated from whatever the
last scour already returned.
"""
from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Iterable

from tool.config import DISTRESS_SIGNALS

log = logging.getLogger("brief.distress")

STATE_DIR = Path(__file__).resolve().parent / "state"


# Compile once at import.
_COMPILED: list[tuple[re.Pattern, str, float]] = [
    (re.compile(pat, re.IGNORECASE), cat, w) for pat, cat, w in DISTRESS_SIGNALS
]


def classify(text: str) -> list[dict]:
    """Return all distress categories that fire on `text`. Each dict:
    {category, weight, matched_phrase}. Empty list if none."""
    if not text:
        return []
    hits = []
    seen_cats: set[str] = set()
    for rx, cat, w in _COMPILED:
        m = rx.search(text)
        if not m:
            continue
        if cat in seen_cats:
            # First hit per category is enough; avoids duplicates from
            # near-synonym patterns ("profit warning" + "issues a profit
            # warning") double-counting the same event.
            continue
        seen_cats.add(cat)
        hits.append({
            "category":       cat,
            "weight":         w,
            "matched_phrase": m.group(0).strip(),
        })
    hits.sort(key=lambda h: h["weight"], reverse=True)
    return hits


def signal_is_distress(signal: dict) -> tuple[bool, list[dict]]:
    """Apply classify() across title + summary of one signal. Returns
    (is_distress, hits). Defensive against non-string field values
    (some upstream feeds return numbers or lists for title)."""
    parts: list[str] = []
    for field in ("title", "summary"):
        v = signal.get(field)
        if v is None:
            continue
        parts.append(v if isinstance(v, str) else str(v))
    haystack = " ".join(p for p in parts if p)
    hits = classify(haystack)
    return bool(hits), hits


def _safe_weight(v) -> float:
    """Coerce a signal's `weight` field to float. Returns 1.0 on any
    failure (missing, None, non-numeric string, etc.)."""
    if v is None:
        return 1.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 1.0


def filter_distress(signals: Iterable[dict]) -> list[dict]:
    """Return a list of signals that contain at least one distress hit,
    each annotated with `_distress` = the hit dicts and a derived
    `_distress_score` = max weight * source weight (if any).

    Sorted by distress score descending so the most urgent hires float
    to the top of the dashboard panel.
    """
    annotated: list[dict] = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        is_d, hits = signal_is_distress(s)
        if not is_d:
            continue
        copy = dict(s)
        copy["_distress"] = hits
        copy["_distress_score"] = max(h["weight"] for h in hits) * _safe_weight(s.get("weight"))
        # Primary category is the highest-weight hit. Used by the
        # dashboard for the colour-coded label badge.
        copy["_distress_category"] = hits[0]["category"]
        annotated.append(copy)
    annotated.sort(key=lambda s: s["_distress_score"], reverse=True)
    return annotated


def load_distress_signals(limit: int = 30) -> list[dict]:
    """Read latest_signals.json and return the distress subset, ready
    for the dashboard. No external API calls."""
    path = STATE_DIR / "latest_signals.json"
    if not path.exists():
        log.info("latest_signals.json missing — distress panel empty")
        return []
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        log.info("latest_signals.json parse failed: %s", e)
        return []
    if not isinstance(data, list):
        return []
    return filter_distress(data)[:limit]


# Human-readable category labels for the dashboard badges.
CATEGORY_LABELS = {
    "profit_warning":       "Profit warning",
    "guidance_cut":         "Guidance cut",
    "ratings":              "Ratings downgrade",
    "activist":             "Activist investor",
    "regulatory_probe":     "Regulatory probe",
    "restructuring":        "Restructuring / redundancies",
    "ceo_exit_under_cloud": "CEO exit (under cloud)",
    "m_and_a_distress":     "M&A under duress",
    "share_price_shock":    "Share-price shock",
    "crisis":               "Crisis (cyber / litigation / suspension)",
}


def category_label(category: str | None) -> str:
    if not category:
        return ""
    return CATEGORY_LABELS.get(category, category)
