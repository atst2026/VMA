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

import re

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
    annual_quotes_reason: str = ""   # why quotes are empty, when they are
    annual_quotes_source: str = ""   # 'annual_report' | 'curated' | ''
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


def _company_word_match(target: str, *fields: str) -> bool:
    """True if `target` appears as a whole word (case-insensitive) in
    any of the given fields. Used instead of strict equality because
    morning-brief's signals carry various forms ('HSBC', 'HSBC Holdings
    PLC', 'HSBC Bank PLC') and matching them all requires a fuzzier
    rule than '=='. Word-boundary check prevents false positives like
    'BT' matching 'BPT' or 'BTW'."""
    if not target or not target.strip():
        return False
    pattern = re.compile(r"\b" + re.escape(target.strip()) + r"\b",
                         re.IGNORECASE)
    return any(pattern.search(f or "") for f in fields)


def _load_recent_signals_for_company(company: str, limit: int = 5) -> list[dict]:
    """Three sources, deduplicated:
      1. Morning brief's filtered signals (latest_signals.json) - high
         relevance (already filtered to comms / IC / corporate affairs)
         but narrow.
      2. Live Google News RSS query for the company - last 14 days of
         general news. Reliable, no API key, returns headlines for any
         company name. The primary live source.
      3. Live GDELT query for the company - secondary live source,
         catches international coverage Google News may down-rank.

    Without 2 + 3, the brief returned 'no recent press' for major PLCs
    that have news every day - because the filtered morning brief
    rarely surfaces non-comms-specific events at any single account.
    """
    matched: list[dict] = []
    seen_titles: set[str] = set()

    def _add(s: dict) -> None:
        title_key = (s.get("title") or "")[:120].lower().strip()
        if title_key and title_key not in seen_titles:
            seen_titles.add(title_key)
            matched.append(s)

    # Source 1: morning brief signals
    path = STATE_DIR / "latest_signals.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list):
                for s in data:
                    if not isinstance(s, dict):
                        continue
                    if _company_word_match(company, s.get("company", ""),
                                            s.get("title", "")):
                        _add(s)
        except Exception:
            pass

    # Source 2: live Google News RSS
    for art in _fetch_google_news_rss(company, max_articles=limit + 5):
        _add(art)

    # Source 3: live GDELT
    for art in _fetch_live_gdelt_news(company, days_back=14, max_articles=limit + 5):
        _add(art)

    matched.sort(key=lambda s: s.get("published", ""), reverse=True)
    return matched[:limit]


def _fetch_google_news_rss(company: str, max_articles: int = 10) -> list[dict]:
    """Live Google News RSS query for `company`. Reliable, no API key,
    returns recent headlines for any company name. Returns [] silently
    on network errors. UK-prefixed (hl=en-GB, gl=GB) to bias coverage
    to UK outlets - Sara's accounts are UK-centric."""
    try:
        from tool.sources._http import get
        from tool.sources._http import parse_rss
    except Exception as e:
        log.info("Google News RSS unavailable: %s", e)
        return []
    from urllib.parse import quote_plus
    q = quote_plus(f'"{company}"')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"
    r = get(url, timeout=15)
    if not r or r.status_code != 200 or not r.content:
        log.info("Google News RSS %s -> %s",
                 company[:40], r.status_code if r else "no-resp")
        return []
    try:
        items = parse_rss(r.content)
    except Exception as e:
        log.info("Google News RSS parse failed for %s: %s", company, e)
        return []
    out: list[dict] = []
    for it in items[:max_articles]:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        # Google News RSS titles are "<headline> - <source>"; split off source.
        source = "Google News"
        if " - " in title:
            head, _, tail = title.rpartition(" - ")
            if head and tail and len(tail) < 60:
                title = head.strip()
                source = tail.strip()
        out.append({
            "title": title,
            "url": it.get("link", ""),
            "source": source,
            "published": it.get("published", ""),
            "company": company,
            "kind": "news",
        })
    log.info("Google News RSS: %d articles for %s", len(out), company)
    return out


