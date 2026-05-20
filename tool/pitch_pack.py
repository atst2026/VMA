#!/usr/bin/env python3
"""Retained pitch-pack generator. Manual / on-demand only.

Sara fires this when she has a contingent brief she wants to flip to retained.
Tool assembles a one-pager she can paste into a pitch email or walk into a
meeting with.

Usage:
    python3 -m tool.pitch_pack "Unilever"
    python3 -m tool.pitch_pack "Unilever" send       # email Sara
    python3 -m tool.pitch_pack "Unilever" test       # email amirt12

Output sections:
  1. Account snapshot (Companies House)
  2. Why this role matters now (recent RNS/news/regulator hits)
  3. Cost of vacancy (template calc — Sara overrides salary if needed)
  4. Comparable employers (peer market map)
  5. Sector salary benchmark
  6. Candidate landscape (best-effort — where the candidates are)
  7. 6-week methodology with milestone-linked fees
"""
from __future__ import annotations
import html
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool import config
from tool import annual_report as ar
from tool.email_send import send as email_send
from tool.peers import peers_for, detect_sector
from tool.sources import companies_house, gdelt
from tool.sources._http import get

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("pitch_pack")

STATE_DIR = _REPO_ROOT / "tool" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


# ---- Salary benchmarks by role tier (UK, April 2026 market) -----------
# These are sensible defaults from Sara's plan + market knowledge. Sara
# can override any of these in the pitch pack before sending.
SALARY_BANDS_GBP = {
    "head of internal communications":   (85_000, 130_000),
    "head of corporate communications":  (100_000, 150_000),
    "head of communications":            (90_000, 140_000),
    "communications director":           (130_000, 180_000),
    "corporate affairs director":        (140_000, 200_000),
    "chief communications officer":      (180_000, 300_000),
    "head of pr":                        (80_000, 120_000),
    "pr director":                       (110_000, 160_000),
    "head of media relations":           (90_000, 130_000),
    "marketing and brand director":      (130_000, 180_000),
    "head of corporate affairs":         (110_000, 160_000),
    "head of external communications":   (95_000, 145_000),
}


def _salary_band(role: str) -> tuple[int, int, str]:
    """Find the closest matching salary band. Returns (low, high, matched_role).
    Defaults to Head of IC if nothing matches."""
    r = (role or "").lower().strip()
    if not r:
        return *SALARY_BANDS_GBP["head of internal communications"], "head of internal communications"
    # Exact substring match
    for key, band in SALARY_BANDS_GBP.items():
        if key in r or r in key:
            return *band, key
    return *SALARY_BANDS_GBP["head of internal communications"], "head of internal communications (default)"


# ---- Cost of vacancy: simple defensible template ---------------------
# Senior comms searches typically run 14–18 weeks brief-to-placement
# (retained, named-longlist). We use 16 weeks as the COV assumption —
# more defensible than the previous 12-week figure and produces a
# stronger retained argument.
COV_WEEKS = 16


def cost_of_vacancy(role: str, salary_midpoint: int) -> dict:
    """The £ cost to the CLIENT of leaving the permanent seat empty —
    the business case for engaging the retained search now (NOT a
    recommendation to place an interim; interim is off-product):
    - Productivity loss while the seat is unfilled (16 weeks)
    - Stop-gap cover the client bears during the gap (~£600/day equiv.)
    - Risk premium for a rushed bad hire (~30% of first-year salary)
    Returns a dict of {label: amount}.
    """
    productivity = int(salary_midpoint * 1.5 * (COV_WEEKS / 52))
    gap_cover = 600 * 5 * COV_WEEKS
    bad_hire_risk = int(salary_midpoint * 0.30)
    return {
        f"Lost productivity ({COV_WEEKS} wks vacant)": productivity,
        f"Stop-gap cover during the vacancy ({COV_WEEKS} wks)": gap_cover,
        "Rushed bad-hire downside risk (30% salary)": bad_hire_risk,
        "Cost of leaving the seat empty": productivity + gap_cover + bad_hire_risk,
    }


