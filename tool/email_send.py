"""Email delivery.

Primary path: Resend (free 100/day, no domain required for `@resend.dev` sender).
Fallback: write the brief to disk and print the path — honest failure mode.

Env required for send:
  RESEND_API_KEY   — get free from resend.com (2-min signup)
  RESEND_FROM      — optional; defaults to onboarding@resend.dev
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path

import requests

from tool.config import RESEND_API_KEY, RESEND_FROM

log = logging.getLogger("brief.email")


def send(to: str, subject: str, html: str, text: str | None = None) -> dict:
    """Send via Resend. Returns {ok: bool, detail: ...}."""
    if not RESEND_API_KEY:
        return {"ok": False, "detail": "RESEND_API_KEY not set — see tool/README.md step 1"}
    payload = {
        "from": RESEND_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=30,
        )
        ok = r.status_code in (200, 202)
        return {"ok": ok, "status": r.status_code, "body": r.text[:500]}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
