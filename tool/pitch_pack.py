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
import re as _re
import sys
import textwrap
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool import config
from tool import annual_report as ar
from tool.email_send import send as email_send
from tool.peers import pitch_peers_for, detect_sector
from tool.sources import companies_house, gdelt
from tool.sources._http import get

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("pitch_pack")

from tool.state_paths import state_dir, state_root
STATE_DIR = state_dir()
STATE_DIR.mkdir(parents=True, exist_ok=True)


# ---- Salary benchmarks by role tier (UK senior market baseline) -------
# Hand-maintained reference bands; the rendered pack stamps the current
# month/year, so refresh these periodically to keep that label honest.
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

# Marketing desk (FIRST DRAFT): senior UK marketing salary bands.
_MARKETING_SALARY_BANDS_GBP = {
    "head of marketing":             (85_000, 130_000),
    "head of brand":                 (90_000, 135_000),
    "brand director":                (110_000, 160_000),
    "marketing director":            (120_000, 175_000),
    "director of marketing":         (120_000, 175_000),
    "chief marketing officer":       (180_000, 320_000),
    "head of growth":                (90_000, 150_000),
    "head of digital marketing":     (85_000, 130_000),
    "head of performance marketing": (85_000, 130_000),
    "head of product marketing":     (90_000, 140_000),
    "ecommerce director":            (110_000, 160_000),
    "crm director":                  (95_000, 140_000),
    "head of demand generation":     (85_000, 130_000),
}

from tool.profiles import active_profile as _active_profile
_MKT = _active_profile().key == "marketing"
if _MKT:
    SALARY_BANDS_GBP = _MARKETING_SALARY_BANDS_GBP
# Profile-aware defaults used below + in the generated copy.
_DEFAULT_BAND_KEY = "head of marketing" if _MKT else "head of internal communications"
_DEFAULT_ROLE = "Head of Marketing" if _MKT else "Head of Internal Communications"
_NOUN = "marketing" if _MKT else "comms"
_DOWNSTREAM_EXAMPLES = (
    "Brand Managers, Campaign Managers, Digital Marketing Managers" if _MKT
    else "IC Business Partners, Change Comms Leads, Digital Comms Managers")


def _salary_band(role: str) -> tuple[int, int, str]:
    """Find the closest matching salary band. Returns (low, high, matched_role).
    Defaults to the profile's default seat if nothing matches."""
    r = (role or "").lower().strip()
    if not r:
        return *SALARY_BANDS_GBP[_DEFAULT_BAND_KEY], _DEFAULT_BAND_KEY
    # Exact substring match
    for key, band in SALARY_BANDS_GBP.items():
        if key in r or r in key:
            return *band, key
    return *SALARY_BANDS_GBP[_DEFAULT_BAND_KEY], f"{_DEFAULT_BAND_KEY} (default)"


# ---- Timeline: reconcile "cost of an empty seat" with the methodology -
# The retained SEARCH is ~6 weeks brief-to-offer — the part VMA controls
# (Section 6). A senior hire then serves notice (typically ~3 months at this
# level), so the seat is effectively unfilled until someone is in and
# productive ~18 weeks out, and every week the brief is delayed adds on top.
# Cost-of-vacancy is measured over time-to-productive; the 6-week methodology
# is the controllable search window. This removes the old 16-vs-6-week
# contradiction the pack used to print on the same page.
SEARCH_WEEKS = 6
NOTICE_WEEKS = 12
TIME_TO_PRODUCTIVE_WEEKS = SEARCH_WEEKS + NOTICE_WEEKS  # ~18

# Interim senior-cover day-rate the client bears during the gap (UK, 2026).
INTERIM_DAY_RATE_GBP = 700

# Base -> estimated first-year TOTAL comp (base + typical bonus/LTIP). The
# retained fee is quoted on total comp (Section 6), so the headline fee in
# Section 1 must be too — the old pack computed it on base only, which both
# contradicted Section 6 and anchored the client BELOW VMA's real number.
BONUS_LTIP_UPLIFT = 1.20


def estimate_total_comp(base_midpoint: int) -> int:
    """Estimated first-year total compensation from a base midpoint
    (base + typical 10-25% bonus/LTIP at senior FTSE-listed level)."""
    return int(round(base_midpoint * BONUS_LTIP_UPLIFT, -2))


def _comms_cov_headline(role: str, trigger_context: str) -> str:
    role_l = (role or "comms leader").strip()
    if trigger_context:
        return (f"With {trigger_context} and no {role_l} in seat, the cost of the "
                "gap isn't lost admin time — it's reputational: a mishandled "
                "disclosure, a slow crisis response, or a media and stakeholder "
                "moment landing with no senior owner. That exposure runs every week "
                "the seat is open.")
    return ("For a comms function the cost of an empty seat isn't lost admin time — "
            "it's reputational: a mishandled disclosure, a slow crisis response, or "
            "a media and stakeholder moment landing with no senior owner. That "
            "exposure runs every week the seat is open, which is what a retained "
            "search is bought to close quickly.")


def _marketing_cov_headline(role: str, trigger_context: str) -> str:
    role_l = (role or "marketing leader").strip()
    if trigger_context:
        return (f"With {trigger_context} and no {role_l} in seat, the cost of the "
                "gap is commercial, not administrative: demand generation stalls, "
                "campaigns and launches slip, and pipeline that should be building "
                "simply isn't. Every quarter the seat is open is a quarter of "
                "deferred growth.")
    return ("For a marketing function the cost of an empty seat is commercial, not "
            "administrative: demand generation stalls, campaigns and launches slip, "
            "and brand momentum decays. Every quarter the seat is open is a quarter "
            "of deferred pipeline and growth — which is what a retained search is "
            "bought to protect.")