# ---- Recent news lookups ---------------------------------------------
def recent_news_for(target: str, hours_back: int = 24 * 90) -> list[dict]:
    """Last 90 days of news mentioning the target. Best-effort via GDELT
    (no auth needed). Returns list of article dicts."""
    from tool.config import SOURCES
    r = get(SOURCES["gdelt_doc"], params={
        "query": f'"{target}"',
        "mode": "ArtList",
        "format": "json",
        "timespan": f"{hours_back}h",
        "maxrecords": 10,
        "sort": "datedesc",
    })
    if not r:
        log.warning("GDELT %r: no HTTP response", target)
        return []
    if r.status_code != 200:
        log.warning("GDELT %r: HTTP %s body=%s",
                    target, r.status_code, (r.text or "")[:200])
        return []
    try:
        articles = (r.json().get("articles") or [])[:10]
    except Exception as e:
        log.warning("GDELT %r: JSON parse failed (%s); raw=%s",
                    target, e, (r.text or "")[:200])
        return []
    log.info("GDELT %r: %d articles", target, len(articles))
    return articles


# ---- HTML email rendering --------------------------------------------
def _esc(s) -> str:
    return html.escape(str(s) if s is not None else "", quote=True)


def _fmt_gbp(n: int) -> str:
    return f"£{n:,.0f}"


