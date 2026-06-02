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
import base64
import functools
import json
import logging
import os
import sys
import zipfile
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import re
import requests
from flask import Flask, g, jsonify, make_response, redirect, render_template_string, request, Response
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

# This dashboard serves one profile per process, selected by VMA_PROFILE
# (default: comms). State lives in that profile's namespace: comms uses the
# legacy root tool/state/ (so Sara's tool is unaffected); other profiles get
# tool/state/<key>/. See tool/profiles/ and tool/state_paths.py.
from tool.profiles import active_profile
from tool.state_paths import state_dir, state_root

PROFILE = active_profile()
STATE_DIR = state_dir()
STATE_DIR.mkdir(parents=True, exist_ok=True)
# Optional map of profile-key -> absolute dashboard URL, so the chooser can
# link sibling desks that run as their own instances (same codebase, own
# VMA_PROFILE, own state). JSON in VMA_PROFILE_URLS, e.g.
#   {"marketing": "https://vma-marketing.onrender.com"}
try:
    PROFILE_URLS = json.loads(os.environ.get("VMA_PROFILE_URLS") or "{}")
    if not isinstance(PROFILE_URLS, dict):
        PROFILE_URLS = {}
except Exception:
    PROFILE_URLS = {}

# Per-REQUEST profile helpers. This single process serves BOTH desks
# (/comms and /marketing), so anything profile-dependent resolves per
# request via active_profile() (which honours the request-scoped override).
def _is_mkt() -> bool:
    return active_profile().key == "marketing"


def _default_role_label() -> str:
    return "Head of Marketing" if _is_mkt() else "Head of Internal Communications"


def _seat_fallback() -> str:
    return "senior marketing seat" if _is_mkt() else "senior comms seat"

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
    """POST to /actions/workflows/{file}/dispatches. Returns
    {ok, status, detail, dispatched_at} — the timestamp lets the UI
    poll for the artifact this run produces."""
    if not GITHUB_TOKEN:
        return {"ok": False, "detail": "GITHUB_TOKEN not set in .env"}
    url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
           f"/actions/workflows/{workflow_filename}/dispatches")
    body = {"ref": "main", "inputs": inputs}
    dispatched_at = datetime.now(timezone.utc).isoformat()
    try:
        r = requests.post(url, headers=_github_headers(), json=body, timeout=15)
        ok = r.status_code in (204, 200)
        return {"ok": ok, "status": r.status_code,
                "dispatched_at": dispatched_at,
                "detail": "Running… the report opens here when ready."
                          if ok else f"GitHub returned {r.status_code}: {r.text[:200]}"}
    except requests.RequestException as e:
        return {"ok": False, "detail": f"Network error: {e}"}


# ---- Workflow output (artifact) polling + serving --------------------
# Maps a dispatched workflow to the artifact name it uploads.
_WORKFLOW_ARTIFACT = {
    "pitch-pack.yml": "pitch-pack",
    "reverse-match.yml": "reverse-match",
    "pre-meeting-brief.yml": "pre-meeting-brief",
    "fortnightly-sweep.yml": "fortnightly-sweep",
}

# Artifact name -> human label for the Recent Reports panel.
_ARTIFACT_LABEL = {
    "pitch-pack": "Pitch Pack",
    "reverse-match": "Reverse Match",
    "pre-meeting-brief": "Pre-meeting Brief",
    "fortnightly-sweep": "Manual Sweep",
}


def _artifact_index() -> dict:
    """{artifact_name: [(created_dt, id), …] newest-first}. One API call."""
    if not GITHUB_TOKEN:
        return {}
    url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
           f"/actions/artifacts?per_page=100")
    try:
        r = requests.get(url, headers=_github_headers(), timeout=15)
        if r.status_code != 200:
            return {}
        idx: dict = {}
        for a in r.json().get("artifacts", []):
            nm = a.get("name")
            if nm not in _ARTIFACT_LABEL or a.get("expired"):
                continue
            try:
                created = datetime.fromisoformat(
                    a.get("created_at", "").replace("Z", "+00:00"))
            except Exception:
                continue
            idx.setdefault(nm, []).append((created, a.get("id")))
        for nm in idx:
            idx[nm].sort(key=lambda t: t[0], reverse=True)
        return idx
    except requests.RequestException:
        return {}


def _delete_report_artifacts() -> dict:
    """Permanently delete every report artifact from GitHub (frees the
    storage; nothing can reappear on refresh). Returns {deleted, failed}."""
    if not GITHUB_TOKEN:
        return {"deleted": 0, "failed": 0}
    deleted = failed = 0
    base = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    try:
        r = requests.get(f"{base}/actions/artifacts?per_page=100",
                         headers=_github_headers(), timeout=15)
        if r.status_code != 200:
            return {"deleted": 0, "failed": 0}
        for a in r.json().get("artifacts", []):
            if a.get("name") not in _ARTIFACT_LABEL:
                continue
            try:
                d = requests.delete(
                    f"{base}/actions/artifacts/{a.get('id')}",
                    headers=_github_headers(), timeout=15)
                if d.status_code in (204, 200):
                    deleted += 1
                else:
                    failed += 1
            except requests.RequestException:
                failed += 1
    except requests.RequestException:
        pass
    return {"deleted": deleted, "failed": failed}


def _recent_reports(hours: int = 48) -> list[dict]:
    """Reports from the last `hours`. Merges the dispatch log (gives
    Type/Company/Name) with the actual artifacts (so historical runs
    with no log entry still show, just without Company/Name). A log
    entry whose artifact hasn't appeared yet shows as 'generating'."""
    from tool import report_log
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    idx = _artifact_index()                       # {name: [(created,id)…]}
    used: set = set()
    out = []

    # 1. Logged dispatches — richest rows (Type/Company/Name).
    for e in report_log.recent(hours):
        try:
            ts = datetime.fromisoformat(e.get("ts", ""))
        except Exception:
            continue
        if ts < cutoff:
            continue
        art = e.get("artifact", "")
        aid = None
        for created, _id in idx.get(art, []):
            if _id in used:
                continue
            if created >= ts - timedelta(seconds=90):
                aid, ts_eff = _id, created
                used.add(_id)
                break
        out.append({
            "type": e.get("type", ""), "company": e.get("company", ""),
            "name": e.get("name", ""), "artifact": art,
            "ts": e.get("ts", ""), "id": aid,
        })

    # 2. Artifacts with no matching log entry (pre-log / older runs).
    for art, items in idx.items():
        for created, _id in items:
            if _id in used or created < cutoff:
                continue
            out.append({
                "type": _ARTIFACT_LABEL.get(art, art), "company": "",
                "name": "", "artifact": art,
                "ts": created.isoformat(), "id": _id,
            })

    out.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return out


def _find_output_artifact(name: str, since_iso: str) -> dict | None:
    """Newest non-expired artifact called `name` created at/after
    `since_iso` (the dispatch time). None while the run is still going
    (the artifact only exists once the run finished and uploaded)."""
    if not GITHUB_TOKEN:
        return None
    try:
        since = datetime.fromisoformat(since_iso)
    except Exception:
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
    # Small slack so clock skew between us and GitHub can't hide a hit.
    cutoff = since - timedelta(seconds=90)
    url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
           f"/actions/artifacts?per_page=40")
    try:
        r = requests.get(url, headers=_github_headers(), timeout=15)
        if r.status_code != 200:
            return None
        cands = []
        for a in r.json().get("artifacts", []):
            if a.get("name") != name or a.get("expired"):
                continue
            try:
                created = datetime.fromisoformat(
                    a.get("created_at", "").replace("Z", "+00:00"))
            except Exception:
                continue
            if created >= cutoff:
                cands.append((created, a))
        if not cands:
            return None
        cands.sort(key=lambda t: t[0], reverse=True)
        return cands[0][1]
    except requests.RequestException:
        return None


def _artifact_html(artifact_id: int) -> str | None:
    """Download an artifact zip by id and return its first .html file."""
    if not GITHUB_TOKEN:
        return None
    url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
           f"/actions/artifacts/{artifact_id}/zip")
    try:
        r = requests.get(url, headers=_github_headers(), timeout=40)
        if r.status_code != 200 or not r.content:
            return None
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            html_members = sorted(
                m for m in zf.namelist() if m.lower().endswith(".html"))
            if not html_members:
                return None
            return zf.read(html_members[0]).decode("utf-8", "replace")
    except (requests.RequestException, zipfile.BadZipFile):
        return None


# A reader skin injected at serve time so every report (pitch pack,
# reverse match, pre-meeting, sweep) looks on-brand without touching
# the four generators. The generators emit bare <html><body style=…>;
# we strip that inline body style and inject this.
_REPORT_SKIN = (
    '<meta charset="utf-8">'
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800'
    '&family=Crimson+Pro:wght@500;600&display=swap" rel="stylesheet">'
    '<style>'
    'html{background:#f7f9fc;-webkit-text-size-adjust:100%;}'
    'body{font-family:"Inter",-apple-system,system-ui,sans-serif!important;'
    'max-width:840px!important;margin:32px auto!important;padding:46px 56px!important;'
    'background:#fff!important;color:#1F1F1F!important;font-size:14px!important;'
    'line-height:1.62!important;border-radius:14px;position:relative;'
    'box-shadow:0 10px 28px rgba(31,55,124,.10),0 2px 6px rgba(31,55,124,.06);}'
    '.vma-brand{position:absolute;top:30px;right:52px;width:56px;'
    'height:auto;line-height:0;}'
    '.vma-brand svg{display:block;width:100%;height:auto;}'
    'body>h1:first-of-type,body>h2:first-of-type,'
    'body>h1:first-of-type+div,body>h2:first-of-type+div{padding-right:92px;}'
    'h1,h2,h3,h4{font-family:"Crimson Pro",Georgia,serif;color:#1F1F1F;'
    'line-height:1.25;font-weight:600;}'
    'body>h1:first-child,body>h2:first-child{font-size:26px;color:#1A3D7C;'
    'margin:0 0 4px!important;}'
    'h2{font-size:19px;margin:26px 0 8px;}h3{font-size:16px;margin:20px 0 6px;}'
    'p{margin:0 0 12px;}ul,ol{margin:6px 0 14px;padding-left:22px;}li{margin:5px 0;}'
    'a{color:#1A3D7C;}hr{border:none;border-top:1px solid rgba(60,64,67,.14);'
    'margin:22px 0;}'
    'table{border-collapse:collapse;width:100%;margin:10px 0 16px;font-size:13px;}'
    'td,th{padding:8px 10px;border-bottom:1px solid rgba(60,64,67,.14);'
    'text-align:left;}th{color:#5F6368;font-weight:600;}'
    '</style>'
)


# Inline recreation of the VMA Group mark (steel-blue square, white
# "VMA" / "GROUP"). Self-contained so the served report needs no hosted
# asset. Swap for an <img> if the real logo file is added to the repo.
_VMA_LOGO_SVG = (
    '<svg viewBox="0 0 120 110" xmlns="http://www.w3.org/2000/svg" '
    'role="img" aria-label="VMA Group">'
    '<rect width="120" height="110" rx="3" fill="#3F5E83"/>'
    '<text x="60" y="58" text-anchor="middle" '
    'font-family="Arial,Helvetica,sans-serif" font-weight="800" '
    'font-size="42" fill="#fff" letter-spacing="1">VMA</text>'
    '<text x="60" y="88" text-anchor="middle" '
    'font-family="Arial,Helvetica,sans-serif" font-weight="500" '
    'font-size="22" fill="#fff" letter-spacing="3">GROUP</text></svg>'
)
_BRAND_DIV = '<body><div class="vma-brand">' + _VMA_LOGO_SVG + '</div>'


def _skin_report_html(html: str) -> str:
    """Strip the generator's inline body style and inject the reader
    skin, so the served report is on-brand and readable."""
    import re
    html = re.sub(r"<body[^>]*>", lambda _m: _BRAND_DIV,
                   html, count=1, flags=re.I)
    if re.search(r"</head>", html, re.I):
        return re.sub(r"</head>", _REPORT_SKIN + "</head>", html,
                      count=1, flags=re.I)
    m = re.search(r"<html[^>]*>", html, re.I)
    if m:
        return (html[:m.end()] + "<head>" + _REPORT_SKIN + "</head>"
                + html[m.end():])
    return _REPORT_SKIN + html


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
        # Per-desk artifact: the marketing brief uploads "marketing-brief";
        # comms uploads "morning-brief" (+ the manual "fortnightly-sweep").
        _wanted_names = (("marketing-brief",) if _is_mkt()
                         else ("morning-brief", "fortnightly-sweep"))
        wanted = [a for a in artifacts
                  if a.get("name") in _wanted_names
                  and not a.get("expired")]
        if not wanted:
            _wf = "VMA Marketing Brief" if _is_mkt() else "Sara's Morning Brief"
            return {"ok": False, "detail": "No recent brief/sweep artifact found on GitHub Actions. "
                                            f"Trigger a brief manually: Actions tab → '{_wf}' → Run workflow."}
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
                       "or no new jobs matched the desk's criteria).")
        return {"ok": True, "detail": detail, "leads": leads_n, "predictors": predictors_n,
                "artifact_name": latest.get("name"), "artifact_created_at": latest.get("created_at"),
                "files": file_list}
    except Exception as e:
        return {"ok": False, "detail": f"Refresh failed: {e}"}


# ---- Local state loaders ------------------------------------------------
def _lead_id(s: dict) -> str:
    """Stable id for a lead so its triage status survives refreshes.
    Most scraped leads carry a source-derived 'id'; fall back to a
    hash of url|company|title when one is missing."""
    sid = (s.get("id") or "").strip()
    if sid:
        return sid
    import hashlib
    basis = f"{s.get('url','')}|{s.get('company','')}|{s.get('title','')}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def load_latest_signals() -> list[dict]:
    p = STATE_DIR / "latest_signals.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except Exception:
        return []
    # Two filters before enrichment:
    #  - drop appointment news (kind=leadership_change): the company in
    #    the headline is NOT hiring (the seat is filled). That signal
    #    still feeds the Mandates Worth Following detector via the
    #    morning brief unchanged; here it's just noise.
    #  - drop signals with no parsed company: structurally unusable
    #    (these are the "—" dash badges, mostly GDELT headlines whose
    #    entity extraction returned empty).
    data = [s for s in data
            if (s.get("kind") or "").strip().lower() != "leadership_change"
            and (s.get("company") or "").strip()]
    # Compute stable ids up-front so we can drive 7-day retention.
    # Any lead whose posting is >7d past its PUBLISHED date is dropped here
    # (and tombstoned so it can't return unless freshly re-posted) — Live
    # Jobs naturally clears without Sara having to triage stale items.
    from tool import lead_first_seen
    pre_filter = [{**s, "lead_id": _lead_id(s)} for s in data]
    kept_ids = lead_first_seen.record_and_filter(pre_filter)
    data = [s for s in pre_filter if s["lead_id"] in kept_ids]
    # The uniform sequence, identical for every lead regardless of kind:
    # resolve best-available contact (+ confidence) -> personalised draft
    # -> one precise LinkedIn click.
    from tool.hiring_manager import resolve_lead_contact
    from tool import lead_status
    try:
        from tool.contacts.store import load_contacts
        _cc = load_contacts()
    except Exception:
        _cc = {}
    _lstat = lead_status.get_statuses()
    for s in data:
        # lead_id already set during retention filter above
        s["status"] = _lstat.get(s["lead_id"], "active")
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
    from tool import predictor_status
    _ps_overlay = predictor_status.get_statuses()
    today = datetime.now(timezone.utc).date().isoformat()
    for p_item in data:
        first_seen = p_item.get("first_seen") or ""
        p_item["is_new"] = first_seen.startswith(today)
        p_item.setdefault("status", "active")
        p_item.setdefault("pid", predictor_pipeline._pid(p_item.get("company", "")))
        # Durable triage overlay wins over the (ephemeral) pipeline
        # status so followed-up/dismissed survive redeploys.
        _ov = _ps_overlay.get(p_item["pid"])
        if _ov:
            p_item["status"] = _ov
        p_item["outreach"] = draft_outreach_for_predictor(p_item)
        p_item["linkedin"] = linkedin_search_for_predictor(p_item)
        evs = p_item.get("events") or []
        p_item["advisory"] = advisory_for(
            evs[0].get("trigger_key") if evs and isinstance(evs[0], dict) else None
        )
        # Opportunity value (signal strength × imminence). The Low/Med/High
        # tier is assigned later relative to the whole Pre-Market panel
        # (see _assign_opportunity_tiers) so it can't collapse to all-Low.
        _ww = ((p_item.get("window_weeks_min"), p_item.get("window_weeks_max"))
               if p_item.get("window_weeks_min") is not None else None)
        p_item["_opp"] = predictor_pipeline.opportunity_value(
            p_item.get("score") or 0.0, _ww)
    return data


def _assign_opportunity_tiers(items: list[dict], floor: float = 0.20) -> None:
    """Relative-priority Low/Med/High tiering across the Pre-Market panel
    (predictors + funding pooled), with a dead-floor so a trivially weak
    pipeline can't fake a 'High'.

    Among items whose '_opp' clears the floor: the top ~20% are 'high', the
    next ~30% 'medium', the rest 'low'. Items below the floor are 'low'.
    Sets item['strength'] in place. This guarantees a usable spread for a
    daily worklist regardless of the absolute score range (which drifts),
    rather than collapsing everything into one band."""
    import math
    eligible = sorted(
        (it for it in items if (it.get("_opp") or 0.0) >= floor),
        key=lambda it: it.get("_opp") or 0.0, reverse=True,
    )
    m = len(eligible)
    hi_cut = math.ceil(m * 0.20)
    md_cut = math.ceil(m * 0.50)
    for i, it in enumerate(eligible):
        it["strength"] = "high" if i < hi_cut else ("medium" if i < md_cut else "low")
    for it in items:
        if (it.get("_opp") or 0.0) < floor:
            it["strength"] = "low"


# ---- Outreach message drafting -----------------------------------------
# Default outreach copy. Same message for every lead and every predictor —
# just edit the (Name) placeholder per recipient. Profile-aware specialism
# line; comms keeps the message Sara approved.
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
    import re as _re
    t = (title or "").strip()
    t = _re.split(r"\s+[–—-]\s+", t)[0]
    t = _re.split(r"\s+at\s+[A-Z]", t)[0]
    return t.split(",")[0].strip()


def draft_outreach_for_lead(signal: dict) -> str:
    """Outreach draft for a lead — fixed template with the contact's
    first name, the advertised role, and the company filled in."""
    c = signal.get("contact") or {}
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


def draft_outreach_for_predictor(_predictor: dict) -> str:
    return _default_outreach()


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


# Render's filesystem is ephemeral — pull the durably-stored copies of
# the dashboard-written state files into local state before serving, so
# a redeploy/cold-start keeps dismissed leads and the watch roster.
_HYDRATE_PATHS = [
    "tool/state/candidate_watch.json",
    "tool/state/lead_status.json",
    "tool/state/lead_first_seen.json",
    "tool/state/funding_status.json",
    "tool/state/framework_status.json",
    "tool/state/pulse_dismissed.json",
    "tool/state/report_log.json",
    "tool/state/predictor_status.json",
    "tool/state/contact_flags.json",
    "tool/state/cascade_events.json",
    "tool/state/cascade_suppression.json",
    "tool/state/top_three_state.json",
    # Morning-brief outputs — pulled on cold-start so the
    # dashboard shows leads + predictors + funding immediately
    # on opening, not just after Daily Refresh.
    "tool/state/latest_signals.json",
    "tool/state/latest_predictive.json",
    "tool/state/latest_funding.json",
    "tool/state/predictor_pipeline.json",
    # BD-Calendar auto-update pipelines (curated baseline +
    # auto-discovered placement windows / events / frameworks).
    "tool/state/calendar_pipeline_windows.json",
    "tool/state/calendar_pipeline_events.json",
    "tool/state/calendar_pipeline_frameworks.json",
]


def _hydrate_active() -> None:
    """Restore the ACTIVE desk's state namespace from the dashboard-state
    branch. github_state._ns() namespaces every path by the active profile,
    so comms restores tool/state/… and marketing restores
    tool/state/marketing/… — each desk only ever touches its own files."""
    try:
        from tool import github_state
        github_state.hydrate(list(_HYDRATE_PATHS))
    except Exception as e:
        log.warning("state hydrate skipped: %s", e)


def _boot_state_hydrate():
    """At cold start, restore EVERY desk's namespace — comms AND marketing —
    not just the env default. Both desks are served from this one process, so
    the marketing namespace must be hydrated too; without it a Render
    free-tier spin-down left the marketing desk empty/stale (zero live jobs,
    last-known BD leads) until a manual refresh. We pin VMA_PROFILE per pass
    so _ns() targets the right namespace. Safe to mutate the env here: this
    runs once at import, single-threaded, before any request is served, and
    the original value is restored afterwards."""
    from tool.profiles import all_profiles
    _prev = os.environ.get("VMA_PROFILE")
    try:
        for p in all_profiles():
            os.environ["VMA_PROFILE"] = p.key
            _hydrate_active()
    finally:
        if _prev is None:
            os.environ.pop("VMA_PROFILE", None)
        else:
            os.environ["VMA_PROFILE"] = _prev


_boot_state_hydrate()


_LAST_STATE_REFRESH: dict[str, datetime] = {}  # per-DESK last stale re-hydrate


def _brief_is_today() -> bool:
    """We hold today's brief only if the predictor pipeline is from today
    (UTC) AND actually contains predictors. Requiring non-empty predictors
    means an empty/0 pipeline always triggers the artifact pull below, even
    if its timestamp happens to look current."""
    try:
        from tool import predictor_pipeline
        pl = predictor_pipeline.load_pipeline() or {}
        ua = pl.get("updated_at") or ""
        is_today = ua[:10] == datetime.now(timezone.utc).date().isoformat()
        return is_today and bool(pl.get("predictors"))
    except Exception:
        return False


def _refresh_state_if_stale() -> None:
    """The dashboard process is long-running and only hydrates at boot, so
    after the morning brief lands it keeps serving boot-time data (e.g. 7
    predictors, or none) until a restart or a manual Daily Refresh — the
    "7 until I refreshed, then 51" / "no predictors until refresh" we saw.

    On a stale load, pull the SAME authoritative source the Daily Refresh
    button uses: the latest GitHub Actions brief artifact (not the
    dashboard-state branch, whose pipeline copy can lag the artifact).
    Bounded to once / 10 minutes so a pre-brief window never hammers the
    API. Restores leads + predictors + funding together."""
    if _brief_is_today():
        return
    now = datetime.now(timezone.utc)
    # Per-DESK throttle: comms and marketing share this ONE process, so a
    # single shared timer let busy comms traffic (or a keep-warm pinger)
    # consume the 10-min budget and permanently starve the marketing desk's
    # recovery pull — leaving it on stale boot data. Key the throttle by desk
    # so each recovers independently.
    key = active_profile().key
    last = _LAST_STATE_REFRESH.get(key)
    if last and (now - last) < timedelta(minutes=10):
        return
    _LAST_STATE_REFRESH[key] = now
    try:
        if GITHUB_TOKEN:
            res = refresh_latest_brief_from_github()
            log.info("auto stale-refresh [%s]: %s", key, res.get("detail", res))
        else:
            # No token to pull the live artifact — restore THIS desk's
            # namespace from the durable state branch instead.
            _hydrate_active()
    except Exception as e:
        log.warning("auto stale-refresh failed: %s", e)


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


def _landing_signal_pool() -> list[dict]:
    """The FULL pool of live signal chips for the landing-page scanner — built
    straight from the raw state files (NO contact-resolution / outreach
    drafting, which would be far too heavy for the landing route). Pulls every
    available BD lead (funding + predictor pre-market triggers) and every job
    lead, so the scanner can rotate through totally different ones. Each chip
    is {label, kind} where kind drives the dot colour (blue/green/gold).
    Labels are kept short so the popping chips never get massively wide."""
    def _short(s: str, n: int) -> str:
        s = (s or "").strip()
        return s if len(s) <= n else s[: n - 1].rstrip() + "…"
    pool: list[dict] = []
    try:
        # Pre-market BD triggers — funding rounds.
        from tool.funding_round import load_funding
        for f in load_funding():
            comp = _short(f.get("company") or "", 22)
            amt = (f.get("amount") or "").strip()
            if comp:
                pool.append({"label": (amt + " · " if amt else "") + comp + " · hiring",
                             "kind": "green"})
    except Exception:
        pass
    try:
        # Pre-market BD triggers — predictor seats.
        from tool import predictor_pipeline
        for p in predictor_pipeline.all_predictors():
            comp = _short(p.get("company") or "", 22)
            seat = _short(p.get("role") or p.get("seat") or _seat_fallback(), 24)
            if comp:
                pool.append({"label": comp + " · " + seat, "kind": "gold"})
    except Exception:
        pass
    try:
        # Every live job lead (the "All" set) — the bread-and-butter signal.
        p = STATE_DIR / "latest_signals.json"
        raw = json.loads(p.read_text()) if p.exists() else []
        for s in raw:
            title = _short(s.get("title") or "", 24)
            comp = _short(s.get("company") or "", 20)
            if title and comp and (s.get("kind") or "").lower() != "leadership_change":
                pool.append({"label": title + " · " + comp, "kind": "blue"})
    except Exception:
        pass
    # de-dup by label, preserve order
    seen, out = set(), []
    for ch in pool:
        if ch["label"] not in seen:
            seen.add(ch["label"])
            out.append(ch)
    return out


