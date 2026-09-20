"""
DODO — skills/email_sender.py
Send emails via Gmail compose URL — no password or OAuth needed.
Opens Gmail in Chrome with To/Subject/Body pre-filled. User just clicks Send.
Optional: add Gmail SMTP credentials to config.json for fully automatic sending.
"""

import urllib.parse
import subprocess
import webbrowser
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from core.memory import load_config

logger = logging.getLogger("dodo.email")


def _get_smtp_credentials() -> tuple[str, str] | tuple[None, None]:
    """Load Gmail SMTP credentials from config.json (optional)."""
    cfg = load_config()
    addr = cfg.get("gmail_address", "")
    pwd  = cfg.get("gmail_app_password", "")
    return (addr, pwd) if addr and pwd else (None, None)


def send_email(to: str, subject: str, body: str) -> str:
    """
    Send an email. Two modes:
    1. If SMTP credentials in config.json → send automatically (silent)
    2. Otherwise → open Gmail compose URL in Chrome (user clicks Send)
    """
    # --- Mode 1: SMTP (fully automatic, needs App Password in config) ---
    sender, password = _get_smtp_credentials()
    if sender and password:
        try:
            msg = MIMEMultipart("alternative")
            msg["From"]    = sender
            msg["To"]      = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
                server.login(sender, password)
                server.sendmail(sender, to, msg.as_string())
            logger.info("Email sent via SMTP to %s", to)
            return f"✅ Email sent to {to}. Subject: '{subject}'"
        except Exception as e:
            logger.warning("SMTP failed, falling back to browser: %s", e)

    # --- Mode 2: Gmail compose URL (no password needed) ---
    params = urllib.parse.urlencode({
        "fs":   "1",         # fs=1 forces the compose window to open (critical!)
        "to":   to,
        "su":   subject,
        "body": body,
    })
    gmail_url = f"https://mail.google.com/mail/u/0/?view=cm&{params}"

    try:
        webbrowser.open(gmail_url)
        logger.info("Gmail compose opened for %s", to)
        return (
            f"📧 Gmail opened with the email pre-filled.\n"
            f"To: {to}\nSubject: {subject}\n"
            f"Just click Send in Chrome!"
        )
    except Exception as e:
        return f"Couldn't open Gmail: {str(e)[:100]}"


def is_configured() -> bool:
    sender, password = _get_smtp_credentials()
    return bool(sender and password)
