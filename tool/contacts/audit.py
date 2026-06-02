"""One-shot integrity audit for the contact-resolution subsystem.

Run it whenever you want the whole contact pipeline checked at once,
instead of eyeballing seven files:

    python -m tool.contacts.audit                 # audit the active desk
    python -m tool.contacts.audit --all-profiles  # comms AND marketing

Why this exists
---------------
Contact logic is spread across resolver.py, hiring_manager.py,
linkedin_resolver.py, auto_update.py, divisional_contacts.py, store.py and
dashboard.py — with multiple graph-writers, two runtime readers, several
confidence scales and two company-matching rules. Bugs hide in the *gaps
between* those files, not inside any one of them. This module turns each
known failure class into a mechanical check so regressions can't creep back
in silently.

Each check returns (name, status, detail):
  PASS  — behaves correctly
  WARN  — works but is a latent risk / missing capability
  FAIL  — a real, demonstrable bug
  ERROR — the check itself blew up (treat as needs-attention)

Exit code is non-zero if anything FAILs, so this can gate CI later.
"""
from __future__ import annotations

import os
import re
import sys
import subprocess
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOOL_DIR = REPO_ROOT / "tool"

PASS, WARN, FAIL, ERROR = "PASS", "WARN", "FAIL", "ERROR"


# --------------------------------------------------------------------------
# Check 1 — every resolution path runs and returns the documented shape
# --------------------------------------------------------------------------
def check_smoke_resolvers():
    """The three resolvers (seeder / jobs-signals / predictors) must run
    without network and return their documented shapes."""
    from tool.contacts.resolver import resolve
    from tool import hiring_manager as hm
    from tool import linkedin_resolver as lnr

    # Seeder resolver (no fetch => CH+RNS only, no network needed)
    entry, rec = resolve("Tesco", "cco", fetch=None)
    if not hasattr(rec, "outcome"):
        return ("smoke: seeder resolve", FAIL, "resolve() returned no ResolutionRecord")

    # Jobs/signals runtime resolver — across the kinds it special-cases
    needed = {"name", "title", "confidence", "basis", "linkedin_url", "slot"}
    for kind, title in (("job", "Internal Communications Manager"),
                        ("trade_press", "Acme appoints Jane Doe as CCO"),
                        ("rns", "Trading update")):
        c = hm.resolve_lead_contact({"company": "Tesco", "kind": kind,
                                     "title": title, "summary": "based in London"})
        missing = needed - set(c)
        if missing:
            return ("smoke: resolve_lead_contact", FAIL,
                    f"kind={kind} missing keys {missing}")
        if not (0.0 <= float(c["confidence"]) <= 1.0):
            return ("smoke: resolve_lead_contact", FAIL,
                    f"kind={kind} confidence out of [0,1]: {c['confidence']}")

    # Predictor runtime resolver
    pd = {"events": [{"trigger_key": "ceo_change", "company": "Tesco"}]}
    out = lnr.resolve_named_contact_for_predictor(pd)  # None is a valid result
    if out is not None and "confidence" not in out:
        return ("smoke: predictor resolve", FAIL, "predictor result missing confidence")

    return ("smoke: all three resolvers run", PASS,
            "seeder + jobs/signals(3 kinds) + predictor all return valid shapes")