def _landing_signals(limit: int = 4) -> list[dict]:
    """A randomised handful of chips for the initial render — different on each
    page load so the scanner shows totally different signals over time."""
    import random
    pool = _landing_signal_pool()
    if len(pool) <= limit:
        return pool
    return random.sample(pool, limit)


# ---- Secondary-desk info page (used by the /marketing handoff) ----------
_CHOOSER_CSS = (
    ":root{--steel:#3F5E83;--deep:#1A3D7C;--ink:#1f2733;--muted:#6b7689;"
    "--line:rgba(31,39,51,.12);}*{box-sizing:border-box;}"
    "body{margin:0;min-height:100vh;font-family:-apple-system,BlinkMacSystemFont,"
    "'Segoe UI',Roboto,Arial,sans-serif;color:var(--ink);"
    "background:radial-gradient(1200px 600px at 50% -10%,#eef3fb 0%,#f7f9fc 55%,#fbfcfe 100%);"
    "display:flex;flex-direction:column;align-items:center;justify-content:center;"
    "padding:40px 20px;}"
    ".logo{width:62px;height:auto;border-radius:8px;box-shadow:0 6px 20px rgba(26,61,124,.18);}"
    "h1{font-size:24px;font-weight:700;letter-spacing:.2px;margin:18px 0 4px;color:var(--deep);}"
    ".sub{color:var(--muted);font-size:14px;margin:0 0 32px;text-align:center;"
    "max-width:520px;line-height:1.5;}"
    ".grid{display:flex;gap:20px;flex-wrap:wrap;justify-content:center;width:100%;max-width:720px;}"
    ".card{flex:1 1 280px;max-width:330px;background:#fff;border:1px solid var(--line);"
    "border-radius:16px;padding:26px 24px;text-decoration:none;color:inherit;display:block;"
    "transition:transform .15s ease,box-shadow .15s ease,border-color .15s ease;"
    "box-shadow:0 1px 2px rgba(16,24,40,.04);}"
    ".card:hover{transform:translateY(-3px);box-shadow:0 12px 30px rgba(26,61,124,.14);"
    "border-color:var(--steel);}.card.soon{opacity:.72;}"
    ".card.soon:hover{transform:none;box-shadow:0 1px 2px rgba(16,24,40,.04);border-color:var(--line);}"
    ".dot{width:10px;height:10px;border-radius:50%;background:var(--steel);display:inline-block;"
    "margin-right:8px;vertical-align:middle;}.card.soon .dot{background:#c2c9d6;}"
    ".label{font-size:19px;font-weight:700;color:var(--deep);margin:0;display:flex;align-items:center;}"
    ".blurb{color:var(--muted);font-size:13.5px;line-height:1.5;margin:10px 0 18px;}"
    ".go{font-size:13px;font-weight:600;color:var(--steel);}"
    ".pill{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.4px;"
    "text-transform:uppercase;color:#8a93a5;background:#eef1f6;border-radius:999px;padding:4px 10px;}"
    ".foot{margin-top:34px;color:#9aa3b2;font-size:12px;}a{color:var(--steel);}"
)

_DESK_INFO_TEMPLATE = (
    "<!doctype html><html lang=en><head><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width, initial-scale=1'>"
    "<title>{{ label }} &middot; VMA Intelligence</title>"
    "<style>" + _CHOOSER_CSS + "</style></head><body>"
    "<div class=logo>{{ logo|safe }}</div>"
    "<h1>{{ label }} desk</h1>"
    "<p class=sub>{{ message }}</p>"
    "<span class=pill>{{ pill }}</span>"
    "<p class=foot><a href='/'>&larr; Back</a></p>"
    "</body></html>"
)


@app.before_request
def _resolve_desk_profile():
    """Pick the desk (profile) for THIS request from the vma_profile cookie,
    defaulting to comms. The /comms and /marketing entry routes override this
    and set the cookie, so every subsequent page + API call from that desk's
    pages stays on the same desk. One process, both desks."""
    key = (request.cookies.get("vma_profile") or "comms").strip().lower()
    g.vma_profile = key if key in ("comms", "marketing") else "comms"


@app.after_request
def _no_store(resp):
    """Every page and API response is LIVE, per-desk data — never let a
    browser (especially mobile Safari/Chrome, which cache aggressively and
    don't truly purge on pull-to-refresh) replay a stale copy after a deploy
    or a desk switch. This is the difference behind 'laptop shows the fix but
    my phone still shows the old data'. No effect on the comms desk beyond
    guaranteeing it always renders the latest server state."""
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _desk_response(profile_key: str):
    """Render the dashboard for `profile_key` and pin the desk cookie so the
    page's API calls resolve to the same desk."""
    g.vma_profile = profile_key
    resp = make_response(_render_dashboard())
    # Persist the desk choice (max_age) + pin path so it survives a phone
    # backgrounding the tab / opening from a home-screen shortcut — otherwise
    # a dropped session cookie sends the desk's own API calls back to the
    # comms default. Identical for both desks, so comms is unaffected.
    resp.set_cookie("vma_profile", profile_key, samesite="Lax",
                    path="/", max_age=60 * 60 * 24 * 180)
    return resp


@app.route("/comms")
@_auth_required
def comms_desk():
    """Comms desk — the landing 'Comms · Launch App' pill lands here."""
    return _desk_response("comms")


@app.route("/marketing")
@_auth_required
def marketing_desk():
    """Marketing desk — the landing 'Marketing · Launch App' pill lands here.
    Same site, same process, marketing profile."""
    return _desk_response("marketing")


@app.route("/")
@_auth_required
def landing():
    """Gemini-clone landing — verbatim ground-truth CSS captured from
    gemini.google.com/app, ::before recentred for our viewport. VMA logo
    over a market-scanner radar whose signal chips are pulled live from the
    latest leads / pre-market triggers, and rotate through the full pool.
    The single launch pill is split into two: Comms and Marketing."""
    import random
    pool = _landing_signal_pool()
    # Initial 4: guarantee BD leads (funding=green / predictor=gold) get a slot
    # when they exist — job leads vastly outnumber them, so a flat random sample
    # would almost never surface a BD lead. Show up to 2 BD leads, fill with jobs.
    bd = [c for c in pool if c["kind"] in ("green", "gold")]
    jobs = [c for c in pool if c["kind"] == "blue"]
    random.shuffle(bd); random.shuffle(jobs)
    shown = (bd[:2] + jobs)[:4] if bd else jobs[:4]
    random.shuffle(shown)
    if len(shown) < 4:
        shown = pool[:4]
    # Both desks live on THIS instance: the pills just switch desk.
    return render_template_string(
        LANDING_TEMPLATE, signals=shown, signal_pool=pool,
        comms_href="/comms", marketing_href="/marketing",
    )


@app.route("/dashboard")
@_auth_required
def index():
    """Dashboard for the desk chosen by the vma_profile cookie (default
    comms) — so Sara's existing /dashboard bookmark is unchanged."""
    return _render_dashboard()


def _render_dashboard():
    # Keep a long-running process in sync with the morning brief: pull the
    # latest dashboard-state when our data isn't from today (bounded), so we
    # never serve boot-time-stale data until a manual refresh.
    _refresh_state_if_stale()
    from tool import cascade
    from tool.funding_round import load_funding
    predictors = load_latest_predictive()
    leads = load_latest_signals()
    # Hire Watch shows ALL events (not just active) so the filter pills
    # can switch between Active / Followed up / Dismissed in-browser.
    # Each event gets an aggregate "cs_bucket" based on its sides.
    raw_cascade = cascade.list_all()
    def _bucket(e):
        sides = [e.get("old_co_status", "active"),
                 e.get("new_co_status", "active")]
        sides = [s for s in sides if s != "n/a"]
        if any(s == "active" for s in sides):
            return "active"
        if any(s in ("called", "followed_up") for s in sides):
            return "followed_up"
        if sides and all(s == "dismissed" for s in sides):
            return "dismissed"
        return "active"
    cascade_events = [{**e, "cs_bucket": _bucket(e)} for e in raw_cascade]
    cs_counts = {
        "active":      sum(1 for e in cascade_events if e["cs_bucket"] == "active"),
        "followed_up": sum(1 for e in cascade_events if e["cs_bucket"] == "followed_up"),
        "dismissed":   sum(1 for e in cascade_events if e["cs_bucket"] == "dismissed"),
    }
    funding_events = load_funding(limit=30)
    from tool.framework_watch import load_frameworks_live
    framework_events = load_frameworks_live()
    # Decorate with stable id + persisted triage status so the
    # Followed-up / Dismissed buttons can survive a refresh.
    from tool import funding_status as _fs
    _fst = _fs.get_statuses()
    funding_events = [
        {**f, "fid": _fs.funding_id(f),
              "status": _fst.get(_fs.funding_id(f), "active")}
        for f in funding_events
    ]
    # Unified opportunity tiering across Pre-Market (predictors + funding):
    # one relative Low/Med/High scale, so the panel ranks the strongest,
    # soonest opportunities first and funding rows are tiered consistently
    # rather than left untagged.
    from tool.funding_round import opportunity_value as _funding_opp
    for _f in funding_events:
        _f["_opp"] = _funding_opp(_f)
    _assign_opportunity_tiers(predictors + funding_events)
    # Interleave funding rows among the predictors by opportunity value, so a
    # high-tier funding signal sits with the other High rows instead of being
    # stranded at the bottom. One ordered list rendered in a single loop; each
    # row carries a _kind discriminator for the template.
    for _p in predictors:
        _p["_kind"] = "predictor"
    for _f in funding_events:
        _f["_kind"] = "funding"
    premarket_rows = sorted(predictors + funding_events,
                            key=lambda d: d.get("_opp") or 0.0, reverse=True)
    from tool import framework_status as _fws
    _fwst = _fws.get_statuses()
    # `status` already holds the refresh-window state (refresh_window/live);
    # triage (active/followed_up/dismissed) goes in a separate `triage` field.
    framework_events = [
        {**fw, "triage": _fwst.get(fw["key"], "active")}
        for fw in framework_events
    ]
    # Pre-Market pills roll up predictors + funding only. Framework windows
    # live in their own panel with their own triage filter.
    _extra_triage = [f["status"] for f in funding_events]
    _fw_counts = {"active": 0, "followed_up": 0, "dismissed": 0}
    for fw in framework_events:
        _fw_counts[fw.get("triage", "active")] = _fw_counts.get(fw.get("triage", "active"), 0) + 1
    return render_template_string(
        TEMPLATE,
        example_role=_default_role_label(),
        profile_label=active_profile().label,
        leads=leads,
        predictors=predictors,
        funding_events=funding_events,
        premarket_rows=premarket_rows,
        framework_events=framework_events,
        fw_active_count=_fw_counts["active"],
        fw_followed_count=_fw_counts["followed_up"],
        fw_dismissed_count=_fw_counts["dismissed"],
        cascade_events=cascade_events,
        cs_active_count=cs_counts["active"],
        cs_followed_count=cs_counts["followed_up"],
        cs_dismissed_count=cs_counts["dismissed"],
        leads_active_count=sum(1 for s in leads if s.get("status", "active") == "active"),
        leads_new_count=sum(1 for s in leads if s.get("is_new")
                            and s.get("status", "active") == "active"),
        leads_followed_count=sum(1 for s in leads if s.get("status") == "followed_up"),
        leads_dismissed_count=sum(1 for s in leads if s.get("status") == "dismissed"),
        active_count=sum(1 for p in predictors if p.get("status") == "active")
                     + sum(1 for s in _extra_triage if s == "active"),
        new_count=sum(1 for p in predictors if p.get("is_new")
                      and p.get("status", "active") == "active"),
        followed_up_count=sum(1 for p in predictors if p.get("status") == "followed_up")
                          + sum(1 for s in _extra_triage if s == "followed_up"),
        dismissed_count=sum(1 for p in predictors if p.get("status") == "dismissed")
                        + sum(1 for s in _extra_triage if s == "dismissed"),
        last_updated=last_updated(),
        has_token=bool(GITHUB_TOKEN),
        build_stamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        deploy_rev=_DEPLOY_REV,
    )


@app.route("/api/predictor/<pid>/status", methods=["POST"])
@_auth_required
def api_predictor_status(pid: str):
    from tool import predictor_pipeline, predictor_status
    data = _safe_json_body()
    status = (data.get("status") or "").strip()
    if status not in ("active", "followed_up", "dismissed"):
        return jsonify({"ok": False, "detail": "invalid status"}), 400
    # Durable overlay is authoritative + survives redeploys; the pipeline
    # write is best-effort (keeps the in-session local file consistent,
    # and may legitimately miss if the pipeline hasn't been refreshed yet).
    predictor_status.set_status(pid, status)
    predictor_pipeline.set_status(pid, status)
    return jsonify({"ok": True, "pid": pid, "status": status})


@app.route("/api/lead/<lead_id>/status", methods=["POST"])
@_auth_required
def api_lead_status(lead_id: str):
    from tool import lead_status
    data = _safe_json_body()
    status = (data.get("status") or "").strip()
    if status not in ("active", "followed_up", "dismissed"):
        return jsonify({"ok": False, "detail": "invalid status"}), 400
    ok = lead_status.set_status(lead_id, status)
    if not ok:
        return jsonify({"ok": False, "detail": "could not set status"}), 400
    return jsonify({"ok": True, "lead_id": lead_id, "status": status})


@app.route("/api/refresh", methods=["POST"])
@_auth_required
def api_refresh():
    return jsonify(refresh_latest_brief_from_github())


@app.route("/api/dispatch/brief", methods=["POST"])
@_auth_required
def api_dispatch_brief():
    """Trigger a fresh brief run for the ACTIVE desk: the marketing desk
    dispatches the marketing brief, comms dispatches the comms brief. Email
    is off globally, so this just refreshes that desk's dashboard data."""
    if active_profile().key == "marketing":
        return jsonify(trigger_workflow("marketing-brief.yml", {"mode": "send"}))
    return jsonify(trigger_workflow("morning-brief.yml", {"mode": "preview"}))


