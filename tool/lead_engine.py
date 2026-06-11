"""BD lead scoring — a multi-layer CONJUNCTION, not a single trigger.

v3 of the lead-sourcing research ("What makes a genuinely strong pre-contact
BD lead"). A strong lead is signal STACKING: two or three independent,
same-direction signals across DIFFERENT layers, with no material
contradiction. A lone trigger plus a heuristic is a hypothesis to watch, not a
lead to action. Everything here is for the lead-up TO first contact.

The layers (each detectable from public/scraped data, no JobAdder, no outcome
loop yet):

  FIT (ICP, Layer 2)        slow-moving — should VMA serve this org at all?
  SIGNAL / TRIGGER (1)      the event, x recency-decay x source-confidence.
  MARKET STATE (3)          a global macro coefficient (KPMG/REC, IPA, Gartner)
                            that raises the stacking bar in a cold market, so a
                            lone trigger defaults to "watch".
  FINANCIAL DIRECTION (4)   growth funds an external build; cuts absorb it.
                            Signals must point the same way (funding AND
                            layoffs cancel out).
  POSTURE (5)               in-house-vs-agency — the decisive, under-used
                            filter. A building internal TA / "no agencies" /
                            in-house bench is an anti-signal; an active cluster
                            with no internal recruiter, or aged postings, is a
                            pro-signal. Inferred pre-contact, always a
                            probability.
  OPPORTUNITY SIZE (7)      team-build vs single backfill.
  RECENCY + TOO-FRESH (9)   decay both ways: too old = stale; a brand-new
                            leadership / funding trigger is premature (the new
                            leader has no hiring plan for ~8-12 weeks).

Routing (Layer 5 of the report): ACTIVE hiring now (a corroborated live
cluster, no in-house contradiction) is the strongest pre-contact signal ->
Call today. Anticipatory triggers (leadership / funding) need maturity plus a
stack -> Call today; thinner -> Nurture (prepare) or Monitor (watch). Negative
scoring is first-class. Deferred: live index feeds, precise TA / PSL scraping,
and outcome-based re-weighting.

Additive over tool.predictor_pipeline: reads a persisted predictor dict (or a
funding event) and returns a `lead` sub-dict. Comms + marketing desks.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Layer 1 — SIGNAL taxonomy (comms desk). raw_pts per the research, mapped
# onto our existing predictor trigger_keys. family drives the access angle;
# decay 'slow' = leadership change (meaningful through ~90 days), 'fast' =
# event signals (crisis/funding/etc.) that lose value within weeks.
# --------------------------------------------------------------------------
# trigger_key -> (raw_pts, family, decay)
_COMMS_TAXONOMY = {
    # Leadership change in / around the function = master trigger (demand + access)
    "chro_change":             (5, "leadership", "slow"),
    "comms_leader_departure":  (4, "leadership", "slow"),
    "ir_director_change":      (3, "leadership", "slow"),
    "ceo_change":              (3, "leadership", "slow"),
    "cmo_change":              (2, "leadership", "slow"),   # cross-sell: brand/comms reset follows
    "chair_change":            (2, "leadership", "slow"),
    "cfo_change":              (2, "leadership", "slow"),
    # Demand triggers
    "funding":                 (5, "demand", "fast"),
    "ipo_listing":             (5, "demand", "fast"),
    "job_ad_cluster":          (5, "demand", "fast"),
    "mna":                     (3, "demand", "fast"),
    "pe_acquisition":          (3, "demand", "fast"),
    "activist_stake":          (3, "demand", "fast"),
    "crisis_event":            (3, "demand", "fast"),
    "regulator_action":        (3, "demand", "fast"),
    "profit_warning":          (3, "demand", "fast"),
    "restructure":             (3, "demand", "fast"),
    "redundancy":              (4, "demand", "fast"),
    "regulator_probe_early":   (2, "demand", "fast"),
    "contract_loss":           (2, "demand", "fast"),
    "ownership_change":         (3, "demand", "fast"),
    "secured_financing":        (3, "demand", "fast"),
    "rebrand":                 (2, "demand", "fast"),
    "agency_account_move":     (2, "demand", "fast"),
    "market_entry":            (2, "demand", "slow"),   # launch windows run months, not weeks
    "framework_award":         (3, "demand", "fast"),
    "hiring_gap":              (3, "demand", "fast"),
    "seniority_gap":           (4, "demand", "fast"),
    "framework_displacement":  (3, "demand", "fast"),
    "esg_bcorp":               (2, "demand", "fast"),
    # Edge detectors (tool.edge_detectors): a senior interim cover is a
    # dated promise of a perm search; a landed senior leader is a dated
    # promise of a team build-out. Slow decay — the payoff is 1-2
    # quarters out, not this fortnight.
    "interim_watch":           (4, "demand", "slow"),
    "follow_on":               (3, "leadership", "slow"),
    # Access triggers
    "ic_platform_rfp":         (4, "access", "fast"),
    "martech_adoption":        (2, "access", "fast"),
    # Soft / corroboration only (cannot trigger a lead on their own)
    "ned_trustee_appointment": (1, "soft", "slow"),
    "press_velocity_spike":    (1, "soft", "fast"),
    "personal_brand_velocity": (1, "soft", "slow"),
    "leadership_tenure":       (1, "soft", "slow"),
}

# Marketing desk taxonomy. Same trigger detection, different conversion logic:
# growth/brand mandates fire hardest on funding (growth budget), a CMO change
# and a job-ad cluster; corporate-comms-only triggers (IR/regulator) are
# down-weighted. Stress-tested against the same anti-triggers + corroboration
# gate as comms (see tests).
_MKT_TAXONOMY = {
    "comms_leader_departure":  (5, "leadership", "slow"),   # CMO / brand-lead change
    "cmo_change":              (5, "leadership", "slow"),   # incoming CMO rebuilds in 90 days
    "chro_change":             (5, "leadership", "slow"),
    "ceo_change":              (4, "leadership", "slow"),
    "cfo_change":              (3, "leadership", "slow"),    # ROI / efficiency marketing
    "chair_change":            (1, "leadership", "slow"),
    "ir_director_change":      (1, "leadership", "slow"),
    "funding":                 (6, "demand", "fast"),        # marketing master demand trigger
    "ipo_listing":             (5, "demand", "fast"),
    "job_ad_cluster":          (5, "demand", "fast"),
    "profit_warning":          (4, "demand", "fast"),        # demand-gen / ROI drive
    "mna":                     (3, "demand", "fast"),
    "pe_acquisition":          (3, "demand", "fast"),
    "restructure":             (3, "demand", "fast"),
    "redundancy":              (4, "demand", "fast"),
    "crisis_event":            (3, "demand", "fast"),
    "activist_stake":          (2, "demand", "fast"),
    "regulator_action":        (2, "demand", "fast"),
    "regulator_probe_early":   (1, "demand", "fast"),
    "contract_loss":           (2, "demand", "fast"),
    "ic_platform_rfp":         (2, "access", "fast"),
    "rebrand":                 (4, "demand", "fast"),        # marketing brand trigger
    "agency_account_move":     (4, "demand", "fast"),        # client + agency-side reshuffle
    "market_entry":            (3, "demand", "slow"),        # UK launch builds in-country marketing
    "framework_award":         (3, "demand", "fast"),        # agency scaling after gov win
    "hiring_gap":              (3, "demand", "fast"),        # scaling with no comms
    "seniority_gap":           (4, "demand", "fast"),        # senior hire + junior team
    "framework_displacement":  (3, "demand", "fast"),        # competitor agency disruption
    "ownership_change":         (3, "demand", "fast"),
    "secured_financing":        (3, "demand", "fast"),
    "interim_watch":           (4, "demand", "slow"),        # interim-to-perm watch
    "follow_on":               (3, "leadership", "slow"),    # new-leader build-out
    "martech_adoption":        (3, "access", "fast"),        # marketing-ops decision
    "esg_bcorp":               (2, "demand", "fast"),
    "ned_trustee_appointment": (1, "soft", "slow"),
    "press_velocity_spike":    (1, "soft", "fast"),
    "personal_brand_velocity": (1, "soft", "slow"),
    "leadership_tenure":       (1, "soft", "slow"),
}

_SOFT_CAP = 2.0          # soft modifiers add at most +2, and only alongside a real signal
_SIGNAL_HIGH = 6.0       # effective-points threshold for "High SIGNAL"
_FIT_HIGH = 7            # 0-10 threshold for "High FIT"

# Too-fresh holds, per anticipatory family (v2 window re-tool). A leadership
# change presents in the 4-12 week window — hold 28 days, then the lead is
# live until its predicted window closes (the gate enforces the far edge).
# Funding / IPO budget is actionable sooner — hold 21 days, as before.
LEADERSHIP_HOLD_DAYS = 28
EVENT_HOLD_DAYS = 21

# --------------------------------------------------------------------------
# Layer 3 — MARKET STATE (global macro modifier). The report's central
# insight: in a contracting hiring market the SAME trigger means something
# different — companies freeze, absorb hiring in-house and cut budgets — so a
# lone trigger should default to "watch" and a genuinely strong lead must
# clear a higher stacking bar. This is one monthly-updatable constant (no live
# feed yet), read from the KPMG/REC UK Report on Jobs Permanent Placements
# Index (PPI), the IPA Bellwether marketing-budget balance and the Gartner CMO
# Spend Survey. PPI > 50 = expansion, < 50 = contraction. UPDATE MONTHLY.
# --------------------------------------------------------------------------
MARKET_STATE = {
    "as_of": "2025-12",
    "source": "KPMG/REC Report on Jobs; IPA Bellwether; Gartner CMO Spend",
    # The UK-overall Permanent Placements Index (KPMG/REC, Dec 2025) is the
    # default; but the report says SECTOR divergence matters more than
    # live-ness, so a funded tech / life-sciences scale-up must not carry the
    # same cold-market penalty as cyclical retail. A handful of hand-set,
    # monthly-updatable per-sector reads (PPI > 50 = expansion, < 50 =
    # contraction) keyed to tool.peers.detect_sector buckets.
    "default_ppi": 44.3,
    "marketing_budget_balance": 7.3,   # IPA Bellwether Q1 2026 net balance, %
    "sectors": {
        "technology": 52.0,
        "pharma_healthcare": 51.0,
        "financial_services": 47.0,
        "media_telecoms": 46.0,
        "professional_services": 46.0,
        "energy_utilities": 45.0,
        "industrial_manufacturing": 44.0,
        "transport_logistics": 44.0,
        "real_estate": 43.0,
        "public_sector_charities": 43.0,
        "retail_consumer": 41.0,
    },
}


# Auto-ingested override. tool.market_ingest refreshes the UK-overall
# Permanent Placements Index + IPA Bellwether balance monthly and persists
# them to tool/state/market_state.json, so the macro coefficient is no longer
# a value someone has to hand-edit. The hand-set MARKET_STATE above is the
# floor / fallback: if the override file is absent or unparseable, behaviour
# is exactly as before (so this never regresses). The override moves the
# overall level; the hand-set per-sector divergences are preserved by
# applying the same delta to each sector.
from pathlib import Path as _Path

_MARKET_OVERRIDE_FILE = _Path(__file__).resolve().parent / "state" / "market_state.json"
_market_override_cache: dict = {"mtime": None, "value": None}


def _load_market_override() -> dict | None:
    """Persisted auto-ingested macro values, or None. Cached on file mtime so
    repeated score_lead calls don't re-read the disk each row."""
    try:
        st = _MARKET_OVERRIDE_FILE.stat()
    except OSError:
        return None
    if _market_override_cache["mtime"] == st.st_mtime:
        return _market_override_cache["value"]
    try:
        import json
        data = json.loads(_MARKET_OVERRIDE_FILE.read_text())
        if not isinstance(data, dict):
            data = None
    except Exception:
        data = None
    _market_override_cache["mtime"] = st.st_mtime
    _market_override_cache["value"] = data
    return data


