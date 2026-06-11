"""Infer the hiring manager a comms lead reports into.

A scraped job lead names a *role* ("Internal Communications Manager"),
not the person Sara should contact. The person who owns that hire is the
role one rung up the comms line. This module encodes that step.

Two inputs, in priority order:

  1. The job description / summary itself. If it says "reporting to the
     Director of Corporate Affairs", that IS the answer — extract it.
  2. Otherwise, infer from the seniority-up rules below. Comms titles
     are not standardised between companies, so matching is fuzzy
     (token-based, close variants treated as equivalent):

       - Internal Communications Manager (or more junior IC)
             -> Head of Internal Communications
       - Corporate / general Communications Manager
             -> Director of Communications
       - Head of Internal Communications
             -> Director of Communications / Corporate Affairs / CCO
       - Comms Director / Head of Corporate Affairs (already senior)
             -> Chief Communications Officer

The module is pure logic for the inference; `best_named_contact()` is
the only function that touches the seeded contacts roster, and it
degrades gracefully (returns None) if that layer is unavailable.
"""
from __future__ import annotations

import re

from tool.profiles import active_profile

# Roster role-slots (tool/contacts) the inferred manager maps onto, in
# fall-through order. Slots absent from the roster are skipped harmlessly.
_DIRECTOR_SLOTS = ("cco", "head_of_corporate_affairs", "head_of_comms")
_CCO_SLOTS = ("cco", "head_of_corporate_affairs")
_HEAD_IC_SLOTS = ("head_of_ic", "cco", "head_of_corporate_affairs")

_COMMS_TOKENS = (
    "communication", "comms", "corporate affairs", "public relations",
    "media relations", "press office", " pr ", "publicity",
)
_IC_TOKENS = (
    "internal comm", "employee comm", "colleague comm", "internal engagement",
    "employee engagement", "internal & change", "internal and change",
)
_SENIOR_TOKENS = (
    "director", "chief", "vp ", "vice president", "global head",
    "group head", "head of corporate affairs",
)
_JUNIOR_TOKENS = (
    "manager", "lead", "specialist", "officer", "executive", "adviser",
    "advisor", "consultant", "coordinator", "co-ordinator", "assistant",
    "business partner", "partner",
)

# --- Profile-aware role labels + slots ---------------------------------
# Comms keeps the live values above; marketing (FIRST DRAFT) maps the same
# seniority-up ladder onto marketing seats. Editing the marketing branch
# re-tunes the marketing "who to call" — review with the marketing team.
_MKT = active_profile().key == "marketing"
if _MKT:
    _DIRECTOR_SLOTS = ("cmo", "head_of_brand", "head_of_marketing")
    _CCO_SLOTS = ("cmo", "head_of_brand")
    _HEAD_IC_SLOTS = ("head_of_marketing", "cmo")
    _COMMS_TOKENS = (
        "marketing", "brand", "growth", "ecommerce", "e-commerce",
        "demand generation", "performance marketing", "crm", "digital marketing",
    )
    _IC_TOKENS = ()   # no internal-comms concept in marketing
_DIRECTOR_ROLE = "Marketing Director" if _MKT else "Director of Communications"
_HEAD_IC_ROLE = "Head of Marketing" if _MKT else "Head of Internal Communications"
_SENIOR_ROLE = "Chief Marketing Officer" if _MKT else "Chief Communications Officer"
_DEFAULT_ROLE = "Head of Marketing" if _MKT else "Head of Communications"
_LEADERSHIP_ROLE = "Marketing leadership" if _MKT else "Communications leadership"

# "reporting to / reports into the <Title>" — capture the title phrase.
_REPORTING_RX = re.compile(
    r"report(?:s|ing)?\s+(?:directly\s+)?(?:in\s*)?to\s+"
    r"(?:the\s+|a\s+|our\s+|our\s+group\s+|group\s+)?"
    r"([A-Za-z][A-Za-z &/'\-]{4,60}?)"
    r"(?=[.,;:\n)]|\band\b|\bwho\b|\bwill\b|\bbased\b|\bin our\b|$)",
    re.IGNORECASE,
)
# A valid extracted reporting line must look like a real leadership role.
_REPORTING_OK_TOKENS = (
    "communication", "comms", "corporate affairs", "marketing", "brand",
    "chief", "director", "head of", "people", "hr", "human resources",
    "ceo", "officer", "vp", "vice president",
)