def cost_of_vacancy(role: str, salary_midpoint: int, *,
                    frame: str | None = None,
                    trigger_context: str = "") -> dict:
    """The cost to the CLIENT of leaving the permanent seat empty — the
    business case for engaging the retained search now. Function-split by
    the buyer's value frame (the persuasion, not just the arithmetic):

      * comms     -> reputational / event risk is the headline cost
      * marketing -> deferred pipeline / growth is the headline cost

    Both carry the same two defensible £ components — interim cover for the
    gap, and the cost of getting the hire wrong — measured over time-to-
    productive (~18 wks), NOT the old hand-wavy productivity multiplier. Pass
    `trigger_context` (e.g. "half-year results eight weeks out") to anchor the
    headline to the specific live event behind a BD lead; it falls back to a
    frame-generic line when none is supplied.

    Returns {"frame", "headline", "lines": {label: £}, "total", "weeks",
    "total_comp"}.
    """
    frame = frame or ("marketing" if _MKT else "comms")
    weeks = TIME_TO_PRODUCTIVE_WEEKS
    total_comp = estimate_total_comp(salary_midpoint)
    interim = INTERIM_DAY_RATE_GBP * 5 * weeks
    rushed = int(round(total_comp * 0.30, -2))
    cover_label = ("Interim marketing-leadership cover" if frame == "marketing"
                   else "Interim senior-comms cover")
    lines = {
        f"{cover_label} (~{weeks} wks @ ~£{INTERIM_DAY_RATE_GBP}/day)": interim,
        ("Exposure of a rushed/wrong hire (conservative 30% of total comp — "
         "senior mis-hires commonly run materially higher)"): rushed,
    }
    total = sum(lines.values())
    lines["Cost of leaving the seat empty"] = total
    headline = (_marketing_cov_headline(role, trigger_context) if frame == "marketing"
                else _comms_cov_headline(role, trigger_context))
    return {"frame": frame, "headline": headline, "lines": lines,
            "total": total, "weeks": weeks, "total_comp": total_comp}


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


# Consumer-promo / lifestyle / clickbait coverage that names the brand but is
# irrelevant to a senior comms/marketing leadership pitch (e.g. a "free coffee
# using a little-known trick" deals piece). Deliberately targeted so it doesn't
# catch legitimate corporate news (note "free cash flow" is NOT matched — only
# "free <consumer item>").
_NEWS_NOISE = _re.compile(
    r"(?:"
    r"free (?:coffee|drink|cup|gift|sample|delivery|food|meal|item|refill|stuff|breakfast)|"
    r"\bvoucher|\bcoupon|promo code|discount code|\bgiveaway\b|win an? |"
    r"little[\s-]?known trick|how to (?:claim|get a free|save|order|win)|"
    r"secret menu|menu hack|money[\s-]?saving|save (?:£|\$)\s?\d|gift card|"
    r"loyalty (?:card|points|scheme)|reward(?:s)? (?:card|points|scheme)|"
    r"\brecipe\b|taste test|best (?:coffee|deals?|buys?)\b|deal of the (?:day|week)|"
    r"\bhack\b|order now|half[\s-]?price"
    r")", _re.I)


def _is_strategic_headline(title: str) -> bool:
    """False for consumer-promo / lifestyle / clickbait headlines that should
    never reach a client-facing pitch pack's 'Recent market context'."""
    return not _NEWS_NOISE.search(title or "")


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
    articles = [a for a in raw if _is_relevant_english(a, core)
                and _is_strategic_headline(a.get("title") or "")]
    log.info("GDELT %r: %d raw, %d English, on-topic & strategic", target, len(raw), len(articles))
    return articles


# ---- HTML email rendering --------------------------------------------
def _esc(s) -> str:
    return html.escape(str(s) if s is not None else "", quote=True)


def _fmt_gbp(n: int) -> str:
    return f"£{n:,.0f}"


def _prep_html(text: str) -> str:
    """A clearly-INTERNAL prep note — visually distinct so it can't be mistaken
    for client copy. Used only for the few legitimate Sara-only cues; the pack
    otherwise reads as a client-facing document (no backstage instructions)."""
    return (
        "<div style='font-size:11px;color:#9a6b00;background:rgba(255,193,7,0.08);"
        "border-left:2px solid #d39e00;padding:5px 9px;border-radius:3px;margin-top:6px;'>"
        f"▸ <em>Prep note — remove before sending:</em> {_esc(text)}</div>"
    )


# ---- Persona tailoring -----------------------------------------------
# The body of the pack is common; the OPENING reframes it for the specific
# reader (the research's fifth point). Selected by PITCH_PERSONA so a pack
# fired from a lead — or the dashboard — can pin the audience. Body unchanged.
_PERSONA_ALIASES = {
    "ceo": "ceo", "chief executive": "ceo", "chair": "ceo", "chairman": "ceo", "board": "ceo",
    "cfo": "cfo", "finance": "cfo", "fd": "cfo",
    "incoming": "incoming", "incoming leader": "incoming", "new leader": "incoming",
    "cco": "incoming", "cmo": "incoming",
    "hr": "hr", "hrd": "hr", "chro": "hr", "talent": "hr", "people": "hr",
}


def _fee_vs_cost_phrase(fee_low: int, fee_high: int, cov_total: int) -> str:
    """The single most CFO-converting line: the fee set against the cost of the
    empty seat, in one sentence. '' if either number is missing."""
    if not (cov_total and fee_high):
        return ""
    ratio = fee_high / cov_total
    rel = ("under half" if ratio <= 0.5
           else "around two-thirds" if ratio <= 0.7 else "a fraction of")
    return (f"the retained fee ({_fmt_gbp(fee_low)}–{_fmt_gbp(fee_high)}) is {rel} the "
            f"{_fmt_gbp(cov_total)} an empty seat costs")