@app.route("/api/dispatch/pitch-pack", methods=["POST"])
@_auth_required
def api_pitch_pack():
    data = _safe_json_body()
    inputs = {
        "account_name": (data.get("account_name") or "").strip(),
        "role": (data.get("role") or _default_role_label()).strip(),
        # Hard-coded to "preview" — emails for non-brief reports are
        # disabled. HTML is still generated, uploaded as a workflow
        # artifact, and surfaced via Recent Reports.
        "mode": "preview",
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
    res = trigger_workflow("pitch-pack.yml", inputs)
    res["artifact"] = _WORKFLOW_ARTIFACT["pitch-pack.yml"]
    if res.get("ok"):
        from tool import report_log
        report_log.add("Pitch Pack", inputs["account_name"],
                       "", res["artifact"])
    return jsonify(res)


@app.route("/api/dispatch/reverse-match", methods=["POST"])
@_auth_required
def api_reverse_match():
    data = _safe_json_body()
    inputs = {
        "candidate_name": (data.get("candidate_name") or "").strip(),
        "current_company": (data.get("current_company") or "").strip(),
        "current_title": (data.get("current_title") or "").strip(),
        # Hard-coded to "preview" — emails disabled for non-brief reports.
        "mode": "preview",
    }
    missing = [k for k in ("candidate_name", "current_company", "current_title")
               if not inputs[k]]
    if missing:
        return jsonify({"ok": False, "detail": f"Missing: {', '.join(missing)}"}), 400
    res = trigger_workflow("reverse-match.yml", inputs)
    res["artifact"] = _WORKFLOW_ARTIFACT["reverse-match.yml"]
    if res.get("ok"):
        from tool import report_log
        report_log.add("Reverse Match", inputs["current_company"],
                       inputs["candidate_name"], res["artifact"])
    return jsonify(res)


@app.route("/api/dispatch/pre-meeting", methods=["POST"])
@_auth_required
def api_pre_meeting():
    data = _safe_json_body()
    inputs = {
        "account_name": (data.get("account_name") or "").strip(),
        "contact_name": (data.get("contact_name") or "").strip(),
        "meeting_context": (data.get("meeting_context") or "").strip(),
        # Hard-coded to "preview" — emails disabled for non-brief reports.
        "mode": "preview",
    }
    if not inputs["account_name"]:
        return jsonify({"ok": False, "detail": "Account name required"}), 400
    res = trigger_workflow("pre-meeting-brief.yml", inputs)
    res["artifact"] = _WORKFLOW_ARTIFACT["pre-meeting-brief.yml"]
    if res.get("ok"):
        from tool import report_log
        report_log.add("Pre-meeting Brief", inputs["account_name"],
                       inputs.get("contact_name", ""), res["artifact"])
    return jsonify(res)


@app.route("/api/dispatch/sweep", methods=["POST"])
@_auth_required
def api_sweep():
    data = _safe_json_body()
    inputs = {
        "window_days": str(data.get("window_days", "14")),
        # Hard-coded to "preview" — emails disabled for non-brief reports.
        "mode": "preview",
    }
    res = trigger_workflow("fortnightly-sweep.yml", inputs)
    res["artifact"] = _WORKFLOW_ARTIFACT["fortnightly-sweep.yml"]
    if res.get("ok"):
        from tool import report_log
        report_log.add("Manual Sweep", "", "", res["artifact"])
    return jsonify(res)


@app.route("/api/output/status", methods=["GET"])
@_auth_required
def api_output_status():
    """Has the dispatched run produced its artifact yet? The browser
    polls this after triggering a workflow."""
    name = (request.args.get("artifact") or "").strip()
    since = (request.args.get("since") or "").strip()
    if not name or not since:
        return jsonify({"ready": False, "detail": "missing artifact/since"}), 400
    art = _find_output_artifact(name, since)
    if art:
        return jsonify({"ready": True, "id": art.get("id")})
    return jsonify({"ready": False})


@app.route("/api/output/view", methods=["GET"])
@_auth_required
def api_output_view():
    """Serve the report HTML out of a finished run's artifact so the
    browser tab can display it."""
    try:
        artifact_id = int(request.args.get("id") or 0)
    except (TypeError, ValueError):
        artifact_id = 0
    if not artifact_id:
        return Response("Invalid artifact id.", status=400,
                        mimetype="text/plain")
    html = _artifact_html(artifact_id)
    if html is None:
        return Response(
            "Report not available (the run may have failed, or the "
            "artifact expired). The emailed copy is the fallback.",
            status=404, mimetype="text/plain")
    skinned = _skin_report_html(html)
    if request.args.get("download"):
        art = (request.args.get("artifact") or "report").strip()
        fname = f"{art}_{artifact_id}.html"
        return Response(skinned, mimetype="text/html", headers={
            "Content-Disposition": f'attachment; filename="{fname}"'})
    return Response(skinned, mimetype="text/html")


@app.route("/api/output/recent", methods=["GET"])
@_auth_required
def api_output_recent():
    """Reports generated in the last 48h, for the Recent Reports panel."""
    rows = _recent_reports(48)
    return jsonify({"rows": rows, "total": len(rows)})


@app.route("/api/output/clear", methods=["POST"])
@_auth_required
def api_output_clear():
    """Permanently clear the panel: delete every report artifact from
    GitHub (frees storage, can't reappear) and empty the dispatch log."""
    from tool import report_log
    res = _delete_report_artifacts()
    report_log.clear_log()
    return jsonify({"ok": True, **res})


@app.route("/api/contacts/flag", methods=["POST"])
@_auth_required
def api_contacts_flag():
    """Sara hits a wrong contact -> flag it so the resolver skips it
    until the underlying entry changes."""
    from tool import contact_flags
    data = _safe_json_body()
    company = (data.get("company") or "").strip()
    slot = (data.get("slot") or "").strip()
    name = (data.get("name") or "").strip()
    if not (company and slot and name):
        return jsonify({"ok": False, "detail": "company, slot and name required"}), 400
    ok = contact_flags.flag(company, slot, name)
    return jsonify({"ok": ok})


# ---------------------------------------------------------------------------
# Demand-creation tools (in-process; no GitHub Actions roundtrip).
# These run heuristically against the existing state files. They are what
# turn the dashboard from "react fast when market moves" into "create demand
# when market is dead" — distress signals, MPC outreach factory, pipeline
# triage, objection coach, candidate watch, competitor mandates.
# ---------------------------------------------------------------------------

@app.route("/api/candidates/watch", methods=["GET"])
@_auth_required
def api_candidates_watch_list():
    """List watched candidates, sorted by call urgency."""
    from tool.candidate_watch import list_watched
    rows = list_watched()
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
        tenure_start=(data.get("tenure_start") or "").strip(),
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


# ---- Cascade-Hire Watch ----
@app.route("/api/cascade/list", methods=["GET"])
@_auth_required
def api_cascade_list():
    """Active cascade events for the dashboard panel."""
    from tool import cascade
    return jsonify({"events": cascade.list_active()})


@app.route("/api/cascade/mark", methods=["POST"])
@_auth_required
def api_cascade_mark():
    """Mark one side (old_co / new_co) of a cascade event."""
    from tool import cascade
    data = _safe_json_body()
    event_id = (data.get("event_id") or "").strip()
    side = (data.get("side") or "").strip()
    status = (data.get("status") or "").strip()
    if not (event_id and side and status):
        return jsonify({"ok": False,
                        "detail": "event_id, side and status required"}), 400
    ok = cascade.mark(event_id, side, status)
    if not ok:
        return jsonify({"ok": False,
                        "detail": "event not found or invalid input"}), 404
    return jsonify({"ok": True})


@app.route("/api/cascade/scour", methods=["POST"])
@_auth_required
def api_cascade_scour():
    """Re-parse latest_signals.json for cascade moves on demand."""
    from tool import cascade
    return jsonify({"ok": True, **cascade.scour()})


@app.route("/api/funding/mark", methods=["POST"])
@_auth_required
def api_funding_mark():
    """Mark a funding-signal row followed_up / dismissed / active.
    Persists across daily refreshes via funding_status.json."""
    from tool import funding_status
    data = _safe_json_body()
    fid = (data.get("fid") or "").strip()
    status = (data.get("status") or "").strip()
    if not fid or not status:
        return jsonify({"ok": False, "detail": "fid and status required"}), 400
    ok = funding_status.set_status(fid, status)
    if not ok:
        return jsonify({"ok": False,
                        "detail": "invalid fid or status"}), 400
    return jsonify({"ok": True})


@app.route("/api/framework/mark", methods=["POST"])
@_auth_required
def api_framework_mark():
    """Mark a framework-signal row followed_up / dismissed / active.
    Persists across daily refreshes via framework_status.json."""
    from tool import framework_status
    data = _safe_json_body()
    key = (data.get("key") or "").strip()
    status = (data.get("status") or "").strip()
    if not key or not status:
        return jsonify({"ok": False, "detail": "key and status required"}), 400
    if not framework_status.set_status(key, status):
        return jsonify({"ok": False, "detail": "invalid key or status"}), 400
    return jsonify({"ok": True})


# ---- Top-3 Action Surface ----
@app.route("/api/top-three/list", methods=["GET"])
@_auth_required
def api_top_three_list():
    """Re-compute fresh on every call — state overlay handles
    suppression. Cheap (pure parse over already-fetched data)."""
    from tool import top_three
    return jsonify({"actions": top_three.compute_top()})


@app.route("/api/top-three/mark", methods=["POST"])
@_auth_required
def api_top_three_mark():
    """Per-action state mutation. status ∈ {active, done, dismissed}."""
    from tool import top_three
    data = _safe_json_body()
    action_id = (data.get("action_id") or "").strip()
    status = (data.get("status") or "").strip()
    if not (action_id and status):
        return jsonify({"ok": False,
                        "detail": "action_id and status required"}), 400
    ok = top_three.mark(action_id, status)
    if not ok:
        return jsonify({"ok": False,
                        "detail": "invalid action_id or status"}), 400
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


@app.route("/api/pulses", methods=["GET"])
@_auth_required
def api_pulses():
    """Calendar Pulses — deterministic, date-driven placement windows.
    Computed live (days_left changes daily): a statute/regulator date
    forces a comms-capacity build-up in a named watchlist cohort; get
    the retained brief before it's advertised."""
    from tool.calendar_pulses import load_pulses
    from tool import pulse_dismiss
    dismissed = pulse_dismiss.get_dismissed()
    rows = [r for r in load_pulses(limit=20)
            if r.get("key") not in dismissed and r.get("status") != "dismissed"]
    return jsonify({"rows": rows[:10], "total": len(rows[:10])})


@app.route("/api/industry-events", methods=["GET"])
@_auth_required
def api_industry_events():
    """UK + European comms industry events (awards, conferences,
    summits) for the next ~6 months. Internal + external comms."""
    from tool.calendar_pulses import load_events
    from tool import pulse_dismiss
    dismissed = pulse_dismiss.get_dismissed()
    rows = [r for r in load_events(limit=40)
            if r.get("key") not in dismissed and r.get("status") != "dismissed"]
    return jsonify({"rows": rows[:24], "total": len(rows[:24])})


@app.route("/api/pulses/dismiss", methods=["POST"])
@_auth_required
def api_pulses_dismiss():
    """Remove (or restore) a BD-Calendar finding by its stable key.
    body: {"key": "...", "dismissed": true|false}."""
    from tool import pulse_dismiss
    data = _safe_json_body()
    key = (data.get("key") or "").strip()
    dismissed = bool(data.get("dismissed", True))
    if not key:
        return jsonify({"ok": False, "detail": "key required"}), 400
    ok = pulse_dismiss.set_dismissed(key, dismissed)
    return jsonify({"ok": ok})


@app.route("/api/calendar/<kind>/mark", methods=["POST"])
@_auth_required
def api_calendar_mark(kind):
    """Triage a BD-Calendar pipeline item (placement window / event) —
    active / followed_up / dismissed — persisted across refreshes in the
    calendar pipeline, mirroring the predictor + framework triage."""
    from tool import calendar_pipeline
    if kind not in calendar_pipeline.VALID_KINDS:
        return jsonify({"ok": False, "detail": "invalid kind"}), 400
    data = _safe_json_body()
    key = (data.get("key") or "").strip()
    status = (data.get("status") or "").strip()
    if not key or not status:
        return jsonify({"ok": False, "detail": "key and status required"}), 400
    if not calendar_pipeline.set_status(kind, key, status):
        return jsonify({"ok": False, "detail": "invalid key or status"}), 400
    return jsonify({"ok": True})


@app.route("/api/calendar/refresh", methods=["POST"])
@_auth_required
def api_calendar_refresh():
    """Manually re-scour the BD-Calendar sources on demand (the same
    discovery the morning brief runs on cron) — finds new placement
    windows, comms events and exec-search framework notices and merges
    them into the persistent pipelines."""
    from tool import calendar_discovery
    summary = calendar_discovery.refresh_all()
    return jsonify({
        "ok": True,
        "summary": {k: {"new": len(v.get("new", [])),
                        "active": v.get("total_active"),
                        "aged_out": v.get("aged_out")}
                    for k, v in summary.items()},
    })


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


# Gemini-clone landing page — body fill + blurred ::before pseudo-
# element. Ground-truth computed CSS captured from gemini.google.com/app
# (light theme, zero-state). Three positioning values (top/left/transform
# on the ::before) recentred for our blank-page viewport instead of
# Gemini's app-shell context; everything else (size, border-radius,
# radial gradient, blur, opacity, blend-mode) is verbatim from devtools.
LANDING_TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VMA Group</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@300;400;500;700&family=JetBrains+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  /* ============================================================
     L1 — VMA logo over the Gemini ground-truth halo, with a market-
     scanner radar that surfaces live recruitment/market signals.
     Gemini ::before halo values UNCHANGED (only top/left/transform
     recentred). The globe is replaced by the radar + signal chips.
     ============================================================ */
  *{box-sizing:border-box;}
  html,body{margin:0;padding:0;height:100%;}
  html{background-color:rgba(0,0,0,0);background-image:none;}
  body{
    background-color:rgb(253,252,252);
    background-image:none;
    min-height:100vh;
    position:relative;overflow:hidden;
    display:flex;flex-direction:column;align-items:center;justify-content:center;gap:34px;
  }

  /* Gemini halo — verbatim, recentred */
  body::before{
    content:"";position:absolute;z-index:-1;
    width:792px;height:300px;top:50%;left:50%;
    transform:translate(-50%,-50%);
    border-radius:9999px;
    background-image:radial-gradient(100% 100% at 50% 8%,rgb(253,252,252) 0px,rgb(157,210,255) 50%);
    filter:blur(125px);opacity:1;mix-blend-mode:normal;
  }

  /* ----- market-scanner hero: logo at centre, radar behind, signals around ----- */
  .hero{position:relative;z-index:0;width:min(440px,86vw);height:min(440px,86vw);
    display:grid;place-items:center;}
  .hero .viz{position:absolute;inset:0;z-index:0;pointer-events:none;}
  .ring-s{fill:none;stroke:rgba(58,143,164,.28);stroke-width:1;}
  .radar-sweep{transform-box:view-box;transform-origin:210px 210px;animation:sweep 5s linear infinite;}
  @keyframes sweep{to{transform:rotate(360deg);}}

  /* the real VMA logo icon (navy tile) with a slow sheen */
  .logo-tile{position:relative;z-index:3;width:108px;height:108px;border-radius:24px;overflow:hidden;
    box-shadow:0 16px 40px rgba(62,92,132,.42),0 3px 10px rgba(62,92,132,.34);}
  .logo-tile svg{display:block;width:100%;height:100%;}
  .logo-tile::after{content:"";position:absolute;inset:0;
    background:linear-gradient(115deg,transparent 38%,rgba(255,255,255,.34) 50%,transparent 62%);
    transform:translateX(-130%);animation:sheen 5s ease-in-out infinite;}
  @keyframes sheen{0%,72%{transform:translateX(-130%);}88%,100%{transform:translateX(130%);}}

  /* signal chips — recruitment / market triggers the scanner surfaces */
  .sig{position:absolute;z-index:4;display:inline-flex;align-items:center;gap:6px;
    background:#fff;border:1px solid rgba(60,64,67,.12);border-radius:9999px;
    padding:6px 11px 6px 8px;font-family:"Google Sans","Inter",Arial,sans-serif;
    font-size:11.5px;font-weight:600;color:#1A3D7C;white-space:nowrap;
    max-width:248px;box-shadow:0 6px 18px rgba(31,55,124,.12);opacity:0;transform:scale(.8);
    animation:sigpop 7s ease-in-out infinite;}
  .sig b{font-weight:inherit;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .sig i{width:7px;height:7px;border-radius:50%;background:#4285F4;flex-shrink:0;box-shadow:0 0 7px rgba(66,133,244,.6);}
  .sig.green i{background:#34A853;box-shadow:0 0 7px rgba(52,168,83,.6);}
  .sig.gold i{background:#C49A3B;box-shadow:0 0 7px rgba(196,154,59,.6);}
  @keyframes sigpop{0%,92%,100%{opacity:0;transform:scale(.8);}8%,84%{opacity:1;transform:scale(1);}}

  /* Content */
  .stage{position:relative;z-index:1;display:flex;flex-direction:column;align-items:center;gap:32px;text-align:center;}
  .wordmark{font-family:Arial,Helvetica,sans-serif;color:#1F1F1F;display:inline-flex;align-items:baseline;line-height:1;}
  .wordmark .v{font-weight:800;letter-spacing:.06em;font-size:64px;}
  .wordmark .g{font-weight:300;letter-spacing:.32em;font-size:64px;padding-left:.42em;margin-right:-.32em;}

  .pill-row{
    display:flex;gap:14px;justify-content:center;align-items:stretch;
    width:535px;max-width:90vw;flex-wrap:wrap;
  }
  .pill{
    position:relative;
    background:rgb(255,255,255);border:none;border-radius:26px;
    box-shadow:0 2px 8px -2px rgba(0,0,0,0.16);
    padding:0 40px;flex:1 1 200px;min-width:0;height:52px;max-width:none;
    display:flex;align-items:center;justify-content:center;
    text-decoration:none;color:rgb(31,31,31);cursor:pointer;
    transition:transform .15s ease, box-shadow .15s ease;
  }
  .pill:hover{transform:translateY(-1px);box-shadow:0 6px 16px -2px rgba(0,0,0,.18);}

  /* Live pulse dot — actively vibrating + glowing. Absolutely positioned on
     the OUTER edge of each pill (left dot on the left pill, right dot on the
     right pill) so the label stays perfectly centred. The dot itself throbs
     while a ::before pseudo-element radiates an expanding ring outward. */
  .dot{
    position:absolute;top:50%;margin-top:-5.5px;
    width:11px;height:11px;border-radius:50%;
    background:#9FD181;
    box-shadow:
      0 0 10px rgba(159,209,129,.85),
      inset 0 0 3px rgba(255,255,255,.4);
    animation:dot-throb 1.8s ease-in-out infinite;
  }
  .pill.dot-left .dot{left:18px;}
  .pill.dot-right .dot{right:18px;}
  .dot::before{
    content:"";position:absolute;
    inset:-3px;border-radius:50%;
    background:rgba(159,209,129,.55);
    animation:dot-ring 1.8s ease-out infinite;
  }
  .dot::after{
    content:"";position:absolute;
    inset:-6px;border-radius:50%;
    background:rgba(159,209,129,.30);
    animation:dot-ring 1.8s ease-out infinite;
    animation-delay:.6s;
  }
  @keyframes dot-throb{
    0%,100%{transform:scale(1);box-shadow:0 0 8px rgba(159,209,129,.7),inset 0 0 3px rgba(255,255,255,.4);}
    50%    {transform:scale(1.22);box-shadow:0 0 18px rgba(159,209,129,1),inset 0 0 4px rgba(255,255,255,.6);}
  }
  @keyframes dot-ring{
    0%  {transform:scale(1);opacity:.7;}
    100%{transform:scale(3.4);opacity:0;}
  }

  .lbl{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:13px;letter-spacing:.26em;text-transform:uppercase;font-weight:500;}

  @media (max-width:720px){
    .stage{gap:24px;}
    .pill-row{width:92%;}
    .pill{flex:1 1 100%;height:56px;}
    .lbl{font-size:11px;letter-spacing:.18em;}
    .logo-tile{width:92px;height:92px;}
    /* shrink + edge-anchor the signal chips so they never sit over the logo on
       a narrow hero. Cap width and push to the corners. */
    .sig{font-size:10px;padding:5px 9px 5px 7px;max-width:46vw;}
    .sig b{max-width:38vw;}
    .sig[data-slot="0"]{top:2%;left:-2%;}
    .sig[data-slot="1"]{top:18%;right:-4%;}
    .sig[data-slot="2"]{bottom:18%;left:-4%;}
    .sig[data-slot="3"]{bottom:2%;right:-2%;}
  }
  @media (prefers-reduced-motion: reduce){
    .radar-sweep,.logo-tile::after,.dot,.dot::before,.dot::after{animation:none;}
    .sig{opacity:1;transform:none;animation:none;}
  }
</style>
</head>
<body>
  <div class="stage">
    <div class="hero">
      <svg class="viz" viewBox="0 0 420 420" aria-hidden="true">
        <g class="ring-s">
          <circle cx="210" cy="210" r="84"/><circle cx="210" cy="210" r="138"/><circle cx="210" cy="210" r="192"/>
          <line x1="18" y1="210" x2="402" y2="210"/><line x1="210" y1="18" x2="210" y2="402"/>
        </g>
        <defs><linearGradient id="sweepg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="rgba(66,133,244,0)"/><stop offset="1" stop-color="rgba(66,133,244,.34)"/>
        </linearGradient></defs>
        <g class="radar-sweep"><path d="M210 210 L210 18 A192 192 0 0 1 346 74 Z" fill="url(#sweepg)"/></g>
      </svg>
      {% set pos = ['top:8%;left:0%', 'top:24%;right:-2%', 'bottom:20%;left:-2%', 'bottom:7%;right:4%'] %}
      {% set delays = ['0s', '1.6s', '3.1s', '4.6s'] %}
      {% if signals %}
        {% for sg in signals %}
        <span class="sig {{ sg.kind if sg.kind != 'blue' else '' }}" data-slot="{{ loop.index0 }}" style="{{ pos[loop.index0 % 4] }};animation-delay:{{ delays[loop.index0 % 4] }}"><i></i><b>{{ sg.label }}</b></span>
        {% endfor %}
      {% else %}
        <span class="sig"       data-slot="0" style="top:8%;left:0%;animation-delay:0s"><i></i><b>Head of Comms &middot; NHS</b></span>
        <span class="sig green" data-slot="1" style="top:24%;right:-2%;animation-delay:1.6s"><i></i><b>&pound;37m Series B &middot; hiring</b></span>
        <span class="sig gold"  data-slot="2" style="bottom:20%;left:-2%;animation-delay:3.1s"><i></i><b>CEO exit &middot; CCO opening</b></span>
        <span class="sig"       data-slot="3" style="bottom:7%;right:4%;animation-delay:4.6s"><i></i><b>Director of Comms &middot; FTSE</b></span>
      {% endif %}
      <div class="logo-tile"></div>
    </div>
    <div class="pill-row">
      <a class="pill dot-left" href="{{ comms_href }}">
        <span class="dot"></span>
        <span class="lbl">Communications</span>
      </a>
      <a class="pill dot-right" href="{{ marketing_href }}">
        <span class="dot"></span>
        <span class="lbl">Marketing</span>
      </a>
    </div>
  </div>
  <script>
    var LOGO = '<svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">'
      + '<rect width="100" height="100" fill="#3E5C84"/>'
      + '<text x="50" y="55" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-weight="800" font-size="30" letter-spacing="-1.5" fill="#fff">VMA</text>'
      + '<text x="51" y="76" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-weight="300" font-size="13.5" letter-spacing="3" fill="#fff">GROUP</text>'
      + '</svg>';
    document.querySelectorAll('.logo-tile').forEach(function (e) { e.innerHTML = LOGO; });

    // Chips pop in / hold / fade out on the mockup's exact CSS loop (sigpop,
    // 7s, staggered per chip). JS only swaps each chip's label to a fresh LIVE
    // signal during the invisible part of its cycle — so the next pop-in shows
    // a new lead. Pure content swap; the pop/fade animation is the mockup's.
    var POOL = {{ signal_pool | tojson | safe if signal_pool else '[]' }};
    (function () {
      var chips = Array.prototype.slice.call(document.querySelectorAll('.sig[data-slot]'));
      if (!chips.length || POOL.length <= chips.length) return;
      var KIND = { green: 'sig green', gold: 'sig gold', blue: 'sig' };
      var BD = POOL.filter(function (c) { return c.kind === 'green' || c.kind === 'gold'; });
      var JOBS = POOL.filter(function (c) { return c.kind === 'blue'; });
      var shown = {};
      chips.forEach(function (c) { var b = c.querySelector('b'); if (b) shown[b.textContent] = 1; });

      // Kind-preserving swap: a chip showing a BD lead (green/gold) refreshes to
      // another BD lead, a job chip to another job — so the initial mix (2 BD +
      // 2 jobs, set server-side) is kept for the life of the page.
      function swap(chip) {
        var b = chip.querySelector('b'); if (!b) return;
        var isBD = chip.classList.contains('green') || chip.classList.contains('gold');
        var src = isBD ? BD : JOBS;
        if (src.length <= 1) return;   // nothing else of this kind to rotate to
        var pick, tries = 0;
        do { pick = src[Math.floor(Math.random() * src.length)]; tries++; }
        while (shown[pick.label] && tries < 25);
        if (shown[pick.label]) return;
        shown[b.textContent] = 0; shown[pick.label] = 1;
        b.textContent = pick.label;                 // swap text + dot colour
        chip.className = KIND[pick.kind] || 'sig';
      }

      // A chip's content may ONLY change while the chip is invisible. Each loop
      // we set a "pending swap" flag; an rAF watcher then performs the swap the
      // instant the chip's computed opacity is ~0 (the fade-out plateau), and
      // never while any of it is still visible. So the new lead is only ever
      // revealed on the chip's NEXT fade-in — never appearing before the old
      // one has fully gone.
      var pending = new WeakSet();
      chips.forEach(function (chip) {
        chip.addEventListener('animationiteration', function (e) {
          if (e.animationName === 'sigpop') pending.add(chip);
        });
      });
      function watch() {
        chips.forEach(function (chip) {
          if (pending.has(chip) && parseFloat(getComputedStyle(chip).opacity) <= 0.02) {
            pending.delete(chip);
            swap(chip);
          }
        });
        requestAnimationFrame(watch);
      }
      requestAnimationFrame(watch);
    })();
  </script>
</body>
</html>
"""



TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VMA Group</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Crimson+Pro:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      /* Gemini-feel palette. Variable names preserved so the rest of the
         dashboard CSS inherits the new colours — only the values change.
         "navy" maps to Gemini text colour, "teal" maps to Gemini blue. */
      --navy: #1F1F1F;
      --navy-deep: #0F1A2E;
      --navy-soft: rgba(31, 31, 31, 0.06);
      --navy-hairline: rgba(31, 31, 31, 0.10);
      --teal: #4285F4;
      --teal-bright: #5B9BFF;
      --teal-dark: #1A3D7C;
      --teal-glow: rgba(66, 133, 244, 0.22);
      --teal-soft: rgba(66, 133, 244, 0.10);
      --bg: #f7f9fc;
      --bg-warm: #E3EAF5;
      --surface: #FFFFFF;
      --surface-elevated: #F4F7FC;
      --border: rgba(60, 64, 67, 0.12);
      --border-hover: rgba(66, 133, 244, 0.36);
      --text: #1F1F1F;
      --text-muted: #5F6368;
      --text-dim: #9AA0A6;
      --gold: #C49A3B;
      --green: #34A853;
      /* Upgrade-pill palette — sampled from the attached Gemini button.
         Used by .big-refresh, .action-card button, and active filter pills. */
      --btn-bg: #C9DDF8;
      --btn-bg-hover: #B8D0F2;
      --btn-text: #1A3D7C;
      --btn-border: rgba(26, 61, 124, 0.10);
      --shadow-sm: 0 1px 2px rgba(60, 64, 67, 0.05);
      --shadow-md: 0 6px 22px rgba(60, 64, 67, 0.08), 0 1px 3px rgba(60, 64, 67, 0.04);
      --shadow-lg: 0 10px 28px rgba(31, 55, 124, 0.10), 0 2px 6px rgba(31, 55, 124, 0.06);
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; }
    body {
      font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      /* Background matches the landing page exactly: near-white ground with
         the verbatim Gemini halo (see body::before), applied to the body so
         all three pages share the identical soft-blue glow. */
      background-color: rgb(253, 252, 252);
      color: var(--text);
      line-height: 1.5;
      font-weight: 400;
      font-size: 13.5px;
      font-feature-settings: "ss01", "cv11", "cv02", "cv03";
      -webkit-font-smoothing: antialiased;
      letter-spacing: -0.005em;
      position: relative;
      min-height: 100vh;
      overflow: hidden;
    }
    /* Gemini halo — verbatim from LANDING_TEMPLATE, recentred on the viewport
       so the dashboard's background is identical to the landing page. */
    body::before {
      content: "";
      position: fixed;
      z-index: -1;
      width: 792px; height: 300px;
      top: 50%; left: 50%;
      transform: translate(-50%, -50%);
      border-radius: 9999px;
      background-image: radial-gradient(100% 100% at 50% 8%, rgb(253, 252, 252) 0px, rgb(157, 210, 255) 50%);
      filter: blur(125px);
      pointer-events: none;
    }
    .serif { font-family: "Crimson Pro", Georgia, serif; }

    /* TOP BAR — Gemini-feel centered hero. Soft blue halo on the tinted
       page bg; VMA GROUP wordmark centred; "Intelligence Platform · Live"
       subtitle below with a pulsing green liveness dot to the left. No
       hard rule at the bottom — halo bleeds gently into the dashboard. */
    .top-bar {
      position: relative;
      overflow: hidden;
      min-height: 170px;
      padding: 36px 30px 32px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 11px;
      text-align: center;
      /* Opus v2 halo — tight 48% ellipse, six-stop front-loaded fade.
         Final stop is the body bg colour at 0 alpha, so the halo
         dissolves seamlessly into the dashboard body — no visible seam. */
      background:
        radial-gradient(
          ellipse 48% 55% at 50% 55%,
          #a8c8e6 0%,
          #bdd3e9 18%,
          #d2e1ee 40%,
          #e8eff6 65%,
          #f5f8fb 85%,
          rgba(247, 249, 252, 0) 100%
        ),
        var(--bg);
    }
    .top-bar .brand-line-1 {
      font-family: Arial, Helvetica, sans-serif;
      color: var(--text);
      display: inline-flex;
      align-items: baseline;
      line-height: 1;
    }
    .top-bar .bm-vma {
      font-weight: 800;
      letter-spacing: 0.06em;
      font-size: 34px;
    }
    .top-bar .bm-group {
      font-weight: 300;
      letter-spacing: 0.32em;
      font-size: 34px;
      padding-left: 0.42em;
      /* letter-spacing adds trailing space after the final "P"; cancel it
         so the wordmark sits truly centred rather than ~11px to the left. */
      margin-right: -0.32em;
    }
    /* Floating caption under the wordmark — NOT a pill. The liveness dot is
       part of the line, so the whole dot+text unit is centred (which sits the
       text slightly right of the wordmark centre, balancing the dot). */
    .top-bar .sub-cap {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-family: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace;
      color: var(--text-muted);
      font-size: 10.5px;
      letter-spacing: 0.26em;
      text-transform: uppercase;
      font-weight: 400;
    }
    .top-bar .sub-cap::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #9FD181;
      flex-shrink: 0;
      animation: live-pulse 2.4s ease-in-out infinite;
    }
    @keyframes live-pulse {
      0%   { box-shadow: 0 0 0 0 rgba(159, 209, 129, 0.75), 0 0 7px rgba(159, 209, 129, 0.6); }
      70%  { box-shadow: 0 0 0 10px rgba(159, 209, 129, 0), 0 0 12px rgba(159, 209, 129, 0.9); }
      100% { box-shadow: 0 0 0 0 rgba(159, 209, 129, 0),    0 0 7px rgba(159, 209, 129, 0.6); }
    }
    @media (max-width: 720px) {
      .top-bar { min-height: 130px; padding: 26px 16px 22px; gap: 9px; }
      .top-bar .bm-vma, .top-bar .bm-group { font-size: 24px; }
      .top-bar .sub-cap { font-size: 9.5px; letter-spacing: 0.18em; }

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

    .item.predictor .row-summary .chips {
      grid-area: chips;
      display: flex;
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
    /* No accent stripe — clean Gemini-feel refresh bar. */
    .big-refresh {
      background: var(--btn-bg);
      color: var(--btn-text);
      border: 1px solid var(--btn-border);
      padding: 10px 20px;
      border-radius: 999px;
      font-family: inherit;
      font-size: 12.5px;
      font-weight: 600;
      letter-spacing: 0.005em;
      cursor: pointer;
      transition: background 0.15s ease, transform 0.15s ease;
      box-shadow: 0 1px 2px rgba(31, 55, 124, 0.06);
      white-space: nowrap;
    }
    .big-refresh:hover {
      background: var(--btn-bg-hover);
      transform: translateY(-1px);
    }
    .big-refresh:active {
      transform: translateY(0);
    }
    .big-refresh:disabled {
      background: #E5E8EE;
      color: #9AA0A6;
      box-shadow: none;
      cursor: not-allowed;
      transform: none;
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
    .rr-table { width: 100%; border-collapse: collapse; }
    .rr-table th {
      text-align: left; font-size: 10px; letter-spacing: 0.06em;
      text-transform: uppercase; color: var(--text-dim);
      font-weight: 700; padding: 16px 0 9px; border-bottom: 1px solid var(--border);
    }
    .rr-table td {
      padding: 12px 0; border-bottom: 1px solid var(--border);
      font-size: 12.5px; vertical-align: middle;
    }
    .rr-table th:not(:first-child),
    .rr-table td:not(:first-child) { padding-left: 22px; }
    .rr-table tr:last-child td { border-bottom: none; }
    .rr-table tr:hover td { background: var(--surface-elevated); }
    .rr-type { font-weight: 600; white-space: nowrap; }
    .rr-when { color: var(--text-muted); white-space: nowrap; font-size: 11.5px; }
    .rr-muted { color: var(--text-dim); }
    .rr-acts { text-align: right; white-space: nowrap; }
    .rr-acts a { margin-left: 6px; }
    .rr-gen { color: var(--text-muted); font-size: 11px; font-style: italic; }
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
      background: linear-gradient(180deg, rgba(66, 133, 244, 0.04) 0%, rgba(255, 255, 255, 0) 100%);
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

    /* ===== Calendar & Context panels — Placement Windows + Events +
       Frameworks. In the 3-page layout these live in the BD Calendar page
       (hosted in #cal-host, opened in a modal). ===== */
    /* ===== Unified findings list (.row2 — Framework Eligibility windows use
       this row template) =====
       One clean row per finding; click the head to expand its detail. A small
       HW/FW tag on the right names the type. */
    .row2 { border-bottom: 1px solid var(--border); }
    .row2:last-child { border-bottom: none; }
    .row2-head { display: flex; align-items: center; gap: 10px; padding: 11px 4px; cursor: pointer; }
    .row2-head:hover { background: #f7f9fc; }
    .row2-title { flex: 1; min-width: 0; font-weight: 600; font-size: 12.5px; color: var(--navy);
                  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .row2-tags { display: flex; align-items: center; gap: 6px; flex: none; }
    .ipill { font: 600 9.5px/1.4 "Inter", sans-serif; padding: 2px 7px; border-radius: 10px; white-space: nowrap; letter-spacing: .02em; }
    .ipill.s { background: #e7f3ec; color: #2e7d50; }
    .ipill.w { background: var(--teal-soft); color: var(--teal-dark); }
    .ipill.mut { background: #eef0f3; color: #80868b; }   /* not open to bid yet */
    /* Leading type badge (replaces the bullet dot), slightly enlarged. */
    .typ { font: 800 11px/1 "Inter", sans-serif; padding: 5px 7px; border-radius: 5px; letter-spacing: .06em; flex: none; }
    .typ.hw { background: #e7f3ec; color: #2e7d50; }
    .typ.fw { background: #ece9f7; color: #5b4ea6; }
    .row2-chev { color: #9aa0a6; font-size: 14px; flex: none; transition: transform .15s; }
    .row2.open .row2-chev { transform: rotate(90deg); }
    .row2-detail { display: none; padding: 0 4px 12px 25px; }
    .row2.open .row2-detail { display: block; }
    .row2-sub { font-size: 11.5px; color: var(--text-muted); margin: 0 0 8px; }
    .plays { display: flex; flex-direction: column; gap: 8px; }
    .play { border: 1px solid var(--border); border-radius: 8px; padding: 9px 11px; background: #fbfcfe; margin-top: 8px; }
    .play-lab { font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--teal-dark); }
    .play.search .play-lab { color: #2e7d50; }
    .play-desc { font-size: 12px; color: var(--navy); margin-top: 3px; }
    .play .item-actions { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
    /* Funding-signal sub-section inside Predicted Briefs — visually
       same row template so funding rows sit inline among the
       tenure-driven predictors. */
    .funding-chip-inline {
      background: var(--teal-bg, #e8eff6) !important;
      color: var(--teal-dark, #1f377c) !important;
      font-weight: 700; letter-spacing: .04em;
    }
    .framework-chip-inline {
      background: #ece9f7 !important;
      color: #4a3d82 !important;
      font-weight: 700; letter-spacing: .04em;
    }

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
    /* Pips wrap into mini-rows when a month has many findings, instead
       of overflowing a single line. row-gap keeps the rows tight. */
    .cal-pips {
      display: flex; gap: 5px; row-gap: 4px;
      flex-wrap: wrap; justify-content: flex-end;
      max-width: 70px;
    }
    .cal-pip { width: 9px; height: 9px; border-radius: 50%; }
    .cal-pip.high  { background: var(--teal); }   /* high = teal */
    .cal-pip.med   { background: var(--green); }  /* policy-firming = green */
    /* NEW month: same reddening wash as a fresh finding row */
    .cal-tile.fresh {
      background: linear-gradient(90deg, var(--teal-soft), transparent 72%);
      border-color: var(--border-hover);
      border-left: 3px solid var(--teal);
    }
    .cal-tile.fresh .cal-mlab { color: var(--teal-dark); }

    /* New-finding notifier: a bright segment "shoots" around the month
       tile's outline. Implemented with an animated conic gradient
       masked to a 2px ring (a @property-driven angle so it animates
       smoothly). Sits above the tile fill but below its content. */
    @property --cal-shoot-angle {
      syntax: '<angle>';
      initial-value: 0deg;
      inherits: false;
    }
    .cal-tile.fresh::after {
      content: "";
      position: absolute;
      inset: 0;
      border-radius: inherit;
      padding: 2px;
      pointer-events: none;
      background: conic-gradient(
        from var(--cal-shoot-angle),
        transparent 0deg,
        transparent 250deg,
        rgba(58,143,164,.35) 300deg,
        #2fa8d8 340deg,
        #cdeaff 352deg,
        #ffffff 358deg,
        transparent 360deg);
      -webkit-mask:
        linear-gradient(#000 0 0) content-box,
        linear-gradient(#000 0 0);
      -webkit-mask-composite: xor;
              mask-composite: exclude;
      animation: cal-shoot 2.4s linear infinite;
    }
    @keyframes cal-shoot { to { --cal-shoot-angle: 360deg; } }
    @media (prefers-reduced-motion: reduce) {
      .cal-tile.fresh::after { animation: none; opacity: 0; }
    }
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
    .cal-card-head { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
    .cal-rm {
      margin-left: auto;
      background: transparent; color: var(--text-muted);
      border: 1px solid var(--border); border-radius: 4px;
      padding: 2px 7px;
      font: 500 10px/1.4 "Inter", sans-serif;
      cursor: pointer;
      transition: border-color .12s, color .12s;
    }
    .cal-rm:hover { border-color: #A33A22; color: #A33A22; }
    .cal-seat { font-size: 12px; color: var(--text); margin-top: 6px; }
    .cal-angle { font-size: 11.5px; color: var(--text-muted); margin-top: 4px; }
    .cal-scope { font-size: 11px; color: var(--text-dim); margin-top: 6px; }
    .cal-legend { display: flex; gap: 16px; font-size: 10px; color: var(--text-dim); margin-top: 10px; }
    .cal-legend i { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 6px; vertical-align: middle; }
    .cal-dsep { border: 0; border-top: 1px solid var(--border); margin: 10px 0; }

    /* ===== Events & Networking — light chronological list ===== */
    /* Events & Networking — v21 date-tile layout: a calendar date chip, then
       the event with its focus + location. */
    .ev-list { list-style: none; margin: 0; padding: 4px 0; }
    .ev-item { border-bottom: 1px solid var(--hairline); }
    .ev-item:last-child { border-bottom: none; }
    .ev-row { display: flex; align-items: center; gap: 12px; padding: 11px 16px; }
    .ev-item.has-detail .ev-row { cursor: pointer; }
    .ev-row:hover { background: var(--elevated); }
    .ev-chev { margin-left: 2px; flex-shrink: 0; color: var(--dim); font-size: 18px; line-height: 1; transition: transform .15s; }
    .ev-item.expanded .ev-chev { transform: rotate(90deg); }
    .ev-detail { display: none; padding: 2px 16px 12px 56px; }
    .ev-item.expanded .ev-detail { display: block; }
    .ev-date { flex-shrink: 0; width: 40px; height: 40px; border-radius: 10px;
      background: var(--blue-wash); color: var(--blue-deep); display: flex; flex-direction: column;
      align-items: center; justify-content: center; line-height: 1; }
    .ev-date b { font: 700 15px/1 "Inter", sans-serif; }
    .ev-date span { font: 600 7.5px/1 "JetBrains Mono", monospace; letter-spacing: .08em;
      text-transform: uppercase; margin-top: 2px; }
    .ev-main { flex: 1; min-width: 0; }
    .ev-n { font-size: 12.5px; font-weight: 600; color: var(--ink); }
    .ev-t { font: 600 8.5px/1 "JetBrains Mono", monospace; letter-spacing: .06em; text-transform: uppercase;
      color: var(--dim); margin-top: 5px; display: flex; gap: 7px; align-items: center; flex-wrap: wrap; }
    .ev-foc { padding: 2px 6px; border-radius: 5px; background: var(--elevated); color: var(--ink-2); }
    .ev-open { font: 700 8px/1.3 "Inter", sans-serif; letter-spacing: .03em; text-transform: none;
      background: #e7f3ec; color: #2e7d50; padding: 2px 7px; border-radius: 10px; margin-left: 7px; }
    .ev-why { font-size: 11px; color: var(--muted); margin-top: 5px; }
    .ev-rm { background: transparent; color: var(--muted); border: 1px solid var(--border);
      border-radius: 6px; padding: 3px 7px; font: 500 11px/1 "Inter", sans-serif; cursor: pointer;
      flex-shrink: 0; transition: border-color .12s, color .12s; }
    .ev-rm:hover { border-color: #A33A22; color: #A33A22; }
    /* Placement Windows — each window drawn as a glass window-pane tile
       (frame + cross mullions) beside its role + timing. */
    .win-list { padding: 4px 0; }
    .win-row { display: flex; align-items: flex-start; gap: 13px; padding: 13px 16px;
      border-bottom: 1px solid var(--hairline); }
    .win-row:last-child { border-bottom: none; }
    .win-row:hover { background: var(--elevated); }
    .win-tile { position: relative; flex-shrink: 0; width: 46px; height: 46px; border-radius: 5px;
      background: linear-gradient(155deg, #cbe0f5 0%, #eaf3fc 70%); border: 2px solid var(--blue-deep);
      box-shadow: inset 0 0 0 1.5px #fff; margin-top: 1px; }
    .win-tile::before { content: ""; position: absolute; left: 50%; top: 3px; bottom: 3px; width: 2px;
      background: var(--blue-deep); opacity: .5; transform: translateX(-50%); }
    .win-tile::after { content: ""; position: absolute; top: 50%; left: 3px; right: 3px; height: 2px;
      background: var(--blue-deep); opacity: .5; transform: translateY(-50%); }
    .win-main { flex: 1; min-width: 0; }
    .win-name { font-size: 12.5px; font-weight: 600; color: var(--ink); line-height: 1.35; }
    .win-seat { font-size: 11px; color: var(--muted); margin-top: 4px; line-height: 1.4; }
    .win-tags { margin-top: 8px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
    .conf-pill { font: 600 8.5px/1 "JetBrains Mono", monospace; letter-spacing: .06em;
      text-transform: uppercase; padding: 4px 8px; border-radius: 9999px; }
    .conf-pill.high { background: var(--grn-bg); color: var(--grn-tx); }
    .conf-pill.med { background: var(--tan-bg); color: var(--tan-tx); }
    .win-days { font: 600 9.5px/1 "JetBrains Mono", monospace; color: var(--ink-2); }
    /* "Found" = auto-discovered (vs hand-curated) BD-calendar item. */
    .found-pill { font: 700 8px/1 "JetBrains Mono", monospace; letter-spacing: .05em;
      text-transform: uppercase; padding: 2px 6px; border-radius: 9999px; vertical-align: 1px;
      background: var(--blue-soft, #e6effb); color: var(--blue-deep, #1a4f9c); }
    .win-scope { font-size: 11px; color: var(--muted); margin-top: 6px; line-height: 1.45; }
    /* Framework Eligibility — 'structural framework' treatment: each row framed
       like a built structure (left girder + corner joints). */
    #framework-body .framework-row { position: relative; margin: 12px 12px 0;
      padding: 13px 15px 13px 18px; border: 1px solid var(--border); border-radius: 4px;
      background: var(--elevated); }
    #framework-body .framework-row:last-child { margin-bottom: 12px; }
    #framework-body .framework-row::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0;
      width: 3px; border-radius: 4px 0 0 4px; background: linear-gradient(180deg, var(--blue-deep), var(--blue)); }
    #framework-body .framework-row::after { content: ""; position: absolute; inset: 5px; pointer-events: none;
      background:
        linear-gradient(var(--border-hi),var(--border-hi)) left top/11px 2px no-repeat,
        linear-gradient(var(--border-hi),var(--border-hi)) left top/2px 11px no-repeat,
        linear-gradient(var(--border-hi),var(--border-hi)) right bottom/11px 2px no-repeat,
        linear-gradient(var(--border-hi),var(--border-hi)) right bottom/2px 11px no-repeat; }

    /* ===== Groundwork row: Events & Networking + Framework Eligibility =====
       Band-C reference pair; matched height + internal scroll like the
       Hire Watch / Placement Windows row above. */
    /* Calendar & Context strip: three compact columns in one row, reclaiming
       the vertical space the old two-row groundwork layout used. */
    #context-row { grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: start; }
    #context-row > .panel { min-width: 0; }
    #context-row .ctx-col { height: 340px; display: flex; flex-direction: column; }
    #context-row .ctx-col .panel-body { max-height: none; flex: 1; min-height: 0; overflow-y: auto; }
    @media (max-width: 1100px) { #context-row { grid-template-columns: 1fr 1fr; } }
    @media (max-width: 760px)  { #context-row { grid-template-columns: 1fr; } }
    /* Eligibility-not-a-lead disclaimer atop Framework Eligibility. */
    .fw-note {
      font-size: 11px; color: var(--text-dim);
      padding: 10px 16px 4px; line-height: 1.4;
    }

    /* ITEMS */
    .item {
      padding: 11px 16px;
      border-bottom: 1px solid var(--border);
      transition: background 0.15s ease;
    }
    .item:hover { background: rgba(66, 133, 244, 0.03); }
    .item:last-child { border-bottom: 0; }
    .item .rank {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px; height: 18px;
      background: var(--teal-soft);
      color: var(--teal-dark);
      border: 1px solid rgba(66, 133, 244, 0.25);
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
      border: 1px solid rgba(66, 133, 244, 0.30);
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

    /* Predicted role + opportunity-strength chips on the row summary */
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
    /* Opportunity-strength band (replaces the old probability %). */
    .strength-chip {
      display: inline-flex;
      align-items: center;
      padding: 2px 9px;
      font-size: 10.5px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      border-radius: 8px;
      border: 1px solid transparent;
      cursor: help;
    }
    .strength-chip.s-high   { color: #2e7d50; background: #e7f3ec; border-color: #bfe3cd; }
    .strength-chip.s-medium { color: #8a5a00; background: #fff4e0; border-color: #f0d9ad; }
    .strength-chip.s-low    { color: var(--text-muted); background: var(--surface-elevated); border-color: var(--border); }
    .panel-header h2 .window-sub {
      font-weight: 400;
      color: var(--text-muted);
      font-size: 12px;
      letter-spacing: 0;
      margin-left: 4px;
    }

    /* PIPELINE — NEW badge, status badges, filter pills */
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
    .item.lead[data-status="dismissed"] { opacity: 0.55; }
    .item.lead[data-status="followed_up"] { opacity: 0.8; }

    .filter-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      background: var(--bg);
    }
    .filter-pill, .lead-filter-pill {
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
    .filter-pill:hover, .lead-filter-pill:hover {
      border-color: var(--teal);
      color: var(--teal-dark);
    }
    .filter-pill.active, .lead-filter-pill.active {
      background: var(--btn-bg);
      color: var(--btn-text);
      border-color: var(--btn-border);
    }
    .filter-pill .pill-count, .lead-filter-pill .pill-count {
      display: inline-block;
      font-size: 10px;
      padding: 1px 6px;
      background: rgba(66, 133, 244, 0.10);
      color: var(--btn-text);
      border-radius: 10px;
      font-weight: 700;
    }
    .filter-pill.active .pill-count,
    .lead-filter-pill.active .pill-count {
      background: rgba(26, 61, 124, 0.10);
      color: var(--btn-text);
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
    /* Candidate Watch — compact CRM row (Mockup A) */
    .cw-row{display:grid;grid-template-columns:1fr auto;gap:4px 12px;
      align-items:center;padding:9px 4px;border-bottom:1px solid var(--border);}
    .cw-row:last-child{border-bottom:none;}
    .cw-row:hover{background:var(--surface-elevated);}
    .cw-nm{font-size:13px;font-weight:600;}
    .cw-sub{font-size:11.5px;color:var(--text-muted);margin-top:1px;}
    .cw-state{font-size:11px;color:var(--text-muted);margin-top:2px;}
    .cw-tags{margin-top:4px;display:flex;gap:5px;flex-wrap:wrap;}
    .cw-pill{display:inline-flex;align-items:center;font-size:10px;font-weight:700;
      padding:2px 7px;border-radius:10px;letter-spacing:.02em;white-space:nowrap;}
    .cw-pill.overdue{background:#F3D7CC;color:#8C3A1E;}
    .cw-pill.due{background:#E2EAD9;color:#4A6233;}
    .cw-pill.open{background:#EAE3D2;color:#6B5B33;cursor:help;}
    /* Drift-score pill — primary ordering signal on Candidate Watch.
       Colour ramps by score: hot (≥60) = bright coral; warm (30-59) =
       muted coral; low (<30) = neutral grey. Tooltip explains the
       composite score. */
    .cw-pill.drift{cursor:help;}
    .cw-pill.drift.hot{background:rgba(201,100,66,.22);color:#A04E32;}
    .cw-pill.drift.warm{background:rgba(201,100,66,.10);color:#A04E32;}
    .cw-pill.drift.low{background:rgba(140,120,80,.10);color:#7A7164;}
    /* Reason chips — explain WHY a candidate is high-ranked. Small,
       lightweight, hover to see full reason text. */
    .cw-pill.reason{background:#F4F2EC;color:#5F574A;font-weight:500;
                    font-size:9.5px;letter-spacing:0;cursor:help;
                    max-width:220px;overflow:hidden;text-overflow:ellipsis;}
    .cw-right{display:flex;gap:6px;align-items:center;}
    /* Scoped to .action-card so it beats the big ".action-card button"
       primary style (which otherwise turns these into huge orange
       blocks). All three are identical small icon buttons. */
    .action-card .cw-iconbtn{
      width:auto;margin:0;padding:5px 8px;background:transparent;
      color:var(--text-muted);border:1px solid var(--border);
      border-radius:6px;box-shadow:none;letter-spacing:normal;
      font:inherit;font-size:14px;line-height:1;cursor:pointer;transition:.14s;}
    .action-card .cw-iconbtn:hover{
      background:var(--bg-warm);color:var(--text);border-color:var(--teal);}
    .action-card .cw-iconbtn.danger:hover{color:#A33A22;border-color:#A33A22;}
    .action-card .cw-iconbtn.cw-tick{color:var(--green);}
    .action-card .cw-iconbtn.cw-tick:hover{background:var(--bg-warm);color:var(--green);border-color:var(--green);}
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
      background: rgba(66, 133, 244, 0.05);
    }
    /* Title on its own row, chips wrapped on the row beneath — so a long
       company name (e.g. "Close Brothers Group") always shows in full and
       can never be squeezed to an ellipsis by, or overlap with, the chips. */
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
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .item.predictor .row-summary .expand-toggle { grid-area: toggle; }
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
    .item.predictor .row-preview .signal-sub {
      color: var(--text-muted);
      font-weight: 600;
      letter-spacing: 0.01em;
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
    .show-more:hover { background: rgba(66, 133, 244, 0.07); }

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

    /* Recent Reports as the 6th action-card. Single-line-per-report,
       a numbered blue square (matching the signals/leads rank badge)
       indexes each report, one download icon button.
       h3 stays identical to the other action cards (same flex baseline
       and the brand-dot ::before pseudo) so it lines up across the
       3x2 grid; the Clear button is absolutely positioned in the
       top-right corner so it doesn't disturb h3 alignment. */
    .recent-card { position: relative; }
    /* Scoped to .action-card so this beats the broader
       ".action-card button" rule that paints Run buttons as full-width
       blue pills. Without that scoping the Clear control inherits the
       Run-button styling. */
    .action-card .rr-clear {
      position: absolute; top: 16px; right: 18px;
      width: auto;
      margin: 0;
      background: transparent; color: var(--text-muted);
      border: 1px solid var(--border); border-radius: 4px;
      padding: 2px 7px;
      font: 500 10px/1.4 "Inter", sans-serif;
      letter-spacing: 0;
      cursor: pointer;
      box-shadow: none;
      transition: border-color .12s, color .12s, transform .12s;
    }
    .action-card .rr-clear:hover {
      background: transparent;
      transform: none;
      border-color: var(--accent, #3A8FA4);
      color: var(--navy, #1F1F1F);
    }
    .recent-card #recent-reports { display: flex; flex-direction: column; }
    .rr2 {
      display: grid;
      grid-template-columns: 18px 1fr auto auto;
      gap: 9px; align-items: center;
      padding: 9px 0;
      border-bottom: 1px solid var(--border);
      font-size: 12.5px;
    }
    .rr2:last-child { border-bottom: none; }
    /* Numbered blue square — mirrors the signals/leads .rank badge so
       reports index 1,2,3… in the same visual language. */
    .rr2-num {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px; height: 18px;
      background: var(--teal-soft);
      color: var(--teal-dark);
      border: 1px solid rgba(66, 133, 244, 0.25);
      border-radius: 4px;
      font-size: 10px;
      font-weight: 600;
    }
    /* Row name styled to MATCH the field-label typography used across
       the other action cards (e.g. "WINDOW (DAYS)", "CANDIDATE NAME"
       in Manual Sweep / Reverse Match): 9.5px, weight 600, uppercase,
       0.1em tracking, navy. That puts the row entries at the same
       visual hierarchy as form-field labels — distinctly subordinate
       to the h3 "Recent Reports" heading. */
    .rr2-name {
      color: var(--navy, #1F1F1F);
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      min-width: 0;
      font-size: 9.5px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }
    .rr2-name .rr2-primary { /* inherits everything from .rr2-name */ }
    .rr2-name .rr2-type {
      color: var(--text-muted);
      font-weight: 600;     /* same weight, just muted to differentiate */
    }
    .rr2-age {
      color: var(--text-muted);
      font: 500 11px/1 "JetBrains Mono", monospace;
    }
    /* Download icon button — sized like the other icon controls, with a
       faded square that appears on hover and deepens on click. */
    .rr2-icon {
      width: 28px; height: 28px; border-radius: 6px;
      border: none; background: transparent;
      color: var(--text-muted); text-decoration: none;
      display: inline-flex; align-items: center; justify-content: center;
      cursor: pointer;
      transition: background .12s, color .12s;
    }
    .rr2-icon:hover { background: rgba(60, 64, 67, 0.10); color: var(--navy, #1F1F1F); }
    .rr2-icon:active { background: rgba(60, 64, 67, 0.18); }
    .rr2-icon svg { width: 16px; height: 16px; display: block; }
    .rr2-gen {
      color: var(--text-muted); font-size: 10.5px;
      font-style: italic;
    }
    /* Recent Reports page: full-width table (Type · Company · Name · Created · Report). */
    .recent-card { margin: 20px auto 0; max-width: 100%; width: 100%; text-align: left; }
    .rr-empty { color: var(--text-muted); font-size: 12px; padding: 10px 0 2px; }
    .recent-card #recent-reports:not(:empty) + .rr-empty { display: none; }
    .rr-table { width: 100%; border-collapse: collapse; }
    .rr-table th {
      text-align: left; font-size: 10px; letter-spacing: 0.06em;
      text-transform: uppercase; color: var(--text-dim, #80868b);
      font-weight: 700; padding: 6px 0 9px; border-bottom: 1px solid var(--border);
    }
    .rr-table td {
      padding: 12px 0; border-bottom: 1px solid var(--border);
      font-size: 12.5px; vertical-align: middle; color: var(--navy, #1F1F1F);
    }
    .rr-table th:not(:first-child), .rr-table td:not(:first-child) { padding-left: 44px; }
    /* Let the Company / Name columns take the slack so columns breathe. */
    .rr-table th:nth-child(2), .rr-table th:nth-child(3) { width: 28%; }
    .rr-table th:nth-child(1) { width: 18%; }
    .rr-table tr:last-child td { border-bottom: none; }
    .rr-table tr:hover td { background: rgba(60, 64, 67, 0.035); }
    .rr-type { font-weight: 600; white-space: nowrap; }
    .rr-when { color: var(--text-muted); white-space: nowrap; font-size: 11.5px; }
    .rr-muted { color: var(--text-dim, #aab); }
    .rr-acts { text-align: right; white-space: nowrap; }
    .rr-acts a { vertical-align: middle; }
    .rr-acts a + a { margin-left: 8px; }
    .rr-gen { color: var(--text-muted); font-size: 10.5px; font-style: italic; }

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
      background: var(--btn-bg);
      color: var(--btn-text);
      border: 1px solid var(--btn-border);
      border-radius: 999px;
      font-size: 11.5px;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      transition: background 0.15s ease, transform 0.15s ease;
      letter-spacing: 0.02em;
      box-shadow: 0 1px 2px rgba(31, 55, 124, 0.06);
    }
    .action-card button:hover {
      background: var(--btn-bg-hover);
      transform: translateY(-1px);
    }
    .action-card button:disabled {
      background: #E5E8EE;
      color: #9AA0A6;
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
      background: var(--btn-bg);
      color: var(--btn-text);
      border: 1px solid var(--btn-border);
      border-radius: 999px;
      font-size: 11.5px;
      font-weight: 600;
      text-align: center;
      cursor: pointer;
      letter-spacing: 0.02em;
      transition: background 0.15s ease, transform 0.15s ease;
      box-shadow: 0 1px 2px rgba(31, 55, 124, 0.06);
    }
    .action-card summary.add-toggle::-webkit-details-marker { display: none; }
    .action-card summary.add-toggle::marker { content: ""; }
    .action-card summary.add-toggle:hover {
      background: var(--btn-bg-hover);
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
    /* Brand tagline — same treatment as the top-bar "Intelligence
       Platform · Live" sub-cap (JetBrains Mono, uppercase, wide tracking). */
    .footer .brand-tag {
      display: inline-block;
      font-family: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace;
      text-transform: uppercase;
      letter-spacing: 0.26em;
      font-size: 10.5px;
      font-weight: 400;
      color: var(--text-muted);
    }
    .footer .brand-tag .sep { margin: 0 0.5em; color: var(--text-dim); }

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

    /* =====================================================================
       3-PAGE SHELL (layout-only refactor — v21 mockup). New, additive CSS
       only. Existing rules above are untouched. New colour vars added to
       :root below (only the ones not already defined). Collision-safe class
       names; form-field restyling is scoped to .composer .cform so it never
       leaks to other inputs.
       ===================================================================== */
    :root {
      --ink: #1F1F1F; --ink-2: #3C4043; --muted: #5F6368; --dim: #9AA0A6;
      --vma: #3E5C84;
      --blue: #4285F4; --blue-bright: #5B9BFF; --blue-deep: #1A3D7C;
      --blue-soft: #C9DDF8; --blue-wash: #E8F0FE;
      --pulse: #9FD181;
      --hairline: rgba(31,31,31,.06);
      --elevated: #F4F7FC;
      --border-hi: rgba(66,133,244,.4);
      --grn-bg: #E8F5EC; --grn-tx: #1E7A41;
      --tan-bg: #FFF3E0; --tan-tx: #9A6516;
      --r: 16px;
      /* --green already defined above — not redeclared. */
    }

    /* page becomes one viewport tall; the body scroll is owned by each
       page's internal scroll region. */
    html, body { height: 100%; }
    body.has-shell { overflow: hidden; }

    /* ----- left rail ----- */
    .rail { position: fixed; top: 0; left: 0; bottom: 0; width: 62px; display: flex;
      flex-direction: column; align-items: center; gap: 8px; padding: 20px 0; z-index: 1000; }
    .rail .ri { width: 42px; height: 42px; border-radius: 12px; border: none; background: transparent;
      color: var(--muted); display: grid; place-items: center; transition: all .14s; position: relative; cursor: pointer; }
    .rail .ri svg { width: 20px; height: 20px; }
    .rail .ri:hover { background: rgba(31,31,31,.05); color: var(--ink); }
    .rail .ri.active { background: rgba(31,31,31,.07); color: var(--ink); }
    /* VMA logo pinned to the bottom of the rail — same 42x42 faded-navy
       square + radius as the nav icons, holding the navy logo tile. */
    /* Recent-Reports download icon sits at the bottom of the rail, just
       above the VMA logo (margin-top:auto pushes it + the logo down). */
    .rail .ri-bottom { margin-top: auto; }
    .rail .rail-logo { margin-top: 12px; margin-bottom: -13px; width: 42px; height: 42px; border-radius: 12px;
      overflow: hidden; flex-shrink: 0; position: relative; display: grid; place-items: center;
      background: rgba(62,92,132,.09); }
    .rail .rail-logo svg { display: block; width: 100%; height: 100%; border-radius: 12px; }
    .rail [data-tip]:hover::after { content: attr(data-tip); position: absolute; left: 54px; top: 50%;
      transform: translateY(-50%); background: var(--ink); color: #fff; font: 500 11px/1 "Inter", sans-serif;
      padding: 6px 9px; border-radius: 7px; white-space: nowrap; z-index: 5; box-shadow: var(--shadow-md); }

    /* ----- stage + pages ----- */
    .stage { padding: 0 62px; height: 100vh; overflow: hidden; }
    .page { display: none; height: 100vh; overflow: hidden; animation: pgfade .35s ease; }
    .page.active { display: flex; flex-direction: column; }
    @keyframes pgfade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

    /* ----- shared wordmark header (page 1) ----- */
    .wm-head { flex: none; text-align: center; padding: 46px 0 26px;
      background: radial-gradient(ellipse 78% 150% at 50% -8%,
        #a8c8e6 0%, #bdd3e9 14%, #d2e1ee 32%, #e8eff6 56%, #f4f7fb 76%, rgba(247,249,252,0) 100%); }
    .wm-head .brand { display: inline-flex; align-items: center; gap: 14px; }
    .brand-title { font-family: "Newsreader", Georgia, serif; font-weight: 400; font-size: 30px;
      letter-spacing: -.01em; color: var(--ink); }
    /* radar icon at the end of the "Market Opportunities Radar" title — line-art
       dish + dome with a slow sweep, in the brand navy. */
    /* radar icon at the end of the "Market Opportunities Radar" title — matches
       the BD-Calendar page hero tile exactly: same 78px faded-navy square,
       same radius + svg size, with a slow sweep on the dish. */
    .radar-ic { width: 78px; height: 78px; border-radius: 18px; display: grid; place-items: center;
      color: var(--vma); background: rgba(62,92,132,.09); flex-shrink: 0; align-self: center; }
    .radar-ic svg { width: 38px; height: 38px; transform-origin: center; animation: radar-spin 6s linear infinite; }
    @keyframes radar-spin { to { transform: rotate(360deg); } }

    /* ----- page 1: leads/signals one-viewport scroll chain -----
       page (flex col, fixed vh) -> container (flex:1, min-height:0) ->
       row (flex:1, min-height:0) -> panel (min-height:0) -> panel-body
       (scrolls). The refresh bar + specialist row + footer are flex:none. */
    #leads .container { flex: 1; min-height: 0; display: flex; flex-direction: column;
      padding-top: 0; padding-bottom: 10px; overflow: hidden; }
    #leads .refresh-bar { flex: none; }
    #leads .warn-banner { flex: none; }
    #leads .row { flex: 1; min-height: 0; margin-bottom: 0; display: grid;
      grid-template-columns: 1fr 1fr; gap: 16px; }
    @media (max-width: 880px) { #leads .row { grid-template-columns: 1fr; } }
    #leads .row .panel { min-height: 0; }
    #leads .row .panel-body { flex: 1; min-height: 0; max-height: none; overflow-y: auto; }
    #leads #specialist-row { flex: none; }
    #leads .footer { flex: none; padding: 10px 18px; margin-top: 0; }
    /* the refresh button keeps its icon + label inline */
    .big-refresh { display: inline-flex; align-items: center; gap: 7px; }
    .big-refresh svg { flex-shrink: 0; }

    /* ===== Page 1 reskin — match the approved mockup's Leads & Signals look.
       CSS-only: maps the existing markup/classes onto the mockup's visual
       language. No data, markup or JS changes. ===== */
    #leads .container { max-width: 1700px; width: 100%; margin: 0 auto; }
    /* refresh bar -> mockup .refresh (white card, blue accent rail) */
    #leads .refresh-bar { display: flex; align-items: center; gap: 16px; padding: 13px 18px;
      background: #fff; border: 1px solid var(--border); border-radius: 12px;
      box-shadow: var(--shadow-sm); position: relative; overflow: hidden; margin-bottom: 16px; }
    #leads .refresh-bar::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0;
      width: 4px; background: linear-gradient(180deg, var(--blue-bright), var(--blue-deep)); }
    #leads .big-refresh { background: var(--blue-wash); color: var(--blue-deep);
      border: 1px solid rgba(26,61,124,.12); border-radius: 9999px; padding: 10px 18px;
      font: 600 12.5px/1 "Inter"; }
    #leads .big-refresh:hover { background: #d6e4fb; }
    #leads .refresh-meta { display: flex; flex-direction: column; gap: 3px; }
    #leads .refresh-label { font-size: 13px; font-weight: 600; color: var(--ink); }
    #leads .refresh-sub { font-size: 11px; color: var(--muted); }
    /* panels -> mockup .panel + .ph */
    #leads .panel { background: #fff; border: 1px solid var(--border); border-radius: 12px;
      box-shadow: var(--shadow-md); overflow: hidden; display: flex; flex-direction: column; }
    #leads .panel-header { display: flex; align-items: center; justify-content: space-between;
      padding: 12px 16px; border-bottom: 1px solid var(--hairline);
      background: linear-gradient(180deg, rgba(66,133,244,.03), transparent); }
    #leads .panel-header h2 { font-size: 10.5px; font-weight: 600; letter-spacing: .13em;
      text-transform: uppercase; color: var(--blue-deep); display: flex; align-items: center; gap: 8px; }
    #leads .panel-header h2::before { content: ""; width: 5px; height: 5px; border-radius: 50%;
      background: var(--blue); box-shadow: 0 0 6px rgba(66,133,244,.5); }
    #leads .panel-header .count { font: 600 10px/1 "JetBrains Mono"; color: var(--blue-deep);
      background: var(--blue-wash); padding: 3px 9px; border-radius: 9999px; }
    /* filter tabs -> mockup .tabs/.tab */
    #leads .filter-bar { display: flex; gap: 6px; padding: 10px 14px;
      border-bottom: 1px solid var(--hairline); flex-wrap: wrap; }
    #leads .lead-filter-pill, #leads .filter-pill { border: none; background: transparent;
      border-radius: 9999px; padding: 6px 11px; font: 600 11px/1 "Inter"; color: var(--muted);
      display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
    #leads .lead-filter-pill:not(.active):hover, #leads .filter-pill:not(.active):hover {
      background: var(--elevated); color: var(--ink); }
    #leads .lead-filter-pill.active, #leads .filter-pill.active {
      background: var(--blue-wash); color: var(--blue-deep); }
    #leads .pill-count { font: 600 9.5px/1 "JetBrains Mono"; background: rgba(31,31,31,.06);
      color: var(--ink-2); padding: 2px 6px; border-radius: 9999px; }
    #leads .lead-filter-pill.active .pill-count, #leads .filter-pill.active .pill-count {
      background: #fff; color: var(--blue-deep); }
    /* item rows -> mockup .it tokens (layout structure is already analogous) */
    #leads .item { padding: 13px 16px; border-bottom: 1px solid var(--hairline); }
    #leads .item:hover { background: var(--elevated); }
    #leads .item .rank { width: 19px; height: 19px; background: var(--blue-wash);
      color: var(--blue-deep); border: none; border-radius: 5px; font: 600 10px/1 "JetBrains Mono"; }
    #leads .item .title { font-size: 13px; font-weight: 600; color: var(--ink); }
    #leads .item .title a { color: var(--ink); }
    #leads .item .title a:hover { color: var(--blue-deep); border-bottom-color: var(--blue); }
    #leads .item .meta { margin-top: 8px; margin-left: 27px; gap: 6px; font-size: 11px; }
    #leads .item .meta .badge { background: var(--elevated); border: 1px solid var(--border);
      border-radius: 6px; padding: 4px 9px; font: 500 10.5px/1.3 "Inter"; color: var(--ink-2); }
    #leads .item .outreach-text { display: none; }   /* mockup is copy-only, no inline outreach */
    #leads .item .item-actions { margin-top: 9px; margin-left: 27px; gap: 7px; }
    #leads .btn-mini { font: 500 10.5px/1 "Inter"; padding: 6px 11px; border-radius: 7px;
      border: 1px solid var(--border); background: #fff; color: var(--blue-deep);
      letter-spacing: 0; gap: 5px; }
    #leads .btn-mini:hover { background: var(--blue); color: #fff; border-color: var(--blue); }
    #leads .btn-mini.ghost { color: var(--muted); border-color: var(--border); }
    #leads .btn-mini.ghost:hover { background: #fbecea; color: #A33A22; border-color: #e7b9b1; }

    /* ----- page 2: Personal Assistant ----- */
    #agent .agent-wrap { flex: 1; min-height: 0; overflow-y: auto; max-width: 900px; margin: 0 auto;
      padding: 40px 24px; text-align: center; display: flex; flex-direction: column;
      align-items: center; justify-content: center; }
    #reports .reports-wrap { flex: 1; min-height: 0; overflow-y: auto; max-width: 1280px; margin: 0 auto;
      padding: 40px 40px; text-align: center; display: flex; flex-direction: column;
      align-items: stretch; }
    #reports .ea-hero { margin-bottom: 26px; }
    .ea-hero { text-align: center; }
    .cc-bigicon { width: 78px; height: 78px; border-radius: 18px; margin: 0 auto 22px; display: grid;
      place-items: center; color: var(--vma); background: rgba(62,92,132,.09); }
    .cc-bigicon svg { width: 38px; height: 38px; }
    .gemini-title { font-family: "Newsreader", Georgia, serif; font-weight: 400; font-size: 34px;
      letter-spacing: -.01em; color: var(--ink); text-align: center; }
    .cc-sub { font-size: 13.5px; color: var(--muted); margin-top: 11px; }
    /* Agent page: full-size hero sits just above the centred composer pill. */
    #agent .ea-hero { margin-bottom: 26px; }

    /* COMPOSER PILL — verbatim spec from approved mockup. */
    .composer { box-sizing: content-box; width: 672px; max-width: 100%; background: #fff;
      border: 1px solid transparent; border-radius: 20px;
      box-shadow: 0 4px 20px rgba(0,0,0,.07), 0 0 0 .5px rgba(31,31,30,.286);
      padding: 0; margin: 0 auto; text-align: left; transition: border-color .2s, box-shadow .2s; }
    .composer:focus-within { border-color: rgba(31,31,30,.20); }
    .composer .inner { display: flex; flex-direction: column; margin: 14px; gap: 12px; }
    .composer .cform { min-height: 48px; max-height: 384px; overflow-y: auto; padding: 6px 0 0 6px; transition: all .2s; }
    .composer .cfoot { display: flex; justify-content: flex-end; align-items: center; }
    /* Footer send shows in free mode; chip-forms keep their own native submit arrow. */
    .composer:not([data-mode="free"]) .cfoot .send { display: none; }
    .cinput { border: none; outline: none; background: transparent; width: 100%;
      font-family: "Inter", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      font-size: 16px; line-height: 22.4px; font-weight: 430; color: rgb(11,11,11); }
    .cinput::placeholder { color: var(--dim); }
    .cf-head { display: flex; align-items: center; gap: 9px; font-size: 15px; font-weight: 600; color: var(--ink); }
    .cf-head .cf-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--blue); }
    .cf-desc { font-size: 12.5px; color: var(--muted); margin-top: 7px; line-height: 1.5; }
    .cf-label { display: block; font: 600 9.5px/1 "JetBrains Mono", monospace; letter-spacing: .12em;
      text-transform: uppercase; color: var(--ink-2); margin: 15px 0 7px; }
    .cf-input { width: 100%; padding: 11px 14px; border: 1px solid var(--border); border-radius: 10px;
      font: 400 13.5px/1.3 "Inter", sans-serif; color: var(--ink); background: #fff; }
    .cf-input::placeholder { color: var(--dim); }
    .composer .cf-input:focus { outline: none; border-color: var(--blue); box-shadow: 0 0 0 3px rgba(66,133,244,.12); }
    .send { width: 30px; height: 30px; border-radius: 50%; background: #a8c8e6; color: #1A3D7C;
      display: grid; place-items: center; font-size: 16px; border: none; flex-shrink: 0; transition: all .14s; cursor: pointer; }
    .send:hover { background: #93b9de; color: #10294f; }
    .send svg { width: 16px; height: 16px; }
    /* arrow submit sits at the bottom-right of each composer form box */
    .composer .cform form { display: flex; flex-direction: column; }
    .composer .cform .send { align-self: flex-end; order: 99; margin-top: 10px; }
    /* Restyle the MOVED action-forms' bare label/input/button to the cf-* look.
       Scoped to .composer .cform so it never touches other inputs/labels. */
    .composer .cform label { display: block; font: 600 9.5px/1 "JetBrains Mono", monospace;
      letter-spacing: .12em; text-transform: uppercase; color: var(--ink-2); margin: 15px 0 7px; }
    /* :not(.cinput) — restyle ONLY the moved pack-form fields to the boxed
       cf-* look. The free-text prompt (.cinput) is also an <input> inside
       .composer .cform, and this selector outranks `.cinput`, so without the
       exclusion it drew a bordered box around the free-text field. Excluding
       it keeps the prompt borderless/transparent (Claude/ChatGPT/Gemini-style)
       while the pack forms keep their boxes. No sizing of the pill changes. */
    .composer .cform input:not(.cinput), .composer .cform select, .composer .cform textarea {
      width: 100%; padding: 11px 14px; border: 1px solid var(--border); border-radius: 10px;
      font: 400 13.5px/1.3 "Inter", sans-serif; color: var(--ink); background: #fff; }
    .composer .cform input::placeholder { color: var(--dim); }
    .composer .cform input:not(.cinput):focus, .composer .cform select:focus, .composer .cform textarea:focus {
      outline: none; border-color: var(--blue); box-shadow: 0 0 0 3px rgba(66,133,244,.12); }
    /* hide the original full-width Run buttons; the corner arrow submits instead */
    .composer .cform form > button[type="submit"]:not(.send) { display: none; }
    .cf-formhead { margin-bottom: 4px; }
    .cap-form { display: none; }
    .cap-form.active { display: block; }
    /* ambiguous-prompt chooser — "which of the four?" inside the pill */
    .cap-choose .choose-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin-top: 14px; }
    .cap-choose .choose-opt { display: block; width: 100%; text-align: left; cursor: pointer;
      background: #fff; border: 1px solid var(--border); border-radius: 11px; padding: 12px 14px;
      font: 600 13px/1 "Inter", sans-serif; color: var(--ink); transition: all .14s; }
    .cap-choose .choose-opt:hover { background: var(--blue-wash); border-color: var(--blue); color: var(--blue-deep); }
    @media (max-width: 640px) { .cap-choose .choose-grid { grid-template-columns: 1fr; } }
    /* status line inside the moved forms (loses .action-card scoping) */
    .composer .cform .status { margin-top: 10px; padding: 8px 11px; border-radius: 8px;
      font-size: 11.5px; display: none; line-height: 1.4; }
    .composer .cform .status.ok { background: var(--blue-wash); color: var(--blue-deep);
      border-left: 2px solid var(--blue); display: block; }
    .composer .cform .status.err { background: rgba(201,59,43,.08); color: #8B2C20;
      border-left: 2px solid #C93B2B; display: block; }

    /* chips below the pill (scoped to #agent so it never hits the predictor .chips) */
    #agent .chips { display: flex; flex-wrap: wrap; gap: 9px; justify-content: center; margin-top: 22px; max-width: 600px; }
    #agent .chip { display: inline-flex; align-items: center; gap: 8px; background: transparent;
      border: 1px solid var(--border); border-radius: 11px; padding: 10px 14px; font: 500 13px/1 "Inter", sans-serif;
      color: var(--ink); transition: all .14s; cursor: pointer; }
    #agent .chip:hover { background: var(--elevated); border-color: var(--border-hi); }
    #agent .chip .i { color: var(--ink-2); font-size: 13px; }
    #agent .chip.active { background: var(--ink); color: #fff; border-color: var(--ink); }
    #agent .chip.active .i { color: #fff; }

    /* Candidate Watch + Recent Reports moved onto page 2 — keep them in a
       centred column under the composer; reuse their existing .action-card
       look by keeping the .action-card class on the cards. */
    #agent .agent-extras { width: 100%; max-width: 900px; margin: 30px auto 0; display: grid;
      grid-template-columns: 1fr 1fr; gap: 16px; text-align: left; }
    @media (max-width: 760px) { #agent .agent-extras { grid-template-columns: 1fr; } }
    #agent .agent-extras .action-card { height: auto; max-height: 420px; }

    /* ----- page 3: BD Calendar (Customize-Claude-style menu + modal) ----- */
    #cal .cc-wrap { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column;
      align-items: center; justify-content: center; max-width: 720px; margin: 0 auto; padding: 40px 24px; width: 100%; }
    .cc-hero { text-align: center; margin-bottom: 30px; }
    .cc-cards { width: 100%; display: flex; flex-direction: column; gap: 14px; }
    .cc-card { display: flex; align-items: center; gap: 16px; background: #fff; border: 1px solid var(--border);
      border-radius: 22px; padding: 20px 22px; box-shadow: var(--shadow-sm); cursor: pointer;
      transition: box-shadow .16s, border-color .16s, transform .16s; text-align: left; width: 100%; }
    .cc-card:hover { box-shadow: var(--shadow-md); border-color: var(--border-hi); transform: translateY(-1px); }
    .cc-card .ci { width: 44px; height: 44px; border-radius: 12px; background: var(--elevated); display: grid;
      place-items: center; color: var(--vma); flex-shrink: 0; font-size: 18px; }
    .cc-card .cx { flex: 1; display: flex; flex-direction: column; min-width: 0; }
    .cc-card .ct { font-size: 15px; font-weight: 600; color: var(--ink); }
    .cc-card .cd { font-size: 12.5px; color: var(--muted); margin-top: 3px; }
    .cc-card .cbadge { font: 600 9px/1 "JetBrains Mono", monospace; letter-spacing: .06em; text-transform: uppercase;
      color: #fff; background: var(--blue); padding: 4px 8px; border-radius: 9999px; flex-shrink: 0; }
    .cc-card .cbadge:empty { display: none; }
    .cc-card .cv { color: var(--dim); font-size: 22px; flex-shrink: 0; }
    .cal-host { display: none; }

    .bd-modal-backdrop { position: fixed; inset: 0; background: rgba(20,28,46,.28); backdrop-filter: blur(5px);
      z-index: 1100; display: none; align-items: center; justify-content: center; padding: 40px; }
    .bd-modal-backdrop.open { display: flex; animation: pgfade .2s ease; }
    .bd-modal { background: #fff; border: 1px solid var(--border); border-radius: 18px; box-shadow: var(--shadow-lg);
      width: 100%; max-width: 620px; max-height: 82vh; display: flex; flex-direction: column; overflow: hidden; }
    .bd-modal .mh { flex: none; display: flex; align-items: center; gap: 11px; padding: 15px 18px;
      border-bottom: 1px solid var(--hairline); }
    .bd-modal .mh-ic { width: 34px; height: 34px; border-radius: 10px; background: var(--elevated);
      color: var(--vma); display: grid; place-items: center; font-size: 15px; flex-shrink: 0; }
    .bd-modal .mh-t { font-size: 15px; font-weight: 600; flex: 1; }
    .bd-modal .mh-x { width: 30px; height: 30px; border-radius: 8px; border: 1px solid var(--border);
      background: #fff; color: var(--muted); cursor: pointer; }
    .bd-modal .mh-x:hover { background: var(--elevated); color: var(--ink); }
    .bd-modal .mb { flex: 1; min-height: 0; overflow-y: auto; padding: 6px 0; }
    /* the moved context panels lose their #context-row sizing inside the modal;
       give them full height + scroll within the modal body. */
    .bd-modal .mb .panel { border: none; box-shadow: none; border-radius: 0; height: auto; }
    .bd-modal .mb .panel .panel-header { display: none; }
    .bd-modal .mb .panel .panel-body { max-height: none; overflow: visible; }

    /* ============================================================
       MOBILE LAYER — phones only (<= 640px). Purely additive: every
       rule here is inside this media query, so nothing at >= 641px
       (laptop / desktop) is affected. The desktop design assumes a
       tall viewport with a fixed left rail and 100vh, overflow-hidden
       pages that own their own internal scroll — which doesn't work on
       a short phone screen. On mobile we (a) move the rail to a bottom
       tab bar, (b) let the document scroll naturally instead of locking
       each page to 100vh, and (c) fit the fixed-width pieces (composer,
       cards, modal) to the narrow width.
       ============================================================ */
    @media (max-width: 640px) {
      /* let the page scroll naturally on a phone */
      html, body { height: auto; }
      body.has-shell { overflow: auto; -webkit-text-size-adjust: 100%; }

      /* rail -> bottom tab bar */
      .rail { top: auto; right: 0; bottom: 0; width: 100%; height: 56px;
        flex-direction: row; justify-content: space-around; align-items: center;
        gap: 0; padding: 0; background: #fff;
        border-top: 1px solid var(--border); box-shadow: 0 -2px 12px rgba(31,55,124,.06); }
      .rail .ri { width: 46px; height: 40px; border-radius: 10px; }
      /* the VMA logo is desktop rail chrome — hide it in the mobile tab bar so
         the three nav icons stay evenly spaced (its desktop margins would
         otherwise break the horizontal layout). */
      .rail .rail-logo { display: none; }
      /* tooltips would sit off-screen on a bottom bar — hide them on mobile */
      .rail [data-tip]:hover::after { display: none; }

      /* unlock the viewport: pages flow in normal document scroll, with
         room at the bottom for the tab bar */
      .stage { padding: 0 14px 72px; height: auto; overflow: visible; }
      .page { height: auto; overflow: visible; }
      .page.active { display: block; }

      /* page 1 — leads/signals: stack the two panels, but each keeps its
         OWN internal scroll (bounded height) rather than growing the whole
         document down — same in-section scroll feel as desktop. */
      #leads .container { height: auto; overflow: visible; padding-bottom: 0; }
      #leads .row { display: flex; flex-direction: column; gap: 14px; }
      #leads .row .panel { min-height: 0; height: 70vh; }
      #leads .row .panel-body { flex: 1; min-height: 0; overflow-y: auto;
        max-height: none; -webkit-overflow-scrolling: touch; }
      .wm-head { padding: 26px 0 18px; }
      .brand-title { font-size: 22px; }
      #leads .refresh-bar { flex-wrap: wrap; gap: 10px; }

      /* page 2 — assistant: natural scroll, full-width composer */
      #agent .agent-wrap { height: auto; max-width: 100%; padding: 24px 4px 8px;
        display: block; }
      #agent .ea-hero { margin-bottom: 20px; }
      .cc-bigicon { width: 60px; height: 60px; border-radius: 15px; margin-bottom: 16px; }
      .cc-bigicon svg { width: 30px; height: 30px; }
      .gemini-title { font-size: 27px; }
      .composer { width: 100%; }
      .composer .inner { margin: 12px; }
      #agent .chips { max-width: 100%; }
      /* iOS auto-zooms when a focused field is < 16px. Tapping a pack focuses
         the first form input, so force every composer field to 16px on mobile
         to stop the zoom-in. (Desktop keeps its 13.5px — outside this query.) */
      .composer .cform input, .composer .cform select, .composer .cform textarea,
      .cf-input, .cinput { font-size: 16px; }

      /* page 3 — BD calendar: natural scroll, full-width cards + modal */
      #cal .cc-wrap { height: auto; overflow: visible; padding: 8px 0 0; }
      .cc-hero { margin-bottom: 22px; }
      .cc-card { padding: 16px; gap: 12px; }
      .cc-card .cd { font-size: 12px; }
      .bd-modal-backdrop { padding: 12px; align-items: flex-end; }
      .bd-modal { max-width: 100%; max-height: 88vh; border-radius: 16px; }
    }
  </style>
</head>
<body class="has-shell">

<!-- LEFT RAIL — page switcher. Active state toggled by render() (additive JS). -->
<aside class="rail">
  <button class="ri active" id="nav-leads" data-tip="Market Opportunities Radar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" opacity=".4"/><path d="M12 12 L12 3 A9 9 0 0 1 19.8 7.5 Z" fill="currentColor" stroke="none" opacity=".55"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/></svg></button>
  <button class="ri" id="nav-agent" data-tip="Personal Assistant"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="7.5" width="17" height="13" rx="5"/><path d="M12 7.5V4.6"/><circle cx="12" cy="3.4" r="1.2"/><circle cx="9" cy="14" r="1.65" fill="currentColor" stroke="none"/><circle cx="15" cy="14" r="1.65" fill="currentColor" stroke="none"/></svg></button>
  <button class="ri" id="nav-cal" data-tip="BD Calendar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="4.5" width="18" height="16.5" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/></svg></button>
  <button class="ri ri-bottom" id="nav-reports" data-tip="Recent Reports"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button>
  <span class="rail-logo" data-tip="VMA Group"></span>
</aside>

<div class="stage">

  <!-- ===== PAGE 1 · MARKET INTELLIGENCE RADAR (leads + pre-market) ===== -->
  <section class="page active" id="leads">
    <div class="wm-head">
      <div class="brand"><span class="brand-title">Market Opportunities Radar</span><span class="radar-ic" title="Live — scanning the market for opportunities"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" opacity=".35"/><path d="M12 12 L12 3 A9 9 0 0 1 19.8 7.5 Z" fill="currentColor" stroke="none" opacity=".5"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/></svg></span></div>
    </div>

    <div class="container">

    {% if not has_token %}
    <div class="warn-banner">
      <strong>GITHUB_TOKEN not set</strong> in your .env. The "Run and Send" buttons won't work until you add one.
      See <code>DASHBOARD_SETUP.md</code> for instructions (it's a 5-minute one-time setup).
    </div>
    {% endif %}

  <!-- LEADS + PREDICTORS -->
  <div class="row">

    <!-- TODAY'S LEADS -->
    <div class="panel">
      <div class="panel-header">
        <h2>Live Jobs</h2>
        <span class="count" id="leads-count">{{ leads_active_count }}</span>
      </div>
      <div class="filter-bar" id="leads-filter-bar">
        <button class="lead-filter-pill active" data-filter="active">Active <span class="pill-count" id="lead-pc-active">{{ leads_active_count }}</span></button>
        <button class="lead-filter-pill" data-filter="new">New today <span class="pill-count" id="lead-pc-new">{{ leads_new_count }}</span></button>
        <button class="lead-filter-pill" data-filter="followed_up">Followed up <span class="pill-count" id="lead-pc-followed_up">{{ leads_followed_count }}</span></button>
        <button class="lead-filter-pill" data-filter="dismissed">Dismissed <span class="pill-count" id="lead-pc-dismissed">{{ leads_dismissed_count }}</span></button>
        <button class="lead-filter-pill" data-filter="all">All</button>
      </div>
      <div class="panel-body">
        {% if leads %}
          <div id="leads-list">
          {% for s in leads %}
            <div class="item lead" data-lead-id="{{ s.lead_id }}" data-status="{{ s.status }}" data-new="{{ '1' if s.is_new else '0' }}">
              <span class="rank">{{ loop.index }}</span>
              <span class="title">
                {% if s.url %}<a href="{{ s.url | safe_url }}" target="_blank">{{ s.title }}</a>
                {% else %}{{ s.title }}{% endif %}
              </span>
              {% if s.status == 'followed_up' %}<span class="status-badge followed-up">✓ followed up</span>{% endif %}
              {% if s.status == 'dismissed' %}<span class="status-badge dismissed">dismissed</span>{% endif %}
              <div class="meta">
                <span class="badge">{{ s.company or '—' }}</span>
                <span class="badge">{{ s.source }}</span>
                <span class="badge">{{ s.geo }}</span>
              </div>
              <pre class="outreach-text">{{ s.outreach }}</pre>
              <div class="item-actions">
                <button class="btn-mini copy-outreach" type="button">✉ Copy outreach</button>
                {% if s.status == 'active' %}
                  <button class="btn-mini lead-status-action" data-status="followed_up" type="button">✓ Mark followed up</button>
                  <button class="btn-mini lead-status-action ghost" data-status="dismissed" type="button">✕ Dismiss</button>
                {% else %}
                  <button class="btn-mini lead-status-action" data-status="active" type="button">↺ Restore</button>
                {% endif %}
              </div>
            </div>
          {% endfor %}
          </div>
          <div class="empty" id="leads-empty" style="display:none">Nothing here — switch filter above.</div>
        {% else %}
          <div class="empty">No leads loaded yet. Click Daily Refresh.</div>
        {% endif %}
      </div>
    </div>

    <!-- PREDICTOR PIPELINE (rolling 90-day forward window, auto-populated) -->
    <div class="panel">
      <div class="panel-header">
        <h2>BD Leads</h2>
        <span class="count" id="pred-count">{{ active_count }}</span>
      </div>
      <div class="filter-bar">
        <button class="filter-pill active" data-filter="active">Active <span class="pill-count" id="pred-pc-active">{{ active_count }}</span></button>
        <button class="filter-pill" data-filter="new">New today <span class="pill-count" id="pred-pc-new">{{ new_count }}</span></button>
        <button class="filter-pill" data-filter="followed_up">Followed up <span class="pill-count" id="pred-pc-followed_up">{{ followed_up_count }}</span></button>
        <button class="filter-pill" data-filter="dismissed">Dismissed <span class="pill-count" id="pred-pc-dismissed">{{ dismissed_count }}</span></button>
        <button class="filter-pill" data-filter="all">All</button>
      </div>
      <div class="panel-body compact" id="predictor-list">
        {% if premarket_rows %}
          {% for row in premarket_rows %}
          {% if row['_kind'] == 'funding' %}{% set f = row %}
            <div class="item predictor funding-row" data-fid="{{ f.fid }}" data-status="{{ f.status }}" data-new="0">
              <div class="row-summary">
                <span class="rank">{{ loop.index }}</span>
                <span class="title">{{ f.company }}</span>
                <span class="chips">
                  <span class="role-chip funding-chip-inline">Funding</span>
                  <span class="role-chip">{{ f.amount }} {{ f.round }}</span>
                  {% if f.strength %}<span class="strength-chip s-{{ f.strength }}" title="Opportunity strength — relative priority across the current Pre-Market panel. For a funding round: round size, GBP-weighting and the ~6-month senior-comms hire window.">{{ f.strength|capitalize }}</span>{% endif %}
                  {% if f.status == 'followed_up' %}<span class="status-badge followed-up">&#10003; followed up</span>{% endif %}
                  {% if f.status == 'dismissed' %}<span class="status-badge dismissed">dismissed</span>{% endif %}
                </span>
              </div>
              <div class="row-preview">
                <span class="signal-sub">{{ f.window }}{% if f.investor %} · led by {{ f.investor }}{% endif %}</span>
              </div>
              <div class="row-details">
                {% if f.evidence %}
                <div class="meta">
                  <div class="evidence">
                    <strong>Funding round:</strong> {{ f.evidence[:200] }}
                    {% if f.url %} · <a href="{{ f.url | safe_url }}" target="_blank">source</a>{% endif %}
                  </div>
                </div>
                {% endif %}
                <div class="item-actions">
                  {% if f.status == 'active' %}
                    <button class="btn-mini funding-action" data-status="followed_up" type="button">&#10003; Mark followed up</button>
                    <button class="btn-mini funding-action ghost" data-status="dismissed" type="button">&#10005; Dismiss</button>
                  {% else %}
                    <button class="btn-mini funding-action" data-status="active" type="button">&#8634; Restore</button>
                  {% endif %}
                </div>
              </div>
            </div>
          {% else %}{% set p = row %}
            <div class="item predictor" data-pid="{{ p.pid }}" data-status="{{ p.status }}" data-new="{{ '1' if p.is_new else '0' }}">
              <div class="row-summary">
                <span class="rank">{{ loop.index }}</span>
                <span class="title">{{ p.company }}</span>
                <span class="chips">
                  {% if p.predicted_role %}<span class="role-chip">{{ p.predicted_role }}</span>{% endif %}
                  {% if p.strength %}<span class="strength-chip s-{{ p.strength }}" title="Opportunity strength — relative priority across the current Pre-Market panel: how strong the signal is that a senior-comms hire is soon to be needed (trigger weight, stacking, recency, UK weighting × how soon the predicted hiring window opens). High = your strongest current opportunities.">{{ p.strength|capitalize }}</span>{% endif %}
                  {% if p.window_label %}<span class="window-badge">{{ p.window_label }}</span>{% endif %}
                  {% if p.status == 'followed_up' %}<span class="status-badge followed-up">✓ followed up</span>{% endif %}
                  {% if p.status == 'dismissed' %}<span class="status-badge dismissed">dismissed</span>{% endif %}
                </span>
              </div>
              <div class="row-preview">
                {% if p.events %}<span class="signal-sub">{{ p.events[0].trigger_label }}</span>{% endif %}
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
                  {% if p.status == 'active' %}
                    <button class="btn-mini status-action" data-status="followed_up" type="button">✓ Mark followed up</button>
                    <button class="btn-mini status-action ghost" data-status="dismissed" type="button">✕ Dismiss</button>
                  {% else %}
                    <button class="btn-mini status-action" data-status="active" type="button">↺ Restore</button>
                  {% endif %}
                </div>
              </div>
            </div>
          {% endif %}
          {% endfor %}
        {% else %}
          <div class="empty compact">No predictors loaded yet. Click Daily Refresh.</div>
        {% endif %}
      </div>
    </div>

  </div>

  <!-- DETERMINISTIC, DATE-DRIVEN PLACEMENT WINDOWS — Calendar Pulses
       year ribbon. Funding-Round signals are folded into Predicted
       Briefs above; rare detectors live in Specialist Signals below. -->

  <!-- SPECIALIST SIGNALS — Water SAR / Contract-End / Mandates Worth
       Stealing, collapsed into one panel that is HIDDEN unless a
       sub-detector actually has rows. Each sub-section also hides itself
       when empty. -->
  <div class="row row-full" id="specialist-row" style="display:none">
    <div class="panel">
      <div class="panel-header">
        <h2>Specialist Signals</h2>
        <span class="count" id="specialist-count">—</span>
      </div>
      <div class="panel-body" id="specialist-body">

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

  <!-- SPECIALIST SIGNALS row stays hidden on Page 1 above. Footer + dev
       controls sit at the bottom of Page 1. -->
  <div class="footer">
    <span class="dev-zone">
      <span class="dev-zone-label">For dev only - not a user feature:</span>
      <button type="button" onclick="refreshBrief()" id="refresh-btn" class="dev-btn"
              title="Load today's latest brief (the data normally auto-loads; this forces a reload now). Last refreshed: {{ last_updated }}">
        <span class="rbtn-label">Daily Refresh</span>
      </button>
      <button type="button" id="dev-run-brief" class="dev-btn"
              onclick="devTriggerBrief()"
              title="Maintenance: triggers a fresh morning-brief workflow run. Not for day-to-day use — Daily Refresh just loads the last completed run.">
        trigger fresh data
      </button>
      <span class="dev-status" id="dev-run-status"></span>
    </span>
  </div>

    </div><!-- /#leads .container -->
  </section><!-- /#leads -->

  <!-- ===== PAGE 2 · EXECUTIVE ASSISTANT ===== -->
  <section class="page" id="agent">
    <div class="agent-wrap">
      <div class="ea-hero">
        <div class="cc-bigicon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="7.5" width="17" height="13" rx="5"/><path d="M12 7.5V4.6"/><circle cx="12" cy="3.4" r="1.2"/><circle cx="9" cy="14" r="1.65" fill="currentColor" stroke="none"/><circle cx="15" cy="14" r="1.65" fill="currentColor" stroke="none"/></svg></div>
        <h1 class="gemini-title">Personal Assistant</h1>
        <div class="cc-sub">A simple prompt to build key reports in real-time, with the latest data.</div>
      </div>

      <!-- COMPOSER PILL. The .cform morph area holds either the default free-text
           cinput (no chip selected) or one of the four MOVED action-forms. Each
           form keeps its id / field names / onsubmit dispatch(); the original
           full-width Run button is hidden (CSS) and a corner arrow .send submits
           the form via a native click so dispatch()'s window.open isn't blocked. -->
      <div class="composer" data-mode="free">
        <div class="inner">
          <div class="cform" data-cform>

            <!-- DEFAULT free-text prompt (shown when no chip is active) -->
            <input class="cinput" id="cprompt" placeholder="Tell me what to make…">

            <!-- PITCH PACK -->
            <div class="cap-form" data-cap="pitch">
              <div class="cf-head cf-formhead"><span class="cf-dot"></span>Pitch Pack</div>
              <div class="cf-desc">Generate a tailored proposal to upgrade a client's job vacancy into an exclusive, retained search.</div>
              <form id="pitch-form" onsubmit="dispatch(event, 'pitch-form', '/api/dispatch/pitch-pack')">
                <label for="pp-account">Account name</label>
                <input id="pp-account" name="account_name" placeholder="e.g. Unilever" required>
                <label for="pp-role">Role</label>
                <input id="pp-role" name="role" placeholder="e.g. {{ example_role }}" required>
                <button type="submit">Run</button>
                <button type="submit" class="send" title="Run"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
                <div class="status" id="pitch-status"></div>
              </form>
            </div>

            <!-- REVERSE MATCH -->
            <div class="cap-form" data-cap="reverse">
              <div class="cf-head cf-formhead"><span class="cf-dot"></span>Reverse Match</div>
              <div class="cf-desc">Take a candidate, search the market fresh, and give a ranked list of accounts to match them to.</div>
              <form id="rm-form" onsubmit="dispatch(event, 'rm-form', '/api/dispatch/reverse-match')">
                <label for="rm-name">Candidate name</label>
                <input id="rm-name" name="candidate_name" placeholder="e.g. Rebecca Torres" required>
                <label for="rm-company">Current company</label>
                <input id="rm-company" name="current_company" placeholder="e.g. Vodafone" required>
                <label for="rm-title">Current title</label>
                <input id="rm-title" name="current_title" placeholder="e.g. {{ example_role }}" required>
                <button type="submit">Run</button>
                <button type="submit" class="send" title="Run"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
                <div class="status" id="rm-status"></div>
              </form>
            </div>

            <!-- PRE-MEETING BRIEF -->
            <div class="cap-form" data-cap="premeeting">
              <div class="cf-head cf-formhead"><span class="cf-dot"></span>Pre-meeting Brief</div>
              <div class="cf-desc">Walk into any client meeting with up-to-date prep.</div>
              <form id="pm-form" onsubmit="dispatch(event, 'pm-form', '/api/dispatch/pre-meeting')">
                <label for="pm-account">Account name</label>
                <input id="pm-account" name="account_name" placeholder="e.g. Severn Trent" required>
                <label for="pm-contact">Contact (optional)</label>
                <input id="pm-contact" name="contact_name" placeholder="e.g. Carla Sherry">
                <label for="pm-context">Meeting context (optional)</label>
                <input id="pm-context" name="meeting_context" placeholder="e.g. 10am Mon, Zoom">
                <button type="submit">Run</button>
                <button type="submit" class="send" title="Run"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
                <div class="status" id="pm-status"></div>
              </form>
            </div>

            <!-- MANUAL SWEEP -->
            <div class="cap-form" data-cap="sweep">
              <div class="cf-head cf-formhead"><span class="cf-dot"></span>Manual Sweep</div>
              <div class="cf-desc">Sweep for potential missed leads or pre-market signals.</div>
              <form id="sweep-form" onsubmit="dispatch(event, 'sweep-form', '/api/dispatch/sweep')">
                <label for="sw-days">Window (days)</label>
                <input id="sw-days" name="window_days" type="number" min="1" max="60" placeholder="e.g. 14" required>
                <button type="submit">Run</button>
                <button type="submit" class="send" title="Run"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
                <div class="status" id="sweep-status"></div>
              </form>
            </div>

          </div>
          <div class="cfoot"><button class="send" id="composer-send" type="button" title="Run"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button></div>
        </div>
      </div>

      <!-- chip selector — clicking morphs the composer into that form -->
      <div class="chips">
        <button class="chip" data-cap="pitch"><span class="i">✦</span>Pitch Pack</button>
        <button class="chip" data-cap="reverse"><span class="i">↗</span>Reverse Match</button>
        <button class="chip" data-cap="premeeting"><span class="i">◷</span>Pre-meeting</button>
        <button class="chip" data-cap="sweep"><span class="i">⟲</span>Sweep</button>
      </div>


    </div>
  </section>

  <!-- ===== PAGE 3 · BD CALENDAR ===== -->
  <section class="page" id="cal">
    <div class="cc-wrap">
      <div class="cc-hero">
        <div class="cc-bigicon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="4.5" width="18" height="16.5" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/></svg></div>
        <h1 class="gemini-title">BD Calendar</h1>
        <div class="cc-sub">The business-development moves that run on key dates.</div>
      </div>
      <div class="cc-cards">
        <button class="cc-card" data-open="windows"><span class="ci">◴</span><span class="cx"><span class="ct">Placement Windows</span><span class="cd">Statutory hiring windows that open on a known calendar.</span></span><span class="cbadge"></span><span class="cv">›</span></button>
        <button class="cc-card" data-open="events"><span class="ci">▦</span><span class="cx"><span class="ct">Events &amp; Networking</span><span class="cd">Awards, summits and networking dates worth showing up to.</span></span><span class="cbadge"></span><span class="cv">›</span></button>
        <button class="cc-card" data-open="frameworks"><span class="ci">❏</span><span class="cx"><span class="ct">Framework Eligibility</span><span class="cd">Public-sector frameworks where VMA can bid.</span></span><span class="cbadge"></span><span class="cv">›</span></button>
      </div>
    </div>

    <!-- HIDDEN HOST — the three context panels live here so their on-load
         AJAX loaders (loadPulses / loadEvents) and the server-side framework
         loop resolve into still-in-DOM mounts. Clicking a card moves the
         matching panel into the modal body; closing moves it back here. -->
    <div class="cal-host" id="cal-host">

      <div class="panel ctx-col" id="pulses-row" data-bd="windows">
        <div class="panel-header">
          <h2>Placement Windows</h2>
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

      <div class="panel ctx-col" data-bd="events">
        <div class="panel-header">
          <h2>Events &amp; Networking</h2>
          <span class="count" id="events-count">—</span>
        </div>
        <div class="panel-body" id="events-body">
          <div class="empty compact">Loading…</div>
        </div>
      </div>

      <div class="panel ctx-col" id="framework-row" data-bd="frameworks">
        <div class="panel-header">
          <h2>Framework Eligibility</h2>
          <span class="count" id="framework-count">{{ framework_events|length }}</span>
        </div>
        <div class="filter-bar" id="fw-filter-bar">
          <button class="lead-filter-pill active" data-filter="active">Active <span class="pill-count" id="fw-pc-active">{{ fw_active_count }}</span></button>
          <button class="lead-filter-pill" data-filter="followed_up">Followed up <span class="pill-count" id="fw-pc-followed_up">{{ fw_followed_count }}</span></button>
          <button class="lead-filter-pill" data-filter="dismissed">Dismissed <span class="pill-count" id="fw-pc-dismissed">{{ fw_dismissed_count }}</span></button>
          <button class="lead-filter-pill" data-filter="all">All</button>
        </div>
        <div class="panel-body" id="framework-body">
          <div class="fw-note">Where VMA can bid — public-sector framework windows. Eligibility &amp; BD groundwork, not a live lead list.</div>
          {% if framework_events|length == 0 %}
            <div class="empty compact">No framework windows tracked.</div>
          {% endif %}
          {% for fw in framework_events %}
            <div class="row2 framework-row" data-status="{{ fw.triage }}" data-new="0" data-fwid="{{ fw.key }}">
              <div class="row2-head">
                <span class="typ fw">FW</span>
                <span class="row2-title">{{ fw.ad_title or fw.title }}{% if fw.discovered %} <span class="found-pill" title="Auto-discovered from a live public source">Found</span>{% endif %}</span>
                <span class="row2-tags">
                  <span class="ipill {{ 'w' if fw.status == 'refresh_window' else 'mut' }}">{{ fw.window_pill }}</span>
                  {% if fw.triage == 'followed_up' %}<span class="status-badge followed-up">&#10003;</span>{% elif fw.triage == 'dismissed' %}<span class="status-badge dismissed">dismissed</span>{% endif %}
                </span>
                <span class="row2-chev">&rsaquo;</span>
              </div>
              <div class="row2-detail">
                <div class="row2-sub">{{ fw.ad_desc or fw.scope }}</div>
                <div class="play">
                  <div class="play-lab">&#9654; {{ fw.window_label }}</div>
                  <div class="play-desc" title="{{ fw.notes }}">{{ fw.title }} · <a class="lnk" href="{{ fw.portal | safe_url }}" target="_blank" rel="noopener noreferrer">verify on portal &rsaquo;</a></div>
                  <div class="item-actions">
                    {% if fw.triage == 'active' %}
                      <button class="btn-mini framework-action" data-status="followed_up" type="button">&#10003; Mark followed up</button>
                      <button class="btn-mini framework-action ghost" data-status="dismissed" type="button">&#10005; Dismiss</button>
                    {% else %}
                      <button class="btn-mini framework-action" data-status="active" type="button">&#8634; Restore</button>
                    {% endif %}
                  </div>
                </div>
              </div>
            </div>
          {% endfor %}
        </div>
      </div>

    </div>
  </section>

  <!-- ===== PAGE 4 · RECENT REPORTS (opened by the sidebar download icon) ===== -->
  <section class="page" id="reports">
    <div class="reports-wrap">
      <div class="ea-hero">
        <div class="cc-bigicon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></div>
        <h1 class="gemini-title">Recent Reports</h1>
        <div class="cc-sub">Reports you've generated in the last 48 hours — download any of them here.</div>
      </div>
      <div class="action-card recent-card" id="recent-card">
        <button class="rr-clear" onclick="clearRecentReports(this)">Clear</button>
        <h3>Recent Reports</h3>
        <div id="recent-reports"></div>
        <div class="rr-empty" id="rr-empty">No reports yet — generate one from the Personal Assistant and it'll appear here to download.</div>
      </div>
    </div>
  </section>

</div><!-- /.stage -->

<!-- ONE shared modal for the BD-Calendar cards. open/close + node relocation
     handled by additive JS at the end of <script>. -->
<div class="bd-modal-backdrop" id="bd-modal">
  <div class="bd-modal">
    <div class="mh"><span class="mh-ic" id="bd-mic"></span><span class="mh-t" id="bd-mt"></span><button class="mh-x" id="bd-mx">✕</button></div>
    <div class="mb" id="bd-mb"></div>
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
  data.mode = 'send';

  // Open the result tab NOW, inside the click gesture, so the browser
  // doesn't block it as a pop-up when it loads minutes later.
  const win = window.open('', '_blank');
  if (win) {
    win.document.write(
      '<!doctype html><meta charset="utf-8"><title>Preparing report…</title>' +
      '<style>body{font-family:Inter,system-ui,sans-serif;background:#f7f9fc;' +
      'color:#1F1F1F;display:flex;min-height:100vh;align-items:center;' +
      'justify-content:center;margin:0}.b{text-align:center;padding:24px}' +
      '.s{width:30px;height:30px;border:3px solid rgba(66,133,244,.20);' +
      'border-top-color:#4285F4;border-radius:50%;margin:0 auto 16px;' +
      'animation:r .8s linear infinite}@keyframes r{to{transform:rotate(360deg)}}' +
      'p{font-size:13px;color:#5F6368;max-width:340px;line-height:1.55}</style>' +
      '<div class="b"><div class="s"></div><h3>Preparing your report…</h3>' +
      '<p>This can take a few minutes. Keep this tab open — it loads ' +
      'automatically when ready.</p></div>');
  }

  btn.disabled = true;
  btn.textContent = 'Running…';
  status.className = 'status';
  status.style.display = '';
  status.textContent = 'Dispatching…';

  // Render's free tier spins down on idle and the first request after
  // that can hang past the proxy timeout (Safari surfaces it as
  // 'Load failed'). Retry up to 3 times with 2s backoff — the server
  // is warm by the second attempt.
  let j;
  let attempt = 0;
  let lastErr = null;
  while (attempt < 3) {
    attempt++;
    if (attempt > 1) {
      status.textContent = 'Dispatching… retrying (' + attempt + '/3)';
      await new Promise(res => setTimeout(res, 2000));
    }
    try {
      const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      j = await r.json();
      lastErr = null;
      break;
    } catch (e) {
      lastErr = e;
    }
  }
  if (lastErr) {
    if (win && !win.closed) win.close();
    status.textContent = 'Network error after 3 attempts: ' + lastErr.message
      + ' (the server may have been spinning up — please try once more)';
    status.className = 'status err';
    btn.disabled = false; btn.textContent = 'Run';
    return;
  }
  if (!j.ok || !j.artifact || !j.dispatched_at) {
    if (win && !win.closed) win.close();
    status.textContent = j.detail || 'Failed.';
    status.className = 'status err';
    btn.disabled = false; btn.textContent = 'Run';
    return;
  }

  status.textContent = 'Running… the report opens here when ready.';
  status.className = 'status ok';
  loadRecentReports();   // surface the new "generating…" row at once

  const qs = 'artifact=' + encodeURIComponent(j.artifact) +
             '&since=' + encodeURIComponent(j.dispatched_at);
  const started = Date.now();
  const MAX_MS = 25 * 60 * 1000;   // give up after ~25 min

  const poll = async () => {
    if (Date.now() - started > MAX_MS) {
      if (win && !win.closed) win.close();
      status.textContent = 'Still running after 25 min — the emailed copy will still arrive.';
      status.className = 'status err';
      btn.disabled = false; btn.textContent = 'Run';
      return;
    }
    let s;
    try {
      const rr = await fetch('/api/output/status?' + qs);
      s = await rr.json();
    } catch (e) { s = { ready: false }; }
    if (s.ready && s.id) {
      const viewUrl = '/api/output/view?artifact=' +
        encodeURIComponent(j.artifact) + '&id=' + encodeURIComponent(s.id);
      if (win && !win.closed) {
        win.location = viewUrl;
        status.innerHTML = 'Report opened in a new tab · ' +
          '<a href="' + viewUrl + '" target="_blank">open again ↗</a>';
      } else {
        status.innerHTML = 'Report ready · ' +
          '<a href="' + viewUrl + '" target="_blank">open ↗</a>';
      }
      status.className = 'status ok';
      btn.disabled = false; btn.textContent = 'Run';
      loadRecentReports();   // flip the row from "generating…" to View/Download
      return;
    }
    const mins = Math.floor((Date.now() - started) / 60000);
    status.textContent = 'Running…' + (mins ? ' (' + mins + ' min)' : '');
    setTimeout(poll, 12000);
  };
  setTimeout(poll, 12000);
}

// Pipeline filter pills: show only items matching the chosen filter.
function applyFilter(name) {
  document.querySelectorAll('.filter-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.filter === name);
  });
  let vis = 0;
  document.querySelectorAll('#predictor-list .item.predictor').forEach(item => {
    const status = item.dataset.status || 'active';
    const isNew = item.dataset.new === '1';
    let show = false;
    if (name === 'all') show = true;
    else if (name === 'new') show = isNew && status === 'active';
    else if (name === 'active') show = status === 'active';
    else show = status === name;
    item.style.display = show ? '' : 'none';
    // Renumber the visible rows 1..N so the active view never reads 4,7,8,10…
    if (show) { vis += 1; const r = item.querySelector('.rank'); if (r) r.textContent = vis; }
  });
}
document.addEventListener('click', (event) => {
  const pill = event.target.closest('.filter-pill');
  if (!pill) return;
  applyFilter(pill.dataset.filter);
});

// Recompute predictor counts from the DOM (source of truth) and update
// the header + pill badges, then re-apply the current filter so a
// followed-up/dismissed item drops out of the active view.
function recountPredictors() {
  let a = 0, n = 0, f = 0, d = 0;
  // Counts roll up predictors + funding + framework rows (all .item.predictor).
  document.querySelectorAll('#predictor-list .item.predictor').forEach(it => {
    const s = it.dataset.status || 'active';
    if (s === 'active') { a++; if (it.dataset.new === '1') n++; }
    else if (s === 'followed_up') f++;
    else if (s === 'dismissed') d++;
  });
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
  set('pred-count', a); set('pred-pc-active', a); set('pred-pc-new', n);
  set('pred-pc-followed_up', f); set('pred-pc-dismissed', d);
  const ap = document.querySelector('.filter-pill.active');
  applyFilter(ap ? ap.dataset.filter : 'active');
}

// Leads filter — same model as predictors, scoped to the leads list so
// the two panels' pills never cross-fire.
function applyLeadFilter(name) {
  // Scoped to the leads filter bar only — Hire Watch reuses the
  // .lead-filter-pill class, so an unscoped selector would cross-fire
  // and switch both panels' tabs together.
  document.querySelectorAll('#leads-filter-bar .lead-filter-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.filter === name);
  });
  let visible = 0;
  document.querySelectorAll('#leads-list .item.lead').forEach(item => {
    const status = item.dataset.status || 'active';
    const isNew = item.dataset.new === '1';
    const show = (name === 'all') ? true
               : (name === 'new') ? (isNew && status === 'active')
               : (name === 'active') ? status === 'active'
               : status === name;
    item.style.display = show ? '' : 'none';
    // Renumber the visible rows 1..N so the active view never reads 2,3,6,7…
    if (show) { visible++; const r = item.querySelector('.rank'); if (r) r.textContent = visible; }
  });
  const empty = document.getElementById('leads-empty');
  if (empty) empty.style.display = visible ? 'none' : '';
}
document.addEventListener('click', (event) => {
  // Only react to pills inside the leads bar — Hire Watch's pills share
  // the class but are handled by their own scoped listener.
  const pill = event.target.closest('#leads-filter-bar .lead-filter-pill');
  if (!pill) return;
  applyLeadFilter(pill.dataset.filter);
});

function recountLeads() {
  let a = 0, n = 0, f = 0, d = 0;
  document.querySelectorAll('#leads-list .item.lead').forEach(it => {
    const s = it.dataset.status || 'active';
    if (s === 'active') { a++; if (it.dataset.new === '1') n++; }
    else if (s === 'followed_up') f++;
    else if (s === 'dismissed') d++;
  });
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
  set('leads-count', a); set('lead-pc-active', a); set('lead-pc-new', n);
  set('lead-pc-followed_up', f); set('lead-pc-dismissed', d);
  const ap = document.querySelector('#leads-filter-bar .lead-filter-pill.active');
  applyLeadFilter(ap ? ap.dataset.filter : 'active');
}

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

// Expand / collapse unified Hire Watch / Framework rows on head click.
// Clicks on buttons or links inside the head don't toggle.
document.addEventListener('click', (event) => {
  const head = event.target.closest('.row2-head');
  if (!head) return;
  if (event.target.closest('button, a')) return;
  const row = head.closest('.row2');
  if (row) row.classList.toggle('open');
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
    // Update counts + drop the row out of the active view
    recountPredictors();
  } catch (e) {
    alert('Update failed: ' + e.message);
    btn.disabled = false;
    btn.textContent = orig;
  }
});

// Lead triage: mark followed up / dismiss / restore (mirrors predictors,
// persisted by lead id via /api/lead/<id>/status).
document.addEventListener('click', async (event) => {
  const btn = event.target.closest('.lead-status-action');
  if (!btn) return;
  const item = btn.closest('.item.lead');
  if (!item) return;
  const id = item.dataset.leadId;
  const newStatus = btn.dataset.status;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '…';
  try {
    const r = await fetch(`/api/lead/${encodeURIComponent(id)}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    });
    const j = await r.json();
    if (!j.ok) { alert(j.detail || 'Update failed'); btn.disabled = false; btn.textContent = orig; return; }
    item.dataset.status = newStatus;
    const actions = item.querySelector('.item-actions');
    if (actions) {
      actions.querySelectorAll('.lead-status-action').forEach(b => b.remove());
      if (newStatus === 'active') {
        actions.insertAdjacentHTML('beforeend',
          '<button class="btn-mini lead-status-action" data-status="followed_up" type="button">✓ Mark followed up</button>' +
          '<button class="btn-mini lead-status-action ghost" data-status="dismissed" type="button">✕ Dismiss</button>');
      } else {
        actions.insertAdjacentHTML('beforeend',
          '<button class="btn-mini lead-status-action" data-status="active" type="button">↺ Restore</button>');
      }
    }
    item.querySelectorAll('.status-badge').forEach(b => b.remove());
    if (newStatus === 'followed_up') {
      item.querySelector('.title').insertAdjacentHTML('afterend',
        '<span class="status-badge followed-up">✓ followed up</span>');
    } else if (newStatus === 'dismissed') {
      item.querySelector('.title').insertAdjacentHTML('afterend',
        '<span class="status-badge dismissed">dismissed</span>');
    }
    // Update counts + drop the row out of the active view
    recountLeads();
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
//
// After the brief lands, also re-parses the fresh latest_signals.json
// for cascade moves so Hire Watch picks up any senior comms
// appointments in today's news without anyone having to click Re-scan.
async function refreshBrief() {
  const btn = document.getElementById('refresh-btn');
  if (!btn) return;
  // The button now holds a persistent SVG icon + a .rbtn-label span; only
  // ever read/write the label so the icon is never wiped.
  const lbl = btn.querySelector('.rbtn-label');
  const originalLabel = lbl.textContent;
  btn.disabled = true;
  lbl.textContent = 'Refreshing…';
  try {
    const r = await fetch('/api/refresh', { method: 'POST' });
    const j = await r.json();
    if (j.ok && (j.leads > 0 || j.predictors > 0)) {
      // Brief landed — re-parse signals for cascade moves before
      // reloading so the Hire Watch panel reflects today's data.
      // Non-blocking: cascade failure must not stop the reload.
      lbl.textContent = 'Scanning hires…';
      try {
        await fetch('/api/cascade/scour', { method: 'POST' });
      } catch (e) { /* non-fatal — log only */ }
      setTimeout(() => window.location.reload(), 400);
    } else {
      // Only surface a banner when something is actually wrong (refresh
      // failed, or it succeeded but found nothing — both carry a warning).
      showRefreshBanner(j);
      btn.disabled = false;
      lbl.textContent = originalLabel;
    }
  } catch (e) {
    showRefreshBanner({ok: false, detail: 'Refresh failed: ' + e.message});
    btn.disabled = false;
    lbl.textContent = originalLabel;
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
    // Placement Windows = statutory/regulatory + policy pulses ONLY.
    // Industry events were split out into the Events & Networking panel
    // (loadEvents); they no longer share this ribbon.
    const res = await fetch('/api/pulses');
    const j   = await res.json();
    const pulseRows = j.rows || [];
    const total = pulseRows.length;
    count.textContent = total;
    if (total === 0) {
      body.innerHTML = '<div class="empty compact">No placement window open today. ' +
        'Pulses surface only inside a statutory/regulator run-up (FCA Consumer Duty ' +
        'board-report ramp, UK SRS first-cycle build-up, post-Spending-Review ' +
        'machinery-of-government reshuffle) — by design they go quiet outside those ' +
        'dated windows rather than show stale noise.</div>';
      return;
    }
    // Each placement window is drawn as a glass window-pane tile beside its
    // target role + timing — "the concept of a window" made literal.
    const newCount = pulseRows.filter(p => p.just_opened).length;
    const out = ['<div class="win-list">'];
    pulseRows.forEach(p => {
      const high = p.confidence === 'high';
      const confLabel = high ? 'Regulatory deadline' : 'Policy timeline';
      const days = (typeof p.days_left === 'number') ? p.days_left + 'd left' : '';
      const rm = p.key
        ? '<button class="cal-rm" data-key="' + esc(p.key) + '" title="Remove this window">&#10005;</button>'
        : '';
      out.push(
        '<div class="win-row' + (p.just_opened ? ' is-new' : '') + '" data-key="' + esc(p.key || p.name || '') + '">' +
          '<div class="win-tile" title="Placement window"></div>' +
          '<div class="win-main">' +
            '<div class="win-name">' + esc(p.name || '') +
              (p.discovered ? ' <span class="found-pill" title="Auto-discovered from a live public source">Found</span>' : '') + '</div>' +
            (p.seat ? '<div class="win-seat">' + esc(p.seat) + '</div>' : '') +
            '<div class="win-tags">' +
              '<span class="conf-pill ' + (high ? 'high' : 'med') + '">' + esc(confLabel) + '</span>' +
              (days ? '<span class="win-days">' + esc(days) + '</span>' : '') +
            '</div>' +
            (p.scope_note ? '<div class="win-scope">' + esc(p.scope_note) +
              ((p.url || p.source) ? ' &middot; <a href="' + safeUrl(p.url || p.source) +
                 '" target="_blank" rel="noopener noreferrer" style="color:var(--blue-deep);text-decoration:none;">source</a>' : '') +
              '</div>' : '') +
            (p.advisory ? '<div class="win-scope">' + esc(p.advisory) + '</div>' : '') +
          '</div>' +
          rm +
        '</div>'
      );
    });
    out.push('</div>');
    body.innerHTML = out.join('');

    // Remove-a-window: delegated dismissal (shared pulse_dismiss keyspace),
    // then re-render so the count stays correct.
    body.querySelectorAll('.cal-rm').forEach(btn => {
      btn.addEventListener('click', async () => {
        const key = btn.getAttribute('data-key');
        if (!key) return;
        btn.disabled = true;
        try {
          const r = await fetch('/api/pulses/dismiss', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: key, dismissed: true }),
          });
          const j = await r.json();
          if (j.ok) {
            loadPulses();   // re-render with the window removed
          } else {
            btn.disabled = false;
            alert(j.detail || 'Could not remove.');
          }
        } catch (e) {
          btn.disabled = false;
          alert('Network error: ' + e.message);
        }
      });
    });

    const nb = document.getElementById('pulses-new');
    if (nb) {
      if (newCount > 0) {
        const n = document.getElementById('pulses-new-n');
        if (n) n.textContent = newCount;
        nb.style.display = 'inline-flex';
        nb.onclick = null;
      } else {
        nb.style.display = 'none';
      }
    }
  } catch (e) {
    body.innerHTML = '<div class="empty compact">Failed to load: ' + esc(e.message) + '</div>';
  }
}

// ---------- Events & Networking (industry events) ----------
// Relationship / candidate-visibility moments (awards, conferences,
// summits) — split out of the old BD Calendar. Rendered as a light
// chronological list rather than a placement-window ribbon, because an
// event drives networking, not a statute-forced hire.
async function loadEvents() {
  const body = document.getElementById('events-body');
  const count = document.getElementById('events-count');
  if (!body) return;
  try {
    const res = await fetch('/api/industry-events');
    const j = await res.json();
    const rows = j.rows || [];
    if (count) count.textContent = rows.length;
    if (rows.length === 0) {
      body.innerHTML = '<div class="empty compact">No comms awards, conferences or ' +
        'summits in the next ~6 months. This panel lists networking & candidate-' +
        'visibility moments (PRWeek / CIPR / PRCA awards, IoIC, EACD, European ' +
        'Excellence) — relationship groundwork, not statute-forced placement windows.</div>';
      return;
    }
    const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const fmtDate = iso => {
      const p = String(iso || '').split('-');
      if (p.length < 3) return iso || '';
      return parseInt(p[2], 10) + ' ' + (MON[parseInt(p[1], 10) - 1] || '') + ' ' + p[0];
    };
    const whenChip = d => {
      if (typeof d !== 'number') return '';
      if (d < 0) return 'past';
      if (d === 0) return 'today';
      if (d === 1) return 'tomorrow';
      if (d < 60) return 'in ' + d + 'd';
      return 'in ' + Math.round(d / 30) + 'mo';
    };
    const focusChip = f => {
      const lab = f === 'internal' ? 'Internal comms'
                : f === 'external' ? 'External comms' : 'Mixed';
      return '<span class="ev-focus ev-' + esc(f || 'mixed') + '">' + lab + '</span>';
    };
    const dayOf = iso => { const p = String(iso || '').split('-'); return p.length >= 3 ? parseInt(p[2], 10) : ''; };
    const monOf = iso => { const p = String(iso || '').split('-'); return p.length >= 2 ? (MON[parseInt(p[1], 10) - 1] || '') : ''; };
    const out = ['<div class="ev-list">'];
    rows.forEach((e, i) => {
      const win = e.in_action_window
        ? '<span class="ev-open" title="Outreach window open now">window open</span>' : '';
      const rm = e.key
        ? '<button class="ev-rm" data-key="' + esc(e.key) + '" title="Remove this event">&#10005;</button>' : '';
      const focLab = e.focus === 'internal' ? 'Internal' : e.focus === 'external' ? 'External' : 'Mixed';
      const when = whenChip(e.days_to_event);
      const srcLink = e.url || e.source;
      const hasDetail = !!(e.why_now || srcLink);
      out.push(
        '<div class="ev-item' + (hasDetail ? ' has-detail' : '') + '" data-evkey="' + esc(e.key || e.name || '') + '">' +
          '<div class="ev-row">' +
            '<div class="ev-date"><b>' + esc(dayOf(e.event_date)) + '</b><span>' + esc(monOf(e.event_date)) + '</span></div>' +
            '<div class="ev-main">' +
              '<div class="ev-n">' + esc(e.name || '') + win +
                (e.discovered ? ' <span class="found-pill" title="Auto-discovered from a live public source">Found</span>' : '') + '</div>' +
              '<div class="ev-t">' +
                '<span class="ev-foc">' + focLab + '</span>' +
                (e.location ? '<span>' + esc(e.location) + '</span>' : '') +
                (when ? '<span>' + esc(when) + '</span>' : '') +
              '</div>' +
            '</div>' +
            (hasDetail ? '<span class="ev-chev">&rsaquo;</span>' : '') +
            rm +
          '</div>' +
          (hasDetail ?
            '<div class="ev-detail">' +
              (e.why_now ? '<div class="ev-why">' + esc(e.why_now) + '</div>' : '') +
              (srcLink ? '<div class="ev-why"><a href="' + safeUrl(srcLink) +
                 '" target="_blank" rel="noopener noreferrer" style="color:var(--blue-deep);text-decoration:none;">source &rsaquo;</a></div>' : '') +
            '</div>' : '') +
        '</div>'
      );
    });
    out.push('</div>');
    body.innerHTML = out.join('');
    // Click a row to expand its why-now + source link below it.
    body.querySelectorAll('.ev-item.has-detail .ev-row').forEach(row => {
      row.addEventListener('click', (ev) => {
        if (ev.target.closest('.ev-rm')) return;   // dismiss is handled separately
        row.parentElement.classList.toggle('expanded');
      });
    });
    // Remove-an-event (delegated): persist the dismissal (shared pulse_dismiss
    // keyspace) then re-render so the count + list stay correct.
    body.querySelectorAll('.ev-rm').forEach(btn => {
      btn.addEventListener('click', async () => {
        const key = btn.getAttribute('data-key');
        if (!key) return;
        btn.disabled = true;
        try {
          const r = await fetch('/api/pulses/dismiss', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: key, dismissed: true }),
          });
          const jj = await r.json();
          if (jj.ok) { loadEvents(); }
          else { btn.disabled = false; alert(jj.detail || 'Could not remove.'); }
        } catch (err) { btn.disabled = false; alert('Network error: ' + err.message); }
      });
    });
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

// ---------- Specialist Signals orchestrator ----------
// Loads the low-frequency sub-detectors via their unchanged /api
// endpoints; reveals only the sub-sections that have rows, and the
// whole panel only if at least one did. All empty -> panel stays
// hidden (the dashboard never shows an empty Specialist panel).
async function loadSpecialistSignals() {
  const row = document.getElementById('specialist-row');
  const cnt = document.getElementById('specialist-count');
  let total = 0;
  try {
    const counts = await Promise.all([
      loadWaterSar(), loadContractEnd(), loadMandates(),
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

// ---------- Candidate Watch ----------
async function loadWatchList() {
  const wrap = document.getElementById('watch-list');
  const status = document.getElementById('watch-list-status');
  if (!wrap || !status) return;   // Candidate Watch panel removed from the UI
  try {
    const r = await fetch('/api/candidates/watch');
    const j = await r.json();
    if (!j.rows || j.rows.length === 0) {
      status.textContent = 'No candidates yet — add one below.';
      wrap.innerHTML = '';
      return;
    }
    status.textContent = j.total + ' watched · sorted by overdue';
    const out = ['<div class="cw-list">'];
    for (const c of j.rows.slice(0, 10)) {
      const cadence = c.touch_cadence_days || 30;
      const seen = c._days_since_touched;          // null === not yet contacted

      // Overdue / due indicator — primary ordering signal.
      let duePill = '';
      if (c._overdue_days > 0) {
        duePill = '<span class="cw-pill overdue">overdue ' + esc(c._overdue_days) + 'd</span>';
      } else if (seen != null) {
        const left = Math.max(0, cadence - seen);
        duePill = '<span class="cw-pill due">due in ' + esc(left) + 'd</span>';
      } else {
        duePill = '<span class="cw-pill overdue">never touched</span>';
      }

      const sub = [c.current_title, c.current_company].filter(Boolean).map(esc).join(' · ');
      const dn = esc(c.name);
      const dc = esc(c.current_company || '');
      out.push(
        '<div class="cw-row">' +
          '<div>' +
            '<div class="cw-nm">' + esc(c.name) + '</div>' +
            (sub ? '<div class="cw-sub">' + sub + '</div>' : '') +
            (c.last_signal ? '<div class="cw-state"><em>' + esc(c.last_signal) + '</em></div>' : '') +
            '<div class="cw-tags">' + duePill + '</div>' +
          '</div>' +
          '<div class="cw-right">' +
            '<button class="cw-iconbtn cw-tick watch-action" data-action="touch" data-name="' + dn + '" data-company="' + dc + '" title="Mark contacted">✓</button>' +
            '<button class="cw-iconbtn danger watch-action" data-action="remove" data-name="' + dn + '" data-company="' + dc + '" title="Remove from watch list">🗑</button>' +
          '</div>' +
        '</div>'
      );
    }
    out.push('</div>');
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
    const r = await fetch('/api/candidates/watch/touch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, current_company: company, signal: '' }),
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
  // Apply the default 'active' filter immediately so followed-up /
  // dismissed items aren't shown in the main view on first paint.
  applyFilter('active');
  applyLeadFilter('active');
  loadPulses();
  loadEvents();
  loadSpecialistSignals();
  loadWatchList();
  loadRecentReports();
  maybeAutoRefresh();
});

// Consistency guard: if the dashboard opens with BOTH leads and
// predictors empty (i.e. the dashboard-state hydrate hasn't populated
// them yet), auto-pull today's brief once — the same action as the
// Daily Refresh button — so the user never sees a half-populated
// dashboard (Placement Windows / Hire Watch showing while Leads / Pre-Market
// sit empty). Guarded by sessionStorage so it fires at most once per
// tab session and can never loop. Self-disables the moment the
// state-branch hydrate works (leads won't be empty, so it won't fire).
async function maybeAutoRefresh() {
  try {
    const leadsN = parseInt((document.getElementById('leads-count') || {}).textContent || '0', 10);
    const predsN = parseInt((document.getElementById('pred-count') || {}).textContent || '0', 10);
    if (leadsN > 0 || predsN > 0) return;            // already populated
    if (sessionStorage.getItem('vma_autorefresh')) return;  // already tried this session
    sessionStorage.setItem('vma_autorefresh', '1');

    const btn = document.getElementById('refresh-btn');
    // Write only the .rbtn-label span so the persistent SVG icon survives.
    const lbl = btn && btn.querySelector('.rbtn-label');
    if (btn) { btn.disabled = true; if (lbl) lbl.textContent = 'Loading today’s brief…'; }
    const r = await fetch('/api/refresh', { method: 'POST' });
    const j = await r.json();
    if (j.ok && (j.leads > 0 || j.predictors > 0)) {
      // Re-parse cascade moves off the fresh signals, then reload so
      // every panel reflects today's data together.
      try { await fetch('/api/cascade/scour', { method: 'POST' }); } catch (e) {}
      window.location.reload();
    } else if (btn) {
      // Genuinely nothing to pull (no token / no artifact / 0 results).
      // Leave the panels empty and restore the button silently.
      btn.disabled = false; if (lbl) lbl.textContent = 'Daily Refresh';
    }
  } catch (e) {
    const btn = document.getElementById('refresh-btn');
    const lbl = btn && btn.querySelector('.rbtn-label');
    if (btn) { btn.disabled = false; if (lbl) lbl.textContent = 'Daily Refresh'; }
  }
}

// ---- In-place triage for the Hire Watch list (no reload) ----
// Recompute the pill counts from the DOM and re-apply the active filter,
// so marking a row updates instantly without a full page reload.
// (Framework Eligibility now lives in its own panel — see fwRefresh.)
function csRefresh() {
  const bar = document.getElementById('cs-filter-bar');
  const root = document.getElementById('cascade-body');
  if (!bar || !root) return;
  const counts = { active: 0, followed_up: 0, dismissed: 0 };
  root.querySelectorAll('.cascade-item').forEach(it => {
    const b = it.getAttribute('data-cs-bucket') || 'active';
    if (b in counts) counts[b]++;
  });
  const set = (id, n) => { const e = document.getElementById(id); if (e) e.textContent = n; };
  set('cs-pc-active', counts.active);
  set('cs-pc-followed_up', counts.followed_up);
  set('cs-pc-dismissed', counts.dismissed);
  const ap = bar.querySelector('.lead-filter-pill.active');
  const f = ap ? (ap.getAttribute('data-filter') || 'active') : 'active';
  root.querySelectorAll('.cascade-item').forEach(it => {
    const b = it.getAttribute('data-cs-bucket') || 'active';
    it.style.display = (f === 'all' || b === f) ? '' : 'none';
  });
}

// ---- In-place triage for Framework Eligibility (own panel, no reload) ----
function fwRefresh() {
  const bar = document.getElementById('fw-filter-bar');
  const root = document.getElementById('framework-body');
  if (!bar || !root) return;
  const counts = { active: 0, followed_up: 0, dismissed: 0 };
  root.querySelectorAll('.framework-row').forEach(it => {
    const s = it.getAttribute('data-status') || 'active';
    if (s in counts) counts[s]++;
  });
  const set = (id, n) => { const e = document.getElementById(id); if (e) e.textContent = n; };
  set('fw-pc-active', counts.active);
  set('fw-pc-followed_up', counts.followed_up);
  set('fw-pc-dismissed', counts.dismissed);
  const ap = bar.querySelector('.lead-filter-pill.active');
  const f = ap ? (ap.getAttribute('data-filter') || 'active') : 'active';
  root.querySelectorAll('.framework-row').forEach(it => {
    const s = it.getAttribute('data-status') || 'active';
    it.style.display = (f === 'all' || s === f) ? '' : 'none';
  });
}

function hwCsButtons(side, status) {
  let h = '<button class="btn-mini cs-copy" type="button">&#9993; Copy opener</button>';
  if (status === 'active') {
    h += '<button class="btn-mini cs-action" data-side="' + side + '" data-status="followed_up" type="button">&#10003; Mark followed up</button>'
       + '<button class="btn-mini cs-action ghost" data-side="' + side + '" data-status="dismissed" type="button">&#10005; Dismiss</button>';
  } else {
    h += '<button class="btn-mini cs-action" data-side="' + side + '" data-status="active" type="button">&#8634; Restore</button>';
  }
  return h;
}

function fwButtons(status) {
  if (status === 'active') {
    return '<button class="btn-mini framework-action" data-status="followed_up" type="button">&#10003; Mark followed up</button>'
         + '<button class="btn-mini framework-action ghost" data-status="dismissed" type="button">&#10005; Dismiss</button>';
  }
  return '<button class="btn-mini framework-action" data-status="active" type="button">&#8634; Restore</button>';
}

// ---------- Hire Watch (cascade events) ----------
(function(){
  const root = document.getElementById('cascade-body');
  if (!root) return;

  // ----- Filter pills (Active / Followed up / Dismissed / All) -----
  const bar = document.getElementById('cs-filter-bar');
  function applyCsFilter(filter) {
    root.querySelectorAll('.cascade-item').forEach(item => {
      const bucket = item.getAttribute('data-cs-bucket') || 'active';
      item.style.display = (filter === 'all' || bucket === filter) ? '' : 'none';
    });
  }
  if (bar) {
    bar.addEventListener('click', (ev) => {
      const pill = ev.target.closest('.lead-filter-pill');
      if (!pill) return;
      bar.querySelectorAll('.lead-filter-pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      applyCsFilter(pill.getAttribute('data-filter') || 'active');
    });
    applyCsFilter('active');
  }

  // ----- Click handlers (copy opener, mark followed up / dismissed / restore) -----
  root.addEventListener('click', async (ev) => {
    const copyBtn = ev.target.closest('.cs-copy');
    if (copyBtn) {
      const side = copyBtn.closest('.cs-side');
      const txt = side && side.querySelector('.outreach-text');
      if (txt) {
        try {
          await navigator.clipboard.writeText(txt.textContent);
          const orig = copyBtn.textContent;
          copyBtn.textContent = '✓ Copied';
          setTimeout(() => { copyBtn.textContent = orig; }, 1200);
        } catch (e) { /* clipboard blocked - silent */ }
      }
      return;
    }

    const actBtn = ev.target.closest('.cs-action');
    if (actBtn) {
      const item = actBtn.closest('.cascade-item');
      const id = item && item.getAttribute('data-event-id');
      const side = actBtn.getAttribute('data-side');
      const status = actBtn.getAttribute('data-status');
      if (!id || !side || !status) return;
      actBtn.disabled = true;
      try {
        const r = await fetch('/api/cascade/mark', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ event_id: id, side: side, status: status }),
        });
        const j = await r.json();
        if (j.ok) {
          // Update the marked side in place, recompute the row's aggregate
          // bucket, and refresh the filter/counts — no full-page reload.
          const sideEl = actBtn.closest('.cs-side');
          if (sideEl) {
            sideEl.setAttribute('data-side-status', status);
            const actions = sideEl.querySelector('.item-actions');
            if (actions) actions.innerHTML = hwCsButtons(side, status);
          }
          const sides = Array.from(item.querySelectorAll('.cs-side'))
            .map(s => s.getAttribute('data-side-status') || 'active')
            .filter(s => s !== 'n/a');
          let bucket = 'active';
          if (sides.some(s => s === 'active')) bucket = 'active';
          else if (sides.some(s => s === 'called' || s === 'followed_up')) bucket = 'followed_up';
          else if (sides.length && sides.every(s => s === 'dismissed')) bucket = 'dismissed';
          item.setAttribute('data-cs-bucket', bucket);
          csRefresh();
        } else {
          actBtn.disabled = false;
          alert(j.detail || 'Could not update.');
        }
      } catch (e) {
        actBtn.disabled = false;
        alert('Network error: ' + e.message);
      }
    }
  });

  // Manual re-scan (parses the latest_signals.json again — useful if
  // morning_brief ran but the cascade scour hadn't yet executed).
  const scourBtn = document.getElementById('cs-scour');
  if (scourBtn) {
    scourBtn.addEventListener('click', async () => {
      const orig = scourBtn.textContent;
      scourBtn.textContent = 'Scanning…';
      scourBtn.disabled = true;
      try {
        const r = await fetch('/api/cascade/scour', { method: 'POST' });
        const j = await r.json();
        scourBtn.textContent = j.ok
          ? (j.events_new > 0
              ? (j.events_new + ' new — reloading…')
              : 'No new moves')
          : 'Scan failed';
        if (j.ok && j.events_new > 0) {
          setTimeout(() => window.location.reload(), 800);
        } else {
          setTimeout(() => { scourBtn.textContent = orig; scourBtn.disabled = false; }, 1400);
        }
      } catch (e) {
        scourBtn.textContent = 'Network error';
        setTimeout(() => { scourBtn.textContent = orig; scourBtn.disabled = false; }, 1400);
      }
    });
  }
})();

// ---------- Funding signals (followed_up / dismissed / restore) ----------
// Delegated handler on the predictor list — funding-action buttons can
// live anywhere inside the Pre-Market Signals panel.
(function(){
  const host = document.getElementById('predictor-list');
  if (!host) return;
  host.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.funding-action');
    if (!btn) return;
    const row = btn.closest('.funding-row');
    const fid = row && row.getAttribute('data-fid');
    const status = btn.getAttribute('data-status');
    if (!fid || !status) return;
    btn.disabled = true;
    try {
      const r = await fetch('/api/funding/mark', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fid: fid, status: status }),
      });
      const j = await r.json();
      if (j.ok) {
        setTimeout(() => window.location.reload(), 200);
      } else {
        btn.disabled = false;
        alert(j.detail || 'Could not update.');
      }
    } catch (e) {
      btn.disabled = false;
      alert('Network error: ' + e.message);
    }
  });
})();

// Framework Eligibility — own panel (#framework-body): filter pills +
// in-place triage. Unglued from the Hire Watch list.
(function(){
  const root = document.getElementById('framework-body');
  if (!root) return;

  const bar = document.getElementById('fw-filter-bar');
  function applyFwFilter(filter) {
    root.querySelectorAll('.framework-row').forEach(item => {
      const st = item.getAttribute('data-status') || 'active';
      item.style.display = (filter === 'all' || st === filter) ? '' : 'none';
    });
  }
  if (bar) {
    bar.addEventListener('click', (ev) => {
      const pill = ev.target.closest('.lead-filter-pill');
      if (!pill) return;
      bar.querySelectorAll('.lead-filter-pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      applyFwFilter(pill.getAttribute('data-filter') || 'active');
    });
    applyFwFilter('active');
  }

  // (Row expand/collapse is handled by the global .row2-head handler.)
  // In-place triage (mark followed up / dismissed / restore).
  root.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.framework-action');
    if (!btn) return;
    const row = btn.closest('.framework-row');
    const key = row && row.getAttribute('data-fwid');
    const status = btn.getAttribute('data-status');
    if (!key || !status) return;
    btn.disabled = true;
    try {
      const r = await fetch('/api/framework/mark', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key, status: status }),
      });
      const j = await r.json();
      if (j.ok) {
        row.setAttribute('data-status', status);
        const actions = row.querySelector('.item-actions');
        if (actions) actions.innerHTML = fwButtons(status);
        const tags = row.querySelector('.row2-tags');
        if (tags) {
          tags.querySelectorAll('.status-badge').forEach(b => b.remove());
          if (status === 'followed_up') tags.insertAdjacentHTML('beforeend', '<span class="status-badge followed-up">&#10003;</span>');
          else if (status === 'dismissed') tags.insertAdjacentHTML('beforeend', '<span class="status-badge dismissed">dismissed</span>');
        }
        fwRefresh();
      } else {
        btn.disabled = false;
        alert(j.detail || 'Could not update.');
      }
    } catch (e) {
      btn.disabled = false;
      alert('Network error: ' + e.message);
    }
  });
})();

// ---------- Recent Reports Generated (last 48h) ----------
async function flagContact(e, link, company, slot, name) {
  e.preventDefault();
  if (!confirm('Flag ' + name + ' as the wrong contact at ' + company + '?\n' +
               '(the resolver will skip them until the roster entry changes)')) return;
  link.textContent = 'flagging…';
  try {
    const r = await fetch('/api/contacts/flag', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company, slot, name }),
    });
    const j = await r.json();
    if (j.ok) {
      link.textContent = 'flagged';
      link.style.color = '#1A3D7C';
    } else {
      link.textContent = 'wrong?';
      alert(j.detail || 'Could not flag.');
    }
  } catch (err) {
    link.textContent = 'wrong?';
    alert('Network error: ' + err.message);
  }
}

async function clearRecentReports(btn) {
  if (!confirm('Delete all recently generated reports?')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Clearing…'; }
  try {
    await fetch('/api/output/clear', { method: 'POST' });
  } catch (e) { /* best-effort */ }
  if (btn) { btn.disabled = false; btn.textContent = 'Clear'; }
  loadRecentReports();
}

async function loadRecentReports() {
  const body = document.getElementById('recent-reports');
  if (!body) return;
  const empty = document.getElementById('rr-empty');
  const DL_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
    '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  try {
    const r = await fetch('/api/output/recent');
    const j = await r.json();
    if (!j.rows || !j.rows.length) {
      body.innerHTML = '';
      if (empty) empty.style.display = '';
      return;
    }
    if (empty) empty.style.display = 'none';
    const now = Date.now();
    const out = ['<table class="rr-table"><thead><tr>' +
      '<th>Type</th><th>Company</th><th>Name</th><th>Created</th>' +
      '<th class="rr-acts">Report</th></tr></thead><tbody>'];
    for (const x of j.rows.slice(0, 30)) {
      const t = new Date(x.ts).getTime();
      const mins = Math.max(0, Math.round((now - t) / 60000));
      const ago = mins < 1 ? 'just now'
                : mins < 60 ? mins + ' min ago'
                : mins < 1440 ? Math.round(mins / 60) + 'h ago'
                : Math.round(mins / 1440) + 'd ago';
      let acts;
      if (x.id) {
        const base = '/api/output/view?artifact=' +
          encodeURIComponent(x.artifact) + '&id=' + encodeURIComponent(x.id);
        acts = '<a class="btn-mini" href="' + base + '" target="_blank" rel="noopener noreferrer">View</a>' +
          '<a class="rr2-icon" href="' + base + '&download=1" title="Download" download>' + DL_SVG + '</a>';
      } else {
        acts = '<span class="rr-gen">generating…</span>';
      }
      out.push(
        '<tr><td class="rr-type">' + esc(x.type) + '</td>' +
        '<td>' + (x.company && x.company !== '—' ? esc(x.company) : '<span class="rr-muted">—</span>') + '</td>' +
        '<td>' + (x.name ? esc(x.name) : '<span class="rr-muted">—</span>') + '</td>' +
        '<td class="rr-when">' + esc(ago) + '</td>' +
        '<td class="rr-acts">' + acts + '</td></tr>'
      );
    }
    out.push('</tbody></table>');
    body.innerHTML = out.join('');
  } catch (e) {
    body.innerHTML = '<div class="empty compact">Could not load recent reports.</div>';
  }
}

// ===========================================================================
// 3-PAGE SHELL — additive nav, composer morph, BD-calendar modal. Purely
// layout/UI; does not touch any existing data loader or handler. Existing
// delegated handlers select by class/closest (never DOM position), so the
// relocated subtrees keep working unchanged.
// ===========================================================================
(function () {
  // --- VMA logo tile (navy) — injected like the mockup's LOGO const ---
  var LOGO = '<svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">'
    + '<rect width="100" height="100" fill="#3E5C84"/>'
    + '<text x="50" y="56" text-anchor="middle" font-family="Inter,Arial,sans-serif" font-weight="800" font-size="31" letter-spacing="-1.6" fill="#fff">VMA</text>'
    + '<text x="51.6" y="77" text-anchor="middle" font-family="Inter,Arial,sans-serif" font-weight="300" font-size="14.5" letter-spacing="3.4" fill="#fff">GROUP</text></svg>';
  document.querySelectorAll('.vma-ic, .rail-logo').forEach(function (e) { e.innerHTML = LOGO; });

  // --- rail nav: CSS show/hide of .page; inactive pages stay in the DOM ---
  function render(page) {
    document.querySelectorAll('.page').forEach(function (s) {
      s.classList.toggle('active', s.id === page);
    });
    var map = { leads: 'nav-leads', agent: 'nav-agent', cal: 'nav-cal', reports: 'nav-reports' };
    Object.keys(map).forEach(function (k) {
      var b = document.getElementById(map[k]);
      if (b) b.classList.toggle('active', k === page);
    });
  }
  var nl = document.getElementById('nav-leads');
  var na = document.getElementById('nav-agent');
  var nc = document.getElementById('nav-cal');
  var nr = document.getElementById('nav-reports');
  if (nl) nl.onclick = function () { render('leads'); };
  if (na) na.onclick = function () { render('agent'); };
  if (nc) nc.onclick = function () { render('cal'); };
  // Sidebar download icon → the separate Recent Reports page (refresh list).
  if (nr) nr.onclick = function () { render('reports'); if (typeof loadRecentReports === 'function') loadRecentReports(); };
  render('leads');

  // --- composer morph: chips toggle which MOVED form is visible ---
  var agent = document.getElementById('agent');
  if (agent) {
    var composer = agent.querySelector('.composer');
    var cprompt = document.getElementById('cprompt');
    var capForms = agent.querySelectorAll('.cap-form');
    var chips = agent.querySelectorAll('.chip[data-cap]');

    function showFree() {
      composer.dataset.mode = 'free';
      capForms.forEach(function (f) { f.classList.remove('active'); });
      var ch = agent.querySelector('.cap-choose'); if (ch) ch.remove();   // clear any chooser
      if (cprompt) cprompt.style.display = '';
      chips.forEach(function (c) { c.classList.remove('active'); });
    }
    function showCap(cap) {
      composer.dataset.mode = cap;
      if (cprompt) cprompt.style.display = 'none';
      capForms.forEach(function (f) { f.classList.toggle('active', f.dataset.cap === cap); });
      chips.forEach(function (c) { c.classList.toggle('active', c.dataset.cap === cap); });
      // focus the first field of the revealed form for quick entry
      var active = agent.querySelector('.cap-form.active');
      var first = active && active.querySelector('input, select, textarea');
      if (first) { try { first.focus(); } catch (e) {} }
    }
    showFree();
    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        var cap = chip.dataset.cap;
        if (composer.dataset.mode === cap) showFree(); else showCap(cap);
      });
    });
    // ----- Free-text router: type a request any way you like and the
    // composer works out which report you mean, fills the matching form from
    // your words, and runs it. Restores the natural-language entry we had
    // before. Submitting reuses the form's own submit so dispatch()'s popup
    // keeps its user-gesture. -----
    function classifyPrompt(text) {
      var s = text.toLowerCase();
      if (/\b(pitch|proposal|retain|upgrade|pitch pack)\b/.test(s)) return 'pitch';
      if (/\b(reverse|match|place|where (can|could|to)|accounts? for|fit)\b/.test(s)) return 'reverse';
      if (/\b(meeting|brief|prep|pre-?meeting|walk into|before (my|the))\b/.test(s)) return 'premeeting';
      if (/\b(sweep|missed|catch[\s-]?up|scan|last\s+\d+\s*days?|recent)\b/.test(s)) return 'sweep';
      return null;   // ambiguous — ask which of the four rather than guessing
    }
    var CAP_LABELS = { pitch: 'Pitch Pack', reverse: 'Reverse Match',
                       premeeting: 'Pre-meeting Brief', sweep: 'Manual Sweep' };
    // Morph the pill into one of the four forms, pre-filled from the prompt.
    // submit=true runs it immediately (confident match); submit=false just
    // reveals the pre-filled form so the user can confirm (after an ambiguous
    // prompt where they picked the type).
    function fillAndShow(cap, text, submit) {
      if (cap === 'pitch') {
        setField('pp-account', extractAccount(text)); setField('pp-role', extractRole(text));
      } else if (cap === 'reverse') {
        // candidate name (after match/for), their company (after at/from), title
        setField('rm-name', extractCandidate(text));
        setField('rm-company', extractCompany(text));
        setField('rm-title', extractRole(text));
      } else if (cap === 'premeeting') {
        // account = who you're meeting (after for/at); contact = person (after with)
        setField('pm-account', phraseAfter(text, ['for', 'at', 'about']) || extractAccount(text));
        var withName = phraseAfter(text, ['with']);
        if (withName) setField('pm-contact', withName);
      } else if (cap === 'sweep') {
        var d = (text.match(/(\d+)\s*days?/) || [])[1]; setField('sw-days', d || '14');
      }
      showCap(cap);
      if (submit) {
        var form = document.getElementById(
          cap === 'pitch' ? 'pitch-form' : cap === 'reverse' ? 'rm-form'
          : cap === 'premeeting' ? 'pm-form' : 'sweep-form');
        if (form) {
          var btn = form.querySelector('button.send') || form.querySelector('button[type="submit"]');
          if (btn) btn.click();   // user-gesture click keeps the popup unblocked
        }
      }
    }
    // Ambiguous-prompt chooser: morph the pill into a "which of the four?"
    // question with the four options as buttons. Picking one reveals that
    // form pre-filled from what they typed (no auto-submit — they confirm).
    function showChooser(text) {
      composer.dataset.mode = 'choose';
      if (cprompt) cprompt.style.display = 'none';
      capForms.forEach(function (f) { f.classList.remove('active'); });
      chips.forEach(function (c) { c.classList.remove('active'); });
      var host = agent.querySelector('[data-cform]');
      var old = host.querySelector('.cap-choose');
      if (old) old.remove();
      var box = document.createElement('div');
      box.className = 'cap-choose';
      var btns = Object.keys(CAP_LABELS).map(function (k) {
        return '<button type="button" class="choose-opt" data-cap="' + k + '">' + CAP_LABELS[k] + '</button>';
      }).join('');
      box.innerHTML = '<div class="cf-head cf-formhead"><span class="cf-dot"></span>Which would you like to build?</div>'
        + '<div class="cf-desc">I wasn’t sure which report you meant. Pick one and I’ll fill it in from what you typed.</div>'
        + '<div class="choose-grid">' + btns + '</div>';
      host.appendChild(box);
      box.querySelectorAll('.choose-opt').forEach(function (b) {
        b.addEventListener('click', function () {
          box.remove();
          fillAndShow(b.dataset.cap, text, false);   // reveal pre-filled, let them confirm
        });
      });
    }
    // ---- entity extraction ----------------------------------------------
    // Words that end a name phrase (the sentence has moved on).
    var BOUNDARY = /^(for|at|on|about|with|from|to|in|of|and|the|a|an|i|im|i'm|we|our|my|me|is|are|am|who|that|this|it|meeting|meet|role|position|job|vacancy|please|today|tomorrow|tonight|next|this|on|by)$/i;
    var PARTICLE = /^(of|and|the|&|de|du|la|le|van|von)$/i;   // kept INSIDE a name
    function titleCase(s) {
      return s.replace(/\b([a-z])([a-z']*)/gi, function (_, a, b) {
        // keep ALL-CAPS acronyms (HSBC, PA, NHS, BP) as-is
        var w = a + b;
        if (w.length <= 4 && w === w.toUpperCase()) return w;
        return a.toUpperCase() + b.toLowerCase();
      });
    }
    // Grab the phrase right after any of `cues`, stopping at the next boundary
    // word; works regardless of case, then Title-Cases it. e.g. after "for" in
    // "...for molly cutler at pa consulting" -> "Molly Cutler".
    function phraseAfter(text, cues) {
      // try each cue in priority order; take the first that yields a name
      for (var ci = 0; ci < cues.length; ci++) {
        var m = text.match(new RegExp('\\b' + cues[ci] + '\\s+(.+)$', 'i'));
        if (!m) continue;
        var words = m[1].split(/\s+/);
        var out = [];
        for (var i = 0; i < words.length; i++) {
          var hadComma = /[,.;:]/.test(words[i]);
          var raw = words[i].replace(/[,.;:]+.*$/, '');   // a comma also ends the run
          var bare = raw.replace(/[^\w'&-]/g, '');
          if (!bare) break;
          if (PARTICLE.test(bare)) {   // particle kept only if more name follows
            if (out.length && i + 1 < words.length && !BOUNDARY.test(words[i + 1].replace(/[^\w'&-]/g, ''))) {
              out.push(raw); if (hadComma) break; continue;
            }
            break;
          }
          if (BOUNDARY.test(bare)) break;   // next cue / sentence continues
          out.push(raw);
          if (hadComma || out.length >= 4) break;   // stop at a comma or 4 words
        }
        while (out.length && PARTICLE.test(out[out.length - 1])) out.pop();
        if (out.length) return titleCase(out.join(' ').trim());
      }
      return '';
    }
    function extractAccount(text) {
      // company/account: after for/at/with/about, else first capitalised run
      return phraseAfter(text, ['for', 'at', 'with', 'about'])
          || (function () {
               var m = text.match(/\b([A-Z][\w&.'-]*(?:\s+[A-Za-z][\w&.'-]*){0,4})\b/);
               return m ? titleCase(m[1].replace(/[,.;:]+$/, '').trim()) : '';
             })();
    }
    function extractRole(text) {
      var m = text.match(/\b((?:head|director|chief|vp|manager|lead|officer)[\w ,/&-]*?(?:communications?|comms|affairs|relations|marketing|engagement))\b/i);
      return m ? titleCase(m[1].replace(/[,.;:]+$/, '').trim()) : '';
    }
    // Reverse Match wants: candidate (after "match"/"for"), their company
    // (after "at"/"from"), and current title.
    function extractCandidate(text) {
      return phraseAfter(text, ['match', 'for', 'place', 'candidate']);
    }
    function extractCompany(text) {
      return phraseAfter(text, ['at', 'from', 'with', 'works at', 'currently at']);
    }
    function setField(id, val) { var el = document.getElementById(id); if (el && val) el.value = val; }
    function runFromPrompt() {
      var text = (cprompt && cprompt.value || '').trim();
      if (!text) { if (cprompt) cprompt.focus(); return; }
      var cap = classifyPrompt(text);
      if (!cap) { showChooser(text); return; }   // ambiguous -> ask which of the four
      fillAndShow(cap, text, true);              // confident -> fill + run
    }
    // Footer send (free mode) + Enter in the prompt both route the text.
    var footSend = document.getElementById('composer-send');
    if (footSend) footSend.addEventListener('click', runFromPrompt);
    if (cprompt) {
      cprompt.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); runFromPrompt(); }
      });
    }
  }

  // --- BD-calendar modal: move a context panel in on open, back on close ---
  var host = document.getElementById('cal-host');
  var modal = document.getElementById('bd-modal');
  var mb = document.getElementById('bd-mb');
  var mic = document.getElementById('bd-mic');
  var mt = document.getElementById('bd-mt');
  var mx = document.getElementById('bd-mx');
  var BDMETA = {
    windows:    { ic: '◴', t: 'Placement Windows' },
    events:     { ic: '▦', t: 'Events & Networking' },
    frameworks: { ic: '❏', t: 'Framework Eligibility' }
  };
  var openKey = null;
  function panelFor(key) { return host ? host.querySelector('[data-bd="' + key + '"]') : null; }
  function openBD(key) {
    var meta = BDMETA[key]; if (!meta || !mb) return;
    var panel = panelFor(key); if (!panel) return;
    if (openKey && openKey !== key) {   // return any already-open panel to the host first (no stranding on card-to-card switch)
      var prev = mb.querySelector('[data-bd="' + openKey + '"]');
      if (prev && host) host.appendChild(prev);
    }
    if (mic) mic.textContent = meta.ic;
    if (mt) mt.textContent = meta.t;
    mb.appendChild(panel);            // relocate the live panel into the modal
    openKey = key;
    modal.classList.add('open');
  }
  function closeBD() {
    if (!openKey) { modal.classList.remove('open'); return; }
    var panel = mb ? mb.querySelector('[data-bd="' + openKey + '"]') : null;
    if (panel && host) host.appendChild(panel);   // move it back to the host
    openKey = null;
    modal.classList.remove('open');
  }
  document.querySelectorAll('.cc-card[data-open]').forEach(function (c) {
    c.addEventListener('click', function () { openBD(c.dataset.open); markCardSeen(c.dataset.open); });
  });
  if (mx) mx.onclick = closeBD;
  if (modal) modal.addEventListener('click', function (e) { if (e.target === modal) closeBD(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && openKey) closeBD(); });

  // --- card "new" badges: a card shows "N new" only when items are present
  //     whose stable key the user hasn't SEEN yet. Opening a card marks its
  //     current items as seen (clears the badge); a genuinely-new item that
  //     appears later re-triggers it. Seen-keys persist in localStorage so the
  //     badge doesn't reappear on every reload. ---
  var SEEN_PREFIX = 'bdSeen:';   // localStorage key per card
  function seenSet(key) {
    try { return new Set(JSON.parse(localStorage.getItem(SEEN_PREFIX + key) || '[]')); }
    catch (e) { return new Set(); }
  }
  function saveSeen(key, set) {
    try { localStorage.setItem(SEEN_PREFIX + key, JSON.stringify(Array.from(set))); } catch (e) {}
  }
  // Current item keys per card (unique), read from the rendered panels.
  function currentKeys(key) {
    var sel = key === 'windows' ? '#pulses-body .win-row[data-key]'
            : key === 'events' ? '#events-body .ev-item[data-evkey]'
            : key === 'frameworks' ? '#framework-body .framework-row[data-fwid]'
            : null;
    if (!sel) return [];
    var attr = key === 'windows' ? 'data-key' : key === 'events' ? 'data-evkey' : 'data-fwid';
    var seen = {}, out = [];
    Array.prototype.forEach.call(document.querySelectorAll(sel), function (el) {
      var k = el.getAttribute(attr);
      if (k && !seen[k]) { seen[k] = 1; out.push(k); }
    });
    return out;
  }
  function setBadge(key, n) {
    var b = document.querySelector('.cc-card[data-open="' + key + '"] .cbadge');
    if (b) b.textContent = n ? (n + ' new') : '';
  }
  function refreshCardBadges() {
    ['windows', 'events', 'frameworks'].forEach(function (key) {
      var keys = currentKeys(key);
      if (!keys.length) { setBadge(key, 0); return; }   // panel not loaded yet / empty
      var seen = seenSet(key);
      var unseen = keys.filter(function (k) { return !seen.has(k); });
      setBadge(key, unseen.length);
    });
  }
  // Mark every currently-shown item in a card as seen, then clear its badge.
  function markCardSeen(key) {
    var keys = currentKeys(key);
    if (!keys.length) return;
    var seen = seenSet(key);
    keys.forEach(function (k) { seen.add(k); });
    saveSeen(key, seen);
    setBadge(key, 0);
  }
  // Loaders are async fetches fired on DOMContentLoaded; recompute a few
  // times so the badges settle once the panels have rendered.
  [800, 2000, 4000].forEach(function (ms) { setTimeout(refreshCardBadges, ms); });
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
