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
    # Any lead first seen >7d ago is dropped here — Today's Leads
    # naturally clears without Sara having to triage stale items.
    from tool import lead_first_seen
    pre_filter = [{**s, "lead_id": _lead_id(s)} for s in data]
    kept_ids = lead_first_seen.record_and_filter(
        [s["lead_id"] for s in pre_filter])
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


# Render's filesystem is ephemeral — pull the durably-stored copies of
# the dashboard-written state files into local state before serving, so
# a redeploy/cold-start keeps dismissed leads and the watch roster.
def _boot_state_hydrate():
    try:
        from tool import github_state
        github_state.hydrate([
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
        ])
    except Exception as e:
        log.warning("state hydrate skipped: %s", e)


_boot_state_hydrate()


_LAST_STATE_REFRESH = None  # datetime of the last stale re-hydrate attempt


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
    global _LAST_STATE_REFRESH
    if _brief_is_today():
        return
    now = datetime.now(timezone.utc)
    if _LAST_STATE_REFRESH and (now - _LAST_STATE_REFRESH) < timedelta(minutes=10):
        return
    _LAST_STATE_REFRESH = now
    try:
        if GITHUB_TOKEN:
            res = refresh_latest_brief_from_github()
            log.info("auto stale-refresh: %s", res.get("detail", res))
        else:
            _boot_state_hydrate()
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


@app.route("/")
@_auth_required
def landing():
    """Gemini-clone landing — verbatim ground-truth CSS captured from
    gemini.google.com/app, ::before recentred for our viewport. VMA
    wordmark + click-pill into the dashboard. No globe, no map."""
    return render_template_string(LANDING_TEMPLATE)


@app.route("/dashboard")
@_auth_required
def index():
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
    from tool.framework_watch import load_frameworks
    framework_events = load_frameworks()
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
    rows = [r for r in load_pulses(limit=20) if r.get("key") not in dismissed]
    return jsonify({"rows": rows[:10], "total": len(rows[:10])})


