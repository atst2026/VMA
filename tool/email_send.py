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
import base64
import json
import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from tool.config import RESEND_API_KEY, RESEND_FROM

log = logging.getLogger("brief.email")


def send(to: str, subject: str, html: str, text: str | None = None,
         bcc: list[str] | None = None,
         attachments: list[tuple[str, bytes, str]] | None = None,
         from_name: str | None = None) -> dict:
    """Return {ok: bool, detail: ..., provider: 'resend'|'gmail'|'none'}.

    `bcc` is an optional list of additional addresses to silently copy.
    Used in send mode so Amir's hotmail confirms whether the email left
    the system — when Sara's spam filter eats a pack we can tell the
    difference between "didn't send" and "sent but trapped".

    `attachments` is an optional list of (filename, raw_bytes, mimetype)
    tuples — used to attach the comms pitch-pack PDF. Both providers
    support it; omit it (the default) for a plain HTML/text email.

    `from_name` overrides the display name for THIS message only (the
    outreach sender must not arrive as "Sara's Morning Brief"); omitted,
    both providers keep their existing identity behaviour."""
    if RESEND_API_KEY:
        return _send_resend(to, subject, html, text, bcc=bcc, attachments=attachments)
    if os.environ.get("GMAIL_APP_PASSWORD") and os.environ.get("GMAIL_USER"):
        return _send_gmail(to, subject, html, text, bcc=bcc,
                           attachments=attachments, from_name=from_name)
    return {
        "ok": False,
        "provider": "none",
        "detail": (
            "No email provider configured. Set RESEND_API_KEY (resend.com) OR "
            "GMAIL_USER + GMAIL_APP_PASSWORD. See tool/email_send.py header."
        ),
    }


def _send_resend(to: str, subject: str, html: str, text: str | None,
                 bcc: list[str] | None = None,
                 attachments: list[tuple[str, bytes, str]] | None = None) -> dict:
    log.info("Resend: from=%r to=%r bcc=%r subject=%r attachments=%d",
             RESEND_FROM, to, bcc, subject, len(attachments or []))
    payload = {
        "from": RESEND_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if bcc:
        payload["bcc"] = list(bcc)
    if text:
        payload["text"] = text
    if attachments:
        payload["attachments"] = [
            {"filename": fn,
             "content": base64.b64encode(raw).decode("ascii"),
             "content_type": mime}
            for (fn, raw, mime) in attachments
        ]
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


def _send_gmail(to: str, subject: str, html: str, text: str | None,
                bcc: list[str] | None = None,
                attachments: list[tuple[str, bytes, str]] | None = None,
                from_name: str | None = None) -> dict:
    gmail_user = os.environ["GMAIL_USER"].strip()
    gmail_pw = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()
    from tool.profiles import active_profile
    _default_name = ("Marketing Brief" if active_profile().key == "marketing"
                     else "Sara's Morning Brief")
    display_name = from_name or os.environ.get("GMAIL_FROM_NAME", _default_name)
    from_header = f"{display_name} <{gmail_user}>"

    # With attachments the message must be multipart/mixed, with the text/html
    # bodies grouped in an alternative subpart; without, a plain alternative
    # message (unchanged from before).
    body = MIMEMultipart("alternative")
    if text:
        body.attach(MIMEText(text, "plain"))
    body.attach(MIMEText(html, "html"))

    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(body)
        for (fn, raw, mime) in attachments:
            maintype, _, subtype = mime.partition("/")
            part = MIMEApplication(raw, _subtype=subtype or "octet-stream")
            part.add_header("Content-Disposition", "attachment", filename=fn)
            msg.attach(part)
    else:
        msg = body

    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to
    # Bcc is intentionally NOT added as a header (so the To recipient doesn't
    # see the bcc list), but the address IS included in the sendmail
    # destination list per RFC 5321 / smtplib.
    recipients = [to] + list(bcc or [])

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(gmail_user, gmail_pw)
            s.sendmail(gmail_user, recipients, msg.as_string())
        return {"ok": True, "provider": "gmail",
                "detail": f"sent {gmail_user} → {to} (bcc={bcc or []})"}
    except smtplib.SMTPAuthenticationError as e:
        return {"ok": False, "provider": "gmail",
                "detail": f"SMTPAuthenticationError: {e}. Make sure you used an "
                          f"app password, not your Gmail login password."}
    except Exception as e:
        return {"ok": False, "provider": "gmail", "detail": str(e)}
