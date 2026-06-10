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
         pays fees; /red-team and /investigate research the rest online
         and write what they find back here (record_finding).

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
            key = _norm(buyer)
            rec = store.setdefault(key, {"company": buyer})
            rec["agency_user"] = {
                "seen": now,
                "evidence": (f"{buyer} awarded a recruitment/search services "
                             f"contract (public procurement notice) — a "
                             f"proven fee-payer."),
                "url": sig.get("url") or ""}
            found += 1
        if found:
            _save(store)
        log.info("propensity: %d agency-award buyers recorded", found)
        return found
    except Exception as e:
        log.info("propensity award scan skipped (%s)", e)
        return 0


def record_finding(company: str | None, *,
                   internal_ta: bool | None = None,
                   agency_user: bool | None = None,
                   note: str = "", source_url: str = "") -> bool:
    """Write API for /red-team and /investigate: persist a researched
    propensity fact for a company. Findings outrank machine observations
    and expire like them (TA 120d, agency-user 730d), so a researched
    fact is re-checked rather than trusted forever. Returns False when
    there is nothing to record. Never raises."""
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
            finding["agency_user"] = {"value": bool(agency_user), "seen": now,
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
            out["agency_evidence"] = rag.get("evidence") or ""
        else:
            out.pop("agency_user", None)
            out.pop("agency_evidence", None)
    seed = _load(_seeds_path()).get(key) or {}
    if isinstance(seed, dict):
        if seed.get("internal_ta") is not None:
            out["internal_ta"] = bool(seed["internal_ta"])
            out["internal_ta_evidence"] = seed.get("note") or "AD seed"
        if seed.get("agency_user") is not None:
            out["agency_user"] = bool(seed["agency_user"])
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
            item["psl_status"] = "on"   # _posture's history-of-agency-use input
            item["_propensity_note"] = f.get("agency_evidence", "")
        return item
    except Exception:
        return item
