"""Advisory brief generator for Claude Code sessions.

Run this script inside a Claude Code session to prepare research context
for companies that need an advisory brief. Claude Code (Fable) then does
the live research and writes the output back to the advisory research store.

Usage
-----
List companies that need a brief (no existing thesis or past TTL):

    python tool/generate_brief.py

Show the full research context for one company (paste to Claude Code):

    python tool/generate_brief.py --company "MJ Gleeson"
    python tool/generate_brief.py --pid "mj-gleeson"

Save a thesis JSON written by Claude Code back to the store:

    python tool/generate_brief.py --save --pid "mj-gleeson" --file /tmp/thesis.json

Workflow
--------
1. Run with no args to see which companies need research.
2. Run with --company to get the full context block.
3. Tell Claude Code: "Research [company] using this context and write the
   advisory brief JSON."  Claude Code reads public sources, generates the
   JSON matching the schema below, and writes it via --save.
4. The dossier renderer picks up the saved thesis on the next run.

Schema (matches tool/advisory_research._validated output):
{
  "headline": str,
  "function_snapshot": str,
  "needs": [{"need", "service", "service_label", "why_now", "evidence",
              "url", "date", "confidence"}],
  "hiring_needs": [str],
  "meeting_hook": str,
  "talking_points": [str],
  "meeting_prep": {
    "lead_with": str,
    "opening_questions": [str],
    "anticipated_objections": [str],
    "engagement_scope": str
  },
  "sources": [{"url", "label"}]
}

Valid service keys (schema-locked to tool/advisory.SERVICES):
  search, interim, org_design, benchmarking, coaching, edi,
  agency_referral, engagement_platform
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _setup():
    """Minimal path setup — works when run from repo root."""
    import os
    root = Path(__file__).parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.chdir(root)


def _list_pending() -> None:
    from tool import advisory_research as _ar
    from tool import dossier as _doss
    from tool.predictor_pipeline import load_pipeline

    idx = {}
    try:
        idx = _doss._load_index().get("companies") or {}
    except Exception:
        pass

    pipeline = load_pipeline().get("predictors") or {}
    all_theses = _ar.get_all()
    rows = []
    for pid, entry in pipeline.items():
        if entry.get("status") != "active":
            continue
        rec = idx.get(pid) or {}
        gate = rec.get("gate_state") or "queued"
        if gate not in ("presented", "queued"):
            continue
        fresh = pid in all_theses
        ev_hash = _ar.events_hash(rec.get("events") or entry.get("events") or [])
        stale_events = fresh and all_theses[pid].get("events_hash") != ev_hash
        needs_research = not fresh or stale_events
        last_event = max(
            (e.get("date") or "" for e in (rec.get("events") or [])
             if isinstance(e, dict)),
            default="—"
        )
        rows.append({
            "company": entry.get("company") or pid,
            "pid": pid,
            "gate": gate,
            "last_event": last_event,
            "status": ("stale" if stale_events else ("fresh" if fresh else "none")),
            "needs": needs_research,
        })

    rows.sort(key=lambda r: (r["gate"] != "presented", r["last_event"]), reverse=False)
    rows.sort(key=lambda r: not r["needs"])

    print(f"\n{'COMPANY':<35} {'PID':<22} {'GATE':<12} {'LAST SIGNAL':<14} THESIS")
    print("─" * 95)
    for r in rows:
        flag = "  ← needs brief" if r["needs"] else ""
        print(f"{r['company'][:34]:<35} {r['pid'][:21]:<22} {r['gate']:<12} "
              f"{r['last_event']:<14} {r['status']}{flag}")
    pending = sum(1 for r in rows if r["needs"])
    print(f"\n{pending} of {len(rows)} active leads need a brief.\n")


def _context(pid: str | None = None, company: str | None = None) -> None:
    from tool import advisory_research as _ar
    from tool import dossier as _doss
    from tool.predictor_pipeline import load_pipeline
    from tool.advisory import SERVICES, service_fit_for

    pipeline = load_pipeline().get("predictors") or {}
    idx = _doss._load_index().get("companies") or {}

    entry = None
    if pid:
        entry = pipeline.get(pid) or {}
        if not entry:
            for p, e in pipeline.items():
                if p == pid:
                    entry = e
                    pid = p
                    break
    elif company:
        comp_lower = company.lower()
        for p, e in pipeline.items():
            if comp_lower in (e.get("company") or "").lower():
                entry = e
                pid = p
                break

    if not entry or not pid:
        print(f"ERROR: company not found in pipeline. "
              f"Run with no args to see the full list.", file=sys.stderr)
        sys.exit(1)

    rec = idx.get(pid) or {}
    company_name = entry.get("company") or pid

    lines = [
        "=" * 70,
        f"ADVISORY RESEARCH BRIEF — {company_name}",
        f"PID: {pid}",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "=" * 70,
        "",
        f"COMPANY: {company_name}",
        f"Gate state: {rec.get('gate_state') or 'unknown'}",
        f"Budget: {rec.get('budget_why') or 'not assessed'}",
        f"Predicted role: {entry.get('predicted_role') or 'senior comms/marketing'}",
        f"Window: {entry.get('window_label') or 'unknown'}",
        "",
    ]

    # Signal timeline
    events = sorted(rec.get("events") or [], key=lambda x: x.get("date") or "")
    if events:
        lines.append("SIGNAL TIMELINE (full accumulated history):")
        for e in events[-20:]:
            url_part = f" [{e.get('url')}]" if e.get("url") else ""
            lines.append(
                f"  {e.get('date') or '????'} | {e.get('label') or e.get('key')}: "
                f"{(e.get('evidence') or '')[:200]}{url_part}")
        lines.append("")

    # Team map
    try:
        from tool import team_map as _tm
        tm = _tm.summary_lines(company_name)
        if tm:
            lines.append("TEAM MAP (leadership page):")
            lines += [f"  {l}" for l in tm[:12]]
            lines.append("")
    except Exception:
        pass

    # Agency relationships
    try:
        from tool import agency_relationships as _ar2
        ar = _ar2.summary_lines(company_name)
        if ar:
            lines.append("AGENCY RELATIONSHIPS:")
            lines += [f"  {l}" for l in ar[:8]]
            lines.append("")
    except Exception:
        pass

    # Investigation verdict
    try:
        from tool import investigations as _inv
        ov = (_inv.get_all() or {}).get(pid)
        if ov:
            lines.append(f"INVESTIGATION VERDICT: {ov.get('verdict')}")
            if ov.get("note"):
                lines.append(f"  Note: {(ov['note'])[:300]}")
            if ov.get("economic_buyer"):
                lines.append(f"  Economic buyer: {ov['economic_buyer']}")
            lines.append("")
    except Exception:
        pass

    # Static service hypothesis
    try:
        keys = [e.get("key") for e in events if isinstance(e, dict) and e.get("key")]
        fit = service_fit_for(keys) if keys else None
        if fit and fit.get("services"):
            lines.append("STATIC SERVICE HYPOTHESIS (ground or replace with evidence):")
            for s in fit["services"]:
                lines.append(f"  - {s['label']}: {s['reason']}")
            if fit.get("budget_note"):
                lines.append(f"  NOTE: {fit['budget_note']}")
            lines.append("")
    except Exception:
        pass

    # Services catalogue reference
    lines += [
        "VALID SERVICE KEYS (schema-locked — use exact keys):",
    ]
    for key, svc in SERVICES.items():
        lines.append(f"  {key:<22} → {svc['label']}")
    lines.append("")

    # Instructions
    lines += [
        "─" * 70,
        "RESEARCH INSTRUCTIONS FOR CLAUDE CODE:",
        "",
        f"Research {company_name} now using public web sources.",
        "Do NOT fabricate names, numbers, or URLs.",
        "Every need in the output must cite dated evidence.",
        "",
        "Return a single JSON object matching this schema:",
        "  headline         — one-sentence read of their situation",
        "  function_snapshot — what the comms/marketing function looks like today",
        "  needs[]          — genuine gaps VMA can address (max 6)",
        "  hiring_needs[]   — specific hires if any (max 3)",
        "  meeting_hook     — the specific opener that earns the meeting",
        "  talking_points[] — supporting points (max 4)",
        "  meeting_prep     — {lead_with, opening_questions[], ",
        "                       anticipated_objections[], engagement_scope}",
        "  sources[]        — all URLs used, labelled",
        "",
        f"Save with: python tool/generate_brief.py --save --pid {pid} --file <path>",
        "─" * 70,
    ]

    print("\n".join(lines))


def _save(pid: str, file: str) -> None:
    from tool import advisory_research as _ar

    try:
        thesis = json.loads(Path(file).read_text())
    except Exception as e:
        print(f"ERROR reading {file}: {e}", file=sys.stderr)
        sys.exit(1)

    validated = _ar._validated(thesis)
    if not validated:
        print("ERROR: thesis failed validation — check service keys, "
              "required fields (headline, meeting_hook, needs with evidence).",
              file=sys.stderr)
        sys.exit(1)

    from tool import dossier as _doss
    idx = _doss._load_index().get("companies") or {}
    rec = idx.get(pid) or {}
    ev_hash = _ar.events_hash(rec.get("events") or [])

    validated["company"] = (rec.get("company") or pid)
    validated["researched_at"] = datetime.now(timezone.utc).isoformat()
    validated["events_hash"] = ev_hash

    if _ar._write(pid, validated):
        print(f"✓ Advisory brief saved for {validated['company']} (pid: {pid})")
        print(f"  {len(validated['needs'])} needs · "
              f"meeting prep: {'yes' if validated.get('meeting_prep', {}).get('lead_with') else 'no'}")
        print(f"  Dossier will update on next run.")
    else:
        print("ERROR: write to advisory research store failed.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    _setup()

    parser = argparse.ArgumentParser(
        description="Advisory brief generator for Claude Code sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--company", help="Company name (partial match)")
    parser.add_argument("--pid", help="Exact pipeline PID")
    parser.add_argument("--save", action="store_true",
                        help="Save a thesis JSON file to the advisory store")
    parser.add_argument("--file", help="Path to thesis JSON file (used with --save)")
    args = parser.parse_args()

    if args.save:
        if not args.pid or not args.file:
            print("--save requires --pid and --file", file=sys.stderr)
            sys.exit(1)
        _save(args.pid, args.file)
    elif args.company or args.pid:
        _context(pid=args.pid, company=args.company)
    else:
        _list_pending()


if __name__ == "__main__":
    main()