# --------------------------------------------------------------------------
# Check 2 — role-slot vocabulary is consistent end-to-end
# --------------------------------------------------------------------------
def check_slot_consistency():
    """Any slot the routing/inference layer TARGETS must be (a) storable
    (schema.ROLE_SLOTS), (b) producible (resolver.ROLE_TITLE_PATTERNS, so
    classify_title can emit it) and (c) seeded (seeder.SEED_ROLE_SLOTS).
    A targeted-but-not-producible slot is a dead path — the contact graph
    can never name that role. This is exactly the marketing gap."""
    from tool.contacts.schema import ROLE_SLOTS
    from tool.contacts.resolver import ROLE_TITLE_PATTERNS
    from tool.contacts.seeder import SEED_ROLE_SLOTS
    from tool.contacts import routing
    from tool.profiles import active_profile

    targeted = set()
    for chain in routing.TRIGGER_ROLE_CHAIN.values():
        targeted.update(chain)
    targeted.update(routing.DEFAULT_CHAIN)
    targeted.update(routing.ROLE_SLOT_DISPLAY.keys())

    storable = set(ROLE_SLOTS)
    producible = set(ROLE_TITLE_PATTERNS.keys())
    seeded = set(SEED_ROLE_SLOTS)

    not_storable = sorted(targeted - storable)
    not_producible = sorted(targeted - producible)
    not_seeded = sorted(targeted - seeded)

    profile = active_profile().key
    detail = (f"[{profile}] targeted={len(targeted)} "
              f"not_storable={not_storable} "
              f"not_producible={not_producible} "
              f"not_seeded={not_seeded}")
    # not_producible is the hard failure: the resolver can never classify a
    # title into this slot, so the graph can never hold a verified person.
    if not_producible:
        return ("slot consistency", FAIL, detail)
    if not_storable or not_seeded:
        return ("slot consistency", WARN, detail)
    return ("slot consistency", PASS, detail)


# --------------------------------------------------------------------------
# Check 3 — company matching is consistent between writer and reader
# --------------------------------------------------------------------------
def check_company_match_consistency():
    """store.get_contact matches leniently (core-name), but auto_update
    matches by EXACT name. So a departure event for 'HSBC' fails to expire
    a card keyed 'HSBC Holdings plc' — the button keeps showing a departed
    person. This check proves it."""
    from tool.contacts.schema import ContactCard, ContactEntry
    from tool.contacts.store import get_contact
    from tool.contacts.auto_update import expire_departed_contacts

    fresh = datetime.now(timezone.utc).isoformat()
    card = ContactCard(company="HSBC Holdings plc")
    card.entries["ceo"] = ContactEntry(
        name="John Smith", role_title="Chief Executive Officer",
        role_slot="ceo", verified_at=fresh, confidence=0.88)
    contacts = {"HSBC Holdings plc": card}

    # Reader: lenient match should find the card under the short name.
    reader_ok = get_contact(contacts, "HSBC") is not None

    # Writer: a CH departure event for the short name should expire it.
    ev = SimpleNamespace(
        source_label="Companies House (historical)",
        trigger_key="ceo_change",
        company="HSBC",
        evidence="SMITH, John resigned as Chief Executive Officer at HSBC on 2026-01-05.",
    )
    expire_departed_contacts(contacts, [ev])
    entry = contacts["HSBC Holdings plc"].entries["ceo"]
    writer_expired = not entry.is_fresh()

    if reader_ok and not writer_expired:
        return ("company match: reader vs writer", FAIL,
                "reader (get_contact) matches 'HSBC'->'HSBC Holdings plc' but "
                "auto_update's exact match does NOT expire the departed CEO — "
                "stale wrong-name risk")
    if reader_ok and writer_expired:
        return ("company match: reader vs writer", PASS,
                "reader and writer agree on the company match")
    return ("company match: reader vs writer", WARN,
            f"reader_ok={reader_ok} writer_expired={writer_expired} (unexpected)")


# --------------------------------------------------------------------------
# Check 4 — the display gate uses confidence, not just freshness
# --------------------------------------------------------------------------
def check_confidence_gate():
    """A fresh-but-low-confidence entry should NOT be surfaced as a named
    contact. Today best_named_contact returns the first fresh entry
    regardless of confidence, so a weak guess displays as if verified."""
    from tool.contacts.schema import ContactEntry
    from tool.contacts.store import upsert_contact
    from tool import hiring_manager as hm

    fresh = datetime.now(timezone.utc).isoformat()
    contacts = {}
    upsert_contact(contacts, "TestCo", "cco", ContactEntry(
        name="Weak Guess", role_title="comms person", role_slot="cco",
        verified_at=fresh, confidence=0.15))

    nc = hm.best_named_contact("TestCo", ("cco",), contacts=contacts)
    if nc and nc.get("name") == "Weak Guess":
        return ("confidence gate", FAIL,
                "best_named_contact returned a fresh 0.15-confidence name as a "
                "verified contact — display is freshness-gated, not confidence-gated")
    return ("confidence gate", PASS, "low-confidence fresh entry was withheld")


