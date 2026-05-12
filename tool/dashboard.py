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
        return json.loads(p.read_text())
    except Exception:
        return []


def load_latest_predictive() -> list[dict]:
    p = STATE_DIR / "latest_predictive.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


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
  <style>
    :root {
      --navy: #0f1f3a;
      --navy-soft: #1a2c4e;
      --coral: #e94e3d;
      --coral-dark: #c93b2b;
      --bg: #f4f6f8;
      --card: #ffffff;
      --border: #e1e5eb;
      --text: #1a1a1a;
      --muted: #6b7280;
      --success: #2da46d;
      --shadow: 0 1px 3px rgba(15, 31, 58, 0.05), 0 1px 2px rgba(15, 31, 58, 0.03);
      --shadow-hover: 0 6px 16px rgba(15, 31, 58, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 0;
      line-height: 1.5;
    }
    .header {
      background: var(--navy);
      color: white;
      padding: 20px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .header .brand {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .header .logo {
      width: 44px; height: 44px;
      background: var(--coral);
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 18px;
      letter-spacing: -0.5px;
    }
    .header h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 600;
      letter-spacing: -0.2px;
    }
    .header .sub {
      font-size: 13px;
      opacity: 0.7;
      margin-top: 2px;
    }
    .header .meta {
      font-size: 12px;
      opacity: 0.7;
      text-align: right;
    }
    .header .meta button {
      background: rgba(255,255,255,0.1);
      color: white;
      border: 1px solid rgba(255,255,255,0.2);
      padding: 6px 14px;
      border-radius: 6px;
      font-size: 12px;
      cursor: pointer;
      margin-left: 8px;
    }
    .header .meta button:hover { background: rgba(255,255,255,0.18); }

    .container {
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px;
    }

    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
      margin-bottom: 32px;
    }
    @media (max-width: 900px) {
      .row { grid-template-columns: 1fr; }
    }

    .panel {
      background: var(--card);
      border-radius: 12px;
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .panel-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .panel-header h2 {
      margin: 0;
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--navy);
    }
    .panel-header .count {
      background: var(--bg);
      color: var(--muted);
      font-size: 11px;
      font-weight: 600;
      padding: 4px 10px;
      border-radius: 20px;
    }
    .panel-body { padding: 8px 0; }

    .item {
      padding: 14px 20px;
      border-bottom: 1px solid var(--border);
    }
    .item:last-child { border-bottom: 0; }
    .item .rank {
      display: inline-block;
      width: 22px; height: 22px;
      background: var(--navy);
      color: white;
      border-radius: 50%;
      font-size: 12px;
      font-weight: 700;
      text-align: center;
      line-height: 22px;
      margin-right: 10px;
      vertical-align: middle;
    }
    .item .title {
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      margin-right: 4px;
    }
    .item .title a { color: var(--text); text-decoration: none; }
    .item .title a:hover { color: var(--coral); }
    .item .meta {
      font-size: 12px;
      color: var(--muted);
      margin-top: 4px;
      margin-left: 32px;
    }
    .item .meta .badge {
      background: var(--bg);
      padding: 2px 8px;
      border-radius: 4px;
      font-weight: 500;
      color: var(--navy);
      margin-right: 6px;
    }
    .item .meta a {
      color: var(--coral);
      text-decoration: none;
      font-weight: 500;
    }
    .item .meta a:hover { text-decoration: underline; }

    .predictor .stack-label {
      display: inline-block;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 4px;
      margin-left: 8px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .stack-label.stacked { background: var(--coral); color: white; }
    .stack-label.single { background: var(--bg); color: var(--navy); }
    .predictor .evidence {
      font-size: 12px;
      color: var(--muted);
      margin-top: 6px;
      margin-left: 32px;
    }

    .show-more {
      width: 100%;
      padding: 12px;
      background: var(--bg);
      border: none;
      border-top: 1px solid var(--border);
      cursor: pointer;
      font-size: 13px;
      font-weight: 600;
      color: var(--navy);
    }
    .show-more:hover { background: #ebeff4; }

    .empty {
      padding: 32px 20px;
      text-align: center;
      color: var(--muted);
      font-size: 13px;
    }

    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 24px;
    }
    @media (max-width: 1000px) { .actions { grid-template-columns: 1fr; } }

    .action-card { padding: 22px; }
    .action-card h3 {
      margin: 0 0 4px 0;
      font-size: 16px;
      font-weight: 700;
      color: var(--navy);
    }
    .action-card .subhead {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 16px;
    }
    .action-card label {
      display: block;
      font-size: 12px;
      font-weight: 600;
      color: var(--navy);
      margin: 12px 0 4px 0;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .action-card input, .action-card select {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 14px;
      font-family: inherit;
      background: white;
      color: var(--text);
    }
    .action-card input:focus, .action-card select:focus {
      outline: none;
      border-color: var(--navy);
      box-shadow: 0 0 0 3px rgba(15, 31, 58, 0.08);
    }
    .action-card button {
      width: 100%;
      margin-top: 18px;
      padding: 12px 16px;
      background: var(--coral);
      color: white;
      border: none;
      border-radius: 6px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s;
    }
    .action-card button:hover { background: var(--coral-dark); }
    .action-card button:disabled {
      background: #d1d5db;
      cursor: not-allowed;
    }
    .action-card .status {
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 6px;
      font-size: 13px;
      display: none;
    }
    .action-card .status.ok { background: #e8f7ef; color: var(--success); display: block; }
    .action-card .status.err { background: #fce8e6; color: var(--coral-dark); display: block; }

    .footer {
      text-align: center;
      color: var(--muted);
      font-size: 12px;
      padding: 24px;
      margin-top: 16px;
    }

    .warn-banner {
      background: #fff3cd;
      color: #664d03;
      padding: 12px 20px;
      border-left: 4px solid #ffc107;
      margin: 0 32px 16px 32px;
      border-radius: 4px;
      font-size: 13px;
    }
  </style>
</head>
<body>

<div class="header">
  <div class="brand">
    <div class="logo">VMA</div>
    <div>
      <h1>Sara's Desk</h1>
      <div class="sub">Lead detection · pre-advert predictors · on-demand commercial tools</div>
    </div>
  </div>
  <div class="meta">
    <div>Last brief: {{ last_updated }}</div>
    <button onclick="refreshBrief()" id="refresh-btn">↻ Refresh from GitHub</button>
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
      <div class="subhead">Flip a contingent brief to retained. ~£14–21k extra per placement.</div>
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
      <div class="subhead">Sweep the last fortnight for anything the daily brief missed.</div>
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
