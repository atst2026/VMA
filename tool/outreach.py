"""SEND OUTREACH — drafting, gating, and the guarded send itself.

The flow this module owns, end to end:

  nightly (GitHub Action, via enrich_signals):
    per live job  -> research the hiring contact (job_researcher)
                  -> resolve a sendable work email (email_resolver)
                  -> write a personalised draft onto the signal
                     (outreach_ai; the dashboard falls back to the
                      fixed template when the model pass didn't run)

  on click (Render dashboard, via send_outreach):
    re-derive every gate server-side -> reroute to the test inbox in
    test mode -> send via the configured mailbox -> append-only log
    -> the lead flips to followed-up.

GATES (all must pass before a live send; the dashboard shows WHY a
lead isn't sendable rather than hiding the button):
  - a NAMED contact at/above MIN_NAMED_CONFIDENCE, not stale;
  - an address whose status is in EMAIL_SENDABLE_STATUSES (verified or
    published-with-URL; pattern guesses are never one-click sendable);
  - not on the suppression list (opt-outs live forever);
  - not already sent live for this lead, nor to this address for this
    company in the last RESEND_COOL_OFF_DAYS.

TEST MODE is the default: until OUTREACH_TEST_MODE=0, every send is
rerouted to the profile's test inbox with the would-be recipient
stamped on it — the same test-first pattern as the brief itself.

Compliance: every message identifies VMA Group and carries a reply-to-
opt-out line; "no thanks" replies are added to the suppression list by
hand (one click in the modal) and checked before every send. PECR's
corporate-subscriber rule is what makes UK B2B outreach workable, but
sole traders / certain partnerships count as individuals — gate on
entity type during list-building, not here.
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from tool.contacts.schema import (EMAIL_SENDABLE_STATUSES,
                                  MIN_NAMED_CONFIDENCE)
from tool.profiles import active_profile
from tool.state_paths import state_dir

log = logging.getLogger("brief.outreach")

MODEL = "claude-opus-4-8"
MAX_DRAFTS_PER_RUN = 30
RESEND_COOL_OFF_DAYS = 30


# ---- Mode + identity ----------------------------------------------------
def test_mode() -> bool:
    """ON unless explicitly disabled. A misspelt env var must fail safe
    (reroute to the test inbox), never fail live."""
    val = (os.environ.get("OUTREACH_TEST_MODE") or "").strip().lower()
    return val not in ("0", "false", "no", "off")


def sender_name() -> str:
    env = (os.environ.get("OUTREACH_FROM_NAME") or "").strip()
    if env:
        return env
    return ("VMA Group Marketing Desk"
            if active_profile().key == "marketing" else "Sara Tehrani")


def test_recipient() -> str:
    from tool import config
    return config.TEST_RECIPIENT


# ---- Suppression list ---------------------------------------------------
def _suppression_file():
    return state_dir() / "outreach_suppression.json"


def _load_suppression() -> dict:
    try:
        f = _suppression_file()
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def suppress(value: str, reason: str = "") -> bool:
    """Add an email (or a whole domain) to the do-not-contact list.
    Permanent until hand-edited — opt-outs don't expire."""
    value = (value or "").strip().lower()
    if not value or ("@" not in value and "." not in value):
        return False
    d = _load_suppression()
    d[value] = {"at": datetime.now(timezone.utc).isoformat(),
                "reason": (reason or "").strip()[:200]}
    try:
        _suppression_file().write_text(json.dumps(d, indent=1))
        log.info("outreach suppression added: %s (%s)", value, reason)
        return True
    except Exception as e:
        log.info("outreach suppression write failed (%s)", e)
        return False


def is_suppressed(email: str) -> bool:
    email = (email or "").strip().lower()
    if not email:
        return False
    d = _load_suppression()
    if email in d:
        return True
    domain = email.partition("@")[2]
    return bool(domain) and domain in d


# ---- Send log (append-only) ----------------------------------------------
def _log_file():
    return state_dir() / "outreach_log.jsonl"


def _append_log(rec: dict) -> None:
    try:
        with _log_file().open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.info("outreach log write failed (%s)", e)


