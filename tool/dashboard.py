#!/usr/bin/env python3
"""Sara's Desk — local Flask dashboard.

One URL, one bookmark. Shows latest leads + predictors from the most recent
morning brief, and three action boxes (Pitch Pack, Reverse Match, 14-Day
Catch-up) that trigger the corresponding GitHub Actions workflows and email
the results.

Usage (after one-time setup — see DASHBOARD_SETUP.md):
    python3 -m tool.dashboard
Then open http://localhost:8765 in your browser.

Required env (set in .env):
    GITHUB_TOKEN          — Personal Access Token with `workflow` scope
    GITHUB_OWNER          — defaults to "atst2026"
    GITHUB_REPO           — defaults to "VMA"
    DASHBOARD_PORT        — defaults to 8765
"""
from __future__ import annotations
import json
import logging
import os
import sys
import zipfile
import io
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template_string, request
from urllib.parse import quote_plus

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env if present (Flask doesn't do this by default)
_env_file = _REPO_ROOT / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("dashboard")

STATE_DIR = _REPO_ROOT / "tool" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "atst2026")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "VMA")
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))


# ---- GitHub API helpers ------------------------------------------------
def _github_headers() -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def trigger_workflow(workflow_filename: str, inputs: dict) -> dict:
    """POST to /actions/workflows/{file}/dispatches. Returns {ok, status, detail}."""
    if not GITHUB_TOKEN:
        return {"ok": False, "detail": "GITHUB_TOKEN not set in .env"}
    url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
           f"/actions/workflows/{workflow_filename}/dispatches")
    body = {"ref": "main", "inputs": inputs}
    try:
        r = requests.post(url, headers=_github_headers(), json=body, timeout=15)
        ok = r.status_code in (204, 200)
        return {"ok": ok, "status": r.status_code,
                "detail": "Dispatched. Email in 1–2 minutes."
                          if ok else f"GitHub returned {r.status_code}: {r.text[:200]}"}
    except requests.RequestException as e:
        return {"ok": False, "detail": f"Network error: {e}"}


def refresh_latest_brief_from_github() -> dict:
    """Download the most recent morning-brief artifact and unpack into state."""
    if not GITHUB_TOKEN:
        return {"ok": False, "detail": "GITHUB_TOKEN not set in .env"}
    list_url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
                f"/actions/artifacts?per_page=20")
    try:
        r = requests.get(list_url, headers=_github_headers(), timeout=15)
        if r.status_code != 200:
            return {"ok": False, "detail": f"Artifact list: HTTP {r.status_code}"}
        artifacts = r.json().get("artifacts", [])
        morning_artifacts = [a for a in artifacts if a.get("name") == "morning-brief" and not a.get("expired")]
        if not morning_artifacts:
            return {"ok": False, "detail": "No recent morning-brief artifact found."}
        latest = morning_artifacts[0]
        zip_url = latest["archive_download_url"]
        r2 = requests.get(zip_url, headers=_github_headers(), timeout=30)
        if r2.status_code != 200:
            return {"ok": False, "detail": f"Download: HTTP {r2.status_code}"}
        with zipfile.ZipFile(io.BytesIO(r2.content)) as zf:
            for member in zf.namelist():
                if member.endswith((".html", ".txt", ".json")):
                    zf.extract(member, STATE_DIR)
        return {"ok": True, "detail": f"Refreshed from artifact {latest['id']} "
                                       f"({latest.get('created_at', '?')[:10]})."}
    except Exception as e:
        return {"ok": False, "detail": f"Refresh failed: {e}"}


# ---- Local state loaders ------------------------------------------------
def load_latest_signals() -> list[dict]:
    p = STATE_DIR / "latest_signals.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except Exception:
        return []
    # Enrich each lead with a personalised outreach draft + LinkedIn search URL
    for s in data:
        s["outreach"] = draft_outreach_for_lead(s)
        s["linkedin_url"] = linkedin_search_url(s.get("company", ""))
    return data


def load_latest_predictive() -> list[dict]:
    p = STATE_DIR / "latest_predictive.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except Exception:
        return []
    for p_item in data:
        p_item["outreach"] = draft_outreach_for_predictor(p_item)
        p_item["linkedin_url"] = linkedin_search_url(p_item.get("company", ""))
    return data


# ---- Outreach message drafting -----------------------------------------
_SIG = (
    "\n\n— Sara Tehrani\n"
    "Account Director · Internal & Corporate Communications\n"
    "VMA Group"
)