def _fetch_live_gdelt_news(company: str, days_back: int = 14,
                           max_articles: int = 10) -> list[dict]:
    """Live GDELT query for `company` over the last N days. Returns
    articles in the same dict shape as morning brief signals (kind,
    title, url, source, published, company) for downstream rendering.
    Silent failure on network errors - returns []."""
    try:
        import requests
        from tool.config import SOURCES
    except Exception as e:
        log.info("GDELT live query unavailable: %s", e)
        return []
    query = f'"{company}"'
    params = {
        "query": f"sourcelang:eng {query}",
        "mode": "ArtList",
        "format": "json",
        "timespan": f"{days_back}d",
        "maxrecords": max_articles,
        "sort": "datedesc",
    }
    try:
        r = requests.get(SOURCES["gdelt_doc"], params=params, timeout=20)
    except Exception as e:
        log.info("GDELT live fetch failed: %s", e)
        return []
    if not r or r.status_code != 200:
        log.info("GDELT live %s -> HTTP %s",
                 company[:40], r.status_code if r else "no-resp")
        return []
    try:
        articles = r.json().get("articles", []) or []
    except Exception:
        return []
    out = []
    for a in articles:
        title = (a.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "url": a.get("url", ""),
            "source": (a.get("domain") or a.get("sourcecountry") or "GDELT"),
            "published": a.get("seendate", ""),
            "company": company,
            "kind": "news",
        })
    log.info("GDELT live: %d articles for %s (last %dd)",
             len(out), company, days_back)
    return out


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
    # Same word-boundary match as signals. Predictors usually have a
    # clean `company` field set from the stacker's company name, but
    # accommodate variants ('HSBC' vs 'HSBC Holdings PLC') in case the
    # stacker output differs from the user's input form.
    matched = [
        p for p in data
        if isinstance(p, dict)
        and p.get("status") != "dismissed"
        and _company_word_match(company, p.get("company", ""))
    ]
    matched.sort(key=lambda p: float(p.get("score") or 0), reverse=True)
    return matched[:limit]


_TIER_A_PRIORITIES_CACHE: dict | None = None


def _load_curated_priorities(company: str) -> list[str]:
    """Look up `company` in the hand-curated tier-A strategic priorities
    cache. Returns [] if not found. Match is case- and whitespace-
    insensitive, and tolerant of suffixes like 'PLC' / 'Group plc' /
    'Holdings plc' so 'HSBC Holdings PLC' resolves to the 'HSBC' entry."""
    global _TIER_A_PRIORITIES_CACHE
    if _TIER_A_PRIORITIES_CACHE is None:
        path = STATE_DIR / "tier_a_strategic_priorities.json"
        try:
            _TIER_A_PRIORITIES_CACHE = json.loads(path.read_text())
        except Exception as e:
            log.info("tier_a_strategic_priorities load failed: %s", e)
            _TIER_A_PRIORITIES_CACHE = {}

    if not _TIER_A_PRIORITIES_CACHE:
        return []

    needle = company.strip().lower()
    if not needle:
        return []
    # Strip common public-company suffixes for fuzzy match.
    suffixes = (" holdings plc", " group plc", " plc", " group",
                " holdings", " ltd", " limited")
    for suf in suffixes:
        if needle.endswith(suf):
            needle_short = needle[: -len(suf)].strip()
            break
    else:
        needle_short = needle

    for key, value in _TIER_A_PRIORITIES_CACHE.items():
        if key.startswith("_"):
            continue
        k = key.strip().lower()
        if k == needle or k == needle_short:
            return list(value) if isinstance(value, list) else []
        # Also match if user typed a longer form like "HSBC UK"
        if needle.startswith(k) or needle_short == k:
            return list(value) if isinstance(value, list) else []
    return []


def _load_annual_quotes_for_company(company: str, limit: int = 3) -> tuple[list[str], str, str]:
    """Returns (quotes, reason_when_empty, source). source is one of:
      "annual_report" - live extraction from Companies House PDF
      "curated"       - hand-curated tier-A public-priorities cache
      ""              - empty-state (reason explains why)

    Three-tier fallback chain:
      1. Live Companies House annual-report PDF extraction (richest, but
         fails for huge PDFs / scanned filings / non-UK entities)
      2. Hand-curated tier_a_strategic_priorities.json - publicly stated
         priorities for the top 30 Tier-A accounts. Always populates
         Section 4 for any seeded account even when CH extraction fails.
      3. Honest empty-state explanation when neither above produces.
    """
    try:
        from tool.sources.companies_house import resolve_company_number
        from tool.annual_report import fetch_strategic_quotes
    except Exception as e:
        log.info("annual_report pipeline unavailable: %s", e)
        curated = _load_curated_priorities(company)
        if curated:
            return curated[:limit], "", "curated"
        return [], f"Annual report extraction module failed to load: {e}", ""

    number = resolve_company_number(company)
    if number:
        try:
            report = fetch_strategic_quotes(number, top_n=limit)
        except Exception as e:
            log.info("annual_report extraction failed for %s: %s", company, e)
            report = None
        if report and report.quotes:
            return [q.text for q in report.quotes[:limit]], "", "annual_report"

    curated = _load_curated_priorities(company)
    if curated:
        return curated[:limit], "", "curated"

    if not number:
        return [], (f"Companies House did not resolve {company!r} and no "
                    f"curated priorities are seeded for this account. "
                    f"Either it's not UK-registered, or it isn't yet on "
                    f"the Tier-A account list (tool/state/"
                    f"tier_a_strategic_priorities.json)."), ""
    return [], (f"Companies House resolved {company} to {number} but the "
                f"latest annual-report PDF couldn't be parsed (often the "
                f"case for very large filings), and {company} isn't yet "
                f"on the curated Tier-A priorities list."), ""


