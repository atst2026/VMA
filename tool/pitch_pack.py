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
def _core_name(target: str) -> str:
    """Strip common public-company suffixes so 'HSBC UK' / 'Tesco PLC' query
    GDELT on the entity the press actually names ('HSBC' / 'Tesco')."""
    t = (target or "").strip()
    for suf in (" holdings plc", " group plc", " uk plc", " bank plc",
                " plc", " group", " holdings", " uk", " bank",
                " ltd", " limited"):
        if t.lower().endswith(suf):
            return t[: -len(suf)].strip()
    return t


def _gdelt_articles(query: str, hours_back: int) -> list[dict]:
    from tool.config import SOURCES
    r = get(SOURCES["gdelt_doc"], params={
        "query": query, "mode": "ArtList", "format": "json",
        "timespan": f"{hours_back}h", "maxrecords": 10, "sort": "datedesc",
    })
    if not r or r.status_code != 200:
        log.warning("GDELT %s: %s", query,
                    "no response" if not r else f"HTTP {r.status_code}")
        return []
    try:
        return (r.json().get("articles") or [])[:10]
    except Exception as e:
        log.warning("GDELT %s: JSON parse failed (%s)", query, e)
        return []


def _is_relevant_english(article: dict, core: str) -> bool:
    """Keep only English-language coverage that actually names the company.
    GDELT otherwise returns multilingual global noise (Spanish banking,
    Chinese exchange filings, etc.) that has nothing to do with the target —
    which must never reach a client-facing pack or the client-language miner."""
    title = article.get("title") or ""
    if not title:
        return False
    lang = (article.get("language") or "").strip().lower()
    if lang:
        if lang not in ("english", "eng", "en"):
            return False
    else:
        # No language field: require a predominantly ASCII (Latin) title.
        if sum(1 for ch in title if ord(ch) < 128) / len(title) < 0.9:
            return False
    return (core or "").lower() in title.lower()


