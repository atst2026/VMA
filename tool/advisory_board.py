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


# ---------------------------------------------------------------------------
# HTML — the visible advisory lane in the live console (a self-contained
# page; an additive /advisory route renders it without touching the
# existing dashboard templates).
# ---------------------------------------------------------------------------
import html as _html

_VERDICT_CSS = {"PURSUE": "#1f9d55", "DEVELOP": "#c98a1b", "KILL": "#7a3b3b"}

_PAGE_CSS = """
:root{--bg:#0e1116;--panel:#161b22;--ink:#e6edf3;--mut:#8b949e;--line:#222b35}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 60px}
.top{display:flex;align-items:baseline;gap:16px;border-bottom:1px solid var(--line);
padding-bottom:14px;margin-bottom:8px}
.top h1{font-size:20px;margin:0;letter-spacing:.2px}
.nav{margin-left:auto;display:flex;gap:14px}.nav a{color:var(--mut);text-decoration:none}
.nav a:hover{color:var(--ink)}
.sub{color:var(--mut);margin:6px 0 22px}
.sec{margin:26px 0 8px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;
color:var(--mut)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin:10px 0}
.row1{display:flex;align-items:center;gap:10px}
.conv{font-weight:700;font-variant-numeric:tabular-nums;background:#0b0e13;
border:1px solid var(--line);border-radius:6px;padding:2px 8px;font-size:13px}
.co{font-weight:600}.trig{color:var(--mut);font-size:12px}
.pill{margin-left:auto;color:#fff;border-radius:20px;padding:2px 10px;font-size:11px;
font-weight:700;letter-spacing:.04em}
.why{margin:8px 0 4px;color:#cdd6e0}
.meta{color:var(--mut);font-size:12px;margin-top:6px}
.chips{font-variant-numeric:tabular-nums}
details{margin-top:10px;border-top:1px dashed var(--line);padding-top:8px}
summary{cursor:pointer;color:#9db4d0;font-size:12px}
.pack h4{margin:12px 0 2px;font-size:12px;color:var(--mut);text-transform:uppercase;
letter-spacing:.08em}.pack p{margin:2px 0}
.empty{background:var(--panel);border:1px dashed var(--line);border-radius:10px;
padding:22px;color:var(--mut);text-align:center}
"""


def _pack_html(row: dict) -> str:
    """The Evidence Pack for a PURSUE row, reconstructed + composed."""
    try:
        from tool.advisory_signals.base import AdvisorySignal
        from tool.evidence_pack import compose
        sig = AdvisorySignal(**{k: (tuple(v) if k == "window" and v else v)
                                for k, v in (row.get("signal") or {}).items()})
        p = compose(sig)
    except Exception:
        return ""
    nb = p.get("named_buyer", {})
    rs = p.get("recommended_service", {})
    e = _html.escape
    parts = [
        ("The Reframe", p.get("reframe", "")),
        ("Outside-In Diagnostic (hypothesis)", p.get("diagnostic", "")),
        ("Benchmarking Teaser", p.get("benchmark_teaser", "")),
        ("Buyer + Inferred Pain",
         f"{nb.get('buyer','')} — {nb.get('inferred_pain','')}"),
        ("Value Give-Away", p.get("value_give_away", "")),
        ("Recommended Service + Proof",
         f"{rs.get('service','')} · {rs.get('proof_anchor','')}"),
        ("Take-Control Ask", p.get("take_control_ask", "")),
    ]
    body = "".join(f"<h4>{e(t)}</h4><p>{e(str(v))}</p>" for t, v in parts if v)
    return (f"<details><summary>Evidence Pack</summary>"
            f"<div class='pack'>{body}</div></details>")


def _card_html(row: dict) -> str:
    e = _html.escape
    v = row.get("verdict", "DEVELOP")
    company = row.get("company") or (row.get("signal") or {}).get("company", "")
    q = row.get("qual") or {}
    chips = _chips(q)
    owner = _owner_line(row)
    opus = " · Opus" if row.get("opus") else ""
    pack = _pack_html(row) if v == "PURSUE" else ""
    return (
        f"<div class='card'>"
        f"<div class='row1'><span class='conv'>{int(row.get('conviction',0))}</span>"
        f"<span class='co'>{e(company)}</span>"
        f"<span class='trig'>{e(row.get('trigger',''))}{opus}</span>"
        f"<span class='pill' style='background:{_VERDICT_CSS.get(v,'#555')}'>{e(v)}</span>"
        f"</div>"
        f"<div class='why'>{e(row.get('why',''))}</div>"
        + (f"<div class='meta'>{e(owner)}</div>" if owner else "")
        + (f"<div class='meta chips'>gate: {e(chips)}</div>" if chips else "")
        + pack + "</div>")


def render_board_html(rows: list[dict], today: date | None = None,
                      cap: int | None = None, desk: str = "comms") -> str:
    """The advisory board as a standalone HTML page for the live console.
    Self-contained (inline CSS); safe to serve from an additive route."""
    rows = rows or []
    today = today or date.today()
    n_pursue = sum(1 for r in rows if r.get("verdict") == "PURSUE")
    cap_txt = f" · PURSUE cap {cap}" if cap is not None else ""

    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("verdict", "DEVELOP"), []).append(r)

    body = []
    for verdict, label in _HEADINGS:
        group = by.get(verdict) or []
        if not group:
            continue
        group.sort(key=lambda r: -int(r.get("conviction", 0)))
        body.append(f"<div class='sec'>{_html.escape(label)} ({len(group)})</div>")
        body.extend(_card_html(r) for r in group)
    if not body:
        body.append("<div class='empty'>No advisory leads today — no "
                    "statutory window open, or the gender-pay-gap index "
                    "isn't populated yet (the GOV.UK host needs to be on the "
                    "egress allowlist). The lane is live; data flows when a "
                    "detector fires.</div>")

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Advisory · VMA</title><style>{_PAGE_CSS}</style></head><body>"
        "<div class='wrap'><div class='top'>"
        "<h1>Advisory Engine</h1>"
        "<nav class='nav'>"
        "<a href='/comms'>Communications</a><a href='/marketing'>Marketing</a>"
        "<a href='/'>Engine</a></nav></div>"
        f"<div class='sub'>{today.isoformat()} · {len(rows)} leads · "
        f"{n_pursue} call-ready{cap_txt} · {_html.escape(desk)} desk</div>"
        + "".join(body) +
        "</div></body></html>")

