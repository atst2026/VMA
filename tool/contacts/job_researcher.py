"""Per-job hiring-contact research — the accuracy core of SEND OUTREACH.

The deterministic resolver (tool/contacts/resolver.py) fills role slots
from registries and leadership pages on a schedule. A live job ad is a
sharper question: WHO, today, owns THIS hire at THIS employer? This
module asks the model that question with live web search, one vacancy
at a time, and only writes an answer it can defend:

  - right entity  — the JD's own entity hints are in the brief, and the
                    model must say which legal/regional entity it
                    matched (KPMG UK, not KPMG International);
  - right seat    — the reporting line extracted from the JD (or the
                    seniority-up inference) is the starting hypothesis,
                    and the model searches the TITLE FAMILY, not one
                    exact string;
  - right person  — evidence must be dated; the newest sighting drives
                    acceptance, and the model is told to actively check
                    the person hasn't LEFT (departure/appointment news
                    outranks a stale team page);
  - honest score  — the model returns a calibrated confidence; we cap
                    what we store and never store below ACCEPT_CONFIDENCE.

Answers land in the SAME contacts store every other source writes to
(tool/contacts/store), so freshness windows, Sara's wrong-contact flags,
the feedback metric and the re-verify queue all apply unchanged. A
published email found on the way is handed to email_resolver's rules,
not trusted blindly.

Budget: MAX_JOBS_PER_RUN model passes per run; a (company, slot) pair
researched in the last RESEARCH_TTL_DAYS is never re-spent. Graceful
no-op without ANTHROPIC_API_KEY. Never raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from tool.contacts.schema import ROLE_SLOTS
from tool.state_paths import state_dir

log = logging.getLogger("brief.contacts.research")

MODEL = "claude-opus-4-8"
MAX_JOBS_PER_RUN = 10
MAX_CONTINUATIONS = 6
RESEARCH_TTL_DAYS = 7
# Below this the answer is treated as "didn't find them" and the lead
# stays on the role-search fallback — same philosophy as the resolver's
# verified-or-fallback tiers.
ACCEPT_CONFIDENCE = 0.7
# Evidence older than this can't carry an acceptance on its own.
MAX_EVIDENCE_AGE_DAYS = 365
# A model answer never outranks a registry attestation in the store.
STORE_CONFIDENCE_CAP = 0.88

_SYSTEM = (
    "You are the hiring-contact researcher for a UK senior communications "
    "& marketing recruitment desk. You receive ONE live job vacancy and a "
    "hypothesis about which seat owns the hire. Identify the named person "
    "CURRENTLY in that seat at that employer, using live web research.\n\n"
    "Method (web_search and web_fetch; free public sources only):\n"
    "1. ENTITY: pin down which legal/regional entity is hiring (the job "
    "ad's location, entity names in the JD, the careers-page domain). A "
    "group-level leader at the wrong entity is a WRONG answer.\n"
    "2. SEAT: search the seat's TITLE FAMILY, never one exact title — "
    "Head of Corporate Affairs / Comms Director / VP Communications are "
    "the same seat wearing different badges. If the JD names a reporting "
    "line, that is the seat.\n"
    "3. PERSON: find who holds the seat NOW. Prefer primary, dated "
    "sources: the company's own leadership/press pages, RNS/press "
    "releases, trade press (PRWeek, Campaign, PRmoment), LinkedIn public "
    "pages. Note the DATE of each piece of evidence.\n"
    "4. STILL THERE: actively search for the person leaving (\"<name> "
    "departs|joins|appointed\"). A departure or a newer appointment to "
    "the same seat overrides everything older.\n"
    "5. EMAIL: report a work email ONLY if you saw it printed verbatim "
    "in a public source, with that source's URL. NEVER infer or "
    "construct an address.\n\n"
    "Confidence is calibrated, not optimistic: 0.9 = current primary "
    "source names them in the seat this quarter; 0.7 = solid but the "
    "newest evidence is months old; below 0.5 = you are guessing — say "
    "found=false instead. An honest \"not found\" is worth more than a "
    "name that left last year."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "name": {"type": "string"},
        "role_title": {"type": "string"},
        "role_slot": {"type": "string", "enum": list(ROLE_SLOTS) + ["other"]},
        "entity_note": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "date": {"type": "string"},
                    "what": {"type": "string"},
                },
                "required": ["url", "what"],
            },
        },
        "newest_evidence_date": {"type": "string"},
        "linkedin_url": {"type": "string"},
        "published_email": {"type": "string"},
        "published_email_url": {"type": "string"},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
    },
    "required": ["found", "confidence"],
}


def _ledger_file():
    return state_dir() / "job_contact_research.json"


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


def _ledger_key(company: str, slot: str) -> str:
    return f"{(company or '').strip().lower()}::{slot}"


def _recently_researched(ledger: dict, company: str, slot: str) -> bool:
    at = (ledger.get(_ledger_key(company, slot)) or {}).get("at") or ""
    try:
        t = datetime.fromisoformat(at)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t) < timedelta(
            days=RESEARCH_TTL_DAYS)
    except Exception:
        return False


def _brief_for(signal: dict, inference: dict) -> str:
    """The single-vacancy brief the model researches from."""
    summary = re.sub(r"\s+", " ", (signal.get("summary") or "")).strip()
    lines = [
        "VACANCY",
        f"  Advertised title: {signal.get('title') or ''}",
        f"  Company (as scraped): {signal.get('company') or ''}",
        f"  Source: {signal.get('source') or ''}",
        f"  Ad URL: {signal.get('url') or ''}",
        f"  Geography hint: {signal.get('geo') or ''}",
        f"  JD extract: {summary[:1200] or '(none)'}",
        "",
        "SEAT HYPOTHESIS (from the JD's reporting line or seniority-up "
        "inference — verify, don't assume)",
        f"  Seat: {inference.get('manager_title') or ''}",
        f"  Basis: {inference.get('basis') or ''}",
        f"  Acceptable role slots: "
        f"{', '.join(inference.get('slots') or ())}",
        "",
        "Who currently holds this seat at this employer?",
    ]
    return "\n".join(lines)


def _run_model(brief: str) -> dict | None:
    """One research pass: server-side web search/fetch loop, structured
    answer. Isolated so tests inject a stub."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        tools = [{"type": "web_search_20260209", "name": "web_search"},
                 {"type": "web_fetch_20260209", "name": "web_fetch"}]
        messages = [{"role": "user", "content": brief}]
        resp = None
        for _ in range(MAX_CONTINUATIONS):
            resp = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=_SYSTEM,
                tools=tools,
                messages=messages,
                output_config={"format": {"type": "json_schema",
                                          "schema": _SCHEMA}},
            )
            if resp.stop_reason != "pause_turn":
                break
            messages = [{"role": "user", "content": brief},
                        {"role": "assistant", "content": resp.content}]
        if resp is None or resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text) if text else None
    except Exception as e:
        log.info("contact research model call failed: %s", e)
        return None


