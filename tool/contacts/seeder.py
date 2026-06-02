"""Initial seed pass for the hiring-contacts table.

Two modes:
  --dry-run   Resolve a hand-picked adversarial sample (~30 entries
              skewed toward hard cases: recently-rebranded entities,
              private-equity-backed firms, holding companies with
              multiple operating brands, stem collisions). Output
              evidence-per-entry to stdout + state/seed_audit_<ts>.json
              for Sara to spot-check. Nothing written to the main
              contacts table.

  --commit    Run the full seed across all watchlist entities, writing
              to hiring_contacts.json and contact_resolution_log.jsonl.
              Should only be invoked after Sara confirms the dry-run
              sample is acceptable.

Sampling is deliberately adversarial. A random 30-entry sample across
the ~535 watchlist names would be dominated by the easy 80% (FTSE 100
with clean leadership pages); the systematic failures hide in the
awkward 20%. So we stuff the dry-run sample with hard cases by name.
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tool.contacts.schema import ResolutionStatus, ROLE_SLOTS
from tool.contacts.store import (
    append_resolution_log, load_contacts, save_contacts, upsert_contact,
)
from tool.contacts.resolver import resolve

log = logging.getLogger("brief.contacts.seeder")

STATE_DIR = Path(__file__).resolve().parent.parent / "state"


# ---- Adversarial sample (~30 entries, all known awkward cases) -----
ADVERSARIAL_SAMPLE = [
    # Recently-rebranded entities
    "International Distribution Services",   # was Royal Mail
    "abrdn",                                 # was Standard Life Aberdeen
    "Haleon",                                # GSK consumer health spinoff
    "Network International",
    # Holding companies with multiple operating brands
    "Associated British Foods",
    "Compass Group",
    "Bunzl",
    "Pearson",
    "Frasers Group",                         # Sports Direct + House of Fraser etc
    "Whitbread",                             # Premier Inn
    # Stem collisions (names that resemble bigger firms)
    "Capita",
    "Capital & Counties",
    "Standard Chartered",
    # PE-backed / privately held / thin web presence
    "Iceland Foods",
    "Pret a Manger",
    "Stonegate Pub Company",
    "Care UK",
    "Priory Group",
    "Four Seasons Health Care",
    # Family-owned / unusual governance
    "John Lewis Partnership",
    "Cranswick",
    # Charities / public sector (CH may not apply cleanly)
    "British Heart Foundation",
    "Marie Curie",
    "NHS Confederation",
    # Names with ampersands or punctuation (parser stress)
    "Marks & Spencer",
    "Tate & Lyle",
    "Mitchells & Butlers",
    "Legal & General",
    # International (non-UK) — verifies the resolver doesn't UK-bias hard
    "Apple",
    "Nestlé",
    "BlackRock",
]


# Role slots we seed by default. ir_director and head_of_ic deliberately
# excluded from the bulk seed — they're sparse, low signal/noise, and
# don't justify the API spend on the initial pass. They'll get filled
# in opportunistically by the re-verify queue when triggered.
# Profile-aware: each desk seeds its own slots. Universal C-suite
# (ceo/chair/cfo/chro) for both; comms-specific for comms, marketing-
# specific for marketing. Sparse slots (head_of_ic / ir_director for comms,
# head_of_growth for marketing) are deliberately left to opportunistic
# re-verify rather than the bulk seed, to save lookups.
from tool.profiles import active_profile as _active_profile_seed
if _active_profile_seed().key == "marketing":
    SEED_ROLE_SLOTS = ("ceo", "chair", "cfo", "cmo", "chro",
                       "head_of_marketing", "head_of_brand")
else:
    SEED_ROLE_SLOTS = ("ceo", "chair", "cfo", "cco", "chro", "gc",
                       "head_of_comms", "head_of_corporate_affairs")


def _all_watchlist() -> list[str]:
    from tool.peers import SECTOR_PEERS
    seen, out = set(), []
    for names in SECTOR_PEERS.values():
        for n in names:
            key = n.lower().strip()
            if key not in seen:
                seen.add(key)
                out.append(n)
    return out


def _make_fetch():
    """Return a Bright Data fetch callable, or None if BD isn't
    configured. Returns None when EITHER:
      - BRIGHT_DATA_KEY env var is empty (no API key), OR
      - BRIGHT_DATA_ZONE env var is empty (no zone on the BD account)
    When None, the resolver records 'fetch disabled (no Bright Data)'
    instead of attempting calls that would 400. Lets us seed the
    contacts table on CH + RNS alone until BD is provisioned."""
    import os
    if not os.environ.get("BRIGHT_DATA_KEY", "").strip():
        return None
    if not os.environ.get("BRIGHT_DATA_ZONE", "").strip():
        return None
    from tool.linkedin_resolver import _bright_data_fetch_diag
    return _bright_data_fetch_diag


def _audit_path(when: datetime) -> Path:
    ts = when.strftime("%Y%m%d_%H%M")
    return STATE_DIR / f"seed_audit_{ts}.json"


def run_dry_run() -> dict:
    """Produce evidence-per-entry for the adversarial sample. Returns the
    audit dict and writes it to state/seed_audit_<ts>.json."""
    fetch = _make_fetch()
    now = datetime.now(timezone.utc)
    audit: dict = {
        "generated_at": now.isoformat(),
        "sample_size": len(ADVERSARIAL_SAMPLE),
        "bright_data_enabled": fetch is not None,
        "role_slots_seeded": list(SEED_ROLE_SLOTS),
        "entries": [],
    }

    for i, company in enumerate(ADVERSARIAL_SAMPLE, 1):
        log.info("[dry-run %d/%d] %s", i, len(ADVERSARIAL_SAMPLE), company)
        company_block = {"company": company, "by_slot": {}}
        for slot in SEED_ROLE_SLOTS:
            entry, record = resolve(company, slot, fetch=fetch)
            company_block["by_slot"][slot] = {
                "outcome": record.outcome,
                "picked_name": record.picked_name,
                "picked_url": record.picked_url,
                "confidence": record.confidence,
                "candidates_considered": [
                    {
                        "name": c.name,
                        "role_title": c.role_title,
                        "confidence": c.confidence,
                        "source": c.source,
                        "linkedin_url": c.linkedin_url,
                    }
                    for c in record.candidates_considered
                ],
                "sources_queried": [
                    {
                        "source": s.source,
                        "returned_data": s.returned_data,
                        "used": s.used,
                        "reason": s.reason,
                    }
                    for s in record.sources_queried
                ],
            }
            time.sleep(0.4)  # courtesy spacing across slots
        audit["entries"].append(company_block)

    audit_path = _audit_path(now)
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    log.info("Dry-run audit written to %s", audit_path)
    return audit


def run_commit(*, limit: int | None = None) -> dict:
    """Full seed across all watchlist entities. Writes hiring_contacts.json
    and appends one resolution-log line per (company, role_slot) attempted."""
    fetch = _make_fetch()
    contacts = load_contacts()
    names = _all_watchlist()
    if limit is not None:
        names = names[:limit]

    stats = {"companies": len(names), "verified": 0, "no_match": 0}

    for i, company in enumerate(names, 1):
        log.info("[seed %d/%d] %s", i, len(names), company)
        card_seeded_any = False
        for slot in SEED_ROLE_SLOTS:
            entry, record = resolve(company, slot, fetch=fetch)
            append_resolution_log(record)
            if entry is not None and record.outcome == ResolutionStatus.RESOLVED_VERIFIED:
                upsert_contact(contacts, company, slot, entry)
                stats["verified"] += 1
                card_seeded_any = True
            else:
                stats["no_match"] += 1
            time.sleep(0.4)
        if card_seeded_any:
            card = contacts[company] if company in contacts else None
            if card is not None:
                card.last_seeded_at = datetime.now(timezone.utc).isoformat()

    save_contacts(contacts)
    return stats


def _render_dry_run_summary(audit: dict) -> str:
    """Plain-text summary that Sara can scan in under a minute."""
    lines = [
        f"Hiring-contacts seed · dry-run audit",
        f"Generated: {audit['generated_at']}",
        f"Sample size: {audit['sample_size']} entities · adversarial",
        f"Bright Data: {'enabled' if audit['bright_data_enabled'] else 'DISABLED (no BD key)'}",
        "-" * 64,
    ]
    for block in audit["entries"]:
        lines.append(f"\n{block['company']}")
        for slot, info in block["by_slot"].items():
            outcome = info["outcome"]
            picked = info["picked_name"] or "(none)"
            confidence = info["confidence"]
            sources = [s["source"] for s in info["sources_queried"] if s["returned_data"]]
            if outcome == ResolutionStatus.RESOLVED_VERIFIED:
                lines.append(
                    f"  {slot:<28} -> {picked} (conf {confidence:.2f}, "
                    f"via {','.join(sources) or 'unknown'})"
                )
            else:
                reasons = " · ".join(
                    f"{s['source']}={'data' if s['returned_data'] else 'empty'}"
                    + (f"({s['reason']})" if s['reason'] else "")
                    for s in info["sources_queried"]
                )
                lines.append(f"  {slot:<28} -> no match  [{reasons}]")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the hiring-contacts table")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve the adversarial sample only; write audit")
    parser.add_argument("--commit", action="store_true",
                        help="Run the full seed; writes to hiring_contacts.json")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap number of watchlist entities (commit mode)")
    parser.add_argument("--print-summary", action="store_true", default=True,
                        help="Print human-readable summary at the end")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.dry_run == args.commit:
        parser.error("Specify exactly one of --dry-run or --commit")

    if args.dry_run:
        audit = run_dry_run()
        if args.print_summary:
            print(_render_dry_run_summary(audit))
        print(f"\nAudit written to {_audit_path(datetime.fromisoformat(audit['generated_at']))}")
        return 0

    stats = run_commit(limit=args.limit)
    print(f"Seed complete: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
