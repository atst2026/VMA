#!/usr/bin/env python3
"""Cross-reference pasted Recruiter output against today's morning brief.

Usage:
    cat recruiter_export.csv | python3 -m tool.analyse
    # or
    python3 -m tool.analyse <<'EOF'
    Alice Smith, Head of Comms, Unilever
    Bob Jones, Corporate Affairs Director, Diageo
    ...
    EOF

Returns JSON with each parsed row + which flagged companies and recent signals
touch each person. Claude's /analyse slash command runs this, then synthesises
the "call these 5 first" output.
"""
from __future__ import annotations
import json
import logging
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool.config import EXCLUDE_TITLE_TERMS, ROLE_KEYWORDS


log = logging.getLogger("analyse")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

STATE_DIR = _REPO_ROOT / "tool" / "state"

_ROLE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in ROLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_STRONG_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in [
        "head of internal communications", "head of corporate communications",
        "head of communications", "chief communications officer",
        "corporate affairs director", "communications director", "pr director",
    ]) + r")\b",
    re.IGNORECASE,
)
_EXCLUDE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in EXCLUDE_TITLE_TERMS) + r")\b",
    re.IGNORECASE,
)


def load_latest_signals() -> list[dict]:
    path = STATE_DIR / "latest_signals.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def parse_paste(raw: str) -> list[dict]:
    """Parse pasted Recruiter output. Accepts three shapes:
      1. CSV with columns (first row = header including 'name'/'title'/'company')
      2. Tab-separated lines
      3. Comma-separated free text lines like 'Name, Title, Company'
    Best-effort — Claude will see the parsed rows and can clean up any mess.
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return []

    rows: list[dict] = []

    # Heuristic: if first line looks like a CSV header
    first = lines[0].lower()
    header_candidate = any(col in first for col in ("name", "title", "company", "employer", "position"))

    if header_candidate and ("," in lines[0] or "\t" in lines[0]):
        sep = "\t" if "\t" in lines[0] else ","
        cols = [c.strip().lower() for c in lines[0].split(sep)]
        def _col_idx(*candidates: str) -> int | None:
            for c in candidates:
                if c in cols:
                    return cols.index(c)
            return None
        name_i = _col_idx("name", "full name", "candidate", "first name last name")
        title_i = _col_idx("title", "position", "role", "current title")
        company_i = _col_idx("company", "employer", "organisation", "organization", "current company")
        for ln in lines[1:]:
            parts = [p.strip() for p in ln.split(sep)]
            if len(parts) < 2:
                continue
            rows.append({
                "name":    parts[name_i]    if name_i is not None and name_i < len(parts) else "",
                "title":   parts[title_i]   if title_i is not None and title_i < len(parts) else "",
                "company": parts[company_i] if company_i is not None and company_i < len(parts) else "",
                "raw":     ln,
            })
        return rows

    # Fallback: split each line by comma or ' — ' or tabs; first field = name
    for ln in lines:
        parts = [p.strip() for p in re.split(r"[,\t]|\s—\s|\s-\s", ln) if p.strip()]
        if not parts:
            continue
        rows.append({
            "name":    parts[0] if len(parts) > 0 else "",
            "title":   parts[1] if len(parts) > 1 else "",
            "company": parts[2] if len(parts) > 2 else "",
            "raw":     ln,
        })
    return rows


def fit_score(row: dict) -> float:
    """Title-match against Sara's role taxonomy → rough 'fit' score 0–1.
    Word-boundary aware ('cco' matches 'CCO' not 'aCCOunt').
    Excluded titles (agency/sales) score 0.
    """
    title = row.get("title") or ""
    if not title:
        return 0.0
    # Agency/sales titles score 0 — Sara doesn't work these
    if _EXCLUDE_RE.search(title):
        return 0.0
    if _STRONG_RE.search(title):
        return 1.0
    if _ROLE_RE.search(title):
        return 0.6
    return 0.0


def cross_reference(rows: list[dict], signals: list[dict]) -> list[dict]:
    """For each row, list signal IDs whose company matches the row's company."""
    enriched = []
    for row in rows:
        comp = (row.get("company") or "").strip().lower()
        hits = []
        if comp:
            for s in signals:
                sc = (s.get("company") or "").strip().lower()
                st = (s.get("title") or "").lower()
                if not sc and not st:
                    continue
                if (sc and (comp in sc or sc in comp)) or (st and comp in st):
                    hits.append({
                        "title": s.get("title", ""),
                        "source": s.get("source", ""),
                        "url": s.get("url", ""),
                        "published": s.get("published", ""),
                    })
        enriched.append({
            **row,
            "fit": fit_score(row),
            "signal_hits": hits,
            "signal_hit_count": len(hits),
        })
    return enriched


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("No input on stdin. Paste Recruiter output and pipe in.", file=sys.stderr)
        return 2
    rows = parse_paste(raw)
    signals = load_latest_signals()
    enriched = cross_reference(rows, signals)
    # Sort: fit × (1 + 0.3 × hits) descending
    enriched.sort(key=lambda r: r["fit"] * (1 + 0.3 * r["signal_hit_count"]), reverse=True)
    print(json.dumps({
        "input_rows": len(rows),
        "signals_compared_against": len(signals),
        "enriched": enriched,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
