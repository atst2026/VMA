"""Fee-propensity store — will this company actually PAY an agency?

The research consensus on recruitment BD qualification: timing signals
say WHEN a need exists; fee-propensity says whether it converts into a
mandate. The two strongest pre-contact propensity facts, both free:

  ANTI — the company is hiring its own recruiters. TA/recruiter-titled
         postings on its careers board mean the in-house route is being
         built (the ~10-15 hires/yr in-house threshold); agency fees are
         what that team exists to avoid.
  PRO  — the company demonstrably buys recruitment. Public-sector award
         notices naming a recruitment/search supplier prove the buyer
         pays fees; agency-posted comms/marketing job ads prove they pay
         them for VMA's disciplines specifically; /red-team and
         /investigate research the rest online and write what they find
         back here (record_finding). Every agency_user fact carries a
         SCOPE (comms_marketing / general / temp_staffing) — temp-only
         supply never counts as a proven search fee-payer.

The store persists per-company flags with evidence + dates, and
annotate() projects them onto a pipeline entry as the `internal_ta` /
`psl_status` fields lead_engine._posture already reads (authoritative
inputs that were previously never populated). Flags expire so a TA team
hired two years ago can't suppress a 2027 lead forever.

Sources of truth, in precedence order:
  1. Research findings — written programmatically by the /red-team and
     /investigate commands after checking the company online (TA team on
     LinkedIn/public record, agency-posted ads, careers-page evidence).
     record_finding() is their write API. No manual admin work.
  2. Machine observations — ATS TA-counts (daily) and procurement awards
     to recruitment suppliers, from the signals the brief already
     collects.
  (propensity_seeds.json remains supported as an optional override for
  anything the desk wants to pin, but nothing depends on it.)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from tool.state_paths import state_dir

log = logging.getLogger("brief.propensity")

# A TA observation older than this no longer suppresses (teams shrink).
TA_EXPIRE_DAYS = 120
# An observed agency award stays meaningful for years.
AGENCY_EXPIRE_DAYS = 730

# Procurement award notices that prove the buyer pays recruitment fees.
_AGENCY_AWARD_RX = re.compile(
    r"\b(executive search|recruitment services|recruitment agency|"
    r"search and selection|talent acquisition services|headhunt\w*|"
    r"interim management services|temporary staff(?:ing)?|"
    r"permanent recruitment)\b", re.I)
_AWARD_RX = re.compile(r"\b(award(?:ed)?|contract award|awarded to)\b", re.I)

# ---- Agency-use SCOPE -------------------------------------------------
# "They pay recruitment fees" is not one fact. A temp-staffing contract
# for seasonal ops is not evidence anyone would retain a search firm for
# a Head of Communications; an agency-posted comms ad is. Every
# agency_user flag therefore carries a scope:
#   comms_marketing  — fees paid for VMA's disciplines (the gold tier)
#   general          — fees paid, function unverified
#   temp_staffing    — temp/interim volume supply only (does NOT count
#                      as a proven fee-payer for a retained search)
SCOPE_COMMS_MKT = "comms_marketing"
SCOPE_GENERAL = "general"
SCOPE_TEMP = "temp_staffing"

_COMMS_MKT_RX = re.compile(
    r"\b(communications?|comms|public relations|\bPR\b|press office|"
    r"corporate affairs|external affairs|public affairs|investor relations|"
    r"media relations|marketing|brand)\b", re.I)
# Award-regex matches that only ever prove temp/interim volume supply.
_TEMP_TERM_RX = re.compile(
    r"^(interim management services|temporary staff(?:ing)?)$", re.I)

# Posters that look like recruitment agencies (the company field on an
# agency-posted job ad is the agency, not the employer).
_AGENCY_POSTER_RX = re.compile(
    r"\b(recruit\w*|resourcing|staffing|headhunt\w*|talent|search|"
    r"search and selection|executive search)\b", re.I)


def _award_scope(text: str) -> str:
    """Classify an award notice's scope. Comms/marketing language wins;
    a notice whose only recruitment terms are temp/interim is temp-only;
    everything else is a general (function-unverified) fee-payer."""
    if _COMMS_MKT_RX.search(text or ""):
        return SCOPE_COMMS_MKT
    terms = _AGENCY_AWARD_RX.findall(text or "")
    if terms and all(_TEMP_TERM_RX.match(t) for t in terms):
        return SCOPE_TEMP
    return SCOPE_GENERAL


def scope_label(scope: str | None) -> str:
    """Short human label for a scope, used by the dashboard pill."""
    return {SCOPE_COMMS_MKT: "comms/marketing",
            SCOPE_TEMP: "temp staffing only",
            SCOPE_GENERAL: "function unverified"}.get(scope or "", "")


def _store_path() -> Path:
    return Path(str(state_dir())) / "propensity.json"


def _seeds_path() -> Path:
    return Path(str(state_dir())) / "propensity_seeds.json"


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _load(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.info("propensity file %s unreadable (%s)", path.name, e)
        return {}


def _save(data: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(p)


def _fresh(iso: str | None, max_days: int,
           now: datetime | None = None) -> bool:
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return ((now or datetime.now(timezone.utc)) - d).days <= max_days
    except ValueError:
        return False


def ingest_ats_counts(counts: dict, slug_to_company=None) -> int:
    """Fold today's ATS tallies into the store. `counts` is
    jobs.get_ats_headcounts() — {slug: (total, comms, ta)}. Returns the
    number of companies marked as hiring TA. Never raises."""
    try:
        store = _load(_store_path())
        now = datetime.now(timezone.utc).isoformat()
        marked = 0
        for slug, tally in (counts or {}).items():
            ta = tally[2] if len(tally) > 2 else 0
            display = (slug_to_company(slug) if slug_to_company
                       else slug.replace("-", " ").replace("_", " ").title())
            key = _norm(display)
            if not key:
                continue
            rec = store.setdefault(key, {"company": display})
            if ta > 0:
                rec["internal_ta"] = {
                    "count": int(ta), "seen": now,
                    "evidence": (f"{display} is advertising {ta} talent-"
                                 f"acquisition/recruiter role"
                                 f"{'s' if ta != 1 else ''} on its own "
                                 f"careers board — building the in-house "
                                 f"route.")}
                marked += 1
            elif "internal_ta" in rec:
                # Board now shows zero TA roles: stamp the all-clear so the
                # flag ages out from this observation, not the original.
                rec["internal_ta"]["cleared"] = now
        _save(store)
        log.info("propensity: %d companies marked TA-hiring from %d boards",
                 marked, len(counts or {}))
        return marked
    except Exception as e:
        log.info("propensity ATS ingest skipped (%s)", e)
        return 0


def scan_signals_for_agency_awards(signals: list[dict]) -> int:
    """Detect procurement award notices for recruitment/search services in
    the signals the brief already collects, and record the BUYER as a
    proven agency user. Returns the number recorded. Never raises."""
    try:
        from tool.account_match import classify_account
        store = _load(_store_path())
        now = datetime.now(timezone.utc).isoformat()
        found = 0
        for sig in signals or []:
            if not isinstance(sig, dict):
                continue
            text = f"{sig.get('title') or ''} . {sig.get('summary') or ''}"
            if not (_AGENCY_AWARD_RX.search(text) and _AWARD_RX.search(text)):
                continue
            buyer, tier = classify_account(None, text)
            if not buyer or tier != "watchlist":
                continue
            scope = _award_scope(text)
            if scope == SCOPE_COMMS_MKT:
                ev = (f"{buyer} awarded a recruitment/search contract "
                      f"covering comms/marketing (public procurement "
                      f"notice) — a proven fee-payer for VMA's disciplines.")
            elif scope == SCOPE_TEMP:
                ev = (f"{buyer} awarded a temp-staffing/interim supply "
                      f"contract (public procurement notice) — pays for "
                      f"volume supply, NOT evidence they'd retain a "
                      f"comms/marketing search.")
            else:
                ev = (f"{buyer} awarded a recruitment/search services "
                      f"contract (public procurement notice) — a "
                      f"proven fee-payer (function unverified).")
            key = _norm(buyer)
            rec = store.setdefault(key, {"company": buyer})
            existing = rec.get("agency_user") or {}
            # Never let a temp-only notice overwrite stronger scoped
            # evidence already on file.
            if (existing.get("scope") == SCOPE_COMMS_MKT
                    and scope != SCOPE_COMMS_MKT
                    and _fresh(existing.get("seen"), AGENCY_EXPIRE_DAYS)):
                continue
            rec["agency_user"] = {
                "seen": now, "scope": scope, "evidence": ev,
                "url": sig.get("url") or ""}
            found += 1
        if found:
            _save(store)
        log.info("propensity: %d agency-award buyers recorded", found)
        return found
    except Exception as e:
        log.info("propensity award scan skipped (%s)", e)
        return 0


def scan_job_signals_for_agency_posted_ads(signals: list[dict]) -> int:
    """The strongest function-specific propensity fact available for free:
    a comms/marketing job ad POSTED BY a recruitment agency on a watchlist
    company's behalf. Job signals already passed the comms/marketing role
    filter at ingest, so poster-is-an-agency + client-named-in-the-ad
    proves the client pays fees for exactly VMA's disciplines. Records the
    CLIENT as a comms/marketing-scope agency user. Never raises."""
    try:
        from tool.account_match import classify_account
        try:
            from tool.sources.jobs import JOB_AGGREGATORS
        except Exception:
            JOB_AGGREGATORS = ()
        store = _load(_store_path())
        now = datetime.now(timezone.utc).isoformat()
        found = 0
        for sig in signals or []:
            if not isinstance(sig, dict) or sig.get("kind") != "job":
                continue
            poster = (sig.get("company") or "").strip()
            # Poster must look like a recruitment agency — and not be a
            # job board/aggregator wearing a recruitment-y name (Reed,
            # Totaljobs etc. host direct-employer ads).
            if (not poster or poster.lower() in JOB_AGGREGATORS
                    or not _AGENCY_POSTER_RX.search(poster)):
                continue
            text = f"{sig.get('title') or ''} . {sig.get('summary') or ''}"
            client, tier = classify_account(None, text)
            if (not client or tier != "watchlist"
                    or _norm(client) == _norm(poster)):
                continue
            key = _norm(client)
            rec = store.setdefault(key, {"company": client})
            title = (sig.get("title") or "this role").strip()
            rec["agency_user"] = {
                "seen": now, "scope": SCOPE_COMMS_MKT,
                "evidence": (f"“{title}” at {client} is advertised "
                             f"by {poster} — they demonstrably pay agency "
                             f"fees for comms/marketing hires."),
                "url": sig.get("url") or ""}
            found += 1
        if found:
            _save(store)
        log.info("propensity: %d agency-posted comms/marketing ads recorded",
                 found)
        return found
    except Exception as e:
        log.info("propensity agency-posted-ad scan skipped (%s)", e)
        return 0


def record_finding(company: str | None, *,
                   internal_ta: bool | None = None,
                   agency_user: bool | None = None,
                   agency_scope: str | None = None,
                   note: str = "", source_url: str = "") -> bool:
    """Write API for /red-team and /investigate: persist a researched
    propensity fact for a company. Findings outrank machine observations
    and expire like them (TA 120d, agency-user 730d), so a researched
    fact is re-checked rather than trusted forever.

    `agency_scope` qualifies agency_user=True: "comms_marketing" (they
    pay fees for VMA's disciplines — agency-posted comms ad, a search-
    credited appointment in the trade press), "general" (fees paid,
    function unverified — the default), or "temp_staffing" (volume temp
    supply only, which does NOT count as a proven search fee-payer).
    Returns False when there is nothing to record. Never raises."""
    try:
        key = _norm(company)
        if not key or (internal_ta is None and agency_user is None):
            return False
        store = _load(_store_path())
        now = datetime.now(timezone.utc).isoformat()
        rec = store.setdefault(key, {"company": (company or "").strip()})
        finding = rec.setdefault("research", {})
        if internal_ta is not None:
            finding["internal_ta"] = {"value": bool(internal_ta), "seen": now,
                                      "evidence": (note or "research finding")[:300],
                                      "url": source_url[:300]}
        if agency_user is not None:
            scope = (agency_scope or SCOPE_GENERAL).strip().lower()
            if scope not in (SCOPE_COMMS_MKT, SCOPE_GENERAL, SCOPE_TEMP):
                scope = SCOPE_GENERAL
            finding["agency_user"] = {"value": bool(agency_user), "seen": now,
                                      "scope": scope,
                                      "evidence": (note or "research finding")[:300],
                                      "url": source_url[:300]}
        _save(store)
        return True
    except Exception as e:
        log.info("propensity record_finding skipped (%s)", e)
        return False


def flags_for(company: str | None,
              now: datetime | None = None) -> dict:
    """Current propensity flags for a company: seeds win, then fresh
    machine observations. Returns {} when nothing is known."""
    key = _norm(company)
    if not key:
        return {}
    out: dict = {}
    rec = _load(_store_path()).get(key) or {}
    ta = rec.get("internal_ta") or {}
    basis = ta.get("cleared") or ta.get("seen")
    if ta and _fresh(basis, TA_EXPIRE_DAYS, now) and not ta.get("cleared"):
        out["internal_ta"] = True
        out["internal_ta_evidence"] = ta.get("evidence") or ""
    ag = rec.get("agency_user") or {}
    if ag and _fresh(ag.get("seen"), AGENCY_EXPIRE_DAYS, now):
        out["agency_user"] = True
        out["agency_scope"] = ag.get("scope") or SCOPE_GENERAL
        out["agency_evidence"] = ag.get("evidence") or ""
    # Researched findings (written by /red-team and /investigate after
    # checking the company online) outrank passive observations — they
    # can both SET and CLEAR a flag.
    research = rec.get("research") or {}
    rta = research.get("internal_ta") or {}
    if rta and _fresh(rta.get("seen"), TA_EXPIRE_DAYS, now):
        if rta.get("value"):
            out["internal_ta"] = True
            out["internal_ta_evidence"] = rta.get("evidence") or ""
        else:
            out.pop("internal_ta", None)
            out.pop("internal_ta_evidence", None)
    rag = research.get("agency_user") or {}
    if rag and _fresh(rag.get("seen"), AGENCY_EXPIRE_DAYS, now):
        if rag.get("value"):
            out["agency_user"] = True
            out["agency_scope"] = rag.get("scope") or SCOPE_GENERAL
            out["agency_evidence"] = rag.get("evidence") or ""
        else:
            out.pop("agency_user", None)
            out.pop("agency_scope", None)
            out.pop("agency_evidence", None)
    seed = _load(_seeds_path()).get(key) or {}
    if isinstance(seed, dict):
        if seed.get("internal_ta") is not None:
            out["internal_ta"] = bool(seed["internal_ta"])
            out["internal_ta_evidence"] = seed.get("note") or "AD seed"
        if seed.get("agency_user") is not None:
            out["agency_user"] = bool(seed["agency_user"])
            out["agency_scope"] = seed.get("agency_scope") or SCOPE_GENERAL
            out["agency_evidence"] = seed.get("note") or "AD seed"
    return out


def annotate(item: dict) -> dict:
    """Project the flags onto a pipeline entry as the authoritative
    inputs lead_engine._posture reads (`internal_ta`, `psl_status`).
    Mutates and returns `item`; never raises."""
    try:
        f = flags_for((item or {}).get("company"))
        if not f:
            return item
        if f.get("internal_ta"):
            item["internal_ta"] = True
            item["_propensity_note"] = f.get("internal_ta_evidence", "")
        if f.get("agency_user"):
            scope = f.get("agency_scope") or SCOPE_GENERAL
            item["agency_scope"] = scope
            if scope == SCOPE_TEMP:
                # Temp/interim volume supply proves nothing about retained
                # search fees — surface the caveat, leave propensity at
                # the neutral default rather than "proven fee-payer".
                item["_propensity_note"] = f.get("agency_evidence", "")
            else:
                item["psl_status"] = "on"   # _posture's history-of-agency-use input
                item["_propensity_note"] = f.get("agency_evidence", "")
        return item
    except Exception:
        return item