def _norm(title: str) -> str:
    """Lower-case, strip an embedded company suffix and region noise so
    the classifier sees just the role."""
    t = (title or "").strip()
    # "Head of Corporate Communications — AstraZeneca" / " - Monzo"
    t = re.split(r"\s+[–—-]\s+", t)[0]
    # ", EMEA" / ", London" / " at Acme" trailing scope
    t = re.split(r"\s+at\s+[A-Z]", t)[0]
    t = t.split(",")[0]
    return re.sub(r"\s+", " ", t).strip().lower()


def _has(haystack: str, tokens) -> bool:
    return any(tok in haystack for tok in tokens)


def extract_reporting_line(text: str) -> str | None:
    """Pull an explicit 'reports to <Title>' out of a JD/summary, or None.

    Conservative on purpose: a garbled capture is worse than falling back
    to the heuristic, so the phrase must contain a leadership keyword and
    be a sane length."""
    if not text:
        return None
    m = _REPORTING_RX.search(text)
    if not m:
        return None
    phrase = re.sub(r"\s+", " ", m.group(1)).strip(" .,-&/")
    low = phrase.lower()
    if not (3 <= len(phrase) <= 60):
        return None
    if not _has(low, _REPORTING_OK_TOKENS):
        return None
    small = {"of", "the", "and", "for", "to", "a", "an", "in", "&", "at", "on"}
    words = phrase.split()
    out = []
    for i, w in enumerate(words):
        if w.isupper():                       # keep acronyms: CCO, HR, VP
            out.append(w)
        elif i > 0 and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def infer_hiring_manager(title: str, summary: str = "",
                         company: str = "") -> dict:
    """Return the role the lead reports into.

    {
      manager_title: str   -- display/search title for that person
      slots: tuple[str]    -- roster slots to try, in order
      confidence: float    -- 0-1
      basis: str           -- jd_reporting_line | role_heuristic | default
    }
    """
    jd = extract_reporting_line(f"{title}\n{summary}")
    if jd:
        return {
            "manager_title": jd,
            "slots": _slots_for_title(jd),
            "confidence": 0.9,
            "basis": "jd_reporting_line",
        }

    t = _norm(title)
    is_comms = _has(t, _COMMS_TOKENS)
    is_ic = _has(t, _IC_TOKENS)
    is_senior = _has(t, _SENIOR_TOKENS)
    is_junior = _has(t, _JUNIOR_TOKENS)

    if is_ic and ("head of" in t or is_senior):
        # Head of Internal Comms -> Director of Comms / Corp Affairs / CCO
        return _result(_DIRECTOR_ROLE, _DIRECTOR_SLOTS, 0.7)
    if is_ic:
        # IC Manager / Specialist / Officer -> Head of Internal Comms
        return _result(_HEAD_IC_ROLE, _HEAD_IC_SLOTS, 0.7)
    if is_comms and is_senior:
        # Comms Director / Head of Corporate Affairs -> CCO
        return _result(_SENIOR_ROLE, _CCO_SLOTS, 0.65)
    if is_comms and (is_junior or "head of" in t):
        # Corporate / general Comms Manager -> Director of Communications
        return _result(_DIRECTOR_ROLE, _DIRECTOR_SLOTS, 0.7)
    if is_comms:
        return _result(_DIRECTOR_ROLE, _DIRECTOR_SLOTS, 0.5)

    # No specialism signal in the title — generic fallback.
    return _result(_DEFAULT_ROLE, _CCO_SLOTS, 0.3, basis="default")


def _result(manager_title: str, slots: tuple, confidence: float,
            basis: str = "role_heuristic") -> dict:
    return {
        "manager_title": manager_title,
        "slots": slots,
        "confidence": confidence,
        "basis": basis,
    }


