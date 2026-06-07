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
        r"appointed.{0,20}(?:chief executive|ceo)",
        r"to step down as chief executive",
        r"stepping down as chief executive",
        r"steps down as chief executive",
        r"(?:will |to )?step down as.{0,10}(?:chief executive|ceo)",
        r"resignation of.{0,40}chief executive",
        r"departs as ceo",
        r"departs as chief executive",
        r"(?:chief executive|\bceo)\b.{0,25}(?:to leave|is to leave|to retire|to step down|steps down|stepping down|to depart|has left|appointed)",
        r"departure of.{0,15}(?:chief executive|ceo)",
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
        r"appointed.{0,20}chair(?:man|woman|person)?\b",
        r"to step down as chair",
        r"stepping down as chair",
        r"steps down as chair",
        r"step down as chair",
        r"resignation of.{0,20}chair",
        r"chair(?:man|woman)?\b.{0,25}(?:to step down|to leave|is to leave|to retire|steps down|stepping down|to depart)",
        r"departure of.{0,15}chair",
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
        r"appointed.{0,25}(?:chief people officer|chief human resources officer|hr director|people director|chro)",
        r"new hr director",
        r"new people director",
        r"step(?:s|ping)? down as.{0,20}(?:chief people officer|people director|hr director|group people director|chro)",
        r"(?:chief people officer|group people director|people director|hr director|chro)\b.{0,25}(?:to step down|to leave|is to leave|steps down|stepping down|departs|to depart|has left)",
        r"departure of.{0,20}(?:chief people officer|people director|hr director)",
    ),
)


# ---- M&A -- generic merger / takeover (integration window) -------------
# Split into three sub-types: a completed PE acquisition and an activist
# stake disclosure each carry a distinct (faster) hiring window and a
# different downstream comms brief, so they get their own triggers below.
# This generic MNA covers the residual "agreed merger / takeover" case.
MNA = TriggerType(
    key="mna",
    label="M&A (integration)",
    weight=0.9,
    lead_time_weeks=(26, 52),
    who_to_call="CCO of acquirer; Corp Affairs Director at both sides",
    implication=(
        "Agreed M&A at {company}. Post-close integration / rebrand comms "
        "hire is typical across a 6–12 month integration window."
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
        r"all-share merger",
        r"\btakeover\b",
        r"agreed acquisition",
        r"offer for.{0,40}(plc|limited|ltd|group)",
    ),
)


# ---- Activist stake / shareholder pressure -----------------------------
# UK 3%+ major-holding (TR-1) disclosures and activist campaigns. Distinct
# from generic M&A: a 3–6 month senior-comms window opens for reputation
# defence and EGM/shareholder messaging.
ACTIVIST_STAKE = TriggerType(
    key="activist_stake",
    label="Activist stake / shareholder pressure",
    weight=0.9,
    lead_time_weeks=(12, 26),
    who_to_call="CCO / Corporate Affairs Director — reputation defence + EGM/shareholder messaging",
    implication=(
        "Activist stake-building / shareholder pressure at {company}. "
        "Reputation defence and EGM messaging drive a senior corporate-"
        "affairs / comms hire within ~3–6 months — open the retained "
        "search now, ahead of any board fight."
    ),
    patterns=_rx(
        r"activist (?:investor|fund|hedge fund|shareholder)",
        r"builds? a? ?stake",
        r"increased its (?:stake|holding)",
        r"discloses? a.{0,20}stake",
        r"\d+(?:\.\d+)?% stake",
        r"calls for.{0,30}(?:board|chair|chief executive|strategic review|break-?up|sale|spin-?off)",
        r"requisition(?:s|ed)?.{0,20}(?:general meeting|egm)",
        r"\bTR-1\b",
        r"notification of major (?:holdings|interest)",
        r"building a position in",
    ),
)