def draft_outreach_for_lead(s: dict) -> str:
    """Generate a personalised LinkedIn outreach draft for a single lead.
    Sara copies, opens the right LinkedIn profile, edits the [first name]
    placeholder, sends."""
    kind = s.get("kind", "")
    company = s.get("company") or "your organisation"
    title = (s.get("title") or "").strip()

    if kind == "job":
        return (
            f"Hi [first name],\n\n"
            f"Saw {company}'s {title} role go live. I head VMA Group's "
            f"Internal & Corporate Communications practice — we place senior "
            f"comms leaders into roles like this across the UK every month.\n\n"
            f"Happy to share the candidate landscape, typical search timelines "
            f"and salary benchmarks at this level. Worth a quick 15-min call "
            f"this week?" + _SIG
        )
    if kind == "leadership_change":
        return (
            f"Hi [first name],\n\n"
            f"Congratulations on the move to {company}. Senior comms refreshes "
            f"typically follow within the first 6 months in role.\n\n"
            f"I lead VMA Group's Internal & Corporate Communications desk — "
            f"if it's useful to compare notes on the senior candidate landscape "
            f"or what's worked at peer organisations, happy to set up a quick "
            f"call." + _SIG
        )
    if kind == "rns":
        return (
            f"Hi [first name],\n\n"
            f"Noticed {company}'s board change announcement. Comms team "
            f"refreshes typically follow within 6–12 weeks as new leadership "
            f"reviews the function.\n\n"
            f"I head VMA Group's Internal & Corporate Communications practice. "
            f"Happy to share the candidate landscape ahead of any search — "
            f"worth a quick 15-min call?" + _SIG
        )
    if kind == "trade_press":
        return (
            f"Hi [first name],\n\n"
            f"Saw the {company} news. I head VMA Group's Internal & Corporate "
            f"Communications desk and place senior leaders across this space.\n\n"
            f"If you're building or refreshing the comms function, happy to "
            f"share the candidate landscape and typical search shape. Open to "
            f"a quick call?" + _SIG
        )
    if kind == "regulator":
        return (
            f"Hi [first name],\n\n"
            f"Noticed the regulator action affecting {company}. Reputational "
            f"moments typically surface a need for interim crisis comms cover "
            f"or a permanent Head of Comms refresh.\n\n"
            f"I lead VMA Group's Internal & Corporate Communications desk — "
            f"happy to share who's movable at what day rate, and on what "
            f"timeline. Quick call?" + _SIG
        )
    if kind == "filing":
        return (
            f"Hi [first name],\n\n"
            f"Saw {company}'s recent filing. Filings of this kind often signal "
            f"a comms function refresh in the months ahead.\n\n"
            f"I head VMA Group's Internal & Corporate Communications practice. "
            f"Happy to share the candidate landscape if useful — worth a "
            f"quick call?" + _SIG
        )
    if kind == "procurement":
        return (
            f"Hi [first name],\n\n"
            f"Saw {company}'s procurement notice. VMA Group's Internal & "
            f"Corporate Communications practice has placed senior leaders "
            f"against frameworks like this across the UK public sector.\n\n"
            f"Happy to share who's open at the right level — quick call?" + _SIG
        )
    return (
        f"Hi [first name],\n\n"
        f"Noticed something at {company} I thought was worth reaching out on. "
        f"I head VMA Group's Internal & Corporate Communications practice — "
        f"happy to compare notes on the candidate landscape or your current "
        f"senior hiring approach.\n\n"
        f"Open to a 15-min call this week?" + _SIG
    )


def draft_outreach_for_predictor(p: dict) -> str:
    """For a predictor (stacked or single trigger), produce an outreach draft
    pitched at the relevant contact at the named company."""
    company = p.get("company", "your organisation")
    events = p.get("events", []) or []
    depth = p.get("depth", 1) or 1
    keys = {e.get("trigger_key") for e in events}

    if "ceo_change" in keys:
        return (
            f"Hi [first name],\n\n"
            f"I see {company} has just announced new CEO leadership. Most "
            f"incoming CEOs review the comms function within their first 6 "
            f"months — a Corporate Affairs / Head of Comms refresh inside "
            f"12 weeks is the typical pattern.\n\n"
            f"I head VMA Group's Internal & Corporate Communications desk. "
            f"Happy to share the senior candidate landscape and typical "
            f"search timelines ahead of that conversation — quick 15-min "
            f"call?" + _SIG
        )
    if "chro_change" in keys:
        return (
            f"Hi [first name],\n\n"
            f"Congratulations on the move to {company}. Internal Comms "
            f"reports to HR at ~40% of UK mid-caps — a comms direct-report "
            f"refresh in your first 8–16 weeks is the typical pattern.\n\n"
            f"I head VMA Group's Internal & Corporate Communications practice. "
            f"Happy to share the candidate landscape ahead of any search — "
            f"worth a quick call?" + _SIG
        )
    if "chair_change" in keys:
        return (
            f"Hi [first name],\n\n"
            f"Noticed {company}'s new Chair announcement. Board changes "
            f"typically trigger a comms function review within 8–16 weeks.\n\n"
            f"I head VMA Group's Internal & Corporate Communications desk. "
            f"Happy to share the senior candidate landscape ahead of any "
            f"search — quick call?" + _SIG
        )
    if "regulator_action" in keys:
        return (
            f"Hi [first name],\n\n"
            f"I saw the regulator action against {company}. Reputational "
            f"moments like this typically need interim crisis comms cover "
            f"within 2–8 weeks and a permanent Head of Comms review shortly "
            f"after.\n\n"
            f"I lead VMA Group's Internal & Corporate Communications desk — "
            f"happy to share who's available at what day rate, and on what "
            f"timeline. Quick call?" + _SIG
        )
    if "mna" in keys:
        return (
            f"Hi [first name],\n\n"
            f"Saw {company}'s deal announcement. Post-close comms integration "
            f"and rebrand work typically drives senior comms hiring within "
            f"3–12 months.\n\n"
            f"I head VMA Group's Internal & Corporate Communications practice. "
            f"Happy to share the senior candidate landscape ahead of integration "
            f"planning — quick call?" + _SIG
        )
    if "restructure" in keys:
        return (
            f"Hi [first name],\n\n"
            f"Noticed the restructure / strategic review at {company}. Comms "
            f"functions are commonly reorganised within 2–6 months of these "
            f"announcements.\n\n"
            f"I head VMA Group's Internal & Corporate Communications desk. "
            f"Happy to share the candidate landscape ahead of any search — "
            f"quick call?" + _SIG
        )
    if "job_ad_cluster" in keys:
        return (
            f"Hi [first name],\n\n"
            f"I noticed {company} has several mid-level comms roles live but "
            f"no senior Head-of-Comms post yet — typically signals a senior "
            f"hire within 90 days.\n\n"
            f"I head VMA Group's Internal & Corporate Communications practice. "
            f"Happy to share the senior candidate landscape ahead of that "
            f"search — quick call?" + _SIG
        )
    # Stacked-but-no-named-trigger fallback
    stack_phrase = "Multiple signals" if depth > 1 else "A signal"
    return (
        f"Hi [first name],\n\n"
        f"{stack_phrase} at {company} suggests a senior comms refresh is "
        f"likely in the next few months.\n\n"
        f"I head VMA Group's Internal & Corporate Communications desk. "
        f"Happy to share the candidate landscape ahead of any search — "
        f"quick call?" + _SIG
    )