def _effective_market_state() -> dict:
    """MARKET_STATE overlaid with the auto-ingested override (if any). Only a
    plausible PPI (30–70) is accepted, so a bad parse can never poison the
    macro coefficient — it falls straight back to the hand-set constant."""
    base = MARKET_STATE
    ov = _load_market_override()
    new_default = (ov or {}).get("default_ppi")
    if not isinstance(new_default, (int, float)) or not (30.0 <= new_default <= 70.0):
        return base
    delta = new_default - base["default_ppi"]
    sectors = {k: round(v + delta, 1) for k, v in base["sectors"].items()}
    mbb = ov.get("marketing_budget_balance")
    return {
        "as_of": ov.get("as_of", base["as_of"]),
        "source": (ov.get("source") or base["source"]) + " (auto-ingested)",
        "default_ppi": float(new_default),
        "marketing_budget_balance": (mbb if isinstance(mbb, (int, float))
                                     else base["marketing_budget_balance"]),
        "sectors": sectors,
    }


def _market_sector(company: str | None) -> str | None:
    """Classify a company into a MARKET_STATE sector bucket: the peers
    detector first, then a light keyword fallback for funded scale-ups that
    are not in the curated peer lists (quantum / bio / software, etc.)."""
    try:
        from tool.peers import detect_sector
        s = detect_sector(company)
        if s:
            return s
    except Exception:
        pass
    n = (company or "").lower()
    if re.search(r"quantum|software|cyber|fintech|robotics|semiconductor|"
                 r"\bai\b|\bdata\b|technolog|digital|\blabs?\b|systems", n):
        return "technology"
    if re.search(r"bio|biosci|therapeut|pharma|genom|sciences|health|medic|clinic", n):
        return "pharma_healthcare"
    return None