# ---- PE acquisition completion (take-private) --------------------------
# Completed UK mid-cap private-equity buyouts. The fastest window: a new-
# ownership narrative + frequent CFO/CCO churn open a 60–120 day senior
# comms hiring window.
PE_ACQUISITION = TriggerType(
    key="pe_acquisition",
    label="PE acquisition / take-private (completed)",
    weight=0.95,
    lead_time_weeks=(8, 17),
    who_to_call="Incoming CFO/CCO under new ownership; Corporate Affairs Director",
    implication=(
        "PE acquisition / take-private at {company}. New-ownership narrative "
        "and frequent CFO/CCO churn drive a senior comms hire within ~60–120 "
        "days — the fastest of the M&A sub-windows."
    ),
    patterns=_rx(
        r"take[- ]private",
        r"to be taken private",
        r"taken private",
        r"completes? (?:the )?acquisition of",
        r"completion of the acquisition",
        r"(?:backed by|owned by).{0,30}private equity",
        r"private equity.{0,30}(?:acquire|acquisition|buyout|takes? control)",
        r"\bbuyout\b",
        r"(?:KKR|Blackstone|Carlyle|Apollo|CVC|EQT|Permira|Cinven|Bain Capital|TPG|Advent|Hg Capital|Bridgepoint|Apax|Thoma Bravo)\b.{0,40}(?:acquire|acquisition|takeover|to buy|take control)",
    ),
)


