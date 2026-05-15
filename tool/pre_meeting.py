"""Pre-meeting brief: one email per morning summarising prep for every
meeting today with an external attendee at a watchlist company.

Runs at ~05:00 UTC daily so the brief lands by 7am London. For each
meeting it pulls:

  1. Calendar context (time, attendees, location)
  2. Leadership-team context from hiring_contacts.json (so Sara knows
     who at the company is named in our table, and who reports to whom)
  3. Recent press coverage filtered to that company (from latest_signals
     and latest_predictive written by the most recent morning brief)
  4. Recent comms-related signals at the company (predictor triggers)
  5. Strategic-priority quotes from the company's annual report (via
     the existing annual_report.py extraction)
  6. Three suggested conversation hooks built from those signals

LinkedIn-post scraping is a placeholder until Bright Data is configured
(see the note rendered in section 1 of each brief).
"""
from __future__ import annotations
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tool import config
from tool.email_send import send as email_send
from tool.sources.calendar_ical import meetings_for_date, Meeting
from tool.sources.company_domains import company_for_email, company_for_title_text

log = logging.getLogger("brief.pre_meeting")
log.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

STATE_DIR = Path(__file__).resolve().parent / "state"


# ---- Brief assembly per meeting ---------------------------------------
@dataclass
class MeetingBrief:
    meeting: Meeting
    company: str                       # "" if no watchlist company matched
    contact_summary: list[str] = field(default_factory=list)
    recent_signals: list[dict] = field(default_factory=list)
    recent_predictors: list[dict] = field(default_factory=list)
    annual_quotes: list[str] = field(default_factory=list)
    conversation_hooks: list[str] = field(default_factory=list)


def _identify_company(meeting: Meeting) -> str:
    """Try attendee email domains first, then title/description fallback."""
    for email in meeting.external_attendees:
        c = company_for_email(email)
        if c:
            return c
    # Fallback: scan the event title + description for a watchlist name
    text = f"{meeting.summary} {meeting.description} {meeting.location}"
    return company_for_title_text(text) or ""


def _load_contacts_for_company(company: str) -> list[str]:
    """Return a short list of 'Role: Name (last confirmed YYYY-MM-DD)'
    strings from hiring_contacts.json for that company. Empty list if no
    card exists. Stale entries are flagged inline."""
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
    """Filter the morning brief's latest_signals.json down to entries
    for this company. Tolerant of empty/missing files (returns [])."""
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
    """Filter the morning brief's latest_predictive.json down to active
    predictors for this company."""
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


def _load_annual_quotes_for_company(company: str, limit: int = 2) -> list[str]:
    """Pull strategic quotes via the existing annual_report pipeline.
    Caches per-company resolution within a run to avoid re-fetching."""
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


def _build_conversation_hooks(brief: MeetingBrief) -> list[str]:
    """Heuristic generator. Walk signals + predictors + annual quotes and
    propose 3 specific opening lines. Falls back to a generic warm opener
    if no signals are available for the company."""
    hooks: list[str] = []

    # 1. Predictor-driven hook (most timely signal)
    for p in brief.recent_predictors[:1]:
        events = p.get("events") or []
        if not events:
            continue
        trigger = events[0].get("trigger_label") or events[0].get("trigger_key") or "recent activity"
        window = p.get("window_label") or "soon"
        hooks.append(
            f"On the {trigger.lower()} signal: our internal model gives this a "
            f"{p.get('probability', '?')}% probability of resolving in {window}. "
            f"Worth asking how they're thinking about backup capacity."
        )
        break

    # 2. Recent press / signals hook
    for s in brief.recent_signals[:1]:
        title = (s.get("title") or "").strip()
        kind = s.get("kind", "")
        if not title:
            continue
        if kind == "leadership_change":
            hooks.append(f"Open warmly on the recent appointment: \"{title[:120]}\" - "
                         f"good lead-in to ask how the new arrival is shaping their priorities.")
        elif kind in ("rns", "filing", "regulator"):
            hooks.append(f"Reference the recent disclosure: \"{title[:120]}\" - "
                         f"natural pivot to how it affects their comms/IR rhythm.")
        else:
            hooks.append(f"News hook: \"{title[:120]}\" - check whether it shifts any "
                         f"of their stated priorities.")
        break

    # 3. Annual-report hook (their own stated strategy)
    for q in brief.annual_quotes[:1]:
        snippet = q.strip()
        if len(snippet) > 180:
            snippet = snippet[:177] + "..."
        hooks.append(f"Quote-back their own strategy: \"{snippet}\" - "
                     f"ask how that translates into headcount priorities this year.")
        break

    # Fallback if we couldn't generate three
    while len(hooks) < 3:
        if not brief.recent_signals and not brief.recent_predictors:
            hooks.append("No fresh public signals at this employer - open on the "
                         "wider sector pattern (see the morning brief's predictor pipeline).")
        else:
            hooks.append("Listen for cues about their team's bandwidth and immediate "
                         "priorities - the data above is signal, not script.")
    return hooks[:3]


