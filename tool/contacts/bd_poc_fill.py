"""Resolve named Points of Contact for the BD board's companies.

The POC section on BD lead cards is only worth anything when it shows a
REAL person — name, title, and their actual LinkedIn profile. This pass
makes that happen for ANY company the board surfaces (no pre-seeded
roster required — the contacts store is a research CACHE, not a manual
list), in two layers per company:

  1. FREE multi-source resolver — Companies House officers -> RNS
     appointments -> leadership page via Bright Data -> LinkedIn search
     for the desk's function-family slots, plus the Bright Data profile
     resolver to attach a real /in/ URL.
  2. MODEL research fallback — when the free chain can't name an owner,
     the same model + live-web-search engine that researches live jobs
     (tool/contacts/job_researcher) answers "who owns this function at
     this employer today?", budgeted separately and writing through the
     same store under the same acceptance rules.

Everything lands through the same store as every other source, so the
confidence floor, freshness window, Sara's flags and the resolution log
all apply.

Budget: MAX_RESOLUTIONS_PER_RUN free-resolver calls + RESEARCH_MAX
model passes per run; a per-(company, slot) attempt ledger keeps misses
from re-spending daily (successes hold ATTEMPT_TTL_DAYS, misses retry
after FAILURE_RETRY_DAYS), and at most 2 slot attempts per company per
run. Never raises.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from tool.state_paths import state_dir

log = logging.getLogger("brief.contacts.bd_poc")

# Per-run resolution budgets. Raised to clear the ~150-company first-pass
# backlog in 2-3 nightly runs rather than a week — each resolution is
# free (Companies House + RNS + the company's own site, all cached, all
# behind the attempt ledger so nothing is re-spent). Override via
# env to dial back without a deploy.
MAX_RESOLUTIONS_PER_RUN = int(
    os.environ.get("BD_POC_MAX_RESOLUTIONS") or 60)
JOBS_MAX_RESOLUTIONS_PER_RUN = int(
    os.environ.get("JOBS_CONTACT_MAX_RESOLUTIONS") or 30)
# Model-research fallback passes per run (Anthropic credits) for board
# companies the free chain couldn't name an owner at.
RESEARCH_MAX_PER_RUN = int(
    os.environ.get("BD_POC_RESEARCH_MAX") or 10)
ATTEMPT_TTL_DAYS = 7
FAILURE_RETRY_DAYS = 3
MAX_SLOTS_PER_COMPANY = 2


def capability_line() -> str:
    """One honest line per run about what the resolver can actually
    reach — the zero-resolution runs in the logs were caused by a
    missing Bright Data zone silently disabling two sources."""
    try:
        from tool.config import BRIGHT_DATA_KEY
        import os as _os
        zone = (_os.environ.get("BRIGHT_DATA_ZONE") or "").strip()
        if BRIGHT_DATA_KEY and zone:
            bd = "on"
        elif BRIGHT_DATA_KEY:
            bd = "OFF (BRIGHT_DATA_ZONE not set — Google/LinkedIn "
            bd += "sources disabled; direct-site source still runs)"
        else:
            bd = "OFF (no key)"
    except Exception:
        bd = "unknown"
    anthropic = ("on" if (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
                 else "OFF (no key)")
    return f"capabilities: bright_data={bd}; anthropic_key={anthropic}"


def _ledger_file():
    return state_dir() / "bd_poc_fill.json"


def _load_ledger() -> dict:
    try:
        f = _ledger_file()
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def _save_ledger(d: dict) -> None:
    try:
        _ledger_file().write_text(json.dumps(d, indent=1))
    except Exception:
        pass


def _recently_tried(ledger: dict, company: str, slot: str) -> bool:
    rec = ledger.get(f"{company.lower()}::{slot}") or {}
    at = rec.get("at") or ""
    try:
        t = datetime.fromisoformat(at)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        # Failure-aware: a resolved slot holds the full window; a miss
        # frees up for retry sooner (team pages and registries change).
        days = (ATTEMPT_TTL_DAYS if rec.get("resolved")
                else FAILURE_RETRY_DAYS)
        return (datetime.now(timezone.utc) - t) < timedelta(days=days)
    except Exception:
        return False


def _has_fresh_owner(card, slots) -> bool:
    """True if ANY of the function-family slots already holds a fresh,
    floor-clearing named entry — nothing to resolve."""
    if card is None:
        return False
    for slot in slots:
        e = card.get(slot)
        if (e and getattr(e, "name", "") and e.is_fresh()
                and e.meets_named_confidence()):
            return True
    return False


def fill_for_signals(signals: list[dict], desk: str = "comms",
                     max_resolutions: int = JOBS_MAX_RESOLUTIONS_PER_RUN,
                     resolver=None, profile_resolver=None) -> dict:
    """The live-jobs twin of run(): resolve each job-like signal's OWN
    inferred slots (the seat the vacancy reports into) through the same
    multi-source resolver, shared attempt ledger and guards. This is
    what puts a NAMED contact on a live job with zero model credits and
    zero Hunter spend. Returns counters. Never raises."""
    try:
        from tool.hiring_manager import is_job_like, manager_for_signal
        companies, slot_map = [], {}
        for s in signals or []:
            if not isinstance(s, dict) or not is_job_like(s):
                continue
            company = (s.get("company") or "").strip()
            if not company or company in slot_map:
                continue
            slots = tuple(sl for sl in
                          (manager_for_signal(s).get("slots") or ())
                          if sl != "chro")
            if slots:
                companies.append(company)
                slot_map[company] = slots
        return run(companies, desk=desk, max_resolutions=max_resolutions,
                   resolver=resolver, profile_resolver=profile_resolver,
                   slots_for=lambda c: slot_map.get(c, ()))
    except Exception as e:
        log.info("jobs contact fill skipped (%s)", e)
        return {"resolved": 0, "attempted": 0, "profile_links": 0}


def run(companies: list[str], desk: str = "comms",
        max_resolutions: int = MAX_RESOLUTIONS_PER_RUN,
        fetch=None, resolver=None, profile_resolver=None,
        slots_for=None, research_max: int = RESEARCH_MAX_PER_RUN,
        research_runner=None, context_for=None) -> dict:
    """Fill missing function-owner contacts for `companies` (ranked
    order = spend priority). `slots_for(company)` optionally overrides
    the desk's default slot family (the live-jobs fill passes each
    vacancy's own inferred seat). Companies the FREE chain can't name an
    owner at fall through to the model-research layer (`research_max`
    passes; `context_for(company)` supplies trigger context as search
    anchors). Returns counters. Never raises."""
    stats = {"resolved": 0, "attempted": 0, "profile_links": 0,
             "researched": 0, "research_resolved": 0}
    try:
        from tool.contacts.resolver import resolve as _resolve
        from tool.contacts.store import (load_contacts, save_contacts,
                                         get_contact, upsert_contact,
                                         append_resolution_log)
        from tool.hiring_manager import _BD_POC_SLOTS

        resolver = resolver or _resolve
        if fetch is None:
            try:
                from tool.linkedin_resolver import _bright_data_fetch
                from tool.config import BRIGHT_DATA_KEY
                fetch = _bright_data_fetch if BRIGHT_DATA_KEY else None
            except Exception:
                fetch = None
        if profile_resolver is None:
            try:
                from tool.linkedin_resolver import resolve_profile
                profile_resolver = resolve_profile
            except Exception:
                profile_resolver = lambda company, role: None  # noqa: E731

        # The function family only (chro arrives via CH/auto-update);
        # the display layer adds chro when the roster has one.
        default_slots = tuple(s for s in _BD_POC_SLOTS.get(
            desk, _BD_POC_SLOTS["comms"]) if s != "chro")

        log.info("%s", capability_line())
        contacts = load_contacts()
        ledger = _load_ledger()
        changed = False
        unresolved: list[tuple[str, tuple]] = []
        for company in companies or []:
            company = (company or "").strip()
            if not company or company == "—":
                continue
            if stats["attempted"] >= max_resolutions:
                break
            slots = (tuple(slots_for(company)) if slots_for
                     else default_slots) or default_slots
            card = get_contact(contacts, company)
            if _has_fresh_owner(card, slots):
                continue
            tried_here = 0
            for slot in slots:
                if (stats["attempted"] >= max_resolutions
                        or tried_here >= MAX_SLOTS_PER_COMPANY):
                    break
                entry = card.get(slot) if card else None
                if (entry and getattr(entry, "name", "")
                        and entry.is_fresh()
                        and entry.meets_named_confidence()):
                    break   # this slot already names someone current
                if _recently_tried(ledger, company, slot):
                    continue
                stats["attempted"] += 1
                tried_here += 1
                ledger[f"{company.lower()}::{slot}"] = {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "resolved": False}
                try:
                    new_entry, record = resolver(company, slot, fetch=fetch)
                    try:
                        append_resolution_log(record)
                    except Exception:
                        pass
                except Exception as e:
                    log.info("bd poc resolve %s/%s failed (%s)",
                             company, slot, e)
                    continue
                if not new_entry or not new_entry.name:
                    continue
                # Attach a real /in/ profile when the source didn't
                # carry one — searched by the person's NAME at the
                # company (90-day cached, Bright Data free tier).
                if not new_entry.linkedin_url:
                    try:
                        prof = profile_resolver(company, new_entry.name)
                        if prof and prof.get("url"):
                            new_entry.linkedin_url = prof["url"]
                            stats["profile_links"] += 1
                    except Exception:
                        pass
                # Never overwrite a fresh, stronger entry (mirrors the
                # researcher's guard).
                existing = card.get(slot) if card else None
                if (existing and existing.name and existing.is_fresh()
                        and existing.confidence > new_entry.confidence
                        and existing.name.lower()
                        != new_entry.name.lower()):
                    continue
                card = upsert_contact(contacts, company, slot, new_entry)
                changed = True
                stats["resolved"] += 1
                ledger[f"{company.lower()}::{slot}"]["resolved"] = True
                if new_entry.meets_named_confidence():
                    break   # owner found — next company
            # Free chain done for this company: still no floor-clearing
            # owner? Queue it for the model-research layer below.
            if not _has_fresh_owner(get_contact(contacts, company), slots):
                unresolved.append((company, slots))
        if changed:
            save_contacts(contacts)
        _save_ledger(ledger)

        # ---- Layer 2: model + live web search for the leftovers ------
        # The roster-free path: any company the registries and team pages
        # couldn't name an owner at gets the same research engine a live
        # job gets. Budgeted; failure-aware TTL lives in the researcher's
        # own ledger; graceful no-op without ANTHROPIC_API_KEY.
        if unresolved and research_max > 0:
            try:
                from tool.contacts import job_researcher as _jr
                contacts = load_contacts()
                r_changed = False
                r_stats: dict = {"model_calls": 0}
                for company, slots in unresolved:
                    if r_stats["model_calls"] >= research_max:
                        break
                    ctx = ""
                    if context_for is not None:
                        try:
                            ctx = context_for(company) or ""
                        except Exception:
                            ctx = ""
                    if _jr.research_company_owner(
                            company, slots, contacts, desk=desk,
                            context=ctx, runner=research_runner,
                            stats=r_stats):
                        stats["research_resolved"] += 1
                        r_changed = True
                stats["researched"] = r_stats["model_calls"]
                if r_changed:
                    save_contacts(contacts)
            except Exception as e:
                log.info("bd poc research layer skipped (%s)", e)

        log.info("bd poc fill: %s", stats)
        return stats
    except Exception as e:
        log.info("bd poc fill skipped (%s)", e)
        return stats
