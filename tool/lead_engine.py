"""Two-axis BD lead scoring — Fit x Signal (comms desk).

Implements the v2.0 framework from the lead-sourcing research: a single
combined "strength" is split into two independent axes,

    FIT (ICP)     slow-moving, never decays   — should VMA serve this org at all?
    SIGNAL/INTENT fast, decayed, confidence-weighted — is a comms mandate
                                                  imminent and winnable?

and the lead is routed on the fit x signal matrix to one concrete action:
Call today / Nurture / Investigate / Monitor. Each contributing trigger
carries its own raw points x recency-decay x source-confidence, anti-triggers
suppress multiplicatively, and an access angle says *how* VMA gets in.

This is an ADDITIVE layer over tool.predictor_pipeline: it reads a persisted
predictor dict (or a funding event) and returns a `lead` sub-dict. It does not
change the legacy `score` / `strength` / ordering. Comms desk only for now;
the marketing taxonomy is a separate table.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

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
    "regulator_probe_early":   (2, "demand", "fast"),
    "contract_loss":           (2, "demand", "fast"),
    # Access triggers
    "ic_platform_rfp":         (4, "access", "fast"),
    # Soft / corroboration only (cannot trigger a lead on their own)
    "ned_trustee_appointment": (1, "soft", "slow"),
    "press_velocity_spike":    (1, "soft", "fast"),
    "personal_brand_velocity": (1, "soft", "slow"),
}

# Marketing desk taxonomy. Same trigger detection, different conversion logic:
# growth/brand mandates fire hardest on funding (growth budget), a CMO change
# and a job-ad cluster; corporate-comms-only triggers (IR/regulator) are
# down-weighted. Stress-tested against the same anti-triggers + corroboration
# gate as comms (see tests).
_MKT_TAXONOMY = {
    "comms_leader_departure":  (5, "leadership", "slow"),   # CMO / brand-lead change
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
    "crisis_event":            (3, "demand", "fast"),
    "activist_stake":          (2, "demand", "fast"),
    "regulator_action":        (2, "demand", "fast"),
    "regulator_probe_early":   (1, "demand", "fast"),
    "contract_loss":           (2, "demand", "fast"),
    "ic_platform_rfp":         (2, "access", "fast"),
    "ned_trustee_appointment": (1, "soft", "slow"),
    "press_velocity_spike":    (1, "soft", "fast"),
    "personal_brand_velocity": (1, "soft", "slow"),
}

_SOFT_CAP = 2.0          # soft modifiers add at most +2, and only alongside a real signal
_SIGNAL_HIGH = 6.0       # effective-points threshold for "High SIGNAL"
_FIT_HIGH = 7            # 0-10 threshold for "High FIT"

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
    ("hiring_freeze",  re.compile(r"hiring freeze|freeze on hiring|recruitment freeze|pause(?:d|s)? hiring", re.I), "cap"),
    ("layoffs",        re.compile(r"redundanc|lay[\s-]?offs?|job cuts|cutting \d+\s+jobs|axe[sd]? \d+", re.I), 0.3),
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
    "contract_loss": "CEO office / Corporate Affairs",
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
    "crisis_event": "CMO / Corporate Affairs",
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


def _recency_mult(age_days: float, decay: str) -> float:
    """Layer 2. Leadership change stays meaningful across the first ~90 days;
    event signals decay within weeks."""
    if decay == "slow":
        if age_days <= 90:
            return 1.0
        if age_days <= 120:
            return 0.6
        if age_days <= 150:
            return 0.3
        return 0.1
    if age_days <= 7:
        return 1.0
    if age_days <= 21:
        return 0.6
    if age_days <= 45:
        return 0.3
    return 0.1


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
        rmult = _recency_mult(age, decay)
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


def _access(triggers: list[dict], warm: bool, contact_name: str | None) -> tuple[str, str]:
    """The single most important thing before a BD call: warm or cold. `warm`
    means VMA has a contact on file for this account (the relationship proxy we
    can actually compute). The trigger supplies the angle; the relationship
    decides whether it's a warm follow-up or a cold open.

    NOTE: this is a contact-on-file proxy, not full relationship history — VMA
    has no integrated placements feed yet, so a genuine prior placement we
    don't have a contact card for will read 'cold'. That feed is the
    highest-value data integration still missing (see README/notes)."""
    fams = {t["family"] for t in triggers}
    if "access" in fams:
        angle = "A live RFP / platform re-tender is underway"
    elif "leadership" in fams:
        angle = "A new leader has just landed, so the supplier relationship is open"
    elif "demand" in fams:
        angle = "A senior build-out usually follows a move of this size, before the role is briefed out"
    else:
        angle = "Reachable on the trigger above, before the role is briefed out"
    if warm:
        nm = f" ({contact_name})" if contact_name else ""
        return ("warm", f"Warm: VMA has a contact on file{nm}. {angle}.")
    return ("cold", f"Cold: no VMA relationship on file. {angle}.")


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


def _route(fit_pts: int, signal: float, cap: bool, corroborated: bool) -> str:
    """Layer 5 routing. Call today additionally REQUIRES corroboration — a
    single uncorroborated signal never earns a call, however high its raw
    points; it routes to Investigate (verify before spending recruiter time).
    Belt-and-braces over the confidence weighting, which already caps a lone
    single-source signal well below threshold."""
    if cap:
        return "monitor"
    high_fit = fit_pts >= _FIT_HIGH
    high_sig = signal >= _SIGNAL_HIGH
    if high_fit and high_sig:
        return "call_today" if corroborated else "investigate"
    if high_fit:
        return "nurture"
    if high_sig:
        return "investigate"
    return "monitor"


# --------------------------------------------------------------------------
# The "work it" layer — what an AD needs to CONVERT a lead, not just open it.
# Each of these answers a qualifying question an AD asks before/while chasing:
# how big is the prize, who else is in, why VMA wins it, what objection it
# hits, and by when to chase. Everything is desk-aware (comms / marketing) and
# scrupulously honest about what the engine knows from scraped data versus what
# the AD confirms on the call — guesses are labelled, never asserted.
# --------------------------------------------------------------------------

# Indicative recruitment-fee bands (GBP) by seniority. A senior retained search
# on a ~£100-150k seat bills ~28-33% of total comp; a mid-level placement bills
# materially less. Deliberately COARSE ranges, surfaced as "indicative, confirm
# on the call" — they let an AD rank the week by expected value, which a bare
# "build-out (role cluster)" does not. Same order of magnitude across both desks.
_FEE_SENIOR = (25_000, 45_000)     # one senior retained search
_FEE_MID = (9_000, 18_000)         # per mid-level placement


def _fmt_fee(low: int, high: int) -> str:
    return f"£{low // 1000}k-£{high // 1000}k"


def _prize(triggers: list[dict], desk: str) -> dict:
    """Size and shape of the prize: how many roles, at what level, worth what
    fee. A job-ad cluster implies mid-level hiring already underway plus an
    unfilled senior mandate (3+ roles); a lone leadership move is a single
    senior search. Fees are indicative ranges the AD confirms against the brief."""
    noun = "marketing" if (desk or "comms").lower() == "marketing" else "comms"
    keys = {t.get("key") for t in triggers}
    cluster = "job_ad_cluster" in keys
    multi = sum(1 for t in triggers if t.get("family") == "demand") >= 2
    mids = 2 if cluster else (1 if multi else 0)
    roles = 1 + mids
    low = _FEE_SENIOR[0] + mids * _FEE_MID[0]
    high = _FEE_SENIOR[1] + mids * _FEE_MID[1]
    if mids:
        plus = "+" if cluster else ""
        mix = f"1 senior + {mids}{plus} mid-level {noun}"
        roles_label = f"{roles}{plus} roles"
    else:
        mix = f"single senior {noun} search"
        roles_label = "1 senior role"
    return {
        "roles": roles, "mix": mix, "fee": _fmt_fee(low, high),
        "fee_low": low, "fee_high": high,
        "summary": f"{roles_label}: {mix}. Indicative {_fmt_fee(low, high)} in fees.",
        "basis": "Indicative: fee scales with the exact level and salary; confirm the brief on the call.",
    }


def _competitive(anti_flags: list[str], item: dict) -> dict:
    """Who else is in — the thing that most often kills a chase. Surfaces what
    the engine can detect (an incumbent agency from exclusive-retainer language,
    an in-house TA team) and is honest that PSL status is rarely scrapeable, so
    it prompts the AD to confirm rather than asserting VMA is or isn't on it."""
    locked = "competitor_lock" in anti_flags
    in_house = "in_house_team" in anti_flags
    psl_raw = item.get("psl_status")
    if psl_raw in (True, "on", "yes"):
        psl = "on VMA's PSL"
    elif psl_raw in (False, "off", "no"):
        psl = "not on the PSL (confirm)"
    else:
        psl = "PSL status unknown (confirm on call)"
    incumbent = ("incumbent agency likely (exclusive-retainer language detected)"
                 if locked else "no incumbent agency detected (confirm on call)")
    ta = ("active in-house TA / team detected" if in_house
          else "in-house TA strength unknown (confirm)")
    verdict = "locked" if locked else ("contested" if in_house else "open")
    return {"psl": psl, "incumbent": incumbent, "internal_ta": ta,
            "verdict": verdict, "summary": f"{psl} · {incumbent} · {ta}"}


