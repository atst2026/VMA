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


def window_for_stack(stk: Stack) -> tuple[int, int] | None:
    """Pick the narrowest predicted-hire window across the stack's triggers.
    Stacked signals usually imply a narrower, earlier window."""
    mins, maxs = [], []
    for e in stk.events:
        t = P.BY_KEY.get(e.trigger_key)
        if t is not None:
            mins.append(t.lead_time_weeks[0])
            maxs.append(t.lead_time_weeks[1])
        elif e.trigger_key == "job_ad_cluster":
            mins.append(4); maxs.append(12)
    if not mins:
        return None
    lo, hi = min(mins), min(maxs) if stk.depth >= 2 else max(maxs)
    if hi < lo:
        hi = lo + 4
    return lo, hi


def _window(stk: Stack) -> str:
    w = window_for_stack(stk)
    return f"{w[0]}–{w[1]} weeks" if w else "-"


def _who_to_call(stk: Stack) -> str:
    """Prefer the most senior contact mentioned across triggers."""
    order = ["comms_leader_departure", "ic_platform_rfp", "ipo_listing",
             "ceo_change", "mna", "regulator_action", "regulator_probe_early",
             "crisis_event", "contract_loss",
             "chair_change", "cfo_change", "ir_director_change",
             "chro_change", "restructure", "press_velocity_spike",
             "job_ad_cluster"]
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
    order = ["comms_leader_departure", "ic_platform_rfp", "ipo_listing",
             "ceo_change", "mna", "regulator_action", "regulator_probe_early",
             "crisis_event", "contract_loss",
             "chair_change", "cfo_change", "ir_director_change",
             "chro_change", "restructure", "press_velocity_spike",
             "job_ad_cluster"]
    for key in order:
        for e in stk.events:
            if e.trigger_key == key:
                if key == "job_ad_cluster":
                    return (f"Job-ad cluster pattern at {stk.company}: "
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


DASHBOARD_URL = "https://vma-dashboard.onrender.com/"


def render_html(ranked: list[tuple[Stack, float]], limit: int = 5,
                new_count: int | None = None,
                total_active: int | None = None) -> str:
    """Render the pre-advert signals section.

    `ranked` is the daily DELTA (new predictors first-seen today). The
    full active pipeline lives on the dashboard; we just show the new
    items here plus a one-line link to the pipeline.
    """
    # No new items today, but pipeline has active items → tiny stub with link
    if not ranked and total_active:
        return f"""
<h3 style="margin:24px 0 4px 0;">Pre-advert signals</h3>
<div style="color:#666;font-size:13px;margin-bottom:8px;">
  No new predictors today.
  <a href="{DASHBOARD_URL}">{total_active} active in your pipeline →</a>
</div>
"""
    if not ranked:
        return ""

    meta_line = ""
    if total_active is not None and new_count is not None:
        meta_line = (
            f"<div style='color:#888;font-size:12px;margin:4px 0 8px 0;'>"
            f"{new_count} new today · "
            f"<a href='{DASHBOARD_URL}' style='color:#888;'>"
            f"{total_active} active in your pipeline →</a>"
            f"</div>"
        )

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
<h3 style="margin:24px 0 4px 0;">Pre-advert signals · new today</h3>
<div style="color:#666;font-size:12px;margin-bottom:8px;">
  Upstream triggers that empirically precede senior comms hires. Every item has a named public source.
  Probabilities are trigger-class base rates, not calibrated per-company predictions.
</div>
{meta_line}
{''.join(blocks)}
"""


def render_text(ranked: list[tuple[Stack, float]], limit: int = 5,
                new_count: int | None = None,
                total_active: int | None = None) -> str:
    if not ranked and total_active:
        return (
            "Pre-advert signals\n"
            "-" * 56 + "\n"
            f"No new predictors today. {total_active} active in your pipeline:\n"
            f"  {DASHBOARD_URL}\n"
        )
    if not ranked:
        return ""
    lines = [
        "Pre-advert signals · new today",
        "-" * 56,
    ]
    if total_active is not None and new_count is not None:
        lines.append(f"  {new_count} new today · {total_active} active in pipeline: {DASHBOARD_URL}")
        lines.append("")
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