# --------------------------------------------------------------------------
# Check 5 — freshness handles naive (tz-less) timestamps safely
# --------------------------------------------------------------------------
def check_freshness_tz():
    """is_fresh() must treat a recent NAIVE timestamp as fresh. If a
    tz-aware 'now' minus a naive 'verified_at' raises, is_fresh swallows it
    and returns False — a recently-seeded contact silently looks stale."""
    from tool.contacts.schema import ContactEntry
    naive_recent = (datetime.now() - timedelta(days=1)).isoformat()  # no tzinfo
    e = ContactEntry(name="x", role_title="y", role_slot="cco",
                     verified_at=naive_recent, confidence=0.9)
    try:
        fresh = e.is_fresh()
    except Exception as ex:
        return ("freshness tz-safety", FAIL,
                f"is_fresh() raised {type(ex).__name__} on a naive timestamp — "
                "schema.py's try/except wraps only fromisoformat, not the "
                "(now - v) subtraction, so any naive verified_at CRASHES the "
                "caller (resolve_lead_contact / best_named_contact)")
    if not fresh:
        return ("freshness tz-safety", WARN,
                "a 1-day-old NAIVE timestamp reads as STALE — any hand-edited / "
                "externally-written entry without tzinfo silently never shows")
    return ("freshness tz-safety", PASS, "naive recent timestamp handled as fresh")


# --------------------------------------------------------------------------
# Check 6 — inventory the confidence scales across graph-writers
# --------------------------------------------------------------------------
def check_confidence_scales():
    """Count the distinct confidence-assignment schemes. More than one
    scheme means the same person can carry different numbers depending on
    which path resolved them — so a single threshold can't be trusted."""
    writers = {}
    res = (TOOL_DIR / "contacts" / "resolver.py").read_text()
    res_consts = sorted(set(re.findall(r"confidence=([01]?\.\d+)", res)))
    writers["resolver.py (seeder sources)"] = res_consts

    au = (TOOL_DIR / "contacts" / "auto_update.py").read_text()
    writers["auto_update.py"] = sorted(set(re.findall(r"confidence=([01]?\.\d+)", au)))

    hm = (TOOL_DIR / "hiring_manager.py").read_text()
    blend = bool(re.search(r"base_conf\s*\*\s*0?\.\d+\s*\+", hm))
    writers["hiring_manager.py"] = ["linear-blend" if blend else "?"]

    lnr = (TOOL_DIR / "linkedin_resolver.py").read_text()
    raw = "entry.confidence" in lnr
    writers["linkedin_resolver.py (predictor)"] = ["raw entry.confidence" if raw else "?"]

    distinct_schemes = sum(1 for v in writers.values() if v and v != ["?"])
    detail = " | ".join(f"{k}={v}" for k, v in writers.items())
    if distinct_schemes > 1:
        return ("confidence scales", WARN,
                f"{distinct_schemes} different confidence schemes -> "
                f"not comparable on one threshold: {detail}")
    return ("confidence scales", PASS, detail)


# --------------------------------------------------------------------------
# Check 7 — positive-feedback capture exists (needed to MEASURE accuracy)
# --------------------------------------------------------------------------
def check_positive_feedback_capture():
    """The §4.5 success metric is '% marked correct person'. We can only
    measure 80% if there's a positive-signal capture. Today only the
    negative 'wrong person' flag exists."""
    dash = (TOOL_DIR / "dashboard.py").read_text().lower()
    has_negative = "contacts/flag" in dash or "contact_flags" in dash
    has_positive = any(tok in dash for tok in (
        "contacts/correct", "contacts/responded", "mark_correct",
        "correct_person", "contact_responded"))
    if has_negative and not has_positive:
        return ("feedback capture", WARN,
                "only the negative 'wrong person' flag exists — no 'correct/"
                "responded' capture, so the 80% metric has no data source yet")
    if has_positive:
        return ("feedback capture", PASS, "positive-feedback capture present")
    return ("feedback capture", WARN, "no contact feedback capture found at all")