# ---- Regulator action -- fines / enforcement ---------------------------
REGULATOR_ACTION = TriggerType(
    key="regulator_action",
    label="Material regulator action",
    weight=0.8,
    lead_time_weeks=(2, 12),
    who_to_call="CCO / HR Director — open the retained search for the permanent reputation/comms hire now",
    implication=(
        "Material regulator action against {company}. Reputation exposure "
        "triggers a permanent Head of Comms / Corporate Affairs hire and a "
        "comms-function review within ~2–12 weeks — the window to land the "
        "retained mandate is now, before the search is run internally."
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
    who_to_call="CCO / GC — secure the retained brief for the permanent reputation hire ahead of the live period",
    implication=(
        "A regulator has opened an investigation / probe into {company} "
        "(pre-enforcement). These run 6-12 months and typically trigger a "
        "permanent reputation-facing Corporate Affairs hire and a comms-"
        "function review before any penalty is decided — a long retained-"
        "search runway if engaged early."
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
    who_to_call="CCO / GC — pitch the retained search for the permanent reputation hire immediately",
    implication=(
        "A crisis event at {company} (data breach / cyber / litigation / "
        "trading suspension). A permanent reputation / Corporate Affairs "
        "hire follows within weeks; being in front of the decision-maker "
        "on day one is what wins the retained mandate."
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
# RNS). A profit warning is statistically associated with a permanent
# Corporate Affairs / IR hire within 6-12 months — the billable event
# for an Exec Search / Permanent firm. Deliberately excluded earlier as
# "downstream of restructure"; reversed per the detection-engine report.
PROFIT_WARNING = TriggerType(
    key="profit_warning",
    label="Profit warning / negative trading update",
    weight=0.75,
    lead_time_weeks=(2, 26),
    who_to_call="CCO / IR Director — open the retained search for the permanent CorpAffairs/IR hire now",
    implication=(
        "A profit warning / materially negative trading update at "
        "{company}. Associated with a permanent Corporate Affairs / IR "
        "hire within 6-12 months; the warning is the timing trigger to "
        "secure the retained search early."
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
        r"appointed.{0,20}(?:chief financial officer|cfo)",
        r"to step down as chief financial officer",
        r"stepping down as chief financial officer",
        r"steps down as chief financial officer",
        r"(?:will |to )?step down as.{0,10}(?:chief financial officer|cfo)",
        r"resignation of.{0,40}chief financial officer",
        r"departs as cfo",
        r"departs as chief financial officer",
        r"(?:chief financial officer|cfo)\b.{0,25}(?:to step down|to leave|is to leave|steps down|stepping down|to depart|has left|appointed)",
        r"departure of.{0,15}(?:chief financial officer|cfo)",
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
        r"appointment of.{0,40}(?:head of investor relations|director of investor relations|ir director)",
        r"appointed.{0,25}(?:head of investor relations|director of investor relations|ir director)",
        r"to step down as.{0,20}(?:head of investor relations|director of investor relations|ir director)",
        r"stepping down as.{0,20}(?:head of investor relations|director of investor relations)",
        r"(?:head of investor relations|director of investor relations|ir director)\b.{0,25}(?:to leave|is to leave|to depart|to step down|steps down|stepping down|departs|has left|appointed)",
        r"departure of.{0,20}(?:head of investor relations|director of investor relations)",
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
# ---- Personal-brand velocity (senior restlessness, 6–12mo) -------------
# Conference speaking, award shortlists/judging, and trade-body committee
# seats for a named senior comms leader. A soft, leading indicator: rising
# external visibility empirically precedes a move 6–12 months out. Gated in
# the detector to items that name a comms / corporate-affairs role, so the
# peer-scan keys the predictor on the leader's *current employer* (the
# account at risk of losing them).
PERSONAL_BRAND_VELOCITY = TriggerType(
    key="personal_brand_velocity",
    label="Personal-brand velocity (speaking / awards / committee)",
    weight=0.45,
    lead_time_weeks=(26, 52),
    who_to_call="The individual directly — soft approach; map their next move 6–12 months out",
    implication=(
        "External-visibility spike (conference speaking / awards shortlist / "
        "trade-body committee seat) for a senior comms leader linked to "
        "{company}. Personal-brand velocity empirically precedes a move "
        "within 6–12 months — track as both a backfill brief and a candidate."
    ),
    patterns=_rx(
        r"to speak at.{0,30}(?:cipr|prca|ioic|prweek|pr week|internal communications conference|corporate communications)",
        r"(?:keynote|panellist|panelist|speaker).{0,30}(?:cipr|prca|ioic|prweek|internal communications conference)",
        r"shortlisted for.{0,40}(?:prweek|pr week|corp comms|corporate communications|internal communications|ic) awards",
        r"(?:named (?:a )?finalist|finalist).{0,40}(?:prweek|pr week|corp comms|corporate communications|ic) awards",
        r"(?:judging panel|awards judge|to judge).{0,30}(?:prweek|cipr|prca|corp comms|corporate communications)",
        r"(?:joins|appointed to|elected to).{0,20}(?:cipr council|prca|ioic)",
    ),
)


# ---- NED / trustee / charity-board appointment (12–18mo) ---------------
# The strongest of the soft restlessness signals: a senior comms leader
# taking a non-exec / trustee seat elsewhere empirically precedes a 12–18
# month exit from the operating role. Gated to comms-role items as above.
NED_TRUSTEE_APPOINTMENT = TriggerType(
    key="ned_trustee_appointment",
    label="NED / trustee appointment (restlessness)",
    weight=0.55,
    lead_time_weeks=(52, 78),
    who_to_call="The individual directly — NED/trustee seats often precede a 12–18 month exit from the operating role",
    implication=(
        "A senior comms leader linked to {company} has taken a NED / charity-"
        "trustee / external board seat. Non-exec appointments empirically "
        "precede a 12–18 month exit from the operating comms role — the "
        "strongest soft restlessness signal. Open the relationship early and "
        "line up a backfill brief."
    ),
    patterns=_rx(
        r"appointed.{0,20}(?:a |as a )?trustee",
        r"named.{0,20}(?:a )?trustee",
        r"joins.{0,20}board of trustees",
        r"board of trustees",
        r"joins the board of.{0,30}(?:charity|foundation|trust)",
        r"appointed.{0,20}non-?executive director",
        r"joins.{0,20}as a? ?non-?executive",
        r"non-?executive (?:director )?appointment",
    ),
)


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


# ====================================================================
# BD-strengthening triggers — profession-specific leading indicators the
# corporate-event taxonomy under-covered (per the "BD strengthening"
# expert assessment). All free/public-data detectable. Four are regex
# triggers fired off the news/trade-press lanes; three (leadership_tenure,
# secured_financing, ownership_change) carry no regex and are emitted by
# dedicated free sources (Companies House charges/PSC + Wayback), so they
# are registered here only to carry a weight + window (like the velocity
# spike above), never matched on free text.
# ====================================================================

# ---- Rebrand / brand refresh ------------------------------------------
# A direct brand/creative hiring trigger: a rebrand or new visual identity
# pulls through a senior brand / corporate-marketing / comms hire to own
# the rollout. Marketing-heavy, but corporate rebrands also drive a
# Corporate Affairs / reputation hire.
REBRAND = TriggerType(
    key="rebrand",
    label="Rebrand / brand refresh",
    weight=0.7,
    lead_time_weeks=(4, 16),
    who_to_call="CMO / Head of Brand — rebrand rollout drives a senior brand/marketing hire",
    implication=(
        "Rebrand / brand refresh at {company}. A new identity rollout "
        "typically pulls through a senior brand / corporate-marketing hire "
        "(and often a Corporate Affairs hire to own the narrative) within "
        "4–16 weeks."
    ),
    patterns=_rx(
        r"rebrand(?:s|ed|ing)?",
        r"brand refresh",
        r"new (?:brand|visual|corporate) identity",
        r"unveils? (?:a )?new (?:logo|brand|identity)",
        r"(?:reveals?|launches?|introduces?) (?:a )?new (?:logo|brand identity|visual identity)",
        r"rebrands? as",
        r"new name and (?:logo|identity|brand)",
    ),
)


# ---- Agency account win / loss (PRWeek "Pitch Update" / Campaign) -------
# A brand moving its PR / creative / media / marketing account drives
# agency-side hiring on the winning agency AND a client-side marketing
# reshuffle. Openly published in PRWeek's "Pitch Update" and Campaign.
AGENCY_ACCOUNT_MOVE = TriggerType(
    key="agency_account_move",
    label="Agency account win / loss",
    weight=0.6,
    lead_time_weeks=(4, 16),
    who_to_call="CMO / Marketing Director (client-side) or Agency MD / New Business (agency-side)",
    implication=(
        "Agency account move at {company} (PR / creative / media / marketing "
        "account win or loss). Account moves drive agency-side delivery hiring "
        "and a client-side marketing reshuffle within 4–16 weeks."
    ),
    patterns=[
        re.compile(p, re.IGNORECASE) for p in (
            r"\bwins?\b.{0,40}\b(?:PR|comms|communications|creative|advertising|media|marketing|brand)\b.{0,15}\b(?:account|business|brief|retainer|mandate)\b",
            r"\bappoints?\b.{0,45}\bas (?:its |their |lead )?(?:PR|comms|communications|creative|advertising|media|marketing|brand) agency\b",
            r"\bhands?\b.{0,40}\b(?:PR|creative|advertising|media|marketing)\b.{0,15}\baccount to\b",
            r"\bnames?\b.{0,45}\bas (?:its |their )?(?:lead )?(?:agency|creative agency|media agency|PR agency)\b",
            r"\bawards?\b.{0,40}\b(?:PR|advertising|media|creative|marketing)\b.{0,15}\b(?:account|brief|business|mandate)\b",
            r"\bpitch (?:win|update)\b",
            r"\bswitch(?:es|ed)? (?:its )?(?:PR|ad|media|creative|marketing) (?:account|business) to\b",
        )
    ],
)


# ---- Public-sector framework award (agency scaling signal) ---------------
# When a comms / PR / digital agency wins a slot on a government framework
# (Find a Tender / Contracts Finder award notices), it will instantly need
# to scale its delivery team to service the contract. The winning agency
# is the lead — contact the agency MD the week the award is announced.
FRAMEWORK_AWARD = TriggerType(
    key="framework_award",
    label="Public-sector framework award",
    weight=0.7,
    lead_time_weeks=(1, 8),
    who_to_call="Agency MD / Head of Delivery at the winning agency",
    implication=(
        "Public-sector framework award involving {company}. Agencies winning "
        "government PR / communications / digital engagement framework slots "
        "need to scale delivery teams immediately. Contact the agency head "
        "within the first week of the award announcement."
    ),
    patterns=[
        re.compile(p, re.IGNORECASE) for p in (
            # "awarded … communications/PR/digital … framework/contract/lot"
            r"\baward(?:ed|s)?\b.{0,60}?\b(?:communications?|PR|public relations|"
            r"digital engagement|media|campaigns?|stakeholder engagement|"
            r"public affairs|creative)\b.{0,30}?\b(?:framework|contract|lot|agreement)\b",
            # "framework … communications … awarded to <agency>"
            r"\bframework\b.{0,40}?\b(?:communications?|PR|public relations|"
            r"digital|media|public affairs|creative)\b.{0,60}?\bawarded\s+to\b",
            # "appointed to … framework" for comms scope
            r"\bappointed\s+(?:to|onto)\b.{0,40}?\b(?:communications?|PR|"
            r"digital|media|public affairs)\b.{0,20}?\bframework\b",
            # "<agency> wins … government/public-sector … contract"
            r"\bwins?\b.{0,30}?\b(?:government|public[- ]sector|council|NHS|"
            r"central gov(?:ernment)?|local authority)\b.{0,30}?\b(?:communications?|"
            r"PR|digital|media|public affairs)\b.{0,15}?\b(?:contract|framework|brief)\b",
            # Contract award notice phrasing from Find a Tender
            r"\bcontract award(?:ed)? notice\b.{0,80}?\b(?:communications?|PR|"
            r"public relations|digital|media|public affairs|stakeholder engagement)\b",
        )
    ],
)


# ---- ESG / B-Corp certification (CMA Green Claims Code) -----------------
# A new B-Corp certification, science-based target, net-zero strategy or
# CSRD obligation triggers a substantiation-and-comms build-out: someone
# has to evidence and communicate the claims without falling foul of the
# CMA Green Claims Code. A permanent sustainability-comms / brand-trust
# hire commonly follows.
ESG_BCORP = TriggerType(
    key="esg_bcorp",
    label="ESG / B-Corp certification",
    weight=0.5,
    lead_time_weeks=(8, 26),
    who_to_call="Head of Sustainability / Corporate Affairs — substantiation + comms build-out",
    implication=(
        "ESG / B-Corp certification event at {company}. Substantiating and "
        "communicating the claims (under the CMA Green Claims Code) drives a "
        "permanent sustainability-comms / brand-trust hire within 8–26 weeks."
    ),
    patterns=[
        re.compile(p, re.IGNORECASE) for p in (
            r"\bB[\s-]?Corp(?:oration)?\b",
            r"\bcertified B[\s-]?Corp\b",
            r"\bachieves? B[\s-]?Corp\b",
            r"\bbecomes? a B[\s-]?Corp\b",
            r"\bscience[\s-]based targets?\b",
            r"\bnet[\s-]zero (?:strategy|target|plan|commitment|transition plan)\b",
            r"\bgreen claims\b",
            r"\bCSRD\b",
        )
    ],
)


# ---- Martech / digital-marketing platform adoption ---------------------
# Adoption of a marketing-automation / CDP / martech platform is two sides
# of one decision: the platform purchase and the senior marketing-ops /
# change-comms hire to run it. Detectable free in trade press AND via
# technographics (Wappalyzer / BuiltWith — see sources/technographics.py).
MARTECH_VENDORS = [
    "Salesforce Marketing Cloud", "Adobe Experience", "Adobe Experience Cloud",
    "Marketo", "HubSpot", "Braze", "Segment", "Tealium", "Bloomreach",
    "Iterable", "Klaviyo", "Emarsys", "Sitecore", "Optimizely",
]
_MARTECH_ALT = "|".join(re.escape(v) for v in MARTECH_VENDORS)

MARTECH_ADOPTION = TriggerType(
    key="martech_adoption",
    label="Martech / marketing-platform adoption",
    weight=0.5,
    lead_time_weeks=(6, 16),
    who_to_call="CMO / Head of Marketing Ops — platform purchase + senior hire are one decision",
    implication=(
        "Martech / marketing-platform adoption at {company}. A new marketing-"
        "automation / CDP deployment correlates with a senior marketing-ops / "
        "digital-marketing (or change-comms) hire within 6–16 weeks."
    ),
    patterns=[
        re.compile(p, re.IGNORECASE) for p in (
            r"(?:adopts?|implements?|rolls? out|deploys?|migrates? to|selects?|"
            r"chooses?|goes live with)\s+(?:" + _MARTECH_ALT + r")",
            r"(?:" + _MARTECH_ALT + r")\s+(?:deployment|implementation|roll[\s-]?out|go[\s-]?live)",
            r"\bmarketing automation platform\b",
            r"\bcustomer data platform\b",
            r"\bmartech (?:stack|platform|transformation)\b",
            r"\bdigital transformation programme\b",
        )
    ],
)


# ---- Leadership tenure (flight-risk) — Companies House / Wayback --------
# A senior comms / marketing leader past a long-tenure threshold is a
# statistical flight risk (CMO/CCO tenure is among the shortest in the
# C-suite). Emitted by companies_house.detect_tenure_signals off board
# officers' appointed_on dates — a soft, slow restlessness signal that
# keys on the leader's CURRENT employer (the account about to lose them /
# need a backfill). Never regex-matched on free text.
LEADERSHIP_TENURE = TriggerType(
    key="leadership_tenure",
    label="Leadership tenure (flight-risk / succession watch)",
    weight=0.5,
    lead_time_weeks=(26, 52),
    who_to_call="The individual directly + the CHRO — long tenure precedes a move; line up a backfill brief",
    implication=(
        "A senior comms / marketing leader at {company} has passed a long-"
        "tenure threshold. Long tenure in a short-tenure seat empirically "
        "precedes a move within 6–12 months — open the relationship early "
        "and prepare a backfill brief."
    ),
    patterns=[],   # emitted by companies_house.detect_tenure_signals, not regex
)


# ---- Secured financing / charge registered — Companies House -----------
# A newly registered charge (debenture / mortgage) or a share allotment
# (SH01) at a watchlist company is a financing event: growth/secured
# capital that funds an external build. Emitted by
# companies_house.detect_filing_events off the free /charges + filing-
# history endpoints; Tier-1 verified by construction.
SECURED_FINANCING = TriggerType(
    key="secured_financing",
    label="Secured financing / charge registered",
    weight=0.85,
    lead_time_weeks=(8, 26),
    who_to_call="CFO / CEO — fresh capital funds an external comms/marketing build",
    implication=(
        "Secured financing event at {company} (a charge registered, or a "
        "share allotment, at Companies House). Fresh growth capital funds an "
        "external build; a senior comms / marketing hire commonly follows "
        "within 8–26 weeks."
    ),
    patterns=[],   # emitted by companies_house.detect_filing_events, not regex
)


# ---- Ownership change (PSC) — Companies House --------------------------
# A new person/entity with significant control (a PSC notified in the
# window, or an existing one ceased) is an ownership change: a new owner
# typically refreshes the leadership and the corporate narrative. Emitted
# by companies_house.detect_filing_events off the free PSC endpoint;
# Tier-1 verified by construction.
OWNERSHIP_CHANGE = TriggerType(
    key="ownership_change",
    label="Ownership change (new significant control)",
    weight=0.9,
    lead_time_weeks=(8, 24),
    who_to_call="Incoming owner / Corporate Affairs — new ownership refreshes leadership + narrative",
    implication=(
        "Ownership change at {company} (a new person with significant "
        "control filed at Companies House). New owners typically refresh "
        "leadership and the corporate narrative, driving a senior comms / "
        "marketing hire within 8–24 weeks."
    ),
    patterns=[],   # emitted by companies_house.detect_filing_events, not regex
)


TRIGGERS = [CEO_CHANGE, CHAIR_CHANGE, CHRO_CHANGE, CFO_CHANGE,
            IR_DIRECTOR_CHANGE, COMMS_LEADER_DEPARTURE,
            MNA, ACTIVIST_STAKE, PE_ACQUISITION,
            REGULATOR_ACTION, REGULATOR_PROBE_EARLY, CRISIS_EVENT,
            PROFIT_WARNING, RESTRUCTURE, IC_PLATFORM_RFP,
            IPO_LISTING, CONTRACT_LOSS, PRESS_VELOCITY_SPIKE,
            PERSONAL_BRAND_VELOCITY, NED_TRUSTEE_APPOINTMENT,
            # BD-strengthening additions
            REBRAND, AGENCY_ACCOUNT_MOVE, FRAMEWORK_AWARD, ESG_BCORP,
            MARTECH_ADOPTION, LEADERSHIP_TENURE, SECURED_FINANCING,
            OWNERSHIP_CHANGE]
BY_KEY = {t.key: t for t in TRIGGERS}

# Marketing desk (FIRST DRAFT): the trigger DETECTION (regex patterns) is
# universal and unchanged; only the comms-flavoured who_to_call + implication
# narrative shown in the Pre-Market panel is rewritten to marketing. Review
# with the marketing team.
from tool.profiles import active_profile as _active_profile
if _active_profile().key == "marketing":
    _MKT_COPY = {
        "ceo_change": ("CMO / Marketing Director",
            "External CEO/MD change at {company}. Incoming CEOs typically reset "
            "the brand/growth agenda within 6 months; a marketing-leadership "
            "refresh commonly follows in 6–12 weeks."),
        "chair_change": ("CEO office / CMO",
            "New Chair at {company}. Board-level change commonly prompts a "
            "brand/marketing strategy review within 8–16 weeks."),
        "chro_change": ("CMO / Head of Marketing",
            "New CHRO at {company}. Function reviews often reach marketing; a "
            "marketing-team reshape can follow within 8–16 weeks."),
        "mna": ("CMO of acquirer; Head of Brand at both sides",
            "M&A involving {company}. Brand integration and rebrand work drives "
            "senior marketing hires across both sides within 3–6 months."),
        "activist_stake": ("CMO / Head of Brand — brand & growth repositioning",
            "Activist stake in {company}. Repositioning and value-narrative work "
            "open a senior brand/marketing brief."),
        "pe_acquisition": ("Incoming CMO under new ownership; Head of Growth",
            "PE acquisition of {company}. New owners commonly refresh the "
            "marketing/growth leadership in the first 6 months."),
        "regulator_action": ("CMO / Head of Brand — brand-trust & customer-marketing rebuild",
            "Regulator action against {company}. Brand-trust repair and "
            "customer-marketing capacity open a permanent marketing brief."),
        "regulator_probe_early": ("CMO / Head of Brand",
            "Early regulatory probe at {company}. Brand-reputation workstreams "
            "ramp ahead of the live period — secure the retained brief early."),
        "crisis_event": ("CMO / Head of Brand — brand-trust rebuild",
            "Crisis event at {company}. Brand-trust and customer-marketing "
            "rebuild typically drives a permanent senior marketing hire."),
        "profit_warning": ("CMO / Head of Growth — demand & retention reset",
            "Profit warning at {company}. Demand-generation and retention resets "
            "open a senior growth/marketing brief."),
        "restructure": ("CMO / Head of Marketing",
            "Restructure / strategic review at {company}. Marketing is commonly "
            "reorganised — a senior marketing brief often follows."),
        "cfo_change": ("CMO / Head of Growth",
            "New CFO at {company}. Budget and efficiency resets commonly reshape "
            "the marketing/growth leadership."),
        "ir_director_change": ("CMO / Head of Brand",
            "New IR Director at {company}. Investor-narrative refreshes are often "
            "paired with brand/corporate-marketing hires."),
        "comms_leader_departure": ("CEO office / CMO — the marketing seat is open NOW",
            "Senior marketing leader has left {company}. The vacated seat is a "
            "live replacement search."),
        "ic_platform_rfp": ("CMO / Head of Marketing",
            "Marketing / CRM platform activity at {company} signals a senior "
            "marketing hire in 6–12 weeks."),
        "ipo_listing": ("CFO office — brand + investor-marketing hires go in pre-admission",
            "{company} heading to IPO. Brand and investor-marketing capacity is "
            "built pre-admission."),
        "contract_loss": ("Head of Marketing / CMO — reposition + demand defence",
            "Material contract loss at {company}. Repositioning and demand "
            "defence open a senior marketing brief."),
        "press_velocity_spike": ("Head of Marketing / CMO",
            "Press-velocity spike at {company} — heightened market activity often "
            "precedes a senior marketing hire."),
        "rebrand": ("CMO / Head of Brand",
            "Rebrand / brand refresh at {company}. A new identity rollout drives a "
            "senior brand / corporate-marketing hire within 4–16 weeks."),
        "agency_account_move": ("CMO / Marketing Director",
            "Agency account move at {company}. A PR / creative / media account win "
            "or loss drives a client-side marketing reshuffle within 4–16 weeks."),
        "framework_award": ("Agency MD / Head of Delivery",
            "Public-sector framework award at {company}. Winning a government "
            "marketing / digital / creative framework slot requires immediate "
            "delivery-team scaling."),
        "esg_bcorp": ("CMO / Head of Brand — brand-trust & sustainability marketing",
            "ESG / B-Corp certification at {company}. Substantiating green claims "
            "(CMA Green Claims Code) opens a brand-trust / sustainability-marketing brief."),
        "martech_adoption": ("CMO / Head of Marketing Ops",
            "Martech / marketing-platform adoption at {company}. A platform purchase "
            "and a senior marketing-ops hire are two sides of one decision."),
        "secured_financing": ("CFO / CMO (or CEO)",
            "Secured financing at {company}. Fresh growth capital funds an external "
            "marketing build."),
        "ownership_change": ("Incoming owner / CMO",
            "Ownership change at {company}. New owners commonly refresh the marketing "
            "leadership in the first 6 months."),
    }
    for _k, (_w, _i) in _MKT_COPY.items():
        _t = BY_KEY.get(_k)
        if _t is not None:
            _t.who_to_call = _w
            _t.implication = _i

# Comms / corporate-affairs role context. The detector requires one of
# these to be present before firing the person-centric soft triggers
# (personal_brand_velocity, ned_trustee_appointment) — so they only fire
# on items genuinely about a comms leader, and the peer-scan keys the
# predictor on that leader's current employer.
COMMS_ROLE_RX = re.compile(
    r"\b(director of communications|head of communications|"
    r"chief communications officer|communications director|comms director|"
    r"corporate affairs director|director of corporate affairs|"
    r"head of corporate affairs|head of internal communications|"
    r"director of internal communications|head of (?:external|media|public) "
    r"(?:communications|relations|affairs)|head of public affairs)\b",
    re.IGNORECASE,
)


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

# Marketing desk (FIRST DRAFT): the job-ad cluster detector ("2+ mid-level
# roles and no senior") keys off these. Swap to marketing roles so a cluster
# of mid-level marketing hires is what's detected. Comms regexes above are
# untouched.
if _active_profile().key == "marketing":
    SENIOR_RX = re.compile(
        r"\b(head of|director of|chief marketing|cmo|vp marketing|"
        r"vice president marketing|brand director|head of brand|head of growth|"
        r"global head of|group head of)\b",
        re.IGNORECASE,
    )
    MID_RX = re.compile(
        r"\b(marketing manager|brand manager|senior marketing|senior brand|"
        r"growth manager|digital marketing manager|product marketing manager|"
        r"campaign manager|crm manager|content manager|marketing lead)\b",
        re.IGNORECASE,
    )


def is_senior_comms(title: str) -> bool:
    return bool(SENIOR_RX.search(title or ""))


def is_midlevel_comms(title: str) -> bool:
    t = title or ""
    if is_senior_comms(t):
        return False
    return bool(MID_RX.search(t))


# ---- Per-specialism trigger relevance -----------------------------------
# Not every company trigger predicts a hire in every specialism: a regulator
# probe, an IR-director change, a CHRO change, a crisis or an activist stake
# signal a COMMS / reputation / IR hire — not a marketing one. So the marketing
# desk should NOT surface those as BD leads. Comms keeps the full trigger set
# (unchanged); marketing keeps only triggers that plausibly precede a MARKETING
# hire. FIRST DRAFT — review with the marketing team.
_MARKETING_TRIGGER_KEYS = {
    "ceo_change",              # new CEO resets the brand / growth agenda
    "mna",                     # brand integration / rebrand
    "pe_acquisition",          # new owners refresh marketing & growth
    "ipo_listing",             # brand + investor-marketing build pre-admission
    "comms_leader_departure",  # = marketing-leader departure (profile regex)
    "restructure",             # marketing function reorganised
    "press_velocity_spike",    # brand / share-of-voice surge
    "job_ad_cluster",          # cluster of mid-level marketing ads → senior hire
    "profit_warning",          # demand / retention reset → growth hire
    "contract_loss",           # demand & brand defence
    "personal_brand_velocity", # candidate signal (specialism-agnostic)
    "ned_trustee_appointment", # candidate signal (specialism-agnostic)
    # BD-strengthening additions — all plausibly precede a marketing hire
    "rebrand",                 # brand/creative hiring trigger
    "agency_account_move",     # agency + client-side marketing reshuffle
    "esg_bcorp",               # brand-trust / sustainability marketing
    "martech_adoption",        # marketing-ops / digital-marketing hire
    "leadership_tenure",       # candidate signal (specialism-agnostic)
    "secured_financing",       # growth capital → marketing build
    "ownership_change",        # new owner refreshes marketing
}


def relevant_trigger_keys():
    """Trigger keys relevant to the ACTIVE specialism, or None (= keep all,
    comms-unchanged). Marketing drops the comms/IR/governance-only triggers so
    its pre-market BD leads are marketing-specific."""
    from tool.profiles import active_profile
    if active_profile().key == "marketing":
        return set(_MARKETING_TRIGGER_KEYS)
    return None