def _proof(desk: str, competitive: dict) -> dict:
    """The convert-not-just-contact proof. The opener gets the meeting; the AD
    wins the mandate on the second conversation by being specific about why VMA.
    We give the credible category angle and PROMPT the AD to cite a comparable
    placement — we never invent a specific one (that would be a fabricated claim
    the AD might repeat)."""
    mkt = (desk or "comms").lower() == "marketing"
    practice = ("marketing, brand and growth leadership" if mkt
                else "corporate communications and internal comms leadership")
    noun = "marketing" if mkt else "comms"
    angle = (f"VMA runs senior {noun} searches on a retained basis across {practice}. "
             f"Lead with a comparable recent VMA placement at this level and sector as the proof point.")
    if (competitive or {}).get("verdict") in ("contested", "locked"):
        vs = ("Against an in-house team or an incumbent agency: retained buys a market-mapped "
              "longlist assessed against the brief, passive reach into leaders who will not answer "
              "an ad, and an off-limits and replacement guarantee a CV-race supplier will not carry.")
    else:
        vs = "Open lane: be first and frame the brief before a competitor or an in-house hire does."
    return {"angle": angle, "vs_incumbent": vs}


def _objection(relationship: str, anti_flags: list[str],
               triggers: list[dict], desk: str) -> dict:
    """The predictable pushback for this lead type, with a one-line counter, so
    the AD is armed for the moment the call gets hard. Branches on what we know:
    an incumbent, an in-house team, a cold leadership/demand open, or a warm
    follow-up."""
    noun = "marketing" if (desk or "comms").lower() == "marketing" else "comms"
    fams = {t.get("family") for t in triggers}
    if "competitor_lock" in anti_flags:
        return {"likely": "It is already with an agency.",
                "counter": ("Understood. Is that retained or contingent? Retained gives you a "
                            "market-mapped longlist and off-limits protection a CV race will not, "
                            "so a parallel view is worth it before you commit.")}
    if "in_house_team" in anti_flags or (relationship == "cold" and "leadership" in fams):
        return {"likely": f"We handle {noun} hiring in-house.",
                "counter": ("Fine for BAU roles. This is a senior, discreet search where the "
                            "strongest leaders are in seat elsewhere and will not answer your ad, "
                            "which is the reach an in-house team cannot carry.")}
    if "demand" in fams and relationship == "cold":
        return {"likely": "We are not hiring at that level yet.",
                "counter": ("That is exactly the moment, because the senior mandate tends to follow "
                            "a move like this. Mapping the market early costs you nothing, and we "
                            "share who is moving right now.")}
    return {"likely": "Now is not the right time.",
            "counter": ("No problem. A short market map now means you are ready the day it is, "
                        "rather than starting cold under time pressure.")}


