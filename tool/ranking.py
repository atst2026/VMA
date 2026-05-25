"""Filter + rank signals for Sara's daily brief.

Test applied:
- role titles match `ROLE_KEYWORDS` (title OR summary OR company name)
- salary ≥ £40k perm, OR unknown (interim day-rate-only roles dropped:
  Exec Search / Permanent Recruitment is the specialism, not interim)
- UK-primary weighting; international kept but discounted

Rank score = base_weight × geo_weight × role_strength × kind_multiplier × freshness
"""
from __future__ import annotations
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from typing import Iterable

from dateutil import parser as dateparse

from tool.config import (
    COMPANY_EXCLUDE, EXCLUDE_TITLE_TERMS, GEO_PRIMARY, GEO_SECONDARY_WEIGHT,
    JOB_BOARD_COMPANIES, ROLE_KEYWORDS,
)


# Word-boundary regex patterns for every keyword. Using boundaries avoids
# substring false-positives like "cco" matching inside "account".
@lru_cache(maxsize=None)
def _compile_patterns(keywords: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(r"\b" + re.escape(k) + r"\b", re.IGNORECASE) for k in keywords)


_ROLE_PATTERNS = _compile_patterns(tuple(ROLE_KEYWORDS))
_EXCLUDE_PATTERNS = _compile_patterns(tuple(EXCLUDE_TITLE_TERMS))
_STRONG_PATTERNS = _compile_patterns(tuple([
    "chief communications officer", "head of corporate communications",
    "head of internal communications", "corporate affairs director",
    "communications director", "pr director", "head of communications",
]))

log = logging.getLogger("brief.rank")

KIND_MULTIPLIER = {
    "leadership_change": 1.6,
    "rns": 1.3,
    "regulator": 1.2,
    "filing": 1.0,
    "procurement": 0.9,
    "trade_press": 1.1,
    "job": 1.4,
    "linkedin_batch": 0.7,
    "": 1.0,
}

# Verbs / phrases that mark a trade-press item as actual news rather than
# editorial / thought leadership / trends pieces. If a trade_press signal
# does NOT contain one of these, it's dropped — Sara wants BD signals, not
# reading.
NEWS_VERBS = frozenset([
    "appoint", "appointed", "appoints", "appointment",
    "hire", "hired", "hires", "hiring",
    "join", "joined", "joins", "joining",
    "depart", "departed", "departs", "departure", "departing",
    "leave", "leaves", "leaving",
    "step down", "steps down", "stepping down",
    "quit", "quits",
    "resign", "resigned", "resigns",
    "promote", "promoted", "promotes", "promotion",
    "replace", "replaced", "replaces", "replacement",
    "restructure", "restructured", "restructures", "restructuring",
    "layoff", "layoffs", "laid off",
    "exit", "exits", "exited", "exiting",
    "retire", "retires", "retired",
    "named", "names new", "taps",
    "announce", "announces", "announced",
    "to lead", "moves to", "moves from",
    # explicit role-addition phrases
    "new head of", "new director of", "new chief",
    "new cco", "new cmo", "new cpo", "new chro", "new ceo",
])


def _looks_like_news(title: str) -> bool:
    t = (title or "").lower()
    return any(v in t for v in NEWS_VERBS)


def _role_strength(text: str) -> float:
    """How strongly a piece of text matches the role taxonomy. 0 = no match.
    Word-boundary-aware so 'cco' matches 'CCO' but not 'account'.
    """
    if not text:
        return 0.0
    score = 0.0
    for p in _STRONG_PATTERNS:
        if p.search(text):
            score += 1.2
    for p in _ROLE_PATTERNS:
        if p.search(text):
            score += 0.4
    return score


def _geo_weight(geo: str) -> float:
    if geo in GEO_PRIMARY or geo == "UK":
        return 1.0
    if geo in ("EU", "US", "APAC", "INT"):
        return GEO_SECONDARY_WEIGHT
    return 0.7


def _freshness(published: str) -> float:
    """Newer = higher. Daily mode: heavily discount >7 days. Sweep mode
    (VMA_SWEEP_DAYS>1): flatter curve so older items aren't crushed."""
    from tool.config import sweep_days
    sweep = sweep_days() > 1
    if not published:
        return 0.85   # unknown pub date: neutral-minus
    try:
        dt = dateparse.parse(published)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return 0.85
    hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if hours < 0:
        return 1.0
    if sweep:
        # Sweep: gentle decay across 14 days
        if hours < 48:
            return 1.1
        if hours < 24 * 7:
            return 1.0
        if hours < 24 * 14:
            return 0.9
        return 0.6
    # Daily mode (original)
    if hours < 24:
        return 1.2
    if hours < 48:
        return 1.0
    if hours < 72:
        return 0.85
    if hours < 168:
        return 0.65
    return 0.4


_COMPANY_EXCLUDE_LC = tuple(c.lower() for c in COMPANY_EXCLUDE)