def _market_state(company: str | None = None) -> dict:
    """Resolve the macro modifier for this company's sector (falling back to
    the UK-overall read). Returns state, the stacking bar a STRONG lead must
    clear, the sector, and a one-line human read for the dossier."""
    ms = _effective_market_state()
    sector = _market_sector(company)
    ppi = ms["sectors"].get(sector, ms["default_ppi"])
    if ppi >= 52:
        state, req = "expanding", 2
    elif ppi <= 48:
        state, req = "contracting", 3
    else:
        state, req = "flat", 2
    note = {"expanding": "the hiring market in this sector is expanding",
            "flat": "the hiring market is flat",
            "contracting": "the hiring market is contracting, so lone triggers are often absorbed in-house"}[state]
    return {"state": state, "stack_req": req, "note": note, "sector": sector, "ppi": ppi}


# Layer 4 — FINANCIAL DIRECTION. Growth funds an external build; contraction
# absorbs it. The report's rule: signals must point the SAME way, so a trigger
# contradicted by a budget-cut signal downgrades to "watch". (Bare
# "restructure" is a comms trigger that is financially ambiguous, so it is NOT
# treated as a cut unless cut language is present.)
_FIN_PRO_KEYS = {"funding", "ipo_listing", "secured_financing"}
_FIN_PRO_RX = re.compile(
    r"\b(rais(?:e|ed|ing)|funding round|series\s+[a-z]\b|\$\d|£\d+\s?m|investment|"
    r"expansion|expanding|record (?:revenue|results|profit|year)|revenue growth|"
    r"new (?:office|hq|headquarters|market|site)|contract win|won .{0,20}contract|"
    r"scale[\s-]?up|growth capital|ipo|flotation|listing)\b", re.I)
_FIN_ANTI_KEYS = {"profit_warning", "contract_loss"}
# NOTE: no trailing \b — several alternatives are stems (redundanc, efficienc,
# streamlin, insolvenc, downsiz, rightsiz, cost-cut) that must match inside a
# longer word ("redundancies", "streamlining"); a trailing \b would kill them.
_FIN_ANTI_RX = re.compile(
    r"\b(?:redundanc|lay[\s-]?offs?|job cuts|cost[\s-]?cut|"
    r"efficienc|streamlin|rightsiz|downsiz|hiring freeze|recruitment freeze|"
    r"profit warning|administration|insolvenc|savings? (?:programme|program|plan|target)|"
    r"reduce (?:costs|headcount|spend)|cut (?:costs|jobs|headcount|spend))", re.I)

# Layer 5 — IN-HOUSE-vs-AGENCY POSTURE (the decisive, under-used filter).
# Whether a need reaches an external agency depends on the recruitment
# operating model. Detectable, but only inferable pre-contact, so always a
# probability with a confidence flag (never asserted).
_POSTURE_INT_RX = re.compile(
    r"\b(?:talent acquisition|in[\s-]house recruit|internal recruit|talent partner|"
    r"rpo\b|recruitment process outsourc|no agenc|agencies need not|"
    r"direct applicants? only|no recruiters?|strictly no agenc|head of ta\b)", re.I)
_POSTURE_EXT_RX = re.compile(
    r"\b(retained search|executive search|via (?:an )?agency|appointed .{0,20}agency|"
    r"preferred supplier|on (?:the )?psl)\b", re.I)


def _financial_direction(events: list[dict], live_triggers: list[dict]) -> dict:
    blob = " ".join((e.get("evidence") or "") + " " + (e.get("trigger_label") or "")
                    for e in events)
    keys = {t.get("key") for t in live_triggers}
    has_pro = bool(keys & _FIN_PRO_KEYS) or bool(_FIN_PRO_RX.search(blob))
    has_anti = bool(keys & _FIN_ANTI_KEYS) or bool(_FIN_ANTI_RX.search(blob))
    if has_anti and has_pro:
        direction = "conflicting"
    elif has_anti:
        direction = "anti"
    elif has_pro:
        direction = "pro"
    else:
        direction = "neutral"
    return {"direction": direction, "has_pro": has_pro, "has_anti": has_anti}


def _posture(item: dict, live_triggers: list[dict], anti_flags: list[str]) -> dict:
    """Likely to go external (pro) or absorbed in-house (anti)? Inferred from
    seeded flags, job-ad language, an active cluster with no internal recruiter,
    and aged postings. Confidence is always 'inferred' pre-contact."""
    blob = " ".join((t.get("evidence") or "") + " " + (t.get("label") or "")
                    for t in live_triggers)
    keys = {t.get("key") for t in live_triggers}
    reasons_int, reasons_ext = [], []
    # seeded, authoritative flags win
    from tool.seniority import role_is_senior
    ta_volume_only = (item.get("internal_ta") is True
                      and role_is_senior(item.get("predicted_role")))
    if ((item.get("internal_ta") is True and not ta_volume_only)
            or item.get("psl_status") in ("closed", "off", "no", False)):
        reasons_int.append("internal TA / not on PSL on file")
    if "in_house_team" in anti_flags or _POSTURE_INT_RX.search(blob):
        reasons_int.append("in-house team / TA language detected")
    if item.get("psl_status") in ("on", "yes", True) or _POSTURE_EXT_RX.search(blob):
        reasons_ext.append("history of agency use / on a PSL")
    cluster = "job_ad_cluster" in keys
    aged_cluster = any(t.get("key") == "job_ad_cluster" and (t.get("age_days") or 0) >= 45
                       for t in live_triggers)
    if cluster and not reasons_int:
        reasons_ext.append("roles open beyond the average time-to-fill, no in-house recruiter in sight"
                           if aged_cluster else "an active hiring cluster with no in-house recruiter in sight")
    if reasons_int:
        return {"direction": "internal", "confidence": "inferred", "reasons": reasons_int}
    if reasons_ext:
        return {"direction": "external", "confidence": "inferred", "reasons": reasons_ext}
    return {"direction": "neutral", "confidence": "inferred", "reasons": []}


