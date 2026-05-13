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
import functools
import json
import logging
import os
import sys
import zipfile
import io
from datetime import datetime, timezone
from pathlib import Path

import re
import requests
from flask import Flask, jsonify, render_template_string, request, Response
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
# Render sets PORT; locally we keep 8765 unless DASHBOARD_PORT overrides.
PORT = int(os.environ.get("PORT") or os.environ.get("DASHBOARD_PORT") or 8765)
# When DASHBOARD_PASSWORD is set (on Render), every route is HTTP-Basic-Auth
# gated. When not set (local Mac), the dashboard is wide open — which is fine
# because it's only reachable on localhost.
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "sara")


def _auth_required(f):
    """Decorator: HTTP Basic Auth gate. No-op when DASHBOARD_PASSWORD unset."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not DASHBOARD_PASSWORD:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.password != DASHBOARD_PASSWORD:
            return Response(
                "Auth required", 401,
                {"WWW-Authenticate": 'Basic realm="VMA Dashboard"'},
            )
        return f(*args, **kwargs)
    return wrapper


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
    """Download the most recent artifact (morning-brief OR fortnightly-sweep)
    and unpack into state. The sweep artifact is what backfill produces, so
    accepting either lets a single Refresh button pull the freshest data
    regardless of which workflow last ran."""
    if not GITHUB_TOKEN:
        return {"ok": False, "detail": "GITHUB_TOKEN not set in .env"}
    list_url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
                f"/actions/artifacts?per_page=20")
    try:
        r = requests.get(list_url, headers=_github_headers(), timeout=15)
        if r.status_code != 200:
            return {"ok": False, "detail": f"Artifact list: HTTP {r.status_code}"}
        artifacts = r.json().get("artifacts", [])
        # Accept both names; use the most recently-created of either.
        wanted = [a for a in artifacts
                  if a.get("name") in ("morning-brief", "fortnightly-sweep")
                  and not a.get("expired")]
        if not wanted:
            return {"ok": False, "detail": "No recent brief/sweep artifact found."}
        # GitHub returns artifacts newest-first by default
        latest = wanted[0]
        zip_url = latest["archive_download_url"]
        r2 = requests.get(zip_url, headers=_github_headers(), timeout=30)
        if r2.status_code != 200:
            return {"ok": False, "detail": f"Download: HTTP {r2.status_code}"}
        with zipfile.ZipFile(io.BytesIO(r2.content)) as zf:
            for member in zf.namelist():
                if member.endswith((".html", ".txt", ".json")):
                    zf.extract(member, STATE_DIR)
        return {"ok": True, "detail": f"Refreshed from {latest.get('name', '?')} artifact "
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
    # Enrich each lead with a personalised outreach draft + a targeted
    # LinkedIn search (role-at-company, not just company-wide flood)
    for s in data:
        s["outreach"] = draft_outreach_for_lead(s)
        s["linkedin"] = linkedin_search_for_lead(s)
    return data


def load_latest_predictive() -> list[dict]:
    """Read the persistent rolling-window pipeline. Falls back to the
    legacy latest_predictive.json if the pipeline doesn't exist yet
    (first deploy before any morning brief has populated it)."""
    from tool import predictor_pipeline
    data = predictor_pipeline.all_predictors()
    if not data:
        p = STATE_DIR / "latest_predictive.json"
        if p.exists():
            try:
                data = json.loads(p.read_text())
            except Exception:
                data = []

    today = datetime.now(timezone.utc).date().isoformat()
    for p_item in data:
        first_seen = p_item.get("first_seen") or ""
        p_item["is_new"] = first_seen.startswith(today)
        p_item.setdefault("status", "active")
        p_item.setdefault("pid", predictor_pipeline._pid(p_item.get("company", "")))
        p_item["outreach"] = draft_outreach_for_predictor(p_item)
        p_item["linkedin"] = linkedin_search_for_predictor(p_item)
    return data


# ---- Outreach message drafting -----------------------------------------
# Default copy Sara approved. Same message for every lead and every
# predictor — she just edits the (Name) placeholder per recipient.
_DEFAULT_OUTREACH = (
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


def draft_outreach_for_lead(_signal: dict) -> str:
    return _DEFAULT_OUTREACH


def draft_outreach_for_predictor(_predictor: dict) -> str:
    return _DEFAULT_OUTREACH


_JOB_TITLE_TOKENS = (
    "head of", "director of", "chief", "vp ", "vice president",
    "communications", "comms", "corporate affairs", "internal comms",
    "external comms", "pr ", "media relations", "marketing and brand",
)

# Patterns that often surround an appointee's name in a news headline
_APPOINTEE_PATTERNS = [
    re.compile(r"(?:appoints?|names?|hires?|promotes?)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)"),
    re.compile(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\s+(?:joins|appointed|promoted|named|to lead|to head)"),
    re.compile(r"new\s+(?:CCO|CEO|CHRO|chief|head of[^.]+)\s+is\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)"),
]


def _extract_appointee_name(title: str) -> str | None:
    for pat in _APPOINTEE_PATTERNS:
        m = pat.search(title or "")
        if m:
            return m.group(1).strip()
    return None


def _people_search(keywords: str) -> str:
    """LinkedIn Recruiter Talent-search URL with the keyword (role + company)
    pre-filled. Sara has Recruiter, so this drops her into the proper
    Recruiter search interface where she can refine filters (current
    company, geography, tenure, Open-to-Work, etc.) rather than the
    public global people search.
    Always loads — no slug dependencies, no 404s.
    """
    return f"https://www.linkedin.com/talent/search?keywords={quote_plus(keywords.strip())}"


def linkedin_search_for_lead(signal: dict) -> dict:
    """Two-tier URL builder.
       Tier 1 — Bright Data resolved a direct linkedin.com/in/ URL during
                the morning brief → one click, lands on the named person.
       Tier 2 — Fall back to LinkedIn global people search with a tight
                quoted-phrase query. Always loads. Top result is usually
                the right person.
    """
    if signal.get("linkedin_profile_url"):
        role = (signal.get("linkedin_profile_role") or "").strip()
        company = (signal.get("company") or "").strip()
        if role and company:
            label = f"Open {role} at {company}"
        elif company:
            label = f"Open profile at {company}"
        else:
            label = "Open profile"
        return {"label": label, "url": signal["linkedin_profile_url"]}

    company = (signal.get("company") or "").strip()
    title = (signal.get("title") or "").strip()
    kind = (signal.get("kind") or "").strip().lower()
    tlow = title.lower()

    if not company and not title:
        return {"label": "Search LinkedIn",
                "url": "https://www.linkedin.com/search/results/people/"}

    looks_like_job = (
        kind == "job"
        or (
            any(t in tlow for t in _JOB_TITLE_TOKENS)
            and any(t in tlow for t in ("communications", "comms", "pr ",
                                          "corporate affairs", "media relations"))
        )
    )

    # Leadership-change news — search the named appointee
    if kind == "leadership_change" or any(
        v in tlow for v in (" appoints ", " names ", " hired as ",
                              " new ceo", " new chief", " new chair")
    ):
        name = _extract_appointee_name(title)
        if name and company:
            return {"label": f"Search {name} at {company}",
                    "url": _people_search(f'"{name}" "{company}"')}
        if company:
            return {"label": f"Search comms appointee at {company}",
                    "url": _people_search(f'"Head of Communications" "{company}"')}
        return {"label": "Search LinkedIn",
                "url": _people_search(title[:120])}

    # Job posting — hiring manager (CHRO / HRD) at that company
    if looks_like_job and company:
        return {"label": f"Search hiring manager at {company}",
                "url": _people_search(f'"Chief People Officer" OR "CHRO" "{company}"')}

    # RNS / SEC filing / regulator — Head of Comms at that company
    if kind in ("rns", "filing", "regulator") and company:
        return {"label": f"Search Head of Comms at {company}",
                "url": _people_search(f'"Head of Communications" OR "Corporate Affairs" "{company}"')}

    # Procurement — comms lead at the buying body
    if kind == "procurement" and company:
        return {"label": f"Search Head of Comms at {company}",
                "url": _people_search(f'"Head of Communications" "{company}"')}

    # Trade press — pull the appointee name from the headline if we can
    if kind == "trade_press":
        name = _extract_appointee_name(title)
        if name and company:
            return {"label": f"Search {name} at {company}",
                    "url": _people_search(f'"{name}" "{company}"')}
        if company:
            return {"label": f"Search comms at {company}",
                    "url": _people_search(f'"Head of Communications" "{company}"')}
        return {"label": "Search LinkedIn",
                "url": _people_search(title[:120])}

    # Generic — company known, intent unclear
    if company:
        return {"label": f"Search decision-maker at {company}",
                "url": _people_search(f'"CHRO" OR "Head of Communications" "{company}"')}

    return {"label": "Search LinkedIn", "url": _people_search(title or "")}


def linkedin_search_for_predictor(p: dict) -> dict:
    """Same two-tier behaviour as leads."""
    if p.get("linkedin_profile_url"):
        role = (p.get("linkedin_profile_role") or "").strip()
        company = (p.get("company") or "").strip()
        label = f"Open {role} at {company}" if role and company else "Open profile"
        return {"label": label, "url": p["linkedin_profile_url"]}

    company = (p.get("company") or "").strip() or "your target"
    events = p.get("events") or []
    keys = {e.get("trigger_key") for e in events}

    if "comms_leader_departure" in keys:
        return {"label": f"Search CHRO at {company}",
                "url": _people_search(f'"Chief People Officer" OR "CHRO" "{company}"')}
    if "ic_platform_rfp" in keys:
        return {"label": f"Search CHRO at {company}",
                "url": _people_search(f'"Chief People Officer" OR "CHRO" "{company}"')}
    if "ipo_listing" in keys:
        return {"label": f"Search CFO at {company}",
                "url": _people_search(f'"Chief Financial Officer" OR "CFO" "{company}"')}
    if "contract_loss" in keys:
        return {"label": f"Search Head of Comms at {company}",
                "url": _people_search(f'"Head of Communications" OR "Director of Communications" "{company}"')}
    if "cfo_change" in keys:
        return {"label": f"Search Head of IR at {company}",
                "url": _people_search(f'"Head of Investor Relations" OR "IR Director" "{company}"')}
    if "ir_director_change" in keys:
        return {"label": f"Search IR Director at {company}",
                "url": _people_search(f'"Head of Investor Relations" OR "IR Director" "{company}"')}
    if "ceo_change" in keys:
        return {"label": f"Search new CEO at {company}",
                "url": _people_search(f'"Chief Executive" OR "CEO" "{company}"')}
    if "chro_change" in keys:
        return {"label": f"Search new CHRO at {company}",
                "url": _people_search(f'"Chief People Officer" OR "CHRO" "{company}"')}
    if "chair_change" in keys:
        return {"label": f"Search Chair at {company}",
                "url": _people_search(f'"Chair" OR "Chairman" "{company}"')}
    if "regulator_action" in keys:
        return {"label": f"Search CHRO at {company}",
                "url": _people_search(f'"CHRO" OR "Head of Communications" "{company}"')}
    if "mna" in keys:
        return {"label": f"Search Head of Comms at {company}",
                "url": _people_search(f'"Head of Communications" OR "Corporate Affairs" "{company}"')}
    if "restructure" in keys:
        return {"label": f"Search CHRO at {company}",
                "url": _people_search(f'"CHRO" OR "Head of Communications" "{company}"')}
    if "job_ad_cluster" in keys:
        return {"label": f"Search Head of HR at {company}",
                "url": _people_search(f'"Head of HR" OR "Head of Talent" "{company}"')}

    return {"label": f"Search {company} on LinkedIn",
            "url": _people_search(f'"{company}"')}


def last_updated() -> str:
    p = STATE_DIR / "latest_signals.json"
    if not p.exists():
        return "never"
    ts = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    return ts.strftime("%a %d %b %Y · %H:%M UTC")


# ---- Flask app ----------------------------------------------------------
app = Flask(__name__)


@app.route("/")
@_auth_required
def index():
    predictors = load_latest_predictive()
    return render_template_string(
        TEMPLATE,
        leads=load_latest_signals(),
        predictors=predictors,
        active_count=sum(1 for p in predictors if p.get("status") == "active"),
        new_count=sum(1 for p in predictors if p.get("is_new")),
        followed_up_count=sum(1 for p in predictors if p.get("status") == "followed_up"),
        dismissed_count=sum(1 for p in predictors if p.get("status") == "dismissed"),
        last_updated=last_updated(),
        has_token=bool(GITHUB_TOKEN),
        build_stamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


@app.route("/api/predictor/<pid>/status", methods=["POST"])
@_auth_required
def api_predictor_status(pid: str):
    from tool import predictor_pipeline
    data = request.get_json(force=True) or {}
    status = (data.get("status") or "").strip()
    if status not in ("active", "followed_up", "dismissed"):
        return jsonify({"ok": False, "detail": "invalid status"}), 400
    ok = predictor_pipeline.set_status(pid, status)
    if not ok:
        return jsonify({"ok": False, "detail": "predictor not found"}), 404
    return jsonify({"ok": True, "pid": pid, "status": status})


@app.route("/api/refresh", methods=["POST"])
@_auth_required
def api_refresh():
    return jsonify(refresh_latest_brief_from_github())


@app.route("/api/dispatch/brief", methods=["POST"])
@_auth_required
def api_dispatch_brief():
    """Trigger a fresh morning-brief workflow run in preview mode.
    Preview = no email sent; just generates the artifact for refresh."""
    return jsonify(trigger_workflow("morning-brief.yml", {"mode": "preview"}))


@app.route("/api/dispatch/pitch-pack", methods=["POST"])
@_auth_required
def api_pitch_pack():
    data = request.get_json(force=True) or {}
    inputs = {
        "account_name": (data.get("account_name") or "").strip(),
        "role": (data.get("role") or "Head of Internal Communications").strip(),
        "mode": data.get("mode", "send"),
        "salary_min": (data.get("salary_min") or "").strip(),
        "salary_max": (data.get("salary_max") or "").strip(),
    }
    if not inputs["account_name"]:
        return jsonify({"ok": False, "detail": "Account name required"}), 400
    # Validate salary overrides if provided
    for k in ("salary_min", "salary_max"):
        if inputs[k]:
            try:
                int(inputs[k])
            except ValueError:
                return jsonify({"ok": False,
                                "detail": f"{k} must be a whole number (e.g. 95000)"}), 400
    return jsonify(trigger_workflow("pitch-pack.yml", inputs))


@app.route("/api/dispatch/reverse-match", methods=["POST"])
@_auth_required
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
@_auth_required
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
  <title>VMA Group</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Crimson+Pro:ital,wght@0,400;0,500;0,600;1,400;1,500&display=swap" rel="stylesheet">
  <style>
    :root {
      /* Claude-warmth palette. Existing variable names preserved so the
         rest of the dashboard CSS just inherits the new colours — only
         the values change here. "navy" now maps to deep warm ink,
         "teal" remaps to Anthropic coral. */
      --navy: #181613;
      --navy-deep: #0F0D0B;
      --navy-soft: rgba(24, 22, 19, 0.06);
      --navy-hairline: rgba(24, 22, 19, 0.10);
      --teal: #C96442;
      --teal-bright: #E6764E;
      --teal-dark: #A04E32;
      --teal-glow: rgba(201, 100, 66, 0.34);
      --teal-soft: rgba(201, 100, 66, 0.09);
      --bg: #F5F0E8;
      --bg-warm: #EDE5D6;
      --surface: #FFFFFF;
      --surface-elevated: #FBF7EF;
      --border: rgba(140, 120, 80, 0.18);
      --border-hover: rgba(201, 100, 66, 0.36);
      --text: #181613;
      --text-muted: #7A7164;
      --text-dim: #B7AC9A;
      --gold: #C49A3B;
      --green: #6B8C3B;
      --shadow-sm: 0 1px 2px rgba(140, 120, 80, 0.06);
      --shadow-md: 0 4px 14px rgba(140, 120, 80, 0.10), 0 1px 3px rgba(140, 120, 80, 0.06);
      --shadow-lg: 0 10px 36px rgba(140, 120, 80, 0.14);
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; }
    body {
      font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      background-color: var(--bg);
      background-image:
        radial-gradient(ellipse 700px 500px at 8% 12%, rgba(201, 100, 66, 0.07), transparent 60%),
        radial-gradient(ellipse 600px 420px at 92% 92%, rgba(196, 154, 59, 0.08), transparent 60%);
      background-attachment: fixed;
      color: var(--text);
      line-height: 1.5;
      font-weight: 400;
      font-size: 13.5px;
      font-feature-settings: "ss01", "cv11", "cv02", "cv03";
      -webkit-font-smoothing: antialiased;
      letter-spacing: -0.005em;
    }
    .serif { font-family: "Crimson Pro", Georgia, serif; }

    /* TOP BAR — centred VMA logo on the cream background. */
    .top-bar {
      max-width: 1280px;
      margin: 0 auto;
      padding: 22px 28px 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-bottom: 1px solid var(--border);
    }
    .top-bar .logo {
      display: block;
      height: 40px;
      width: auto;
    }
    @media (max-width: 720px) {
      .top-bar { padding: 16px 18px 10px; }
      .top-bar .logo { height: 32px; }
    }

    .container {
      max-width: 1280px;
      margin: 0 auto;
      padding: 22px 28px 16px 28px;
    }

    /* DAILY REFRESH BAR — sits above the panels, primary focal CTA */
    .refresh-bar {
      display: flex;
      align-items: center;
      gap: 18px;
      padding: 14px 18px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      box-shadow: var(--shadow-md);
      margin-bottom: 18px;
      position: relative;
      overflow: hidden;
    }
    .refresh-bar::before {
      content: "";
      position: absolute;
      top: 0; left: 0; bottom: 0;
      width: 4px;
      background: linear-gradient(180deg, var(--teal-bright) 0%, var(--teal-dark) 100%);
    }
    .big-refresh {
      background: linear-gradient(135deg, var(--teal-bright) 0%, var(--teal) 55%, var(--teal-dark) 100%);
      color: white;
      border: 1px solid var(--teal-bright);
      padding: 11px 22px;
      border-radius: 7px;
      font-family: inherit;
      font-size: 13px;
      font-weight: 600;
      letter-spacing: 0.02em;
      cursor: pointer;
      transition: all 0.2s ease;
      box-shadow:
        0 3px 12px var(--teal-glow),
        0 0 0 1px rgba(91, 166, 173, 0.5),
        inset 0 1px 0 rgba(255, 255, 255, 0.2);
      text-shadow: 0 1px 1px rgba(0, 0, 0, 0.12);
      white-space: nowrap;
      margin-left: 6px;   /* clears the left accent stripe */
    }
    .big-refresh:hover {
      transform: translateY(-1px);
      box-shadow:
        0 8px 24px var(--teal-glow),
        0 0 0 1px var(--teal-bright),
        inset 0 1px 0 rgba(255, 255, 255, 0.3);
      filter: brightness(1.05);
    }
    .big-refresh:active {
      transform: translateY(0);
      filter: brightness(0.96);
    }
    .big-refresh:disabled {
      background: #B8C2CC;
      box-shadow: none;
      cursor: not-allowed;
      transform: none;
      filter: none;
    }
    .refresh-meta {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .refresh-label {
      font-size: 13px;
      font-weight: 600;
      color: var(--navy);
      letter-spacing: -0.005em;
    }
    .refresh-sub {
      font-size: 11px;
      color: var(--text-muted);
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
    .window-badge {
      display: inline-block;
      font-size: 9.5px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 3px;
      margin-left: 6px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      vertical-align: middle;
      background: rgba(14, 40, 69, 0.04);
      color: var(--navy);
      border: 1px solid var(--navy-soft);
    }
    .predictor .evidence {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 5px;
      margin-left: 26px;
      line-height: 1.45;
    }
    .predictor .evidence strong { color: var(--navy); font-weight: 600; }

    /* Predicted role + probability chips on the row summary */
    .role-chip {
      display: inline-flex;
      align-items: center;
      padding: 2px 9px;
      font-size: 11px;
      font-weight: 500;
      letter-spacing: -0.005em;
      color: var(--teal-dark);
      background: rgba(201, 100, 66, 0.08);
      border: 1px solid rgba(201, 100, 66, 0.22);
      border-radius: 8px;
    }
    .prob-chip {
      display: inline-flex;
      align-items: center;
      padding: 2px 9px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.02em;
      color: var(--text);
      background: var(--surface-elevated);
      border: 1px solid var(--border);
      border-radius: 8px;
      font-variant-numeric: tabular-nums;
    }
    .panel-header h2 .window-sub {
      font-weight: 400;
      color: var(--text-muted);
      font-size: 12px;
      letter-spacing: 0;
      margin-left: 4px;
    }

    /* PIPELINE — NEW badge, status badges, filter pills */
    .new-badge {
      display: inline-block;
      font-size: 9px;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 3px;
      margin-left: 8px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      vertical-align: middle;
      background: linear-gradient(135deg, #ff6b35 0%, #f7931e 100%);
      color: white;
      box-shadow: 0 0 0 1px rgba(255, 107, 53, 0.2);
    }
    .status-badge {
      display: inline-block;
      font-size: 9px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 3px;
      margin-left: 6px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      vertical-align: middle;
    }
    .status-badge.followed-up {
      background: rgba(34, 139, 87, 0.1);
      color: #228b57;
      border: 1px solid rgba(34, 139, 87, 0.3);
    }
    .status-badge.dismissed {
      background: rgba(120, 120, 120, 0.08);
      color: #888;
      border: 1px solid rgba(120, 120, 120, 0.25);
    }
    .item.predictor[data-status="dismissed"] { opacity: 0.55; }
    .item.predictor[data-status="followed_up"] { opacity: 0.8; }

    .filter-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      background: var(--bg);
    }
    .filter-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 11px;
      font-family: inherit;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.02em;
      color: var(--navy);
      background: white;
      border: 1px solid var(--border);
      border-radius: 14px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .filter-pill:hover {
      border-color: var(--teal);
      color: var(--teal-dark);
    }
    .filter-pill.active {
      background: var(--navy);
      color: white;
      border-color: var(--navy);
    }
    .filter-pill .pill-count {
      display: inline-block;
      font-size: 10px;
      padding: 1px 6px;
      background: rgba(255,255,255,0.18);
      border-radius: 10px;
      font-weight: 700;
    }
    .filter-pill:not(.active) .pill-count {
      background: rgba(14, 40, 69, 0.08);
      color: var(--navy);
    }
    .btn-mini.ghost {
      background: transparent;
      color: var(--text-muted);
      border-color: var(--border);
    }
    .btn-mini.ghost:hover {
      color: #c93737;
      border-color: #c93737;
      background: rgba(201, 55, 55, 0.05);
      box-shadow: 0 0 0 3px rgba(201, 55, 55, 0.08);
    }
    /* Compact predictor rows — single-line summary, expand for full detail */
    .panel-body.compact .item.predictor {
      padding: 9px 14px;
      cursor: pointer;
      transition: background 0.12s;
    }
    .panel-body.compact .item.predictor:hover {
      background: rgba(91, 166, 173, 0.04);
    }
    .item.predictor .row-summary {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 6px;
    }
    .item.predictor .row-summary .title {
      flex: 1;
      min-width: 0;
    }
    .item.predictor .expand-toggle {
      color: var(--text-muted);
      font-size: 14px;
      transition: transform 0.2s ease;
      user-select: none;
    }
    .item.predictor.expanded .expand-toggle {
      transform: rotate(180deg);
    }
    .item.predictor .row-preview {
      display: block;
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 4px;
      margin-left: 26px;
      line-height: 1.45;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .item.predictor .row-preview .more-count {
      color: var(--teal-dark);
      font-weight: 600;
    }
    .item.predictor .row-details {
      display: none;
      margin-top: 8px;
    }
    .item.predictor.expanded .row-preview {
      display: none;
    }
    .item.predictor.expanded .row-details {
      display: block;
    }
    .empty.compact {
      padding: 14px 16px;
      font-size: 12px;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 10px;
    }

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
    .action-card .salary-row-label {
      display: block;
      margin-top: 4px;
      margin-bottom: 4px;
      font-size: 10px;
      font-weight: 600;
      color: var(--text-muted);
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .action-card .salary-row {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .action-card .salary-row input {
      margin-bottom: 4px;
    }
    .action-card .salary-row .dash {
      color: var(--text-muted);
      font-weight: 500;
    }
    .action-card .hint {
      font-size: 11px;
      color: var(--text-muted);
      margin-bottom: 10px;
      font-style: italic;
    }
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

<header class="top-bar">
  <img src="/static/vma_logo.svg" alt="VMA Group" class="logo">
</header>

{% if not has_token %}
<div class="warn-banner">
  <strong>GITHUB_TOKEN not set</strong> in your .env — the "Run and Send" buttons won't work until you add one.
  See <code>DASHBOARD_SETUP.md</code> for instructions (it's a 5-minute one-time setup).
</div>
{% endif %}

<div class="container">

  <!-- DAILY REFRESH BAR — primary CTA, sits above the leads/predictors -->
  <div class="refresh-bar">
    <button onclick="refreshBrief()" id="refresh-btn" class="big-refresh">
      ↻ Daily Refresh
    </button>
    <div class="refresh-meta">
      <span class="refresh-label">Pull today's freshly-generated brief</span>
      <span class="refresh-sub">Last refreshed: {{ last_updated }}</span>
    </div>
  </div>

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
                <a class="btn-mini" href="{{ s.linkedin.url }}" target="_blank" title="{{ s.linkedin.label }}">↗ {{ s.linkedin.label }}</a>
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
                  <a class="btn-mini" href="{{ s.linkedin.url }}" target="_blank" title="{{ s.linkedin.label }}">↗ {{ s.linkedin.label }}</a>
                </div>
              </div>
            {% endfor %}
          </details>
          {% endif %}
        {% else %}
          <div class="empty">No leads loaded yet. Click Daily Refresh.</div>
        {% endif %}
      </div>
    </div>

    <!-- PREDICTOR PIPELINE (rolling 90-day forward window, auto-populated) -->
    <div class="panel">
      <div class="panel-header">
        <h2>Predicted Briefs <span class="window-sub">· next 90 days</span></h2>
        <span class="count">{{ active_count }}</span>
      </div>
      <div class="filter-bar">
        <button class="filter-pill active" data-filter="active">Active <span class="pill-count">{{ active_count }}</span></button>
        <button class="filter-pill" data-filter="new">New today <span class="pill-count">{{ new_count }}</span></button>
        <button class="filter-pill" data-filter="followed_up">Followed up <span class="pill-count">{{ followed_up_count }}</span></button>
        <button class="filter-pill" data-filter="dismissed">Dismissed <span class="pill-count">{{ dismissed_count }}</span></button>
        <button class="filter-pill" data-filter="all">All</button>
      </div>
      <div class="panel-body compact" id="predictor-list">
        {% if predictors %}
          {% for p in predictors %}
            <div class="item predictor" data-pid="{{ p.pid }}" data-status="{{ p.status }}" data-new="{{ '1' if p.is_new else '0' }}">
              <div class="row-summary">
                <span class="rank">{{ loop.index }}</span>
                <span class="title">{{ p.company }}</span>
                {% if p.predicted_role %}<span class="role-chip">{{ p.predicted_role }}</span>{% endif %}
                {% if p.probability %}<span class="prob-chip">{{ p.probability }}%</span>{% endif %}
                {% if p.is_new %}<span class="new-badge">NEW</span>{% endif %}
                {% if p.window_label %}<span class="window-badge">{{ p.window_label }}</span>{% endif %}
                {% if p.depth > 1 %}<span class="stack-label stacked">stacked × {{ p.depth }}</span>{% endif %}
                {% if p.status == 'followed_up' %}<span class="status-badge followed-up">✓ followed up</span>{% endif %}
                {% if p.status == 'dismissed' %}<span class="status-badge dismissed">dismissed</span>{% endif %}
                <span class="expand-toggle">▾</span>
              </div>
              <div class="row-preview">
                {% if p.events %}{{ p.events[0].trigger_label }}: {{ p.events[0].evidence[:140] }}{% if p.events|length > 1 %} <span class="more-count">+{{ p.events|length - 1 }} more</span>{% endif %}{% endif %}
              </div>
              <div class="row-details">
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
                  <a class="btn-mini" href="{{ p.linkedin.url }}" target="_blank" title="{{ p.linkedin.label }}">↗ {{ p.linkedin.label }}</a>
                  {% if p.status == 'active' %}
                    <button class="btn-mini status-action" data-status="followed_up" type="button">✓ Mark followed up</button>
                    <button class="btn-mini status-action ghost" data-status="dismissed" type="button">✕ Dismiss</button>
                  {% else %}
                    <button class="btn-mini status-action" data-status="active" type="button">↺ Restore</button>
                  {% endif %}
                </div>
              </div>
            </div>
          {% endfor %}
        {% else %}
          <div class="empty compact">Pipeline empty. Click Daily Refresh, or wait for tomorrow's morning brief — the 90-day window populates automatically.</div>
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
        <input id="pp-role" name="role" placeholder="e.g. Head of Internal Communications" required>
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
        <button type="submit">Run and send via email</button>
        <div class="status" id="sweep-status"></div>
      </form>
    </div>

  </div>

  <div class="footer">
    Data refreshed from GitHub Actions artifacts.
    All sources are free public surfaces. No automation of LinkedIn account.
    <span style="opacity:0.5; margin-left:8px;">· build {{ build_stamp }}</span>
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

// Pipeline filter pills: show only items matching the chosen filter.
function applyFilter(name) {
  document.querySelectorAll('.filter-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.filter === name);
  });
  document.querySelectorAll('#predictor-list .item.predictor').forEach(item => {
    const status = item.dataset.status || 'active';
    const isNew = item.dataset.new === '1';
    let show = false;
    if (name === 'all') show = true;
    else if (name === 'new') show = isNew && status === 'active';
    else if (name === 'active') show = status === 'active';
    else show = status === name;
    item.style.display = show ? '' : 'none';
  });
}
document.addEventListener('click', (event) => {
  const pill = event.target.closest('.filter-pill');
  if (!pill) return;
  applyFilter(pill.dataset.filter);
});

// Expand / collapse predictor rows on summary click. Buttons and links
// inside the row stop propagation so they don't trigger the toggle.
document.addEventListener('click', (event) => {
  const summary = event.target.closest('.row-summary');
  if (!summary) return;
  // Don't toggle if the user clicked a button or link inside the summary
  if (event.target.closest('button, a')) return;
  const item = summary.closest('.item.predictor');
  if (!item) return;
  item.classList.toggle('expanded');
});

// Predictor status actions: mark followed up / dismiss / restore.
// Updates the item in place — no page reload. On Render free tier the
// container's filesystem is ephemeral, so status changes reset on cold
// start (~15 min idle). The next morning brief rebuilds the pipeline.
document.addEventListener('click', async (event) => {
  const btn = event.target.closest('.status-action');
  if (!btn) return;
  const item = btn.closest('.item.predictor');
  if (!item) return;
  const pid = item.dataset.pid;
  const newStatus = btn.dataset.status;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '…';
  try {
    const r = await fetch(`/api/predictor/${encodeURIComponent(pid)}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    });
    const j = await r.json();
    if (!j.ok) { alert(j.detail || 'Update failed'); btn.disabled = false; btn.textContent = orig; return; }
    item.dataset.status = newStatus;
    // Re-render the action row so buttons match the new status
    const actions = item.querySelector('.item-actions');
    if (actions) {
      const copy = actions.querySelector('.copy-outreach');
      const linkedin = actions.querySelector('a.btn-mini');
      actions.innerHTML = '';
      if (copy) actions.appendChild(copy);
      if (linkedin) actions.appendChild(linkedin);
      if (newStatus === 'active') {
        actions.insertAdjacentHTML('beforeend',
          '<button class="btn-mini status-action" data-status="followed_up" type="button">✓ Mark followed up</button>' +
          '<button class="btn-mini status-action ghost" data-status="dismissed" type="button">✕ Dismiss</button>');
      } else {
        actions.insertAdjacentHTML('beforeend',
          '<button class="btn-mini status-action" data-status="active" type="button">↺ Restore</button>');
      }
    }
    // Add/remove the status badge inline
    item.querySelectorAll('.status-badge').forEach(b => b.remove());
    if (newStatus === 'followed_up') {
      item.querySelector('.title').insertAdjacentHTML('afterend',
        '<span class="status-badge followed-up">✓ followed up</span>');
    } else if (newStatus === 'dismissed') {
      item.querySelector('.title').insertAdjacentHTML('afterend',
        '<span class="status-badge dismissed">dismissed</span>');
    }
    // Re-apply current filter to hide/show row appropriately
    const activePill = document.querySelector('.filter-pill.active');
    if (activePill) applyFilter(activePill.dataset.filter);
  } catch (e) {
    alert('Update failed: ' + e.message);
    btn.disabled = false;
    btn.textContent = orig;
  }
});

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

// Daily Refresh: dispatches a fresh morning brief workflow on GitHub
// Actions (preview mode — no email), waits ~6 minutes for it to
// generate the latest leads + predictors, then pulls the artifact and
// reloads the page. One button, predictable behaviour, guaranteed
// fresh data every click. Countdown state persists in localStorage so
// reloading mid-run resumes the timer instead of resetting.
async function refreshBrief() {
  const btn = document.getElementById('refresh-btn');
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Dispatching…';
  try {
    const r = await fetch('/api/dispatch/brief', { method: 'POST' });
    const j = await r.json();
    if (!j.ok) {
      alert(j.detail || 'Refresh dispatch failed');
      btn.disabled = false;
      btn.textContent = originalLabel;
      return;
    }
    const startedAt = Date.now();
    localStorage.setItem('refreshDispatchedAt', String(startedAt));
    runRefreshCountdown(startedAt, btn, originalLabel);
  } catch (e) {
    alert('Refresh dispatch failed: ' + e.message);
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

function runRefreshCountdown(startedAt, btn, originalLabel) {
  const totalSecs = 6 * 60;   // brief runs 5-10 min in 90-day sweep
  const tick = () => {
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    const remaining = Math.max(0, totalSecs - elapsed);
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    btn.textContent = `⏱ Refreshing · ${m}:${String(s).padStart(2,'0')}`;
    if (remaining > 0) {
      setTimeout(tick, 1000);
    } else {
      btn.textContent = '↻ Pulling fresh data…';
      fetch('/api/refresh', { method: 'POST' })
        .then(r => r.json())
        .then(j => {
          localStorage.removeItem('refreshDispatchedAt');
          if (j.ok) {
            window.location.reload();
          } else {
            alert('Brief generated but the artifact pull failed: '
                  + (j.detail || 'unknown')
                  + '\\n\\nWait 30 seconds and click Daily Refresh again.');
            btn.disabled = false;
            btn.textContent = originalLabel;
          }
        })
        .catch(() => {
          btn.disabled = false;
          btn.textContent = originalLabel;
        });
    }
  };
  tick();
}

// Resume any in-flight refresh countdown after a page reload
(function resumeRefreshIfNeeded() {
  const startedAt = parseInt(localStorage.getItem('refreshDispatchedAt') || '0', 10);
  if (!startedAt) return;
  // Stale guard: if >12 minutes have passed, the dispatched workflow
  // is long done. Clear and let the user click again to fetch fresh.
  if (Date.now() - startedAt > 12 * 60 * 1000) {
    localStorage.removeItem('refreshDispatchedAt');
    return;
  }
  const btn = document.getElementById('refresh-btn');
  if (!btn) return;
  const originalLabel = btn.textContent;
  btn.disabled = true;
  runRefreshCountdown(startedAt, btn, originalLabel);
})();
</script>

</body>
</html>
"""


def main() -> int:
    print(f"\n  Account Director Dashboard")
    print(f"  Open: http://localhost:{PORT}")
    print(f"  GitHub token: {'configured' if GITHUB_TOKEN else 'NOT SET — buttons will fail'}")
    print(f"  Auth gate:    {'ON (DASHBOARD_PASSWORD set)' if DASHBOARD_PASSWORD else 'OFF (open locally)'}")
    print(f"  Repo: {GITHUB_OWNER}/{GITHUB_REPO}")
    print(f"  Press Ctrl-C to stop.\n")
    # 0.0.0.0 so the dev server is reachable on Render too. Locally only
    # the loopback interface listens unless Sara explicitly LAN-shares,
    # so binding 0.0.0.0 doesn't change the local security posture.
    app.run(host="0.0.0.0", port=PORT, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