def recent_news_for(target: str, hours_back: int = 24 * 90) -> list[dict]:
    """Last 90 days of English news that actually names the target. Best-effort
    via GDELT (no auth). Queries the exact name first, then the core name;
    results are then filtered to English-language articles whose title mentions
    the company, so a pack never shows irrelevant foreign coverage."""
    core = _core_name(target) or (target or "").strip()
    raw = _gdelt_articles(f'"{target}"', hours_back)
    if not raw and core.lower() != (target or "").strip().lower():
        log.info("GDELT %r empty — retrying core name %r", target, core)
        raw = _gdelt_articles(f'"{core}"', hours_back)
    articles = [a for a in raw if _is_relevant_english(a, core)]
    log.info("GDELT %r: %d raw, %d English & on-topic", target, len(raw), len(articles))
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
                annual_report=None, curated_priorities: list[str] | None = None) -> str:
    low, high, matched = salary_band
    mid = (low + high) // 2

    # Cover identity: prefer the client's real logo over their name typed as
    # text. logo.dev source via tool/company_logo; any failure (no token, no
    # verified domain, bad logo) returns None and we fall back to the text
    # wordmark below — no regression.
    cover_logo_uri = None
    try:
        from tool import company_logo
        cover_logo_uri = company_logo.logo_data_uri(target, box_h=96)
    except Exception as e:  # never let a logo lookup break pack generation
        log.info("cover logo lookup failed for %s: %s", target, e)

    if cover_logo_uri:
        cover_id_html = (
            f'<div style="margin:0 0 6px 0;">'
            f'<img src="{cover_logo_uri}" alt="{_esc(target)}" '
            f'style="height:48px;max-width:320px;object-fit:contain;display:block;">'
            f'</div>'
            f'<h2 style="margin:0 0 4px 0;">Retained Pitch Pack</h2>'
        )
    else:
        cover_id_html = (
            f'<h2 style="margin:0 0 4px 0;">Retained Pitch Pack - {_esc(target)}</h2>'
        )

    # CH is demoted to a small audit footnote — the company number and street
    # address aren't useful to an AD pitching. The AD-useful snapshot (sector /
    # fee / cost-of-vacancy / pipeline) is assembled below as snapshot_html.
    if ch_snapshot.get("found"):
        resolved = ch_snapshot.get("resolved") or {}
        co_num = _esc(resolved.get("company_number", ""))
        co_status = _esc(resolved.get("company_status", ""))
        ch_footer = (
            "<div style='color:#999;font-size:11px;margin-top:8px;'>"
            f"Companies House: {co_num} · {co_status} "
            "<span style='color:#bbb;'>— confirm this is the right entity before sending</span></div>"
        )
    else:
        ch_footer = (
            "<div style='color:#999;font-size:11px;margin-top:8px;'>"
            "UK entity not resolved (non-UK-registered or trading-name only).</div>"
        )

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
    elif curated_priorities:
        section2_heading = "2. Why this matters now"
        news_html = (
            "<div style='font-size:13px;color:#555;margin-bottom:8px;'>"
            f"Publicly stated strategic priorities for {_esc(target)} (curated from their "
            "latest public reporting — live annual-report extraction unavailable for this "
            "entity). Pick the one that best matches the brief:"
            "</div>"
            "<ul style='padding-left:18px;font-size:13px;'>"
            + "".join(f"<li style='margin-bottom:8px;'>{_esc(p)}</li>" for p in curated_priorities)
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

    # ---- Client-language mirroring (drop-in vocabulary) ----------------
    # Mine the client's own public-comms phrasing (annual-report quotes +
    # recent press) and surface the recurring lines for Sara to echo.
    from tool import client_language as _cl
    _corpus = [q.text for q in annual_report.quotes] if (annual_report and annual_report.quotes) else []
    if not _corpus and curated_priorities:
        _corpus += list(curated_priorities)
    _corpus += [a.get("title", "") for a in (news or [])[:30]]
    _mirror = _cl.mirror_phrases(_corpus, top_n=8)
    if _mirror:
        _mi = "".join(
            "<li style='margin-bottom:8px;'>"
            f"<span style='font-weight:600;color:#1A3D7C;'>{_esc(m['phrase'])}</span>"
            f"<div style='font-size:11px;color:#777;margin-top:2px;'>e.g. &ldquo;{_esc(m['example'])}&rdquo;</div>"
            "</li>"
            for m in _mirror
        )
        client_lang_html = (
            "<div style='font-size:13px;color:#555;margin-bottom:8px;'>"
            "Echo the client&rsquo;s own framing &mdash; the same methodology in their "
            "language converts materially better than generic search vocabulary. "
            "Lift these recurring phrases into your cover note and outreach:"
            "</div>"
            f"<ul style='padding-left:18px;font-size:13px;'>{_mi}</ul>"
        )
    else:
        client_lang_html = ""

    # Peer market map
    sector_label = sector.replace("_", " ").title() if sector else "Detected sector unclear - generic FTSE list"
    peer_html = "<ol style='padding-left:18px;font-size:13px;'>"
    for p in peers:
        peer_html += f"<li>{_esc(p)}</li>"
    peer_html += "</ol>"

    # Dead-market reframe — handles the "there's no one out there" objection
    # with the data we already compute (the named universe + cost of vacancy).
    reframe_html = (
        "<div style='font-size:12.5px;color:#444;background:rgba(61,90,130,0.05);"
        "border-left:3px solid #3D5A82;padding:8px 12px;border-radius:4px;margin-bottom:10px;'>"
        f"If the read on this brief is that the market's quiet — it isn't scarce, it's "
        f"<em>placed</em>. The {len(peers)} named employers below are where a "
        f"{_esc(matched)} sits today; the constraint is reaching them before a competitor "
        f"does, which is exactly what a retained search buys. See the cost of vacancy above "
        f"for what waiting carries."
        "</div>"
    )

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

    # ---- Account snapshot (§1): what they are, what the mandate is worth,
    # the urgency, and the downstream pipeline — built from data we already
    # compute, replacing the bare CH number/address. ----
    sector_disp = sector_label if sector else "Sector unclear — confirm before pitching"
    fee_low = int(round(0.28 * mid, -2))
    fee_high = int(round(0.33 * mid, -2))
    cov_total = next((v for k, v in cov.items()
                      if "leaving the seat empty" in k.lower() or k.lower().startswith("total")), None)
    snapshot_html = (
        "<div style='display:flex;flex-wrap:wrap;gap:14px 30px;font-size:13px;margin-bottom:10px;'>"
        f"<div><span style='color:#888;'>Sector</span><br><strong>{_esc(sector_disp)}</strong></div>"
        "<div><span style='color:#888;'>Indicative retained fee</span><br>"
        f"<strong>{_fmt_gbp(fee_low)}–{_fmt_gbp(fee_high)}</strong> "
        "<span style='color:#888;font-size:11px;'>(28–33% of base; more with bonus/LTIP)</span></div>"
        + (("<div><span style='color:#888;'>Cost of an empty seat</span><br>"
            f"<strong>{_fmt_gbp(cov_total)}</strong> "
            "<span style='color:#888;font-size:11px;'>(see Section 3)</span></div>") if cov_total else "")
        + "</div>"
        "<div style='font-size:13px;color:#444;'>"
        "<span style='color:#888;'>Pipeline:</span> a senior comms placement typically opens "
        "<strong>2–4 follow-on hires</strong> over 12–18 months — a retained engagement positions "
        "VMA for the full pipeline, not just the headline role.</div>"
        f"{ch_footer}"
    )

    note_banner = ""
    if mode == "test":
        note_banner = "<div style='background:#fff3cd;border:1px solid #ffeaa7;padding:8px;margin-bottom:16px;font-size:13px;'>⚠️ TEST PACK - generated for Amir's review. Do not send to client.</div>"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"></head><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:760px;margin:0 auto;padding:20px;color:#111;">
{note_banner}
{cover_id_html}
<div style="color:#666;font-size:13px;margin-bottom:18px;">
  Role: {_esc(role)} · Generated {_esc(datetime.now().strftime('%a %d %b %Y · %H:%M'))} · For Sara Tehrani, VMA Group
</div>
<hr style="border:none;border-top:2px solid #3D5A82;margin:14px 0 24px;">

<h3 style="margin:18px 0 6px 0;">1. Account snapshot</h3>
{snapshot_html}

<h3 style="margin:18px 0 6px 0;">{section2_heading}</h3>
{news_html}
{('<h3 style="margin:18px 0 6px 0;">2b. Client language to mirror</h3>' + client_lang_html) if client_lang_html else ''}

<h3 style="margin:18px 0 6px 0;">3. Cost of vacancy</h3>
<div style='font-size:13px;color:#444;margin-bottom:6px;'>
  Mid-range salary assumed: <strong>{_fmt_gbp(mid)}</strong>. Override if the brief is at a different level.
</div>
{cov_html}

<h3 style="margin:18px 0 6px 0;">4. Talent universe ({_esc(sector_label)})</h3>
{reframe_html}
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
                annual_report=None, curated_priorities: list[str] | None = None) -> str:
    low, high, matched = salary_band
    mid = (low + high) // 2
    lines = [
        f"Retained Pitch Pack - {target}",
        f"Role: {role}  ·  Generated {datetime.now().strftime('%a %d %b %Y · %H:%M')}",
        "=" * 60, "",
        "1. ACCOUNT SNAPSHOT",
    ]
    _sector_disp = (sector.replace("_", " ").title() if sector
                    else "Sector unclear — confirm before pitching")
    _fee_low = int(round(0.28 * mid, -2))
    _fee_high = int(round(0.33 * mid, -2))
    _cov_total = next((v for k, v in cov.items()
                       if "leaving the seat empty" in k.lower() or k.lower().startswith("total")), None)
    lines.append(f"   Sector: {_sector_disp}")
    lines.append(f"   Indicative retained fee: £{_fee_low:,}–£{_fee_high:,} (28–33% of base; more with bonus/LTIP)")
    if _cov_total:
        lines.append(f"   Cost of an empty seat: £{_cov_total:,} (see section 3)")
    lines.append("   Pipeline: a senior comms placement typically opens 2–4 follow-on hires over 12–18 months.")
    if ch_snapshot.get("found"):
        resolved = ch_snapshot.get("resolved") or {}
        lines.append(f"   Companies House: {resolved.get('company_number','?')} · {resolved.get('company_status','?')} "
                     "— confirm this is the right entity before sending")
    else:
        lines.append("   UK entity not resolved (non-UK-registered or trading-name only).")

    if annual_report and annual_report.quotes:
        lines += ["", f"2. WHY THIS MATTERS NOW (annual report filed {annual_report.filing_date})"]
        lines.append("   Pick the quote that best matches the brief context.")
        for q in annual_report.quotes:
            lines.append(f"   - \"{q.text}\"")
            lines.append(f"     [{q.heading}, p.{q.page}]")
    elif curated_priorities:
        lines += ["", f"2. WHY THIS MATTERS NOW (publicly stated priorities for {target})"]
        lines.append("   Curated from their latest public reporting (live annual-report extraction unavailable).")
        for p in curated_priorities:
            lines.append(f"   - {p}")
    elif news:
        lines += ["", "2. RECENT MARKET CONTEXT (generic, no annual report quotes available)"]
        for a in news[:5]:
            lines.append(f"   - {a.get('title','')} ({a.get('seendate','')[:8]})")
            lines.append(f"     {a.get('url','')}")
    else:
        lines += ["", "2. RECENT MARKET CONTEXT"]
        lines.append("   No annual report quotes or press coverage surfaced.")

    from tool import client_language as _cl
    _corpus = [q.text for q in annual_report.quotes] if (annual_report and annual_report.quotes) else []
    if not _corpus and curated_priorities:
        _corpus += list(curated_priorities)
    _corpus += [a.get("title", "") for a in (news or [])[:30]]
    _mirror = _cl.mirror_phrases(_corpus, top_n=8)
    if _mirror:
        lines += ["", "2b. CLIENT LANGUAGE TO MIRROR",
                  "   Echo the client's own framing — converts better than generic search vocab."]
        for m in _mirror:
            lines.append(f"   - {m['phrase']}")
            lines.append(f"     e.g. \"{m['example']}\"")

    lines += ["", "3. COST OF VACANCY", f"   Mid-salary assumed: £{mid:,}"]
    for k, v in cov.items():
        lines.append(f"   {k:<42}  £{v:>10,}")
    sector_label = sector.replace("_", " ").title() if sector else "Generic FTSE"
    lines += ["", f"4. TALENT UNIVERSE ({sector_label})",
              f"   If the brief reads as a quiet market: the profile isn't scarce, it's placed.",
              f"   The {len(peers)} named employers below are where a {matched} sits today —",
              f"   reaching them before a competitor does is what a retained search buys."]
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

    # Curated-priorities fallback: if the live annual-report extraction came
    # back empty (subsidiary filings carry no strategic narrative, scanned
    # PDFs, non-UK entities), fall back to the hand-curated tier-A priorities
    # so Section 2 + the client-language layer still fire for known accounts.
    curated_priorities: list[str] = []
    if not (annual_rep and annual_rep.quotes):
        try:
            from tool.pre_meeting import _load_curated_priorities
            curated_priorities = _load_curated_priorities(target)
        except Exception as e:
            log.info("curated-priorities fallback failed: %s", e)

    html_out = render_html(target, role, ch, news, peers, sector, sal, cov, mode,
                            annual_report=annual_rep, curated_priorities=curated_priorities)
    text_out = render_text(target, role, ch, news, peers, sector, sal, cov,
                            annual_report=annual_rep, curated_priorities=curated_priorities)

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