def _assess(*, fit_band: str, demand_now: bool, n_dim: int, corroborated: bool,
            cap: bool, conflict: bool, contradiction: bool, too_fresh: bool,
            quality_trigger: bool, stack_req: int) -> tuple[str, str]:
    """The conjunction router. Returns (strength_band, action).

    Two routes to STRONG, both gated on corroboration and no contradiction:
      (a) ACTIVE hiring now: a live, corroborated hiring cluster / RFP with no
          in-house contradiction. They are demonstrably hiring externally now,
          which overrides the cold-market default.
      (b) ANTICIPATORY stack: leadership / funding signals need maturity (past
          the 8-12 week too-fresh hold) AND a stack of (stack_req - 1) extra
          same-direction layers (a second event, growth funding, an external
          posture) on top of the base trigger.
    Everything thinner is promising (prepare) or a watch (monitor)."""
    if cap or conflict:
        return "parked", "monitor"
    if contradiction:
        return "watch", "monitor"            # conflicting signals: hypothesis to watch
    if demand_now and corroborated:
        return "strong", ("call_today" if fit_band == "core" else "investigate")
    if too_fresh and quality_trigger:
        return "premature", ("nurture" if fit_band in ("core", "adjacent") else "monitor")
    if quality_trigger and corroborated and n_dim >= (stack_req - 1):
        return "strong", ("call_today" if fit_band == "core" else "investigate")
    if quality_trigger and (n_dim >= 1 or fit_band == "core"):
        return "promising", ("nurture" if fit_band in ("core", "adjacent") else "investigate")
    return "watch", "monitor"


# Numeric rank for ordering the board by strength (strongest first), folded
# into the legacy opportunity value at render time.
_STRENGTH_RANK = {"strong": 1.0, "promising": 0.72, "premature": 0.6,
                  "watch": 0.4, "parked": 0.06}


# Sources we treat as Tier-1 direct verification.
_TIER1 = ("companieshouse", "rns", "londonstockexchange", "lse",
          "regulatory", "gov.uk", "official", "/rns")
# Credible outlets: a major raise reported by the FT, or a posting on a real
# board, is not an unconfirmed inference — it's corroboration-grade on its own.
_TIER2 = ("ft.com", "bloomberg", "reuters", "sky.com", "sky news", "thetimes",
          "telegraph", "theguardian", "guardian", "bbc.", "cityam", "city am",
          "standard.co.uk", "prweek", "campaign", "marketingweek", "techcrunch",
          "sifted", "insidermedia", "businesslive", "linkedin", "indeed",
          "greenhouse", "lever", "workable", "drapers", "retailgazette",
          "investegate")
# Triggers that are an announcement / posting, not an inferred scrape: they are
# verifiable by construction, so a lone one is corroboration-grade (not Tier-3).
_EVENT_GRADE = {"funding", "ipo_listing", "job_ad_cluster", "ic_platform_rfp"}

# Anti-triggers — multiplicative suppression / hard caps (Layer 4). These are
# the cases that *should* score high on raw signal but shouldn't convert: a
# funded company that just built an in-house team, a function already locked to
# a competitor. Text-detected from evidence; a manual override flag can be
# layered on top later.
_ANTI = [
    # A freeze dampens perm strength but is NOT a hard cap: the work
    # still exists and day-rate interim demand rises under a freeze.
    ("hiring_freeze",  re.compile(r"hiring freeze|freeze on hiring|recruitment freeze|pause(?:d|s)? hiring", re.I), 0.7),
    ("layoffs",        re.compile(r"redundanc|lay[\s-]?offs?|job cuts|cutting \d+\s+jobs|axe[sd]? \d+", re.I), 1.0),
    ("administration", re.compile(r"\benters? administration|goes into administration|insolvenc|liquidation", re.I), "cap"),
    ("in_house_team",  re.compile(r"in[\s-]house (?:team|function|capabilit|comms|marketing)|built .{0,25}in[\s-]house|grew? .{0,25}in[\s-]house|fully[\s-]staffed|\d+[\s-]person .{0,20}in[\s-]house|brought .{0,20}in[\s-]house", re.I), 0.5),
    ("competitor_lock", re.compile(r"exclusiv(?:e|ely) (?:retained|appointed|partner)|signed .{0,30}exclusiv|sole (?:agency|search|supplier)|appointed .{0,20}as (?:sole|exclusive)", re.I), 0.2),
]

# Who the AD actually calls — the buyer of a senior comms hire, by strongest
# trigger. Surfaced in the dossier so the lead is actionable, not just scored.
_WHO = {
    # CHRO change is the access route (HR opens the door), but the comms
    # mandate is usually owned by the CCO / CEO with HR running process — aim
    # the call at the owner, not just the seat that changed.
    "chro_change": "CCO / CEO office (HR runs process)",
    "comms_leader_departure": "CEO office / CCO",
    "ir_director_change": "CFO / Head of IR",
    "ceo_change": "Incoming CEO's office / CHRO",
    "chair_change": "CEO office / Company Secretary",
    "cfo_change": "CFO / Head of IR",
    "ipo_listing": "CFO / Head of IR",
    "funding": "CEO / CFO",
    "job_ad_cluster": "CHRO / in-house TA lead",
    "ic_platform_rfp": "Internal Comms lead / IT procurement",
    "mna": "Corporate Affairs / Integration Director",
    "pe_acquisition": "Deal team / incoming Chair",
    "activist_stake": "CEO office / Corporate Affairs",
    "crisis_event": "CEO office / Corporate Affairs",
    "regulator_action": "General Counsel / Corporate Affairs",
    "regulator_probe_early": "General Counsel / Corporate Affairs",
    "profit_warning": "CFO / Head of IR",
    "restructure": "CHRO / Transformation lead",
    "redundancy": "CHRO / Head of IC — redundancy comms almost always goes external",
    "contract_loss": "CEO office / Corporate Affairs",
    "rebrand": "CCO / Head of Brand & Reputation",
    "agency_account_move": "Head of Brand / Corporate Affairs",
    "cmo_change": "The incoming CMO directly / CEO office",
    "market_entry": "CEO / country MD — in-country comms is built around launch",
    "framework_award": "Agency MD / Head of Delivery",
    "hiring_gap": "CEO / CHRO — no comms function exists yet",
    "seniority_gap": "The new CCO / Head of Comms directly",
    "framework_displacement": "Procurement Head / HR Director at the framework client",
    "esg_bcorp": "Head of Sustainability / Corporate Affairs",
    "martech_adoption": "Head of Digital Comms / Marketing Ops",
    "secured_financing": "CFO / CEO",
    "ownership_change": "Incoming owner's office / Corporate Affairs",
    "leadership_tenure": "The individual directly / CHRO (succession watch)",
}
_WHO_DEFAULT = "CHRO / Head of Comms"

