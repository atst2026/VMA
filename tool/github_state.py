"""Durable state across Render redeploys.

Render's filesystem is ephemeral: anything the dashboard writes at
runtime (lead triage status, the Candidate Watch roster) is lost on
the next deploy/cold-start. The morning-brief workflow already treats
the GitHub repo as a durable store (it auto-commits hiring_contacts.json);
this module does the same for the dashboard-written state files, via
the GitHub Contents API using the credentials the service already has.

Model:
  - hydrate()  — on boot, pull the latest copy from the repo into the
                 local file so a fresh container starts from real state.
  - push()     — after every mutation, write the file back to the repo
                 so the NEXT boot has it.

Everything is best-effort: no token / network failure / GitHub down
never raises and never blocks the dashboard — the local file stays the
source of truth for the life of the container.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger("brief.github_state")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "atst2026")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "VMA")
# Default to a dedicated branch Render does NOT auto-deploy from, so
# persisting state never triggers a redeploy. Override via env if needed.
BRANCH = os.environ.get("GITHUB_STATE_BRANCH", "dashboard-state")

# Repo root = two levels up from this file (tool/github_state.py).
REPO_ROOT = Path(__file__).resolve().parent.parent


def _ns(repo_path: str) -> str:
    """Namespace a repo state path by the active profile, mirroring
    state_paths.state_root(): comms/default keeps ``tool/state/…``; every
    other profile gets ``tool/state/<key>/…``. So both the remote
    dashboard-state branch and the local hydrate path stay isolated per
    desk, with zero change for comms."""
    from tool.profiles import DEFAULT_PROFILE_KEY, active_profile
    key = active_profile().key
    prefix = "tool/state/"
    if key != DEFAULT_PROFILE_KEY and repo_path.startswith(prefix):
        return prefix + key + "/" + repo_path[len(prefix):]
    return repo_path


def _enabled() -> bool:
    return bool(GITHUB_TOKEN)


def _headers() -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _contents_url(repo_path: str) -> str:
    return (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
            f"/contents/{_ns(repo_path)}")


def _get_remote(repo_path: str) -> tuple[str | None, str | None]:
    """Return (text, sha) for repo_path on BRANCH, or (None, None)."""
    try:
        r = requests.get(_contents_url(repo_path), headers=_headers(),
                          params={"ref": BRANCH}, timeout=12)
        if r.status_code == 404:
            return None, None
        if r.status_code != 200:
            log.info("github_state GET %s -> HTTP %s", repo_path, r.status_code)
            return None, None
        j = r.json()
        raw = base64.b64decode(j.get("content", "") or "").decode("utf-8")
        return raw, j.get("sha")
    except Exception as e:
        log.info("github_state GET %s failed: %s", repo_path, e)
        return None, None


def hydrate(repo_paths: list[str]) -> None:
    """Pull each path from the repo into its local file, so a fresh
    container starts from the durably-stored state rather than the
    (stale or absent) build-time copy. Silent no-op without a token."""
    if not _enabled():
        log.info("github_state: no token — skipping hydrate (local files as-is)")
        return
    for repo_path in repo_paths:
        text, _sha = _get_remote(repo_path)
        if text is None:
            continue
        try:
            json.loads(text)  # never overwrite a good local file with junk
        except Exception:
            log.info("github_state: remote %s not valid JSON — skip", repo_path)
            continue
        local = REPO_ROOT / _ns(repo_path)
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(text, encoding="utf-8")
            log.info("github_state: hydrated %s from repo", repo_path)
        except Exception as e:
            log.info("github_state: could not write local %s: %s", repo_path, e)


def push(repo_path: str, text: str, message: str) -> bool:
    """Commit `text` to repo_path on BRANCH. Best-effort; returns False
    (never raises) when disabled or on any error."""
    if not _enabled():
        return False
    _remote, sha = _get_remote(repo_path)
    body = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    try:
        r = requests.put(_contents_url(repo_path), headers=_headers(),
                          json=body, timeout=15)
        if r.status_code in (200, 201):
            return True
        log.info("github_state PUT %s -> HTTP %s %s",
                 repo_path, r.status_code, r.text[:160])
        return False
    except Exception as e:
        log.info("github_state PUT %s failed: %s", repo_path, e)
        return False


def push_async(repo_path: str, text: str, message: str) -> None:
    """Fire-and-forget push on a daemon thread. Keeps the durable-state
    write completely off the request path: adding/dismissing is an
    instant local operation; GitHub persistence happens in the
    background and can never block, slow, or break the UI."""
    if not _enabled():
        return
    import threading
    threading.Thread(
        target=push, args=(repo_path, text, message), daemon=True,
    ).start()
