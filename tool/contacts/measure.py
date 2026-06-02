"""Measure the lead-contact button — the real number, in one command.

    python -m tool.contacts.measure                 # active desk
    python -m tool.contacts.measure --all-profiles   # comms AND marketing

Two things, both for the ACTIVE desk's graph:

1. Success metric (§4.5) — '% correct person' over Sara's captured
   feedback (contact_feedback). Empty until feedback accrues; reports the
   50-label volume floor honestly.

2. Coverage baseline (runnable today) — over every company in the contact
   graph, does the button surface a NAMED contact (vs a role-search), and
   is it the curated name? This measures the plumbing + reach of the named
   tier right now, without needing live feedback or network. It is NOT an
   external-accuracy figure (the curated seed is treated as ground truth);
   the true accuracy number only comes from (1) as feedback accrues.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _measure_once() -> int:
    from tool.profiles import active_profile
    from tool.contacts.store import load_contacts
    from tool.contacts.schema import MIN_NAMED_CONFIDENCE
    from tool import contact_feedback, hiring_manager as hm

    desk = active_profile().key
    print(f"\n{'='*72}\nLEAD-CONTACT MEASUREMENT — desk: {desk}\n{'='*72}")

    # --- 1. §4.5 success metric over captured feedback -----------------
    m = contact_feedback.accuracy_metric()
    rate = "n/a" if m["rate"] is None else f"{m['rate']*100:.0f}%"
    floor = "met" if m["meets_floor"] else f"NOT met (need {contact_feedback.MIN_VOLUME_FLOOR})"
    print("\n  §4.5 success metric (Sara's feedback)")
    print(f"    correct-person rate : {rate}")
    print(f"    labelled contacts   : {m['labelled']} (correct {m['correct']}, "
          f"wrong {m['incorrect']}, moved {m['moved']})")
    print(f"    volume floor        : {floor}")

    # --- 2. Coverage baseline over the graph ---------------------------
    contacts = load_contacts()
    companies = list(contacts)
    total_entries = sum(len(c.entries) for c in contacts.values())
    fresh = sum(1 for c in contacts.values() for e in c.entries.values()
                if e.is_fresh())
    named_grade = sum(1 for c in contacts.values() for e in c.entries.values()
                      if e.is_fresh() and e.meets_named_confidence())

    # Does the runtime button surface a NAMED contact per company? Simulate
    # the desk's bread-and-butter job lead at each seeded company.
    title = "Head of Marketing" if desk == "marketing" else "Communications Manager"
    surfaced_named = 0
    surfaced_examples = []
    for company in companies:
        sig = {"company": company, "kind": "job", "title": title, "summary": ""}
        c = hm.resolve_lead_contact(sig, contacts=contacts)
        if c.get("name"):
            surfaced_named += 1
            if len(surfaced_examples) < 5:
                surfaced_examples.append(
                    f"{company} -> {c['name']} ({c.get('slot') or c['title']}, "
                    f"conf {c['confidence']:.2f})")

    pct = (surfaced_named / len(companies) * 100) if companies else 0.0
    print("\n  Coverage baseline (graph plumbing, no network/feedback)")
    print(f"    companies in graph        : {len(companies)}")
    print(f"    contact entries           : {total_entries} "
          f"(fresh {fresh}, named-grade ≥{MIN_NAMED_CONFIDENCE} {named_grade})")
    print(f"    companies the button NAMES: {surfaced_named}/{len(companies)} "
          f"({pct:.0f}%) for a '{title}' lead")
    for ex in surfaced_examples:
        print(f"      e.g. {ex}")
    if not companies:
        print("    (graph empty for this desk — every lead falls to a "
              "role-search until it's seeded)")
    return 0


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--all-profiles" in argv:
        rc = 0
        for desk in ("comms", "marketing"):
            env = dict(os.environ, VMA_PROFILE=desk, PYTHONPATH=str(REPO_ROOT))
            r = subprocess.run([sys.executable, "-m", "tool.contacts.measure"],
                               env=env, cwd=str(REPO_ROOT))
            rc = rc or r.returncode
        return rc
    return _measure_once()


if __name__ == "__main__":
    sys.exit(main())
