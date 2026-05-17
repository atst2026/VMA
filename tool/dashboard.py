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


def _register_json_error_handlers(app):
    """API routes should return JSON errors, not Flask's HTML default.
    Wired below right after `app = Flask(__name__)`."""
    from werkzeug.exceptions import HTTPException

    @app.errorhandler(HTTPException)
    def _http_err(e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "detail": e.description, "code": e.code}), e.code
        return e

    @app.errorhandler(Exception)
    def _unhandled(e):
        log.exception("Unhandled error on %s", request.path)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "detail": "internal error", "code": 500}), 500
        raise e


def _safe_json_body() -> dict:
    """Return the request JSON body as a dict, always. Defends against:
      - empty body / non-JSON body                → {} (Flask raises 400
        when Content-Type=application/json and body is invalid; we catch
        that and treat as empty)
      - non-object JSON (lists, numbers, strings) → {} (caller does
        .get(...) which would otherwise AttributeError)
      - null                                       → {}
    Always returns a dict so downstream `.get(...)` is safe.
    """
    try:
        data = request.get_json(force=True, silent=True)
    except Exception:
        data = None
    return data if isinstance(data, dict) else {}


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
    and unpack into state. Returns ok/detail PLUS counts of what landed so
    the UI can surface the truth: are we showing 0 leads because the
    workflow produced 0, or because the artifact didn't contain the file?"""
    if not GITHUB_TOKEN:
        return {"ok": False, "detail": "GITHUB_TOKEN not set in .env"}
    list_url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
                f"/actions/artifacts?per_page=20")
    try:
        r = requests.get(list_url, headers=_github_headers(), timeout=15)
        if r.status_code != 200:
            return {"ok": False, "detail": f"Artifact list: HTTP {r.status_code}"}
        artifacts = r.json().get("artifacts", [])
        wanted = [a for a in artifacts
                  if a.get("name") in ("morning-brief", "fortnightly-sweep")
                  and not a.get("expired")]
        if not wanted:
            return {"ok": False, "detail": "No recent brief/sweep artifact found on GitHub Actions. "
                                            "Trigger a brief manually: Actions tab → 'Sara's Morning Brief' → Run workflow."}
        latest = wanted[0]
        zip_url = latest["archive_download_url"]
        r2 = requests.get(zip_url, headers=_github_headers(), timeout=30)
        if r2.status_code != 200:
            return {"ok": False, "detail": f"Download: HTTP {r2.status_code}"}
        extracted = []
        with zipfile.ZipFile(io.BytesIO(r2.content)) as zf:
            for member in zf.namelist():
                if not member.endswith((".html", ".txt", ".json")):
                    continue
                # Strip any path prefix and write the file directly into
                # STATE_DIR. actions/upload-artifact v4 may preserve the
                # tool/state/ prefix in the zip; without this, files would
                # land in STATE_DIR/tool/state/* (doubled prefix) and the
                # loader would not find them.
                basename = member.rsplit("/", 1)[-1]
                with zf.open(member) as src, open(STATE_DIR / basename, "wb") as dst:
                    dst.write(src.read())
                extracted.append(member)

        # Count what we got so the user can SEE whether the artifact has data
        leads_n, predictors_n = 0, 0
        try:
            sigs = json.loads((STATE_DIR / "latest_signals.json").read_text())
            leads_n = len(sigs) if isinstance(sigs, list) else 0
        except Exception:
            pass
        try:
            preds = json.loads((STATE_DIR / "latest_predictive.json").read_text())
            predictors_n = len(preds) if isinstance(preds, list) else 0
        except Exception:
            pass

        # Match by basename (filename only) so we don't depend on whether
        # actions/upload-artifact v4 preserves the tool/state/ prefix.
        basenames = {f.rsplit("/", 1)[-1] for f in extracted}
        has_signals_file = "latest_signals.json" in basenames
        has_brief_html = "latest_brief.html" in basenames
        has_pipeline = "predictor_pipeline.json" in basenames
        ts = (latest.get("created_at", "?") or "")[:16].replace("T", " ")

        # Returned in the JSON for diagnostics, not surfaced in the UI.
        file_list = sorted(set(
            f.replace("tool/state/", "") for f in extracted
            if f.endswith((".html", ".txt", ".json"))
        ))

        detail = (f"Pulled today's brief — {leads_n} leads, "
                  f"{predictors_n} predictors.")
        if not has_brief_html:
            detail += (" ⚠ Brief script likely crashed before writing any state - "
                       "check the GitHub Actions run logs for that workflow.")
        elif leads_n == 0 and not has_signals_file:
            detail += (" ⚠ Artifact has the rendered brief but no signals.json. "
                       "Either the workflow YAML was old at dispatch time, OR the brief "
                       "script crashed between rendering and saving the signals JSON.")
        elif leads_n == 0:
            detail += (" ⚠ Today's brief found 0 leads (dedup filtered everything, "
                       "or no new jobs matched Sara's criteria).")
        return {"ok": True, "detail": detail, "leads": leads_n, "predictors": predictors_n,
                "artifact_name": latest.get("name"), "artifact_created_at": latest.get("created_at"),
                "files": file_list}
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
    # The uniform sequence, identical for every lead regardless of kind:
    # resolve best-available contact (+ confidence) -> personalised draft
    # -> one precise LinkedIn click.
    from tool.hiring_manager import resolve_lead_contact
    try:
        from tool.contacts.store import load_contacts
        _cc = load_contacts()
    except Exception:
        _cc = {}
    for s in data:
        s["contact"] = resolve_lead_contact(s, contacts=_cc)
        s["outreach"] = draft_outreach_for_lead(s)
        s["linkedin"] = linkedin_click(s)
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

    from tool.advisory import advisory_for
    today = datetime.now(timezone.utc).date().isoformat()
    for p_item in data:
        first_seen = p_item.get("first_seen") or ""
        p_item["is_new"] = first_seen.startswith(today)
        p_item.setdefault("status", "active")
        p_item.setdefault("pid", predictor_pipeline._pid(p_item.get("company", "")))
        p_item["outreach"] = draft_outreach_for_predictor(p_item)
        p_item["linkedin"] = linkedin_search_for_predictor(p_item)
        evs = p_item.get("events") or []
        p_item["advisory"] = advisory_for(
            evs[0].get("trigger_key") if evs and isinstance(evs[0], dict) else None
        )
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


def _display_role(title: str) -> str:
    """Strip an embedded company / region suffix off a scraped job title
    so it reads naturally inside a sentence, keeping original casing."""
    import re as _re
    t = (title or "").strip()
    t = _re.split(r"\s+[–—-]\s+", t)[0]
    t = _re.split(r"\s+at\s+[A-Z]", t)[0]
    return t.split(",")[0].strip()


def draft_outreach_for_lead(signal: dict) -> str:
    """Personalised draft for ANY lead, off the uniform contact resolver
    (signal['contact'])."""
    c = signal.get("contact") or {}
    company = (signal.get("company") or "").strip()
    if not company:
        return _DEFAULT_OUTREACH
    from tool.hiring_manager import is_job_like
    name = (c.get("name") or "").strip()
    first = name.split()[0] if name else ""
    title = c.get("title") or "Head of Communications"
    if c.get("basis") == "appointee":
        body = (f"Congratulations on the move to {company} — I saw the "
                "announcement. I'd love to introduce VMA Group as you "
                "settle in and build out the team.")
    elif is_job_like(signal):
        role = _display_role(signal.get("title") or "")
        what = f"for the {role} role" if role else "across its comms team"
        body = (f"I saw {company} is hiring {what}. As {company}'s {title} "
                "you're likely shaping that hire, so I'm reaching out "
                "directly.")
    else:
        body = (f"I've been following developments at {company} — as its "
                f"{title} you're the natural person to connect with.")
    return (
        f"Hi {first or '(Name)'}, I'm Sara from VMA Group.\n\n"
        "We specialise in executive search and recruitment across "
        f"corporate communications, internal comms and marketing. {body}\n\n"
        "I'd love to grab a coffee in the next couple of weeks to share "
        "what we're seeing in the market. I've attached our brochure in "
        "case it's useful.\n\n"
        "Would be great to connect.\n\n"
        "Best,\n"
        "Sara"
    )


def draft_outreach_for_predictor(_predictor: dict) -> str:
    return _DEFAULT_OUTREACH


def _people_search(keywords: str) -> str:
    """LinkedIn Recruiter Talent-search URL with the keyword (role + company)
    pre-filled. Sara has Recruiter, so this drops her into the proper
    Recruiter search interface where she can refine filters (current
    company, geography, tenure, Open-to-Work, etc.) rather than the
    public global people search.
    Always loads — no slug dependencies, no 404s.
    """
    return f"https://www.linkedin.com/talent/search?keywords={quote_plus(keywords.strip())}"