def _persona_opener(persona: str, role: str, target: str,
                    fee_low: int = 0, fee_high: int = 0, cov_total: int = 0) -> str:
    """A tailored opening paragraph that BOTH frames the pack for the reader and
    pays off that frame (the research's point: a swappable opener + reordered
    emphasis, not a banner the body ignores). '' for none/unknown persona."""
    key = _PERSONA_ALIASES.get((persona or "").strip().lower())
    if not key:
        return ""
    role_l = (role or f"senior {_NOUN} leader").strip()
    if key == "cfo":
        fvc = _fee_vs_cost_phrase(fee_low, fee_high, cov_total)
        money = f" — {fvc} (Section 3)" if fvc else " (Section 3)"
        return (f"For finance: the case is in the numbers{money}, and the rebate and "
                "replacement guarantee (Section 9) cap the downside of a wrong hire.")
    _ceo_value = ("drives brand, growth and customer demand" if _MKT
                  else f"protects {target}'s reputation")
    return {
        "ceo": (f"For the chief executive: a board-level appointment — a {role_l} who acts as "
                f"strategic counsel and {_ceo_value} through the period ahead, "
                "with the search run discreetly and at pace."),
        "incoming": ("For an incoming leader: your first 6–12 months — we build the team beneath "
                     "you quickly and credibly (a senior placement typically opens 2–4 follow-on "
                     "hires), with a shortlist that lets you show the board early wins."),
        "hr": ("For HR and Talent: this runs alongside your in-house team, not over it — the "
               "passive reach and discretion an internal search can't carry, on a defined "
               "methodology with a replacement guarantee."),
    }[key]


def _persona_html(persona: str, role: str, target: str,
                  fee_low: int = 0, fee_high: int = 0, cov_total: int = 0) -> str:
    opener = _persona_opener(persona, role, target, fee_low, fee_high, cov_total)
    if not opener:
        return ""
    return (
        "<div style='font-size:13px;color:#1A3D7C;background:rgba(61,90,130,0.06);"
        "border-left:3px solid #3D5A82;padding:9px 13px;border-radius:4px;margin-bottom:16px;'>"
        f"{_esc(opener)}</div>")


# --------------------------------------------------------------------------
# Output house-style: no em dashes (an AI tell), British spelling. Applied to
# the FINAL html/text so it also catches dynamic external text (news / annual-
# report excerpts), not only our own copy.
# --------------------------------------------------------------------------
def _de_em_dash(text: str) -> str:
    if not text:
        return text
    # Spaced em dash reads as an appositive/parenthetical -> comma.
    text = _re.sub(r"\s+—\s+", ", ", text)
    # Any remaining em dash / horizontal bar -> hyphen.
    text = _re.sub(r"[—―]", "-", text)
    # Tidy artefacts (" ," / doubled commas).
    text = _re.sub(r"\s+,", ",", text)
    text = _re.sub(r",\s*,", ",", text)
    return text


# American -> British. Deliberately excludes words that also appear in CSS or
# markup (color, center, program) so we never corrupt styles, classes or URLs.
_BRITISH = {
    "organize": "organise", "organizes": "organises", "organized": "organised",
    "organizing": "organising", "organization": "organisation",
    "organizations": "organisations", "organizational": "organisational",
    "optimize": "optimise", "optimizes": "optimises", "optimized": "optimised",
    "optimizing": "optimising", "optimization": "optimisation",
    "prioritize": "prioritise", "prioritizes": "prioritises",
    "prioritized": "prioritised", "prioritizing": "prioritising",
    "prioritization": "prioritisation",
    "specialize": "specialise", "specialized": "specialised",
    "specializing": "specialising", "specialization": "specialisation",
    "recognize": "recognise", "recognizes": "recognises",
    "recognized": "recognised", "recognizing": "recognising",
    "realize": "realise", "realizes": "realises", "realized": "realised",
    "realizing": "realising", "realization": "realisation",
    "maximize": "maximise", "maximized": "maximised", "maximizing": "maximising",
    "minimize": "minimise", "minimized": "minimised", "minimizing": "minimising",
    "analyze": "analyse", "analyzes": "analyses", "analyzed": "analysed",
    "analyzing": "analysing",
    "capitalize": "capitalise", "capitalized": "capitalised",
    "capitalizing": "capitalising", "capitalization": "capitalisation",
    "emphasize": "emphasise", "emphasized": "emphasised",
    "emphasizing": "emphasising",
    "summarize": "summarise", "summarized": "summarised",
    "customize": "customise", "customized": "customised",
    "standardize": "standardise", "standardized": "standardised",
    "categorize": "categorise", "categorized": "categorised",
    "utilize": "utilise", "utilized": "utilised", "utilization": "utilisation",
    "modernize": "modernise", "modernized": "modernised",
    "behavior": "behaviour", "behaviors": "behaviours",
    "behavioral": "behavioural", "favor": "favour", "favors": "favours",
    "favored": "favoured", "favorite": "favourite", "favorable": "favourable",
    "labor": "labour", "labors": "labours", "honor": "honour",
    "honored": "honoured", "defense": "defence", "offense": "offence",
    "catalog": "catalogue", "catalogs": "catalogues", "dialog": "dialogue",
    "analog": "analogue", "fulfill": "fulfil", "fulfills": "fulfils",
    "fulfillment": "fulfilment", "enrollment": "enrolment",
    "modeling": "modelling", "modeled": "modelled", "traveled": "travelled",
    "traveling": "travelling", "canceled": "cancelled",
    "canceling": "cancelling", "labeled": "labelled", "labeling": "labelling",
    "fiber": "fibre", "theater": "theatre",
}
_BRITISH_RX = _re.compile(
    r"\b(" + "|".join(sorted(_BRITISH, key=len, reverse=True)) + r")\b",
    _re.IGNORECASE)


def _to_british(text: str) -> str:
    if not text:
        return text

    def _repl(m):
        w = m.group(0)
        br = _BRITISH.get(w.lower())
        if not br:
            return w
        if w.isupper():
            return br.upper()
        if w[0].isupper():
            return br[0].upper() + br[1:]
        return br
    return _BRITISH_RX.sub(_repl, text)


