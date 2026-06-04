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
    "chro_change": "CHRO / People Director",
    "comms_leader_departure": "CEO office / CHRO",
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
    """Layer 3. Tier 1 verified (filing / official / listed) x1.0;
    Tier 2 multi-source consensus (2+ independent) x0.6; Tier 3 single x0.3."""
    blob = " ".join(str(event.get(k) or "").lower() for k in ("url", "source", "tier"))
    if any(s in blob for s in _TIER1) or (event.get("tier") or "").lower() == "listed":
        return ("verified", 1.0)
    if independent_sources >= 2:
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


def _access(triggers: list[dict], seeded_contact: str | None,
            linkedin: str | None) -> tuple[str, str]:
    fams = {t["family"] for t in triggers}
    if seeded_contact:
        return ("contact_known", f"A named decision-maker is mapped ({seeded_contact}).")
    if linkedin:
        return ("contact_known", "A named decision-maker is already mapped.")
    if "access" in fams:
        return ("live_rfp", "A live RFP / platform re-tender is underway.")
    if "leadership" in fams:
        return ("new_supplier", "A new leader just landed, so the supplier relationship is open.")
    return ("inbound", "Reachable on the trigger above before the role is briefed out.")


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
            seeded, linkedin = None, None
        else:
            events = [e for e in (item.get("events") or []) if isinstance(e, dict)]
            account_tier = item.get("account_tier") or "watchlist"
            fallback = item.get("last_seen") or item.get("first_seen")
            seeded = item.get("seeded_contact_name")
            linkedin = item.get("linkedin_profile_url")

        fit_pts, fit_band, fit_why = fit_score(company, account_tier)
        signal, triggers = _signal(events, fallback, taxonomy)
        anti_flags, anti_mult, cap = _anti_triggers(events)
        signal = round(signal * anti_mult, 2)
        # Corroboration gate: 2+ independent sources OR one Tier-1 verified
        # signal. A lone single-source scrape can't reach Call today.
        independent = len({(t.get("source") or t.get("url") or "").lower()
                           for t in triggers if (t.get("source") or t.get("url"))})
        has_verified = any(t.get("confidence") == "verified" for t in triggers)
        corroborated = independent >= 2 or has_verified
        action = _route(fit_pts, signal, cap, corroborated)
        access_key, access_text = _access(triggers, seeded, linkedin)
        return {
            "fit": fit_pts, "fit_band": fit_band, "fit_why": fit_why,
            "signal": signal,
            "signal_band": ("high" if signal >= _SIGNAL_HIGH
                            else "medium" if signal >= 3 else "low"),
            "action": action, "action_label": _ACTION_LABEL[action],
            "access": access_key, "access_text": access_text,
            "who_to_call": _who_to_call(triggers, seeded,
                                        item.get("seeded_contact_role"),
                                        who_map, who_default),
            "corroboration": len(triggers), "corroborated": corroborated,
            "anti_triggers": anti_flags,
            "triggers": triggers,
        }
    except Exception:
        return {"fit": 0, "fit_band": "out", "fit_why": "", "signal": 0.0,
                "signal_band": "low", "action": "monitor", "action_label": "Monitor",
                "access": "inbound", "access_text": "", "who_to_call": _WHO_DEFAULT,
                "corroboration": 0, "corroborated": False,
                "anti_triggers": [], "triggers": []}
