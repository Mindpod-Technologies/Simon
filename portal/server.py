"""Portal web app: license-key sign-in, dashboard, support cases."""

from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from billing import store as billing_store
from . import cases, stats

log = logging.getLogger(__name__)

_COOKIE = "simon_portal"
_SESSIONS: dict[str, dict] = {}  # token -> {email, plan} (process-local)

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Simon Cloud — {title}</title>
<style>
 body {{ font-family:-apple-system,sans-serif; background:#06090d;
        color:#d7e3ea; margin:0; padding:32px 16px; }}
 .wrap {{ max-width:860px; margin:0 auto; }}
 h1 {{ color:#3fc6c9; font-size:20px; letter-spacing:.25em; }}
 h2 {{ font-size:12px; letter-spacing:.2em; color:#5b7180;
      border-top:1px solid #16232b; padding-top:18px; margin-top:26px; }}
 .card {{ background:#0b1116; border:1px solid #16232b; border-radius:12px;
         padding:18px 20px; margin-top:12px; }}
 .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
         gap:10px; }}
 .stat {{ background:#101a21; border:1px solid #16232b; border-radius:10px;
         padding:12px; }}
 .stat .n {{ font-size:22px; color:#9fe8ea; font-weight:600; }}
 .stat .l {{ font-size:10px; letter-spacing:.15em; color:#5b7180;
            margin-top:4px; }}
 input, textarea {{ background:#101a21; border:1px solid #16232b;
   border-radius:9px; color:#d7e3ea; padding:9px 12px; font-size:13px;
   width:100%; box-sizing:border-box; }}
 button {{ background:linear-gradient(135deg,#16494c,#1d5f63); color:#d7e3ea;
   border:none; border-radius:9px; padding:9px 18px; font-size:12px;
   letter-spacing:.1em; cursor:pointer; }}
 a {{ color:#3fc6c9; }}
 .note {{ color:#5b7180; font-size:12px; }}
 table {{ width:100%; border-collapse:collapse; font-size:13px; }}
 td, th {{ text-align:left; padding:8px 6px; border-bottom:1px solid #16232b; }}
 .pill {{ font-size:10px; padding:2px 9px; border-radius:99px;
         border:1px solid #16232b; }}
 .open {{ color:#e2c15c; }} .in_progress {{ color:#3fc6c9; }}
 .resolved {{ color:#6fce85; }}
</style></head><body><div class="wrap">{body}</div></body></html>"""


def _page(title: str, body: str) -> str:
    return PAGE.format(title=title, body=body)


def _customer(token: str) -> Optional[dict]:
    return _SESSIONS.get(token)


def _find_license(key: str, db_path: Optional[str]) -> Optional[dict]:
    """License key → issued-license record (email, plan)."""
    key = (key or "").strip()
    if not key.startswith("SIMON-"):
        return None
    conn = billing_store._connect(db_path)
    try:
        row = conn.execute(
            "SELECT email, plan, created_at FROM issued_licenses"
            " WHERE license_key = ?", (key,)).fetchone()
        return dict(row) if row else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()


def _tenant_dir_for(email: str) -> str:
    root = os.environ.get("SIMON_CLOUD_ROOT", "/opt/simon-cloud/tenants")
    try:
        import json as _json
        state = _json.loads(
            (os.path.join(root, "tenants.json") and
             open(os.path.join(root, "tenants.json")).read()))
        for t in state.get("tenants", {}).values():
            if t.get("email") == email:
                return t.get("dir", "")
    except (OSError, ValueError):
        pass
    return ""


def create_app(billing_db: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="Simon Cloud Portal")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        cust = _customer(request.cookies.get(_COOKIE, ""))
        if not cust:
            return RedirectResponse("/login", status_code=303)
        return _dashboard(cust)

    @app.get("/login", response_class=HTMLResponse)
    async def login_form():
        return _page("Sign in", """
<h1>SIMON CLOUD</h1>
<div class="card">
 <p class="note">Sign in with the license key from your purchase email
 (SIMON-pro-… / SIMON-business-…).</p>
 <form method="post" action="/login">
  <input name="key" placeholder="SIMON-…" style="font-family:monospace">
  <div style="margin-top:12px"><button>Sign in</button></div>
 </form>
</div>""")

    @app.post("/login")
    async def login(key: str = Form("")):
        record = _find_license(key, billing_db)
        if not record:
            return _page("Sign in", """
<h1>SIMON CLOUD</h1>
<div class="card"><p>That key isn't on record — check the purchase email
and paste the full key. Need help? <a href="/login">try again</a> or email
support@mindpodtech.com.</p></div>""")
        token = secrets.token_urlsafe(24)
        _SESSIONS[token] = record
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(_COOKIE, token, httponly=True, samesite="lax")
        return resp

    @app.get("/logout")
    async def logout(request: Request):
        _SESSIONS.pop(request.cookies.get(_COOKIE, ""), None)
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(_COOKIE)
        return resp

    def _dashboard(cust: dict) -> str:
        tdir = _tenant_dir_for(cust["email"])
        st = stats.tenant_stats(tdir) if tdir else {"hosted": False}
        usage = ""
        if st.get("hosted"):
            channels = "".join(
                f"<tr><td>{k}</td><td>{v}</td></tr>"
                for k, v in st.get("turns_by_channel", {}).items())
            usage = f"""
<h2>Usage — last {st['days']} days</h2>
<div class="grid">
 <div class="stat"><div class="n">{st['turns_total']}</div><div class="l">CONVERSATIONS</div></div>
 <div class="stat"><div class="n">{st['avg_latency_s']}s</div><div class="l">AVG RESPONSE</div></div>
 <div class="stat"><div class="n">{st.get('automations_active', 0)}</div><div class="l">ACTIVE AUTOMATIONS</div></div>
 <div class="stat"><div class="n">{st.get('jobs', {}).get('done', 0)}</div><div class="l">JOBS COMPLETED</div></div>
 <div class="stat"><div class="n">{st.get('approvals', {}).get('pending', 0)}</div><div class="l">AWAITING APPROVAL</div></div>
</div>
{"<h2>By channel</h2><div class='card'><table>" + channels + "</table></div>" if channels else ""}
<p class="note">Last activity: {st.get('last_active', '—')}</p>"""
        else:
            usage = """
<h2>Usage</h2>
<div class="card"><p class="note">You're self-hosted — your stats stay on
your hardware (we never phone home). Your Simon's own monitoring portal
(:8789) has the live feed. Hosted plan members see usage here.</p></div>"""
        return _page("Dashboard", f"""
<h1>SIMON CLOUD</h1>
<p class="note">{cust['email']} · <b>{cust['plan']}</b> plan · licensed since
{cust['created_at'][:10]} · <a href="/logout">sign out</a></p>
{usage}
<h2>Support</h2>
<div class="card">
 <form method="post" action="/cases">
  <input name="subject" placeholder="Subject">
  <div style="height:8px"></div>
  <textarea name="body" rows="4" placeholder="What happened? What did you expect?"></textarea>
  <div style="margin-top:10px"><button>Open a support case</button></div>
 </form>
</div>
<div id="cases">{_cases_html(cust["email"])}</div>""")

    def _cases_html(email: str) -> str:
        rows = cases.list_cases(email, path=os.environ.get("PORTAL_DB") or None)
        if not rows:
            return '<div class="card"><p class="note">No cases yet.</p></div>'
        lines = "".join(
            f"<tr><td><b>{c['case_ref']}</b></td><td>{c['subject']}</td>"
            f"<td><span class='pill {c['status']}'>{c['status']}</span></td>"
            f"<td class='note'>{c['updated_at']}</td></tr>"
            for c in rows)
        return (f"<div class='card'><table><tr><th>Case</th><th>Subject</th>"
                f"<th>Status</th><th>Updated</th></tr>{lines}</table></div>")

    @app.post("/cases")
    async def create_case(request: Request, subject: str = Form(""),
                          body: str = Form("")):
        cust = _customer(request.cookies.get(_COOKIE, ""))
        if not cust:
            return RedirectResponse("/login", status_code=303)
        try:
            ref = cases.create_case(cust["email"], cust["plan"], subject, body,
                                    path=os.environ.get("PORTAL_DB") or None)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _notify_support(ref, cust, subject)
        return RedirectResponse("/", status_code=303)

    return app


def _notify_support(ref: str, cust: dict, subject: str) -> None:
    """New case → email support@mindpodtech.com from Simon's mailbox."""
    if os.environ.get("PORTAL_NOTIFY_EMAIL") != "1":
        return
    try:
        from simon.config import get_settings
        from simon.tools.graph_mail import send_email_graph
        send_email_graph(
            get_settings(), "support@mindpodtech.com",
            f"[{ref}] {subject}",
            f"New support case from {cust['email']} ({cust['plan']} plan).")
    except Exception:  # noqa: BLE001
        log.exception("support notification failed for %s", ref)


def main() -> None:  # python -m portal.server
    import uvicorn
    from simon.config import get_settings  # noqa: F401  (loads .env)
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host="0.0.0.0", port=8793, log_level="info")


if __name__ == "__main__":
    main()
