import os
import smtplib
from email.mime.text import MIMEText
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx

AFFILIATE_TAG_PARAM = os.environ.get("AFFILIATE_TAG_PARAM")  # e.g. "tag"
AFFILIATE_TAG_VALUE = os.environ.get("AFFILIATE_TAG_VALUE")  # e.g. "watchpage-20"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "alerts@watchpage.local")

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")

# Preference order: Resend (simplest, resend.com, 3000 free emails/mo) -> SMTP -> dev log file
DEV_MODE = not (RESEND_API_KEY or (SMTP_HOST and SMTP_USER and SMTP_PASS))
DEV_LOG_PATH = "dev_emails.log"


def with_affiliate_tag(url: str) -> str:
    """Append our affiliate tag to an outgoing product link, if configured.
    No-op until real affiliate program credentials are set via env vars."""
    if not (AFFILIATE_TAG_PARAM and AFFILIATE_TAG_VALUE):
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.setdefault(AFFILIATE_TAG_PARAM, AFFILIATE_TAG_VALUE)
    return urlunsplit(parts._replace(query=urlencode(query)))


def _send_via_resend(to_email: str, subject: str, body: str):
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from": FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "text": body,
        },
        timeout=10,
    )
    resp.raise_for_status()


def _send_via_smtp(to_email: str, subject: str, body: str):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, [to_email], msg.as_string())


def send_alert(to_email: str, subject: str, body: str):
    if RESEND_API_KEY:
        _send_via_resend(to_email, subject, body)
        return
    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        _send_via_smtp(to_email, subject, body)
        return

    with open(DEV_LOG_PATH, "a") as f:
        f.write(f"\n--- EMAIL (dev mode, not actually sent) ---\nTo: {to_email}\nSubject: {subject}\n{body}\n")
    print(f"[dev email] to={to_email} subject={subject}")
