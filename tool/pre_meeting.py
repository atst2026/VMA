"""Pre-meeting brief: on-demand prep pack for one client meeting.

Triggered from the dashboard (or CLI) with an account name + optional
contact + optional meeting context. Builds a one-pager with:

  1. Leadership-team context from hiring_contacts.json (named CEO, CFO,
     CHRO, CCO, GC at the account, with stale entries flagged)
  2. Recent press / RNS / regulator signals at that account (from the
     most recent morning-brief artefact)
  3. Active predictor signals at the account
  4. Strategic-priority quotes from the account's annual report
     (re-uses tool/annual_report.py)
  5. Three suggested conversation hooks built heuristically from above

Usage (CLI):
    python -m tool.pre_meeting "Severn Trent" send
    python -m tool.pre_meeting "Severn Trent" send "Carla Sherry" "10am Mon"

Usage (dashboard):
    /api/dispatch/pre-meeting POST  -> triggers workflow_dispatch on
    pre-meeting-brief.yml with the four inputs.
"""
from __future__ import annotations
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tool import config
from tool.email_send import send as email_send

log = logging.getLogger("brief.pre_meeting")
log.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

STATE_DIR = Path(__file__).resolve().parent / "state"


@dataclass
class PrepBrief:
    account: str
    contact_name: str = ""
    meeting_context: str = ""
    contact_summary: list[str] = field(default_factory=list)
    recent_signals: list[dict] = field(default_factory=list)
    recent_predictors: list[dict] = field(default_factory=list)
    annual_quotes: list[str] = field(default_factory=list)
    conversation_hooks: list[str] = field(default_factory=list)


def _load_contacts_for_company(company: str) -> list[str]:
    """Return 'Role: Name' strings from hiring_contacts.json for that
    company. Stale entries flagged inline. Empty list if no card."""
    try:
        from tool.contacts.store import load_contacts, get_contact
        from tool.contacts.routing import display_title_for_slot
    except Exception:
        return []
    contacts = load_contacts()
    card = get_contact(contacts, company)
    if card is None:
        return []
    out = []
    now = datetime.now(timezone.utc)
    for slot, entry in card.entries.items():
        fresh = entry.is_fresh(as_of=now)
        flag = "" if fresh else " · STALE - verify before referencing"
        out.append(f"{display_title_for_slot(slot)}: {entry.name}{flag}")
    return out


def _load_recent_signals_for_company(company: str, limit: int = 5) -> list[dict]:
    path = STATE_DIR / "latest_signals.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    matched = [
        s for s in data
        if isinstance(s, dict)
        and (s.get("company") or "").strip().lower() == company.lower()
    ]
    matched.sort(key=lambda s: s.get("published", ""), reverse=True)
    return matched[:limit]


def _load_recent_predictors_for_company(company: str, limit: int = 3) -> list[dict]:
    path = STATE_DIR / "latest_predictive.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    matched = [
        p for p in data
        if isinstance(p, dict)
        and (p.get("company") or "").strip().lower() == company.lower()
        and p.get("status") != "dismissed"
    ]
    matched.sort(key=lambda p: float(p.get("score") or 0), reverse=True)
    return matched[:limit]


def _load_annual_quotes_for_company(company: str, limit: int = 3) -> list[str]:
    try:
        from tool.sources.companies_house import resolve_company_number
        from tool.annual_report import fetch_strategic_quotes
    except Exception as e:
        log.info("annual_report pipeline unavailable: %s", e)
        return []
    number = resolve_company_number(company)
    if not number:
        return []
    try:
        report = fetch_strategic_quotes(number, top_n=limit)
    except Exception as e:
        log.info("annual_report extraction failed for %s: %s", company, e)
        return []
    if not report or not report.quotes:
        return []
    return [q.text for q in report.quotes[:limit]]


def _build_conversation_hooks(brief: PrepBrief) -> list[str]:
    """One predictor-driven hook, one news-driven hook, one quote-back
    hook from the annual report. Fills in with a generic-but-honest
    fallback if any tier produced nothing."""
    hooks: list[str] = []

    for p in brief.recent_predictors[:1]:
        events = p.get("events") or []
        if not events:
            continue
        trigger = events[0].get("trigger_label") or events[0].get("trigger_key") or "recent activity"
        window = p.get("window_label") or "soon"
        prob = p.get("probability", "?")
        hooks.append(
            f"Predictor angle: the {trigger.lower()} signal at {brief.account} "
            f"resolves to a {prob}% probability of comms hire within {window}. "
            f"Ask how they're thinking about backup capacity."
        )
        break

    for s in brief.recent_signals[:1]:
        title = (s.get("title") or "").strip()
        kind = s.get("kind", "")
        if not title:
            continue
        if kind == "leadership_change":
            hooks.append(
                f"News angle: \"{title[:140]}\" - "
                f"lead with curiosity about how the new arrival is shaping priorities."
            )
        elif kind in ("rns", "filing", "regulator"):
            hooks.append(
                f"News angle: their recent disclosure \"{title[:140]}\" - "
                f"pivot to how it affects their comms / IR rhythm."
            )
        else:
            hooks.append(
                f"News angle: \"{title[:140]}\" - check whether it shifts any "
                f"of their stated priorities this year."
            )
        break

    for q in brief.annual_quotes[:1]:
        snippet = q.strip()
        if len(snippet) > 200:
            snippet = snippet[:197] + "..."
        hooks.append(
            f"Quote-back angle: their own annual report says \"{snippet}\" - "
            f"ask how that translates into headcount priorities."
        )
        break

    while len(hooks) < 3:
        if not (brief.recent_signals or brief.recent_predictors or brief.annual_quotes):
            hooks.append(
                f"No fresh public signals at {brief.account} - open on the wider "
                f"sector pattern (see the morning brief's predictor pipeline) and "
                f"ask what's keeping them up at night this quarter."
            )
        else:
            hooks.append(
                "Listen for cues about bandwidth and immediate hiring priorities - "
                "the data above is signal, not script."
            )
    return hooks[:3]


