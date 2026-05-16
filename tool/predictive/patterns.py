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


# ---- Early regulatory probe (pre-enforcement) --------------------------
# Broadens REGULATOR_ACTION (which only fires on enforcement-grade
# language + a £5m+ amount, see detector.py) to catch the *opening* of
# an investigation / probe / inquiry — the point at which a 6-12 month
# crisis-comms / reputation-Director hiring window starts, well before
# any fine lands. This is the signal class that previously only lived in
# the standalone Distress Watch panel.
REGULATOR_PROBE_EARLY = TriggerType(
    key="regulator_probe_early",
    label="Early regulatory probe",
    weight=0.7,
    lead_time_weeks=(6, 26),
    who_to_call="CCO / GC — pitch crisis-comms interim ahead of the live period",
    implication=(
        "A regulator has opened an investigation / probe into {company} "
        "(pre-enforcement). These run 6-12 months and typically trigger "
        "an interim crisis-comms hire and a reputation-facing Corporate "
        "Affairs review before any penalty is decided."
    ),
    patterns=[
        re.compile(p, re.IGNORECASE) for p in (
            r"\b(?:FCA|PRA|Ofcom|Ofgem|Ofwat|CMA|SFO|ICO)\b.{0,40}\b(?:investigation|probe|inquiry|opens? (?:an? )?(?:investigation|probe|inquiry))\b",
            r"\b(?:investigation|probe|inquiry) (?:by|from|into|launched by) (?:the )?(?:FCA|PRA|Ofcom|Ofgem|Ofwat|CMA|SFO|ICO)\b",
            r"\b(?:section 166|skilled person review)\b",
            r"\bunder investigation by\b",
            r"\b(?:CMA|FCA|Ofcom|Ofgem|Ofwat) (?:launches|opens|begins) (?:a |an )?(?:strategic market status |market |formal )?(?:investigation|inquiry|probe|review into)\b",
        )
    ],
)


# ---- Crisis event (breach / litigation / suspension) -------------------
# Cyber / data breach, class action or group litigation, suspended
# trading — high-conversion crisis-comms triggers at a watchlist entity.
# Account relevance is enforced downstream by extract_company (no
# company extracted → dropped, same as every other trigger).
CRISIS_EVENT = TriggerType(
    key="crisis_event",
    label="Crisis event (breach / litigation / suspension)",
    weight=0.75,
    lead_time_weeks=(2, 16),
    who_to_call="CCO / GC — pitch crisis-comms interim immediately",
    implication=(
        "A crisis event at {company} (data breach / cyber / litigation / "
        "trading suspension). Interim crisis-comms capacity is usually "
        "engaged within days and a permanent reputation hire follows."
    ),
    patterns=[
        re.compile(p, re.IGNORECASE) for p in (
            r"\b(?:data breach|cyber[- ]?attack|cyberattack|ransomware|hacked|"
            r"data leak|security breach)\b",
            r"\b(?:class action|group litigation|group claim)\b",
            r"\bsuspended trading\b|\btrading (?:in its shares )?suspended\b",
            r"\b(?:major|nationwide|systemwide|system-wide) outage\b",
        )
    ],
)


