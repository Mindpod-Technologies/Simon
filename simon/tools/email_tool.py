"""Optional email tools: read recent mail via IMAP, send via SMTP (SSL).

Only registered when the corresponding settings are configured.
"""
from __future__ import annotations

import imaplib
import logging
import smtplib
from email.header import decode_header
from email.message import EmailMessage

from .base import Tool

log = logging.getLogger(__name__)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def read_recent_emails(settings, count: int = 5) -> str:
    """Fetch the last `count` emails (subject/from/date) via IMAP."""
    with imaplib.IMAP4_SSL(settings.imap_host) as conn:
        conn.login(settings.imap_user, settings.imap_password)
        conn.select("INBOX", readonly=True)
        status, data = conn.search(None, "ALL")
        if status != "OK":
            return "Error: could not search inbox"
        ids = data[0].split()[-count:]
        lines = []
        for msg_id in reversed(ids):
            status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
            if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            header = msg_data[0][1].decode("utf-8", errors="replace")
            fields = {"subject": "", "from": "", "date": ""}
            current = None
            for line in header.splitlines():
                if line[:1] in (" ", "\t") and current:
                    fields[current] += " " + line.strip()
                    continue
                if ":" in line:
                    key, _, val = line.partition(":")
                    current = key.strip().lower()
                    if current in fields:
                        fields[current] = val.strip()
            lines.append(
                f"- Subject: {_decode(fields['subject'])}\n"
                f"  From: {_decode(fields['from'])}\n"
                f"  Date: {_decode(fields['date'])}"
            )
        return "\n".join(lines) if lines else "(inbox is empty)"


def send_email(settings, to: str, subject: str, body: str) -> str:
    """Send an email via SMTP over SSL."""
    msg = EmailMessage()
    msg["From"] = settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(settings.smtp_host) as conn:
        conn.login(settings.smtp_user, settings.smtp_password)
        conn.send_message(msg)
    return f"Email sent to {to} with subject '{subject}'."


def register_email_tools(registry, settings) -> None:
    if getattr(settings, "imap_host", ""):
        registry.register(Tool(
            name="read_recent_emails",
            description="Read the most recent emails from the inbox (subject, from, date).",
            parameters={
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of recent emails (default 5)", "default": 5}
                },
                "required": [],
            },
            func=lambda count=5: read_recent_emails(settings, count),
        ))
    if getattr(settings, "smtp_host", ""):
        registry.register(Tool(
            name="send_email",
            description="Send an email via SMTP.",
            parameters={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject"},
                    "body": {"type": "string", "description": "Plain-text email body"},
                },
                "required": ["to", "subject", "body"],
            },
            func=lambda to, subject, body: send_email(settings, to, subject, body),
        ))