def _sanitise_output(text: str, is_html: bool) -> str:
    """Apply house-style to the final output. For HTML, href/src values are
    masked first so a word inside a link URL (e.g. a news slug) is never
    rewritten and the link can't break."""
    if not text:
        return text
    saved: list[str] = []

    def _mask(m):
        saved.append(m.group(0))
        return f"\x00{len(saved) - 1}\x00"
    if is_html:
        # Protect link URLs and meta values (logo/domain) from rewriting.
        text = _re.sub(r'(?:href|src|content)="[^"]*"', _mask, text)
    # Protect bare URLs (the plain-text build prints article links inline).
    text = _re.sub(r"https?://\S+", _mask, text)
    text = _de_em_dash(text)
    text = _to_british(text)
    if saved:
        text = _re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], text)
    return text


def render_html(target: str, role: str, ch_snapshot: dict,
                news: list[dict], peers: list[str], sector: str | None,
                salary_band: tuple[int, int, str],
                cov: dict, mode: str,
                annual_report=None, curated_priorities: list[str] | None = None,
                peer_label: str = "", peer_source: str = "sector",
                sector_context: list[str] | None = None,
                persona: str = "", trigger_context: str = "") -> str:
    low, high, matched = salary_band
    mid = (low + high) // 2
    total_comp_mid = estimate_total_comp(mid)
    # Display label for the talent universe / sector field. The affinity
    # cohort (e.g. "Global consumer brands & FMCG") is sharper than the broad
    # ranker sector; fall back to the sector label, then a neutral note.
    universe_label = peer_label or (
        sector.replace("_", " ").title() if sector else "Sector unclear")

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

    # Section 2 — "Why this matters now". Clean, client-facing copy whatever the
    # source (bespoke annual-report quote > curated priorities > recent press >
    # sector-level context). No backstage confessions about the extraction
    # pipeline ("extraction unavailable", "check trade press manually") — the
    # buyer doesn't need to know how the line was sourced; the content stands.
    # The only internal cue is a clearly-marked prep note where one is genuinely
    # warranted (sector-level fallback / nothing resolved).
    if annual_report and annual_report.quotes:
        section2_heading = "2. Why this matters now"
        section2_subline = (
            f"In {_esc(target)}'s own words, from their {_esc(annual_report.filing_date)} "
            f"annual report:"
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
            f"{_esc(target)}'s publicly stated strategic priorities:"
            "</div>"
            "<ul style='padding-left:18px;font-size:13px;'>"
            + "".join(f"<li style='margin-bottom:8px;'>{_esc(p)}</li>" for p in curated_priorities)
            + "</ul>"
        )
    elif news:
        section2_heading = "2. Recent market context"
        news_html = (
            "<div style='font-size:13px;color:#555;margin-bottom:8px;'>"
            f"Recent coverage of {_esc(target)}:"
            "</div>"
            "<ul style='padding-left:18px;font-size:13px;'>"
        )
        for a in news[:5]:
            news_html += f"<li><a href='{_esc(a.get('url',''))}'>{_esc(a.get('title',''))}</a> <span style='color:#888;'>· {_esc(a.get('seendate','')[:8])}</span></li>"
        news_html += "</ul>"
    elif sector_context:
        # Never blank: a sector/cohort-level read of what's pulling senior
        # comms/marketing leaders into the market now. Presented as clean copy;
        # the only internal note flags it as sector-level so Sara adds a line.
        section2_heading = "2. Why this matters now"
        news_html = (
            "<div style='font-size:13px;color:#555;margin-bottom:8px;'>"
            f"What's driving senior {_NOUN} demand across {_esc(universe_label)} right now:"
            "</div>"
            "<ul style='padding-left:18px;font-size:13px;'>"
            + "".join(f"<li style='margin-bottom:8px;'>{_esc(c)}</li>" for c in sector_context)
            + "</ul>"
            + _prep_html(f"Sector-level context — add one {target}-specific line from "
                         "their latest results to make this bespoke.")
        )
    else:
        section2_heading = "2. Why this matters now"
        news_html = _prep_html(
            f"No company or sector context resolved for {target} — add a strategic line "
            "from their latest results or recent trade press before sending.")

    # NOTE: the old "2b. Client language to mirror" block has been removed. It
    # was a backstage instruction ("lift these phrases into your cover note")
    # that should never be visible to the client, and it surfaced mangled
    # fragments. Client-language mirroring is a tool for Sara's own prep, not
    # content for the buyer — it does not belong in the client-facing pack.
    client_lang_html = ""

    # Peer market map. The affinity cohort gives a tight, relevant move-from
    # set (brand houses for a drinks brand, not grocers). If only the generic
    # FTSE fallback resolved, REFUSE to show it — an irrelevant list ("your
    # candidates sit at BP") damages credibility more than an honest prompt.
    if peer_source == "generic":
        peer_html = (
            "<div style='font-size:13px;color:#a3690a;background:rgba(255,193,7,0.10);"
            "border-left:3px solid #d39e00;padding:8px 12px;border-radius:4px;'>"
            f"Sector not auto-detected for {_esc(target)} — the talent universe needs "
            "tailoring to this brief before sending. (Naming the right 12-15 comparable "
            "employers here is the section a sophisticated client reads most closely.)</div>"
        )
        reframe_html = ""
    else:
        peer_html = "<ol style='padding-left:18px;font-size:13px;'>"
        for p in peers:
            peer_html += f"<li>{_esc(p)}</li>"
        peer_html += "</ol>"
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

    # COV: buyer-shaped headline narrative + the defensible £ breakdown.
    cov_lines = cov.get("lines", {}) if isinstance(cov, dict) else {}
    cov_headline = cov.get("headline", "") if isinstance(cov, dict) else ""
    cov_html = (
        "<div style='font-size:13px;color:#333;margin-bottom:8px;'>"
        f"{_esc(cov_headline)}</div>"
        if cov_headline else ""
    )
    cov_html += "<table style='border-collapse:collapse;font-size:13px;'>"
    for k, v in cov_lines.items():
        bold = "font-weight:600;" if "leaving the seat empty" in k.lower() else ""
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
    sector_disp = universe_label
    # Fee on ESTIMATED TOTAL COMP (base + bonus/LTIP) — the same basis Section
    # 6 quotes, so the headline number no longer contradicts it or anchors the
    # client below VMA's real fee.
    fee_low = int(round(0.28 * total_comp_mid, -2))
    fee_high = int(round(0.33 * total_comp_mid, -2))
    cov_total = cov.get("total") if isinstance(cov, dict) else None
    snapshot_html = (
        "<div style='display:flex;flex-wrap:wrap;gap:14px 30px;font-size:13px;margin-bottom:10px;'>"
        f"<div><span style='color:#888;'>Sector</span><br><strong>{_esc(sector_disp)}</strong></div>"
        "<div><span style='color:#888;'>Indicative retained fee</span><br>"
        f"<strong>{_fmt_gbp(fee_low)}–{_fmt_gbp(fee_high)}</strong> "
        "<span style='color:#888;font-size:11px;'>(28–33% of est. first-year total comp — "
        f"base + typical bonus/LTIP; total comp ≈ {_fmt_gbp(total_comp_mid)})</span></div>"
        + (("<div><span style='color:#888;'>Cost of an empty seat</span><br>"
            f"<strong>{_fmt_gbp(cov_total)}</strong> "
            "<span style='color:#888;font-size:11px;'>(see Section 3)</span></div>") if cov_total else "")
        + "</div>"
        "<div style='font-size:13px;color:#444;'>"
        f"<span style='color:#888;'>Pipeline:</span> a senior {_NOUN} placement typically opens "
        "<strong>2–4 follow-on hires</strong> over 12–18 months — a retained engagement positions "
        "VMA for the full pipeline, not just the headline role.</div>"
        f"{ch_footer}"
    )

    note_banner = ""
    if mode == "test":
        note_banner = "<div style='background:#fff3cd;border:1px solid #ffeaa7;padding:8px;margin-bottom:16px;font-size:13px;'>⚠️ TEST PACK - generated for Amir's review. Do not send to client.</div>"

    persona_html = _persona_html(persona, role, target, fee_low, fee_high, cov_total)
    # The fee-vs-cost juxtaposition — the line a finance reader converts on —
    # rendered in §3 for every reader (the CFO persona block leads with it too).
    _fvc = _fee_vs_cost_phrase(fee_low, fee_high, cov_total)
    fee_vs_cost_html = (
        f"<div style='font-size:13px;color:#1A3D7C;font-weight:600;margin-top:8px;'>"
        f"Put together: {_fvc} — small against the problem it solves.</div>" if _fvc else "")
    # §8 "why now" leads with the specific live event when one is supplied
    # (PITCH_TRIGGER) — e.g. Diageo's 6 Aug Strategy Update — so the argument is
    # concrete, not generic, and ties back to Section 2.
    why_now_event_li = (
        f"<li><strong>{_esc(trigger_context)}</strong> — and no {_esc(role)} in seat to own it; "
        "the cost of that gap runs from today, not from when you start looking.</li>"
        if trigger_context else "")
    salary_period = datetime.now().strftime("%B %Y")
    cov_weeks = cov.get("weeks", TIME_TO_PRODUCTIVE_WEEKS) if isinstance(cov, dict) else TIME_TO_PRODUCTIVE_WEEKS
    # Section 4 intro — relevant when we have a real cohort; honest prompt when
    # only the generic fallback resolved (peer_html already carries the warning).
    if peer_source == "generic":
        universe_intro = ""
    else:
        universe_intro = (
            "<div style='font-size:13px;color:#555;margin-bottom:6px;'>"
            f"Where the candidate pool sits — the realistic move-from set for a "
            f"{_esc(matched)}-level hire across {_esc(universe_label.lower())}.</div>"
        )
    # Reconcile the 6-week search with the cost-of-vacancy window so the pack
    # never again bills "16 weeks vacant" next to "offer by week 6".
    methodology_note = (
        "<div style='font-size:12px;color:#888;margin-top:6px;'>"
        f"The 6-week timeline is brief-to-offer — the part a retained search controls. "
        f"Senior notice periods then apply, so plan ~{cov_weeks} weeks to a productive "
        f"start; the cost of vacancy in Section 3 is measured over that period, which is "
        f"why every week of delay compounds and starting the search now matters."
        "</div>"
    )

    # Salary benchmark: hedge rather than assert an unsourced range (a sharp
    # reward/HR buyer will poke it).
    salary_source_html = (" <span style='color:#888;'>Indicative — confirm against VMA's "
                          "latest salary benchmarking before quoting.</span>")

    # ---- Section 7: Why external retained search NOW ----------------------
    # The real competition in a down market isn't contingent rivals — it's the
    # client doing it in-house, on LinkedIn, or not at all. This answers all
    # three: why external, why retained, why now.
    why_now_html = f"""
    <div style='font-size:13px;color:#333;'>
      <div style='font-weight:600;margin:2px 0 4px;'>Why an external search, not in-house</div>
      <ul style='padding-left:18px;margin:0 0 10px;'>
        <li><strong>Reach:</strong> the strongest {_NOUN} leaders aren't applying — they're in seat elsewhere and won't answer a job ad or an approach with your name on it. Reaching them is the job.</li>
        <li><strong>Discretion:</strong> a named third party runs the search confidentially — essential when an incumbent is still in post or the brief is sensitive.</li>
        <li><strong>Brand-neutral approach:</strong> we can sound out your competitors' people with your name off the table until they're warm, which an in-house team structurally can't.</li>
        <li><strong>Calibration:</strong> a benchmarked, market-mapped longlist — not whoever happens to apply — assessed against the brief before you spend interview time.</li>
      </ul>
      <div style='font-weight:600;margin:2px 0 4px;'>Why retained, not contingent</div>
      <ul style='padding-left:18px;margin:0 0 10px;'>
        <li>Exclusivity unlocks deeper passive outreach (~3× the universe a contingent firm will work)</li>
        <li>Milestone fees align our priority with yours — we're not racing six firms on the same role</li>
        <li>If you already have a supplier: retained means a market-mapped longlist assessed against the brief, not a CV race, plus the off-limits protection and replacement guarantee an ad-hoc supplier rarely carries</li>
        <li>Senior {_NOUN} placements typically open 2–4 downstream hires ({_DOWNSTREAM_EXAMPLES}) over 12–18 months, so retained positions VMA for the full pipeline, not just the headline role</li>
      </ul>
      <div style='font-weight:600;margin:2px 0 4px;'>Why now, not when the market picks up</div>
      <ul style='padding-left:18px;margin:0;'>
        {why_now_event_li}
        <li>The cost-of-vacancy clock (Section 3) runs every week the seat is open; notice periods mean ~{cov_weeks} weeks to a productive start even on a fast search</li>
        <li>In a quieter market the strongest leaders are reachable precisely because fewer firms are hunting them — "wait until it picks up" is backwards; the window is now</li>
      </ul>
    </div>
    """

    return f"""<!doctype html>
<html><head><meta charset="utf-8"></head><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:760px;margin:0 auto;padding:20px;color:#111;">
{note_banner}
<h2 style="margin:0 0 4px 0;">Retained Pitch Pack - {_esc(target)}</h2>
<div style="color:#666;font-size:13px;margin-bottom:18px;">
  Role: {_esc(role)}
</div>
<hr style="border:none;border-top:2px solid #3D5A82;margin:14px 0 24px;">
{persona_html}
<h3 style="margin:18px 0 6px 0;">1. Account snapshot</h3>
{snapshot_html}

<h3 style="margin:18px 0 6px 0;">{section2_heading}</h3>
{news_html}

<h3 style="margin:18px 0 6px 0;">3. Cost of vacancy</h3>
<div style='font-size:13px;color:#444;margin-bottom:6px;'>
  Base salary assumed: <strong>{_fmt_gbp(mid)}</strong> (est. total comp <strong>{_fmt_gbp(total_comp_mid)}</strong>). Override if the brief is at a different level.
</div>
{cov_html}
{fee_vs_cost_html}

<h3 style="margin:18px 0 6px 0;">4. Talent universe — {_esc(universe_label)}</h3>
{reframe_html}
{universe_intro}
{peer_html}

<h3 style="margin:18px 0 6px 0;">5. Salary benchmark</h3>
<div style='font-size:13px;'>
  UK {_esc(salary_period)} range for <strong>{_esc(matched)}</strong>: <strong>{_fmt_gbp(low)}–{_fmt_gbp(high)}</strong> base, plus 10–25% bonus / LTIP at FTSE-listed level (est. total comp ≈ <strong>{_fmt_gbp(total_comp_mid)}</strong>).{salary_source_html}
</div>

<h3 style="margin:18px 0 6px 0;">6. 6-week retained methodology</h3>
{methodology_html}
{methodology_note}
<div style='font-size:12px;color:#888;margin-top:6px;'>
  Retained fee: 28–33% of first-year total comp, in thirds at engagement / shortlist / accepted offer.
  Versus contingent at 22–25% of base only.
</div>

<h3 style="margin:18px 0 6px 0;">7. Why external retained search now</h3>
{why_now_html}

<h3 style="margin:18px 0 6px 0;">8. Risk-mitigation terms</h3>
<div style='font-size:13px;color:#333;'>
  Standard rebate schedule, off-limits clause, exclusivity period, and replacement guarantee per VMA Group's terms of engagement, provided separately as part of the contract pack.
</div>

</body></html>
"""


