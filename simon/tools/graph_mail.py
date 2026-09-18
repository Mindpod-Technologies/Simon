"""Microsoft Graph mail tools — Simon's mailbox on Microsoft 365.

Why this exists: the mindpodtech.com tenant has basic auth disabled for IMAP
and SMTP (Microsoft 365 default), so a mailbox password alone cannot be used.
The supported path is Microsoft Graph with an Entra ID app registration
(client-credentials daemon flow, scoped to Simon's mailbox).

Setup (one time, tenant admin):
  1. Entra ID → App registrations → New registration "Simon Mail".
  2. API permissions → Microsoft Graph → Application permissions:
     Mail.Read, Mail.Send → Grant admin consent.
  3. Certificates & secrets → New client secret.
  4. Put GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET and
     SIMON_MAILBOX in .env.
  Recommended: an application access policy limiting the app to Simon's
  mailbox only (Limiting application permissions to specific Exchange Online
  mailboxes).
"""
from __future__ import annotations

import logging
import time

import requests

from .base import Tool

log = logging.getLogger(__name__)

_SCOPE = "https://graph.microsoft.com/.default"
_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_EARLY_S = 120  # refresh this many seconds before expiry

_token_cache: dict = {"value": None, "exp": 0.0}


def _token(s) -> str:
    now = time.time()
    if _token_cache["value"] and now < _token_cache["exp"] - _TOKEN_EARLY_S:
        return _token_cache["value"]
    resp = requests.post(
        f"https://login.microsoftonline.com/{s.graph_tenant_id}"
        "/oauth2/v2.0/token",
        data={
            "client_id": s.graph_client_id,
            "client_secret": s.graph_client_secret,
            "grant_type": "client_credentials",
            "scope": _SCOPE,
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    _token_cache["value"] = body["access_token"]
    _token_cache["exp"] = now + int(body.get("expires_in", 3600))
    return _token_cache["value"]


def _headers(s) -> dict:
    return {"Authorization": f"Bearer {_token(s)}",
            "Content-Type": "application/json"}


def read_recent_emails_graph(s, count: int = 10) -> str:
    """List recent inbox messages: subject, from, date, snippet, id."""
    count = max(1, min(int(count or 10), 25))
    mb = s.simon_mailbox
    resp = requests.get(
        f"{_BASE}/users/{mb}/mailFolders/Inbox/messages",
        headers=_headers(s),
        params={
            "$top": str(count),
            "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead",
            "$orderby": "receivedDateTime desc",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        return f"graph mail: inbox read failed ({resp.status_code}): {resp.text[:300]}"
    msgs = resp.json().get("value", [])
    if not msgs:
        return "Inbox is empty."
    lines = []
    for i, m in enumerate(msgs, 1):
        frm = (m.get("from") or {}).get("emailAddress", {})
        unread = "" if m.get("isRead") else " [UNREAD]"
        lines.append(
            f"{i}. {m.get('subject') or '(no subject)'}{unread}\n"
            f"   from: {frm.get('name') or ''} <{frm.get('address') or ''}>\n"
            f"   date: {m.get('receivedDateTime', '')}\n"
            f"   {m.get('bodyPreview', '')[:160]}\n"
            f"   id: {m.get('id', '')}"
        )
    return "\n\n".join(lines)


def read_email_graph(s, message_id: str) -> str:
    """Fetch one full message body (plain text) by id."""
    if not (message_id or "").strip():
        return "graph mail: message_id required."
    resp = requests.get(
        f"{_BASE}/users/{s.simon_mailbox}/messages/{message_id.strip()}",
        headers=_headers(s),
        params={"$select": "subject,from,receivedDateTime,body,toRecipients"},
        timeout=30,
    )
    if resp.status_code != 200:
        return f"graph mail: read failed ({resp.status_code}): {resp.text[:300]}"
    m = resp.json()
    frm = (m.get("from") or {}).get("emailAddress", {})
    body = (m.get("body") or {})
    text = body.get("content", "")
    if body.get("contentType") == "html":
        import re
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    return (f"Subject: {m.get('subject') or '(no subject)'}\n"
            f"From: {frm.get('name') or ''} <{frm.get('address') or ''}>\n"
            f"Date: {m.get('receivedDateTime', '')}\n\n{text[:4000]}")


def send_email_graph(s, to: str, subject: str, body: str) -> str:
    """Send an email from Simon's mailbox via Graph."""
    to = (to or "").strip()
    if not to or not (subject or "").strip():
        return "graph mail: 'to' and 'subject' are required."
    payload = {
        "message": {
            "subject": subject.strip(),
            "body": {"contentType": "Text", "content": body or ""},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": True,
    }
    resp = requests.post(
        f"{_BASE}/users/{s.simon_mailbox}/sendMail",
        headers=_headers(s), json=payload, timeout=30,
    )
    if resp.status_code in (200, 202):
        log.info("graph mail: sent to=%s subject=%.60s", to, subject)
        return f"Email sent to {to}: {subject.strip()}"
    return f"graph mail: send failed ({resp.status_code}): {resp.text[:300]}"


def register_graph_mail_tools(registry, settings) -> None:
    """Register Graph mail tools when GRAPH_* credentials are configured."""
    if not (getattr(settings, "graph_tenant_id", "")
            and getattr(settings, "graph_client_id", "")
            and getattr(settings, "graph_client_secret", "")
            and getattr(settings, "simon_mailbox", "")):
        return
    registry.register(Tool(
        name="read_recent_emails",
        description="Read the most recent emails from Simon's Microsoft 365 "
                    "inbox (subject, from, date, preview, message id).",
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 10,
                          "description": "How many messages (1-25)."},
            },
        },
        func=lambda count=10, **_kw: read_recent_emails_graph(settings, count),
    ))
    registry.register(Tool(
        name="read_email",
        description="Read one full email by its message id (from "
                    "read_recent_emails).",
        parameters={
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
            },
            "required": ["message_id"],
        },
        func=lambda message_id="", **kw: read_email_graph(
            settings, message_id or kw.get("id", "") or kw.get("address", "")),
    ))
    registry.register(Tool(
        name="send_email",
        description="Send an email from Simon's Microsoft 365 mailbox.",
        parameters={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient address."},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
        func=lambda to="", subject="", body="", **kw: send_email_graph(
            settings, to or kw.get("recipient", "") or kw.get("address", ""),
            subject, body),
    ))
    log.info("graph mail tools registered (mailbox: %s)",
             settings.simon_mailbox)