def _slots_for_title(manager_title: str) -> tuple:
    """Map a free-text / extracted manager title onto roster slots."""
    low = manager_title.lower()
    if "internal comm" in low:
        return _HEAD_IC_SLOTS
    if "corporate affairs" in low:
        return ("head_of_corporate_affairs", "cco")
    if "chief comm" in low or low.startswith("cco"):
        return ("cco", "head_of_corporate_affairs")
    if any(k in low for k in ("hr", "people", "human resources", "chro")):
        return ("chro", "ceo")
    if _has(low, _COMMS_TOKENS):
        return _DIRECTOR_SLOTS
    return _CCO_SLOTS


# Job-title tokens that mark a scraped item as an actual vacancy rather
# than a news headline / filing. Shared by the dashboard and the morning
# brief so both agree on which leads get reporting-line inference.
_VACANCY_TITLE_TOKENS = (
    "head of", "director of", "chief", "vp ", "vice president",
    "manager", "lead", "officer", "specialist", "business partner",
)
_NON_JOB_KINDS = ("rns", "filing", "regulator", "procurement",
                   "trade_press", "leadership_change")


def is_job_like(signal: dict) -> bool:
    """True if the lead is a vacancy (reporting-line inference applies).
    News / appointment / filing kinds keep their existing routing."""
    kind = (signal.get("kind") or "").strip().lower()
    if kind == "job":
        return True
    if kind in _NON_JOB_KINDS:
        return False
    t = (signal.get("title") or "").lower()
    return (
        any(tok in t for tok in _VACANCY_TITLE_TOKENS)
        and any(tok in t for tok in ("communication", "comms", "pr ",
                                      "corporate affairs", "media relations"))
    )


def manager_for_signal(signal: dict) -> dict:
    """Convenience: run infer_hiring_manager off a scraped signal dict."""
    return infer_hiring_manager(
        signal.get("title") or "",
        signal.get("summary") or "",
        (signal.get("company") or "").strip(),
    )


# Patterns that surround an appointee's name in an appointment headline.
_APPOINTEE_RX = [
    re.compile(r"(?:appoints?|names?|hires?|promotes?)\s+"
               r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)"),
    re.compile(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\s+"
               r"(?:joins|appointed|promoted|named|to lead|to head)"),
    re.compile(r"new\s+(?:CCO|CEO|CHRO|chief|head of[^.]+)\s+is\s+"
               r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)"),
]
_COMMS_SLOTS = (("cmo", "head_of_brand", "head_of_marketing", "chro") if _MKT
                else ("cco", "head_of_corporate_affairs", "head_of_comms", "chro"))
# Per-company structure -> the slot to put first for a comms vacancy.
# Other slots in the original priority list fall through after.
_STRUCTURE_LEADS = {
    "chro_led": "chro",
    "corp_affairs_led": "head_of_corporate_affairs",
    "head_of_comms_led": "head_of_comms",
    "cco_led": "cco",
}


def _apply_structure(slots: tuple, structure: str) -> tuple:
    """If the company has a structure hint, put the hinted seat first.
    Prepends even if the seat isn't in the default candidate list — the
    hint is the user explicitly saying "for THIS company, that role is
    the one to try", which is more authoritative than the generic
    seniority-up rule."""
    lead = _STRUCTURE_LEADS.get((structure or "").strip().lower())
    if not lead:
        return slots
    if lead in slots:
        return (lead,) + tuple(s for s in slots if s != lead)
    return (lead,) + tuple(slots)


def _company_structure(company: str, contacts: dict | None) -> str:
    """Read the structure hint on the company's ContactCard, '' if none."""
    if not company:
        return ""
    try:
        from tool.contacts.store import load_contacts, get_contact
        if contacts is None:
            contacts = load_contacts()
        card = get_contact(contacts, company)
        return getattr(card, "structure", "") if card else ""
    except Exception:
        return ""


def _appointee_name(title: str) -> str | None:
    for pat in _APPOINTEE_RX:
        m = pat.search(title or "")
        if m:
            return m.group(1).strip()
    return None