# Generic recruitment-agency name detector. Agency-posted ads hide the
# real client and are another recruiter's mandate — off-product for an
# exec-search firm and noise in Sara's top-5. High-precision, word-
# boundary tokens only (NOT bare "search"/"talent" — those false-trip
# on "Research"/product names; brand-name agencies like EquiTalent are
# handled via the curated COMPANY_EXCLUDE list instead).
_AGENCY_NAME_RX = re.compile(
    r"\b(?:recruitment|recruiters?|recruiting|resourcing|staffing|"
    r"headhunters?|headhunting|executive search|"
    r"search (?:&|and) selection|"
    r"talent (?:acquisition|solutions|partners|associates|group))\b",
    re.IGNORECASE,
)


def score(signal: dict) -> float:
    title = signal.get("title") or ""
    company = (signal.get("company") or "").lower().strip()
    # Hard exclusion: never show jobs at Sara's own employer (VMA Group)
    # or at direct competitor search firms. Substring match (case-insensitive)
    # against the company name in the signal.
    for excluded in _COMPANY_EXCLUDE_LC:
        if excluded and excluded in company:
            return 0.0
    # Recruitment-agency posting (hidden client, competitor mandate).
    if company and _AGENCY_NAME_RX.search(company):
        return 0.0
    # Hard exclusion: agency/sales client-service roles are out regardless of
    # what else matches. Word-boundary matched to avoid clobbering legit
    # in-house titles that happen to share a word.
    for p in _EXCLUDE_PATTERNS:
        if p.search(title):
            return 0.0

    # Predictive-trigger news lanes (kind="news": GDELT predictive,
    # Google News, the UKTN/BusinessCloud/Tech.eu funding feeds) exist to
    # feed the PREDICTOR and the standalone detectors — which read the
    # raw signal stream, NOT this ranked set. They are upstream-event
    # news, never advertised vacancies, so they must never appear as a
    # "Today's Lead" (this is what put "Pierre Poilievre's communications
    # director stepping down" into Sara's emailed top-5). detect_events /
    # detect_funding / etc. are unaffected — they don't go through rank().
    if signal.get("kind") == "news":
        return 0.0

    # Trade press: only let actual news through (appointments, departures,
    # restructures). Editorial / thought leadership / trend pieces all drop.
    if signal.get("kind") == "trade_press" and not _looks_like_news(title):
        return 0.0

    text = " ".join(filter(None, [
        signal.get("title", ""), signal.get("summary", ""), signal.get("company", ""),
    ]))
    rs = _role_strength(text)
    if rs == 0:
        return 0.0
    base = signal.get("weight", 1.0)
    kind = KIND_MULTIPLIER.get(signal.get("kind", ""), 1.0)
    geo = _geo_weight(signal.get("geo", ""))
    fresh = _freshness(signal.get("published", ""))
    from tool.peers import sector_heat_multiplier
    heat = sector_heat_multiplier(signal.get("company", "") or "")
    return round(base * kind * geo * fresh * heat * (1.0 + 0.25 * rs), 3)


def _norm_title(t: str) -> str:
    """Collapse whitespace, lowercase, strip 'maternity cover' / 'contract' /
    trailing punctuation so two near-identical listings dedup cleanly."""
    t = (t or "").lower()
    for junk in ("(12 month contract)", "(maternity cover)", "(mat cover)",
                 "- mat cover", "- maternity cover", "- 12 month contract",
                 "12 month mat cover", "12-month mat cover"):
        t = t.replace(junk, "")
    t = " ".join(t.split())
    return t.strip(" -,·")


# Legal/structural suffixes + descriptors that vary between aggregator
# feeds for the SAME employer ("Linklaters" vs "LINKLATERS LLP", "Harris
# Federation" vs "Harris Federation Head Office"). Stripping them lets the
# dedup collapse the same job that Adzuna returns from several boards.
_COMPANY_SUFFIX_RX = re.compile(
    r"\b(?:ltd|limited|llp|plc|inc|incorporated|group|holdings|"
    r"head\s+office|hq|careers|uk|gb)\b", re.I)


def _norm_company(c: str) -> str:
    c = (c or "").lower().strip()
    if not c:
        return ""
    c = c.replace("&", " and ")
    c = re.sub(r"[^a-z0-9 ]", " ", c)
    c = re.sub(r"^the\s+", "", c)
    c = _COMPANY_SUFFIX_RX.sub(" ", c)
    return " ".join(c.split())


def _title_key(t: str) -> str:
    """Aggressive title normaliser used only for the dedup key: on top of
    `_norm_title` it folds `&`->`and` and drops all punctuation so comma /
    dash variants of the same role collapse. Does NOT strip the
    company-suffix tokens `_norm_company` does (we don't want 'Group Head'
    and 'Head' to merge)."""
    t = _norm_title(t).replace("&", " and ")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return " ".join(t.split())


_JOB_BOARD_NORMS = frozenset(_norm_company(b) for b in JOB_BOARD_COMPANIES)


