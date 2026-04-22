"""Regex library for each predictive trigger type.

Word-boundary matched, case-insensitive. Each trigger has a `patterns` list
of regexes and a `min_score` threshold — a signal has to hit at least one
pattern to count.
"""
from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class TriggerType:
    key: str                   # machine id used in ranker/output
    label: str                 # human-facing in the email
    weight: float              # trigger_weight in the score formula
    lead_time_weeks: tuple[int, int]   # (min, max) predictive window
    who_to_call: str           # mapping to the recommended contact
    implication: str           # template — {company} and {evidence} filled in
    patterns: list[re.Pattern]


def _rx(*terms: str) -> list[re.Pattern]:
    """Compile case-insensitive word-boundary regexes."""
    return [re.compile(r"\b" + t + r"\b", re.IGNORECASE) for t in terms]


# ---- CEO / MD appointment or departure ---------------------------------
CEO_CHANGE = TriggerType(
    key="ceo_change",
    label="CEO change",
    weight=1.0,
    lead_time_weeks=(6, 12),
    who_to_call="CPO / CHRO",
    implication=(
        "External CEO/MD change at {company}. Incoming CEO typically reviews "
        "C-suite within 6 months; Corporate Affairs / Head of Comms "
        "replacement likely in 6–12 weeks."
    ),
    patterns=_rx(
        r"new chief executive",
        r"incoming chief executive",
        r"appointment of.{0,40}chief executive",
        r"appointment of.{0,20}ceo\b",
        r"\bnew ceo\b",
        r"appointed as chief executive",
        r"appointed as ceo",
        r"to step down as chief executive",
        r"stepping down as chief executive",
        r"steps down as chief executive",
        r"resignation of.{0,40}chief executive",
        r"departs as ceo",
        r"departs as chief executive",
        r"chief executive.{0,20}to leave",
        r"chief executive.{0,20}to retire",
        r"new managing director",
        r"appointment of.{0,40}managing director",
    ),
)


# ---- Chair / Chairman appointment or departure -------------------------
CHAIR_CHANGE = TriggerType(
    key="chair_change",
    label="Chair change",
    weight=0.8,
    lead_time_weeks=(8, 16),
    who_to_call="CEO office / CPO",
    implication=(
        "New Chair at {company}. Board dynamics commonly trigger a comms "
        "review within 8–16 weeks."
    ),
    patterns=_rx(
        r"new chair\b",
        r"\bnew chairman\b",
        r"chair designate",
        r"appointment of.{0,20}chair",
        r"appointed.{0,20}chair",
        r"to step down as chair",
        r"stepping down as chair",
        r"resignation of.{0,20}chair",
    ),
)


# ---- CHRO / CPO / HR Director -----------------------------------------
CHRO_CHANGE = TriggerType(
    key="chro_change",
    label="CHRO / HR leadership change",
    weight=0.6,
    lead_time_weeks=(8, 16),
    who_to_call="The new CHRO directly",
    implication=(
        "New CHRO / People Director at {company}. Internal Comms reports to "
        "HR at ~40% of UK mid-caps — expect a comms direct-report refresh "
        "within 8–16 weeks."
    ),
    patterns=_rx(
        r"new chief people officer",
        r"new chief human resources officer",
        r"\bnew chro\b",
        r"appointment of.{0,40}chief people officer",
        r"appointment of.{0,40}chief human resources",
        r"appointment of.{0,40}hr director",
        r"appointment of.{0,40}people director",
        r"appointed.{0,20}chief people officer",
        r"appointed.{0,20}chief human resources",
        r"new hr director",
        r"new people director",
    ),
)


# ---- M&A -- acquisition / merger / takeover ----------------------------
MNA = TriggerType(
    key="mna",
    label="M&A announcement",
    weight=0.9,
    lead_time_weeks=(12, 36),
    who_to_call="CCO of acquirer; Corp Affairs Director at both sides",
    implication=(
        "Major M&A at {company}. Post-close comms integration or rebrand "
        "hire typical within 3–12 months."
    ),
    patterns=_rx(
        r"recommended cash offer",
        r"firm intention to make an offer",
        r"scheme of arrangement",
        r"\bto acquire\b",
        r"\bacquires\b",
        r"acquisition of",
        r"agreed to buy",
        r"merger with",
        r"\btakeover\b",
        r"agreed acquisition",
        r"offer for.{0,40}(plc|limited|ltd|group)",
    ),
)