def resolve_lead_contact(signal: dict, contacts: dict | None = None) -> dict:
    """The single, uniform contact resolver for ANY lead, whatever its
    kind. Always returns the same shape — never None, never a special
    case the caller has to branch on:

      {name, title, confidence, basis, linkedin_url}

    Strategy lives here, once:
      - vacancy        -> the reporting-line manager (seniority-up rules)
      - appointment    -> the newly appointed person named in the headline
      - filing / news  -> the comms decision-maker at that company
    then, for any of the above, the best named roster contact is layered
    on if one exists (a verified name beats a role guess).
    """
    company = (signal.get("company") or "").strip()
    kind = (signal.get("kind") or "").strip().lower()
    preset_name = ""

    if is_job_like(signal):
        inf = manager_for_signal(signal)
        title, slots = inf["manager_title"], inf["slots"]
        base_conf, basis = inf["confidence"], inf["basis"]
        # Company-specific structure (e.g. comms reports to HR) reorders
        # the slot priority before we look up the roster.
        slots = _apply_structure(slots, _company_structure(company, contacts))
        # The ad's OWN named contact outranks every inference and every
        # roster entry: the employer attached this person to THIS
        # vacancy. A printed address is published evidence (the ad URL
        # is the citation); an address the nightly pass found later
        # rides on the signal with its own status.
        try:
            from tool.contacts import ad_contact as _adc
            _ad = _adc.extract(signal)
        except Exception:
            _ad = None
        if _ad and _ad.get("name"):
            _ad_email = ((signal.get("ad_contact_email") or "").strip()
                         or (_ad.get("email") or "").strip())
            if _ad_email:
                _ad_status = (signal.get("ad_contact_email_status")
                              or "published")
                _ad_src = (signal.get("ad_contact_email_source")
                           or _ad.get("source_url") or "")
            else:
                _ad_status, _ad_src = "", ""
            return {
                "name": _ad["name"],
                "title": _ad.get("title") or title,
                "confidence": 0.88 if _ad_email else 0.85,
                "basis": "ad_named_contact",
                "linkedin_url": None,
                "stale": False,
                "verified_at": "",
                "division": "",
                "divisional_uncertain": False,
                "slot": "",
                "email": _ad_email,
                "email_status": _ad_status,
                "email_source_url": _ad_src,
            }
    elif kind in ("leadership_change", "trade_press"):
        appointee = _appointee_name(signal.get("title") or "")
        if appointee:
            preset_name = appointee
            title, slots, base_conf, basis = (
                _LEADERSHIP_ROLE, _COMMS_SLOTS, 0.6, "appointee")
        else:
            title, slots, base_conf, basis = (
                _DEFAULT_ROLE, _COMMS_SLOTS, 0.4, "role_heuristic")
    else:
        title, slots, base_conf, basis = (
            _DEFAULT_ROLE, _COMMS_SLOTS, 0.45, "role_heuristic")

    name, linkedin_url, confidence = preset_name, None, base_conf
    stale = False
    verified_at = ""
    division = ""
    divisional_uncertain = False
    slot = ""
    email = ""
    email_status = ""
    email_source_url = ""
    # For job-like leads, try the divisional roster first when the
    # lead's title/JD names a division of this parent company. This
    # catches "Head of Comms, Global Commercial Organization" at a
    # conglomerate where the group-level CCO is the wrong person.
    if not preset_name and is_job_like(signal):
        try:
            from tool import divisional_contacts as _div
            lead_text = (signal.get("title") or "") + " " + (signal.get("summary") or "")
            div_name, parent_has_divs = _div.match_division(company, lead_text)
            if div_name:
                d_entry = _div.lookup_division_entry(company, div_name, slots)
                if d_entry and d_entry.get("name"):
                    name = d_entry["name"]
                    linkedin_url = d_entry.get("linkedin_url")
                    verified_at = d_entry.get("verified_at", "")
                    division = div_name
                    confidence = round(
                        min(0.95, 0.1 + base_conf * 0.5
                            + float(d_entry.get("confidence") or 0) * 0.5), 2)
            elif parent_has_divs and _div.has_divisional_hint(lead_text):
                divisional_uncertain = True
        except Exception:
            pass

    if not preset_name and not name:
        nc = best_named_contact(company, slots, contacts=contacts)
        if nc:
            name = nc["name"]
            linkedin_url = nc.get("linkedin_url")
            stale = bool(nc.get("stale"))
            verified_at = nc.get("verified_at", "") or ""
            slot = nc.get("slot", "")
            email = nc.get("email", "") or ""
            email_status = nc.get("email_status", "") or ""
            email_source_url = nc.get("email_source_url", "") or ""
            # Verified named person: blend role-inference certainty with
            # the roster entry's own confidence, so a named hit always
            # outranks a role-only one. Stale entries are already
            # confidence-discounted upstream in best_named_contact.
            confidence = round(
                min(0.95, 0.1 + base_conf * 0.5
                    + float(nc.get("confidence") or 0) * 0.5), 2)
        # else: role-only. Confidence stays = base_conf — the honest
        # confidence in the reporting-line inference we're actually
        # showing. The missing person is already signalled by the
        # absence of a name; don't double-penalise it in the number.

    return {
        "name": name,
        "title": title,
        "confidence": confidence,
        "basis": basis,
        "linkedin_url": linkedin_url,
        "stale": stale,
        "verified_at": verified_at,
        "division": division,
        "divisional_uncertain": divisional_uncertain,
        "slot": slot,
        "email": email,
        "email_status": email_status,
        "email_source_url": email_source_url,
    }