def _build_conversation_hooks(brief: PrepBrief) -> list[str]:
    """One predictor-driven hook, one news-driven hook, one quote-back
    hook from the annual report. Fills any missing slots with VARIED
    generic-but-useful angles so Sara never sees three identical
    fallback lines."""
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
        elif kind == "news":
            hooks.append(
                f"News angle: \"{title[:140]}\" - reference it in opening "
                f"to show you've been tracking them, then ask what it means "
                f"for the year ahead."
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
        if brief.annual_quotes_source == "annual_report":
            hooks.append(
                f"Quote-back angle: their own annual report says \"{snippet}\" - "
                f"ask how that translates into headcount priorities."
            )
        else:
            hooks.append(
                f"Priority angle: their published priorities include \"{snippet}\" - "
                f"ask how that translates into comms / IC / corporate-affairs "
                f"headcount priorities for the year."
            )
        break

    # Varied fallbacks if any of the three primary slots came up empty.
    # Three distinct angles so Sara never sees identical lines stacked.
    # Phrasing avoids claiming "no signals" since other slots may have
    # surfaced specific ones - these are angles, not gap-fillers.
    fallbacks = [
        f"Sector angle: ask whether they're seeing the same talent-flow "
        f"patterns across their sector - peer benchmarks are useful "
        f"comparators that often surface unstated hiring intent at "
        f"{brief.account}.",

        f"Discovery angle: 'what's the one comms capability gap you wish "
        f"you had more bandwidth for?' is a single open question that "
        f"often unlocks more than any prepared pitch.",

        f"Year-ahead angle: 'looking 6 months out, what would make your "
        f"comms function easier to run?' surfaces plans that haven't hit "
        f"any public channel yet.",
    ]
    fallback_idx = 0
    while len(hooks) < 3 and fallback_idx < len(fallbacks):
        hooks.append(fallbacks[fallback_idx])
        fallback_idx += 1
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
    brief.annual_quotes, brief.annual_quotes_reason, brief.annual_quotes_source = _load_annual_quotes_for_company(brief.account)
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
        reason = brief.annual_quotes_reason or "Strategic priorities not available."
        quotes_html = f"<div style='color:#888;'>{_esc(reason)}</div>"

    quotes_subtitle = {
        "annual_report": "Verbatim from the company's most recent annual report (Companies House).",
        "curated":       "Paraphrased from the company's publicly stated strategic priorities (latest annual report / investor materials).",
    }.get(brief.annual_quotes_source, "From public sources.")

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
  <div style="color:#666;font-size:12px;margin-bottom:4px;">{quotes_subtitle}</div>
  {quotes_html}
</div>

<div style="margin-top:16px;font-size:14px;background:#f6f8fa;padding:14px;border-left:3px solid #0366d6;">
  <h3 style="margin:0 0 6px 0;">5. Three conversation hooks</h3>
  {hooks_html}
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

    src_label = {
        "annual_report": "annual report",
        "curated":       "publicly stated priorities",
    }.get(brief.annual_quotes_source, "")
    header = "4. THEIR STATED STRATEGIC PRIORITIES"
    if src_label:
        header += f" ({src_label})"
    lines.append(header)
    if brief.annual_quotes:
        for q in brief.annual_quotes:
            lines.append(f'   "{q[:240]}"')
    else:
        lines.append(f"   {brief.annual_quotes_reason or '(extraction unavailable)'}")
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