def build_brief(account: str, contact_name: str = "", meeting_context: str = "") -> PrepBrief:
    brief = PrepBrief(
        account=account.strip(),
        contact_name=contact_name.strip(),
        meeting_context=meeting_context.strip(),
    )
    brief.contact_summary = _load_contacts_for_company(brief.account)
    brief.recent_signals = _load_recent_signals_for_company(brief.account)
    brief.recent_predictors = _load_recent_predictors_for_company(brief.account)
    brief.annual_quotes = _load_annual_quotes_for_company(brief.account)
    brief.conversation_hooks = _build_conversation_hooks(brief)
    return brief


def _esc(s: str | None) -> str:
    import html
    return html.escape(s or "", quote=True)


def render_html(brief: PrepBrief) -> str:
    contact_line = ""
    if brief.contact_name:
        contact_line = f"<strong>Meeting:</strong> {_esc(brief.contact_name)}"
        if brief.meeting_context:
            contact_line += f" &middot; {_esc(brief.meeting_context)}"
    elif brief.meeting_context:
        contact_line = f"<strong>Context:</strong> {_esc(brief.meeting_context)}"

    if brief.contact_summary:
        contacts_html = "<ul style='margin:6px 0;padding-left:20px;'>" + "".join(
            f"<li>{_esc(c)}</li>" for c in brief.contact_summary
        ) + "</ul>"
    else:
        contacts_html = "<div style='color:#888;'>No leadership contacts seeded for this account.</div>"

    if brief.recent_predictors:
        pred_html = "<ul style='margin:6px 0;padding-left:20px;'>"
        for p in brief.recent_predictors:
            events = p.get("events") or []
            trigger_label = (events[0].get("trigger_label") if events else "") or "predictor"
            prob = p.get("probability") or "?"
            window = p.get("window_label") or ""
            first_evidence = (events[0].get("evidence") if events else "") or ""
            pred_html += (
                f"<li><strong>{_esc(trigger_label)}</strong> "
                f"(prob {prob}% &middot; window {_esc(window)}): "
                f"<span style='color:#444;'>{_esc(first_evidence[:240])}</span></li>"
            )
        pred_html += "</ul>"
    else:
        pred_html = "<div style='color:#888;'>No active predictor signals at this account.</div>"

    if brief.recent_signals:
        sig_html = "<ul style='margin:6px 0;padding-left:20px;'>"
        for s in brief.recent_signals:
            title_s = _esc(s.get("title") or "")
            url = _esc(s.get("url") or "#")
            src = _esc(s.get("source") or "")
            sig_html += (
                f"<li><a href='{url}' style='color:#0366d6;'>{title_s}</a> "
                f"<span style='color:#888;font-size:12px;'>&middot; {src}</span></li>"
            )
        sig_html += "</ul>"
    else:
        sig_html = "<div style='color:#888;'>No recent public news/RNS for this account in the latest scour.</div>"

    if brief.annual_quotes:
        quotes_html = "<ul style='margin:6px 0;padding-left:20px;'>" + "".join(
            f"<li style='margin-bottom:8px;'><em style='color:#222;'>&ldquo;{_esc(q)}&rdquo;</em></li>"
            for q in brief.annual_quotes
        ) + "</ul>"
    else:
        quotes_html = ("<div style='color:#888;'>Annual report quotes not extracted "
                       "(non-UK-registered, abbreviated filing, or scanned PDF).</div>")

    hooks_html = "<ol style='margin:6px 0;padding-left:22px;'>" + "".join(
        f"<li style='margin-bottom:6px;'>{_esc(h)}</li>" for h in brief.conversation_hooks
    ) + "</ol>"

    return f"""<!doctype html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:760px;margin:0 auto;padding:20px;color:#111;">
<h2 style="margin:0 0 4px 0;">Pre-meeting prep &middot; {_esc(brief.account)}</h2>
<div style="color:#666;font-size:13px;margin-bottom:18px;">
  {contact_line or 'On-demand prep pack generated from the dashboard.'}
</div>

<div style="margin-top:14px;font-size:14px;">
  <h3 style="margin:14px 0 4px 0;">1. Leadership context</h3>
  {contacts_html}
</div>

<div style="margin-top:14px;font-size:14px;">
  <h3 style="margin:14px 0 4px 0;">2. Active predictor signals</h3>
  {pred_html}
</div>

<div style="margin-top:14px;font-size:14px;">
  <h3 style="margin:14px 0 4px 0;">3. Recent press &amp; disclosures</h3>
  {sig_html}
</div>

<div style="margin-top:14px;font-size:14px;">
  <h3 style="margin:14px 0 4px 0;">4. Their stated strategic priorities</h3>
  <div style="color:#666;font-size:12px;margin-bottom:4px;">From the company's most recent annual report.</div>
  {quotes_html}
</div>

<div style="margin-top:16px;font-size:14px;background:#f6f8fa;padding:14px;border-left:3px solid #0366d6;">
  <h3 style="margin:0 0 6px 0;">5. Three conversation hooks</h3>
  {hooks_html}
</div>

<hr style="margin:28px 0;border:none;border-top:1px solid #ddd;">
<div style="color:#888;font-size:12px;">
  Generated by the VMA Group recruitment intelligence dashboard.
  Sources: Companies House &middot; GDELT &middot; LSE RNS &middot; UK regulator feeds &middot;
  seeded contacts table &middot; annual-report extraction.
</div>
</body></html>
"""