def _is_job_board(company: str) -> bool:
    """True if the 'company' is actually a job board, not the employer.
    Exact normalised match (no substring) so e.g. 'Reed' can't trip
    'Reed Smith'."""
    return _norm_company(company) in _JOB_BOARD_NORMS


def _acronym(name: str) -> str:
    parts = name.split()
    return "".join(p[0] for p in parts) if len(parts) >= 2 else ""


def _companies_related(a: str, b: str) -> bool:
    """True if two NORMALISED employer names are near-certainly the same
    organisation: identical, one a prefix/substring of the other, or one
    the acronym of the other. Catches 'Citi'/'Citigroup', 'Wildlife
    Trust'/'Wildlife Trusts', 'LSEC'/'London South East Colleges'. Only
    ever consulted when the role title is already identical, so it cannot
    merge two unrelated employers that happen to share a generic title."""
    if not a or not b:
        return False
    if a == b:
        return True
    x, y = a.replace(" ", ""), b.replace(" ", "")
    short, lng = (x, y) if len(x) <= len(y) else (y, x)
    if len(short) >= 4 and lng.startswith(short):
        return True
    if len(short) >= 5 and short in lng:
        return True
    if len(y) >= 3 and _acronym(a) == y:
        return True
    if len(x) >= 3 and _acronym(b) == x:
        return True
    return False


def dedup(signals: list[dict]) -> list[dict]:
    """Dedup by signal ID (same source+guid) AND by role title + employer.
    Within an identical normalised title, employers are matched leniently
    (`_companies_related`) so the SAME job that Adzuna returns from several
    boards under prefix / plural / acronym / suffix variants of the
    employer name collapses to one — while two genuinely different
    employers with the same generic title stay separate. When merging,
    upgrade an ALL-CAPS display name to a nicer-cased variant."""
    seen_ids = set()
    by_title: dict[str, list[tuple[str, int]]] = {}  # title_key -> [(norm_company, out_idx)]
    out = []
    for s in signals:
        sid = s.get("id") or (s.get("source", "") + "|" + s.get("title", ""))
        if sid in seen_ids:
            continue
        tk = _title_key(s.get("title", ""))
        nc = _norm_company(s.get("company", ""))
        # Some feeds append " - <Employer>" to the role; strip a trailing
        # copy of THIS row's own employer so it matches the clean variant.
        if tk and nc and tk.endswith(" " + nc):
            tk = tk[: -(len(nc) + 1)].strip()
        dup_idx = None
        if tk:
            for knc, idx in by_title.get(tk, []):
                if knc == nc or _companies_related(knc, nc):
                    dup_idx = idx
                    break
        if dup_idx is not None:
            kept = out[dup_idx]
            cur = (s.get("company") or "").strip()
            if (kept.get("company") or "").strip().isupper() and cur and not cur.isupper():
                kept["company"] = cur
            continue
        seen_ids.add(sid)
        out.append(s)
        if tk:
            by_title.setdefault(tk, []).append((nc, len(out) - 1))
    return _collapse_board_dupes(out)


def _collapse_board_dupes(out: list[dict]) -> list[dict]:
    """Drop a job board's COPY of a role (company is a board name, e.g.
    'onlyFE') when the SAME role — identical normalised title — also
    appears under exactly one real employer; that employer row is the
    keeper. Board-only leads with no employer twin, and titles split
    across several distinct employers, are left untouched — so unique
    board-sourced leads survive and a generic title never wrongly
    collapses a board listing into an unrelated employer."""
    groups: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(out):
        tk = _title_key(s.get("title", ""))
        if tk:
            groups[tk].append(i)
    drop: set[int] = set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        boards = [i for i in idxs if _is_job_board(out[i].get("company", ""))]
        employers = {
            _norm_company(out[i].get("company", ""))
            for i in idxs
            if not _is_job_board(out[i].get("company", ""))
            and _norm_company(out[i].get("company", ""))
        }
        if boards and len(employers) == 1:
            drop.update(boards)
    return [s for i, s in enumerate(out) if i not in drop]


def rank(signals: list[dict]) -> list[dict]:
    deduped = dedup(signals)
    scored = []
    for s in deduped:
        s["score"] = score(s)
        if s["score"] > 0:
            scored.append(s)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def suggest_angle(signal: dict) -> str:
    """One-line opening angle based on signal kind."""
    k = signal.get("kind", "")
    title = signal.get("title", "")
    company = signal.get("company", "")
    if k == "job":
        tgt = company or "the employer"
        return f"Live role at {tgt} — pitch retained before the internal search runs out of runway."
    if k == "leadership_change":
        return "Leadership change in the comms stack — call the new decision-maker inside 72 hours."
    if k == "rns":
        return "Regulatory announcement — comms restructure often follows within 4–8 weeks."
    if k == "regulator":
        return "Regulator action — reputation exposure; a permanent reputation hire follows. Engage the decision-maker now for the retained search."
    if k == "procurement":
        return "Public-sector comms procurement — framework or contract entry point."
    if k == "trade_press":
        return "Sector coverage — read, then surface an angle to the named individual."
    return "Public signal worth a 10-minute dig before dialling."