# Competing recruiters / staffing firms. VMA IS a recruitment company, so a
# rival agency (or the government's in-house recruitment arm) hiring its own
# comms team is a conflict, not a clean BD lead — it must not rank Fit 10/10
# above real targets. Flagged and de-ranked, not silently dropped (VMA does
# occasionally place into other recruiters), so the AD sees the conflict.
_RECRUITER_RX = re.compile(
    r"\b(recruit(?:ment|er|ers|ing)?|staffing|resourcing|personnel|headhunt\w*|"
    r"manpower|employment agency|executive search|search\s*(?:&|and)\s*selection)\b",
    re.I)


def _is_recruiter(company: str) -> bool:
    return bool(_RECRUITER_RX.search(company or ""))


# Marketing-desk buyer map. Defaults to the functional buyer (CMO / Marketing
# Director); a named, mapped contact (seeded_contact) always wins over this.
_MKT_WHO = {
    "funding": "CEO / CFO (or CMO if appointed)",
    "ipo_listing": "CFO / CMO",
    "ceo_change": "Incoming CEO's office / CMO",
    "comms_leader_departure": "CEO office / CMO",
    "chro_change": "CHRO / CMO",
    "cfo_change": "CFO (ROI / efficiency)",
    "job_ad_cluster": "CMO / Marketing Director",
    "profit_warning": "CFO / CMO",
    "mna": "Brand / Integration Director",
    "pe_acquisition": "Deal team / incoming CMO",
    "restructure": "CMO / Transformation lead",
    "redundancy": "CMO / Head of Employer Brand — change comms & employer-brand rebuild",
    "crisis_event": "CMO / Corporate Affairs",
    "rebrand": "CMO / Head of Brand",
    "agency_account_move": "CMO / Marketing Director",
    "cmo_change": "The incoming CMO directly — they rebuild the team in their first 90 days",
    "market_entry": "Country MD / incoming CMO — in-country marketing is built around launch",
    "framework_award": "Agency MD / Head of Delivery",
    "hiring_gap": "CEO / CHRO — no marketing function exists yet",
    "seniority_gap": "The new CMO / Marketing Director directly",
    "framework_displacement": "Procurement Head / HR Director at the framework client",
    "esg_bcorp": "CMO / Head of Brand (sustainability marketing)",
    "martech_adoption": "CMO / Head of Marketing Ops",
    "secured_financing": "CFO / CMO",
    "ownership_change": "Incoming owner / CMO",
    "activist_stake": "CMO / Head of Brand — activist defence repositioning",
    "leadership_tenure": "The individual directly / CMO (succession watch)",
}
_MKT_WHO_DEFAULT = "CMO / Marketing Director"


def _tables(desk: str):
    """(taxonomy, who_map, who_default) for the desk."""
    if (desk or "comms").lower() == "marketing":
        return _MKT_TAXONOMY, _MKT_WHO, _MKT_WHO_DEFAULT
    return _COMMS_TAXONOMY, _WHO, _WHO_DEFAULT


def _age_days(iso: str | None, fallback: str | None = None) -> float:
    for s in (iso, fallback):
        if not s:
            continue
        try:
            d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400.0)
        except Exception:
            continue
    return 0.0


def _recency_mult(age_days: float, decay: str,
                   lead_time_weeks: tuple[int, int] | None = None) -> float:
    """Layer 2. Decay anchored to the trigger's own hiring window.

    A signal stays at full value (1.0) through the START of its lead-time
    window, then decays smoothly toward 0.1 by twice the end of the window.
    No cliff edges — a lead that's "call today" on Monday won't vanish
    on Tuesday.
    """
    import math
    if lead_time_weeks:
        flat_days = lead_time_weeks[0] * 7
        end_days = lead_time_weeks[1] * 7
    elif decay == "slow":
        flat_days = 42
        end_days = 168
    else:
        flat_days = 14
        end_days = 84

    if age_days <= flat_days:
        return 1.0
    decay_span = max(end_days - flat_days, 14)
    t = (age_days - flat_days) / decay_span
    return max(0.1, math.exp(-1.2 * t))


def _confidence(event: dict, independent_sources: int) -> tuple[str, float]:
    """Layer 3. Tier 1 verified (filing / official / listed) x1.0; Tier 2
    corroboration-grade (2+ independent sources, OR a credible named outlet,
    OR an announcement/posting trigger) x0.6; Tier 3 lone unconfirmed scrape
    x0.3. The Tier-2 widening stops a real raise reported by the FT being
    treated as an unconfirmed rumour and crushed to 0.3."""
    blob = " ".join(str(event.get(k) or "").lower() for k in ("url", "source", "tier"))
    if any(s in blob for s in _TIER1) or (event.get("tier") or "").lower() == "listed":
        return ("verified", 1.0)
    if (independent_sources >= 2 or any(s in blob for s in _TIER2)
            or event.get("trigger_key") in _EVENT_GRADE):
        return ("corroborated", 0.6)
    return ("single-source", 0.3)


def _norm(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (company or "").lower()).strip()


def _is_uk(company: str) -> bool:
    try:
        from tool.peers import SECTOR_PEERS
        intl = {_norm(p) for p in SECTOR_PEERS.get("international", [])}
        return _norm(company) not in intl
    except Exception:
        return True