def render_text(target: str, role: str, ch_snapshot: dict,
                news: list[dict], peers: list[str], sector: str | None,
                salary_band: tuple[int, int, str], cov: dict,
                annual_report=None, curated_priorities: list[str] | None = None,
                peer_label: str = "", peer_source: str = "sector",
                sector_context: list[str] | None = None,
                persona: str = "", trigger_context: str = "") -> str:
    low, high, matched = salary_band
    mid = (low + high) // 2
    total_comp_mid = estimate_total_comp(mid)
    universe_label = peer_label or (
        sector.replace("_", " ").title() if sector else "Sector unclear")
    _fee_low = int(round(0.28 * total_comp_mid, -2))
    _fee_high = int(round(0.33 * total_comp_mid, -2))
    _cov_total = cov.get("total") if isinstance(cov, dict) else None
    lines = [
        f"Retained Pitch Pack - {target}",
        f"Role: {role}",
        "=" * 60, ""]
    _persona_line = _persona_opener(persona, role, target, _fee_low, _fee_high, _cov_total or 0)
    if _persona_line:
        for _seg in textwrap.wrap(_persona_line, 72):
            lines.append(_seg)
        lines.append("")
    lines += ["1. ACCOUNT SNAPSHOT"]
    lines.append(f"   Sector: {universe_label}")
    lines.append(f"   Indicative retained fee: £{_fee_low:,}–£{_fee_high:,} "
                 f"(28–33% of est. first-year total comp ≈ £{total_comp_mid:,}; base + bonus/LTIP)")
    if _cov_total:
        lines.append(f"   Cost of an empty seat: £{_cov_total:,} (see section 3)")
    lines.append(f"   Pipeline: a senior {_NOUN} placement typically opens 2–4 follow-on hires over 12–18 months.")
    if ch_snapshot.get("found"):
        resolved = ch_snapshot.get("resolved") or {}
        lines.append(f"   Companies House: {resolved.get('company_number','?')} · {resolved.get('company_status','?')} "
                     "— confirm this is the right entity before sending")
    else:
        lines.append("   UK entity not resolved (non-UK-registered or trading-name only).")

    if annual_report and annual_report.quotes:
        lines += ["", f"2. WHY THIS MATTERS NOW  ({target}'s own words, {annual_report.filing_date} annual report)"]
        for q in annual_report.quotes:
            lines.append(f"   - \"{q.text}\"")
            lines.append(f"     [{q.heading}, p.{q.page}]")
    elif curated_priorities:
        lines += ["", f"2. WHY THIS MATTERS NOW  ({target}'s publicly stated strategic priorities)"]
        for p in curated_priorities:
            lines.append(f"   - {p}")
    elif news:
        lines += ["", f"2. RECENT MARKET CONTEXT  (recent coverage of {target})"]
        for a in news[:5]:
            lines.append(f"   - {a.get('title','')} ({a.get('seendate','')[:8]})")
            lines.append(f"     {a.get('url','')}")
    elif sector_context:
        lines += ["", "2. WHY THIS MATTERS NOW",
                  f"   What's driving senior {_NOUN} demand across {universe_label}:"]
        for c in sector_context:
            lines.append(f"   - {c}")
        lines.append(f"   [prep note - remove before sending: add one {target}-specific line.]")
    else:
        lines += ["", "2. WHY THIS MATTERS NOW",
                  f"   [prep note - remove before sending: no context resolved for {target};",
                  "    add a strategic line from their latest results or recent trade press.]"]

    # (The old "2b. Client language to mirror" block is removed — it was a
    # backstage instruction that should never appear in the client-facing pack.)

    _cov_lines = cov.get("lines", {}) if isinstance(cov, dict) else {}
    _cov_headline = cov.get("headline", "") if isinstance(cov, dict) else ""
    _cov_weeks = cov.get("weeks", TIME_TO_PRODUCTIVE_WEEKS) if isinstance(cov, dict) else TIME_TO_PRODUCTIVE_WEEKS
    lines += ["", "3. COST OF VACANCY",
              f"   Base £{mid:,} (est. total comp £{total_comp_mid:,})."]
    if _cov_headline:
        for _seg in textwrap.wrap(_cov_headline, 72):
            lines.append(f"   {_seg}")
    for k, v in _cov_lines.items():
        lines.append(f"   {k:<58}  £{v:>10,}")
    _fvc = _fee_vs_cost_phrase(_fee_low, _fee_high, _cov_total or 0)
    if _fvc:
        for _seg in textwrap.wrap(f"Put together: {_fvc} - small against the problem it solves.", 72):
            lines.append(f"   {_seg}")
    _salary_period = datetime.now().strftime("%B %Y")
    if peer_source == "generic":
        lines += ["", f"4. TALENT UNIVERSE ({universe_label})",
                  f"   Sector not auto-detected for {target} — name the right 12-15 comparable",
                  "   employers for this brief before sending (the section clients read closely)."]
    else:
        lines += ["", f"4. TALENT UNIVERSE — {universe_label}",
                  f"   If the brief reads as a quiet market: the profile isn't scarce, it's placed.",
                  f"   The {len(peers)} named employers below are where a {matched} sits today —",
                  f"   reaching them before a competitor does is what a retained search buys."]
        for i, p in enumerate(peers, 1):
            lines.append(f"   {i:>2}. {p}")
    _sal_note = "Indicative - confirm against VMA's latest salary benchmarking before quoting."
    lines += ["", "5. SALARY BENCHMARK",
              f"   UK {_salary_period} range for {matched}: £{low:,}–£{high:,} base + 10–25% bonus/LTIP",
              f"   (est. first-year total comp ≈ £{total_comp_mid:,})",
              f"   {_sal_note}",
              "", "6. 6-WEEK METHODOLOGY",
              "   Wk 1   Briefing + market pack                       (1/3 on engagement)",
              "   Wk 2–3 Universe mapped + longlist of named candidates",
              "   Wk 3–4 Outreach + shortlist of 5–7                  (1/3 on shortlist)",
              "   Wk 4–5 Client interviews + finals",
              "   Wk 6   Offer + onboarding handover                  (1/3 on accepted offer)",
              f"   (6 wks is brief-to-offer; with notice periods plan ~{_cov_weeks} wks to a",
              "    productive start — the cost-of-vacancy window, which is why starting now matters)",
              "", "Retained fee: 28–33% of first-year total comp (vs 22–25% contingent on base only).",
              ""]

    # 7. Why external retained search NOW (the real competition: in-house / wait).
    lines += [
              "7. WHY EXTERNAL RETAINED SEARCH NOW",
              "   Why external, not in-house:",
              f"   - Reach: the strongest {_NOUN} leaders aren't applying — they're in seat",
              "     elsewhere and won't answer a job ad or an approach with your name on it",
              "   - Discretion: a named third party runs the search confidentially",
              "   - Brand-neutral approach: sound out competitors' people with your name off",
              "     the table until they're warm — an in-house team structurally can't",
              "   - Calibration: a benchmarked, market-mapped longlist, not whoever applies",
              "   Why retained, not contingent:",
              "   - Exclusivity unlocks ~3x the passive universe a contingent firm will work",
              "   - Milestone fees align priority — not racing six firms on the same role",
              "   - If you already have a supplier: retained is a market-mapped longlist, not a",
              "     CV race, with off-limits protection and a replacement guarantee an ad-hoc",
              "     supplier rarely carries",
              f"   - Senior {_NOUN} placements open 2-4 downstream hires over 12-18 months",
              "   Why now, not when the market picks up:"]
    if trigger_context:
        _ev = (f"{trigger_context} - and no {role} in seat to own it; "
               "the cost of that gap runs from today.")
        for _i, _seg in enumerate(textwrap.wrap(_ev, 68)):
            lines.append(f"   - {_seg}" if _i == 0 else f"     {_seg}")
    lines += [
              f"   - Cost-of-vacancy clock runs weekly; ~{_cov_weeks} wks to a productive start",
              "   - In a quiet market the best leaders are reachable because fewer firms hunt",
              "     them — 'wait until it picks up' is backwards; the window is now",
              "",
              "8. RISK-MITIGATION TERMS",
              "   Standard rebate schedule, off-limits clause, exclusivity period, and",
              "   replacement guarantee per VMA Group's terms of engagement, provided",
              "   separately as part of the contract pack."]
    return "\n".join(lines)