def linkedin_click(signal: dict) -> dict:
    """One uniform click for EVERY lead, off signal['contact']:
    a resolved /in/ profile if we have one, else a precise name+company
    search, else a precise role+company search. Never a dead end."""
    c = signal.get("contact") or {}
    company = (signal.get("company") or "").strip()
    name = (c.get("name") or "").strip()
    title = c.get("title") or "Head of Communications"
    direct = signal.get("linkedin_profile_url") or c.get("linkedin_url")
    if direct:
        url = direct
    elif not company:
        url = _people_search(name or title)
    elif name:
        url = _people_search(f'"{name}" "{company}"')
    else:
        url = _people_search(f'"{title}" "{company}"')
    return {"label": "LinkedIn", "url": url}


def linkedin_search_for_predictor(p: dict) -> dict:
    """Three-tier: direct profile -> seeded name search -> role search."""
    if p.get("linkedin_profile_url"):
        role = (p.get("linkedin_profile_role") or "").strip()
        company = (p.get("company") or "").strip()
        label = f"Open {role} at {company}" if role and company else "Open profile"
        return {"label": label, "url": p["linkedin_profile_url"]}

    # Tier 1b: search by the seeded contact's actual name.
    seeded_name = (p.get("seeded_contact_name") or "").strip()
    company = (p.get("company") or "").strip()
    if seeded_name and company:
        role_label = p.get("seeded_contact_role") or "contact"
        return {"label": f"Search {seeded_name} ({role_label}) at {company}",
                "url": _people_search(f'"{seeded_name}" "{company}"')}

    company = company or "your target"
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
    if "regulator_action" in keys or "regulator_probe_early" in keys or "crisis_event" in keys:
        return {"label": f"Search CHRO at {company}",
                "url": _people_search(f'"CHRO" OR "Head of Communications" "{company}"')}
    if "profit_warning" in keys:
        return {"label": f"Search IR Director at {company}",
                "url": _people_search(f'"Investor Relations" OR "Corporate Affairs" "{company}"')}
    if "mna" in keys:
        return {"label": f"Search Head of Comms at {company}",
                "url": _people_search(f'"Head of Communications" OR "Corporate Affairs" "{company}"')}
    if "restructure" in keys:
        return {"label": f"Search CHRO at {company}",
                "url": _people_search(f'"CHRO" OR "Head of Communications" "{company}"')}
    if "press_velocity_spike" in keys:
        return {"label": f"Search Head of Comms at {company}",
                "url": _people_search(f'"Head of Communications" OR "Corporate Affairs" "{company}"')}
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


def _deploy_rev() -> str:
    """Short commit of the running code, so a deploy can be confirmed at
    a glance. Render exposes RENDER_GIT_COMMIT; fall back to local git."""
    rev = (os.environ.get("RENDER_GIT_COMMIT") or "").strip()
    if rev:
        return rev[:7]
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).resolve().parent),
            stderr=subprocess.DEVNULL, timeout=2).decode().strip() or "unknown"
    except Exception:
        return "unknown"


_DEPLOY_REV = _deploy_rev()


# ---- Flask app ----------------------------------------------------------
app = Flask(__name__)
_register_json_error_handlers(app)


@app.template_filter("safe_url")
def _safe_url_filter(u):
    """Jinja-side counterpart of the JS safeUrl(): rewrite URLs to '#'
    unless they're http(s) or mailto. Defends against javascript:/data:
    URLs that could appear in upstream RSS/GDELT signals and execute on
    click when rendered server-side in Today's Leads / Predictor rows."""
    if not u:
        return "#"
    s = str(u).strip()
    low = s.lower()
    if low.startswith("http://") or low.startswith("https://") or low.startswith("mailto:"):
        return s
    return "#"


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
        deploy_rev=_DEPLOY_REV,
    )


@app.route("/api/predictor/<pid>/status", methods=["POST"])
@_auth_required
def api_predictor_status(pid: str):
    from tool import predictor_pipeline
    data = _safe_json_body()
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
    data = _safe_json_body()
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
    data = _safe_json_body()
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


@app.route("/api/dispatch/pre-meeting", methods=["POST"])
@_auth_required
def api_pre_meeting():
    data = _safe_json_body()
    inputs = {
        "account_name": (data.get("account_name") or "").strip(),
        "contact_name": (data.get("contact_name") or "").strip(),
        "meeting_context": (data.get("meeting_context") or "").strip(),
        "mode": data.get("mode", "send"),
    }
    if not inputs["account_name"]:
        return jsonify({"ok": False, "detail": "Account name required"}), 400
    return jsonify(trigger_workflow("pre-meeting-brief.yml", inputs))


@app.route("/api/dispatch/sweep", methods=["POST"])
@_auth_required
def api_sweep():
    data = _safe_json_body()
    inputs = {
        "window_days": str(data.get("window_days", "14")),
        "mode": data.get("mode", "send"),
    }
    return jsonify(trigger_workflow("fortnightly-sweep.yml", inputs))


# ---------------------------------------------------------------------------
# Demand-creation tools (in-process; no GitHub Actions roundtrip).
# These run heuristically against the existing state files. They are what
# turn the dashboard from "react fast when market moves" into "create demand
# when market is dead" — distress signals, MPC outreach factory, pipeline
# triage, objection coach, candidate watch, competitor mandates.
# ---------------------------------------------------------------------------

@app.route("/api/mpc/build", methods=["POST"])
@_auth_required
def api_mpc_build():
    """Build a per-account MPC outreach hit list for one pasted candidate."""
    from tool.mpc_factory import MPCCandidate, build_hit_list, hit_list_to_json
    data = _safe_json_body()
    name = (data.get("name") or "").strip()
    current_company = (data.get("current_company") or "").strip()
    current_title = (data.get("current_title") or "").strip()
    if not name or not current_company or not current_title:
        return jsonify({"ok": False,
                        "detail": "Missing: name, current_company, current_title required"}), 400
    candidate = MPCCandidate(
        name=name,
        current_company=current_company,
        current_title=current_title,
        sectors=[s.strip() for s in (data.get("sectors") or "").split(",") if s.strip()],
        specialism=(data.get("specialism") or "").strip(),
        notes=(data.get("notes") or "").strip(),
    )
    try:
        top_n = int(data.get("top_n") or 20)
    except (TypeError, ValueError):
        top_n = 20
    top_n = max(1, min(top_n, 50))   # clamp so negative / huge values can't break the slice
    hits = build_hit_list(candidate, top_n=top_n)
    return jsonify({"ok": True, "hits": hit_list_to_json(hits), "count": len(hits)})


@app.route("/api/candidates/watch", methods=["GET"])
@_auth_required
def api_candidates_watch_list():
    """List watched candidates, sorted by call urgency."""
    from tool.candidate_watch import list_watched
    include_snoozed = request.args.get("include_snoozed") == "1"
    rows = list_watched(include_snoozed=include_snoozed)
    return jsonify({"rows": rows, "total": len(rows)})


@app.route("/api/candidates/watch/add", methods=["POST"])
@_auth_required
def api_candidates_watch_add():
    from tool.candidate_watch import add_candidate
    data = _safe_json_body()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "detail": "Name required"}), 400
    sectors = [s.strip() for s in (data.get("sectors") or "").split(",") if s.strip()]
    try:
        cadence = int(data.get("touch_cadence_days") or 30)
    except (TypeError, ValueError):
        cadence = 30
    cadence = max(1, min(cadence, 365))
    rec = add_candidate(
        name=name,
        current_company=(data.get("current_company") or "").strip(),
        current_title=(data.get("current_title") or "").strip(),
        linkedin_url=(data.get("linkedin_url") or "").strip(),
        sectors=sectors,
        notes=(data.get("notes") or "").strip(),
        touch_cadence_days=cadence,
    )
    return jsonify({"ok": True, "candidate": rec})


@app.route("/api/candidates/watch/touch", methods=["POST"])
@_auth_required
def api_candidates_watch_touch():
    from tool.candidate_watch import mark_touched
    data = _safe_json_body()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "detail": "Name required"}), 400
    rec = mark_touched(
        name=name,
        current_company=(data.get("current_company") or "").strip(),
        signal=(data.get("signal") or "").strip(),
    )
    if not rec:
        return jsonify({"ok": False, "detail": "Candidate not found"}), 404
    return jsonify({"ok": True, "candidate": rec})


@app.route("/api/candidates/watch/snooze", methods=["POST"])
@_auth_required
def api_candidates_watch_snooze():
    from tool.candidate_watch import snooze_candidate
    data = _safe_json_body()
    name = (data.get("name") or "").strip()
    current_company = (data.get("current_company") or "").strip()
    try:
        days = int(data.get("days") or 14)
    except (TypeError, ValueError):
        days = 14
    days = max(1, min(days, 365))
    rec = snooze_candidate(name, current_company, days)
    if not rec:
        return jsonify({"ok": False, "detail": "Candidate not found"}), 404
    return jsonify({"ok": True, "candidate": rec})