def render_text(brief: PrepBrief) -> str:
    lines = [f"Pre-meeting prep · {brief.account}"]
    if brief.contact_name:
        suffix = f" · {brief.meeting_context}" if brief.meeting_context else ""
        lines.append(f"Meeting: {brief.contact_name}{suffix}")
    elif brief.meeting_context:
        lines.append(f"Context: {brief.meeting_context}")
    lines.append("")

    lines.append("1. LEADERSHIP CONTEXT")
    if brief.contact_summary:
        for c in brief.contact_summary:
            lines.append(f"   - {c}")
    else:
        lines.append("   (no leadership contacts seeded)")
    lines.append("")

    lines.append("2. ACTIVE PREDICTOR SIGNALS")
    if brief.recent_predictors:
        for p in brief.recent_predictors:
            events = p.get("events") or []
            label = (events[0].get("trigger_label") if events else "predictor")
            lines.append(f"   - {label} (prob {p.get('probability', '?')}% · "
                          f"window {p.get('window_label', '')})")
    else:
        lines.append("   (none active)")
    lines.append("")

    lines.append("3. RECENT PRESS & DISCLOSURES")
    if brief.recent_signals:
        for s in brief.recent_signals[:5]:
            lines.append(f"   - {(s.get('title') or '')[:120]}")
    else:
        lines.append("   (no recent matches)")
    lines.append("")

    lines.append("4. THEIR STATED STRATEGIC PRIORITIES (annual report)")
    if brief.annual_quotes:
        for q in brief.annual_quotes:
            lines.append(f'   "{q[:240]}"')
    else:
        lines.append("   (extraction unavailable for this entity)")
    lines.append("")

    lines.append("5. THREE CONVERSATION HOOKS")
    for h in brief.conversation_hooks:
        lines.append(f"   * {h}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m tool.pre_meeting <account> <mode> [contact] [context]"""
    argv = argv or sys.argv[1:]
    if len(argv) < 1:
        print('Usage: python -m tool.pre_meeting "<account>" [send|test|preview] '
              '["<contact name>"] ["<meeting context>"]', file=sys.stderr)
        return 2
    account = argv[0].strip()
    mode = (argv[1] if len(argv) > 1 else "preview").lower()
    contact = argv[2].strip() if len(argv) > 2 else ""
    context_str = argv[3].strip() if len(argv) > 3 else ""

    if mode not in ("send", "test", "preview"):
        print(f"Unknown mode {mode!r}", file=sys.stderr)
        return 2

    log.info("Building pre-meeting brief for %r · contact=%r · context=%r · mode=%s",
             account, contact, context_str, mode)
    brief = build_brief(account, contact, context_str)

    html_out = render_html(brief)
    text_out = render_text(brief)

    safe = "".join(c if c.isalnum() else "_" for c in account.lower())[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    (STATE_DIR / f"pre_meeting_{safe}_{stamp}.html").write_text(html_out)
    (STATE_DIR / f"pre_meeting_{safe}_{stamp}.txt").write_text(text_out)

    if mode == "preview":
        print(text_out)
        return 0

    to = config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT
    subject = f"[Pre-meeting] {account}"
    if contact:
        subject += f" - {contact}"
    if mode == "test":
        subject = "[TEST] " + subject
    result = email_send(to, subject, html_out, text_out)
    log.info("Send: %s", result)
    if not result.get("ok"):
        print("\n--- EMAIL SEND FAILED ---")
        print(result)
        return 2
    print(f"✓ Sent to {to}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
