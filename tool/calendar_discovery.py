"""Discovery lanes for the BD-Calendar tools.

Finds NEW items for the three BD-Calendar tools from real, public, free
sources — so Placement Windows, Events & Networking and Framework
Eligibility auto-update like Today's Leads / Pre-Market, instead of only
showing the hand-curated seeds.

Each lane returns a list of item dicts shaped to match that tool's
existing seed schema (so a discovered item renders identically to a
curated one), each carrying:
    key            stable id (sha1-based, dedups across days)
    discovered     True (so the UI can badge auto-found items)
    source / url   provenance for Sara to verify

Precision-first and fully non-fatal: every lane wraps its fetch in
try/except and returns [] on any failure (a dead feed, a parse error,
egress 403 in the sandbox). Nothing here can break the morning brief or
the dashboard — worst case a lane contributes nothing and the curated
seeds still show.

The detectors are deliberately conservative keyword gates over public RSS
titles/summaries: high precision beats recall here, because a noisy BD
calendar is worse than a sparse one. The curated seeds remain the
guaranteed baseline; discovery only ever ADDS.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape

from tool.config import SOURCES
from tool.sources._http import get, parse_rss, signal_id

log = logging.getLogger("brief.caldiscovery")

_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")


def _clean(s: str) -> str:
    return _WS_RX.sub(" ", unescape(_TAG_RX.sub(" ", s or ""))).strip()


def _key(prefix: str, payload: str) -> str:
    return f"{prefix}_{signal_id(prefix, payload)}"


def _parse_when(published: str):
    """Parse an RSS published date to a date; None if unusable."""
    if not published:
        return None
    try:
        from dateutil import parser as dateparse
        dt = dateparse.parse(published)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.date()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 1 · FRAMEWORK ELIGIBILITY — public-sector exec-search framework notices.
# Find a Tender (and the gov CMA atom feed) publish procurement notices;
# we surface ones about EXECUTIVE SEARCH / RECRUITMENT frameworks, which is
# exactly the "eligibility to bid" groundwork this tool tracks.
# ---------------------------------------------------------------------------
_FW_INCLUDE = re.compile(
    r"\b(executive search|recruitment|resourcing|search and selection|"
    r"permanent recruitment|interim management|talent acquisition|"
    r"managed service|staffing)\b", re.I)
_FW_RELEVANT = re.compile(
    r"\b(executive search|senior|leadership|director|recruitment framework|"
    r"managed service|comms|communications|corporate affairs|public sector)\b",
    re.I)
# Drop obvious non-comms commodity recruitment (clinical/teaching agency
# staffing) — keep leadership/exec-search framework signals.
_FW_EXCLUDE = re.compile(
    r"\b(locum|agency nursing|nursing|teaching staff|supply teacher|"
    r"social work agency|domiciliary|catering|cleaning|security guard)\b",
    re.I)


def discover_frameworks() -> list[dict]:
    """Find newly-published exec-search / recruitment FRAMEWORK notices from
    Find a Tender. Shaped to framework_watch's row schema."""
    out: list[dict] = []
    url = SOURCES.get("find_a_tender")
    if not url:
        return out
    try:
        r = get(url)
        if not r or not r.content:
            return out
        items = parse_rss(r.content)
    except Exception as e:
        log.info("framework discovery fetch failed: %s", e)
        return out

    seen: set[str] = set()
    for it in items:
        title = _clean(it.get("title"))
        summary = _clean(it.get("summary"))
        text = f"{title} . {summary}"
        if not title:
            continue
        if not _FW_INCLUDE.search(text):
            continue
        if _FW_EXCLUDE.search(text) and not re.search(r"executive search", text, re.I):
            continue
        if not _FW_RELEVANT.search(text):
            continue
        link = it.get("link") or url
        key = _key("fw", link or title)
        if key in seen:
            continue
        seen.add(key)
        # Estimate the agreement end from the notice date + a typical 4-year
        # framework term when no explicit end is parseable (honest estimate,
        # flagged as such — same convention as the curated seeds).
        pub = _parse_when(it.get("published"))
        exp = (pub or date.today()).replace(year=(pub or date.today()).year + 4)
        out.append({
            "key": key,
            "title": title[:120],
            "ad_title": title[:120],
            "ad_desc": (summary[:200] or "Public-sector recruitment / "
                        "executive-search framework notice."),
            "code": "",
            "buyer": "See notice",
            "scope": summary[:200] or title[:120],
            "comms_relevant": bool(re.search(
                r"comms|communications|corporate affairs|executive search|"
                r"senior|leadership", text, re.I)),
            "expiry_date": exp.isoformat(),
            "date_confidence": "estimate",
            "portal": link,
            "notes": "Auto-discovered from Find a Tender — verify scope, lots "
                     "and dates on the portal.",
            "source": "Find a Tender",
            "url": link,
            "discovered": True,
        })
    log.info("framework discovery: %d candidate notice(s)", len(out))
    return out


