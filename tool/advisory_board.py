"""Render the advisory lane as a board — the bridge to any surface.

`originate()` returns verdict rows; this turns them into a presentable
board (the "visible advisory lane" of ADVISORY_ENGINE.md Phase 1) without
touching the 8.7k-line dashboard. Plain text / light markdown, grouped
Call-ready (PURSUE) → Developing → Killed, each row carrying its owner,
the one-line why, and the gate scorecard chips. Consumed today by
`/advisory-brief`; the morning advisory pulse and the dashboard lane render
the same rows later. Pure; never raises.
"""
from __future__ import annotations

from datetime import date

_HEADINGS = [("PURSUE", "Call-ready"), ("DEVELOP", "Developing"),
             ("KILL", "Killed")]


def _chips(q: dict) -> str:
    if not q:
        return ""
    return ("PAIN%s SPONSOR%s MANDATE%s TIMING%s ACCESS%s PROOF%s (%s/12)" % (
        q.get("pain"), q.get("sponsor"), q.get("mandate"), q.get("timing"),
        q.get("access"), q.get("proof"), q.get("total")))


def _owner_line(row: dict) -> str:
    o = row.get("owner") or {}
    if not o.get("owner"):
        return ""
    line = f"owner: {o['owner']}"
    a = o.get("associate")
    if a:
        line += f" · delivery {a['name']} ({a['firm']})"
    if o.get("co_owner"):
        line += f" · + {o['co_owner']}"
    return line


def render_board(rows: list[dict], today: date | None = None,
                 cap: int | None = None) -> str:
    """The advisory board as text. Groups by verdict, ranks by conviction
    within each group, and shows owner + why + gate chips per row."""
    rows = rows or []
    today = today or date.today()
    out = [f"# Advisory board — {today.isoformat()}"]
    n_pursue = sum(1 for r in rows if r.get("verdict") == "PURSUE")
    cap_note = f" (PURSUE cap {cap})" if cap is not None else ""
    out.append(f"{len(rows)} leads · {n_pursue} call-ready{cap_note}")

    by_verdict: dict[str, list[dict]] = {}
    for r in rows:
        by_verdict.setdefault(r.get("verdict", "DEVELOP"), []).append(r)

    for verdict, label in _HEADINGS:
        group = by_verdict.get(verdict) or []
        if not group:
            continue
        group.sort(key=lambda r: -int(r.get("conviction", 0)))
        out.append(f"\n## {label} ({len(group)})")
        for r in group:
            company = r.get("company") or (r.get("signal") or {}).get("company", "")
            out.append(f"\n[{int(r.get('conviction', 0)):3d}] {company} — "
                       f"{r.get('trigger', '')}")
            if r.get("why"):
                out.append(f"   {r['why']}")
            ol = _owner_line(r)
            if ol:
                out.append(f"   {ol}")
            chips = _chips(r.get("qual") or {})
            if chips:
                out.append(f"   gate: {chips}")
    if len(out) == 2:
        out.append("\nNo advisory leads today (no statutory window open, or "
                   "the GPG index is not yet populated).")
    return "\n".join(out)
