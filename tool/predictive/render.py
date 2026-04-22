"""Render the 'Pre-advert signals' email section.

Inserted between the top-5 live call list and the full signal set. Only
rendered if there's at least one ranked predictive stack.
"""
from __future__ import annotations
import html
from datetime import datetime

from tool.predictive import patterns as P
from tool.predictive.stacker import Stack


_ESC = html.escape


def _date(dt: datetime) -> str:
    try:
        return dt.strftime("%-d %b %Y")
    except Exception:
        return dt.isoformat()


def _stack_descriptor(stk: Stack) -> str:
    labels = sorted({_ESC(e.trigger_label) for e in stk.events})
    joined = " + ".join(labels)
    return f"stacked ({joined})" if stk.depth >= 2 else f"single ({labels[0]})"


def _window(stk: Stack) -> str:
    """Pick the narrowest window across the stack's triggers. Stacked
    signals usually imply a narrower, earlier window."""
    mins, maxs = [], []
    for e in stk.events:
        t = P.BY_KEY.get(e.trigger_key)
        if t is not None:
            mins.append(t.lead_time_weeks[0])
            maxs.append(t.lead_time_weeks[1])
        elif e.trigger_key == "job_ad_cluster":
            mins.append(4); maxs.append(12)
    if not mins:
        return "—"
    lo, hi = min(mins), min(maxs) if stk.depth >= 2 else max(maxs)
    if hi < lo:
        hi = lo + 4
    return f"{lo}–{hi} weeks"


def _who_to_call(stk: Stack) -> str:
    """Prefer the most senior contact mentioned across triggers."""
    order = ["ceo_change", "mna", "chair_change", "regulator_action",
             "chro_change", "restructure", "job_ad_cluster"]
    for key in order:
        for e in stk.events:
            if e.trigger_key == key:
                t = P.BY_KEY.get(key)
                if t is not None:
                    return t.who_to_call
                if key == "job_ad_cluster":
                    return "HR Director"
    return "HR Director"


def _implication(stk: Stack) -> str:
    """Use the strongest trigger's implication template."""
    order = ["ceo_change", "mna", "chair_change", "regulator_action",
             "chro_change", "restructure", "job_ad_cluster"]
    for key in order:
        for e in stk.events:
            if e.trigger_key == key:
                if key == "job_ad_cluster":
                    return (f"Job-ad cluster pattern at {stk.company} — "
                            f"~60% base rate for a senior hire within 90 days.")
                t = P.BY_KEY.get(key)
                if t:
                    return t.implication.format(company=stk.company)
    return ""


def _sources_line(stk: Stack) -> str:
    rows = []
    for e in sorted(stk.events, key=lambda x: x.published, reverse=True)[:4]:
        label = _ESC(e.source_label or "source")
        date = _date(e.published)
        if e.url:
            rows.append(f"<a href=\"{_ESC(e.url)}\">{label} {date}</a>")
        else:
            rows.append(f"{label} {date}")
    return " · ".join(rows)


def render_html(ranked: list[tuple[Stack, float]], limit: int = 5) -> str:
    if not ranked:
        return ""
    top = ranked[:limit]
    blocks = []
    for i, (stk, sc) in enumerate(top, 1):
        implication = _ESC(_implication(stk))
        window = _ESC(_window(stk))
        who = _ESC(_who_to_call(stk))
        descriptor = _stack_descriptor(stk)
        evidence_lines = []
        for e in sorted(stk.events, key=lambda x: x.published, reverse=True)[:3]:
            evidence_lines.append(
                f"<div style='font-size:12px;color:#555;margin-left:4px;'>"
                f"• {_ESC(e.trigger_label)}: {_ESC(e.evidence)}"
                f"</div>"
            )
        blocks.append(f"""
        <div style="padding:12px 0;border-bottom:1px solid #e5e5e5;">
            <div style="font-weight:600;font-size:15px;">
                {i}. {_ESC(stk.company)} · {descriptor}
            </div>
            <div style="color:#111;font-size:13px;margin-top:6px;">
                {implication}
            </div>
            {''.join(evidence_lines)}
            <div style="color:#111;font-size:13px;margin-top:8px;">
                <strong>Window:</strong> {window} &nbsp;·&nbsp;
                <strong>Call:</strong> {who}
            </div>
            <div style="margin-top:6px;font-size:12px;">
                {_sources_line(stk)}
            </div>
        </div>
        """)
    return f"""
<h3 style="margin:24px 0 4px 0;">Pre-advert signals — comms hire likely within 3–9 months</h3>
<div style="color:#666;font-size:12px;margin-bottom:8px;">
  Upstream triggers that empirically precede senior comms hires. Every item has a named public source.
  Probabilities are trigger-class base rates, not calibrated per-company predictions.
</div>
{''.join(blocks)}
"""


def render_text(ranked: list[tuple[Stack, float]], limit: int = 5) -> str:
    if not ranked:
        return ""
    lines = [
        "Pre-advert signals — comms hire likely within 3–9 months",
        "—" * 56,
    ]
    for i, (stk, sc) in enumerate(ranked[:limit], 1):
        lines.append(f"{i}. {stk.company} · {_stack_descriptor(stk)}")
        lines.append(f"   {_implication(stk)}")
        for e in sorted(stk.events, key=lambda x: x.published, reverse=True)[:3]:
            lines.append(f"     • {e.trigger_label}: {e.evidence}")
        lines.append(f"   Window: {_window(stk)}   Call: {_who_to_call(stk)}")
        for e in sorted(stk.events, key=lambda x: x.published, reverse=True)[:4]:
            lines.append(f"     {e.source_label} {_date(e.published)}  {e.url}")
        lines.append("")
    return "\n".join(lines)