# ---------------------------------------------------------------------------
# 2 · PLACEMENT WINDOWS — regulator/statutory reporting & consultation dates.
# UK regulators (FCA/Ofwat/Ofcom/Ofgem/CMA) publish consultations, policy
# statements and reporting deadlines that force a comms-capacity build-up
# in a defined cohort. We surface ones whose title implies a dated
# obligation, and derive an ACTION window ending on that date.
# ---------------------------------------------------------------------------
_REG_FEEDS = {
    "FCA": ("fca_news", ["financial_services"]),
    "Ofwat": ("ofwat_news", ["energy_utilities"]),
    "Ofcom": ("ofcom_news", ["media_telecoms"]),
    "Ofgem": ("ofgem_news", ["energy_utilities"]),
    "CMA": ("cma_news", ["financial_services", "energy_utilities"]),
}
_PW_TRIGGER = re.compile(
    r"\b(consultation|policy statement|reporting|annual report|disclosure|"
    r"deadline|requirements?|framework|review|implementation|compliance|"
    r"price control|business plan|sustainability)\b", re.I)
_PW_DATE = re.compile(
    r"\b(\d{1,2}\s+"
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+20\d{2})\b", re.I)


def discover_windows() -> list[dict]:
    """Find newly-published regulator obligations that create a dated
    comms-capacity window. Shaped to calendar_pulses' active-pulse schema."""
    out: list[dict] = []
    today = date.today()
    for reg, (skey, sectors) in _REG_FEEDS.items():
        url = SOURCES.get(skey)
        if not url:
            continue
        try:
            r = get(url)
            if not r or not r.content:
                continue
            items = parse_rss(r.content)
        except Exception as e:
            log.info("window discovery fetch failed (%s): %s", reg, e)
            continue
        for it in items[:40]:
            title = _clean(it.get("title"))
            summary = _clean(it.get("summary"))
            text = f"{title} . {summary}"
            if not title or not _PW_TRIGGER.search(text):
                continue
            # Anchor the action window: prefer an explicit future date in the
            # text, else the publication date + a 12-week build-up.
            legal_date = None
            m = _PW_DATE.search(text)
            if m:
                ld = _parse_when(m.group(1))
                if ld and ld >= today:
                    legal_date = ld
            pub = _parse_when(it.get("published")) or today
            if legal_date is None:
                legal_date = pub + timedelta(weeks=12)
            if legal_date < today:
                continue  # obligation already passed — not actionable
            win_start = max(today, legal_date - timedelta(weeks=12))
            link = it.get("link") or url
            key = _key("pw", link or title)
            # RAW shape — identical to a calendar_pulses.PULSES entry, so the
            # existing live decorator (active_pulses) computes days_left etc.
            out.append({
                "key": key,
                "name": f"{reg}: {title}"[:120],
                "window": [win_start.isoformat(), legal_date.isoformat()],
                "legal_date": legal_date.isoformat(),
                "sectors": sectors,
                "seat": "Head of Regulatory / Corporate Communications",
                "angle": (f"{reg} obligation creates a dated comms-capacity "
                          "build-up in the regulated cohort — pitch the "
                          "retained brief before it's advertised."),
                "scope_note": f"{reg}-regulated firms in scope of this notice.",
                "confidence": "medium",
                "source": f"{reg} — {summary[:80]}" if summary else reg,
                "url": link,
                "discovered": True,
            })
    log.info("window discovery: %d regulator obligation(s)", len(out))
    return out


# ---------------------------------------------------------------------------
# 3 · EVENTS & NETWORKING — comms awards / conferences from trade-press +
# Google News RSS. Surface items whose title names a comms award/conference
# with a parseable future date.
# ---------------------------------------------------------------------------
_EV_TRIGGER = re.compile(
    r"\b(awards?|conference|summit|forum|festival|symposium|gala|"
    r"ceremony|expo)\b", re.I)
_EV_COMMS = re.compile(
    r"\b(comms|communications?|public relations|\bPR\b|internal comms|"
    r"corporate affairs|marketing|reputation|public affairs|engagement)\b",
    re.I)
_EV_NEWS_QUERIES = [
    '"communications" (awards OR conference OR summit) 2026',
    '"internal communications" (conference OR awards) 2026',
    '"public relations" (awards OR conference) UK 2026',
    '"corporate communications" summit 2026',
]


