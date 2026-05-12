#!/usr/bin/env python3
"""Reverse-match: turn one candidate Sara is working with into 10–15 named
target companies where they'd fit, with the contact to call at each.

Manual / on-demand only. Sara fires this after a strong candidate call.

Usage:
    python3 -m tool.reverse_match "Rebecca Torres" "Vodafone" "Head of Internal Communications"
    python3 -m tool.reverse_match "Rebecca Torres" "Vodafone" "Head of IC" send
    python3 -m tool.reverse_match "Rebecca Torres" "Vodafone" "Head of IC" test

Output: ranked target list with company + recommended contact + opener angle.

Honest scope: without paid LinkedIn data (Coresignal) we identify TARGET
COMPANIES reliably but the named decision-maker per target is a *role*
(CHRO, CPO, Head of Comms) rather than a person. Sara opens her Recruiter
seat to put a face on each.
"""
from __future__ import annotations
import html
import logging
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool import config
from tool.email_send import send as email_send
from tool.peers import peers_for, detect_sector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("reverse_match")

STATE_DIR = _REPO_ROOT / "tool" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


# ---- Recommended contact by candidate title --------------------------
# Maps a candidate's current title to who Sara should call at each target
# (the hiring manager for that level of role).
def _recommended_contact(candidate_title: str) -> str:
    t = (candidate_title or "").lower()
    if any(k in t for k in ("chief communications", "cco")):
        return "CEO's office / Chair"
    if any(k in t for k in ("corporate affairs director", "communications director",
                            "director of communications")):
        return "CPO / CHRO"
    if "head of internal" in t or "head of ic" in t:
        return "CHRO / HR Director (IC reports to HR at ~40% of UK mid-caps)"
    if "head of corporate" in t or "head of external" in t:
        return "CHRO or Head of Strategy"
    if "head of pr" in t or "pr director" in t:
        return "CMO / CHRO"
    if "head of media" in t:
        return "CMO / Head of External Affairs"
    if "marketing and brand" in t or "head of brand" in t:
        return "CMO"
    return "CHRO / CPO"


def _opener_for(candidate_name: str, candidate_title: str,
                candidate_company: str, target: str) -> str:
    return (
        f"\"Hi — I'm working with {candidate_name}, currently {candidate_title} at "
        f"{candidate_company}. They're at the point of exploring next moves and "
        f"{target} came up as a natural fit. Open to a 15-minute call?\""
    )


# ---- Per-target rationale --------------------------------------------
# Light-touch — we don't know each target's specific situation without
# paid data. We surface the angle the SECTOR fit creates.
def _rationale(target: str, sector: str | None,
               candidate_company: str, candidate_title: str) -> str:
    sector_label = sector.replace("_", " ") if sector else "the same sector"
    return (
        f"Direct sector peer of {candidate_company} ({sector_label}). "
        f"Comms challenges at {target} closely overlap with {candidate_company}'s; "
        f"the candidate's playbook transfers."
    )


# ---- Rendering -------------------------------------------------------
def _esc(s) -> str:
    return html.escape(str(s) if s is not None else "", quote=True)