# ---- Profit warning / negative trading update --------------------------
# Phase 1 amend: the densest free signal in the UK market (Investegate
# RNS). A profit warning is statistically associated with crisis-comms
# interim demand and, within 6-12 months, permanent corporate-affairs
# and IR hires. Deliberately excluded earlier as "downstream of
# restructure"; that call is reversed per the detection-engine report.
PROFIT_WARNING = TriggerType(
    key="profit_warning",
    label="Profit warning / negative trading update",
    weight=0.75,
    lead_time_weeks=(2, 26),
    who_to_call="CCO / IR Director — crisis-comms interim now, permanent CorpAffairs/IR follows",
    implication=(
        "A profit warning / materially negative trading update at "
        "{company}. Associated with near-term crisis-comms interim "
        "demand and a permanent Corporate Affairs / IR hire within "
        "6-12 months."
    ),
    patterns=[
        re.compile(p, re.IGNORECASE) for p in (
            r"\bprofit warning\b",
            r"\bissues?\b.{0,30}\bprofit warning\b",
            r"\bwarns?\b.{0,20}\bon\b.{0,20}\b(?:full[- ]year|FY|annual)?\s*(?:profit|profits|earnings|guidance|outlook)\b",
            r"\b(?:profit|profits|earnings|revenue|sales)\b.{0,40}\b(?:materially |significantly |substantially )?(?:below|behind|short of)\b.{0,40}\b(?:expectation|expectations|forecast|guidance|consensus|market)\b",
            r"\b(?:significantly|materially|substantially)\s+below\b.{0,30}\b(?:expectation|expectations|guidance|forecast|consensus)\b",
            r"\b(?:downgrades?|cuts?|lowers?|reduces?)\b.{0,30}\b(?:full[- ]year |FY |annual )?(?:guidance|outlook|forecast|profit (?:guidance|expectations?))\b",
            r"\b(?:trading update|trading statement|update on trading)\b.{0,70}\b(?:below|behind|shortfall|disappointing|weaker than|deteriorat)\b",
        )
    ],
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


# ---- CFO change -- often drives investor-narrative refresh -----------
CFO_CHANGE = TriggerType(
    key="cfo_change",
    label="CFO change",
    weight=0.5,
    lead_time_weeks=(12, 24),
    who_to_call="Head of IR / CCO — investor-narrative refresh usually triggers a comms hire",
    implication=(
        "New / departing CFO at {company}. CFO changes often drive an "
        "investor-narrative refresh that pulls through a Corporate Affairs / "
        "Comms hire within 12–24 weeks."
    ),
    patterns=_rx(
        r"new chief financial officer",
        r"\bnew cfo\b",
        r"incoming chief financial officer",
        r"appointment of.{0,40}chief financial officer",
        r"appointment of.{0,20}cfo\b",
        r"appointed as chief financial officer",
        r"appointed as cfo",
        r"to step down as chief financial officer",
        r"stepping down as chief financial officer",
        r"steps down as chief financial officer",
        r"resignation of.{0,40}chief financial officer",
        r"departs as cfo",
        r"departs as chief financial officer",
    ),
)


# ---- IR Director / Head of Investor Relations change ----------------
IR_DIRECTOR_CHANGE = TriggerType(
    key="ir_director_change",
    label="IR Director change",
    weight=0.5,
    lead_time_weeks=(6, 16),
    who_to_call="The new IR Director directly — IR + Corp Affairs hires often paired",
    implication=(
        "New Head of Investor Relations / IR Director at {company}. "
        "Capital-markets repositioning typically triggers a paired Corporate "
        "Affairs hire within 6–16 weeks."
    ),
    patterns=_rx(
        r"new head of investor relations",
        r"new director of investor relations",
        r"new ir director",
        r"appointment of.{0,40}head of investor relations",
        r"appointment of.{0,40}director of investor relations",
        r"appointment of.{0,30}ir director",
        r"appointed.{0,20}head of investor relations",
        r"appointed.{0,30}ir director",
        r"to step down as head of investor relations",
        r"stepping down as head of investor relations",
        r"new chief investor officer",
    ),
)


# ---- Comms-leader departure (the closest thing to mind-reading) ------
# When a named senior comms person leaves a known company in trade press,
# that company has an OPEN senior comms vacancy right now — often weeks
# before the role is advertised. Highest-yield Phase-2 trigger.
COMMS_LEADER_DEPARTURE = TriggerType(
    key="comms_leader_departure",
    label="Senior comms leader departure",
    weight=1.2,
    lead_time_weeks=(0, 12),
    who_to_call="CHRO / Chief People Officer — vacancy is open NOW",
    implication=(
        "Named senior comms leader has left / is leaving {company} per trade "
        "press. The role is OPEN now — they'll need to replace within 12 "
        "weeks. This is pre-advert signal: most external recruiters won't "
        "spot the vacancy until the role hits a job board, ~4–8 weeks behind."
    ),
    patterns=[
        # Departure verbs anchored on senior comms titles. Comms-role
        # keyword + leaving verb in the same ~120-char window so we
        # don't false-fire on a CEO article that mentions "communications"
        # elsewhere. Covers both "Head of X" and "X Director" word order.
        re.compile(
            r"(?:head of (?:internal|corporate|external)?\s*comm(?:s|unications)|"
            r"director of (?:internal|corporate|external)?\s*comm(?:s|unications)|"
            r"(?:internal|external|corporate)?\s*communications director|"
            r"(?:internal|external)?\s*comms director|"
            r"(?:chief|corporate) communications officer|"
            r"corporate affairs director|"
            r"(?:head|director) of corporate affairs|"
            r"(?:head|director) of (?:media relations|pr|public relations)|"
            r"(?:media relations|pr|public relations) director|"
            r"pr director)"
            r".{0,120}?"
            r"(?:has left|is leaving|departs|departed|departing|"
            r"steps down|stepping down|stepped down|resigns|resigned|"
            r"to leave|to depart|exit(?:s|ing|ed)|"
            r"on gardening leave|to step down|moves to|joining|moving to)",
            re.IGNORECASE,
        ),
        # Inverted phrasing: "X departs/leaves as [comms title]"
        re.compile(
            r"(?:departs|leaves|exits|resigns|stepping down)"
            r".{0,40}"
            r"(?:as|from)\s+"
            r"(?:head of (?:internal|corporate|external)?\s*comm(?:s|unications)|"
            r"director of (?:internal|corporate|external)?\s*comm(?:s|unications)|"
            r"(?:communications|comms) director|"
            r"(?:chief|corporate) communications officer|"
            r"corporate affairs director|"
            r"(?:head|director) of (?:corporate affairs|media relations|pr)|"
            r"pr director)",
            re.IGNORECASE,
        ),
        # "Company loses its CCO" / "X loses Head of Communications"
        re.compile(
            r"loses\s+(?:its\s+)?(?:head of (?:internal|corporate|external)?\s*comm(?:s|unications)|"
            r"director of comm(?:s|unications)|"
            r"(?:communications|comms) director|"
            r"(?:chief|corporate) communications officer|"
            r"corporate affairs director|"
            r"pr director)",
            re.IGNORECASE,
        ),
    ],
)


# ---- IC platform RFP / case-study leak / adjacent-job-ad mention -----
# Internal Communications + employee-engagement platforms whose
# purchase or RFP almost always coincides with a senior comms hire
# decision. Adjacent job ads that require experience with one of these
# platforms are a strong "this employer has just bought / is buying"
# signal — the senior hire follows in 8–12 weeks.
IC_PLATFORM_VENDORS = [
    # Internal comms / intranet platforms
    "Staffbase", "Poppulo", "Workvivo", "Simpplr", "Firstup",
    "SocialChorus", "LumApps", "Unily", "Smarp", "Beekeeper",
    "Sociabble", "Haiilo", "Interact Intranet", "Happeo",
    # Employee engagement / survey platforms
    "Culture Amp", "Peakon", "Glint", "Officevibe", "Lattice", "15Five",
    # Microsoft / large vendor employee experience
    "Viva Engage", "Yammer",
]
_IC_VENDOR_ALT = "|".join(re.escape(v) for v in IC_PLATFORM_VENDORS)

IC_PLATFORM_RFP = TriggerType(
    key="ic_platform_rfp",
    label="IC platform RFP / leak",
    weight=1.0,
    lead_time_weeks=(6, 12),
    who_to_call="CHRO / Head of HR — senior IC hire likely in 6–12 weeks",
    implication=(
        "Internal-communications platform activity detected at {company}. "
        "Procurement / case-study / adjacent-job-ad signals correlate with "
        "a senior comms hire within 6–12 weeks (vendor purchase + senior hire "
        "are two sides of the same decision)."
    ),
    patterns=[
        # Procurement / RFP wording
        re.compile(r"\b(?:RFP|invitation to tender|ITT|request for proposal|"
                   r"tender notice|framework agreement|contract award|"
                   r"supplier selection)\b.{0,200}(?:" + _IC_VENDOR_ALT +
                   r"|internal communications platform|employee engagement platform|"
                   r"employee experience platform|intranet platform)", re.IGNORECASE),
        # Trade-press / vendor case-study leak: "Acme selected Staffbase"
        re.compile(r"(?:selected|chose|chosen|deployed|rolled out|partners with|"
                   r"now uses|switching to|adopts?)\s+(?:" + _IC_VENDOR_ALT + r")",
                   re.IGNORECASE),
        re.compile(r"(?:" + _IC_VENDOR_ALT + r")\s+(?:customer|case study|"
                   r"deployment|win|client|partnership)", re.IGNORECASE),
        # Adjacent job ad — mid-level comms role requiring named platform
        # (caught in either title or description by the detector pipeline)
        re.compile(r"experience\s+(?:with|of|using)\s+(?:" + _IC_VENDOR_ALT + r")",
                   re.IGNORECASE),
        re.compile(r"(?:" + _IC_VENDOR_ALT + r")\s+(?:experience|knowledge|"
                   r"proficiency|expertise|administrator)", re.IGNORECASE),
    ],
)


# ---- IPO / listing activity ------------------------------------------
# Companies preparing to list (or re-list, or move markets) build out
# Corporate Affairs + IR aggressively in the 3–4 month run-up to
# admission. High-yield, narrow window, very specific RNS language.
IPO_LISTING = TriggerType(
    key="ipo_listing",
    label="IPO / listing activity",
    weight=1.0,
    lead_time_weeks=(4, 16),
    who_to_call="CFO office — IR + Corporate Affairs hires both go in pre-admission",
    implication=(
        "Pre-IPO / listing activity at {company}. Listing companies "
        "typically build out Corporate Affairs + IR teams in the 4–16 "
        "weeks pre-admission; a senior comms hire is almost certain."
    ),
    patterns=_rx(
        r"intention to float",
        r"intention to seek admission",
        r"admission to (?:aim|the main market|the london stock exchange|the lse|aquis)",
        r"to be admitted to (?:aim|the main market|the london stock exchange|the lse|aquis)",
        r"publication of (?:the )?prospectus",
        r"prospectus published",
        r"prospectus approved",
        r"direct listing",
        r"announces (?:the )?initial public offering",
        r"initial public offering of",
        r"placing and admission",
        r"\bipo\b",
        r"first day of (?:dealing|conditional dealing)",
    ),
)


# ---- Material contract loss / termination ---------------------------
# Loss of marquee revenue triggers a defensive comms hire (reposition
# the equity story, calm customers/staff). Lower base-rate than M&A
# but a clean signal when material. Detector gates this to RNS sources
# or £5m+ amounts to avoid sports/HR false positives.
CONTRACT_LOSS = TriggerType(
    key="contract_loss",
    label="Material contract loss",
    weight=0.7,
    lead_time_weeks=(4, 16),
    who_to_call="Head of Communications / CCO — reposition + reputation defence",
    implication=(
        "Material contract / customer loss disclosed at {company}. "
        "Loss of marquee revenue typically pulls through a defensive "
        "Corporate Affairs hire (reposition the equity story) within 4–16 weeks."
    ),
    patterns=_rx(
        # Explicit material-qualifier wording (high-confidence on its own)
        r"loss of (?:a |the |its )?(?:major|material|key|marquee|flagship|significant) (?:customer|client|contract|account)",
        r"(?:lost|loses) (?:its |a |the )?(?:major|material|key|marquee|flagship|significant) (?:customer|client|contract|account)",
        # Explicit £-amount wording (materiality embedded in the phrase)
        r"loss of (?:a |the )?£\s?\d+(?:[.,]\d+)?\s?(?:m|mn|million|bn|billion)?\s?(?:contract|customer|client|account)",
        r"(?:lost|loses) (?:a |the )?contract worth (?:£|\$|€)",
        r"loss of contract worth (?:£|\$|€)",
        # Termination / non-renewal phrasing — gated by detector tier or £ amount
        r"termination of (?:the )?(?:contract|agreement) with",
        r"contract (?:has been |was |is being )?terminated",
        r"contract.{0,20}not (?:been )?renewed",
        r"non-renewal of (?:the )?contract",
        r"cancellation of (?:the )?(?:contract|framework)",
    ),
)


# ---- Press release velocity spike ------------------------------------
# Not regex-matched — emitted by tool/predictive/velocity.py when a
# company's press output triples vs its 90-day rolling baseline.
# Registered here so render.py + linkedin_resolver + dashboard wiring
# treat it as a known trigger key.
PRESS_VELOCITY_SPIKE = TriggerType(
    key="press_velocity_spike",
    label="Press release velocity spike",
    weight=0.7,
    lead_time_weeks=(8, 24),
    who_to_call="Head of Communications / CCO — velocity spike often precedes Corp Affairs hire",
    implication=(
        "Press release output at {company} has tripled vs the 90-day "
        "baseline. Sustained velocity spikes empirically precede senior "
        "Corp Comms / IR hires within 8–24 weeks (the team has more to "
        "say than capacity to say it)."
    ),
    patterns=[],   # emitted by velocity.detect_velocity_spikes, not regex
)


TRIGGERS = [CEO_CHANGE, CHAIR_CHANGE, CHRO_CHANGE, CFO_CHANGE,
            IR_DIRECTOR_CHANGE, COMMS_LEADER_DEPARTURE,
            MNA, REGULATOR_ACTION, REGULATOR_PROBE_EARLY, CRISIS_EVENT,
            PROFIT_WARNING, RESTRUCTURE, IC_PLATFORM_RFP,
            IPO_LISTING, CONTRACT_LOSS, PRESS_VELOCITY_SPIKE]
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