@app.route("/api/candidates/watch/remove", methods=["POST"])
@_auth_required
def api_candidates_watch_remove():
    from tool.candidate_watch import remove_candidate
    data = _safe_json_body()
    name = (data.get("name") or "").strip()
    current_company = (data.get("current_company") or "").strip()
    ok = remove_candidate(name, current_company)
    if not ok:
        return jsonify({"ok": False, "detail": "Candidate not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/competitor-mandates", methods=["GET"])
@_auth_required
def api_competitor_mandates():
    """Comms job ads past their per-source stale threshold (100d public
    sector, 50d direct ATS, 60d aggregators) — clients open to a second
    agency or to off-piste candidates. An optional ?min_age=N raises the
    bar globally on top of the per-source thresholds."""
    from tool.competitor_mandates import stale_mandates
    raw = request.args.get("min_age")
    min_age = int(raw) if (raw and raw.isdigit()) else None
    rows = stale_mandates(min_age_days=min_age)
    return jsonify({"rows": rows, "total": len(rows),
                    "min_age_days": min_age, "per_source": min_age is None})


@app.route("/api/following", methods=["GET"])
@_auth_required
def api_following():
    """Mandates Worth Following — vacated-seat / backfill detector. A
    senior comms person publicly moved; the seat they left at a
    watchlist company is the live brief."""
    from tool.following import load_following
    rows = load_following(limit=40)
    return jsonify({"rows": rows, "total": len(rows)})


@app.route("/api/pulses", methods=["GET"])
@_auth_required
def api_pulses():
    """Calendar Pulses — deterministic, date-driven placement windows.
    Computed live (days_left changes daily): a statute/regulator date
    forces a comms-capacity build-up in a named watchlist cohort; get
    the retained brief before it's advertised."""
    from tool.calendar_pulses import load_pulses
    rows = load_pulses(limit=10)
    return jsonify({"rows": rows, "total": len(rows)})


@app.route("/api/water-sar", methods=["GET"])
@_auth_required
def api_water_sar():
    """Water Special-Administration Watch — SAR / financial-resilience
    events at England & Wales regulated water companies. Highest-value
    single comms event in UK utilities; the resilience run-up is visible
    weeks before the appointment news everyone else reacts to."""
    from tool.water_sar import load_water_sar
    rows = load_water_sar(limit=20)
    return jsonify({"rows": rows, "total": len(rows)})


@app.route("/api/contract-end", methods=["GET"])
@_auth_required
def api_contract_end():
    """Contract-End / Re-Tender Window — proactive leading indicator. A
    watchlist employer's flagship contract is approaching expiry /
    recompete / hand-over; the change & transition comms review happens
    in that window, months before any contract-loss RNS."""
    from tool.contract_end import load_contract_end
    rows = load_contract_end(limit=30)
    return jsonify({"rows": rows, "total": len(rows)})


@app.route("/api/funding", methods=["GET"])
@_auth_required
def api_funding():
    """Funding-Round detector — a scaling private firm closed a >=£20m
    growth round; a step-change senior in-house comms hire typically
    follows ~6 months later, months after the round is public."""
    from tool.funding_round import load_funding
    rows = load_funding(limit=30)
    return jsonify({"rows": rows, "total": len(rows)})


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

      /* Mobile predictor row: grid puts chips on their own dedicated
         row beneath the company name so 'Corporate Affairs Director'
         can't render on top of 'Intertek Group'. */
      .item.predictor .row-summary {
        display: grid;
        grid-template-columns: auto 1fr auto;
        grid-template-areas:
          "rank title toggle"
          "chips chips chips";
        align-items: center;
        column-gap: 8px;
        row-gap: 6px;
      }
      .item.predictor .row-summary .rank { grid-area: rank; }
      .item.predictor .row-summary .title {
        grid-area: title;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .item.predictor .row-summary .expand-toggle { grid-area: toggle; }
      .item.predictor .row-summary .chips {
        grid-area: chips;
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .item.predictor .row-preview { margin-left: 0; }
    }

    /* Desktop chips container — inline-flex so it stays on the same
       row as title; only the mobile grid breaks them onto their own
       line. */
    .item.predictor .row-summary .chips {
      display: inline-flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
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
    /* Single full-width panel (e.g. Mandates Worth Stealing) — without
       this it sits in the left half of a 2-col grid with dead space
       beside it. */
    .row.row-full { grid-template-columns: 1fr; }
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

    /* ===== Calendar Pulses — year ribbon (Alternate A) ===== */
    /* Fixed-size boxes (mockup parity, 400px). Anything taller scrolls
       inside the body — the panel/section never grows. Scoped to this
       row so other panels keep the global .panel-body sizing. */
    #pulses-row .panel { height: 400px; }
    #pulses-row .panel-body { max-height: none; flex: 1; min-height: 0; overflow-y: auto; }
    /* Match the 16px horizontal inset every other section's .item uses
       so funding rows aren't jammed against the panel edge. */
    #funding-body { padding: 8px 16px 12px; }

    .cal-wrap { padding: 14px 16px; }
    .cal-ribbon {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
    }
    .cal-tile {
      position: relative;
      border: 1px solid var(--border);
      border-radius: 9px;
      height: 46px;
      padding: 7px 11px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: var(--text-muted);
      overflow: hidden;
    }
    .cal-tile.past { opacity: .4; }
    .cal-tile.now { border-color: var(--teal); }
    .cal-tile.has { cursor: pointer; background: var(--surface-elevated); }
    .cal-tile.has:hover { border-color: var(--border-hover); }
    .cal-tile.sel { outline: 2px solid var(--teal); outline-offset: -2px; }
    .cal-mlab { font-size: 12px; font-weight: 700; color: var(--text); white-space: nowrap; }
    .cal-tile.past .cal-mlab { color: var(--text-muted); }
    .cal-right { display: flex; align-items: center; gap: 7px; }
    .cal-pips { display: flex; gap: 7px; }
    .cal-pip { width: 9px; height: 9px; border-radius: 50%; }
    .cal-pip.high { background: var(--teal); }   /* high = coral */
    .cal-pip.med  { background: var(--green); }   /* policy-firming = green (distinct) */
    /* NEW month: same reddening wash as a fresh finding row */
    .cal-tile.fresh {
      background: linear-gradient(90deg, var(--teal-soft), transparent 72%);
      border-color: var(--border-hover);
      border-left: 3px solid var(--teal);
    }
    .cal-tile.fresh .cal-mlab { color: var(--teal-dark); }
    .cal-nbadge {
      display: inline-flex; align-items: center;
      font-size: 9px; font-weight: 800; letter-spacing: .05em;
      color: #fff; background: var(--teal);
      padding: 2px 7px; border-radius: 6px;
      box-shadow: var(--shadow-md);
      animation: cal-bob 1.05s ease-in-out infinite;
    }
    @keyframes cal-bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }
    .cal-headnew {
      display: inline-flex; align-items: center; gap: 6px;
      font-size: 10px; font-weight: 600; letter-spacing: .02em;
      color: var(--teal-dark); background: var(--teal-soft);
      border: 1px solid var(--border-hover);
      padding: 2px 9px 2px 7px; border-radius: 10px;
      cursor: pointer; font-family: inherit;
    }
    .cal-headnew:hover { background: rgba(201,100,66,.16); }
    .cal-nd {
      width: 6px; height: 6px; border-radius: 50%;
      background: var(--teal);
      animation: cal-ping 1.5s ease-in-out infinite;
    }
    @keyframes cal-ping {
      0%,100% { box-shadow: 0 0 0 0 rgba(201,100,66,.55); }
      50% { box-shadow: 0 0 0 5px rgba(201,100,66,0); }
    }
    .cal-detail { margin-top: 12px; }
    .cal-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 13px;
      box-shadow: var(--shadow-md);
    }
    .cal-ph { color: var(--text-dim); font-size: 12px; }
    .cal-c-name { font-weight: 600; font-size: 13px; color: var(--text); }
    .cal-days {
      font-size: 10.5px; font-weight: 700; color: #fff;
      background: var(--teal); border-radius: 999px;
      padding: 2px 8px; margin-left: 6px;
    }
    .cal-days.far { color: var(--text-dim); background: transparent; border: 1px solid var(--border); }
    .cal-seat { font-size: 12px; color: var(--text); margin-top: 6px; }
    .cal-angle { font-size: 11.5px; color: var(--text-muted); margin-top: 4px; }
    .cal-scope { font-size: 11px; color: var(--text-dim); margin-top: 6px; }
    .cal-legend { display: flex; gap: 16px; font-size: 10px; color: var(--text-dim); margin-top: 10px; }
    .cal-legend i { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 6px; vertical-align: middle; }
    .cal-dsep { border: 0; border-top: 1px solid var(--border); margin: 10px 0; }

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

    /* Advisory Services lens — the second billable path on the same
       signal (capability review / talent map / benchmarking). Subtle
       accent so it reads as a distinct revenue line, not noise. */
    .advisory-line {
      margin: 6px 0 2px;
      padding: 6px 9px;
      font-size: 12px;
      line-height: 1.45;
      color: var(--teal-dark);
      background: var(--teal-soft);
      border-left: 2px solid var(--teal);
      border-radius: 3px;
    }

    /* Specialist Signals — four low-frequency detectors collapsed into
       one panel that renders ONLY when a sub-detector has results, so
       the dashboard never advertises empty panels (the empty-panel
       state trains the user to stop checking). Detector logic + the
       /api endpoints are unchanged; this is pure presentation. */
    .specialist-sub { margin: 0 0 18px; }
    .specialist-sub:last-child { margin-bottom: 0; }
    .specialist-h {
      margin: 0 0 6px;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.02em;
      color: var(--text);
      border-bottom: 1px solid var(--navy-hairline);
      padding-bottom: 4px;
    }

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
    /* ---- Demand-creation tool badges & pills ---- */
    .mandate-age {
      display: inline-block;
      font-size: 10.5px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 3px;
      background: #F5E9D8;
      color: #6B4A0B;
      margin-right: 6px;
    }
    .hook-badge {
      display: inline-block;
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      padding: 1px 6px;
      border-radius: 3px;
      margin-left: 4px;
      vertical-align: 2px;
      background: #ECE8DD;
      color: #4A4537;
    }
    .hook-badge.distress_signal   { background: #FCD5C9; color: #8C2A0E; }
    .hook-badge.predictor_signal  { background: #E0EAD8; color: #3F5727; }
    .hook-badge.leadership_change { background: #E2E6F0; color: #2A3556; }
    .hook-badge.recent_signal     { background: #F5E9D8; color: #6B4A0B; }
    .hook-badge.generic_fit       { background: #ECE8DD; color: #7A7164; }
    .triage-label {
      display: inline-block;
      font-size: 10.5px;
      font-weight: 700;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      padding: 2px 7px;
      border-radius: 3px;
      margin-right: 6px;
    }
    .triage-label.alive   { background: #E0EAD8; color: #3F5727; }
    .triage-label.stalled { background: #FCEED1; color: #6B4A0B; }
    .triage-label.cold    { background: #E2E6F0; color: #2A3556; }
    .triage-label.dead    { background: #ECE8DD; color: #7A7164; text-decoration: line-through; }
    .triage-label.unclear { background: #F0E8E0; color: #6B5A4A; }
    :root {
      --alive-bar:   #6B8C3B;
      --stalled-bar: #C49A3B;
      --cold-bar:    #5C6BA0;
      --dead-bar:    #B7AC9A;
      --unclear-bar: #A89684;
    }
    .overdue-pill {
      display: inline-block;
      font-size: 10.5px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 3px;
      background: #FCD5C9;
      color: #8C2A0E;
      margin-right: 4px;
    }
    .restless-pill {
      display: inline-block;
      font-size: 10.5px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 3px;
      background: #FCEED1;
      color: #6B4A0B;
      margin-right: 4px;
    }
    .inline-result {
      margin-top: 12px;
      max-height: 480px;
      overflow-y: auto;
    }
    .inline-result::-webkit-scrollbar { width: 6px; }
    .inline-result::-webkit-scrollbar-thumb {
      background: var(--navy-soft);
      border-radius: 3px;
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

    /* ACTION CARDS — fixed, uniform 3x2 grid. Every card is the SAME
       size; a card whose content is taller than the box (e.g. MPC's
       5 fields) scrolls internally rather than growing the box. */
    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 16px;
    }
    /* Always 3 across (2 rows of 3) on desktop/laptop; only collapse to
       a single column on genuinely narrow screens. No 2-column tier —
       that was forcing "3 rows of 2". */
    @media (max-width: 900px) { .actions { grid-template-columns: 1fr; } }

    .action-card {
      width: 100%;
      /* Uniform box sized to the Reverse Match / Pitch Pack card (a
         ~3-field form: padding+h3+subhead+3 fields+button ≈ 330px;
         360 gives the reference card a little headroom so IT never
         scrolls). The taller MPC card (5 fields) scrolls inside this
         same box rather than growing it — all six are identical. */
      height: 360px;
      overflow-y: auto;
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
    .action-card input, .action-card select, .action-card textarea {
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
    .action-card textarea { resize: vertical; line-height: 1.5; }
    .action-card input::placeholder,
    .action-card textarea::placeholder { color: #A6AFBE; font-weight: 400; }
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
    .action-card input:focus, .action-card select:focus,
    .action-card textarea:focus {
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
    .action-card summary.add-toggle {
      list-style: none;
      display: block;
      width: 100%;
      margin-top: 10px;
      padding: 9px 14px;
      background: linear-gradient(135deg, var(--teal) 0%, var(--teal-dark) 100%);
      color: white;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
      text-align: center;
      cursor: pointer;
      letter-spacing: 0.04em;
      transition: all 0.18s ease;
      box-shadow: 0 1px 2px rgba(91, 166, 173, 0.15);
    }
    .action-card summary.add-toggle::-webkit-details-marker { display: none; }
    .action-card summary.add-toggle::marker { content: ""; }
    .action-card summary.add-toggle:hover {
      box-shadow: 0 4px 16px var(--teal-glow), 0 0 0 1px var(--teal);
      transform: translateY(-1px);
    }
    .funding-details > summary {
      list-style: none;
      cursor: pointer;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface-elevated);
      font-size: 13px;
      line-height: 1.5;
      transition: border-color 0.15s ease;
    }
    .funding-details > summary::-webkit-details-marker { display: none; }
    .funding-details > summary::marker { content: ""; }
    .funding-details > summary:hover { border-color: var(--border-hover); }
    .funding-details[open] > summary { margin-bottom: 8px; }
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

    /* Developer-only maintenance control. Deliberately drab and set
       apart from the user-facing footer text so it reads as "not for
       you" and doesn't invite a curious click. */
    .dev-zone {
      display: block;
      margin-top: 10px;
      padding-top: 8px;
      border-top: 1px dashed var(--border);
      opacity: 0.45;
      font-size: 10px;
    }
    .dev-zone:hover { opacity: 0.8; }
    .dev-zone-label {
      color: var(--text-dim);
      font-style: italic;
      margin-right: 6px;
      text-transform: none;
      letter-spacing: 0;
    }
    .dev-btn {
      font: inherit;
      font-size: 10px;
      color: var(--text-muted);
      background: transparent;
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 2px 8px;
      cursor: pointer;
    }
    .dev-btn:hover { color: var(--text); border-color: var(--text-dim); }
    .dev-btn:disabled { opacity: 0.5; cursor: default; }
    .dev-status { margin-left: 8px; color: var(--text-dim); }

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
  <strong>GITHUB_TOKEN not set</strong> in your .env. The "Run and Send" buttons won't work until you add one.
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
                {% if s.url %}<a href="{{ s.url | safe_url }}" target="_blank">{{ s.title }}</a>
                {% else %}{{ s.title }}{% endif %}
              </span>
              <div class="meta">
                <span class="badge">{{ s.company or '—' }}</span>
                <span class="badge">{{ s.source }}</span>
                <span class="badge">{{ s.geo }}</span>
              </div>
              {% if s.contact %}
              <div style="font-size:11px;color:var(--text-muted);margin:2px 0 4px;">
                → Contact:
                <strong style="color:#333;">{{ s.contact.name or s.contact.title }}</strong>{% if s.contact.name %} · {{ s.contact.title }}{% endif %}
                · confidence {{ '%.0f' | format(s.contact.confidence * 100) }}%{% if s.contact.basis == 'jd_reporting_line' %} · from JD{% elif s.contact.basis == 'appointee' %} · named in headline{% endif %}
              </div>
              {% endif %}
              <pre class="outreach-text">{{ s.outreach }}</pre>
              <div class="item-actions">
                <button class="btn-mini copy-outreach" type="button">✉ Copy outreach</button>
                <a class="btn-mini" href="{{ s.linkedin.url | safe_url }}" target="_blank" title="{{ s.linkedin.label }}">↗ {{ s.linkedin.label }}</a>
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
                  {% if s.url %}<a href="{{ s.url | safe_url }}" target="_blank">{{ s.title }}</a>
                  {% else %}{{ s.title }}{% endif %}
                </span>
                <div class="meta">
                  <span class="badge">{{ s.company or '-' }}</span>
                  <span class="badge">{{ s.source }}</span>
                  <span class="badge">{{ s.geo }}</span>
                </div>
                {% if s.contact %}
                <div style="font-size:11px;color:var(--text-muted);margin:2px 0 4px;">
                  → Contact:
                  <strong style="color:#333;">{{ s.contact.name or s.contact.title }}</strong>{% if s.contact.name %} · {{ s.contact.title }}{% endif %}
                  · confidence {{ '%.0f' | format(s.contact.confidence * 100) }}%{% if s.contact.basis == 'jd_reporting_line' %} · from JD{% elif s.contact.basis == 'appointee' %} · named in headline{% endif %}
                </div>
                {% endif %}
                <pre class="outreach-text">{{ s.outreach }}</pre>
                <div class="item-actions">
                  <button class="btn-mini copy-outreach" type="button">✉ Copy outreach</button>
                  <a class="btn-mini" href="{{ s.linkedin.url | safe_url }}" target="_blank" title="{{ s.linkedin.label }}">↗ {{ s.linkedin.label }}</a>
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
                <span class="chips">
                  {% if p.predicted_role %}<span class="role-chip">{{ p.predicted_role }}</span>{% endif %}
                  {% if p.probability %}<span class="prob-chip">{{ p.probability }}%</span>{% endif %}
                  {% if p.is_new %}<span class="new-badge">NEW</span>{% endif %}
                  {% if p.window_label %}<span class="window-badge">{{ p.window_label }}</span>{% endif %}
                  {% if p.depth > 1 %}<span class="stack-label stacked">stacked × {{ p.depth }}</span>{% endif %}
                  {% if p.status == 'followed_up' %}<span class="status-badge followed-up">✓ followed up</span>{% endif %}
                  {% if p.status == 'dismissed' %}<span class="status-badge dismissed">dismissed</span>{% endif %}
                </span>
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
                      {% if e.url %} · <a href="{{ e.url | safe_url }}" target="_blank">source</a>{% endif %}
                    </div>
                  {% endfor %}
                </div>
                {% if p.advisory %}<div class="advisory-line">{{ p.advisory }}</div>{% endif %}
                <pre class="outreach-text">{{ p.outreach }}</pre>
                <div class="item-actions">
                  <button class="btn-mini copy-outreach" type="button">✉ Copy outreach</button>
                  <a class="btn-mini" href="{{ p.linkedin.url | safe_url }}" target="_blank" title="{{ p.linkedin.label }}">↗ {{ p.linkedin.label }}</a>
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
          <div class="empty compact">No predictors loaded yet. Click Daily Refresh.</div>
        {% endif %}
      </div>
    </div>

  </div>

  <!-- DETERMINISTIC, DATE-DRIVEN PLACEMENT WINDOWS — Calendar Pulses
       (year ribbon) and the Funding-Round pre-hire window sit
       side-by-side at equal size (2-col grid; stacks under 900px).
       Both reliably fire; the rare detectors live in Specialist
       Signals below. -->
  <div class="row" id="pulses-row">
    <div class="panel">
      <div class="panel-header">
        <h2>Calendar Pulses</h2>
        <div style="display:flex;align-items:center;gap:8px;">
          <button class="cal-headnew" id="pulses-new" type="button" style="display:none;">
            <span class="cal-nd"></span><span id="pulses-new-n">0</span>&nbsp;new</button>
          <span class="count" id="pulses-count">—</span>
        </div>
      </div>
      <div class="panel-body" id="pulses-body">
        <div class="empty compact">Loading…</div>
      </div>
    </div>

    <!-- FUNDING-ROUND PRE-HIRE WINDOW (>=£20m -> ~6-month comms hire) -->
    <div class="panel">
      <div class="panel-header">
        <h2>Funding-Round Pre-Hire Window</h2>
        <span class="count" id="funding-count">—</span>
      </div>
      <div class="panel-body" id="funding-body">
        <div class="empty compact">Loading…</div>
      </div>
    </div>

  </div>

  <!-- SPECIALIST SIGNALS — Mandates Worth Following / Water SAR /
       Contract-End / Mandates Worth Stealing, collapsed into one panel
       that is HIDDEN unless a sub-detector actually has rows. Each
       sub-section also hides itself when empty. Detectors + /api
       endpoints unchanged; pure presentation. -->
  <div class="row row-full" id="specialist-row" style="display:none">
    <div class="panel">
      <div class="panel-header">
        <h2>Specialist Signals</h2>
        <span class="count" id="specialist-count">—</span>
      </div>
      <div class="panel-body" id="specialist-body">

        <div class="specialist-sub" id="sub-following" style="display:none">
          <h3 class="specialist-h">Mandates Worth Following</h3>
          <div id="following-body"></div>
        </div>

        <div class="specialist-sub" id="sub-watersar" style="display:none">
          <h3 class="specialist-h">Water Special-Administration Watch</h3>
          <div id="watersar-body"></div>
        </div>

        <div class="specialist-sub" id="sub-contractend" style="display:none">
          <h3 class="specialist-h">Contract-End / Re-Tender Window</h3>
          <div id="contractend-body"></div>
        </div>

        <div class="specialist-sub" id="sub-mandates" style="display:none">
          <h3 class="specialist-h">Mandates Worth Stealing</h3>
          <div id="mandates-body"></div>
        </div>

      </div>
    </div>
  </div>

  <!-- ACTION BOXES -->
  <div class="actions">

    <!-- PRE-MEETING BRIEF -->
    <div class="panel action-card">
      <h3>Pre-meeting Brief</h3>
      <div class="subhead">Walk into any client meeting with prep no competitor matches.</div>
      <form id="pm-form" onsubmit="dispatch(event, 'pm-form', '/api/dispatch/pre-meeting')">
        <label for="pm-account">Account name</label>
        <input id="pm-account" name="account_name" placeholder="e.g. Severn Trent" required>
        <label for="pm-contact">Contact (optional)</label>
        <input id="pm-contact" name="contact_name" placeholder="e.g. Carla Sherry">
        <label for="pm-context">Meeting context (optional)</label>
        <input id="pm-context" name="meeting_context" placeholder="e.g. 10am Mon, Zoom">
        <button type="submit">Run and send via email</button>
        <div class="status" id="pm-status"></div>
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

    <!-- MPC OUTREACH FACTORY -->
    <div class="panel action-card">
      <h3>MPC Outreach Factory</h3>
      <div class="subhead">Get a ranked list of target accounts for a candidate, each with a paste-ready outreach hook, woven from signals already gathered (a live trigger at that company, or a structural fit).</div>
      <form id="mpc-form" onsubmit="runMPC(event)">
        <label for="mpc-name">Candidate name</label>
        <input id="mpc-name" name="name" placeholder="e.g. James Carter" required>
        <label for="mpc-company">Current company</label>
        <input id="mpc-company" name="current_company" placeholder="e.g. Barclays" required>
        <label for="mpc-title">Current title</label>
        <input id="mpc-title" name="current_title" placeholder="e.g. Director of Corporate Comms" required>
        <button type="submit">Build hit list</button>
        <div class="status" id="mpc-status"></div>
        <div id="mpc-result" class="inline-result"></div>
      </form>
    </div>

    <!-- CANDIDATE WATCH -->
    <div class="panel action-card">
      <h3>Candidate Watch</h3>
      <div class="subhead">Keep a roster of warm candidates to stay in touch with. Overdue ones float to the top so relationships don't go cold. Note — the "restlessness" score only keyword-matches notes Sara types herself, not live intelligence.</div>
      <div id="watch-list-wrap">
        <div class="status" id="watch-list-status">Loading…</div>
        <div id="watch-list"></div>
      </div>
      <details style="margin-top:10px;">
        <summary class="add-toggle">+ Add candidate</summary>
        <form id="watch-add-form" onsubmit="addWatchCandidate(event)" style="margin-top:8px;">
          <label for="wa-name">Name</label>
          <input id="wa-name" name="name" required>
          <label for="wa-company">Current company</label>
          <input id="wa-company" name="current_company">
          <label for="wa-title">Current title</label>
          <input id="wa-title" name="current_title">
          <label for="wa-linkedin">LinkedIn URL</label>
          <input id="wa-linkedin" name="linkedin_url" placeholder="https://linkedin.com/in/...">
          <label for="wa-cadence">Touch cadence (days)</label>
          <input id="wa-cadence" name="touch_cadence_days" type="number" value="30" min="7" max="180">
          <label for="wa-notes">Notes</label>
          <input id="wa-notes" name="notes">
          <button type="submit">Add</button>
          <div class="status" id="watch-add-status"></div>
        </form>
      </details>
    </div>

    <!-- REVERSE MATCH -->
    <div class="panel action-card">
      <h3>Reverse Match</h3>
      <div class="subhead">Get a ranked list of accounts where your candidate could be pitched. Unlike MPC Factory, this one goes out and searches the market fresh each time - casts a wider net.</div>
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

  </div>

  <div class="footer">
    Data refreshed from GitHub Actions artifacts.
    All sources are free public surfaces. No automation of LinkedIn account.
    <span style="opacity:0.5; margin-left:8px;">· build {{ build_stamp }} · rev {{ deploy_rev }}</span>
    <span class="dev-zone">
      <span class="dev-zone-label">dev / tech only — not a user feature:</span>
      <button type="button" id="dev-run-brief" class="dev-btn"
              onclick="devTriggerBrief()"
              title="Maintenance: triggers a fresh morning-brief workflow run. Not for day-to-day use — Daily Refresh is the user control.">
        trigger fresh scour
      </button>
      <span class="dev-status" id="dev-run-status"></span>
    </span>
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

// Daily Refresh: pulls the latest GitHub Actions artifact and unpacks
// it. Simple, fast (~5s). Surfaces the exact result in a banner so the
// user can see WHY the dashboard is empty if it is — too-old artifact,
// missing file, or genuinely zero results from today's brief.
async function refreshBrief() {
  const btn = document.getElementById('refresh-btn');
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Refreshing…';
  try {
    const r = await fetch('/api/refresh', { method: 'POST' });
    const j = await r.json();
    if (j.ok && (j.leads > 0 || j.predictors > 0)) {
      // Normal success: just reload. No banner — the data speaks for itself.
      setTimeout(() => window.location.reload(), 600);
    } else {
      // Only surface a banner when something is actually wrong (refresh
      // failed, or it succeeded but found nothing — both carry a warning).
      showRefreshBanner(j);
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  } catch (e) {
    showRefreshBanner({ok: false, detail: 'Refresh failed: ' + e.message});
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

function showRefreshBanner(result) {
  let banner = document.getElementById('refresh-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'refresh-banner';
    banner.style.cssText = 'max-width:1280px;margin:0 auto 14px;padding:12px 16px;'
      + 'border-radius:8px;font-size:13px;line-height:1.5;border:1px solid;';
    const wrap = document.querySelector('.refresh-bar')?.parentNode;
    if (wrap) wrap.insertBefore(banner, document.querySelector('.refresh-bar').nextSibling);
  }
  if (result.ok) {
    banner.style.background = 'rgba(107, 140, 59, 0.08)';
    banner.style.borderColor = 'rgba(107, 140, 59, 0.32)';
    banner.style.color = '#3F5727';
  } else {
    banner.style.background = 'rgba(201, 100, 66, 0.08)';
    banner.style.borderColor = 'rgba(201, 100, 66, 0.32)';
    banner.style.color = '#7A3A22';
  }
  banner.textContent = result.detail || (result.ok ? 'Refreshed.' : 'Refresh failed.');
}

// ===========================================================================
// DEMAND-CREATION TOOLS (in-process; no GitHub Actions roundtrip)
// ===========================================================================

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
// URL whitelist: only http(s) and mailto pass through. Defends against
// javascript: / data: URLs that could appear in upstream RSS/GDELT data
// and execute on click.
function safeUrl(u) {
  if (!u) return '#';
  const s = String(u).trim();
  if (/^https?:\/\//i.test(s) || /^mailto:/i.test(s)) return esc(s);
  return '#';
}


// ---------- Mandates Worth Stealing ----------
async function loadMandates() {
  const body = document.getElementById('mandates-body');
  const sub = document.getElementById('sub-mandates');
  try {
    const r = await fetch('/api/competitor-mandates');
    const j = await r.json();
    const rows = (j && j.rows) || [];
    if (!rows.length) { sub.style.display = 'none'; return 0; }
    const out = ['<ul style="margin:6px 0;padding:0;list-style:none;">'];
    for (const m of rows.slice(0, 12)) {
      out.push(
        '<li style="padding:8px 0;border-bottom:1px solid var(--border);">' +
          '<span class="mandate-age">' + esc(m.days_live) + 'd</span> ' +
          '<a href="' + safeUrl(m.url) + '" target="_blank" rel="noopener noreferrer" style="color:var(--text);">' +
            esc(m.title || '(no title)') + '</a>' +
          '<span style="color:var(--text-muted);font-size:12px;display:block;margin-top:2px;">' +
            esc(m.company || '') + ' &middot; ' + esc(m.source || '') +
            (m.threshold ? ' &middot; flagged at ' + esc(m.threshold) + 'd' : '') +
            ' &middot; first seen ' + esc(m.first_seen) +
          '</span>' +
        '</li>'
      );
    }
    out.push('</ul>');
    body.innerHTML = out.join('');
    sub.style.display = '';
    return rows.length;
  } catch (e) {
    sub.style.display = 'none';
    return 0;
  }
}

// ---------- Calendar Pulses (deterministic placement windows) ----------
async function loadPulses() {
  const body = document.getElementById('pulses-body');
  const count = document.getElementById('pulses-count');
  try {
    const r = await fetch('/api/pulses');
    const j = await r.json();
    count.textContent = j.total;
    if (!j.rows || j.rows.length === 0) {
      body.innerHTML = '<div class="empty compact">No placement window open today. ' +
        'Pulses surface only inside a statutory/regulator run-up (FCA Consumer Duty ' +
        'board-report ramp, UK SRS first-cycle build-up, post-Spending-Review ' +
        'machinery-of-government reshuffle) — by design they go quiet outside those ' +
        'dated windows rather than show stale noise.</div>';
      return;
    }
    const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const rows = j.rows;

    // act-by month/year per pulse (window-end month — the deadline the
    // run-up builds to). Fall back to the printed window if act_by absent.
    const abOf = p => {
      const ab = (p.act_by || (String(p.window || '').split('→').pop() || '')).trim();
      return { y: parseInt(ab.slice(0, 4), 10), m: parseInt(ab.slice(5, 7), 10) - 1 };
    };
    // Ribbon year = the most common act-by year across active pulses.
    const yc = {};
    rows.forEach(p => { const y = abOf(p).y; if (y) yc[y] = (yc[y] || 0) + 1; });
    const ribYear = Object.keys(yc).sort((a, b) => yc[b] - yc[a])[0]
      ? parseInt(Object.keys(yc).sort((a, b) => yc[b] - yc[a])[0], 10)
      : new Date().getFullYear();
    const now = new Date(), nowY = now.getFullYear(), nowM = now.getMonth();

    // Bucket pulses by act-by month within the ribbon year.
    const buckets = Array.from({ length: 12 }, () => []);
    rows.forEach(p => { const a = abOf(p); if (a.y === ribYear && a.m >= 0 && a.m < 12) buckets[a.m].push(p); });

    const newCount = rows.filter(p => p.just_opened).length;
    let freshMonth = -1;
    for (let m = 0; m < 12; m++) if (buckets[m].some(p => p.just_opened)) { freshMonth = m; break; }

    const pipFor = p => '<span class="cal-pip ' + (p.confidence === 'high' ? 'high' : 'med') + '"></span>';
    const out = ['<div class="cal-wrap"><div class="cal-ribbon">'];
    for (let m = 0; m < 12; m++) {
      const ps = buckets[m], has = ps.length > 0;
      const fresh = ps.some(p => p.just_opened);
      const past = (ribYear < nowY) || (ribYear === nowY && m < nowM);
      const isNow = (ribYear === nowY && m === nowM);
      const cls = 'cal-tile' + (past ? ' past' : '') + (isNow ? ' now' : '') +
                  (has ? ' has' : '') + (fresh ? ' fresh' : '');
      out.push(
        '<div class="' + cls + '" data-m="' + m + '">' +
          '<span class="cal-mlab">' + MON[m] + '</span>' +
          '<span class="cal-right">' +
            (has ? '<span class="cal-pips">' + ps.map(pipFor).join('') + '</span>' : '') +
            (fresh ? '<span class="cal-nbadge">NEW</span>' : '') +
          '</span>' +
        '</div>'
      );
    }
    out.push('</div>');  // .cal-ribbon
    out.push(
      '<div class="cal-detail">' +
        '<div class="cal-card" id="cal-card">' +
          '<span class="cal-ph">Click a month with pips for the lead detail.</span>' +
        '</div>' +
        '<div class="cal-legend">' +
          '<span><i style="background:var(--teal)"></i>high confidence</span>' +
          '<span><i style="background:var(--green)"></i>policy timeline firming</span>' +
        '</div>' +
      '</div></div>'  // .cal-detail .cal-wrap
    );
    body.innerHTML = out.join('');

    const cardFor = p => {
      const conf = (p.confidence === 'high') ? 'mandate-age' : 'hook-badge generic_fit';
      const far = (typeof p.days_left === 'number' && p.days_left > 150);
      const targets = (p.targets || []).map(t =>
        '<span class="hook-badge generic_fit" style="margin:2px 4px 2px 0;display:inline-block;">' +
        esc(t) + '</span>').join('');
      return (
        '<span class="' + conf + '">' + esc(p.confidence || '') + '</span> ' +
        '<span class="cal-c-name">' + esc(p.name || '') + '</span>' +
        (typeof p.days_left === 'number'
          ? '<span class="cal-days' + (far ? ' far' : '') + '">' +
            esc(String(p.days_left)) + 'd left</span>' : '') +
        '<div class="cal-seat">' + esc(p.seat || '') + '</div>' +
        '<div class="cal-angle">' + esc(p.angle || '') + '</div>' +
        (targets ? '<div style="margin-top:6px;">' + targets + '</div>' : '') +
        '<div class="cal-scope">' + esc(p.scope_note || '') +
          (p.source ? ' &middot; ' + esc(p.source) : '') + '</div>' +
        (p.advisory ? '<div class="advisory-line">' + esc(p.advisory) + '</div>' : '')
      );
    };
    const openMonth = m => {
      const ps = buckets[m] || [];
      if (!ps.length) return;
      body.querySelectorAll('.cal-tile').forEach(t => t.classList.remove('sel'));
      const tile = body.querySelector('.cal-tile[data-m="' + m + '"]');
      if (tile) tile.classList.add('sel');
      document.getElementById('cal-card').innerHTML =
        ps.map(cardFor).join('<hr class="cal-dsep">');
    };
    body.querySelectorAll('.cal-tile.has').forEach(t =>
      t.addEventListener('click', () => openMonth(parseInt(t.dataset.m, 10))));

    const nb = document.getElementById('pulses-new');
    if (newCount > 0 && freshMonth >= 0) {
      document.getElementById('pulses-new-n').textContent = newCount;
      nb.style.display = 'inline-flex';
      nb.onclick = () => openMonth(freshMonth);
    } else if (nb) {
      nb.style.display = 'none';
    }

    // No auto-open: the ribbon shows the placeholder until Sara clicks
    // a month. The NEW badge / header chip entice the click instead.
  } catch (e) {
    body.innerHTML = '<div class="empty compact">Failed to load: ' + esc(e.message) + '</div>';
  }
}

// ---------- Water Special-Administration Watch ----------
async function loadWaterSar() {
  const body = document.getElementById('watersar-body');
  const sub = document.getElementById('sub-watersar');
  try {
    const r = await fetch('/api/water-sar');
    const j = await r.json();
    const rows = (j && j.rows) || [];
    if (!rows.length) { sub.style.display = 'none'; return 0; }
    const out = ['<ul style="margin:6px 0;padding:0;list-style:none;">'];
    for (const w of rows.slice(0, 16)) {
      const live = (w.stage === 'SAR live / imminent');
      const stageBadge = live ? 'mandate-age' : 'hook-badge generic_fit';
      const confBadge = (w.confidence === 'high') ? 'mandate-age' : 'hook-badge generic_fit';
      out.push(
        '<li style="padding:9px 0;border-bottom:1px solid var(--border);">' +
          '<span class="' + stageBadge + '">' + esc(w.stage || '') + '</span> ' +
          '<span class="' + confBadge + '">' + esc(w.confidence || '') + '</span> ' +
          '<strong style="color:var(--text);">' + esc(w.company || '(unknown)') + '</strong>' +
          '<div style="color:var(--text);font-size:12px;margin-top:3px;">' +
            esc(w.who_to_call || '') + '</div>' +
          '<div style="color:var(--text-muted);font-size:12px;margin-top:3px;">' +
            (w.url
              ? '<a href="' + safeUrl(w.url) + '" target="_blank" rel="noopener noreferrer" style="color:#0366d6;">' + esc(w.evidence || 'source') + '</a>'
              : esc(w.evidence || '')) +
            (w.source ? ' &middot; ' + esc(w.source) : '') +
          '</div>' +
          (w.advisory ? '<div class="advisory-line">' + esc(w.advisory) + '</div>' : '') +
        '</li>'
      );
    }
    out.push('</ul>');
    body.innerHTML = out.join('');
    sub.style.display = '';
    return rows.length;
  } catch (e) {
    sub.style.display = 'none';
    return 0;
  }
}

// ---------- Contract-End / Re-Tender Window ----------
async function loadContractEnd() {
  const body = document.getElementById('contractend-body');
  const sub = document.getElementById('sub-contractend');
  try {
    const r = await fetch('/api/contract-end');
    const j = await r.json();
    const rows = (j && j.rows) || [];
    if (!rows.length) { sub.style.display = 'none'; return 0; }
    const out = ['<ul style="margin:6px 0;padding:0;list-style:none;">'];
    for (const c of rows.slice(0, 16)) {
      const conf = (c.confidence === 'high') ? 'mandate-age' : 'hook-badge generic_fit';
      out.push(
        '<li style="padding:8px 0;border-bottom:1px solid var(--border);">' +
          '<span class="' + conf + '">' + esc(c.confidence || '') + '</span> ' +
          '<strong style="color:var(--text);">' + esc(c.company || '(unknown)') + '</strong>' +
          ' &middot; <span style="color:var(--text);">' + esc(c.event || 'contract-end window') + '</span>' +
          '<span style="color:var(--text-muted);font-size:12px;display:block;margin-top:2px;">' +
            (c.url
              ? '<a href="' + safeUrl(c.url) + '" target="_blank" rel="noopener noreferrer" style="color:#0366d6;">' + esc(c.evidence || 'source') + '</a>'
              : esc(c.evidence || '')) +
            (c.source ? ' &middot; ' + esc(c.source) : '') +
            (c.sector ? ' &middot; ' + esc(c.sector) : '') +
          '</span>' +
          (c.advisory ? '<div class="advisory-line">' + esc(c.advisory) + '</div>' : '') +
        '</li>'
      );
    }
    out.push('</ul>');
    body.innerHTML = out.join('');
    sub.style.display = '';
    return rows.length;
  } catch (e) {
    sub.style.display = 'none';
    return 0;
  }
}

// ---------- Funding-Round Pre-Hire Window ----------
async function loadFunding() {
  const body = document.getElementById('funding-body');
  const count = document.getElementById('funding-count');
  try {
    const r = await fetch('/api/funding');
    const j = await r.json();
    count.textContent = j.total;
    if (!j.rows || j.rows.length === 0) {
      body.innerHTML = '<div class="empty compact">No qualifying funding round ' +
        '(&ge;£20m growth/Series round) detected yet. Click daily refresh. ' +
        'This fires when a scaling private firm closes a material equity round: ' +
        'a step-change senior in-house comms hire typically follows ~6 months ' +
        'later, months after the round is public. Debt/refinancing is excluded ' +
        'by design.</div>';
      return;
    }
    // Each round is its own compact clickable row, stacked. The summary
    // line is the at-a-glance view; clicking a row opens its evidence.
    const out = [];
    for (const f of j.rows.slice(0, 16)) {
      const conf = (f.confidence === 'high') ? 'mandate-age' : 'hook-badge generic_fit';
      const summary =
        '<span class="' + conf + '">' + esc(f.confidence || '') + '</span> ' +
        (f.sector ? esc(f.sector) + ' ' : '') +
        '<strong>' + esc(f.company || '(unknown)') + '</strong> &middot; ' +
        esc(f.amount || '') + ' ' + esc(f.round || 'funding round');
      const detail =
        '<div style="color:var(--text-muted);font-size:12px;margin-top:6px;">' +
          (f.url
            ? '<a href="' + safeUrl(f.url) + '" target="_blank" rel="noopener noreferrer" style="color:#0366d6;">' + esc(f.evidence || 'source') + '</a>'
            : esc(f.evidence || '')) +
          (f.window ? ' &middot; ' + esc(f.window) : '') +
          (f.source ? ' &middot; ' + esc(f.source) : '') +
          (f.sector ? ' &middot; ' + esc(f.sector) : '') +
        '</div>' +
        (f.advisory ? '<div class="advisory-line">' + esc(f.advisory) + '</div>' : '');
      out.push(
        '<details class="funding-details" style="margin-bottom:8px;">' +
          '<summary>' + summary + '</summary>' +
          detail +
        '</details>'
      );
    }
    body.innerHTML = out.join('');
  } catch (e) {
    body.innerHTML = '<div class="empty compact">Failed to load: ' + esc(e.message) + '</div>';
  }
}

// ---------- Mandates Worth Following (vacated-seat detector) ----------
async function loadFollowing() {
  const body = document.getElementById('following-body');
  const sub = document.getElementById('sub-following');
  try {
    const r = await fetch('/api/following');
    const j = await r.json();
    const rows = (j && j.rows) || [];
    if (!rows.length) { sub.style.display = 'none'; return 0; }
    const out = ['<ul style="margin:6px 0;padding:0;list-style:none;">'];
    for (const f of rows.slice(0, 14)) {
      const conf = (f.confidence === 'high') ? 'mandate-age' : 'hook-badge generic_fit';
      out.push(
        '<li style="padding:8px 0;border-bottom:1px solid var(--border);">' +
          '<span class="' + conf + '">' + esc(f.confidence || '') + '</span> ' +
          '<strong style="color:var(--text);">' + esc(f.company || '(unknown)') + '</strong>' +
          ' &middot; <span style="color:var(--text);">' + esc(f.vacated_role || 'senior comms seat') + '</span>' +
          '<span style="color:var(--text-muted);font-size:12px;display:block;margin-top:2px;">' +
            (f.url
              ? '<a href="' + safeUrl(f.url) + '" target="_blank" rel="noopener noreferrer" style="color:#0366d6;">' + esc(f.evidence || 'source') + '</a>'
              : esc(f.evidence || '')) +
            (f.source ? ' &middot; ' + esc(f.source) : '') +
            (f.sector ? ' &middot; ' + esc(f.sector) : '') +
          '</span>' +
          (f.advisory ? '<div class="advisory-line">' + esc(f.advisory) + '</div>' : '') +
        '</li>'
      );
    }
    out.push('</ul>');
    body.innerHTML = out.join('');
    sub.style.display = '';
    return rows.length;
  } catch (e) {
    sub.style.display = 'none';
    return 0;
  }
}

// ---------- Specialist Signals orchestrator ----------
// Loads the four low-frequency sub-detectors via their unchanged /api
// endpoints; reveals only the sub-sections that have rows, and the
// whole panel only if at least one did. All empty -> panel stays
// hidden (the dashboard never shows an empty Specialist panel).
async function loadSpecialistSignals() {
  const row = document.getElementById('specialist-row');
  const cnt = document.getElementById('specialist-count');
  let total = 0;
  try {
    const counts = await Promise.all([
      loadFollowing(), loadWaterSar(), loadContractEnd(), loadMandates(),
    ]);
    total = counts.reduce((a, b) => a + (b || 0), 0);
  } catch (e) {
    total = 0;
  }
  if (total > 0) {
    cnt.textContent = total;
    row.style.display = '';
  } else {
    row.style.display = 'none';
  }
}

// ---------- MPC Outreach Factory ----------
async function runMPC(event) {
  event.preventDefault();
  const form = document.getElementById('mpc-form');
  const btn = form.querySelector('button[type=submit]');
  const status = document.getElementById('mpc-status');
  const result = document.getElementById('mpc-result');
  const data = {};
  new FormData(form).forEach((v, k) => { data[k] = v; });
  btn.disabled = true; btn.textContent = 'Building…';
  status.textContent = ''; status.className = 'status';
  result.innerHTML = '';
  try {
    const r = await fetch('/api/mpc/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const j = await r.json();
    if (!j.ok) {
      status.textContent = j.detail || 'Failed.'; status.className = 'status err';
    } else {
      status.textContent = 'Built ' + j.count + ' hooks.'; status.className = 'status ok';
      const out = ['<ol style="margin:10px 0 0 0;padding-left:20px;">'];
      for (const h of j.hits) {
        const evHref = safeUrl(h.evidence_url);
        out.push(
          '<li style="margin-bottom:12px;">' +
            '<div><strong>' + esc(h.account) + '</strong> ' +
              '<span class="hook-badge ' + esc(h.hook_kind) + '">' + esc(h.hook_kind.replace(/_/g, ' ')) + '</span>' +
              ' <span style="color:var(--text-muted);font-size:11px;">score ' + esc(h.score) + '</span>' +
            '</div>' +
            '<div style="color:#333;margin-top:4px;">' + esc(h.hook) + '</div>' +
            (evHref !== '#' ? '<a href="' + evHref +
              '" target="_blank" rel="noopener noreferrer" style="font-size:11px;">↗ evidence</a>' : '') +
          '</li>'
        );
      }
      out.push('</ol>');
      result.innerHTML = out.join('');
    }
  } catch (e) {
    status.textContent = 'Network error: ' + e.message; status.className = 'status err';
  }
  btn.disabled = false; btn.textContent = 'Build hit list';
}

// ---------- Candidate Watch ----------
async function loadWatchList() {
  const wrap = document.getElementById('watch-list');
  const status = document.getElementById('watch-list-status');
  try {
    const r = await fetch('/api/candidates/watch');
    const j = await r.json();
    if (!j.rows || j.rows.length === 0) {
      status.textContent = 'No candidates yet — add one below.';
      wrap.innerHTML = '';
      return;
    }
    status.textContent = j.total + ' watched · sorted by call urgency';
    const out = ['<ul style="margin:8px 0 0 0;padding:0;list-style:none;">'];
    for (const c of j.rows.slice(0, 10)) {
      const overdue = c._overdue_days > 0
        ? '<span class="overdue-pill">overdue ' + esc(c._overdue_days) + 'd</span> '
        : '';
      const restless = c._restlessness_hits > 0
        ? '<span class="restless-pill">restless ×' + esc(c._restlessness_hits) + '</span> '
        : '';
      // Data attributes carry name/company so we never inject user-controlled
      // text into onclick="..." (which HTML-decodes attribute values before
      // JS parses — a name like O'Brien or '); alert(1); // would otherwise
      // break or inject script).
      const dn = esc(c.name);
      const dc = esc(c.current_company || '');
      out.push(
        '<li style="padding:8px 0;border-bottom:1px solid var(--border);">' +
          overdue + restless +
          '<strong>' + esc(c.name) + '</strong> ' +
          '<span style="color:var(--text-muted);font-size:12px;">@ ' + esc(c.current_company || '?') + '</span>' +
          (c.current_title ? '<div style="font-size:12px;color:#444;">' + esc(c.current_title) + '</div>' : '') +
          (c.last_signal ? '<div style="font-size:12px;color:#555;"><em>' + esc(c.last_signal) + '</em></div>' : '') +
          '<div style="font-size:11px;color:var(--text-muted);margin-top:2px;">' + esc(c._status_label) + '</div>' +
          '<div style="margin-top:6px;">' +
            '<button class="btn-mini watch-action" data-action="touch"  data-name="' + dn + '" data-company="' + dc + '">✓ Touched</button> ' +
            '<button class="btn-mini watch-action" data-action="snooze" data-name="' + dn + '" data-company="' + dc + '">⏸ Snooze 14d</button> ' +
            '<button class="btn-mini ghost watch-action" data-action="remove" data-name="' + dn + '" data-company="' + dc + '">✕</button>' +
          '</div>' +
        '</li>'
      );
    }
    out.push('</ul>');
    wrap.innerHTML = out.join('');
  } catch (e) {
    status.textContent = 'Failed: ' + e.message;
  }
}

// Delegated handler: data-action / data-name / data-company carry the
// payload as DOM attributes, so user-controlled text never touches an
// onclick attribute (where HTML decoding would un-escape quotes and
// break or inject JS).
document.addEventListener('click', async (event) => {
  const btn = event.target.closest('.watch-action');
  if (!btn) return;
  const action  = btn.dataset.action;
  const name    = btn.dataset.name    || '';
  const company = btn.dataset.company || '';
  if (action === 'touch') {
    const signal = prompt('What did you observe? (optional restlessness notes; e.g. "updated profile, posting more")', '') || '';
    const r = await fetch('/api/candidates/watch/touch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, current_company: company, signal }),
    });
    if (r.ok) loadWatchList();
  } else if (action === 'snooze') {
    const r = await fetch('/api/candidates/watch/snooze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, current_company: company, days: 14 }),
    });
    if (r.ok) loadWatchList();
  } else if (action === 'remove') {
    if (!confirm('Remove ' + name + ' from the watch list?')) return;
    const r = await fetch('/api/candidates/watch/remove', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, current_company: company }),
    });
    if (r.ok) loadWatchList();
  }
});

async function addWatchCandidate(event) {
  event.preventDefault();
  const form = document.getElementById('watch-add-form');
  const status = document.getElementById('watch-add-status');
  const data = {};
  new FormData(form).forEach((v, k) => { data[k] = v; });
  try {
    const r = await fetch('/api/candidates/watch/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const j = await r.json();
    if (!j.ok) { status.textContent = j.detail || 'Failed.'; status.className = 'status err'; return; }
    status.textContent = 'Added.'; status.className = 'status ok';
    form.reset();
    document.getElementById('wa-cadence').value = '30';
    loadWatchList();
  } catch (e) {
    status.textContent = 'Network error: ' + e.message; status.className = 'status err';
  }
}

// Developer/tech-only: trigger a fresh morning-brief workflow run in
// preview mode (no email). NOT the user "Daily Refresh" — that only
// pulls the last artifact. Guarded by an explicit confirm so a curious
// click can't fire a CI run blind.
async function devTriggerBrief() {
  const btn = document.getElementById('dev-run-brief');
  const status = document.getElementById('dev-run-status');
  if (!confirm(
    'DEVELOPER / TECH ACTION — not a day-to-day feature.\n\n' +
    'This starts a fresh morning-brief scour on GitHub Actions ' +
    '(~5–8 min, no email sent). The user-facing control is ' +
    '"Daily Refresh", which just loads the last completed run.\n\n' +
    'Proceed with the maintenance run?'
  )) return;
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = 'dispatching…';
  status.textContent = '';
  try {
    const r = await fetch('/api/dispatch/brief', { method: 'POST' });
    const j = await r.json();
    status.textContent = j.ok
      ? 'dispatched — ~5–8 min, then click Daily Refresh'
      : ('failed: ' + (j.detail || ('HTTP ' + r.status)));
  } catch (e) {
    status.textContent = 'network error: ' + e.message;
  }
  btn.disabled = false;
  btn.textContent = orig;
}

// Auto-load the intel panels on page ready.
document.addEventListener('DOMContentLoaded', () => {
  loadPulses();
  loadFunding();
  loadSpecialistSignals();
  loadWatchList();
});
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