def linkedin_search_url(company: str) -> str:
    """Open LinkedIn people search for the company. Sara filters internally."""
    c = (company or "").strip()
    if not c:
        return "https://www.linkedin.com/search/results/people/"
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(c)}"


def last_updated() -> str:
    p = STATE_DIR / "latest_signals.json"
    if not p.exists():
        return "never"
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    return ts.strftime("%a %d %b %Y · %H:%M UTC")


# ---- Flask app ----------------------------------------------------------
app = Flask(__name__)


@app.route("/")
def index():
    return render_template_string(
        TEMPLATE,
        leads=load_latest_signals(),
        predictors=load_latest_predictive(),
        last_updated=last_updated(),
        has_token=bool(GITHUB_TOKEN),
    )


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    return jsonify(refresh_latest_brief_from_github())


@app.route("/api/dispatch/pitch-pack", methods=["POST"])
def api_pitch_pack():
    data = request.get_json(force=True) or {}
    inputs = {
        "account_name": (data.get("account_name") or "").strip(),
        "role": (data.get("role") or "Head of Internal Communications").strip(),
        "mode": data.get("mode", "send"),
    }
    if not inputs["account_name"]:
        return jsonify({"ok": False, "detail": "Account name required"}), 400
    return jsonify(trigger_workflow("pitch-pack.yml", inputs))


@app.route("/api/dispatch/reverse-match", methods=["POST"])
def api_reverse_match():
    data = request.get_json(force=True) or {}
    inputs = {
        "candidate_name": (data.get("candidate_name") or "").strip(),
        "current_company": (data.get("current_company") or "").strip(),
        "current_title": (data.get("current_title") or "").strip(),
        "mode": data.get("mode", "send"),
    }
    missing = [k for k in ("candidate_name", "current_company", "current_title")
               if not inputs[k]]
    if missing:
        return jsonify({"ok": False, "detail": f"Missing: {', '.join(missing)}"}), 400
    return jsonify(trigger_workflow("reverse-match.yml", inputs))


@app.route("/api/dispatch/sweep", methods=["POST"])
def api_sweep():
    data = request.get_json(force=True) or {}
    inputs = {
        "window_days": str(data.get("window_days", "14")),
        "mode": data.get("mode", "send"),
    }
    return jsonify(trigger_workflow("fortnightly-sweep.yml", inputs))


TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sara's Desk · VMA Group</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --navy: #0E2845;
      --navy-deep: #061528;
      --navy-soft: rgba(14, 40, 69, 0.08);
      --navy-hairline: rgba(14, 40, 69, 0.12);
      --teal: #5BA6AD;
      --teal-bright: #6FB8BF;
      --teal-dark: #458C92;
      --teal-glow: rgba(91, 166, 173, 0.35);
      --teal-soft: rgba(91, 166, 173, 0.10);
      --bg: #F6F8FB;
      --surface: #FFFFFF;
      --surface-elevated: #FFFFFF;
      --border: rgba(14, 40, 69, 0.08);
      --border-hover: rgba(14, 40, 69, 0.16);
      --text: #0E2845;
      --text-muted: #6B7888;
      --shadow-sm: 0 1px 2px rgba(14, 40, 69, 0.04);
      --shadow-md: 0 4px 12px rgba(14, 40, 69, 0.06), 0 1px 3px rgba(14, 40, 69, 0.04);
      --shadow-lg: 0 10px 32px rgba(14, 40, 69, 0.10);
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; }
    body {
      font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      background-color: var(--bg);
      background-image:
        radial-gradient(circle at 1px 1px, rgba(14, 40, 69, 0.04) 1px, transparent 0);
      background-size: 20px 20px;
      color: var(--text);
      line-height: 1.5;
      font-weight: 400;
      font-size: 13px;
      font-feature-settings: "ss01", "cv11";
      -webkit-font-smoothing: antialiased;
      letter-spacing: -0.005em;
    }

    /* HEADER — navy with backlit gradient */
    .header {
      background: linear-gradient(135deg, var(--navy-deep) 0%, var(--navy) 60%, #143352 100%);
      color: white;
      padding: 14px 28px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid rgba(91, 166, 173, 0.18);
      position: relative;
      overflow: hidden;
    }
    .header::before {
      content: "";
      position: absolute;
      top: -50%; right: -10%;
      width: 400px; height: 200%;
      background: radial-gradient(circle, rgba(91, 166, 173, 0.12) 0%, transparent 70%);
      pointer-events: none;
    }
    .header .brand { display: flex; align-items: center; gap: 22px; position: relative; z-index: 1; }
    .wordmark {
      font-size: 15px;
      letter-spacing: 0.02em;
      line-height: 1;
      color: white;
    }
    .wordmark .vma { font-weight: 700; letter-spacing: 0.005em; }
    .wordmark .group {
      font-weight: 300;
      margin-left: 5px;
      opacity: 0.85;
      letter-spacing: 0.22em;
      font-size: 14px;
    }
    .header h1 {
      margin: 0;
      font-size: 13px;
      font-weight: 500;
      letter-spacing: -0.005em;
      color: white;
      padding-left: 22px;
      border-left: 1px solid rgba(255,255,255,0.16);
    }
    .header h1 .sub {
      display: block;
      font-size: 11px;
      font-weight: 400;
      opacity: 0.6;
      margin-top: 3px;
      letter-spacing: 0;
    }
    .header .meta {
      font-size: 11px;
      opacity: 0.92;
      text-align: right;
      display: flex;
      align-items: center;
      gap: 14px;
      position: relative;
      z-index: 1;
    }
    .header .meta .last {
      font-weight: 400;
      letter-spacing: 0;
      display: flex;
      align-items: center;
      gap: 7px;
      color: rgba(255,255,255,0.78);
    }
    .header .meta .last::before {
      content: "";
      width: 6px; height: 6px;
      background: var(--teal-bright);
      border-radius: 50%;
      box-shadow: 0 0 0 0 rgba(91, 166, 173, 0.7), 0 0 8px var(--teal-glow);
      animation: pulse 2.4s infinite;
    }
    @keyframes pulse {
      0% { box-shadow: 0 0 0 0 rgba(91, 166, 173, 0.6), 0 0 8px var(--teal-glow); }
      70% { box-shadow: 0 0 0 8px rgba(91, 166, 173, 0), 0 0 8px var(--teal-glow); }
      100% { box-shadow: 0 0 0 0 rgba(91, 166, 173, 0), 0 0 8px var(--teal-glow); }
    }
    .header .meta button {
      background: rgba(255, 255, 255, 0.04);
      color: white;
      border: 1px solid rgba(91, 166, 173, 0.4);
      padding: 6px 13px;
      border-radius: 6px;
      font-size: 11px;
      font-family: inherit;
      font-weight: 500;
      letter-spacing: 0.01em;
      cursor: pointer;
      transition: all 0.18s ease;
      backdrop-filter: blur(8px);
    }
    .header .meta button:hover {
      background: rgba(91, 166, 173, 0.18);
      border-color: var(--teal);
      box-shadow: 0 0 12px var(--teal-glow);
    }

    .container {
      max-width: 1280px;
      margin: 0 auto;
      padding: 22px 28px 16px 28px;
    }

    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-bottom: 18px;
    }
    @media (max-width: 900px) {
      .row { grid-template-columns: 1fr; }
    }

    /* PANEL — soft card, no thick borders */
    .panel {
      background: var(--surface);
      border-radius: 10px;
      border: 1px solid var(--border);
      box-shadow: var(--shadow-md);
      overflow: hidden;
      transition: box-shadow 0.18s ease, transform 0.18s ease;
      display: flex;
      flex-direction: column;
    }
    .panel:hover {
      box-shadow: var(--shadow-lg);
    }
    .panel-header {
      padding: 11px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: linear-gradient(180deg, rgba(91, 166, 173, 0.03) 0%, rgba(255, 255, 255, 0) 100%);
      flex-shrink: 0;
    }
    .panel-header h2 {
      margin: 0;
      font-size: 10.5px;
      font-weight: 600;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--navy);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .panel-header h2::before {
      content: "";
      display: inline-block;
      width: 5px; height: 5px;
      background: var(--teal);
      border-radius: 50%;
      box-shadow: 0 0 6px var(--teal-glow);
    }
    .panel-header .count {
      background: var(--teal-soft);
      color: var(--teal-dark);
      font-size: 10px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 10px;
      letter-spacing: 0.02em;
      border: 1px solid var(--teal-soft);
    }
    .panel-body {
      flex: 1;
      max-height: 460px;
      overflow-y: auto;
    }
    /* Hide ugly default scrollbars, show on hover */
    .panel-body::-webkit-scrollbar { width: 6px; }
    .panel-body::-webkit-scrollbar-thumb {
      background: var(--navy-hairline);
      border-radius: 3px;
    }
    .panel-body::-webkit-scrollbar-thumb:hover { background: var(--navy-soft); }

    /* ITEMS */
    .item {
      padding: 11px 16px;
      border-bottom: 1px solid var(--border);
      transition: background 0.15s ease;
    }
    .item:hover { background: rgba(91, 166, 173, 0.025); }
    .item:last-child { border-bottom: 0; }
    .item .rank {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px; height: 18px;
      background: var(--teal-soft);
      color: var(--teal-dark);
      border: 1px solid rgba(91, 166, 173, 0.25);
      border-radius: 4px;
      font-size: 10px;
      font-weight: 600;
      margin-right: 8px;
      vertical-align: middle;
    }
    .item .title {
      font-size: 12.5px;
      font-weight: 500;
      color: var(--navy);
      letter-spacing: -0.005em;
    }
    .item .title a {
      color: var(--navy);
      text-decoration: none;
      border-bottom: 1px solid transparent;
      transition: border-color 0.15s;
    }
    .item .title a:hover { border-bottom-color: var(--teal); }
    .item .meta {
      font-size: 10.5px;
      color: var(--text-muted);
      margin-top: 5px;
      margin-left: 26px;
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    .item .meta .badge {
      background: var(--bg);
      padding: 2px 7px;
      border-radius: 3px;
      font-weight: 500;
      color: var(--navy);
      font-size: 10px;
      border: 1px solid var(--border);
    }
    .item .meta a {
      color: var(--teal-dark);
      text-decoration: none;
      font-weight: 500;
    }
    .item .meta a:hover { text-decoration: underline; }

    /* Per-item mini action buttons */
    .item .item-actions {
      margin-top: 7px;
      margin-left: 26px;
      display: flex;
      gap: 5px;
      flex-wrap: wrap;
    }
    .btn-mini {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-family: inherit;
      font-size: 10px;
      font-weight: 500;
      letter-spacing: 0.02em;
      padding: 4px 9px;
      border-radius: 5px;
      cursor: pointer;
      transition: all 0.18s ease;
      text-decoration: none;
      line-height: 1.2;
      background: white;
      color: var(--teal-dark);
      border: 1px solid rgba(91, 166, 173, 0.3);
    }
    .btn-mini:hover {
      background: var(--teal);
      color: white;
      border-color: var(--teal);
      box-shadow: 0 0 0 3px var(--teal-soft);
      transform: translateY(-1px);
    }
    .btn-mini.copied {
      background: var(--teal-dark);
      color: white;
      border-color: var(--teal-dark);
    }
    .outreach-text { display: none; }

    /* PREDICTORS */
    .predictor .stack-label {
      display: inline-block;
      font-size: 9px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 3px;
      margin-left: 8px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      vertical-align: middle;
    }
    .stack-label.stacked {
      background: linear-gradient(135deg, var(--teal) 0%, var(--teal-dark) 100%);
      color: white;
      box-shadow: 0 0 0 1px rgba(91, 166, 173, 0.2);
    }
    .stack-label.single {
      background: var(--bg);
      color: var(--teal-dark);
      border: 1px solid var(--teal-soft);
    }
    .predictor .evidence {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 5px;
      margin-left: 26px;
      line-height: 1.45;
    }
    .predictor .evidence strong { color: var(--navy); font-weight: 600; }

    .show-more {
      width: 100%;
      padding: 9px;
      background: var(--bg);
      border: none;
      border-top: 1px solid var(--border);
      cursor: pointer;
      font-size: 10px;
      font-weight: 600;
      color: var(--navy);
      letter-spacing: 0.1em;
      text-transform: uppercase;
      font-family: inherit;
      transition: background 0.15s;
    }
    .show-more:hover { background: rgba(91, 166, 173, 0.06); }

    .empty {
      padding: 28px 16px;
      text-align: center;
      color: var(--text-muted);
      font-size: 11.5px;
      font-weight: 400;
    }

    /* ACTION CARDS — refined, modern */
    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 16px;
    }
    @media (max-width: 1000px) { .actions { grid-template-columns: 1fr; } }

    .action-card {
      padding: 16px 18px 18px 18px;
      background: var(--surface);
      border-radius: 10px;
      border: 1px solid var(--border);
      box-shadow: var(--shadow-md);
      transition: box-shadow 0.18s ease;
    }
    .action-card:hover { box-shadow: var(--shadow-lg); }
    .action-card h3 {
      margin: 0 0 4px 0;
      font-size: 13.5px;
      font-weight: 600;
      color: var(--navy);
      letter-spacing: -0.01em;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .action-card h3::before {
      content: "";
      display: inline-block;
      width: 5px; height: 5px;
      background: var(--teal);
      border-radius: 50%;
      box-shadow: 0 0 6px var(--teal-glow);
    }
    .action-card .subhead {
      font-size: 11.5px;
      color: var(--text-muted);
      margin: 4px 0 14px 13px;
      font-weight: 400;
      line-height: 1.5;
    }
    .action-card label {
      display: block;
      font-size: 9.5px;
      font-weight: 600;
      color: var(--navy);
      margin: 9px 0 4px 0;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }
    .action-card input, .action-card select {
      width: 100%;
      padding: 8px 11px;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 12px;
      font-family: inherit;
      background: white;
      color: var(--navy);
      font-weight: 400;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .action-card input::placeholder { color: #A6AFBE; font-weight: 400; }
    .action-card input:focus, .action-card select:focus {
      outline: none;
      border-color: var(--teal);
      box-shadow: 0 0 0 3px var(--teal-soft);
    }
    .action-card button {
      width: 100%;
      margin-top: 14px;
      padding: 9px 14px;
      background: linear-gradient(135deg, var(--teal) 0%, var(--teal-dark) 100%);
      color: white;
      border: none;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      transition: all 0.18s ease;
      letter-spacing: 0.04em;
      box-shadow: 0 1px 2px rgba(91, 166, 173, 0.15);
    }
    .action-card button:hover {
      box-shadow: 0 4px 16px var(--teal-glow), 0 0 0 1px var(--teal);
      transform: translateY(-1px);
    }
    .action-card button:disabled {
      background: #B8C2CC;
      cursor: not-allowed;
      box-shadow: none;
      transform: none;
    }
    .action-card .status {
      margin-top: 10px;
      padding: 8px 11px;
      border-radius: 5px;
      font-size: 11px;
      display: none;
      line-height: 1.4;
    }
    .action-card .status.ok {
      background: var(--teal-soft);
      color: var(--teal-dark);
      border-left: 2px solid var(--teal);
      display: block;
    }
    .action-card .status.err {
      background: rgba(201, 59, 43, 0.08);
      color: #8B2C20;
      border-left: 2px solid #C93B2B;
      display: block;
    }

    .footer {
      text-align: center;
      color: var(--text-muted);
      font-size: 10.5px;
      padding: 18px;
      margin-top: 8px;
      letter-spacing: 0;
      font-weight: 400;
    }

    .warn-banner {
      background: rgba(212, 162, 26, 0.08);
      color: #6B5012;
      padding: 10px 16px;
      border-left: 2px solid #D4A21A;
      margin: 0 28px 14px 28px;
      border-radius: 5px;
      font-size: 11.5px;
      line-height: 1.45;
    }
    .warn-banner strong { font-weight: 600; }
    .warn-banner code {
      background: rgba(0,0,0,0.06);
      padding: 1px 5px;
      border-radius: 2px;
      font-size: 11px;
      font-family: "SF Mono", Monaco, Consolas, monospace;
    }
  </style>
</head>
<body>

<div class="header">
  <div class="brand">
    <div class="wordmark"><span class="vma">VMA</span><span class="group">GROUP</span></div>
    <h1>Sara's Desk
      <span class="sub">Lead detection · pre-advert predictors · commercial tools</span>
    </h1>
  </div>
  <div class="meta">
    <div class="last">Last brief: {{ last_updated }}</div>
    <button onclick="refreshBrief()" id="refresh-btn">↻ Daily Refresh</button>
  </div>
</div>

{% if not has_token %}
<div class="warn-banner">
  <strong>GITHUB_TOKEN not set</strong> in your .env — the "Run and Send" buttons won't work until you add one.
  See <code>DASHBOARD_SETUP.md</code> for instructions (it's a 5-minute one-time setup).
</div>
{% endif %}

<div class="container">

  <!-- LEADS + PREDICTORS -->
  <div class="row">

    <!-- TODAY'S LEADS -->
    <div class="panel">
      <div class="panel-header">
        <h2>Today's Leads</h2>
        <span class="count">{{ leads|length }}</span>
      </div>
      <div class="panel-body">
        {% if leads %}
          {% for s in leads[:5] %}
            <div class="item">
              <span class="rank">{{ loop.index }}</span>
              <span class="title">
                {% if s.url %}<a href="{{ s.url }}" target="_blank">{{ s.title }}</a>
                {% else %}{{ s.title }}{% endif %}
              </span>
              <div class="meta">
                <span class="badge">{{ s.company or '—' }}</span>
                <span class="badge">{{ s.source }}</span>
                <span class="badge">{{ s.geo }}</span>
              </div>
              <pre class="outreach-text">{{ s.outreach }}</pre>
              <div class="item-actions">
                <button class="btn-mini copy-outreach" type="button">✉ Copy outreach</button>
                <a class="btn-mini" href="{{ s.linkedin_url }}" target="_blank">↗ LinkedIn</a>
              </div>
            </div>
          {% endfor %}
          {% if leads|length > 5 %}
          <details>
            <summary class="show-more">Show all {{ leads|length }} ▾</summary>
            {% for s in leads[5:] %}
              <div class="item">
                <span class="rank">{{ loop.index + 5 }}</span>
                <span class="title">
                  {% if s.url %}<a href="{{ s.url }}" target="_blank">{{ s.title }}</a>
                  {% else %}{{ s.title }}{% endif %}
                </span>
                <div class="meta">
                  <span class="badge">{{ s.company or '—' }}</span>
                  <span class="badge">{{ s.source }}</span>
                  <span class="badge">{{ s.geo }}</span>
                </div>
                <pre class="outreach-text">{{ s.outreach }}</pre>
                <div class="item-actions">
                  <button class="btn-mini copy-outreach" type="button">✉ Copy outreach</button>
                  <a class="btn-mini" href="{{ s.linkedin_url }}" target="_blank">↗ LinkedIn</a>
                </div>
              </div>
            {% endfor %}
          </details>
          {% endif %}
        {% else %}
          <div class="empty">No leads loaded yet. Click "Refresh from GitHub" or wait for the morning brief to land.</div>
        {% endif %}
      </div>
    </div>

    <!-- TODAY'S PREDICTORS -->
    <div class="panel">
      <div class="panel-header">
        <h2>Today's Predictors</h2>
        <span class="count">{{ predictors|length }}</span>
      </div>
      <div class="panel-body">
        {% if predictors %}
          {% for p in predictors[:5] %}
            <div class="item predictor">
              <span class="rank">{{ loop.index }}</span>
              <span class="title">{{ p.company }}</span>
              <span class="stack-label {{ 'stacked' if p.depth > 1 else 'single' }}">
                {{ 'stacked × ' ~ p.depth if p.depth > 1 else 'single' }}
              </span>
              <div class="meta">
                {% for e in p.events[:3] %}
                  <div class="evidence">
                    <strong>{{ e.trigger_label }}:</strong> {{ e.evidence[:200] }}
                    {% if e.url %} · <a href="{{ e.url }}" target="_blank">source</a>{% endif %}
                  </div>
                {% endfor %}
              </div>
              <pre class="outreach-text">{{ p.outreach }}</pre>
              <div class="item-actions">
                <button class="btn-mini copy-outreach" type="button">✉ Copy outreach</button>
                <a class="btn-mini" href="{{ p.linkedin_url }}" target="_blank">↗ LinkedIn</a>
              </div>
            </div>
          {% endfor %}
          {% if predictors|length > 5 %}
          <details>
            <summary class="show-more">Show all {{ predictors|length }} ▾</summary>
            {% for p in predictors[5:] %}
              <div class="item predictor">
                <span class="rank">{{ loop.index + 5 }}</span>
                <span class="title">{{ p.company }}</span>
                <span class="stack-label {{ 'stacked' if p.depth > 1 else 'single' }}">
                  {{ 'stacked × ' ~ p.depth if p.depth > 1 else 'single' }}
                </span>
                <div class="meta">
                  {% for e in p.events[:2] %}
                    <div class="evidence">
                      <strong>{{ e.trigger_label }}:</strong> {{ e.evidence[:140] }}
                    </div>
                  {% endfor %}
                </div>
                <pre class="outreach-text">{{ p.outreach }}</pre>
                <div class="item-actions">
                  <button class="btn-mini copy-outreach" type="button">✉ Copy outreach</button>
                  <a class="btn-mini" href="{{ p.linkedin_url }}" target="_blank">↗ LinkedIn</a>
                </div>
              </div>
            {% endfor %}
          </details>
          {% endif %}
        {% else %}
          <div class="empty">No predictors today. The pipeline only fires when actual triggers land in public sources.</div>
        {% endif %}
      </div>
    </div>

  </div>

  <!-- ACTION BOXES -->
  <div class="actions">

    <!-- PITCH PACK -->
    <div class="panel action-card">
      <h3>Pitch Pack</h3>
      <div class="subhead">Bespoke proposal to flip a contingent brief to retained.</div>
      <form id="pitch-form" onsubmit="dispatch(event, 'pitch-form', '/api/dispatch/pitch-pack')">
        <label for="pp-account">Account name</label>
        <input id="pp-account" name="account_name" placeholder="e.g. Unilever" required>
        <label for="pp-role">Role</label>
        <input id="pp-role" name="role" value="Head of Internal Communications" required>
        <button type="submit">Run and send via email</button>
        <div class="status" id="pitch-status"></div>
      </form>
    </div>

    <!-- REVERSE MATCH -->
    <div class="panel action-card">
      <h3>Reverse Match</h3>
      <div class="subhead">Turn one strong candidate into 10–15 named BD targets.</div>
      <form id="rm-form" onsubmit="dispatch(event, 'rm-form', '/api/dispatch/reverse-match')">
        <label for="rm-name">Candidate name</label>
        <input id="rm-name" name="candidate_name" placeholder="e.g. Rebecca Torres" required>
        <label for="rm-company">Current company</label>
        <input id="rm-company" name="current_company" placeholder="e.g. Vodafone" required>
        <label for="rm-title">Current title</label>
        <input id="rm-title" name="current_title" placeholder="e.g. Head of Internal Communications" required>
        <button type="submit">Run and send via email</button>
        <div class="status" id="rm-status"></div>
      </form>
    </div>

    <!-- 14-DAY CATCH-UP -->
    <div class="panel action-card">
      <h3>14-Day Catch-up</h3>
      <div class="subhead">Sweep the last fortnight for any missed leads or predictors.</div>
      <form id="sweep-form" onsubmit="dispatch(event, 'sweep-form', '/api/dispatch/sweep')">
        <label for="sw-days">Window (days)</label>
        <input id="sw-days" name="window_days" type="number" min="1" max="60" value="14" required>
        <label for="sw-mode" style="margin-top: 12px;">&nbsp;</label>
        <div style="font-size:12px;color:var(--muted);margin-top:-8px;">
          Fires the full 14-day catch-up. Both leads and predictors over the window.
        </div>
        <button type="submit">Run and send via email</button>
        <div class="status" id="sweep-status"></div>
      </form>
    </div>

  </div>

  <div class="footer">
    Sara's Desk · Local dashboard · Data refreshed from GitHub Actions artifacts.
    All sources are free public surfaces. No automation of LinkedIn account.
  </div>

</div>

<script>
async function dispatch(event, formId, url) {
  event.preventDefault();
  const form = document.getElementById(formId);
  const btn = form.querySelector('button[type=submit]');
  const status = form.querySelector('.status');
  const data = {};
  new FormData(form).forEach((v, k) => { data[k] = v; });
  data.mode = 'send';   // dashboard always fires live to Sara

  btn.disabled = true;
  btn.textContent = 'Dispatching…';
  status.className = 'status';
  status.style.display = 'none';

  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const j = await r.json();
    status.textContent = j.detail || (j.ok ? 'Dispatched.' : 'Failed.');
    status.className = 'status ' + (j.ok ? 'ok' : 'err');
  } catch (e) {
    status.textContent = 'Network error: ' + e.message;
    status.className = 'status err';
  }
  btn.disabled = false;
  btn.textContent = 'Run and send via email';
}

// Copy outreach text -> clipboard; brief "Copied" feedback on the button.
document.addEventListener('click', async (event) => {
  const btn = event.target.closest('.copy-outreach');
  if (!btn) return;
  const item = btn.closest('.item');
  const pre = item ? item.querySelector('.outreach-text') : null;
  if (!pre) return;
  const text = pre.textContent.trim();
  try {
    await navigator.clipboard.writeText(text);
    const orig = btn.textContent;
    btn.textContent = '✓ Copied';
    btn.classList.add('copied');
    setTimeout(() => {
      btn.textContent = orig;
      btn.classList.remove('copied');
    }, 1600);
  } catch (e) {
    // Fallback: select the text so user can manually copy
    const range = document.createRange();
    range.selectNodeContents(pre);
    pre.style.display = 'block';
    pre.style.whiteSpace = 'pre-wrap';
    pre.style.background = '#FAFCFD';
    pre.style.padding = '8px';
    pre.style.border = '1px solid var(--border)';
    pre.style.borderRadius = '3px';
    pre.style.marginTop = '6px';
    pre.style.fontSize = '12px';
    pre.style.fontFamily = 'inherit';
    const sel = window.getSelection();
    sel.removeAllRanges(); sel.addRange(range);
    btn.textContent = '⚠ Select & ⌘C';
  }
});

async function refreshBrief() {
  const btn = document.getElementById('refresh-btn');
  btn.disabled = true;
  btn.textContent = 'Refreshing…';
  try {
    const r = await fetch('/api/refresh', { method: 'POST' });
    const j = await r.json();
    if (j.ok) {
      window.location.reload();
    } else {
      alert(j.detail || 'Refresh failed');
      btn.disabled = false;
      btn.textContent = '↻ Refresh from GitHub';
    }
  } catch (e) {
    alert('Refresh failed: ' + e.message);
    btn.disabled = false;
    btn.textContent = '↻ Refresh from GitHub';
  }
}
</script>

</body>
</html>
"""


def main() -> int:
    print(f"\n  Sara's Desk dashboard")
    print(f"  Open: http://localhost:{PORT}")
    print(f"  GitHub token: {'configured' if GITHUB_TOKEN else 'NOT SET — buttons will fail'}")
    print(f"  Repo: {GITHUB_OWNER}/{GITHUB_REPO}")
    print(f"  Press Ctrl-C to stop.\n")
    app.run(host="127.0.0.1", port=PORT, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