def render_html(target: str, role: str, ch_snapshot: dict,
                news: list[dict], peers: list[str], sector: str | None,
                salary_band: tuple[int, int, str],
                cov: dict, mode: str,
                annual_report=None) -> str:
    low, high, matched = salary_band
    mid = (low + high) // 2

    # CH snapshot
    if ch_snapshot.get("found"):
        resolved = ch_snapshot.get("resolved") or {}
        co_num = _esc(resolved.get("company_number", ""))
        co_addr = _esc(resolved.get("address_snippet", ""))
        co_status = _esc(resolved.get("company_status", ""))
        ch_html = f"""
        <div><strong>Companies House:</strong> {co_num} · {co_status}</div>
        <div style='color:#555;font-size:13px;'>{co_addr}</div>
        """
    else:
        ch_html = "<div style='color:#888;'>Companies House: not resolved (likely non-UK-registered or trading-name only).</div>"

    # Section 2 dual mode:
    #   (a) Bespoke — quotes from the target's most recent annual report
    #   (b) Fallback — GDELT headlines, labelled "Recent market context"
    # The labelling difference tells Sara whether to trust this section
    # as the bespoke edge of the pack or as defensible filler.
    if annual_report and annual_report.quotes:
        section2_heading = "2. Why this matters now"
        section2_subline = (
            f"Strategic context from {_esc(target)}'s annual report filed "
            f"{_esc(annual_report.filing_date)}. Pick the quote that best "
            f"matches the brief context; delete the rest before sending."
        )
        quote_items = []
        for q in annual_report.quotes:
            quote_items.append(
                f"<li style='margin-bottom:10px;'>"
                f"<span style='font-style:italic;color:#222;'>“{_esc(q.text)}”</span>"
                f"<div style='font-size:11px;color:#888;margin-top:2px;'>- {_esc(q.heading)}, p.{q.page}</div>"
                f"</li>"
            )
        news_html = (
            f"<div style='font-size:13px;color:#555;margin-bottom:8px;'>"
            f"{section2_subline}</div>"
            f"<ul style='padding-left:18px;font-size:13px;'>"
            + "".join(quote_items)
            + "</ul>"
        )
    elif news:
        section2_heading = "2. Recent market context"
        news_html = (
            "<div style='font-size:13px;color:#555;margin-bottom:8px;'>"
            "Generic news context (annual report quote extraction unavailable for this company - "
            "consider adding a bespoke strategic line in your cover note):"
            "</div>"
            "<ul style='padding-left:18px;font-size:13px;'>"
        )
        for a in news[:5]:
            news_html += f"<li><a href='{_esc(a.get('url',''))}'>{_esc(a.get('title',''))}</a> <span style='color:#888;'>· {_esc(a.get('seendate','')[:8])}</span></li>"
        news_html += "</ul>"
    else:
        section2_heading = "2. Recent market context"
        news_html = "<div style='color:#888;'>No strategic-report quotes available and no GDELT coverage - check trade press manually.</div>"

    # Peer market map
    sector_label = sector.replace("_", " ").title() if sector else "Detected sector unclear - generic FTSE list"
    peer_html = "<ol style='padding-left:18px;font-size:13px;'>"
    for p in peers:
        peer_html += f"<li>{_esc(p)}</li>"
    peer_html += "</ol>"

    # COV breakdown
    cov_html = "<table style='border-collapse:collapse;font-size:13px;'>"
    for k, v in cov.items():
        bold = "font-weight:600;" if "Total" in k else ""
        cov_html += f"<tr><td style='padding:4px 12px 4px 0;{bold}'>{_esc(k)}</td><td style='text-align:right;{bold}'>{_fmt_gbp(v)}</td></tr>"
    cov_html += "</table>"

    # Methodology
    methodology_html = """
    <table style='border-collapse:collapse;font-size:13px;'>
      <tr><th style='text-align:left;padding:4px 12px 4px 0;'>Week</th><th style='text-align:left;padding:4px;'>Deliverable</th><th style='text-align:right;padding:4px;'>Fee milestone</th></tr>
      <tr><td>1</td><td>Briefing call · success criteria signed off · market intelligence pack delivered</td><td style='text-align:right;'>1/3 on engagement</td></tr>
      <tr><td>2–3</td><td>Universe mapped · longlist of named candidates calibrated to the brief's complexity</td><td></td></tr>
      <tr><td>3–4</td><td>Outreach + qualification · shortlist of 5–7 confirmed for interview</td><td style='text-align:right;'>1/3 on shortlist</td></tr>
      <tr><td>4–5</td><td>Client interviews · feedback management · final 2–3</td><td></td></tr>
      <tr><td>6</td><td>Offer · references · onboarding handover</td><td style='text-align:right;'>1/3 on accepted offer</td></tr>
    </table>
    """

    note_banner = ""
    if mode == "test":
        note_banner = "<div style='background:#fff3cd;border:1px solid #ffeaa7;padding:8px;margin-bottom:16px;font-size:13px;'>⚠️ TEST PACK - generated for Amir's review. Do not send to client.</div>"

    return f"""<!doctype html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:760px;margin:0 auto;padding:20px;color:#111;">
{note_banner}
<h2 style="margin:0 0 4px 0;">Retained Pitch Pack - {_esc(target)}</h2>
<div style="color:#666;font-size:13px;margin-bottom:18px;">
  Role: {_esc(role)} · Generated {_esc(datetime.now().strftime('%a %d %b %Y · %H:%M'))} · For Sara Tehrani, VMA Group
</div>
<hr style="border:none;border-top:2px solid #C96442;margin:14px 0 24px;">

<h3 style="margin:18px 0 6px 0;">1. Account snapshot</h3>
{ch_html}

<h3 style="margin:18px 0 6px 0;">{section2_heading}</h3>
{news_html}

<h3 style="margin:18px 0 6px 0;">3. Cost of vacancy</h3>
<div style='font-size:13px;color:#444;margin-bottom:6px;'>
  Mid-range salary assumed: <strong>{_fmt_gbp(mid)}</strong>. Override if the brief is at a different level.
</div>
{cov_html}

<h3 style="margin:18px 0 6px 0;">4. Talent universe ({_esc(sector_label)})</h3>
<div style='font-size:13px;color:#555;margin-bottom:6px;'>
  Where the candidate pool sits. These 15 named employers represent the realistic move-from set for a {_esc(matched)}-level hire in this sector.
</div>
{peer_html}

<h3 style="margin:18px 0 6px 0;">5. Salary benchmark</h3>
<div style='font-size:13px;'>
  UK April 2026 range for <strong>{_esc(matched)}</strong>: <strong>{_fmt_gbp(low)}–{_fmt_gbp(high)}</strong> base, plus 10–25% bonus / LTIP at FTSE-listed level.
</div>

<h3 style="margin:18px 0 6px 0;">6. 6-week retained methodology</h3>
{methodology_html}
<div style='font-size:12px;color:#888;margin-top:6px;'>
  Retained fee: 28–33% of first-year total comp, in thirds at engagement / shortlist / accepted offer.
  Versus contingent at 22–25% of base only.
</div>

<h3 style="margin:18px 0 6px 0;">7. Why retained over contingent</h3>
<ul style='padding-left:18px;font-size:13px;color:#333;'>
  <li>Exclusivity unlocks deeper passive-candidate outreach (~3× larger universe vs contingent)</li>
  <li>Milestone fees align our priority with yours, so we're not racing 6 other firms on the same role</li>
  <li>Pre-agreed methodology removes 8–10 hours of back-and-forth at submission stage</li>
  <li>Senior comms placements typically open 2-4 downstream hires (IC Business Partners, Change Comms Leads, Digital Comms Managers) over the following 12-18 months, so a retained engagement positions VMA Group for the full pipeline, not just the headline role</li>
</ul>

<h3 style="margin:18px 0 6px 0;">8. Risk-mitigation terms</h3>
<div style='font-size:13px;color:#333;'>
  Standard rebate schedule, off-limits clause, exclusivity period, and replacement guarantee per VMA Group's terms of engagement, provided separately as part of the contract pack.
</div>

</body></html>
"""