def best_named_contact(company: str, slots: tuple,
                       contacts: dict | None = None) -> dict | None:
    """First fresh, named roster contact across `slots`. If none of the
    slots have a fresh entry, falls back to the first stale-but-named
    entry with stale=True — so the UI can surface "verify" rather than
    silently dropping to a role-only search.

    Returns {name, role_title, linkedin_url, confidence, stale,
    verified_at} or None."""
    if not company or not slots:
        return None
    try:
        from tool.contacts.store import load_contacts, get_contact
    except Exception:
        return None
    try:
        if contacts is None:
            contacts = load_contacts()
        card = get_contact(contacts, company)
    except Exception:
        return None
    if card is None:
        return None
    # Drop any contact the user has flagged as wrong (until the entry's
    # name changes — CH/manual refresh implicitly clears the flag).
    try:
        from tool import contact_flags
        flagged = contact_flags.get_flags()
    except Exception:
        flagged = {}
    stale_fallback = None
    for slot in slots:
        entry = card.get(slot)
        if not entry or not getattr(entry, "name", ""):
            continue
        key = f"{company}::{slot}"
        if flagged.get(key, {}).get("name") == entry.name:
            continue   # user flagged this exact person as wrong
        # Conservative named-tier gate: a sub-threshold entry is a weak /
        # speculative match — skip it entirely (don't even keep it as a
        # stale fallback), so the lead falls through to a role-search.
        if not entry.meets_named_confidence():
            continue
        # Email fields ride along only while they're themselves current
        # (sendable) — a dead address presented as sendable is exactly
        # the bounce-risk the schema's statuses exist to prevent.
        _email_ok = entry.email_is_sendable() if hasattr(
            entry, "email_is_sendable") else False
        _email = {
            "email": (entry.email if _email_ok else "") or "",
            "email_status": (entry.email_status if _email_ok else "") or "",
            "email_source_url": (getattr(entry, "email_source_url", "")
                                 if _email_ok else "") or "",
        }
        if entry.is_fresh():
            return {
                "name": entry.name,
                "role_title": getattr(entry, "role_title", "") or "",
                "linkedin_url": entry.linkedin_url,
                "confidence": getattr(entry, "confidence", 0.0),
                "stale": False,
                "verified_at": getattr(entry, "verified_at", "") or "",
                "slot": slot,
                **_email,
            }
        if stale_fallback is None:
            stale_fallback = {
                "name": entry.name,
                "role_title": getattr(entry, "role_title", "") or "",
                "linkedin_url": entry.linkedin_url,
                "confidence": max(0.0, getattr(entry, "confidence", 0.0) - 0.2),
                "stale": True,
                "verified_at": getattr(entry, "verified_at", "") or "",
                "slot": slot,
                **_email,
            }
    return stale_fallback