def _run_comms_proposal(target: str, role: str, mode: str) -> int:
    """Comms desk pitch pack: the branded, client-facing VMA "Search Proposal"
    PDF (tool/pitch_proposal). The target company's own logo, the predicted
    seat and the generation date are wired onto the cover, and the company name
    replaces the template's example client throughout. Saves a .pdf into the
    per-profile state dir and, in send/test mode, emails it as an attachment.

    This is the comms-only output (the marketing desk keeps the dynamic pack in
    main()). Returns a process exit code."""
    from tool import pitch_proposal
    log.info("Building comms SEARCH PROPOSAL PDF for %r · seat %r · mode %r",
             target, role, mode)
    # The cover logo is pulled from the target company's own website, validated
    # and confidence-gated (never a wrong logo). If no confident logo is found,
    # generate() falls back to a clean text wordmark, so a pack is always
    # produced for whatever company a BD lead names.
    pdf_bytes = pitch_proposal.generate(target, role)
    safe = "".join(c if c.isalnum() else "_" for c in target.lower())[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    pdf_path = STATE_DIR / f"pitch_pack_{safe}_{stamp}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    log.info("Proposal PDF: %d bytes -> %s", len(pdf_bytes), pdf_path)

    if mode in ("send", "test") and getattr(config, "NON_BRIEF_EMAIL_ENABLED", False):
        to = config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT
        subject = f"[Pitch Pack] {target} - {role}"
        if mode == "test":
            subject = "[TEST] " + subject
        body_html = (
            f"<p>Please find attached the VMA Group search proposal for "
            f"<strong>{html.escape(target)}</strong> &ndash; {html.escape(role)}.</p>"
            "<p>Strictly private &amp; confidential.</p>")
        body_text = (f"Please find attached the VMA Group search proposal for "
                     f"{target} - {role}.\n\nStrictly private & confidential.")
        result = email_send(to, subject, body_html, body_text,
                            attachments=[(pdf_path.name, pdf_bytes, "application/pdf")])
        log.info("Send: %s", result)
        if not result.get("ok"):
            print("\n--- EMAIL SEND FAILED ---")
            print(result)
            return 2
        print(f"✓ Sent proposal to {to}.")
        return 0

    print(f"[saved comms proposal PDF to tool/state/{pdf_path.name}]")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(f'Usage: python -m tool.pitch_pack "<account name>" [role="{_DEFAULT_ROLE}"] [mode=preview|send|test]', file=sys.stderr)
        return 2

    target = sys.argv[1].strip()
    role = os.environ.get("PITCH_ROLE", _DEFAULT_ROLE)
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

    # Comms desk: the client-facing pitch pack is the branded VMA "Search
    # Proposal" PDF. Marketing keeps the dynamic, data-driven pack below.
    # (Wired for comms only, per the current scope.)
    if not _MKT:
        return _run_comms_proposal(target, role, mode)

    ch = companies_house.company_events(target)
    news = recent_news_for(target)
    # Pitch-Pack talent universe: affinity cohort > sector > guarded generic.
    peer_meta = pitch_peers_for(target, k=15)
    peers = peer_meta["peers"]
    sector = detect_sector(target)
    log.info("talent universe: source=%s label=%r (%d peers)",
             peer_meta["source"], peer_meta["label"], len(peers))

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
    # Optional event anchor (e.g. "half-year results eight weeks out"). Manual
    # packs leave it blank (frame-generic headline); a pack fired FROM a BD lead
    # can pass the trigger via PITCH_TRIGGER so the cost-of-vacancy speaks to the
    # specific live event — the comms COV and the lead are the same seam.
    trigger_context = (os.environ.get("PITCH_TRIGGER") or "").strip()
    cov = cost_of_vacancy(role, mid, trigger_context=trigger_context)

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

    # Sector/cohort strategic context — the never-blank final rung for Section 2
    # when neither the annual report nor curated priorities resolve.
    sector_ctx = None
    if not (annual_rep and annual_rep.quotes) and not curated_priorities:
        try:
            from tool import sector_context as _sc
            sector_ctx = _sc.strategic_context(
                peer_meta.get("key"),
                "marketing" if _MKT else "comms")
        except Exception as e:
            log.info("sector-context fallback failed: %s", e)

    # Optional audience persona (ceo / cfo / incoming / hr) — reframes the
    # opening for the reader. Manual packs can leave it blank.
    persona = (os.environ.get("PITCH_PERSONA") or "").strip()
    _render_kw = dict(annual_report=annual_rep, curated_priorities=curated_priorities,
                      peer_label=peer_meta["label"], peer_source=peer_meta["source"],
                      sector_context=sector_ctx, persona=persona,
                      trigger_context=trigger_context)
    html_out = render_html(target, role, ch, news, peers, sector, sal, cov, mode,
                            **_render_kw)
    text_out = render_text(target, role, ch, news, peers, sector, sal, cov,
                            **_render_kw)

    # House-style the final output: no em dashes, British spelling (also
    # catches dynamic news / annual-report text). HTML link URLs are protected.
    html_out = _sanitise_output(html_out, is_html=True)
    text_out = _sanitise_output(text_out, is_html=False)

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
