"""Multi-source resolver: given (company, role_slot), produce a ContactEntry
with the best evidence currently available, or return RESOLVED_NO_MATCH.

Sources, in the order they're consulted:
  1. companies_house  — fast, free, only catches statutory directors (CEO,
                        CFO, Chair); rarely names Heads-of-X.
  2. rns_announcements — recent appointment/departure RNS hits; high
                         precision when present but sparse.
  3. company_leadership_page — Bright Data fetch of "<company> leadership"
                               Google result, parse <h2>/<h3> name+title
                               pairs. Highest recall across role slots.
  4. bright_data_linkedin — last-resort Google site:linkedin.com/in search
                            for the role at the company. Reuses
                            linkedin_resolver._bright_data_fetch.

Every source either RETURNED data (with use/reject reason) or DID NOT.
Both states are logged per ResolutionRecord so failure-mode distributions
can be analysed without instrumentation later.
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote_plus

from tool.contacts.schema import (
    ContactEntry, ResolutionStatus, CandidateRecord, SourceQuery,
    ResolutionRecord,
)
from tool.contacts.routing import display_title_for_slot

log = logging.getLogger("brief.contacts.resolver")


# ---- Role-slot pattern dictionary -----------------------------------
# Maps role_slot -> regex matching plausible job titles on leadership
# pages, CH occupation fields, and RNS announcements. Order matters: the
# slot we're trying to fill is matched first, others second (so we can
# detect that we found a person but they fill a different slot).

ROLE_TITLE_PATTERNS = {
    "ceo": re.compile(
        r"\b(?:chief executive(?: officer)?|ceo|managing director(?!\s+of\s+\w+\s+division)|"
        r"group chief executive)\b",
        re.IGNORECASE,
    ),
    "chair": re.compile(
        r"\b(?:chair(?:man|woman|person)?|non-executive chair|chair of the board)\b",
        re.IGNORECASE,
    ),
    "cfo": re.compile(
        r"\b(?:chief financial officer|cfo|finance director|group finance director)\b",
        re.IGNORECASE,
    ),
    "cco": re.compile(
        r"\b(?:chief communications? officer|cco|"
        r"chief corporate affairs officer|"
        r"group communications director|"
        r"group head of communications?|"
        r"director of (?:group |corporate )?communications?|"
        r"vp\s*,?\s*(?:of\s+)?communications?|"
        r"vice president(?:\s*,?\s*(?:of\s+)?)\s*communications?|"
        r"svp(?:\s*,?\s*of)?\s+communications?|"
        r"senior vice president(?:\s*,?\s*(?:of\s+)?)\s*communications?)\b",
        re.IGNORECASE,
    ),
    "chro": re.compile(
        r"\b(?:chief people officer|cpo|chief human resources officer|chro|"
        r"hr director|group hr director|people director|"
        r"chief talent officer|group people director)\b",
        re.IGNORECASE,
    ),
    "gc": re.compile(
        r"\b(?:general counsel|gc|chief legal officer|"
        r"group general counsel|legal director)\b",
        re.IGNORECASE,
    ),
    "head_of_comms": re.compile(
        r"\bhead of (?:external |corporate |group )?communications?\b",
        re.IGNORECASE,
    ),
    "head_of_corporate_affairs": re.compile(
        r"\bhead of (?:group |corporate )?corporate affairs|"
        r"director of corporate affairs|corporate affairs director\b",
        re.IGNORECASE,
    ),
    "head_of_ic": re.compile(
        r"\bhead of internal communications?|director of internal communications?|"
        r"internal communications director\b",
        re.IGNORECASE,
    ),
    "ir_director": re.compile(
        r"\b(?:head of investor relations|director of investor relations|"
        r"ir director|investor relations director)\b",
        re.IGNORECASE,
    ),
}


def classify_title(title: str) -> str | None:
    """Return the role_slot the title fills, or None."""
    if not title:
        return None
    # head_of_X are more specific than chro/cco — match them first
    priority = (
        "head_of_corporate_affairs", "head_of_ic", "head_of_comms",
        "ir_director", "gc", "cfo", "chro", "cco", "chair", "ceo",
    )
    for slot in priority:
        if ROLE_TITLE_PATTERNS[slot].search(title):
            return slot
    return None


# ---- Source 1: Companies House officer listing ----------------------
def _query_companies_house(company: str, role_slot: str) -> tuple[list[CandidateRecord], SourceQuery]:
    """Returns (candidates, source_query). Companies House only knows
    about statutory directors — useful for CEO/Chair/CFO, very rarely
    for comms or IR-D."""
    try:
        from tool.sources.companies_house import (
            resolve_company_number, company_officers,
        )
    except Exception as e:
        return [], SourceQuery(
            source="companies_house", returned_data=False,
            reason=f"module import failed: {e}",
        )

    number = resolve_company_number(company)
    if not number:
        return [], SourceQuery(
            source="companies_house", returned_data=False,
            reason="company not resolved on CH",
        )
    officers = company_officers(number)
    if not officers:
        return [], SourceQuery(
            source="companies_house", returned_data=False,
            reason="no officers returned",
        )

    candidates = []
    for o in officers:
        if o.get("resigned_on"):
            continue
        occupation = (o.get("occupation") or "").strip()
        name = (o.get("name") or "").strip()
        slot = classify_title(occupation)
        if not slot:
            continue
        appointed_on = o.get("appointed_on") or None
        candidates.append(CandidateRecord(
            name=_normalise_ch_name(name),
            role_title=occupation,
            confidence=0.85 if slot == role_slot else 0.0,
            source="companies_house",
            linkedin_url=None,
        ))
        candidates[-1]._appointed_on = appointed_on  # type: ignore[attr-defined]
        candidates[-1]._slot = slot                  # type: ignore[attr-defined]

    return candidates, SourceQuery(
        source="companies_house",
        returned_data=bool(candidates),
        reason="" if candidates else "no matching officer titles",
    )


def _normalise_ch_name(ch_name: str) -> str:
    """CH formats names 'SMITH, John Andrew' — return 'John Andrew Smith'."""
    if "," in ch_name:
        last, first = ch_name.split(",", 1)
        return f"{first.strip().title()} {last.strip().title()}"
    return ch_name.title()


# ---- Source 2: RNS / appointment announcements ----------------------
def _query_rns(company: str, role_slot: str) -> tuple[list[CandidateRecord], SourceQuery]:
    """Mine the existing daily-signal feed for any recent appointment
    naming the role at this company."""
    state_file = (
        __file__  # /home/user/VMA/tool/contacts/resolver.py
    )
    import json
    from pathlib import Path
    state_dir = Path(state_file).resolve().parent.parent / "state"
    candidates = []
    matched = False
    for fname in ("latest_signals.json", "latest_sweep_signals.json"):
        p = state_dir / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        for sig in data:
            if not isinstance(sig, dict):
                continue
            if (sig.get("company") or "").strip().lower() != company.strip().lower():
                continue
            title = sig.get("title") or ""
            # Look for "X appointed/named/joins as Y" patterns
            person = _extract_person_from_appointment(title)
            slot = classify_title(title)
            if not person or not slot:
                continue
            matched = True
            candidates.append(CandidateRecord(
                name=person,
                role_title=_extract_title_substring(title, slot),
                confidence=0.9 if slot == role_slot else 0.0,
                source="rns_announcements",
                linkedin_url=None,
            ))
            candidates[-1]._slot = slot  # type: ignore[attr-defined]

    return candidates, SourceQuery(
        source="rns_announcements",
        returned_data=matched,
        reason="" if matched else "no recent appointment in feed",
    )


_APPOINTMENT_RX = re.compile(
    r"(?P<person>[A-Z][a-zA-Z\-']+\s+[A-Z][a-zA-Z\-']+(?:\s+[A-Z][a-zA-Z\-']+)?)"
    r"\s+(?:appointed|joins|named|to join|to take up|takes up|to become|"
    r"becomes|to be appointed|has been appointed|will join)"
)


def _extract_person_from_appointment(text: str) -> str | None:
    if not text:
        return None
    m = _APPOINTMENT_RX.search(text)
    return m.group("person") if m else None


def _extract_title_substring(title: str, slot: str) -> str:
    rx = ROLE_TITLE_PATTERNS.get(slot)
    if rx:
        m = rx.search(title)
        if m:
            return m.group(0)
    return display_title_for_slot(slot)


# ---- Source 3: Company leadership page (Bright Data) ----------------
def _query_leadership_page(company: str, role_slot: str,
                           fetch: Callable[[str], dict | str | None]
                           ) -> tuple[list[CandidateRecord], SourceQuery]:
    """Use Bright Data to fetch a Google result for '<company> leadership team',
    then parse the first plausible company-owned URL and extract name+title
    pairs.

    `fetch` may return either a string (legacy) or a dict with diagnostic
    info (text, status, error). The dict form lets the audit surface the
    actual HTTP status code instead of just 'fetch returned empty'."""
    query = f'"{company}" leadership team OR executive team OR our people site:{_likely_domain(company)} OR "{company}" leadership'
    google_url = f"https://www.google.com/search?q={quote_plus(query)}"
    html, fetch_reason = _normalise_fetch_result(fetch(google_url))
    if not html:
        return [], SourceQuery(
            source="leadership_page", returned_data=False,
            reason=fetch_reason,
        )

    # Parse name + title pairs from the leadership page HTML. We don't
    # actually navigate to the leadership page; we use the SERP snippets
    # which usually include the name + title together for FTSE firms.
    candidates = _extract_name_title_pairs(html)
    return candidates, SourceQuery(
        source="leadership_page",
        returned_data=bool(candidates),
        reason="" if candidates else "no name/title pairs in SERP",
    )


def _normalise_fetch_result(result) -> tuple[str | None, str]:
    """Accept either a plain str/None or a diag dict from
    _bright_data_fetch_diag. Returns (html, reason_when_failed)."""
    if isinstance(result, dict):
        text = result.get("text")
        if text:
            return text, ""
        err = result.get("error") or "fetch returned empty"
        zone = result.get("used_zone")
        if zone:
            err = f"{err} [zone={zone!r}]"
        return None, err
    # Legacy str/None path
    if result:
        return result, ""
    return None, "fetch returned empty"


def _likely_domain(company: str) -> str:
    """Best-effort domain guess: strip suffixes, lowercase, dot-com.
    Used inside the SERP query to bias toward the company's own site;
    Google ignores it gracefully if wrong."""
    s = re.sub(r"\b(plc|ltd|limited|group|holdings|llp)\b", "", company,
               flags=re.IGNORECASE).strip()
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return f"{s}.com"


_NAME_TITLE_PAIR_RX = re.compile(
    r"(?P<name>[A-Z][a-zA-Z\-']+\s+[A-Z][a-zA-Z\-']+(?:\s+[A-Z][a-zA-Z\-']+)?)"
    r"\s*[,–\-:]+\s*"
    r"(?P<title>(?:Chief|Head of|Director of|Group|Senior|VP|Vice President|"
    r"General Counsel|CFO|CEO|CCO|CHRO)[^.<\n,]{2,80})"
)


def _extract_name_title_pairs(html: str) -> list[CandidateRecord]:
    if not html:
        return []
    # Strip tags coarsely — we only care about adjacent text fragments.
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    seen = set()
    out = []
    for m in _NAME_TITLE_PAIR_RX.finditer(text):
        name = m.group("name").strip()
        title = m.group("title").strip().rstrip(",.")
        key = (name.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        slot = classify_title(title)
        if not slot:
            continue
        out.append(CandidateRecord(
            name=name, role_title=title,
            confidence=0.7, source="leadership_page",
            linkedin_url=None,
        ))
        out[-1]._slot = slot  # type: ignore[attr-defined]
    return out


# ---- Source 4: Bright Data LinkedIn search (fallback) ----------------
def _query_linkedin(company: str, role_slot: str,
                    fetch: Callable[[str], dict | str | None]
                    ) -> tuple[list[CandidateRecord], SourceQuery]:
    """Last-resort: Google for 'site:linkedin.com/in' results."""
    role = display_title_for_slot(role_slot)
    query = f'"{role}" "{company}" site:linkedin.com/in'
    google_url = f"https://www.google.com/search?q={quote_plus(query)}"
    html, fetch_reason = _normalise_fetch_result(fetch(google_url))
    if not html:
        return [], SourceQuery(
            source="bright_data_linkedin", returned_data=False,
            reason=fetch_reason,
        )

    # Reuse existing linkedin profile regex
    from tool.linkedin_resolver import _parse_first_profile
    profile_url = _parse_first_profile(html)
    if not profile_url:
        return [], SourceQuery(
            source="bright_data_linkedin", returned_data=False,
            reason="no /in/ url in SERP",
        )

    name = _name_from_linkedin_url(profile_url)
    return [
        CandidateRecord(
            name=name, role_title=role,
            confidence=0.55, source="bright_data_linkedin",
            linkedin_url=profile_url,
        )
    ], SourceQuery(
        source="bright_data_linkedin", returned_data=True,
    )


def _name_from_linkedin_url(url: str) -> str:
    m = re.search(r"/in/([a-zA-Z0-9\-_%~.]+)", url)
    if not m:
        return ""
    raw = m.group(1)
    parts = raw.split("-")
    # Drop trailing 5+ char alphanumeric handle suffixes
    cleaned = [p for p in parts if not re.fullmatch(r"[a-z0-9]{6,}", p)]
    return " ".join(p.capitalize() for p in cleaned[:3])


# ---- Top-level resolve() --------------------------------------------
def resolve(company: str, role_slot: str, *,
            fetch: Callable[[str], str | None] | None = None,
            consult_linkedin_fallback: bool = True,
            ) -> tuple[ContactEntry | None, ResolutionRecord]:
    """Try all sources in order, return the best ContactEntry and a full
    ResolutionRecord describing what was queried.

    `fetch` is an optional Bright Data callable; if None, the linkedin
    + leadership-page sources are skipped (used by tests / dry-runs
    without budget). Companies House and RNS run regardless.
    """
    sources_queried: list[SourceQuery] = []
    all_candidates: list[CandidateRecord] = []

    # Source 1: Companies House
    ch_cands, ch_sq = _query_companies_house(company, role_slot)
    sources_queried.append(ch_sq)
    all_candidates.extend(ch_cands)

    # Source 2: RNS
    rns_cands, rns_sq = _query_rns(company, role_slot)
    sources_queried.append(rns_sq)
    all_candidates.extend(rns_cands)

    # Source 3: Leadership page (Bright Data)
    if fetch is not None:
        lp_cands, lp_sq = _query_leadership_page(company, role_slot, fetch)
        sources_queried.append(lp_sq)
        all_candidates.extend(lp_cands)
    else:
        sources_queried.append(SourceQuery(
            source="leadership_page", returned_data=False,
            reason="fetch disabled (no Bright Data)",
        ))

    # Pick the best matching candidate for the target role_slot first
    target_candidates = [
        c for c in all_candidates
        if getattr(c, "_slot", None) == role_slot
    ]
    pick = max(target_candidates, key=lambda c: c.confidence, default=None)

    # Source 4: LinkedIn fallback if no target-slot match yet
    if pick is None and consult_linkedin_fallback and fetch is not None:
        li_cands, li_sq = _query_linkedin(company, role_slot, fetch)
        sources_queried.append(li_sq)
        all_candidates.extend(li_cands)
        if li_cands:
            pick = li_cands[0]
    elif pick is None:
        sources_queried.append(SourceQuery(
            source="bright_data_linkedin", returned_data=False,
            reason="not consulted (fetch disabled or target-slot match found)",
        ))

    # Mark the chosen source as "used"
    if pick is not None:
        for sq in sources_queried:
            if sq.source == pick.source:
                sq.used = True

    now = datetime.now(timezone.utc).isoformat()
    if pick is None:
        record = ResolutionRecord(
            timestamp=now,
            company=company,
            role_slot=role_slot,
            role_title_query=display_title_for_slot(role_slot),
            outcome=ResolutionStatus.RESOLVED_NO_MATCH,
            candidates_considered=all_candidates,
            sources_queried=sources_queried,
        )
        return None, record

    appointed_on = getattr(pick, "_appointed_on", None)
    entry = ContactEntry(
        name=pick.name,
        role_title=pick.role_title,
        role_slot=role_slot,
        linkedin_url=pick.linkedin_url,
        source_url="",
        source_label=pick.source,
        tenure_start=appointed_on,
        verified_at=now,
        confidence=pick.confidence,
    )
    record = ResolutionRecord(
        timestamp=now,
        company=company,
        role_slot=role_slot,
        role_title_query=display_title_for_slot(role_slot),
        outcome=ResolutionStatus.RESOLVED_VERIFIED,
        picked_name=pick.name,
        picked_url=pick.linkedin_url,
        confidence=pick.confidence,
        candidates_considered=all_candidates,
        sources_queried=sources_queried,
    )
    return entry, record