def _chase_by(triggers: list[dict]) -> dict:
    """Timing handle for the chase itself. The mandate WINDOW says when the role
    opens; this says when to FOLLOW UP if the first call goes to voicemail,
    derived from the decay model: an event signal's freshness fades within a
    week, a leadership signal holds but the first-mover edge fades within ~3
    weeks. Without it, a decaying lead just sits in the queue."""
    if not triggers:
        return {}
    best = min(triggers, key=lambda t: t.get("age_days") if t.get("age_days") is not None else 9999)
    age = best.get("age_days") or 0
    slow = best.get("family") == "leadership"
    window = 21 if slow else 7
    days_left = window - int(age)
    if days_left <= 0:
        days_left = 2          # lapsed: chase within a couple of days or lose it
    target = (datetime.now(timezone.utc) + timedelta(days=days_left)).date()
    rel = "today" if days_left == 0 else f"in {days_left} day" + ("" if days_left == 1 else "s")
    rationale = ("leadership signal holds, but the first-mover edge fades within ~3 weeks" if slow
                 else "event signal, freshness fades within a week")
    return {"date": target.isoformat(),
            "label": f"Chase by {target.day} {target.strftime('%b')}",
            "days": days_left, "rel": rel, "rationale": rationale}


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
        signal, triggers = _signal(events, fallback, taxonomy)
        anti_flags, anti_mult, cap = _anti_triggers(events)
        if conflict:
            anti_flags = anti_flags + ["competing_recruiter"]
            cap = True   # never routes above Monitor
        signal = round(signal * anti_mult, 2)
        # Corroboration gate: 2+ independent sources OR one Tier-1 verified
        # signal. A lone single-source scrape can't reach Call today.
        independent = len({(t.get("source") or t.get("url") or "").lower()
                           for t in triggers if (t.get("source") or t.get("url"))})
        has_verified = any(t.get("confidence") == "verified" for t in triggers)
        corroborated = independent >= 2 or has_verified
        action = _route(fit_pts, signal, cap, corroborated)
        # Warm/cold = do we hold a contact for this account (the proxy we can
        # compute). contact_on_file is set by the caller from the contacts store.
        warm = bool(name or item.get("contact_on_file"))
        access_key, access_text = _access(triggers, warm, name)
        relationship = "warm" if warm else "cold"
        # The "work it" layer — deal size, competitive position, the proof, the
        # likely objection, and a chase-by date (see the helpers above).
        competitive = _competitive(anti_flags, item)
        return {
            "fit": fit_pts, "fit_band": fit_band, "fit_why": fit_why,
            "signal": signal,
            "signal_band": ("high" if signal >= _SIGNAL_HIGH
                            else "medium" if signal >= 3 else "low"),
            "action": action, "action_label": _ACTION_LABEL[action],
            "access": access_key, "access_text": access_text,
            "relationship": relationship,
            "scale": _scale(triggers),
            "conflict": conflict,
            "who_to_call": _who_to_call(triggers, name, name_role, who_map, who_default),
            "who_url": who_url,
            "corroboration": len(triggers), "corroborated": corroborated,
            "anti_triggers": anti_flags,
            "triggers": triggers,
            "prize": _prize(triggers, desk),
            "competitive": competitive,
            "proof": _proof(desk, competitive),
            "objection": _objection(relationship, anti_flags, triggers, desk),
            "chase_by": _chase_by(triggers),
        }
    except Exception:
        return {"fit": 0, "fit_band": "out", "fit_why": "", "signal": 0.0,
                "signal_band": "low", "action": "monitor", "action_label": "Monitor",
                "access": "cold", "access_text": "", "relationship": "cold",
                "scale": "single senior search", "conflict": False,
                "who_to_call": _WHO_DEFAULT,
                "who_url": "", "corroboration": 0, "corroborated": False,
                "anti_triggers": [], "triggers": [],
                "prize": {}, "competitive": {}, "proof": {},
                "objection": {}, "chase_by": {}}
