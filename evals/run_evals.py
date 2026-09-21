#!/usr/bin/env python3
"""Simon evals runner — behavioural scenarios against the live configuration.

Usage (from ~/simon):
    .venv/bin/python evals/run_evals.py                 # run all scenarios
    .venv/bin/python evals/run_evals.py --name recall   # substring filter

Each scenario sends one message through a real Agent (same router, memory,
persona and tools as production) and asserts expectations:

    route                 'fast' | 'smart' — which brain the router picked
    reply_contains_any    at least one substring (case-insensitive) in reply
    reply_not_contains    none of these substrings may appear
    reply_min_len         minimum reply length
    fact_stored_contains  substring that must appear in the facts DB afterwards

Results print as a table and are recorded as an 'eval' event in the
observability DB (visible in the monitoring portal under "Last eval run").

Note: scenarios run against the real models — a full run takes several
minutes (smart-brain scenarios are slow by nature).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simon import memory, obs  # noqa: E402
from simon.agent import Agent  # noqa: E402
from simon.config import get_settings  # noqa: E402


def check(expect: dict, reply: str, route: str) -> list[str]:
    """Return a list of failed assertions (empty = pass)."""
    failures = []
    # Normalise unicode whitespace (models love narrow no-break spaces) so
    # string checks don't false-fail on a correct answer.
    low = reply.lower()
    for ch in ("\u202f", "\u00a0", "\u2009", "\u2007"):
        low = low.replace(ch, " ")
    if "route" in expect and route != expect["route"]:
        failures.append(f"route={route} (expected {expect['route']})")
    for needle in expect.get("reply_not_contains", []):
        n = needle.lower()
        for ch in ("\u202f", "\u00a0", "\u2009", "\u2007"):
            n = n.replace(ch, " ")
        if n in low:
            failures.append(f"reply contains forbidden '{needle}'")
    if "reply_contains_any" in expect:
        needles = expect["reply_contains_any"]
        norm = []
        for n in needles:
            n = n.lower()
            for ch in ("\u202f", "\u00a0", "\u2009", "\u2007"):
                n = n.replace(ch, " ")
            norm.append(n)
        if not any(n in low for n in norm):
            failures.append(f"reply contains none of {needles}")
    if "reply_min_len" in expect and len(reply) < expect["reply_min_len"]:
        failures.append(f"reply too short ({len(reply)} chars)")
    if "fact_stored_contains" in expect:
        needle = expect["fact_stored_contains"][0]
        rows = memory._connect().execute("SELECT value FROM facts").fetchall()
        if not any(needle.lower() in r["value"].lower() for r in rows):
            failures.append(f"no stored fact contains '{needle}'")
    if "job_created_contains" in expect:
        from simon import jobs
        needle = expect["job_created_contains"][0]
        recent = jobs.list_jobs(limit=50)
        if not any(needle.lower() in j["description"].lower()
                   for j in recent):
            failures.append(f"no recent job description contains '{needle}'")
    if "schedule_created_contains" in expect:
        from simon import schedules
        needle = expect["schedule_created_contains"][0]
        recent = schedules.list_schedules(active_only=True)
        if not any(needle.lower() in s["description"].lower()
                   for s in recent):
            failures.append(f"no active schedule description contains '{needle}'")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="", help="substring filter")
    args = parser.parse_args()

    settings = get_settings()
    memory.init_db()
    scenarios = json.loads(
        (Path(__file__).parent / "scenarios.json").read_text())["scenarios"]
    if args.name:
        scenarios = [s for s in scenarios if args.name in s["name"]]

    run_id = uuid.uuid4().hex[:8]
    results = []
    t_start = time.time()
    print(f"Eval run {run_id} — {len(scenarios)} scenario(s)\n")

    # Snapshot pending background jobs so jobs queued DURING this eval run
    # can be cancelled afterwards — the live JobRunner must never execute
    # eval leftovers and deliver test chatter to the owner.
    try:
        from simon import jobs, schedules
        pre_existing = {j["id"] for j in
                        jobs.list_jobs(status="pending", limit=1000)}
        pre_existing_schedules = {s["id"] for s in
                                  schedules.list_schedules(active_only=True)}
    except Exception:  # noqa: BLE001
        pre_existing = set()
        pre_existing_schedules = set()

    for sc in scenarios:
        session = f"eval-{run_id}" if sc.get("fresh_session") \
            else f"eval-{run_id}-shared"
        # Optional RAG seeding: ingest a document into this scenario's
        # knowledge space before the turn runs.
        if sc.get("ingest_doc"):
            import tempfile
            from simon import rag
            doc = sc["ingest_doc"]
            with tempfile.NamedTemporaryFile(
                    "w", suffix="-" + doc["filename"], delete=False,
                    encoding="utf-8") as fh:
                fh.write(doc["text"])
                seed_path = fh.name
            rag.add_document(seed_path, namespace=session)
        agent = Agent(settings, session_id=session, interface="eval")
        t0 = time.time()
        try:
            reply = agent.handle(sc["say"])
        except Exception as exc:  # noqa: BLE001
            reply = f"<agent raised: {exc}>"
        elapsed = time.time() - t0
        route = ("fast" if getattr(agent.llm, "last_route_reason", "")
                 == "simple turn" else "smart")
        failures = check(sc.get("expect", {}), reply, route)
        status = "PASS" if not failures else "FAIL"
        results.append({"name": sc["name"], "status": status,
                        "failures": failures, "elapsed_s": round(elapsed, 1)})
        print(f"[{status}] {sc['name']}  ({elapsed:.0f}s, route={route})")
        for f in failures:
            print(f"       └─ {f}")
        print(f"       reply: {reply[:110]!r}\n")

    passed = sum(1 for r in results if r["status"] == "PASS")
    duration = round(time.time() - t_start, 1)
    summary = {
        "run_id": run_id,
        "passed": passed,
        "failed": len(results) - passed,
        "total": len(results),
        "duration_s": duration,
        "failures": [r["name"] for r in results if r["status"] == "FAIL"],
    }
    obs.record_event("eval", interface="evals", **summary)
    print(f"== {passed}/{len(results)} passed in {duration}s ==")

    # Cleanup: cancel any background jobs queued during this run, and
    # deactivate any recurring schedules created by eval scenarios.
    try:
        for j in jobs.list_jobs(status="pending", limit=1000):
            if j["id"] not in pre_existing:
                jobs.cancel_job(j["id"])
        for s in schedules.list_schedules(active_only=True):
            if s["id"] not in pre_existing_schedules:
                schedules.deactivate_schedule(s["id"])
    except Exception:  # noqa: BLE001 - cleanup must never fail the run
        pass
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
