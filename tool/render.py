"""HTML render for the morning brief email."""
from __future__ import annotations
import html
from datetime import datetime
from typing import Iterable

from tool.ranking import suggest_angle


def _esc(s: str | None) -> str:
    return html.escape(s or "", quote=True)


def render_html(ranked: list[dict], source_report: dict, now_str: str,
                covered_days: str, predictive_html: str = "") -> str:
    top5 = ranked[:5]
    rest = ranked[5:40]

    def _item_block(i: int, s: dict) -> str:
        angle = suggest_angle(s)
        company = _esc(s.get("company") or "")
        source = _esc(s.get("source") or "")
        title = _esc(s.get("title") or "")
        url = _esc(s.get("url") or "#")
        published = _esc(s.get("published") or "")
        geo = _esc(s.get("geo") or "")
        return f"""
        <div style="padding:12px 0;border-bottom:1px solid #e5e5e5;">
            <div style="font-weight:600;font-size:15px;">{i}. {title}</div>
            <div style="color:#555;font-size:13px;margin-top:3px;">
                {company or '—'} · {source} · {geo} {f'· {published}' if published else ''}
            </div>
            <div style="color:#111;font-size:13px;margin-top:6px;">
                <em>Angle:</em> {_esc(angle)}
            </div>
            <div style="margin-top:6px;font-size:12px;">
                <a href="{url}" style="color:#0366d6;">source →</a>
            </div>
        </div>
        """

    top_blocks = "".join(_item_block(i + 1, s) for i, s in enumerate(top5))
    rest_rows = "".join(
        f"<li style='margin:4px 0;'>"
        f"<a href='{_esc(s.get('url') or '#')}' style='color:#0366d6;'>{_esc(s.get('title') or '')}</a> "
        f"<span style='color:#666;font-size:12px;'>— {_esc(s.get('source') or '')} · {_esc(s.get('geo') or '')}</span>"
        f"</li>"
        for s in rest
    )

    sources_summary = ", ".join(
        f"{_esc(k)} ({v})"
        for k, v in sorted(source_report.items(), key=lambda kv: kv[1], reverse=True)
    )

    return f"""<!doctype html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:720px;margin:0 auto;padding:20px;color:#111;">
<h2 style="margin:0 0 4px 0;">Sara's Morning Brief — {_esc(now_str)}</h2>
<div style="color:#666;font-size:13px;margin-bottom:18px;">
  Coverage: {_esc(covered_days)}. Ranked by fee-value × signal strength. UK primary.
</div>

<h3 style="margin:16px 0 4px 0;">Call these 5 first</h3>
{top_blocks or '<div style="color:#555;">No ranked calls today — the full signal list is below.</div>'}

{predictive_html}

<h3 style="margin:24px 0 6px 0;">Full signal set ({len(ranked)} items)</h3>
<ul style="padding-left:20px;font-size:13px;color:#333;">
{rest_rows}
</ul>

<hr style="margin:28px 0;border:none;border-top:1px solid #ddd;">
<div style="color:#888;font-size:12px;">
  Sources queried today: {sources_summary}<br>
  Zero automation of Sara's LinkedIn account. Bright Data = licensed logged-off surface, separate dataset.<br>
  Claude found and prepared. Sara closes.
</div>
</body></html>
"""


def render_plaintext(ranked: list[dict], now_str: str, covered_days: str,
                     predictive_text: str = "") -> str:
    lines = [
        f"Sara's Morning Brief — {now_str}",
        f"Coverage: {covered_days}. Ranked by fee-value × signal strength. UK primary.",
        "",
        "Call these 5 first",
        "—" * 40,
    ]
    for i, s in enumerate(ranked[:5], 1):
        lines.append(f"{i}. {s.get('title','')}")
        lines.append(f"   {s.get('company','—')} · {s.get('source','')} · {s.get('geo','')}")
        lines.append(f"   Angle: {suggest_angle(s)}")
        lines.append(f"   {s.get('url','')}")
        lines.append("")
    if predictive_text:
        lines.append("")
        lines.append(predictive_text)
        lines.append("")
    lines.append(f"Full signal set ({len(ranked)} items):")
    for s in ranked[5:40]:
        lines.append(f"  · {s.get('title','')} — {s.get('source','')} [{s.get('geo','')}]")
        if s.get("url"):
            lines.append(f"    {s['url']}")
    return "\n".join(lines)
