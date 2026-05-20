#!/usr/bin/env python3
"""Reverse-match: turn one candidate Sara is working with into 10–15 named
target companies where they'd fit, with the contact to call at each.

Manual / on-demand only. Sara fires this after a strong candidate call.

Usage:
    python3 -m tool.reverse_match "Rebecca Torres" "Vodafone" "Head of Internal Communications"
    python3 -m tool.reverse_match "Rebecca Torres" "Vodafone" "Head of IC" send
    python3 -m tool.reverse_match "Rebecca Torres" "Vodafone" "Head of IC" test

Output: ranked target list with per-target priority (HOT / WARM / COLD)
derived from cross-referencing the morning brief's predictor pipeline
and active live-leads. Per-target personalised opener referencing the
specific signal that makes that target fit.
"""
from __future__ import annotations
import html
import json
import logging
import re
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


# ---- Cross-reference helpers -----------------------------------------
# Read the morning brief's state files to find OPEN ROLES (latest_signals.json)
# and ACTIVE PREDICTORS (predictor_pipeline.json) at each target company.
# Without these the reverse-match output is generic; with them, every target
# has a specific reason it's a fit.

_SUFFIX_RX = re.compile(
    r"\b(plc|p\.l\.c\.|limited|ltd|group|holdings|inc|llp|llc|corp|"
    r"corporation|ag|s\.a\.|sa|n\.v\.|nv|gmbh|b\.v\.|bv|spa|oy|uk)\b\.?",
    re.IGNORECASE,
)