def _iter_log():
    f = _log_file()
    if not f.exists():
        return
    with f.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def already_sent_live(lead_id: str = "", email: str = "",
                      company: str = "") -> str:
    """'' if clear to send, else a short human reason. Only LIVE sends
    block; test sends may repeat freely (that's what testing is)."""
    email = (email or "").strip().lower()
    company_n = (company or "").strip().lower()
    cutoff = datetime.now(timezone.utc) - timedelta(days=RESEND_COOL_OFF_DAYS)
    for rec in _iter_log() or []:
        if rec.get("mode") != "live" or not rec.get("ok"):
            continue
        if lead_id and rec.get("lead_id") == lead_id:
            return "already sent for this lead"
        if email and (rec.get("to_email") or "").lower() == email \
                and (rec.get("company") or "").strip().lower() == company_n:
            try:
                at = datetime.fromisoformat(rec.get("at") or "")
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                if at > cutoff:
                    return (f"already emailed within "
                            f"{RESEND_COOL_OFF_DAYS} days")
            except Exception:
                return f"already emailed within {RESEND_COOL_OFF_DAYS} days"
    return ""


# ---- Drafting (canonical home of the AD-approved copy) -------------------
# Moved here from tool/dashboard.py so the GitHub Action (no Flask
# installed) can build drafts; the dashboard imports these back.
def _default_outreach() -> str:
    """Default predictor-outreach copy for the active desk (per request)."""
    if active_profile().key == "marketing":
        return (
            "Hi (Name), I'm (Your name) from VMA Group.\n\n"
            "We specialise in executive search and recruitment across marketing, "
            "brand and growth leadership. I'd love to grab a coffee in the next "
            "couple of weeks to introduce VMA Group and share what we're seeing "
            "in the market. I've attached our brochure in case it's useful.\n\n"
            "Would be great to connect.\n\n"
            "Best,\n"
            "(Your name)"
        )
    return (
        "Hi (Name), I'm Sara from VMA Group.\n\n"
        "We specialise in executive search and recruitment across corporate "
        "communications, internal comms and marketing. I'd love to grab a "
        "coffee in the next couple of weeks to introduce VMA Group and share "
        "what we're seeing in the market. I've attached our brochure in case "
        "it's useful.\n\n"
        "Would be great to connect.\n\n"
        "Best,\n"
        "Sara"
    )


def _display_role(title: str) -> str:
    """Strip an embedded company / region suffix off a scraped job title
    so it reads naturally inside a sentence, keeping original casing."""
    t = (title or "").strip()
    t = re.split(r"\s+[–—-]\s+", t)[0]
    t = re.split(r"\s+at\s+[A-Z]", t)[0]
    return t.split(",")[0].strip()


def draft_outreach_for_lead(signal: dict, contact: dict | None = None) -> str:
    """Outreach draft for a lead — fixed template with the contact's
    first name, the advertised role, and the company filled in."""
    c = contact if contact is not None else (signal.get("contact") or {})
    company = (signal.get("company") or "").strip() or "[Company]"
    name = (c.get("name") or "").strip()
    first = name.split()[0] if name else ""
    role = _display_role(signal.get("title") or "")
    role_phrase = f"the {role}" if role else "the role you've advertised"
    return (
        f"Hi {first or '[Name]'},\n\n"
        f"I noticed your recent ad for {role_phrase} and thought it might "
        f"be worth reaching out. We work with companies like {company} to "
        "support with talent solutions across communications, marketing, "
        "digital, sales and change.\n\n"
        "I'll attach our corporate brochure which includes some more "
        "information. If you're open for a quick conversation, I'd love to "
        "hear some more about the role and what you're looking for to see "
        "if there's any way we could add value.\n\n"
        "Best,\n"
        "[Your name]"
    )