def _on_patch(company: str) -> bool:
    try:
        from tool.peers import sector_heat_multiplier
        if sector_heat_multiplier(company) > 1.0:
            return True
    except Exception:
        pass
    try:
        from tool.peers import SECTOR_PEERS
        n = _norm(company)
        for k, names in SECTOR_PEERS.items():
            if k == "international":
                continue
            if any(_norm(x) == n for x in names):
                return True
    except Exception:
        pass
    return False


def fit_score(company: str, account_tier: str) -> tuple[int, str, str]:
    """Layer 0 — ICP / FIT (0-10), transparent and rule-based. The curated
    watchlist IS the ICP (sized, comms-relevant), so membership is the
    dominant fit signal; sector-on-patch and UK geo refine it. Unknown stays
    'adjacent' rather than suppressing (protect recall). Returns
    (points, band, one-line rationale)."""
    wl = (account_tier or "watchlist") == "watchlist"
    onp = _on_patch(company)
    uk = _is_uk(company)
    pts = (5 if wl else 1) + (3 if onp else 0) + (2 if uk else 0)
    pts = max(0, min(10, pts))
    band = "core" if pts >= _FIT_HIGH else ("adjacent" if pts >= 4 else "out")
    parts = [("watchlist account" if wl else "off-watchlist"),
             ("on-patch sector" if onp else "sector unconfirmed"),
             ("UK" if uk else "non-UK")]
    why = band.capitalize() + ": " + ", ".join(parts)
    return pts, band, why


_REGISTRY_URL_RX = re.compile(
    r"companieshouse|investegate|londonstockexchange|gov\.uk|find-tender|"
    r"fca\.org|sec\.gov", re.I)

# Crisis urgency decays in DAYS, not weeks — override the pattern window
# for recency purposes only (flat 0 weeks, gone by ~4).
_FAST_DECAY_LTW = {"crisis_event": (1, 2)}


def _dedupe_events(events: list[dict]) -> tuple[list[dict], bool]:
    """Item 9 (AD room): five outlets covering one departure are ONE event
    with high confidence, not five signals — otherwise PR echo inflates
    exactly the companies best at PR. Press events collapse on
    (trigger_key, ~7-day bucket); registry-grade URLs (Companies House,
    RNS, regulators) NEVER collapse — three distinct filings are three
    facts. Returns (deduped_events, extra_corroboration) where the flag
    records that a collapsed cluster spanned 2+ independent sources, so
    corroboration is preserved even though the count shrank."""
    out, seen = [], {}
    extra = False
    for e in sorted((e for e in (events or []) if isinstance(e, dict)),
                    key=lambda x: x.get("published") or ""):
        url = e.get("url") or ""
        if _REGISTRY_URL_RX.search(url):
            out.append(e)
            continue
        try:
            from datetime import date as _date
            bucket = _date.fromisoformat(
                (e.get("published") or "")[:10]).toordinal() // 7
        except Exception:
            bucket = (e.get("published") or "")[:7]
        key = (e.get("trigger_key"), bucket)
        prev = seen.get(key)
        if prev is None:
            seen[key] = e
            out.append(e)
        else:
            a = (prev.get("url") or prev.get("source") or "").lower()
            b = (e.get("url") or e.get("source") or "").lower()
            if a and b and a != b:
                prev["corroborants"] = prev.get("corroborants", 0) + 1
                extra = True
    return out, extra


def _signal(events: list[dict], fallback_date: str | None, taxonomy: dict):
    """Layer 1-3 — SIGNAL = sum of raw_pts x recency x confidence, soft
    modifiers capped and gated on at least one real signal."""
    independent = len({(e.get("url") or e.get("source") or "").lower()
                       for e in events if (e.get("url") or e.get("source"))})
    hard = 0.0
    soft = 0.0
    triggers = []
    for e in events:
        spec = taxonomy.get(e.get("trigger_key"))
        if not spec:
            continue
        pts, family, decay = spec
        age = _age_days(e.get("published"), fallback_date)
        from tool.predictive.patterns import BY_KEY as _BY_KEY
        _ttype = _BY_KEY.get(e.get("trigger_key"))
        _ltw = (_FAST_DECAY_LTW.get(e.get("trigger_key"))
                or (_ttype.lead_time_weeks if _ttype else None))
        rmult = _recency_mult(age, decay, lead_time_weeks=_ltw)
        ctier, cmult = _confidence(e, independent)
        eff = round(pts * rmult * cmult, 2)
        triggers.append({
            "key": e.get("trigger_key"), "label": e.get("trigger_label"),
            "family": family, "raw_pts": pts, "age_days": round(age),
            "recency_mult": rmult, "confidence": ctier, "confidence_mult": cmult,
            "effective": eff, "source": e.get("source") or "", "url": e.get("url") or "",
            "evidence": e.get("evidence") or "",
        })
        if family == "soft":
            soft += eff
        else:
            hard += eff
    soft = min(soft, _SOFT_CAP)
    score = round(hard + (soft if hard > 0 else 0.0), 2)
    triggers.sort(key=lambda t: t["effective"], reverse=True)
    return score, triggers


def _anti_triggers(events: list[dict]) -> tuple[list[str], float, bool]:
    """Layer 4. Returns (flags, multiplier, hard_cap_to_monitor)."""
    blob = " ".join(str(e.get("evidence") or "") + " " + str(e.get("trigger_label") or "")
                    for e in events)
    flags, mult, cap = [], 1.0, False
    for name, rx, effect in _ANTI:
        if rx.search(blob):
            flags.append(name)
            if effect == "cap":
                cap = True
            else:
                mult *= effect
    return flags, mult, cap


def _access(triggers: list[dict]) -> str:
    """The opening angle for the call — the trigger supplies it."""
    fams = {t["family"] for t in triggers}
    if "access" in fams:
        angle = "A live RFP / platform re-tender is underway"
    elif "leadership" in fams:
        angle = "A new leader has just landed, so the supplier relationship is open"
    elif "demand" in fams:
        angle = "A senior build-out usually follows a move of this size, before the role is briefed out"
    else:
        angle = "Reachable on the trigger above, before the role is briefed out"
    return f"{angle}."


def _scale(triggers: list[dict]) -> str:
    """Size of the prize: a single retained search vs a build-out. An AD pitches
    these very differently and budgets time on exactly this."""
    keys = {t["key"] for t in triggers}
    if "job_ad_cluster" in keys:
        return "build-out (role cluster)"
    if sum(1 for t in triggers if t["family"] == "demand") >= 2:
        return "multi-signal build-out"
    return "single senior search"