def render_html(candidate_name: str, candidate_company: str, candidate_title: str,
                targets: list[str], sector: str | None, mode: str) -> str:
    sector_label = sector.replace("_", " ").title() if sector else "Sector unclear (defaulted to FTSE 100 mix)"
    contact = _recommended_contact(candidate_title)

    rows = []
    for i, t in enumerate(targets, 1):
        rationale = _rationale(t, sector, candidate_company, candidate_title)
        opener = _opener_for(candidate_name, candidate_title, candidate_company, t)
        rows.append(f"""
        <div style="padding:14px 0;border-bottom:1px solid #e5e5e5;">
          <div style="font-weight:600;font-size:15px;">{i}. {_esc(t)}</div>
          <div style="font-size:13px;color:#444;margin-top:4px;">{_esc(rationale)}</div>
          <div style="font-size:13px;margin-top:6px;"><strong>Call:</strong> {_esc(contact)} (look up named person in Recruiter)</div>
          <div style="font-size:13px;color:#333;margin-top:6px;font-style:italic;">{_esc(opener)}</div>
        </div>
        """)

    banner = ""
    if mode == "test":
        banner = "<div style='background:#fff3cd;border:1px solid #ffeaa7;padding:8px;margin-bottom:16px;font-size:13px;'>⚠️ TEST output for Amir.</div>"

    return f"""<!doctype html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:760px;margin:0 auto;padding:20px;color:#111;">
{banner}
<h2 style="margin:0 0 4px 0;">Reverse-match: {_esc(candidate_name)}</h2>
<div style="color:#666;font-size:13px;margin-bottom:18px;">
  Currently {_esc(candidate_title)} at {_esc(candidate_company)} ({_esc(sector_label)}) ·
  Generated {_esc(datetime.now().strftime('%a %d %b %Y · %H:%M'))}
</div>

<div style="font-size:13px;color:#444;margin-bottom:12px;">
  {len(targets)} sector-peer employers where {_esc(candidate_name)}'s profile transfers cleanly.
  For each, the recommended contact role is below — open Recruiter to put a name on it,
  then use the opener as a starting point.
</div>

{''.join(rows)}

<hr style="margin:24px 0;border:none;border-top:1px solid #ddd;">
<div style="color:#888;font-size:12px;">
  Source: peer-employer maps (tool/peers.py) + Sara's market knowledge.
  Named decision-makers per target require LinkedIn Recruiter — this list is the BD-call universe.
</div>
</body></html>
"""


def render_text(candidate_name: str, candidate_company: str, candidate_title: str,
                targets: list[str], sector: str | None) -> str:
    sector_label = sector.replace("_", " ") if sector else "Sector unclear"
    contact = _recommended_contact(candidate_title)
    lines = [
        f"Reverse-match: {candidate_name}",
        f"Currently {candidate_title} at {candidate_company} ({sector_label})",
        f"Generated {datetime.now().strftime('%a %d %b %Y · %H:%M')}",
        "=" * 60, "",
        f"{len(targets)} sector-peer targets. Recommended contact at each: {contact}",
        "(Open Recruiter to put a named person on it.)", "",
    ]
    for i, t in enumerate(targets, 1):
        lines.append(f"{i:>2}. {t}")
        lines.append(f"    {_rationale(t, sector, candidate_company, candidate_title)}")
        lines.append(f"    Opener: {_opener_for(candidate_name, candidate_title, candidate_company, t)}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 4:
        print('Usage: python -m tool.reverse_match "<Candidate Name>" "<Current Company>" "<Current Title>" [mode=preview|send|test]', file=sys.stderr)
        return 2

    candidate_name = sys.argv[1].strip()
    candidate_company = sys.argv[2].strip()
    candidate_title = sys.argv[3].strip()
    mode = (sys.argv[4] if len(sys.argv) > 4 else "preview").lower()

    log.info("Reverse-matching %r (%s @ %s) · mode=%s",
             candidate_name, candidate_title, candidate_company, mode)

    targets, sector = peers_for(candidate_company, k=15)
    html_out = render_html(candidate_name, candidate_company, candidate_title,
                           targets, sector, mode)
    text_out = render_text(candidate_name, candidate_company, candidate_title,
                           targets, sector)

    safe = "".join(c if c.isalnum() else "_" for c in candidate_name.lower())[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    (STATE_DIR / f"reverse_match_{safe}_{stamp}.html").write_text(html_out)
    (STATE_DIR / f"reverse_match_{safe}_{stamp}.txt").write_text(text_out)

    if mode in ("send", "test"):
        to = config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT
        subject = f"[Reverse-match] {candidate_name} → {len(targets)} target employers"
        if mode == "test":
            subject = "[TEST] " + subject
        result = email_send(to, subject, html_out, text_out)
        log.info("Send: %s", result)
        if not result.get("ok"):
            print("\n--- EMAIL SEND FAILED ---")
            print(result)
            return 2
        print(f"✓ Sent to {to}.")
        return 0

    print(text_out)
    print(f"\n[saved to tool/state/reverse_match_{safe}_{stamp}.{{html,txt}}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
