"""Email delivery.

Dispatches based on which env vars are configured:

1. Resend (recommended when you've verified a domain on resend.com).
   Env:  RESEND_API_KEY  (required)
         RESEND_FROM     (optional; default onboarding@resend.dev — but that
                          only sends to your Resend account email unless you
                          verify your own domain)

2. Gmail SMTP (works anywhere, including Hotmail/Outlook/any inbox).
   Env:  GMAIL_USER            (full gmail address)
         GMAIL_APP_PASSWORD    (16-char app password; NOT your login password)
         GMAIL_FROM_NAME       (optional display name)
   Set up:
     https://myaccount.google.com/security → enable 2-Step Verification
     https://myaccount.google.com/apppasswords → create an app password
     Paste the 16-char password (spaces stripped) into GMAIL_APP_PASSWORD.

If both are set, Resend wins. If neither is set, we write a clear error.
"""
from __future__ import annotations
import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from tool.config import RESEND_API_KEY, RESEND_FROM

log = logging.getLogger("brief.email")


def send(to: str, subject: str, html: str, text: str | None = None) -> dict:
    """Return {ok: bool, detail: ..., provider: 'resend'|'gmail'|'none'}."""
    if RESEND_API_KEY:
        return _send_resend(to, subject, html, text)
    if os.environ.get("GMAIL_APP_PASSWORD") and os.environ.get("GMAIL_USER"):
        return _send_gmail(to, subject, html, text)
    return {
        "ok": False,
        "provider": "none",
        "detail": (
            "No email provider configured. Set RESEND_API_KEY (resend.com) OR "
            "GMAIL_USER + GMAIL_APP_PASSWORD. See tool/email_send.py header."
        ),
    }


def _send_resend(to: str, subject: str, html: str, text: str | None) -> dict:
    log.info("Resend: from=%r to=%r subject=%r", RESEND_FROM, to, subject)
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
        return {"ok": ok, "provider": "resend", "status": r.status_code,
                "body": r.text[:500]}
    except Exception as e:
        return {"ok": False, "provider": "resend", "detail": str(e)}


def _send_gmail(to: str, subject: str, html: str, text: str | None) -> dict:
    gmail_user = os.environ["GMAIL_USER"].strip()
    gmail_pw = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()
    display_name = os.environ.get("GMAIL_FROM_NAME", "Sara's Morning Brief")
    from_header = f"{display_name} <{gmail_user}>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to

    if text:
        msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(gmail_user, gmail_pw)
            s.sendmail(gmail_user, [to], msg.as_string())
        return {"ok": True, "provider": "gmail",
                "detail": f"sent {gmail_user} → {to}"}
    except smtplib.SMTPAuthenticationError as e:
        return {"ok": False, "provider": "gmail",
                "detail": f"SMTPAuthenticationError: {e}. Make sure you used an "
                          f"app password, not your Gmail login password."}
    except Exception as e:
        return {"ok": False, "provider": "gmail", "detail": str(e)}