def _normalise_company(name: str) -> str:
    s = (name or "").lower().strip()
    s = _SUFFIX_RX.sub("", s)
    s = re.sub(r"[^a-z0-9 &]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _load_state_file(filename: str):
    p = STATE_DIR / filename
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _predictor_for_target(target: str) -> dict | None:
    """Return the predictor pipeline entry matching this target, or None."""
    pipeline = _load_state_file("predictor_pipeline.json") or {}
    predictors = pipeline.get("predictors") or {}
    target_norm = _normalise_company(target)
    if not target_norm:
        return None
    for entry in predictors.values():
        company = entry.get("company") or ""
        if _normalise_company(company) == target_norm:
            return entry
    return None


def _active_leads_for_target(target: str) -> list[dict]:
    """Return live-leads (open roles) for this target. May be empty."""
    leads = _load_state_file("latest_signals.json") or []
    if not isinstance(leads, list):
        return []
    target_norm = _normalise_company(target)
    if not target_norm:
        return []
    out = []
    for lead in leads:
        company = lead.get("company") or ""
        if _normalise_company(company) == target_norm:
            out.append(lead)
    return out


# ---- Recommended contact by candidate title --------------------------
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


# ---- Per-target rationale (NEW: uses cross-references) --------------
def _build_rationale(target: str, candidate_company: str, candidate_title: str,
                     predictor: dict | None, leads: list[dict],
                     sector: str | None) -> dict:
    """Return {priority, label, detail, trigger_hint}.

    Priority tiers:
      HOT   — target has an OPEN role right now matching the candidate
              level. Immediate-call signal.
      WARM  — target has an ACTIVE predictor in the pipeline (recent
              CEO/CFO change, M&A, restructure, etc.). Comms hire
              likely in 4–16 weeks. Candidate fits the upcoming brief.
      COLD  — no active signal at this target. Sector-peer fit only
              (the previous default behaviour).
    """
    sector_label = sector.replace("_", " ") if sector else "the same sector"

    # Tier 1 — Live brief at target
    if leads:
        top = sorted(leads, key=lambda l: -float(l.get("score") or 0))[0]
        role_title = top.get("title", "a senior comms role")
        return {
            "priority": "HOT",
            "label": "Live brief",
            "detail": (f"{target} has '{role_title}' posted right now. "
                       f"Immediate fit - call today before competitors do."),
            "trigger_hint": "open role",
        }

    # Tier 2 — Active predictor at target
    if predictor:
        events = predictor.get("events") or []
        if events:
            trigger_label = (events[0].get("trigger_label") or "trigger event").lower()
            window = predictor.get("window_label", "")
            probability = predictor.get("probability", 0)
            window_str = f", predicted hire window {window}" if window else ""
            prob_str = f" ({probability}% likely)" if probability else ""
            return {
                "priority": "WARM",
                "label": "Predictor active",
                "detail": (f"{trigger_label.capitalize()} at {target}{prob_str} - "
                           f"comms hire forecast{window_str}. "
                           f"Candidate fits the brief about to land."),
                "trigger_hint": trigger_label,
            }

    # Tier 3 — Sector-peer fallback
    return {
        "priority": "COLD",
        "label": "Sector peer",
        "detail": (f"Direct sector peer of {candidate_company} ({sector_label}). "
                   f"Candidate's {candidate_title} playbook transfers cleanly."),
        "trigger_hint": "sector peer",
    }


# ---- Per-target personalised opener ----------------------------------
def _build_opener(candidate_name: str, candidate_title: str,
                  candidate_company: str, target: str,
                  rationale: dict) -> str:
    priority = rationale.get("priority")
    hint = rationale.get("trigger_hint", "")
    if priority == "HOT":
        return (
            f'"Hi - saw {target} has a senior comms role open right now. '
            f"I'm working with {candidate_name}, currently {candidate_title} at "
            f"{candidate_company}. They're exploring next moves and {target} "
            f'is a natural fit. Can I send their profile across today?"'
        )
    if priority == "WARM":
        return (
            f'"Hi - noticed the {hint} at {target} recently. '
            f"I'm working with {candidate_name}, currently {candidate_title} at "
            f"{candidate_company}. Given the comms picture forming at {target}, "
            f'they could be a strong fit ahead of any formal brief. Worth a 15-minute call?"'
        )
    # COLD / sector-peer default
    return (
        f'"Hi - I\'m working with {candidate_name}, currently {candidate_title} at '
        f"{candidate_company}. They're at the point of exploring next moves and "
        f'{target} came up as a natural fit. Open to a 15-minute call?"'
    )


# ---- Rendering -------------------------------------------------------
def _esc(s) -> str:
    return html.escape(str(s) if s is not None else "", quote=True)


_PRIORITY_COLOR = {
    "HOT":  "#C9573D",   # coral
    "WARM": "#B68C2F",   # gold
    "COLD": "#7A7164",   # muted ink
}


def render_html(candidate_name: str, candidate_company: str, candidate_title: str,
                targets_with_rationale: list[tuple[str, dict]], sector: str | None,
                mode: str) -> str:
    sector_label = sector.replace("_", " ").title() if sector else "Sector unclear (defaulted to FTSE 100 mix)"
    contact = _recommended_contact(candidate_title)

    rows = []
    for i, (target, rationale) in enumerate(targets_with_rationale, 1):
        opener = _build_opener(candidate_name, candidate_title, candidate_company,
                                target, rationale)
        priority = rationale.get("priority", "COLD")
        priority_color = _PRIORITY_COLOR.get(priority, "#7A7164")
        rows.append(f"""
        <div style="padding:14px 0;border-bottom:1px solid #e5e5e5;">
          <div style="display:flex;align-items:baseline;gap:10px;">
            <div style="font-weight:600;font-size:15px;">{i}. {_esc(target)}</div>
            <span style="font-size:10px;font-weight:700;letter-spacing:0.10em;
                         color:white;background:{priority_color};
                         padding:2px 8px;border-radius:4px;">
              {_esc(priority)}
            </span>
            <span style="font-size:11px;color:#666;">{_esc(rationale.get('label', ''))}</span>
          </div>
          <div style="font-size:13px;color:#333;margin-top:6px;">{_esc(rationale.get('detail', ''))}</div>
          <div style="font-size:13px;margin-top:6px;"><strong>Call:</strong> {_esc(contact)} (look up named person in Recruiter)</div>
          <div style="font-size:13px;color:#222;margin-top:8px;font-style:italic;
                      background:rgba(201, 100, 66, 0.05);padding:8px 10px;border-radius:4px;
                      border-left:3px solid {priority_color};">
            {_esc(opener)}
          </div>
        </div>
        """)

    banner = ""
    if mode == "test":
        banner = "<div style='background:#fff3cd;border:1px solid #ffeaa7;padding:8px;margin-bottom:16px;font-size:13px;'>⚠️ TEST output for Amir.</div>"

    # Summary of priority counts at the top
    counts = {"HOT": 0, "WARM": 0, "COLD": 0}
    for _, r in targets_with_rationale:
        counts[r.get("priority", "COLD")] = counts.get(r.get("priority", "COLD"), 0) + 1
    priority_summary = (
        f"<strong style='color:{_PRIORITY_COLOR['HOT']};'>🔴 {counts['HOT']} HOT</strong> "
        f"· <strong style='color:{_PRIORITY_COLOR['WARM']};'>🟡 {counts['WARM']} WARM</strong> "
        f"· <strong style='color:{_PRIORITY_COLOR['COLD']};'>⚪ {counts['COLD']} COLD</strong>"
    )

    return f"""<!doctype html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:760px;margin:0 auto;padding:20px;color:#111;">
{banner}
<h2 style="margin:0 0 4px 0;">Reverse-match: {_esc(candidate_name)}</h2>
<div style="color:#666;font-size:13px;margin-bottom:8px;">
  Currently {_esc(candidate_title)} at {_esc(candidate_company)} ({_esc(sector_label)}) ·
  Generated {_esc(datetime.now().strftime('%a %d %b %Y · %H:%M'))}
</div>
<hr style="border:none;border-top:2px solid #C96442;margin:14px 0 24px;">
<div style="font-size:13px;margin-bottom:18px;">
  {len(targets_with_rationale)} target employers · {priority_summary}
</div>

<div style="font-size:13px;color:#444;margin-bottom:12px;">
  Each target cross-referenced against today's predictor pipeline and live-leads.
  <strong style="color:{_PRIORITY_COLOR['HOT']};">HOT</strong> = open role NOW;
  <strong style="color:{_PRIORITY_COLOR['WARM']};">WARM</strong> = active predictor (comms hire forecast);
  <strong style="color:{_PRIORITY_COLOR['COLD']};">COLD</strong> = sector-peer fit only.
  Open Recruiter to put a named person on the call.
</div>

{''.join(rows)}

</body></html>
"""


def render_text(candidate_name: str, candidate_company: str, candidate_title: str,
                targets_with_rationale: list[tuple[str, dict]], sector: str | None) -> str:
    sector_label = sector.replace("_", " ") if sector else "Sector unclear"
    contact = _recommended_contact(candidate_title)
    counts = {"HOT": 0, "WARM": 0, "COLD": 0}
    for _, r in targets_with_rationale:
        counts[r.get("priority", "COLD")] = counts.get(r.get("priority", "COLD"), 0) + 1
    lines = [
        f"Reverse-match: {candidate_name}",
        f"Currently {candidate_title} at {candidate_company} ({sector_label})",
        f"Generated {datetime.now().strftime('%a %d %b %Y · %H:%M')}",
        "=" * 60, "",
        f"{len(targets_with_rationale)} targets · {counts['HOT']} HOT · {counts['WARM']} WARM · {counts['COLD']} COLD",
        f"Recommended contact at each: {contact}", "",
    ]
    for i, (target, rationale) in enumerate(targets_with_rationale, 1):
        priority = rationale.get("priority", "COLD")
        opener = _build_opener(candidate_name, candidate_title, candidate_company,
                                target, rationale)
        lines.append(f"{i:>2}. [{priority}] {target} - {rationale.get('label', '')}")
        lines.append(f"    {rationale.get('detail', '')}")
        lines.append(f"    Opener: {opener}")
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

    # Cross-reference each target with the morning brief's outputs to build
    # per-target rationale (HOT live brief / WARM predictor / COLD sector peer)
    targets_with_rationale = []
    for target in targets:
        predictor = _predictor_for_target(target)
        leads = _active_leads_for_target(target)
        rationale = _build_rationale(target, candidate_company, candidate_title,
                                      predictor, leads, sector)
        targets_with_rationale.append((target, rationale))

    # Re-rank by priority: HOT first, then WARM, then COLD
    priority_rank = {"HOT": 0, "WARM": 1, "COLD": 2}
    targets_with_rationale.sort(key=lambda pair: priority_rank.get(pair[1].get("priority"), 99))

    hot_n = sum(1 for _, r in targets_with_rationale if r.get("priority") == "HOT")
    warm_n = sum(1 for _, r in targets_with_rationale if r.get("priority") == "WARM")
    cold_n = sum(1 for _, r in targets_with_rationale if r.get("priority") == "COLD")
    log.info("Cross-ref complete: %d HOT, %d WARM, %d COLD", hot_n, warm_n, cold_n)

    html_out = render_html(candidate_name, candidate_company, candidate_title,
                           targets_with_rationale, sector, mode)
    text_out = render_text(candidate_name, candidate_company, candidate_title,
                           targets_with_rationale, sector)

    safe = "".join(c if c.isalnum() else "_" for c in candidate_name.lower())[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    (STATE_DIR / f"reverse_match_{safe}_{stamp}.html").write_text(html_out)
    (STATE_DIR / f"reverse_match_{safe}_{stamp}.txt").write_text(text_out)

    if mode in ("send", "test") and getattr(config, "NON_BRIEF_EMAIL_ENABLED", False):
        to = config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT
        n_hot = sum(1 for _, r in targets_with_rationale if r.get("priority") == "HOT")
        n_warm = sum(1 for _, r in targets_with_rationale if r.get("priority") == "WARM")
        subject = (f"[Reverse-match] {candidate_name} → {len(targets_with_rationale)} targets "
                   f"({n_hot} HOT, {n_warm} WARM)")
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