# --- BD Point of Contact ---------------------------------------------
# For a BD lead (predictor stack, funding round, warm signal) the right
# first conversation is NEVER the CEO/CFO: it's the senior owner of the
# function future hires will sit in — comms/corporate affairs (or
# marketing on that desk) — or the HR leader who runs the search. These
# slot families deliberately exclude every statutory seat (ceo, cfo,
# chair, gc, ir_director). Same roster, same flags, same freshness rules
# as every other reader; LinkedIn is the only channel here by design —
# no email lookups, no Hunter spend.
_BD_POC_SLOTS = {
    "comms": ("cco", "head_of_corporate_affairs", "head_of_comms",
              "head_of_ic", "chro"),
    "marketing": ("cmo", "head_of_brand", "head_of_marketing",
                  "head_of_growth", "chro"),
}
# Verified-or-fallback: when the roster has no named owner, a precise
# Recruiter role-search beats a guess. Two searches per desk — the
# function owner, then the HR route.
_BD_POC_FALLBACKS = {
    "comms": ("Communications Director", "HR Director"),
    "marketing": ("Marketing Director", "HR Director"),
}


def _li_talent_search(keywords: str) -> str:
    """Same Recruiter Talent-search shape the rest of the dashboard
    uses (Sara has Recruiter) — keyword pre-filled, never a dead end."""
    from urllib.parse import quote_plus
    return ("https://www.linkedin.com/talent/search?keywords="
            + quote_plus((keywords or "").strip()))


def bd_points_of_contact(company: str, desk: str | None = None,
                         contacts: dict | None = None,
                         limit: int = 3) -> list[dict]:
    """The people to open the BD conversation with at `company`:

      [{name, title, url, stale}]

    Named, confidence-gated roster entries across the desk's
    comms/marketing + HR slot families (fresh first, stale flagged for
    re-verification, user-flagged entries skipped, deduped, capped at
    `limit`); each links to the person's LinkedIn profile when the
    roster holds one, else a precise name+company Recruiter search.
    With no named owner at all, returns role-search fallbacks instead
    — never empty, never a C-suite statutory seat. Never raises."""
    try:
        desk = (desk or active_profile().key or "comms").strip().lower()
    except Exception:
        desk = "comms"
    slots = _BD_POC_SLOTS.get(desk, _BD_POC_SLOTS["comms"])
    company = (company or "").strip()
    fresh: list[dict] = []
    stale: list[dict] = []
    if company and company != "—":
        try:
            from tool.contacts.store import load_contacts, get_contact
            from tool.contacts.routing import display_title_for_slot
            if contacts is None:
                contacts = load_contacts()
            card = get_contact(contacts, company)
            try:
                from tool import contact_flags
                flagged = contact_flags.get_flags()
            except Exception:
                flagged = {}
            seen: set = set()
            for slot in slots:
                entry = card.get(slot) if card else None
                if not entry or not getattr(entry, "name", ""):
                    continue
                if flagged.get(f"{company}::{slot}", {}).get("name") \
                        == entry.name:
                    continue
                if not entry.meets_named_confidence():
                    continue
                key = entry.name.strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                item = {
                    "name": entry.name,
                    "title": (getattr(entry, "role_title", "")
                              or display_title_for_slot(slot)),
                    "url": (entry.linkedin_url
                            or _li_talent_search(
                                f'"{entry.name}" "{company}"')),
                    "stale": not entry.is_fresh(),
                }
                (fresh if entry.is_fresh() else stale).append(item)
        except Exception:
            pass
    out = (fresh + stale)[:limit]
    if out:
        return out
    return [{
        "name": "",
        "title": t,
        "url": _li_talent_search(f'"{t}" "{company}"' if company else t),
        "stale": False,
    } for t in _BD_POC_FALLBACKS.get(desk, _BD_POC_FALLBACKS["comms"])]
