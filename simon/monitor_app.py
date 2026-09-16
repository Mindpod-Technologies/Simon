"""Simon monitoring portal — standalone dashboard on port 8789.

Run:  .venv/bin/python -m simon.monitor_app
Reads the observability events recorded by simon.obs (same SQLite DB as
memory) and serves a dark single-page dashboard plus JSON APIs:

    GET /            dashboard HTML
    GET /api/stats   aggregate stats (turns, latency, models, interfaces…)
    GET /api/events  recent events (?limit=&kind=)
"""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from simon import obs
from simon.config import get_settings

_STARTED = time.time()

# Model-role labels for the dashboard: the old heuristic (/27b|q3_K/) broke
# the moment the smart brain became gpt-oss:20b. Data-driven instead.
SMART_MODEL = get_settings().llm_model

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SIMON · Monitor</title>
<style>
  :root { --bg:#0a0e14; --panel:#111822; --line:#1e2a38; --txt:#c9d6e3;
          --dim:#5c7186; --accent:#4fd1c5; --warn:#f6ad55; --bad:#fc8181; }
  * { box-sizing: border-box; margin: 0; }
  body { background: var(--bg); color: var(--txt);
         font: 14px/1.5 "SF Mono", Menlo, monospace; padding: 24px;
         max-width: 1100px; margin: 0 auto; }
  h1 { color: var(--accent); letter-spacing: .4em; font-size: 20px; }
  h1 small { color: var(--dim); letter-spacing: .1em; font-size: 11px;
             margin-left: 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
          gap: 12px; margin: 20px 0; }
  .card { background: var(--panel); border: 1px solid var(--line);
          border-radius: 10px; padding: 14px; }
  .card .n { font-size: 24px; color: var(--accent); }
  .card .l { color: var(--dim); font-size: 11px; text-transform: uppercase;
             letter-spacing: .08em; margin-top: 4px; }
  .panel { background: var(--panel); border: 1px solid var(--line);
           border-radius: 10px; padding: 16px; margin-bottom: 16px; }
  .panel h2 { font-size: 12px; color: var(--dim); text-transform: uppercase;
              letter-spacing: .1em; margin-bottom: 10px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  td, th { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--line);
           vertical-align: top; }
  th { color: var(--dim); font-weight: normal; text-transform: uppercase;
       font-size: 10px; letter-spacing: .08em; }
  .pill { padding: 1px 8px; border-radius: 99px; font-size: 11px;
          border: 1px solid var(--line); }
  .fast { color: var(--accent); } .smart { color: var(--warn); }
  .err { color: var(--bad); }
  .bar { height: 8px; border-radius: 4px; background: var(--accent);
         display: inline-block; }
  .muted { color: var(--dim); }
</style>
</head>
<body>
<h1>SIMON <small>MONITORING PORTAL</small></h1>
<div class="grid" id="cards"></div>
<div class="panel"><h2>Traffic by interface</h2><div id="iface"></div></div>
<div class="panel"><h2>Brain usage (router)</h2><div id="models"></div></div>
<div class="panel"><h2>Last eval run</h2><div id="eval" class="muted">—</div></div>
<div class="panel"><h2>Recent turns</h2><table id="events">
<thead><tr><th>Time</th><th>Interface</th><th>Model</th><th>Route reason</th>
<th>Latency</th><th>Tools</th></tr></thead><tbody></tbody></table></div>
<script>
function bars(el, data) {
  const max = Math.max(1, ...Object.values(data));
  el.innerHTML = Object.entries(data).map(([k,v]) =>
    `<div style="margin:6px 0"><span class="pill">${k.replace(/.*\\//,'')}</span>
     <span class="bar" style="width:${Math.round(120*v/max)}px;margin-left:8px"></span>
     <span class="muted"> ${v}</span></div>`).join('');
}
async function refresh() {
  const s = await (await fetch('/api/stats')).json();
  document.getElementById('cards').innerHTML = [
    ['Total turns', s.total_turns], ['Avg latency', (s.avg_latency_ms/1000).toFixed(1)+'s'],
    ['p95 latency', (s.p95_latency_ms/1000).toFixed(1)+'s'],
    ['Errors', s.error_count], ['Uptime', Math.round(s.uptime_s/60)+' min'],
  ].map(([l,n]) => `<div class="card"><div class="n">${n}</div><div class="l">${l}</div></div>`).join('');
  bars(document.getElementById('iface'), s.by_interface);
  bars(document.getElementById('models'), s.by_model);
  const ev = s.last_eval;
  document.getElementById('eval').innerHTML = ev
    ? `<span class="${ev.failed ? 'err' : 'fast'}">${ev.passed}/${ev.total} passed</span>
       <span class="muted"> · ${s.last_eval_ts||''} · ${ev.duration_s||'?'}s</span>
       ${ev.failures && ev.failures.length ? '<div class="err">failed: '+ev.failures.join(', ')+'</div>' : ''}`
    : 'No eval run recorded yet.';
  const rows = await (await fetch('/api/events?limit=15&kind=turn')).json();
  document.querySelector('#events tbody').innerHTML = rows.map(e => {
    const d = JSON.parse(e.detail || '{}');
    const cls = e.model === "__SMART_MODEL__" ? 'smart' : 'fast';
    return `<tr><td class="muted">${e.ts.slice(11,19)}</td><td>${e.interface}</td>
      <td class="${cls}">${(e.model||'').replace(/.*\\//,'')}</td>
      <td class="muted">${e.route_reason||''}${d.escalated?' <span class="err">⇑escalated</span>':''}</td>
      <td>${(e.latency_ms/1000).toFixed(1)}s</td>
      <td class="muted">${(d.tools||[]).join(', ')||'—'}</td></tr>`;
  }).join('');
}
refresh(); setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def create_app() -> FastAPI:
    app = FastAPI(title="Simon Monitor")

    # Same owner gate as the main UI: the session cookie is keyed on the
    # password hash and cookies are port-independent, so one login covers
    # both apps.
    from fastapi import Request
    from fastapi.responses import JSONResponse, RedirectResponse

    from simon import auth as auth_mod
    from simon import settings_api

    MAIN = "http://localhost:8788"

    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        try:
            values, _ = settings_api.read_env()
            stored = values.get("SIMON_OWNER_PASSWORD_HASH", "")
        except Exception:  # noqa: BLE001
            stored = ""
        token = request.cookies.get(auth_mod.COOKIE_NAME, "")
        if auth_mod.check_session(token, stored):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "authentication required"},
                                status_code=401)
        return RedirectResponse(
            MAIN + ("/setup" if not stored else "/login"), status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        # Inject the configured smart model so the JS can label rows by
        # actual role instead of a stale name heuristic.
        return DASHBOARD_HTML.replace(
            "__SMART_MODEL__", SMART_MODEL.replace('"', ""))

    @app.get("/api/stats")
    async def stats() -> dict:
        data = obs.summary()
        data["uptime_s"] = int(time.time() - _STARTED)
        return data

    @app.get("/api/events")
    async def events(limit: int = 50, kind: str | None = None) -> list:
        return obs.recent_events(limit=min(limit, 200), kind=kind or None)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8789, log_level="warning")


if __name__ == "__main__":
    main()