def build_brief_for_meeting(meeting: Meeting) -> MeetingBrief:
    """Top-level: gather all context for one meeting."""
    company = _identify_company(meeting)
    brief = MeetingBrief(meeting=meeting, company=company)
    if not company:
        # Still return a basic brief - calendar + attendees, no enrichment
        return brief
    brief.contact_summary = _load_contacts_for_company(company)
    brief.recent_signals = _load_recent_signals_for_company(company)
    brief.recent_predictors = _load_recent_predictors_for_company(company)
    brief.annual_quotes = _load_annual_quotes_for_company(company)
    brief.conversation_hooks = _build_conversation_hooks(brief)
    return brief


# ---- Rendering --------------------------------------------------------
def _esc(s: str | None) -> str:
    import html
    return html.escape(s or "", quote=True)


def _format_time(dt: datetime) -> str:
    """Render in London time for the email body."""
    try:
        from zoneinfo import ZoneInfo
        local = dt.astimezone(ZoneInfo("Europe/London"))
    except Exception:
        local = dt
    return local.strftime("%H:%M")


def render_html(briefs: list[MeetingBrief], for_date: date) -> str:
    if not briefs:
        return (f"<p>No external meetings on {for_date.strftime('%A %d %B %Y')}.</p>")

    sections = []
    for i, b in enumerate(briefs, 1):
        m = b.meeting
        time_str = _format_time(m.start)
        title = _esc(m.summary or "(no title)")
        company_line = (
            f"<strong>{_esc(b.company)}</strong>" if b.company
            else "<span style='color:#888;'>No watchlist match - see attendees below</span>"
        )
        attendee_line = ", ".join(_esc(a) for a in m.external_attendees) or "(no external attendees parsed)"
        location_line = _esc(m.location) if m.location else ""

        # Contacts block
        if b.contact_summary:
            contacts_html = "<ul style='margin:6px 0;padding-left:20px;'>" + "".join(
                f"<li>{_esc(c)}</li>" for c in b.contact_summary
            ) + "</ul>"
        else:
            contacts_html = "<div style='color:#888;'>(No leadership contacts seeded for this employer)</div>"

        # Predictors
        if b.recent_predictors:
            pred_html = "<ul style='margin:6px 0;padding-left:20px;'>"
            for p in b.recent_predictors:
                window = _esc(p.get("window_label") or "")
                prob = p.get("probability") or "?"
                events = p.get("events") or []
                first_evidence = (events[0].get("evidence") if events else "") or ""
                trigger_label = (events[0].get("trigger_label") if events else "") or "predictor"
                pred_html += (
                    f"<li><strong>{_esc(trigger_label)}</strong> "
                    f"(prob {prob}% · window {window}): "
                    f"<span style='color:#444;'>{_esc(first_evidence[:200])}</span></li>"
                )
            pred_html += "</ul>"
        else:
            pred_html = "<div style='color:#888;'>(No active predictor signals at this employer)</div>"

        # Signals
        if b.recent_signals:
            sig_html = "<ul style='margin:6px 0;padding-left:20px;'>"
            for s in b.recent_signals:
                title_s = _esc(s.get("title") or "")
                url = _esc(s.get("url") or "#")
                src = _esc(s.get("source") or "")
                sig_html += (
                    f"<li><a href='{url}' style='color:#0366d6;'>{title_s}</a> "
                    f"<span style='color:#888;font-size:12px;'>· {src}</span></li>"
                )
            sig_html += "</ul>"
        else:
            sig_html = "<div style='color:#888;'>(No recent public news/RNS for this employer in this morning's scour)</div>"

        # Annual-report quotes
        if b.annual_quotes:
            quotes_html = "<ul style='margin:6px 0;padding-left:20px;'>" + "".join(
                f"<li style='margin-bottom:8px;'><em style='color:#222;'>&ldquo;{_esc(q)}&rdquo;</em></li>"
                for q in b.annual_quotes
            ) + "</ul>"
        else:
            quotes_html = "<div style='color:#888;'>(Annual report not parsed - non-UK-registered, "
            quotes_html += "abbreviated filing, or scanned PDF)</div>"

        # Conversation hooks
        if b.conversation_hooks:
            hooks_html = "<ol style='margin:6px 0;padding-left:22px;'>" + "".join(
                f"<li style='margin-bottom:6px;'>{_esc(h)}</li>" for h in b.conversation_hooks
            ) + "</ol>"
        else:
            hooks_html = "<div style='color:#888;'>(No hooks generated - check the signals above)</div>"

        sections.append(f"""
        <div style="padding:14px 0;border-bottom:1px solid #e5e5e5;">
          <div style="font-weight:600;font-size:15px;">
            {i}. {time_str} &middot; {title}
          </div>
          <div style="color:#444;font-size:13px;margin-top:4px;">
            {company_line}{(' &middot; ' + location_line) if location_line else ''}
          </div>
          <div style="color:#666;font-size:12px;margin-top:2px;">
            Attendees: {attendee_line}
          </div>

          <div style="margin-top:10px;font-size:13px;">
            <strong>Leadership context</strong>
            {contacts_html}
          </div>

          <div style="margin-top:10px;font-size:13px;">
            <strong>Active predictor signals</strong>
            {pred_html}
          </div>

          <div style="margin-top:10px;font-size:13px;">
            <strong>Recent press / disclosures</strong>
            {sig_html}
          </div>

          <div style="margin-top:10px;font-size:13px;">
            <strong>Their stated strategic priorities</strong> (from latest annual report)
            {quotes_html}
          </div>

          <div style="margin-top:10px;font-size:13px;background:#f8f8fa;padding:10px;border-left:3px solid #0366d6;">
            <strong>Three conversation hooks for this meeting</strong>
            {hooks_html}
          </div>

          <div style="margin-top:8px;font-size:11px;color:#999;">
            LinkedIn posts will appear here once Bright Data is enabled. Until
            then, glance at the contact's public profile manually before the call.
          </div>
        </div>
        """)

    body = "".join(sections)
    when_str = for_date.strftime("%A %d %B %Y")
    return f"""<!doctype html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:760px;margin:0 auto;padding:20px;color:#111;">
<h2 style="margin:0 0 4px 0;">Pre-meeting prep &middot; {when_str}</h2>
<div style="color:#666;font-size:13px;margin-bottom:18px;">
  {len(briefs)} meetings with external attendees today. Context pulled from this
  morning's brief, the seeded contacts table, and the target company's annual
  report. Conversation hooks are heuristic suggestions - not a script.
</div>
{body}
<hr style="margin:28px 0;border:none;border-top:1px solid #ddd;">
<div style="color:#888;font-size:12px;">
  Generated by the VMA Group recruitment intelligence dashboard.
  Sources: Google/Microsoft calendar iCal feed &middot; Companies House &middot;
  GDELT &middot; LSE RNS &middot; UK regulator feeds &middot; seeded contacts table.
</div>
</body></html>
"""