def render_text(target: str, role: str, ch_snapshot: dict,
                news: list[dict], peers: list[str], sector: str | None,
                salary_band: tuple[int, int, str], cov: dict,
                annual_report=None) -> str:
    low, high, matched = salary_band
    mid = (low + high) // 2
    lines = [
        f"Retained Pitch Pack - {target}",
        f"Role: {role}  ·  Generated {datetime.now().strftime('%a %d %b %Y · %H:%M')}",
        "=" * 60, "",
        "1. ACCOUNT SNAPSHOT",
    ]
    if ch_snapshot.get("found"):
        resolved = ch_snapshot.get("resolved") or {}
        lines.append(f"   Companies House: {resolved.get('company_number','?')} · {resolved.get('company_status','?')}")
        lines.append(f"   {resolved.get('address_snippet','')}")
    else:
        lines.append("   Companies House: not resolved.")

    if annual_report and annual_report.quotes:
        lines += ["", f"2. WHY THIS MATTERS NOW (annual report filed {annual_report.filing_date})"]
        lines.append("   Pick the quote that best matches the brief context.")
        for q in annual_report.quotes:
            lines.append(f"   - \"{q.text}\"")
            lines.append(f"     [{q.heading}, p.{q.page}]")
    elif news:
        lines += ["", "2. RECENT MARKET CONTEXT (generic, no annual report quotes available)"]
        for a in news[:5]:
            lines.append(f"   - {a.get('title','')} ({a.get('seendate','')[:8]})")
            lines.append(f"     {a.get('url','')}")
    else:
        lines += ["", "2. RECENT MARKET CONTEXT"]
        lines.append("   No annual report quotes or press coverage surfaced.")
    lines += ["", "3. COST OF VACANCY", f"   Mid-salary assumed: £{mid:,}"]
    for k, v in cov.items():
        lines.append(f"   {k:<42}  £{v:>10,}")
    sector_label = sector.replace("_", " ").title() if sector else "Generic FTSE"
    lines += ["", f"4. TALENT UNIVERSE ({sector_label})"]
    for i, p in enumerate(peers, 1):
        lines.append(f"   {i:>2}. {p}")
    lines += ["", "5. SALARY BENCHMARK",
              f"   UK April 2026 range for {matched}: £{low:,}–£{high:,} base + 10–25% bonus/LTIP",
              "", "6. 6-WEEK METHODOLOGY",
              "   Wk 1   Briefing + market pack                       (1/3 on engagement)",
              "   Wk 2–3 Universe mapped + longlist of named candidates",
              "   Wk 3–4 Outreach + shortlist of 5–7                  (1/3 on shortlist)",
              "   Wk 4–5 Client interviews + finals",
              "   Wk 6   Offer + onboarding handover                  (1/3 on accepted offer)",
              "", "Retained fee: 28–33% of first-year total comp (vs 22–25% contingent on base only).",
              "",
              "7. WHY RETAINED",
              "   - Exclusivity unlocks ~3x larger passive universe",
              "   - Milestone fees align priority",
              "   - Pre-agreed methodology removes 8-10 hrs of back-and-forth",
              "   - Senior comms placements typically open 2-4 downstream hires over",
              "     12-18 months, so retained positions VMA Group for the full pipeline",
              "",
              "8. RISK-MITIGATION TERMS",
              "   Standard rebate schedule, off-limits clause, exclusivity period, and",
              "   replacement guarantee per VMA Group's terms of engagement, provided",
              "   separately as part of the contract pack."]
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python -m tool.pitch_pack "<account name>" [role="Head of Internal Communications"] [mode=preview|send|test]', file=sys.stderr)
        return 2

    target = sys.argv[1].strip()
    role = os.environ.get("PITCH_ROLE", "Head of Internal Communications")
    mode = (sys.argv[2] if len(sys.argv) > 2 else "preview").lower()
    log.info("Building pitch pack for %r · role %r · mode %r", target, role, mode)

    # Diagnostic startup banner — surfaces secret-propagation issues early.
    log.info("env check: COMPANIES_HOUSE_KEY=%s · BRIGHT_DATA_KEY=%s · "
             "GMAIL_USER=%s · PITCH_SALARY_MIN=%r · PITCH_SALARY_MAX=%r",
             "set" if os.environ.get("COMPANIES_HOUSE_KEY") else "EMPTY",
             "set" if os.environ.get("BRIGHT_DATA_KEY") else "EMPTY",
             "set" if os.environ.get("GMAIL_USER") else "EMPTY",
             os.environ.get("PITCH_SALARY_MIN", ""),
             os.environ.get("PITCH_SALARY_MAX", ""))

    ch = companies_house.company_events(target)
    news = recent_news_for(target)
    peers, sector = peers_for(target, k=15)

    # Annual report quote extraction (best-effort; falls back to GDELT if it
    # can't reach the PDF or scores no quotes). Adds ~10–30sec to pack
    # generation when it lands a FTSE annual report; near-zero overhead
    # if the company isn't CH-resolved.
    annual_rep = None
    if ch.get("found"):
        co_num = (ch.get("resolved") or {}).get("company_number", "")
        if co_num:
            try:
                annual_rep = ar.fetch_strategic_quotes(co_num)
            except Exception as e:
                log.exception("annual_report extraction failed: %s", e)

    # Salary band: auto-detect from role, with optional override via env
    # (set by the dashboard form). Both bounds are optional — if just one
    # is provided we keep the auto-detected partner for the other.
    sal = _salary_band(role)
    override_min = (os.environ.get("PITCH_SALARY_MIN") or "").strip()
    override_max = (os.environ.get("PITCH_SALARY_MAX") or "").strip()
    if override_min or override_max:
        try:
            low = int(override_min) if override_min else sal[0]
            high = int(override_max) if override_max else sal[1]
            if low > high:
                low, high = high, low
            sal = (low, high, f"{sal[2]} (Sara override)")
            log.info("Salary override applied: £%d–£%d", low, high)
        except ValueError:
            log.warning("Bad salary override (min=%r max=%r); using auto-detected band",
                        override_min, override_max)
    mid = (sal[0] + sal[1]) // 2
    cov = cost_of_vacancy(role, mid)

    html_out = render_html(target, role, ch, news, peers, sector, sal, cov, mode,
                            annual_report=annual_rep)
    text_out = render_text(target, role, ch, news, peers, sector, sal, cov,
                            annual_report=annual_rep)

    safe = "".join(c if c.isalnum() else "_" for c in target.lower())[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    (STATE_DIR / f"pitch_pack_{safe}_{stamp}.html").write_text(html_out)
    (STATE_DIR / f"pitch_pack_{safe}_{stamp}.txt").write_text(text_out)

    if mode in ("send", "test") and getattr(config, "NON_BRIEF_EMAIL_ENABLED", False):
        to = config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT
        subject = f"[Pitch Pack] {target} - {role}"
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

    print(text_out)
    print(f"\n[saved to tool/state/pitch_pack_{safe}_{stamp}.{{html,txt}}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