@app.route("/api/industry-events", methods=["GET"])
@_auth_required
def api_industry_events():
    """UK + European comms industry events (awards, conferences,
    summits) for the next ~6 months. Internal + external comms."""
    from tool.calendar_pulses import load_events
    from tool import pulse_dismiss
    dismissed = pulse_dismiss.get_dismissed()
    rows = [r for r in load_events(limit=40) if r.get("key") not in dismissed]
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
     L1 Ethena-style globe over Gemini ground-truth halo.
     Gemini ::before values untouched (only top/left/transform
     recentred). Wireframe globe overlay + live-pulse dot.
     ============================================================ */
  *{box-sizing:border-box;}
  html,body{margin:0;padding:0;height:100%;}
  html{background-color:rgba(0,0,0,0);background-image:none;}
  body{
    background-color:rgb(253,252,252);
    background-image:none;
    min-height:100vh;
    position:relative;overflow:hidden;
    display:flex;flex-direction:column;align-items:center;justify-content:center;gap:36px;
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

  /* Wireframe globe with intelligence-point pulse nodes */
  .globe{
    position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
    width:min(720px,76vh);height:min(720px,76vh);
    z-index:0;pointer-events:none;
  }
  .globe .ring{fill:none;stroke:rgba(58,143,164,.42);stroke-width:1;}
  .globe .ring.bold{stroke:rgba(58,143,164,.68);stroke-width:1.3;}
  .globe .spin{transform-origin:center;animation:spin 32s linear infinite;}
  @keyframes spin{from{transform:rotateZ(0deg);}to{transform:rotateZ(360deg);}}
  .node{
    fill:rgb(157,210,255);
    filter:drop-shadow(0 0 6px rgb(157,210,255));
    animation:nodepulse 3.2s ease-in-out infinite;
  }
  @keyframes nodepulse{
    0%,100%{r:3;opacity:.75;}
    50%{r:6;opacity:1;}
  }
  .node.b{animation-delay:.7s;}.node.c{animation-delay:1.4s;}.node.d{animation-delay:2.1s;}

  /* Content */
  .stage{position:relative;z-index:1;display:flex;flex-direction:column;align-items:center;gap:32px;text-align:center;}
  .wordmark{font-family:Arial,Helvetica,sans-serif;color:#1F1F1F;display:inline-flex;align-items:baseline;line-height:1;}
  .wordmark .v{font-weight:800;letter-spacing:.06em;font-size:64px;}
  .wordmark .g{font-weight:300;letter-spacing:.32em;font-size:64px;padding-left:.42em;margin-right:-.32em;}

  .pill{
    background:rgb(255,255,255);border:none;border-radius:32px;
    box-shadow:0 2px 8px -2px rgba(0,0,0,0.16);
    padding:0 22px;width:660px;height:64px;max-width:none;
    display:flex;align-items:center;justify-content:center;gap:14px;
    text-decoration:none;color:rgb(31,31,31);cursor:pointer;
    transition:transform .15s ease, box-shadow .15s ease;
  }
  .pill:hover{transform:translateY(-1px);box-shadow:0 6px 16px -2px rgba(0,0,0,.18);}

  /* Live pulse dot — actively vibrating + glowing.
     The dot itself throbs (scale + brightness) while a ::before
     pseudo-element radiates an expanding ring outward. */
  .dot{
    position:relative;
    width:11px;height:11px;border-radius:50%;
    background:#9FD181;flex-shrink:0;
    box-shadow:
      0 0 10px rgba(159,209,129,.85),
      inset 0 0 3px rgba(255,255,255,.4);
    animation:dot-throb 1.8s ease-in-out infinite;
  }
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
  .arrow{color:#5F6368;font-size:18px;line-height:1;margin-left:6px;}

  @media (max-width:720px){
    .stage{gap:24px;}
    .wordmark .v,.wordmark .g{font-size:44px;}
    .pill{width:90%;height:56px;}
    .lbl{font-size:11px;letter-spacing:.18em;}
    .globe{width:min(420px,48vh);height:min(420px,48vh);}
  }
  @media (prefers-reduced-motion: reduce){
    .globe .spin,.node,.dot,.dot::before,.dot::after{animation:none;}
  }
</style>
</head>
<body>
  <svg class="globe" viewBox="0 0 720 720">
    <g class="spin">
      <circle class="ring bold" cx="360" cy="360" r="340"/>
      <ellipse class="ring" cx="360" cy="360" rx="340" ry="80"/>
      <ellipse class="ring" cx="360" cy="360" rx="340" ry="160"/>
      <ellipse class="ring" cx="360" cy="360" rx="340" ry="240"/>
      <ellipse class="ring" cx="360" cy="360" rx="340" ry="340"/>
      <ellipse class="ring" cx="360" cy="360" rx="80"  ry="340"/>
      <ellipse class="ring" cx="360" cy="360" rx="160" ry="340"/>
      <ellipse class="ring" cx="360" cy="360" rx="240" ry="340"/>
      <ellipse class="ring" cx="360" cy="360" rx="340" ry="340"/>
      <circle class="node"   cx="320" cy="240" r="4"/>
      <circle class="node b" cx="180" cy="320" r="4"/>
      <circle class="node c" cx="360" cy="260" r="4"/>
      <circle class="node d" cx="540" cy="380" r="4"/>
    </g>
  </svg>
  <div class="stage">
    <div class="wordmark"><span class="v">VMA</span><span class="g">GROUP</span></div>
    <a class="pill" href="/dashboard">
      <span class="dot"></span>
      <span class="lbl">Intelligence Platform &middot; Live</span>
      <span class="arrow">&rarr;</span>
    </a>
  </div>
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
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Crimson+Pro:ital,wght@0,400;0,500;0,600;1,400;1,500&display=swap" rel="stylesheet">
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
      background-color: var(--bg);
      /* No body radial overlays — keep the page a flat #f7f9fc so the
         top-bar halo can fade into transparent and meet the body
         seamlessly with no visible seam. */
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

    /* ===== Calendar Pulses — year ribbon (Alternate A) =====
       Vacated Seats & Senior Moves + Placement Windows sit side-by-side;
       both panels share the same fixed 400px height for visual parity.
       Bodies scroll internally. minmax(0,1fr) + min-width:0 keep the split
       a true 50/50. */
    #hire-calendar-row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
    #hire-calendar-row > .panel { min-width: 0; }
    #hire-calendar-row .panel { height: 400px; display: flex; flex-direction: column; }
    #hire-calendar-row .panel-body { max-height: none; flex: 1; min-height: 0; overflow-y: auto; }
    /* ===== Unified findings list (.row2 — Vacated Seats moves + Framework
       Eligibility windows share this row template) =====
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
    @media (max-width: 900px) {
      /* Stack vertically on mobile so neither panel is squashed. */
      #hire-calendar-row { grid-template-columns: 1fr; }
    }
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
    .ev-list { list-style: none; margin: 0; padding: 0; }
    .ev-item { padding: 11px 16px; border-bottom: 1px solid var(--border); }
    .ev-item:last-child { border-bottom: none; }
    .ev-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .ev-name { font-weight: 600; font-size: 12.5px; color: var(--navy); }
    .ev-focus {
      font: 700 9.5px/1.4 "Inter", sans-serif; letter-spacing: .04em;
      text-transform: uppercase; padding: 2px 7px; border-radius: 10px; white-space: nowrap;
    }
    .ev-internal { background: var(--teal-soft); color: var(--teal-dark); }
    .ev-external { background: #fff4e0; color: #8a5a00; }
    .ev-mixed    { background: #eef0f3; color: #80868b; }
    .ev-open {
      font: 700 9.5px/1.4 "Inter", sans-serif; letter-spacing: .03em;
      background: #e7f3ec; color: #2e7d50; padding: 2px 7px; border-radius: 10px;
    }
    .ev-when { margin-left: auto; font-size: 11px; font-weight: 600; color: var(--text-muted); white-space: nowrap; }
    .ev-rm {
      background: transparent; color: var(--text-muted);
      border: 1px solid var(--border); border-radius: 4px; padding: 1px 6px;
      font: 500 10px/1.4 "Inter", sans-serif; cursor: pointer;
      transition: border-color .12s, color .12s;
    }
    .ev-rm:hover { border-color: #A33A22; color: #A33A22; }
    .ev-meta { font-size: 11px; color: var(--text-muted); margin-top: 5px; }
    .ev-why  { font-size: 11.5px; color: var(--text-dim); margin-top: 4px; }
    .ev-src  { font-size: 11px; margin-top: 4px; }
    .ev-src a { color: #0366d6; text-decoration: none; }

    /* ===== Groundwork row: Events & Networking + Framework Eligibility =====
       Band-C reference pair; matched height + internal scroll like the
       Hire Watch / Placement Windows row above. */
    #groundwork-row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
    #groundwork-row > .panel { min-width: 0; }
    #groundwork-row .panel { height: 360px; display: flex; flex-direction: column; }
    #groundwork-row .panel-body { max-height: none; flex: 1; min-height: 0; overflow-y: auto; }
    @media (max-width: 900px) { #groundwork-row { grid-template-columns: 1fr; } }
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
    .rr2-icon {
      width: 26px; height: 26px; border-radius: 6px;
      border: 1px solid rgba(66, 133, 244, 0.25); background: var(--teal-soft);
      color: var(--teal, #4285F4); text-decoration: none;
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 13px;
      transition: border-color .12s, background .12s;
    }
    .rr2-icon:hover { border-color: var(--teal, #4285F4); background: rgba(66, 133, 244, 0.16); }
    .rr2-gen {
      color: var(--text-muted); font-size: 10.5px;
      font-style: italic;
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
  </style>
</head>
<body>

<header class="top-bar" aria-label="VMA Group">
  <div class="brand-line-1">
    <span class="bm-vma">VMA</span><span class="bm-group">GROUP</span>
  </div>
  <div class="sub-cap">Intelligence Platform &middot; Live</div>
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
        <h2>Pre-Market Signals</h2>
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

  <!-- VACATED SEATS & SENIOR MOVES + PLACEMENT WINDOWS side-by-side.
       Vacated Seats (cascade engine: watchlist-gated senior-comms moves —
       replacement-search + re-org-watch, merges the former Hire Watch +
       Mandates Worth Following) on the left; Placement Windows
       (statutory/regulatory + policy pulses ONLY) on the right. Framework
       Windows were unglued into the Framework Eligibility panel (Band C);
       industry events into Events & Networking. Both stack under 900px. -->
  <div class="row" id="hire-calendar-row">
    <div class="panel" id="cascade-row">
      <div class="panel-header">
        <h2>Vacated Seats &amp; Senior Moves</h2>
        <span style="display:flex;align-items:center;gap:10px;">
          <button type="button" class="btn-mini" id="cs-scour">Re-scan</button>
          <span class="count" id="cascade-count">{{ cascade_events|length }}</span>
        </span>
      </div>
      <div class="filter-bar" id="cs-filter-bar">
        <button class="lead-filter-pill active" data-filter="active">Active <span class="pill-count" id="cs-pc-active">{{ cs_active_count }}</span></button>
        <button class="lead-filter-pill" data-filter="followed_up">Followed up <span class="pill-count" id="cs-pc-followed_up">{{ cs_followed_count }}</span></button>
        <button class="lead-filter-pill" data-filter="dismissed">Dismissed <span class="pill-count" id="cs-pc-dismissed">{{ cs_dismissed_count }}</span></button>
        <button class="lead-filter-pill" data-filter="all">All</button>
      </div>
      <div class="panel-body" id="cascade-body">
        {% if cascade_events|length == 0 %}
          <div class="empty compact">No UK senior-comms move in the latest brief. This panel surfaces a vacated senior-comms seat at any UK employer (core-watchlist accounts ranked first; others tagged &ldquo;Broader UK&rdquo;), plus re-org watches at watchlist firms. Off-patch / non-UK headlines are filtered out rather than shown as noise.</div>
        {% endif %}

        {% for c in cascade_events %}
          {% set old_st = c.old_co_status|default('active') %}
          {% set new_st = c.new_co_status|default('active') %}
          {% set _old_followed = old_st in ('called','followed_up') %}
          {% set _new_followed = new_st in ('called','followed_up') %}
          {% set _old_on = c.old_company and old_st != 'n/a' %}
          {% set _new_on = new_st != 'n/a' %}
          <div class="row2 cascade-item" data-event-id="{{ c.event_id }}" data-cs-bucket="{{ c.cs_bucket }}">
            <div class="row2-head">
              <span class="typ hw">VS</span>
              <span class="row2-title">{% if c.person_name %}{{ c.person_name }}{% if c.role %} &rarr; {{ c.role }}{% endif %}{% else %}{{ (c.role or 'Senior comms seat')|title }}{% if c.old_company %} &middot; {{ c.old_company }}{% endif %}{% endif %}</span>
              <span class="row2-tags">
                {% if c.confidence == 'medium' %}<span class="ipill mut" title="UK employer, not on the core watchlist — verify fit">Broader UK</span>{% endif %}
                {% if _old_on %}<span class="ipill s">Search</span>{% endif %}
                {% if _new_on %}<span class="ipill w">Watch</span>{% endif %}
              </span>
              <span class="row2-chev">&rsaquo;</span>
            </div>
            <div class="row2-detail">
              <div class="row2-sub">{% if c.old_company %}{{ c.old_company }} &rarr; {{ c.new_company }}{% else %}&rarr; {{ c.new_company }}{% endif %} · senior comms move{% if c.article_url %} · <a class="lnk" href="{{ c.article_url | safe_url }}" target="_blank" rel="noopener noreferrer">source &rsaquo;</a>{% endif %}</div>
              <div class="plays">
                {% if _old_on %}
                <div class="play search cs-side" data-side="old_co" data-side-status="{{ old_st }}">
                  <div class="play-lab">&#9654; Replacement search · {{ c.old_company }}{% if _old_followed %} · followed up{% elif old_st == 'dismissed' %} · dismissed{% endif %}</div>
                  <div class="play-desc">Seat just vacated — pitch VMA to run the replacement search.</div>
                  <pre class="outreach-text" hidden>{{ c.old_co_opener }}</pre>
                  <div class="item-actions">
                    <button class="btn-mini cs-copy" type="button">&#9993; Copy opener</button>
                    {% if old_st == 'active' %}
                      <button class="btn-mini cs-action" data-side="old_co" data-status="followed_up" type="button">&#10003; Mark followed up</button>
                      <button class="btn-mini cs-action ghost" data-side="old_co" data-status="dismissed" type="button">&#10005; Dismiss</button>
                    {% else %}
                      <button class="btn-mini cs-action" data-side="old_co" data-status="active" type="button">&#8634; Restore</button>
                    {% endif %}
                  </div>
                </div>
                {% endif %}
                {% if _new_on %}
                <div class="play cs-side" data-side="new_co" data-side-status="{{ new_st }}">
                  <div class="play-lab">&#9654; Re-org watch · {{ c.new_company }}{% if _new_followed %} · followed up{% elif new_st == 'dismissed' %} · dismissed{% endif %}</div>
                  <div class="play-desc">New comms leader landed — watch for team build-out and new briefs.</div>
                  <pre class="outreach-text" hidden>{{ c.new_co_opener }}</pre>
                  <div class="item-actions">
                    <button class="btn-mini cs-copy" type="button">&#9993; Copy opener</button>
                    {% if new_st == 'active' %}
                      <button class="btn-mini cs-action" data-side="new_co" data-status="followed_up" type="button">&#10003; Mark followed up</button>
                      <button class="btn-mini cs-action ghost" data-side="new_co" data-status="dismissed" type="button">&#10005; Dismiss</button>
                    {% else %}
                      <button class="btn-mini cs-action" data-side="new_co" data-status="active" type="button">&#8634; Restore</button>
                    {% endif %}
                  </div>
                </div>
                {% endif %}
              </div>
            </div>
          </div>
        {% endfor %}
      </div>
    </div>

    <div class="panel" id="pulses-row">
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
  </div>

  <!-- SPECIALIST SIGNALS — Water SAR / Contract-End / Mandates Worth
       Stealing, collapsed into one panel that is HIDDEN unless a
       sub-detector actually has rows. Each sub-section also hides itself
       when empty. (Mandates Worth Following was merged into the Vacated
       Seats & Senior Moves panel above.) -->
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

  <!-- GROUNDWORK & RELATIONSHIPS (Band C) — context, not a live lead list.
       Left: Events & Networking (UK/EU comms awards, conferences, summits —
       relationship / candidate-visibility moments, split out of the old BD
       Calendar; loaded from /api/industry-events).
       Right: Framework Eligibility (public-sector framework bid windows —
       where VMA can compete; eligibility/BD groundwork, ~yearly cadence).
       Unglued from the Hire Watch panel: a vacated seat is a commission
       play, a framework window is eligibility-to-bid — different things.
       Both stack under 900px. -->
  <div class="row" id="groundwork-row">
    <div class="panel">
      <div class="panel-header">
        <h2>Events &amp; Networking</h2>
        <span class="count" id="events-count">—</span>
      </div>
      <div class="panel-body" id="events-body">
        <div class="empty compact">Loading…</div>
      </div>
    </div>

    <div class="panel" id="framework-row">
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
              <span class="row2-title">{{ fw.ad_title or fw.title }}</span>
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

  <!-- ACTION BOXES -->
  <div class="actions">

    <!-- REVERSE MATCH -->
    <div class="panel action-card">
      <h3>Reverse Match</h3>
      <div class="subhead">Take a candidate, search the market fresh, and give a ranked list of accounts to match them to.</div>
      <form id="rm-form" onsubmit="dispatch(event, 'rm-form', '/api/dispatch/reverse-match')">
        <label for="rm-name">Candidate name</label>
        <input id="rm-name" name="candidate_name" placeholder="e.g. Rebecca Torres" required>
        <label for="rm-company">Current company</label>
        <input id="rm-company" name="current_company" placeholder="e.g. Vodafone" required>
        <label for="rm-title">Current title</label>
        <input id="rm-title" name="current_title" placeholder="e.g. Head of Internal Communications" required>
        <button type="submit">Run</button>
        <div class="status" id="rm-status"></div>
      </form>
    </div>

    <!-- PITCH PACK -->
    <div class="panel action-card">
      <h3>Pitch Pack</h3>
      <div class="subhead">Generate a tailored proposal to upgrade a client's job vacancy into an exclusive, retained search.</div>
      <form id="pitch-form" onsubmit="dispatch(event, 'pitch-form', '/api/dispatch/pitch-pack')">
        <label for="pp-account">Account name</label>
        <input id="pp-account" name="account_name" placeholder="e.g. Unilever" required>
        <label for="pp-role">Role</label>
        <input id="pp-role" name="role" placeholder="e.g. Head of Internal Communications" required>
        <button type="submit">Run</button>
        <div class="status" id="pitch-status"></div>
      </form>
    </div>

    <!-- PRE-MEETING BRIEF -->
    <div class="panel action-card">
      <h3>Pre-meeting Brief</h3>
      <div class="subhead">Walk into any client meeting with up-to-date prep.</div>
      <form id="pm-form" onsubmit="dispatch(event, 'pm-form', '/api/dispatch/pre-meeting')">
        <label for="pm-account">Account name</label>
        <input id="pm-account" name="account_name" placeholder="e.g. Severn Trent" required>
        <label for="pm-contact">Contact (optional)</label>
        <input id="pm-contact" name="contact_name" placeholder="e.g. Carla Sherry">
        <label for="pm-context">Meeting context (optional)</label>
        <input id="pm-context" name="meeting_context" placeholder="e.g. 10am Mon, Zoom">
        <button type="submit">Run</button>
        <div class="status" id="pm-status"></div>
      </form>
    </div>

    <!-- CANDIDATE WATCH -->
    <div class="panel action-card">
      <h3>Candidate Watch</h3>
      <div class="subhead">Keep a roster of warm candidates to stay in touch with. Overdue ones float to the top so relationships don't go cold.</div>
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
          <label for="wa-cadence">Remind me every (days)</label>
          <input id="wa-cadence" name="touch_cadence_days" type="number" value="30" min="7" max="180">
          <label for="wa-notes">Notes</label>
          <input id="wa-notes" name="notes">
          <button type="submit">Add</button>
          <div class="status" id="watch-add-status"></div>
        </form>
      </details>
    </div>

    <!-- MANUAL SWEEP -->
    <div class="panel action-card">
      <h3>Manual Sweep</h3>
      <div class="subhead">Sweep for potential missed leads or pre-market signals.</div>
      <form id="sweep-form" onsubmit="dispatch(event, 'sweep-form', '/api/dispatch/sweep')">
        <label for="sw-days">Window (days)</label>
        <input id="sw-days" name="window_days" type="number" min="1" max="60" placeholder="e.g. 14" required>
        <button type="submit">Run</button>
        <div class="status" id="sweep-status"></div>
      </form>
    </div>

    <!-- RECENT REPORTS (6th action-card slot — fits the 3x2 grid) -->
    <div class="panel action-card recent-card">
      <h3>Recent Reports</h3>
      <div class="subhead">Generated within the last 48 hours.</div>
      <button type="button" class="rr-clear" onclick="clearRecentReports(this)">Clear</button>
      <div id="recent-reports">
        <div class="empty compact">Loading…</div>
      </div>
    </div>

  </div>

  <div class="footer">
    <span class="brand-tag">Recruitment<span class="sep">•</span>Executive Search<span class="sep">•</span>Advisory Services</span>
    <span class="dev-zone">
      <span class="dev-zone-label">For dev only - not a user feature:</span>
      <button type="button" id="dev-run-brief" class="dev-btn"
              onclick="devTriggerBrief()"
              title="Maintenance: triggers a fresh morning-brief workflow run. Not for day-to-day use — Daily Refresh is the user control.">
        trigger fresh data
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
// appointments in today's news without Sara having to click Re-scan.
async function refreshBrief() {
  const btn = document.getElementById('refresh-btn');
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Refreshing…';
  try {
    const r = await fetch('/api/refresh', { method: 'POST' });
    const j = await r.json();
    if (j.ok && (j.leads > 0 || j.predictors > 0)) {
      // Brief landed — re-parse signals for cascade moves before
      // reloading so the Hire Watch panel reflects today's data.
      // Non-blocking: cascade failure must not stop the reload.
      btn.textContent = 'Scanning hires…';
      try {
        await fetch('/api/cascade/scour', { method: 'POST' });
      } catch (e) { /* non-fatal — log only */ }
      setTimeout(() => window.location.reload(), 400);
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
    const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const rows = pulseRows;

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

    const pipFor = p => {
      const cls = p.confidence === 'high' ? 'high' : 'med';
      return '<span class="cal-pip ' + cls + '"></span>';
    };
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
          '<span><i style="background:var(--teal)"></i>Regulatory deadline</span>' +
          '<span><i style="background:var(--green)"></i>Policy timeline</span>' +
        '</div>' +
      '</div></div>'  // .cal-detail .cal-wrap
    );
    body.innerHTML = out.join('');

    const cardFor = p => {
      const conf = (p.confidence === 'high') ? 'mandate-age' : 'hook-badge generic_fit';
      const confLabel = (p.confidence === 'high') ? 'Regulatory deadline' : 'Policy timeline';
      const far = (typeof p.days_left === 'number' && p.days_left > 150);
      const daysLabel = (typeof p.days_left === 'number') ? (p.days_left + 'd left') : '';
      const targets = (p.targets || []).map(t =>
        '<span class="hook-badge generic_fit" style="margin:2px 4px 2px 0;display:inline-block;">' +
        esc(t) + '</span>').join('');
      const rm = p.key
        ? '<button class="cal-rm" data-key="' + esc(p.key) + '" title="Remove this finding">✕ Remove</button>'
        : '';
      return (
        '<div class="cal-card-head">' +
          '<span class="' + conf + '">' + esc(confLabel) + '</span> ' +
          '<span class="cal-c-name">' + esc(p.name || '') + '</span>' +
          (daysLabel
            ? '<span class="cal-days' + (far ? ' far' : '') + '">' + esc(daysLabel) + '</span>'
            : '') +
          rm +
        '</div>' +
        '<div class="cal-seat">' + esc(p.seat || '') + '</div>' +
        '<div class="cal-angle">' + esc(p.angle || '') + '</div>' +
        (targets ? '<div style="margin-top:6px;">' + targets + '</div>' : '') +
        '<div class="cal-scope">' + esc(p.scope_note || '') +
          (p.source
            ? ' &middot; <a href="' + safeUrl(p.source) + '" target="_blank" rel="noopener noreferrer" style="color:#0366d6;">source</a>'
            : '') +
        '</div>' +
        (p.advisory ? '<div class="advisory-line">' + esc(p.advisory) + '</div>' : '')
      );
    };
    const CAL_PH = '<span class="cal-ph">Click a month with pips for the lead detail.</span>';
    const openMonth = m => {
      const ps = buckets[m] || [];
      if (!ps.length) return;
      const tile = body.querySelector('.cal-tile[data-m="' + m + '"]');
      const alreadyOpen = tile && tile.classList.contains('sel');
      body.querySelectorAll('.cal-tile').forEach(t => t.classList.remove('sel'));
      if (alreadyOpen) {
        // Second click on the open month → collapse back to placeholder.
        document.getElementById('cal-card').innerHTML = CAL_PH;
        return;
      }
      if (tile) tile.classList.add('sel');
      document.getElementById('cal-card').innerHTML =
        ps.map(cardFor).join('<hr class="cal-dsep">');
    };
    body.querySelectorAll('.cal-tile.has').forEach(t =>
      t.addEventListener('click', () => openMonth(parseInt(t.dataset.m, 10))));

    // Remove-a-finding: delegated click on the cal-card. Persists the
    // dismissal then re-renders the whole calendar so pip counts and
    // month buckets stay correct.
    const calCard = document.getElementById('cal-card');
    if (calCard) {
      calCard.addEventListener('click', async (ev) => {
        const rmBtn = ev.target.closest('.cal-rm');
        if (!rmBtn) return;
        const key = rmBtn.getAttribute('data-key');
        if (!key) return;
        rmBtn.disabled = true;
        try {
          const r = await fetch('/api/pulses/dismiss', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: key, dismissed: true }),
          });
          const j = await r.json();
          if (j.ok) {
            loadPulses();   // re-render with the finding removed
          } else {
            rmBtn.disabled = false;
            alert(j.detail || 'Could not remove.');
          }
        } catch (e) {
          rmBtn.disabled = false;
          alert('Network error: ' + e.message);
        }
      });
    }

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
    const out = ['<ul class="ev-list">'];
    rows.forEach(e => {
      const win = e.in_action_window
        ? '<span class="ev-open" title="Outreach window open now">window open</span>' : '';
      const rm = e.key
        ? '<button class="ev-rm" data-key="' + esc(e.key) + '" title="Remove this event">&#10005;</button>' : '';
      out.push(
        '<li class="ev-item">' +
          '<div class="ev-top">' +
            focusChip(e.focus) +
            '<span class="ev-name">' + esc(e.name || '') + '</span>' +
            (win ? ' ' + win : '') +
            '<span class="ev-when">' + esc(whenChip(e.days_to_event)) + '</span>' +
            rm +
          '</div>' +
          '<div class="ev-meta">' + esc(fmtDate(e.event_date)) +
            (e.location ? ' &middot; ' + esc(e.location) : '') + '</div>' +
          (e.why_now ? '<div class="ev-why">' + esc(e.why_now) + '</div>' : '') +
          (e.source ? '<div class="ev-src"><a href="' + safeUrl(e.source) +
             '" target="_blank" rel="noopener noreferrer">source &rsaquo;</a></div>' : '') +
        '</li>'
      );
    });
    out.push('</ul>');
    body.innerHTML = out.join('');
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
    if (btn) { btn.disabled = true; btn.textContent = 'Loading today’s brief…'; }
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
      btn.disabled = false; btn.textContent = '↻ Daily Refresh';
    }
  } catch (e) {
    const btn = document.getElementById('refresh-btn');
    if (btn) { btn.disabled = false; btn.textContent = '↻ Daily Refresh'; }
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
  // Detail = company / candidate name when we have it, else the
  // report's own type label as the primary line. Either way we render
  // at weight 500 so it doesn't compete with the h3.
  const inlineDetail = (x) => {
    const parts = [];
    if (x.company && x.company !== '—') parts.push(x.company);
    if (x.name) parts.push(x.name);
    return parts.join(' · ');
  };
  try {
    const r = await fetch('/api/output/recent');
    const j = await r.json();
    if (!j.rows || !j.rows.length) {
      body.innerHTML = '';
      return;
    }
    const now = Date.now();
    const out = [];
    const rows = j.rows.slice(0, 30);
    for (let i = 0; i < rows.length; i++) {
      const x = rows[i];
      const t = new Date(x.ts).getTime();
      const mins = Math.max(0, Math.round((now - t) / 60000));
      const ago = mins < 1 ? 'now'
                : mins < 60 ? mins + 'm'
                : mins < 1440 ? Math.round(mins / 60) + 'h'
                : Math.round(mins / 1440) + 'd';
      const detail = inlineDetail(x);
      // No <strong> wrap — CSS .rr2-name gives the right weight.
      const namePart = detail
        ? '<span class="rr2-primary">' + esc(detail) + '</span> · <span class="rr2-type">' + esc(x.type) + '</span>'
        : '<span class="rr2-primary">' + esc(x.type) + '</span>';
      let action;
      if (x.id) {
        const dl = '/api/output/view?artifact=' +
          encodeURIComponent(x.artifact) + '&id=' + encodeURIComponent(x.id) + '&download=1';
        action = '<a class="rr2-icon" href="' + dl + '" title="Download">⬇</a>';
      } else {
        action = '<span class="rr2-gen">generating…</span>';
      }
      out.push(
        '<div class="rr2">' +
          '<span class="rr2-num">' + (i + 1) + '</span>' +
          '<span class="rr2-name">' + namePart + '</span>' +
          '<span class="rr2-age">' + esc(ago) + '</span>' +
          action +
        '</div>'
      );
    }
    body.innerHTML = out.join('');
  } catch (e) {
    body.innerHTML = '<div class="empty compact">Could not load recent reports.</div>';
  }
}
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