# ---- Regulator action -- fines / enforcement ---------------------------
REGULATOR_ACTION = TriggerType(
    key="regulator_action",
    label="Material regulator action",
    weight=0.8,
    lead_time_weeks=(2, 12),
    who_to_call="HR Director — pitch crisis comms interim + permanent",
    implication=(
        "Material regulator action against {company}. Reputation exposure "
        "often triggers interim crisis comms hire within 2–8 weeks and a "
        "permanent Head of Comms review shortly after."
    ),
    patterns=_rx(
        r"\bfines\b.{0,60}(£|$|€)",
        r"\bfined\b.{0,60}(£|$|€)",
        r"enforcement action",
        r"financial penalty",
        r"penalty of.{0,40}(£|$|€)",
        r"prohibits.{0,40}individual",
        r"prohibition order",
        r"censure",
        r"formally investigat",
    ),
)


# ---- Restructure / transformation / strategic review -------------------
RESTRUCTURE = TriggerType(
    key="restructure",
    label="Restructure / transformation announced",
    weight=0.6,
    lead_time_weeks=(8, 24),
    who_to_call="CHRO / Head of Transformation",
    implication=(
        "Restructure or strategic review at {company}. Comms function is "
        "commonly reorganised within 2–6 months."
    ),
    patterns=_rx(
        r"strategic review",
        r"\brestructure\b",
        r"\brestructuring\b",
        r"\breorganisation\b",
        r"\breorganization\b",
        r"transformation programme",
        r"transformation program",
        r"cost reduction programme",
        r"operating model review",
        r"business simplification",
    ),
)


TRIGGERS = [CEO_CHANGE, CHAIR_CHANGE, CHRO_CHANGE, MNA, REGULATOR_ACTION, RESTRUCTURE]
BY_KEY = {t.key: t for t in TRIGGERS}


def match_triggers(text: str) -> list[TriggerType]:
    """Return every trigger type whose patterns hit in text."""
    if not text:
        return []
    hits = []
    for t in TRIGGERS:
        if any(p.search(text) for p in t.patterns):
            hits.append(t)
    return hits


# ---- Material regulator action threshold (£5m) -------------------------
# For a regulator hit to count as predictive, we want material actions only.
# Extract a £ amount from the text and gate on >= £5m.
_AMOUNT_RX = re.compile(
    r"(£|\$|€)\s?(\d+(?:[,.]\d+)?)\s?(m|mn|million|bn|billion|k|thousand)?",
    re.IGNORECASE,
)


def extract_gbp_amount_millions(text: str) -> float | None:
    """Rough GBP-amount extractor. Returns millions GBP, or None if unclear.
    Treats $ and € as roughly equivalent to £ for material-threshold purposes
    (close enough for gating)."""
    if not text:
        return None
    m = _AMOUNT_RX.search(text)
    if not m:
        return None
    try:
        n = float(m.group(2).replace(",", ""))
    except ValueError:
        return None
    unit = (m.group(3) or "").lower()
    if unit in ("m", "mn", "million"):
        return n
    if unit in ("bn", "billion"):
        return n * 1000
    if unit in ("k", "thousand"):
        return n / 1000
    # no unit: assume full pounds, convert to millions
    return n / 1_000_000


# ---- Mid-level vs senior comms role ------------------------------------
# Used by cluster.py. A cluster is "2+ mid-level roles and no senior role"
# at the same employer.
SENIOR_RX = re.compile(
    r"\b(head of|director of|chief communications|vp communications|"
    r"vice president communications|global head of|group head of)\b",
    re.IGNORECASE,
)
MID_RX = re.compile(
    r"\b(communications manager|comms manager|senior communications|"
    r"senior comms|internal comms lead|internal communications lead|"
    r"pr manager|media relations manager|corporate affairs manager|"
    r"communications lead|pr lead|content lead)\b",
    re.IGNORECASE,
)


def is_senior_comms(title: str) -> bool:
    return bool(SENIOR_RX.search(title or ""))


def is_midlevel_comms(title: str) -> bool:
    t = title or ""
    if is_senior_comms(t):
        return False
    return bool(MID_RX.search(t))
