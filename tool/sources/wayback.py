"""Wayback Machine careers-page diffing — pre-announcement leader departures.

The single highest-craft free signal in the assessment: a senior comms /
marketing leader is usually removed from their employer's leadership / team
page WEEKS before the departure is announced anywhere. Diffing the current
page against an archived snapshot from ~N days ago surfaces exactly that —
a named senior leader present in the OLD snapshot and ABSENT from the
current page = a likely departure, emitted as a comms_leader_departure
event (the system's highest-yield trigger) keyed on their employer.

All free: the Internet Archive CDX API (find the nearest old snapshot) +
the archived + live page fetches. No key. Per-company team-page URLs are
seeded in TEAM_PAGES (extend freely); without a reachable page the company
is skipped. Fully non-fatal.

The name extractor is deliberately conservative (a Title-Case full name
sitting next to a senior comms/marketing role phrase) to keep precision
high — and it is unit-tested against static HTML, so the diff logic is
verified without any network.
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

from tool.predictive.detector import TriggerEvent
from tool.sources._http import get, signal_id

log = logging.getLogger("brief.wayback")

CDX_API = "http://web.archive.org/cdx/search/cdx"
WAYBACK_BASE = "http://web.archive.org/web"

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# How far back to fetch the comparison snapshot. ~45 days is past the
# average time a departed leader lingers on a page but inside the
# predictor's actionable window.
LOOKBACK_DAYS = 45

# v2 mishire classification: a departed leader ABSENT from a snapshot
# this far back joined within the window — a short-tenure exit, the
# failed-hire signature. Emitted as mishire_reversal instead of an
# ordinary departure (never both, so the stack depth is not inflated
# by one fact).
SHORT_TENURE_DAYS = 540  # ~18 months

# Curated company -> leadership/team/people page URL. Seed set; extend to
# the full ~550 watchlist as URLs are confirmed. A page that 404s or has no
# archived snapshot is skipped (non-fatal).
TEAM_PAGES = {
    "BT Group": "https://www.bt.com/about/bt/our-company/our-leadership-team",
    "Aviva": "https://www.aviva.com/about-us/our-leadership-team/",
    "National Grid": "https://www.nationalgrid.com/about-us/leadership-team",
    "Severn Trent": "https://www.severntrent.com/about-us/our-leadership/",
    "Sainsbury's": "https://www.about.sainsburys.co.uk/about-us/our-leadership",
}

_HTML_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")

# Senior comms / marketing / corporate-affairs role phrases (desk-agnostic;
# the emitted trigger maps per-desk downstream). Anchored to the senior
# seats VMA backfills.
_SENIOR_ROLE_RX = re.compile(
    r"\b(?:chief communications officer|director of communications|"
    r"head of communications|communications director|"
    r"chief marketing officer|cmo\b|marketing director|director of marketing|"
    r"head of marketing|brand director|director of brand|head of brand|"
    r"corporate affairs director|director of corporate affairs|"
    r"head of corporate affairs|chief brand officer|"
    r"director of communications and marketing)\b",
    re.IGNORECASE,
)
# A person's name: 2–3 Title-Case tokens (allowing internal apostrophes /
# hyphens). Conservative on purpose — a single capitalised word is never a
# confident person name.
_NAME_RX = re.compile(r"\b([A-Z][a-z'’\-]+(?:\s+[A-Z][a-z'’\-]+){1,2})\b")
# Words that look like names but are page chrome — never a person.
_NAME_STOPWORDS = {
    "Our Leadership", "Leadership Team", "Executive Committee", "Board Of",
    "Senior Management", "Privacy Policy", "Cookie Policy", "Modern Slavery",
    "Read More", "Find Out", "Our Company", "About Us", "Annual Report",
}
# Title / role / connective tokens. Flattened leadership-page text interleaves
# names and titles ("Sarah Mitchell Chief Communications Officer James Okoro
# …"), so a Title-Case name regex can absorb role words ("Communications
# Officer James"). A candidate containing ANY of these is a title fragment,
# not a person, and is rejected — this keeps the extracted set to clean
# person names so the old-vs-new diff is exact.
_TITLE_TOKENS = {
    "chief", "communications", "comms", "officer", "marketing", "director",
    "head", "brand", "corporate", "affairs", "financial", "executive",
    "of", "and", "the", "group", "global", "vice", "president", "investor",
    "relations", "people", "human", "resources", "public", "media", "digital",
    "deputy", "operating", "commercial", "strategy", "general", "counsel",
    "company", "secretary", "non", "exec", "trustee", "chair", "chairman",
}


def _page_text(html: str) -> str:
    return _WS_RX.sub(" ", unescape(_HTML_TAG_RX.sub(" ", html or ""))).strip()


_NAME_TOKEN_RX = re.compile(r"^[A-Z][a-z'’\-]+$")


def _clean_run(tokens) -> str | None:
    """Join a run of clean Title-Case person-name tokens (2–3) into a name,
    or None. Tokens are already verified non-title / Title-Case by the
    caller."""
    run = [t for t in tokens if t]
    if len(run) < 2:
        return None
    return " ".join(run[-3:])      # surnames last; cap at 3 tokens


def _trailing_name(segment: str) -> str | None:
    """The person name ending a text segment (the 'Name → Title' layout):
    the maximal trailing run of clean Title-Case, non-title tokens."""
    run = []
    for tok in reversed(segment.split()):
        clean = tok.strip(".,;:|()")
        if _NAME_TOKEN_RX.match(clean) and clean.lower() not in _TITLE_TOKENS:
            run.append(clean)
        else:
            break
    run.reverse()
    return _clean_run(run)


def _leading_name(segment: str) -> str | None:
    """The person name starting a text segment (the 'Title → Name' layout):
    the maximal leading run of clean Title-Case, non-title tokens."""
    run = []
    for tok in segment.split():
        clean = tok.strip(".,;:|()")
        if _NAME_TOKEN_RX.match(clean) and clean.lower() not in _TITLE_TOKENS:
            run.append(clean)
        else:
            break
    return _clean_run(run)


def _display_role(matched: str) -> str:
    """Normalise a matched role phrase for display ('cmo' -> 'Chief
    Marketing Officer', otherwise title-case the phrase)."""
    r = _WS_RX.sub(" ", matched or "").strip()
    if r.lower() == "cmo":
        return "Chief Marketing Officer"
    return r.title()


def roster_with_roles(html: str) -> dict[str, str]:
    """Return {person name: role} for every person who sits next to a
    senior comms / marketing role phrase on the page. Leadership pages
    render either 'Name then Title' or 'Title then Name', so for each role
    hit we take the clean Title-Case run immediately before AND after it.
    Role / title words are excluded token-by-token, so flattened text that
    interleaves names and titles ('Sarah Mitchell Chief Communications
    Officer James Okoro …') still yields clean person names and an exact
    old-vs-new diff. Feeds both the departure diff and the living team
    map (tool/team_map.py)."""
    text = _page_text(html)
    roster: dict[str, str] = {}
    for m in _SENIOR_ROLE_RX.finditer(text):
        before = text[max(0, m.start() - 80):m.start()]
        after = text[m.end():m.end() + 80]
        # Prefer the name immediately BEFORE the title (the dominant
        # 'Name then Title' layout) — this pairs each title with its own
        # person and never grabs the NEXT leader. Only fall back to the
        # name AFTER the title for 'Title then Name' pages.
        nm = _trailing_name(before) or _leading_name(after)
        if nm and nm not in _NAME_STOPWORDS:
            roster.setdefault(nm, _display_role(m.group(0)))
    return roster


def people_with_senior_role(html: str) -> set[str]:
    """The set of person names next to a senior comms/marketing role
    phrase — the roster without its roles (the departure diff's view)."""
    return set(roster_with_roles(html))


def _cdx_nearest(url: str, days_ago: int) -> str | None:
    """Find the Wayback timestamp of a snapshot of `url` near `days_ago`
    days back. Returns a 14-digit timestamp, or None if no snapshot."""
    target = datetime.now(timezone.utc).timestamp() - days_ago * 86400
    target_dt = datetime.fromtimestamp(target, tz=timezone.utc)
    frm = (target_dt.strftime("%Y%m%d"))
    # Ask the CDX API for captures in a ~30-day band around the target and
    # take the first (closest to `from`). Status 200 captures only.
    r = get(CDX_API, params={
        "url": url, "output": "json",
        "from": frm,
        "to": (datetime.now(timezone.utc).strftime("%Y%m%d")),
        "filter": "statuscode:200",
        "collapse": "timestamp:8",   # one per day
        "limit": 1,
    }, tries=1)
    if not r or r.status_code != 200:
        return None
    try:
        rows = r.json()
    except Exception:
        return None
    # rows[0] is the header ["urlkey","timestamp",...]; data starts at [1].
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    header = rows[0]
    try:
        ts_idx = header.index("timestamp")
    except (ValueError, AttributeError):
        ts_idx = 1
    try:
        return rows[1][ts_idx]
    except Exception:
        return None


def _fetch(url: str) -> str | None:
    r = get(url, tries=1)
    if not r or r.status_code != 200 or not r.text:
        return None
    return r.text


def split_short_tenure(departed: set[str],
                       people_long_ago: set[str] | None) -> tuple[set[str], set[str]]:
    """Split departures into (ordinary, short_tenure). A departed person
    absent from the ~SHORT_TENURE_DAYS-old snapshot joined within the
    window — a short-tenure exit. With no usable old snapshot (None or a
    zero-people parse) everyone stays ordinary: never fabricate a mishire
    from a missing or unparseable page."""
    if not people_long_ago:
        return set(departed), set()
    short = {p for p in departed if p not in people_long_ago}
    return departed - short, short


def diff_company(company: str, url: str,
                 lookback_days: int = LOOKBACK_DAYS) -> list[TriggerEvent]:
    """Compare the current leadership page against the nearest archived
    snapshot ~lookback_days ago; emit a departure event for each senior
    leader present THEN and absent NOW. Departures are tenure-checked
    against a ~SHORT_TENURE_DAYS-old snapshot: a leader who joined within
    that window is emitted as mishire_reversal (failed-hire signature)
    instead of an ordinary departure."""
    live_html = _fetch(url)
    if not live_html:
        return []
    roster_now = roster_with_roles(live_html)
    # Living team map: fold today's parsed roster in so the current team
    # (and every observed joiner/leaver) accumulates per company — even on
    # runs where no archived snapshot exists for the departure diff.
    try:
        from tool import team_map as _team_map
        _team_map.update_roster(company, url, roster_now)
    except Exception as e:
        log.info("team map update failed for %s: %s", company, e)
    ts = _cdx_nearest(url, lookback_days)
    if not ts:
        return []
    # `id_` suffix asks Wayback for the raw archived page (no toolbar chrome).
    old_html = _fetch(f"{WAYBACK_BASE}/{ts}id_/{url}")
    if not old_html:
        return []
    now_people = set(roster_now)
    then_people = people_with_senior_role(old_html)
    departed = then_people - now_people
    # Guard: if the live page parsed to zero people, the page layout/JS
    # likely changed (names render client-side) — don't fabricate
    # departures from a parse failure.
    if not now_people and departed:
        log.info("wayback: %s live page parsed 0 leaders — skipping (likely JS-rendered)", company)
        return []
    # Tenure check (one extra archived fetch, only when something departed).
    people_18mo: set[str] | None = None
    if departed:
        ts_old = _cdx_nearest(url, SHORT_TENURE_DAYS)
        if ts_old and ts_old != ts:
            html_18mo = _fetch(f"{WAYBACK_BASE}/{ts_old}id_/{url}")
            if html_18mo:
                people_18mo = people_with_senior_role(html_18mo)
    ordinary, short_tenure = split_short_tenure(departed, people_18mo)
    out: list[TriggerEvent] = []
    now = datetime.now(timezone.utc)
    for person in sorted(ordinary):
        out.append(TriggerEvent(
            trigger_key="comms_leader_departure",
            trigger_label="Senior leader removed from leadership page (pre-announcement)",
            company=company,
            evidence=(f"{person} appeared as a senior comms/marketing leader on "
                      f"{company}'s leadership page ~{lookback_days} days ago but is "
                      f"no longer listed — a likely departure ahead of any announcement."),
            url=url,
            source_label="Wayback careers-page diff",
            published=now,
            raw_signal_id=signal_id("wayback_dep", f"{company}|{person}"),
            tier_hint="covered",
        ))
    for person in sorted(short_tenure):
        out.append(TriggerEvent(
            trigger_key="mishire_reversal",
            trigger_label="Short-tenure exit (failed-hire signature)",
            company=company,
            evidence=(f"{person} joined {company}'s leadership page within the "
                      f"last ~18 months and has now been removed — a short-tenure "
                      f"exit. A failed senior hire forces an urgent, usually "
                      f"confidential replacement search."),
            url=url,
            source_label="Wayback careers-page diff (tenure-checked)",
            published=now,
            raw_signal_id=signal_id("wayback_mishire", f"{company}|{person}"),
            tier_hint="covered",
        ))
    return out


def detect_team_page_departures(pages: dict | None = None) -> list[TriggerEvent]:
    """Run the diff across every seeded team page. Non-fatal per company."""
    pages = pages if pages is not None else TEAM_PAGES
    events: list[TriggerEvent] = []
    for company, url in pages.items():
        try:
            events.extend(diff_company(company, url))
        except Exception as e:
            log.info("wayback: %s diff failed (%s) — skipped", company, e)
    log.info("wayback: %d pre-announcement departure events across %d pages",
             len(events), len(pages))
    return events