def _events_from_items(items, src_label: str, today: date) -> list[dict]:
    out: list[dict] = []
    for it in items:
        title = _clean(it.get("title"))
        summary = _clean(it.get("summary"))
        text = f"{title} . {summary}"
        if not title or not _EV_TRIGGER.search(text) or not _EV_COMMS.search(text):
            continue
        m = _PW_DATE.search(text)
        ev_date = _parse_when(m.group(1)) if m else None
        if not ev_date or ev_date < today or (ev_date - today).days > 365:
            continue
        link = it.get("link") or ""
        key = _key("ev", (title.lower()[:60]) or link)
        action_start = max(today, ev_date - timedelta(weeks=8))
        # RAW shape — identical to a calendar_pulses.INDUSTRY_EVENTS entry, so
        # active_events computes days_to_event / in_action_window live.
        out.append({
            "key": key,
            "name": title[:120],
            "event_date": ev_date.isoformat(),
            "action_window": [action_start.isoformat(), ev_date.isoformat()],
            "location": "See listing",
            "focus": "internal" if re.search(r"internal", text, re.I) else "external",
            "why_now": (summary[:160] or "Comms industry event — finalists / "
                        "speakers skew senior in-house comms."),
            "source": link or src_label,
            "url": link,
            "discovered": True,
        })
    return out


def discover_events() -> list[dict]:
    """Find comms awards/conferences with future dates from trade-press RSS
    + Google News. Shaped to calendar_pulses' active-event schema."""
    out: list[dict] = []
    today = date.today()
    seen: set[str] = set()

    # Trade-press feeds already configured for the brief.
    for skey in ("corpcomms", "prmoment", "cipr_influence", "ragan"):
        url = SOURCES.get(skey)
        if not url:
            continue
        try:
            r = get(url)
            if not r or not r.content:
                continue
            for ev in _events_from_items(parse_rss(r.content)[:40], skey, today):
                if ev["key"] not in seen:
                    seen.add(ev["key"]); out.append(ev)
        except Exception as e:
            log.info("event discovery fetch failed (%s): %s", skey, e)

    # Google News RSS — keyless, no per-query rate wall.
    gn = "https://news.google.com/rss/search"
    for q in _EV_NEWS_QUERIES:
        try:
            r = get(gn, params={"q": q, "hl": "en-GB", "gl": "GB", "ceid": "GB:en"})
            if not r or not r.content:
                continue
            for ev in _events_from_items(parse_rss(r.content)[:20], "Google News", today):
                if ev["key"] not in seen:
                    seen.add(ev["key"]); out.append(ev)
        except Exception as e:
            log.info("event discovery gnews failed (%s): %s", q, e)

    log.info("event discovery: %d comms event(s)", len(out))
    return out


# ---------------------------------------------------------------------------
# Orchestrator — called by the morning brief (and the manual refresh).
# ---------------------------------------------------------------------------
def discover(kind: str) -> list[dict]:
    if kind == "frameworks":
        return discover_frameworks()
    if kind == "windows":
        return discover_windows()
    if kind == "events":
        return discover_events()
    return []


def _curated_seeds(kind: str) -> list[dict]:
    """The hand-curated baseline entries for a kind, as RAW pipeline items
    (NOT date-decorated — the dashboard re-decorates live on each load so
    days_left / status labels stay correct as the date advances). These are
    seeded into the pipeline every scour so the panel always has its
    trusted baseline; discovery only ADDS to it.

    Tuple windows are JSON-normalised to lists (json.dump turns tuples into
    lists anyway; doing it here keeps the in-memory + persisted shapes
    identical so re-decoration is uniform)."""
    try:
        if kind == "frameworks":
            from tool.framework_watch import FRAMEWORKS
            return [dict(f) for f in FRAMEWORKS]
        if kind == "windows":
            from tool.calendar_pulses import PULSES
            seeds = []
            for p in PULSES:
                d = dict(p)
                w = d.get("window")
                if isinstance(w, (list, tuple)) and len(w) == 2:
                    d["window"] = [w[0], w[1]]
                seeds.append(d)
            return seeds
        if kind == "events":
            from tool.calendar_pulses import INDUSTRY_EVENTS
            seeds = []
            for e in INDUSTRY_EVENTS:
                d = dict(e)
                aw = d.get("action_window")
                if isinstance(aw, (list, tuple)) and len(aw) == 2:
                    d["action_window"] = [aw[0], aw[1]]
                seeds.append(d)
            return seeds
    except Exception as e:
        log.info("curated seeds load failed (%s): %s", kind, e)
    return []


def refresh_all() -> dict:
    """Seed curated baselines + discovered items into all three calendar
    pipelines. Called by the morning brief (cron) and the manual refresh
    endpoint. Returns a per-kind summary. Fully non-fatal per kind."""
    from tool import calendar_pipeline
    summary: dict[str, dict] = {}
    for kind in ("windows", "events", "frameworks"):
        try:
            found = _curated_seeds(kind) + discover(kind)
            summary[kind] = calendar_pipeline.upsert(kind, found)
        except Exception as e:
            log.info("calendar refresh failed (%s): %s", kind, e)
            summary[kind] = {"new": [], "updated": 0, "error": str(e)}
    return summary