def render_text(briefs: list[MeetingBrief], for_date: date) -> str:
    """Plain-text fallback for email clients that don't render HTML."""
    if not briefs:
        return f"No external meetings on {for_date.strftime('%A %d %B %Y')}."
    lines = [f"Pre-meeting prep · {for_date.strftime('%A %d %B %Y')}", ""]
    for i, b in enumerate(briefs, 1):
        m = b.meeting
        lines.append(f"{i}. {_format_time(m.start)} · {m.summary or '(no title)'}")
        lines.append(f"   Company: {b.company or '(no watchlist match)'}")
        lines.append(f"   Attendees: {', '.join(m.external_attendees) or '(none parsed)'}")
        if b.contact_summary:
            lines.append("   Leadership:")
            for c in b.contact_summary:
                lines.append(f"     - {c}")
        if b.recent_predictors:
            lines.append("   Active predictors:")
            for p in b.recent_predictors:
                lines.append(f"     - {(p.get('events') or [{}])[0].get('trigger_label', 'predictor')} "
                              f"(prob {p.get('probability', '?')}%)")
        if b.recent_signals:
            lines.append("   Recent press:")
            for s in b.recent_signals[:3]:
                lines.append(f"     - {s.get('title', '')[:100]}")
        if b.annual_quotes:
            lines.append("   Their stated priorities:")
            for q in b.annual_quotes:
                lines.append(f"     \"{q[:200]}\"")
        if b.conversation_hooks:
            lines.append("   Conversation hooks:")
            for h in b.conversation_hooks:
                lines.append(f"     * {h}")
        lines.append("")
    return "\n".join(lines)