# --------------------------------------------------------------------------
# Check 8 — every button surface routes through the central resolvers
# --------------------------------------------------------------------------
def check_surface_inheritance():
    """Files that render a LinkedIn contact button must CALL the central
    resolvers, not reimplement contact logic. If a surface stops calling
    them, a central gate fix won't reach it."""
    central = ("resolve_lead_contact", "resolve_named_contact",
               "best_named_contact", "linkedin_click",
               "linkedin_search_for_predictor")
    surfaces = ["morning_brief.py", "sweep.py", "predictor_pipeline.py",
                "dashboard.py"]
    offenders = []
    for fname in surfaces:
        txt = (TOOL_DIR / fname).read_text()
        renders_button = ("linkedin" in txt.lower()
                          or "_people_search" in txt)
        calls_central = any(c in txt for c in central)
        if renders_button and not calls_central:
            offenders.append(fname)
    if offenders:
        return ("surface inheritance", FAIL,
                f"these render contact buttons but bypass the central "
                f"resolvers: {offenders}")
    return ("surface inheritance", PASS,
            f"all {len(surfaces)} surfaces route through the central resolvers")


# --------------------------------------------------------------------------
# Check 9 — region disambiguation feasibility per lead type
# --------------------------------------------------------------------------
def check_region_signal_availability():
    """'KPMG UK not International' needs a region signal on the lead. Job
    leads carry a location; predictor Stacks carry only `company`. Flag the
    asymmetry so region-aware keying isn't promised for BD leads."""
    jobs = (TOOL_DIR / "sources" / "jobs.py").read_text()
    job_has_loc = bool(re.search(r"\bloc(ation)?\b", jobs))
    stacker = (TOOL_DIR / "predictive" / "stacker.py").read_text()
    stack_has_loc = bool(re.search(r"region|country|location", stacker))
    if job_has_loc and not stack_has_loc:
        return ("region signal availability", WARN,
                "job leads carry a location but predictor Stacks carry only "
                "`company` — region-aware entity keying is feasible for JOB "
                "leads only, not BD/predictor leads")
    return ("region signal availability", PASS,
            f"job_has_loc={job_has_loc} stack_has_loc={stack_has_loc}")


CHECKS = [
    check_smoke_resolvers,
    check_slot_consistency,
    check_company_match_consistency,
    check_confidence_gate,
    check_freshness_tz,
    check_confidence_scales,
    check_positive_feedback_capture,
    check_surface_inheritance,
    check_region_signal_availability,
]


def _run_once() -> int:
    from tool.profiles import active_profile
    profile = active_profile().key
    print(f"\n{'='*72}\nCONTACT SUBSYSTEM AUDIT — desk: {profile}\n{'='*72}")
    rows = []
    for fn in CHECKS:
        try:
            name, status, detail = fn()
        except Exception:
            name, status, detail = fn.__name__, ERROR, traceback.format_exc().strip().splitlines()[-1]
        rows.append((name, status, detail))

    width = max(len(n) for n, _, _ in rows)
    icon = {PASS: "✓", WARN: "▲", FAIL: "✗", ERROR: "!"}
    for name, status, detail in rows:
        print(f"  {icon[status]} {status:5} {name:<{width}}  {detail}")

    counts = {s: sum(1 for _, st, _ in rows if st == s) for s in (PASS, WARN, FAIL, ERROR)}
    print(f"\n  {counts[PASS]} pass · {counts[WARN]} warn · {counts[FAIL]} fail · {counts[ERROR]} error")
    return 1 if (counts[FAIL] or counts[ERROR]) else 0


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--all-profiles" in argv:
        rc = 0
        for desk in ("comms", "marketing"):
            env = dict(os.environ, VMA_PROFILE=desk, PYTHONPATH=str(REPO_ROOT))
            r = subprocess.run([sys.executable, "-m", "tool.contacts.audit"],
                               env=env, cwd=str(REPO_ROOT))
            rc = rc or r.returncode
        return rc
    return _run_once()


if __name__ == "__main__":
    sys.exit(main())
