"""Stripe webhook server + post-purchase success page (port 8792).

No Stripe SDK dependency — webhook signatures are plain HMAC-SHA256, and
Payment Links need no API calls at all. Configure in the Stripe dashboard:

  Webhook endpoint:  https://<public-host>/stripe/webhook
  Events:            checkout.session.completed
  Payment Link success URL:
                     https://<public-host>/success?session_id={CHECKOUT_SESSION_ID}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import keys, store

log = logging.getLogger(__name__)

SIGNATURE_TOLERANCE_S = 300

EMAIL_SUBJECT = "Your Simon Work license key"
EMAIL_BODY = """Thank you for buying Simon Work {plan}!

Your license key:

{key}

To activate:
  1. Open Simon Work → Settings → License
  2. Paste the key and save.

Or set SIMON_LICENSE_KEY in your .env and restart.

Keep this email — the key is your proof of purchase.
— Mindpod Technologies
"""


def verify_signature(payload: bytes, header: str, secret: str,
                     now: Optional[float] = None) -> bool:
    """Validate Stripe-Signature (t=…,v1=…) against the raw body."""
    if not secret or not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    timestamp, sig = parts.get("t"), parts.get("v1")
    if not timestamp or not sig:
        return False
    try:
        if abs((now or time.time()) - int(timestamp)) > SIGNATURE_TOLERANCE_S:
            return False
    except ValueError:
        return False
    expected = hmac.new(secret.encode(),
                        f"{timestamp}.".encode() + payload,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def plan_for_session(session: dict) -> str:
    """Map a checkout session to a plan via STRIPE_PAYMENT_LINK_PLANS
    ("plink_abc=pro,plink_xyz=business"); falls back to amount tiers."""
    link_id = session.get("payment_link") or ""
    mapping = os.environ.get("STRIPE_PAYMENT_LINK_PLANS", "")
    for part in mapping.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() == link_id:
                plan = v.strip().lower()
                if plan in ("pro", "business"):
                    return plan
    # Amount fallback (USD cents): >= $300 → business, else pro.
    if int(session.get("amount_total") or 0) >= 30000:
        return "business"
    return "pro"


def fulfill(session: dict, db_path: Optional[str] = None,
            mailer=None) -> dict:
    """Mint, store and email the key for a completed checkout session.
    Idempotent on session id. Returns the issued record."""
    session_id = session.get("id") or ""
    email = ((session.get("customer_details") or {}).get("email")
             or session.get("customer_email") or "")
    if not session_id or not email:
        raise ValueError("session lacks id or customer email")
    existing = store.lookup(session_id, path=db_path)
    if existing:
        log.info("session %s already fulfilled — skipping", session_id)
        return existing
    plan = plan_for_session(session)
    key = keys.make_key(plan, email, keys.expiry_for(plan))
    store.record_issued(session_id, email, plan, key, path=db_path)
    if mailer is not None:
        try:
            mailer(email, EMAIL_SUBJECT.format(plan=plan),
                   EMAIL_BODY.format(plan=plan, key=key))
            store.mark_emailed(session_id, path=db_path)
        except Exception:  # noqa: BLE001 - key is stored; email can retry
            log.exception("license email to %s failed", email)
    log.info("issued %s license to %s (session %s)", plan, email, session_id)
    return {"stripe_session": session_id, "email": email, "plan": plan,
            "license_key": key}


def _graph_mailer(settings):
    """Send via Simon's Graph mailbox; None when not configured."""
    try:
        from simon.tools.graph_mail import send_email_graph
    except Exception:  # noqa: BLE001
        return None

    def mail(to: str, subject: str, body: str) -> None:
        result = send_email_graph(settings, to, subject, body)
        if str(result).startswith("Error"):
            raise RuntimeError(result)
    return mail


SUCCESS_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<title>Simon Work — Thank you</title>
<style>
 body {{ font-family: -apple-system, sans-serif; background: #0a0e14;
        color: #d7e3ea; display: flex; justify-content: center;
        padding: 48px 16px; }}
 .card {{ max-width: 560px; background: #0b1116; border: 1px solid #16232b;
         border-radius: 12px; padding: 32px; }}
 h1 {{ font-size: 22px; color: #3fc6c9; }}
 code {{ display: block; background: #101a21; border: 1px solid #16232b;
        border-radius: 8px; padding: 14px; margin: 16px 0;
        word-break: break-all; font-size: 13px; color: #9fe8ea; }}
 p {{ line-height: 1.5; margin: 10px 0; font-size: 14px; }}
 .dim {{ color: #5b7180; font-size: 13px; }}
</style></head><body><div class="card">{body}</div></body></html>"""


def success_html(record: Optional[dict]) -> str:
    if record:
        body = (f"<h1>You're in — Simon Work {record['plan']}</h1>"
                f"<p>Your license key (also emailed to "
                f"<b>{record['email']}</b>):</p>"
                f"<code>{record['license_key']}</code>"
                f"<p>Open Simon Work → Settings → License, paste the key, "
                f"save. Keep this page — it's your proof of purchase.</p>")
        return SUCCESS_PAGE.format(refresh="", body=body)
    body = ("<h1>Payment received</h1>"
            "<p>We're preparing your license key right now — this page will "
            "refresh automatically. It will also arrive by email within a "
            "minute.</p>"
            "<p class='dim'>Nothing after a few minutes? Write to "
            "support@mindpodtech.com and we'll sort it out.</p>")
    return SUCCESS_PAGE.format(
        refresh='<meta http-equiv="refresh" content="4">', body=body)


def create_app(settings=None, db_path: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="Simon Billing")
    mailer = _graph_mailer(settings) if settings is not None else None

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.post("/stripe/webhook")
    async def stripe_webhook(request: Request):
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        payload = await request.body()
        if not verify_signature(
                payload, request.headers.get("stripe-signature", ""), secret):
            return JSONResponse({"error": "bad signature"}, status_code=400)
        try:
            event = json.loads(payload)
        except ValueError:
            return JSONResponse({"error": "bad json"}, status_code=400)
        if event.get("type") != "checkout.session.completed":
            return {"ignored": event.get("type", "unknown")}
        try:
            record = fulfill(event.get("data", {}).get("object", {}),
                             db_path=db_path, mailer=mailer)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return {"issued": record["plan"], "email": record["email"]}

    @app.get("/success", response_class=HTMLResponse)
    async def success(session_id: str = ""):
        record = store.lookup(session_id, path=db_path) if session_id else None
        return success_html(record)

    return app


def _load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from the repo .env (launchd gives us nothing).
    Existing environment variables win; malformed lines are skipped. Inline
    comments on unquoted values are stripped, matching dotenv semantics."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if not key or key in os.environ:
                    continue
                if value[:1] in ("'", '"') and value.endswith(value[0]):
                    value = value[1:-1]
                elif " #" in value:  # unquoted: trailing comment
                    value = value.split(" #", 1)[0].rstrip()
                os.environ[key] = value
    except OSError:
        log.warning("no .env found at %s — billing config incomplete", path)


def main() -> None:  # python -m billing.server
    import uvicorn
    from simon.config import get_settings
    logging.basicConfig(level=logging.INFO)
    _load_dotenv()
    app = create_app(get_settings())
    uvicorn.run(app, host="0.0.0.0", port=8792, log_level="info")


if __name__ == "__main__":
    main()