# ---- Entry point ------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    mode = (argv[0] if argv else "preview").lower()
    if mode not in ("send", "test", "preview"):
        print(f"Usage: python -m tool.pre_meeting [send|test|preview]", file=sys.stderr)
        return 2

    today = datetime.now(timezone.utc).astimezone().date()
    # Allow override for testing: VMA_BRIEF_DATE=2026-05-15
    override = os.environ.get("VMA_BRIEF_DATE", "").strip()
    if override:
        try:
            today = datetime.strptime(override, "%Y-%m-%d").date()
        except ValueError:
            log.warning("Bad VMA_BRIEF_DATE %r - using today", override)

    log.info("Pre-meeting brief for %s (mode=%s)", today, mode)
    meetings = meetings_for_date(today)
    if not meetings:
        log.info("No meetings today - skipping email send")
        return 0

    briefs = [build_brief_for_meeting(m) for m in meetings]
    # Skip the email if no meeting matched a watchlist company AND no
    # external attendees - that means nothing useful to brief on.
    if not any(b.company or b.meeting.external_attendees for b in briefs):
        log.info("No meetings with external attendees or watchlist matches - skipping email")
        return 0

    html_out = render_html(briefs, today)
    text_out = render_text(briefs, today)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    (STATE_DIR / f"pre_meeting_{today.isoformat()}_{stamp}.html").write_text(html_out)
    (STATE_DIR / f"pre_meeting_{today.isoformat()}_{stamp}.txt").write_text(text_out)

    if mode == "preview":
        print(text_out)
        return 0

    to = config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT
    subject = f"Pre-meeting prep · {today.strftime('%a %d %b')} · {len(briefs)} meeting(s)"
    if mode == "test":
        subject = "[TEST] " + subject
    # Silent confirmation channel on send-mode (same as pitch_pack/reverse_match)
    bcc = [config.TEST_RECIPIENT] if mode == "send" else None
    result = email_send(to, subject, html_out, text_out, bcc=bcc)
    log.info("Send: %s", result)
    if not result.get("ok"):
        print("\n--- EMAIL SEND FAILED ---")
        print(result)
        return 2
    print(f"✓ Sent to {to}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