def _evidence_fresh_enough(data: dict) -> bool:
    raw = (data.get("newest_evidence_date") or "").strip()
    if not raw:
        return False
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d) < timedelta(
            days=MAX_EVIDENCE_AGE_DAYS)
    except Exception:
        return False


def _acceptable(data: dict, slots: tuple) -> bool:
    if not isinstance(data, dict) or not data.get("found"):
        return False
    if not (data.get("name") or "").strip():
        return False
    if float(data.get("confidence") or 0) < ACCEPT_CONFIDENCE:
        return False
    if not _evidence_fresh_enough(data):
        return False
    # The slot must be a real roster slot, but NOT necessarily one of
    # the hypothesis slots — research legitimately discovers that e.g.
    # comms reports into HR at this company. The hypothesis guides the
    # search; the evidence decides the answer.
    return (data.get("role_slot") or "") in ROLE_SLOTS


def research_job_contact(signal: dict, contacts: dict, runner=None,
                         ledger: dict | None = None,
                         stats: dict | None = None) -> bool:
    """Research ONE vacancy's hiring contact and upsert any defensible
    answer into the shared contacts store (caller saves). Returns True
    if the store changed. `stats["model_calls"]` is incremented when a
    model pass is actually spent (skips are free). Never raises."""
    try:
        from tool.hiring_manager import manager_for_signal
        from tool.contacts.store import upsert_contact, get_contact
        from tool.contacts.schema import ContactEntry

        company = (signal.get("company") or "").strip()
        if not company:
            return False
        inference = manager_for_signal(signal)
        slots = tuple(inference.get("slots") or ())
        primary_slot = slots[0] if slots else "head_of_comms"

        own_ledger = ledger is None
        if own_ledger:
            ledger = _load_ledger()
        if _recently_researched(ledger, company, primary_slot):
            return False

        # Already holding a fresh, named, sendable answer? Don't spend.
        card = get_contact(contacts, company)
        if card:
            for s in slots:
                e = card.get(s)
                if (e and e.name and e.is_fresh()
                        and e.meets_named_confidence()
                        and e.email_is_sendable()):
                    return False

        if stats is not None:
            stats["model_calls"] = stats.get("model_calls", 0) + 1
        data = (runner or _run_model)(_brief_for(signal, inference))
        ledger[_ledger_key(company, primary_slot)] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "found": bool(isinstance(data, dict) and data.get("found")),
        }
        if own_ledger:
            _save_ledger(ledger)
        if data is None:
            return False
        if not _acceptable(data, slots):
            log.info("contact research %s: no defensible answer "
                     "(found=%s conf=%s)", company,
                     (data or {}).get("found"),
                     (data or {}).get("confidence"))
            return False

        slot = data["role_slot"]
        evidence = [e for e in (data.get("evidence") or [])
                    if isinstance(e, dict) and e.get("url")]
        src_url = evidence[0]["url"] if evidence else ""
        now = datetime.now(timezone.utc).isoformat()
        entry = ContactEntry(
            name=data["name"].strip(),
            role_title=(data.get("role_title") or "").strip()
                       or inference.get("manager_title") or "",
            role_slot=slot,
            linkedin_url=(data.get("linkedin_url") or "").strip() or None,
            source_url=src_url,
            source_label="AI web research (live job)",
            verified_at=now,
            confidence=round(min(float(data.get("confidence") or 0),
                                 STORE_CONFIDENCE_CAP), 2),
        )
        # A published email travels under the published-source rules —
        # URL required; verification (if configured) can upgrade/kill it.
        pub_email = (data.get("published_email") or "").strip()
        pub_url = (data.get("published_email_url") or "").strip()
        if pub_email and pub_url and "@" in pub_email:
            from tool.contacts import email_resolver
            verdict = email_resolver.hunter_verify(pub_email)
            if verdict != "invalid":
                entry.email = pub_email
                entry.email_status = ("verified" if verdict == "valid"
                                      else "published")
                entry.email_source = "ai_web_research"
                entry.email_source_url = pub_url
                entry.email_checked_at = now

        # Never silently overwrite a FRESH higher-confidence entry from
        # a stronger source (registry attestations sit above the cap).
        existing = card.get(slot) if card else None
        if (existing and existing.name and existing.is_fresh()
                and existing.confidence > entry.confidence
                and existing.name.lower() != entry.name.lower()):
            log.info("contact research %s/%s: kept existing %s "
                     "(%.2f) over researched %s (%.2f)", company, slot,
                     existing.name, existing.confidence, entry.name,
                     entry.confidence)
            return False
        if (existing and existing.name
                and existing.name.lower() == entry.name.lower()):
            # Same person re-confirmed: refresh the clock, keep the
            # email fields if the research pass didn't bring better.
            if not entry.email and existing.email:
                entry.email = existing.email
                entry.email_status = existing.email_status
                entry.email_source = existing.email_source
                entry.email_source_url = existing.email_source_url
                entry.email_checked_at = existing.email_checked_at
            entry.confidence = round(max(entry.confidence,
                                         existing.confidence), 2)

        upsert_contact(contacts, company, slot, entry)
        log.info("contact research %s: %s — %s (slot %s, conf %.2f%s)",
                 company, entry.name, entry.role_title, slot,
                 entry.confidence,
                 ", email " + entry.email_status if entry.email else "")
        return True
    except Exception as e:
        log.info("contact research skipped (%s)", e)
        return False


def research_signals(signals: list[dict], contacts: dict,
                     runner=None, max_jobs: int = MAX_JOBS_PER_RUN) -> int:
    """Budgeted pass over the day's job-like leads (ranked order = spend
    priority). Mutates `contacts`; caller saves. Returns changes."""
    try:
        from tool.hiring_manager import is_job_like
        ledger = _load_ledger()
        stats: dict = {"model_calls": 0}
        changed = 0
        for s in signals or []:
            if stats["model_calls"] >= max_jobs:
                break
            if not isinstance(s, dict) or not is_job_like(s):
                continue
            if not (s.get("company") or "").strip():
                continue
            if research_job_contact(s, contacts, runner=runner,
                                    ledger=ledger, stats=stats):
                changed += 1
        _save_ledger(ledger)
        log.info("contact research: %d model passes, %d store changes",
                 stats["model_calls"], changed)
        return changed
    except Exception as e:
        log.info("contact research pass skipped (%s)", e)
        return 0