_ACTION_LABEL = {
    "call_today": "Call today", "nurture": "Nurture",
    "investigate": "Investigate", "monitor": "Monitor",
}


def _who_to_call(triggers, seeded, seeded_role, who_map, who_default):
    # A named, mapped functional contact always wins over the generic buyer.
    if seeded:
        return seeded + (f" ({seeded_role})" if seeded_role else "")
    return who_map.get(triggers[0]["key"], who_default) if triggers else who_default


def _why_now_narrative(desk: str, company: str, trig_label: str | None,
                       seat: str | None, window: str | None, band: str,
                       pro_human: list[str], contradictions: list[str],
                       market_note: str) -> str:
    """Articulate the CONJUNCTION, not a single-trigger heuristic. British, no
    em dashes. This is what makes the dossier say *why* a pre-market hiring
    opportunity is real (or why it is only one to watch)."""
    noun = "marketing" if (desk or "comms").lower() == "marketing" else "comms"
    co = (company or "this account").strip()
    seat = (seat or f"a senior {noun} hire").strip()
    w = (window or "the coming weeks").strip()
    trig = (trig_label or "a recent development").strip()

    def _join(items):
        items = [i for i in items if i]
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        return ", ".join(items[:-1]) + " and " + items[-1]

    if band == "strong":
        stack = _join(pro_human)
        tail = f" It stacks with {stack}." if stack else ""
        return (f"{trig} at {co}, and the signals point the same way.{tail} "
                f"This reads as a team build heading to an agency, so {seat} is winnable now. Worth a call.")
    if band == "premature":
        return (f"{trig} at {co} has only just landed. A new leader rarely has a hiring plan for "
                f"8 to 12 weeks, so prepare the approach now and make contact when the plan forms.")
    if band == "watch":
        return (f"{trig} at {co}, but {_join(contradictions) or 'the corroborating signals are not there yet'}. "
                f"The signals conflict, so this is one to watch rather than call.")
    if band == "parked":
        return (f"{trig} at {co}. {_join(contradictions) or 'Not a clean external mandate'}. "
                f"Not actionable as a BD lead.")
    # promising
    missing = []
    if not pro_human:
        missing.append("no corroborating build-out yet")
    if "contracting" in (market_note or ""):
        missing.append(market_note)
    tail = _join(missing) or "it needs a second, corroborating signal"
    extra = (" " + _join(pro_human) + " already point the right way, but") if pro_human else ""
    return (f"{trig} at {co}. A real trigger for {seat};{extra} {tail}. "
            f"Prepare and watch for the build-out within {w}.")


