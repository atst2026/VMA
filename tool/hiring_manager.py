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
        return _result("Director of Communications", _DIRECTOR_SLOTS, 0.7)
    if is_ic:
        # IC Manager / Specialist / Officer -> Head of Internal Comms
        return _result("Head of Internal Communications", _HEAD_IC_SLOTS, 0.7)
    if is_comms and is_senior:
        # Comms Director / Head of Corporate Affairs -> CCO
        return _result("Chief Communications Officer", _CCO_SLOTS, 0.65)
    if is_comms and (is_junior or "head of" in t):
        # Corporate / general Comms Manager -> Director of Communications
        return _result("Director of Communications", _DIRECTOR_SLOTS, 0.7)
    if is_comms:
        return _result("Director of Communications", _DIRECTOR_SLOTS, 0.5)

    # No comms signal in the title — generic fallback.
    return _result("Head of Communications", _CCO_SLOTS, 0.3, basis="default")


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


def best_named_contact(company: str, slots: tuple,
                       contacts: dict | None = None) -> dict | None:
    """First fresh, named roster contact across `slots`, or None.

    {name, role_title, linkedin_url, confidence}. Isolated so a missing
    or broken contacts layer never breaks lead enrichment. Pass a
    preloaded `contacts` dict to avoid a per-call disk read when
    enriching many leads in one pass (morning-brief loop)."""
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
    for slot in slots:
        entry = card.get(slot)
        if entry and getattr(entry, "name", "") and entry.is_fresh():
            return {
                "name": entry.name,
                "role_title": getattr(entry, "role_title", "") or "",
                "linkedin_url": entry.linkedin_url,
                "confidence": getattr(entry, "confidence", 0.0),
            }
    return None