_DRAFT_SYSTEM = (
    "You write first-touch business-development emails for {sender}, a "
    "consultant at VMA Group, a UK executive search firm for senior "
    "communications and marketing roles. You are given one live job "
    "advert and the hiring contact it will be sent to, plus the firm's "
    "approved template for tone.\n\n"
    "Rules:\n"
    "- 80–130 words, UK English, plain text only, no subject line.\n"
    "- Warm and specific, never salesy; one clear, low-stakes ask "
    "(a short conversation about the role).\n"
    "- Personalise ONLY from the facts provided (role title, company, "
    "where it's advertised, JD extract, contact's name and title). "
    "NEVER invent facts, names, numbers, claims about the company, or "
    "any relationship that isn't in the input.\n"
    "- Address the contact by first name if provided, otherwise open "
    "with 'Hi there'. No placeholder brackets of any kind.\n"
    "- Do not mention how the contact was identified or researched.\n"
    "- Sign off as {sender}."
)


def ai_draft(signal: dict, contact: dict | None = None) -> str | None:
    """One personalised draft for one lead, or None (no key / weak
    output). The fixed template stays the fallback everywhere."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return None
    c = contact if contact is not None else (signal.get("contact") or {})
    summary = re.sub(r"\s+", " ", (signal.get("summary") or "")).strip()
    facts = {
        "advertised_role": _display_role(signal.get("title") or ""),
        "company": (signal.get("company") or "").strip(),
        "advertised_on": (signal.get("source") or "").strip(),
        "jd_extract": summary[:900],
        "contact_first_name": ((c.get("name") or "").split() or [""])[0],
        "contact_title": (c.get("title") or "").strip(),
    }
    user = (
        "APPROVED TEMPLATE (tone reference — do not copy verbatim):\n"
        + draft_outreach_for_lead(signal, contact=c)
        + "\n\nFACTS (the only permissible personalisation):\n"
        + json.dumps(facts, indent=2, ensure_ascii=False)
        + "\n\nWrite the email body now."
    )
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=800,
            system=_DRAFT_SYSTEM.format(sender=sender_name()),
            messages=[{"role": "user", "content": user}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        text = text.strip().strip("`").strip()
    except Exception as e:
        log.info("outreach draft model call failed (%s)", e)
        return None
    # A draft with leftover placeholders or silly length is worse than
    # the fixed template — reject and fall back.
    if not text or len(text) > 1400 or "[" in text or "{" in text:
        return None
    return text


# ---- The nightly enrichment pass -----------------------------------------
def enrich_signals(signals: list[dict], research_runner=None,
                   draft_max: int = MAX_DRAFTS_PER_RUN) -> dict:
    """Run in the brief AFTER ranking, BEFORE latest_signals.json is
    written: research contacts for the day's job leads, resolve their
    emails, attach personalised drafts (signal['outreach_ai']). Every
    stage degrades gracefully; returns counters for the log."""
    stats = {"research_changes": 0, "email_changes": 0, "drafts": 0}
    try:
        from tool.contacts.store import (load_contacts, save_contacts,
                                         get_contact)
        from tool.contacts import email_resolver, job_researcher
        from tool.hiring_manager import (is_job_like, manager_for_signal,
                                         resolve_lead_contact)

        contacts = load_contacts()
        email_resolver.reset_budget()
        stats["research_changes"] = job_researcher.research_signals(
            signals or [], contacts, runner=research_runner)

        # Email pass: any named, fresh, confident entry the day's job
        # leads point at, still missing a sendable address.
        for s in signals or []:
            if not isinstance(s, dict) or not is_job_like(s):
                continue
            company = (s.get("company") or "").strip()
            if not company:
                continue
            card = get_contact(contacts, company)
            if not card:
                continue
            for slot in (manager_for_signal(s).get("slots") or ()):
                e = card.get(slot)
                if (e and e.name and e.is_fresh()
                        and e.meets_named_confidence()
                        and not e.email_is_sendable()):
                    if email_resolver.resolve_email(company, e):
                        stats["email_changes"] += 1
                    break

        if stats["research_changes"] or stats["email_changes"]:
            save_contacts(contacts)

        # Drafts, in ranked order, against the refreshed contact graph.
        for s in signals or []:
            if stats["drafts"] >= draft_max:
                break
            if not isinstance(s, dict) or not is_job_like(s):
                continue
            if not (s.get("company") or "").strip():
                continue
            contact = resolve_lead_contact(s, contacts=contacts)
            draft = ai_draft(s, contact)
            if draft:
                s["outreach_ai"] = draft
                stats["drafts"] += 1
        log.info("outreach enrichment: %s", stats)
        return stats
    except Exception as e:
        log.info("outreach enrichment skipped (%s)", e)
        return stats


# ---- The send itself ------------------------------------------------------
def sendable_state(contact: dict) -> tuple[bool, str]:
    """(ok, reason-if-not). The single gate definition the dashboard
    renders and the send endpoint enforces — never two opinions."""
    c = contact or {}
    if not (c.get("name") or "").strip():
        return False, "no named contact yet — research pending"
    if c.get("stale"):
        return False, "contact needs re-verifying (gone stale)"
    if float(c.get("confidence") or 0) < MIN_NAMED_CONFIDENCE:
        return False, "contact confidence below the send floor"
    email = (c.get("email") or "").strip()
    if not email:
        return False, "no work email found yet"
    if (c.get("email_status") or "") not in EMAIL_SENDABLE_STATUSES:
        return False, "email is an unverified pattern guess"
    if is_suppressed(email):
        return False, "contact opted out (suppression list)"
    return True, ""


def send_outreach(lead: dict, body: str = "", subject: str = "") -> dict:
    """The guarded send. `lead` must be the SERVER's enriched lead (the
    endpoint re-loads it by id — recipient identity is never taken from
    the client; only the body/subject text is editable)."""
    contact = (lead or {}).get("contact") or {}
    lead_id = (lead or {}).get("lead_id") or ""
    company = ((lead or {}).get("company") or "").strip()
    if not lead_id:
        return {"ok": False, "detail": "unknown lead"}
    ok, reason = sendable_state(contact)
    if not ok:
        return {"ok": False, "detail": reason}

    email = contact["email"].strip()
    mode = "test" if test_mode() else "live"
    if mode == "live":
        blocked = already_sent_live(lead_id=lead_id, email=email,
                                    company=company)
        if blocked:
            return {"ok": False, "detail": blocked}

    role = _display_role((lead or {}).get("title") or "")
    subject = (subject or "").strip()[:160] \
        or (f"Your {role} search — VMA Group" if role
            else "Your search — VMA Group")
    body = (body or "").strip()[:6000] \
        or (lead.get("outreach") or "").strip()
    if not body:
        return {"ok": False, "detail": "empty message body"}

    footer = (
        "\n\n—\n"
        f"{sender_name()} · VMA Group — executive search for senior "
        "communications & marketing\n"
        f"You're receiving this one-off note at your work address because "
        f"of {company or 'your company'}'s advertised role. If you'd "
        "rather not hear from us, reply \"no thanks\" and we won't "
        "contact you again."
    )
    text = body + footer

    to = email
    if mode == "test":
        to = test_recipient()
        subject = f"[TEST → {contact.get('name')} <{email}>] {subject}"
        text = (f"— TEST MODE: live mode would send this to "
                f"{contact.get('name')} <{email}> —\n\n") + text

    html = "<html><body><p>" + _html.escape(text).replace(
        "\n\n", "</p><p>").replace("\n", "<br>") + "</p></body></html>"

    from tool import email_send
    result = email_send.send(to, subject, html, text,
                             from_name=sender_name())
    rec = {
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "lead_id": lead_id,
        "company": company,
        "role": role,
        "to_name": contact.get("name") or "",
        "to_email": email,
        "rerouted_to": to if mode == "test" else "",
        "subject": subject,
        "email_status": contact.get("email_status") or "",
        "confidence": contact.get("confidence") or 0,
        "ok": bool(result.get("ok")),
        "provider": result.get("provider") or "",
        "detail": str(result.get("detail") or "")[:300],
    }
    _append_log(rec)
    log.info("outreach send %s: %s -> %s (%s)", mode, lead_id,
             to, "ok" if rec["ok"] else rec["detail"])
    return {"ok": rec["ok"], "mode": mode, "to": to,
            "to_email": email, "provider": rec["provider"],
            "detail": rec["detail"]}