def score_lead(item: dict, kind: str = "predictor", desk: str = "comms") -> dict:
    """Score one BD lead on the two-axis model. `item` is a persisted
    predictor dict, or a funding event when kind='funding'. `desk` selects the
    comms / marketing taxonomy + buyer map. Returns the `lead` sub-dict; never
    raises (returns a safe default on bad input)."""
    try:
        taxonomy, who_map, who_default = _tables(desk)
        company = (item.get("company") or "").strip()
        if kind == "funding":
            events = [{
                "trigger_key": "funding",
                "trigger_label": f"{item.get('amount','')} {item.get('round','')}".strip() or "Funding round",
                "evidence": item.get("evidence") or "",
                "url": item.get("url") or "", "source": item.get("source") or "",
                "published": item.get("first_seen") or item.get("last_seen"),
                "tier": item.get("tier") or "",
            }]
            account_tier = "watchlist"   # funding rows are UK-gated, on-patch by detection
            fallback = item.get("first_seen")
            name, name_role, who_url = None, None, ""
        else:
            events = [e for e in (item.get("events") or []) if isinstance(e, dict)]
            account_tier = item.get("account_tier") or "watchlist"
            fallback = item.get("last_seen") or item.get("first_seen")
            # Resolve a PERSON, not just a seat: seeded hiring contact, then a
            # resolved LinkedIn profile name. The dossier should never make the
            # AD open a tab to find who to ring.
            name = item.get("seeded_contact_name") or item.get("linkedin_profile_name")
            name_role = item.get("seeded_contact_role") or item.get("linkedin_profile_role")
            _lk = item.get("linkedin")
            who_url = (item.get("linkedin_profile_url")
                       or ((_lk or {}).get("url") if isinstance(_lk, dict) else (_lk or ""))
                       or "")

        conflict = _is_recruiter(company)
        fit_pts, fit_band, fit_why = fit_score(company, account_tier)
        if conflict:
            # Out of ICP by conflict, regardless of sector/size/UK.
            fit_pts, fit_band = 2, "out"
            fit_why = "Out: competing recruiter / staffing firm (likely conflict)"
        events, _extra_corr = _dedupe_events(events)
        signal, triggers = _signal(events, fallback, taxonomy)
        anti_flags, anti_mult, cap = _anti_triggers(events)
        if conflict:
            anti_flags = anti_flags + ["competing_recruiter"]
            cap = True   # never routes above Monitor
        # The freeze dampener (0.7) blocks the PERM line only: with live
        # demand in the stack (open ads / an RFP / interim cover) the work
        # demonstrably exists and the day-rate interim play runs at full
        # strength. RECENCY GUARD: the demand must POSTDATE the freeze
        # signal — a zombie ad posted before the freeze and never taken
        # down proves nothing, so it does not lift the dampener.
        if "hiring_freeze" in anti_flags:
            _frz_rx = next(rx for n, rx, _eff in _ANTI
                           if n == "hiring_freeze")
            _frz_at = max((e.get("published") or "" for e in events
                           if _frz_rx.search(
                               str(e.get("evidence") or "") + " "
                               + str(e.get("trigger_label") or ""))),
                          default="")
            if any((e.get("trigger_key") or "") in
                   ("job_ad_cluster", "ic_platform_rfp", "interim_watch")
                   and (e.get("published") or "") >= _frz_at
                   for e in events):
                anti_mult = min(round(anti_mult / 0.7, 4), 1.0)
        signal = round(signal * anti_mult, 2)
        # Corroboration gate: 2+ independent sources OR one Tier-1 verified
        # signal. A lone single-source scrape can't reach Call today.
        independent = len({(t.get("source") or t.get("url") or "").lower()
                           for t in triggers if (t.get("source") or t.get("url"))})
        has_verified = any(t.get("confidence") == "verified" for t in triggers)
        # A collapsed press cluster spanning 2+ outlets still corroborates —
        # dedupe shrinks the COUNT, never the evidence.
        corroborated = independent >= 2 or has_verified or _extra_corr

        # ---- the CONJUNCTION model (the report's core) ----------------------
        # Only signals that are still live (not decayed) count toward the stack.
        live = [t for t in triggers if (t.get("recency_mult") or 0) >= 0.3]
        live_keys = {t.get("key") for t in live}
        live_fams = {t.get("family") for t in live}
        quality_trigger = any(t.get("confidence") in ("verified", "corroborated") for t in live)
        demand_now = any(t.get("key") in ("job_ad_cluster", "ic_platform_rfp")
                         and (t.get("recency_mult") or 0) >= 0.6 for t in live)
        buildout = _scale(live) != "single senior search"
        multi_event = len(live_keys) >= 2
        fin = _financial_direction(events, live)
        posture = _posture(item, live, anti_flags)
        mkt = _market_state(company)

        # EXTRA same-direction layers on top of the base trigger. These are the
        # independent corroborations the report calls "signal stacking across
        # different layers": a second distinct event, growth/funding, or an
        # external posture read.
        dim_financial = fin["direction"] == "pro"
        dim_posture = posture["direction"] == "external"
        n_dim = sum((multi_event, dim_financial, dim_posture))
        # a single number for ordering / debugging: base trigger + extra layers
        n_pro = (1 if quality_trigger else 0) + n_dim

        # negative scoring: a trigger sitting beside a contradiction is a watch
        # Exception: when redundancy IS the trigger, cuts are the signal, not a
        # contradiction — redundancy programmes always need comms.
        _is_redundancy_lead = "redundancy" in live_keys
        contradictions = []
        if not _is_redundancy_lead and (fin["direction"] in ("anti", "conflicting") or "layoffs" in anti_flags):
            contradictions.append("budget pressure or cuts point the other way")
        if posture["direction"] == "internal":
            contradictions.append("the hiring looks likely to be absorbed in-house")
        if "competitor_lock" in anti_flags:
            contradictions.append("the search looks locked to an incumbent")
        material_contradiction = bool(contradictions)

        # ---- the too-fresh hold, per family (v2 window re-tool) -------------
        # Leadership changes present in the 4-12 week window: a new leader
        # needs ~a month in seat before a hiring plan exists (the research's
        # "honeymoon" timing — a CMO at month 2-4 is prime), so the hold is
        # 28 days. Funding / IPO keep the shorter 21-day hold — budget is
        # real sooner. The hold binds per anticipatory trigger (a fresh
        # crisis next to a mature leadership change no longer re-freezes the
        # stack). Active demand always bypasses the hold.
        _hold_pairs = []
        for t in live:
            _age = t.get("age_days")
            if _age is None:
                continue
            if t.get("family") == "leadership":
                _hold_pairs.append((LEADERSHIP_HOLD_DAYS, _age))
            elif t.get("key") in ("funding", "ipo_listing"):
                _hold_pairs.append((EVENT_HOLD_DAYS, _age))
        _binding = max(((h, a) for h, a in _hold_pairs if a < h),
                       key=lambda p: p[0] - p[1], default=None)
        freshest = min((t.get("age_days") for t in live if t.get("age_days") is not None),
                       default=999)
        too_fresh = _binding is not None and not demand_now
        fresh_hold_days = _binding[0] if _binding else 0
        freshest_age_days = _binding[1] if _binding else freshest

        strength, action = _assess(
            fit_band=fit_band, demand_now=demand_now, n_dim=n_dim,
            corroborated=corroborated, cap=cap, conflict=conflict,
            contradiction=material_contradiction, too_fresh=too_fresh,
            quality_trigger=quality_trigger, stack_req=mkt["stack_req"])

        # Human-readable stack for the dossier narrative.
        pro_human = []
        if demand_now:
            pro_human.append("several roles already open")
        elif buildout:
            pro_human.append("a team build, not a backfill")
        elif multi_event:
            pro_human.append("more than one signal at once")
        if dim_financial:
            pro_human.append("growth funding the build")
        if dim_posture and not demand_now:
            pro_human.append("no in-house recruiter in sight")

        access_text = _access(triggers)

        seat = (item.get("predicted_role") or "").strip() or None
        window = (item.get("window_label") or item.get("window") or "").strip() or None
        why_now = _why_now_narrative(
            desk, company, (triggers[0].get("label") if triggers else None),
            seat, window, strength, pro_human, contradictions, mkt["note"])

        return {
            "fit": fit_pts, "fit_band": fit_band, "fit_why": fit_why,
            "signal": signal,
            "signal_band": ("high" if signal >= _SIGNAL_HIGH
                            else "medium" if signal >= 3 else "low"),
            "action": action, "action_label": _ACTION_LABEL[action],
            "access_text": access_text,
            "scale": _scale(triggers),
            "conflict": conflict,
            "who_to_call": _who_to_call(triggers, name, name_role, who_map, who_default),
            "who_url": who_url,
            "corroboration": len(triggers), "corroborated": corroborated,
            "anti_triggers": anti_flags,
            "triggers": triggers,
            # ---- the conjunction layer ----
            "strength": strength,
            "strength_rank": _STRENGTH_RANK.get(strength, 0.4),
            "stack": pro_human,
            "n_pro": n_pro,
            "contradictions": contradictions,
            "premature": too_fresh,
            "fresh_hold_days": fresh_hold_days,
            "freshest_age_days": freshest_age_days,
            "financial": fin,
            "posture": posture,
            "market_state": {"state": mkt["state"], "note": mkt["note"]},
            "why_now": why_now,
        }
    except Exception:
        return {"fit": 0, "fit_band": "out", "fit_why": "", "signal": 0.0,
                "signal_band": "low", "action": "monitor", "action_label": "Monitor",
                "access_text": "",
                "scale": "single senior search", "conflict": False,
                "who_to_call": _WHO_DEFAULT,
                "who_url": "", "corroboration": 0, "corroborated": False,
                "anti_triggers": [], "triggers": [],
                "strength": "watch", "strength_rank": 0.4, "stack": [], "n_pro": 0,
                "contradictions": [], "premature": False,
                "fresh_hold_days": 0, "freshest_age_days": 999,
                "financial": {"direction": "neutral"}, "posture": {"direction": "neutral"},
                "market_state": {"state": "flat", "note": ""}, "why_now": ""}
