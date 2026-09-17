#!/usr/bin/env python3
"""Simon entry point: python run.py [server|telegram|voice|all]."""

from __future__ import annotations

import argparse
import asyncio
import sys


def warm_models(settings) -> None:
    """Pre-load the smart + fast models into Ollama in the background.

    Cold loads cost 5-15 s on a single-GPU machine and hit whoever happens
    to ask first after a restart or an eviction. A one-token chat request
    with keep_alive pins each model; runs in a daemon thread so startup
    is never delayed.
    """
    import threading

    def _warm() -> None:
        try:
            from openai import OpenAI
            client = OpenAI(base_url=settings.llm_base_url,
                            api_key=settings.llm_api_key or "simon-no-key")
            models = {settings.llm_model,
                      getattr(settings, "llm_model_fast", "") or ""} - {""}
            for model in models:
                try:
                    client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": "ping"}],
                        max_tokens=1,
                        extra_body={"keep_alive": "24h",
                                    "reasoning_effort": "none"})
                except Exception:  # noqa: BLE001 - warm-up is best-effort
                    pass
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_warm, daemon=True, name="simon-model-warmup"
                     ).start()


def cmd_server() -> None:
    """Run the FastAPI web interface on 0.0.0.0:8788."""
    import uvicorn

    from simon.config import get_settings
    from simon.interfaces.web import create_app

    settings = get_settings()
    if "11434" in (settings.llm_base_url or ""):
        warm_models(settings)
    uvicorn.run(create_app(settings), host="0.0.0.0", port=8788)


def cmd_telegram() -> None:
    """Run the Telegram bot (long polling)."""
    from simon.config import get_settings
    from simon.interfaces.telegram_bot import run_telegram

    run_telegram(get_settings())


def cmd_voice() -> None:
    """Run the local voice CLI (record -> STT -> agent -> TTS)."""
    from simon.config import get_settings
    from simon.interfaces.voice_cli import run_voice_cli

    run_voice_cli(get_settings())


def cmd_slack() -> None:
    """Run the Slack bot (Socket Mode)."""
    from simon.config import get_settings
    from simon.interfaces.slack_bot import run_slack

    run_slack(get_settings())


def cmd_teams() -> None:
    """Run the Microsoft Teams bot (aiohttp on TEAMS_PORT)."""
    from simon.config import get_settings
    from simon.interfaces.teams_bot import run_teams

    run_teams(get_settings())


async def _run_all() -> None:
    """Run web server + every configured chat interface + scheduler in one loop.

    Telegram, Slack and Teams are each started only when their tokens are
    configured; the web server and scheduler always run.
    """
    import uvicorn

    from simon.agent import Agent
    from simon.config import get_settings
    from simon.interfaces.web import create_app
    from simon.scheduler import Scheduler
    from simon.tools import build_default_registry

    settings = get_settings()
    if "11434" in (settings.llm_base_url or ""):
        warm_models(settings)
    # Scheduler delivers proactive messages through Telegram when configured,
    # otherwise it just logs.
    telegram_notify = None
    if settings.telegram_bot_token:
        from simon.interfaces.telegram_bot import make_notify
        telegram_notify = make_notify(settings)
        # Approval asks raised in ANY session (including background jobs)
        # are pushed to Telegram so nothing parks silently.
        from simon import approvals
        approvals.set_notifier(telegram_notify)

    scheduler = Scheduler(
        settings,
        agent_factory=lambda: Agent(
            settings,
            registry=build_default_registry(
                settings, exclude={"start_job", "schedule_task"}),
            interface="scheduler"),
        notify=telegram_notify or (lambda text: print(f"[simon] {text}")),
    )
    scheduler.start()

    # Background job harness: executes long-running assignments one at a
    # time and delivers results via the notify channel. Job agents get a
    # per-job session and cannot queue further jobs or schedules (depth 0).
    job_runner = None
    if getattr(settings, "simon_jobs_enabled", False):
        from simon.jobs import JobRunner

        def _job_agent(job_id: int) -> Agent:
            registry = build_default_registry(
                settings, exclude={"start_job", "schedule_task"})
            return Agent(settings, registry=registry,
                         session_id=f"job-{job_id}", interface="job")

        job_runner = JobRunner(
            settings,
            agent_factory=_job_agent,
            notify=telegram_notify or (lambda text: print(f"[simon] {text}")),
        )
        job_runner.start()

    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="0.0.0.0", port=8788,
                       log_level="info")
    )

    tasks = [asyncio.create_task(server.serve())]
    live = ["web(0.0.0.0:8788)", "scheduler"]

    if settings.telegram_bot_token:
        from simon.interfaces.telegram_bot import run_telegram_async
        tasks.append(asyncio.create_task(run_telegram_async(settings)))
        live.append("telegram")
    else:
        print("[simon] TELEGRAM_BOT_TOKEN not set; Telegram interface disabled.",
              file=sys.stderr)

    if settings.slack_bot_token and settings.slack_app_token:
        from simon.interfaces.slack_bot import run_slack_async
        tasks.append(asyncio.create_task(run_slack_async(settings)))
        live.append("slack")
    else:
        print("[simon] SLACK_BOT_TOKEN/SLACK_APP_TOKEN not set; "
              "Slack interface disabled.", file=sys.stderr)

    if settings.teams_app_id:
        from simon.interfaces.teams_bot import run_teams_async
        tasks.append(asyncio.create_task(run_teams_async(settings)))
        live.append(f"teams(0.0.0.0:{settings.teams_port})")
    else:
        print("[simon] TEAMS_APP_ID not set; Teams interface disabled.",
              file=sys.stderr)

    print(f"[simon] interfaces live: {', '.join(live)}")

    try:
        await asyncio.gather(*tasks)
    finally:
        scheduler.shutdown()
        if job_runner is not None:
            job_runner.shutdown()


def cmd_all() -> None:
    """Run server + configured interfaces + scheduler via asyncio."""
    # Diagnostics: `kill -USR1 <pid>` dumps all thread stacks to stderr —
    # invaluable when a turn hangs in the long-lived launchd process.
    import faulthandler
    import signal

    faulthandler.register(signal.SIGUSR1)
    asyncio.run(_run_all())


def cmd_update(args) -> None:
    """Self-update to the newest git tag, with automatic rollback."""
    from simon.updater import UpdateError, check, update

    try:
        if args.check:
            info = check()
            print(f"current: {info['current_tag']} ({info['current_sha']})  "
                  f"latest: {info['latest_tag']}  [{info['source']}]")
            print("update available" if info["update_available"]
                  else "up to date")
        else:
            update(skip_tests=args.skip_tests)
    except UpdateError as exc:
        print(f"update: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_rollback(_args) -> None:
    """Roll back to the version running before the last update."""
    from simon.updater import UpdateError, rollback

    try:
        rollback()
    except UpdateError as exc:
        print(f"rollback: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Parse arguments and dispatch to the selected interface."""
    parser = argparse.ArgumentParser(
        prog="simon",
        description="Simon - a self-hosted JARVIS-style personal AI assistant.",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="server",
        choices=["server", "telegram", "slack", "teams", "voice", "all",
                 "update", "rollback"],
        help="server: web UI on 0.0.0.0:8788 | telegram: bot | "
             "slack: Slack bot (Socket Mode) | teams: MS Teams bot | "
             "voice: local voice CLI | all: server+configured interfaces+scheduler | "
             "update: self-update to newest tag (auto-rollback on failure) | "
             "rollback: return to pre-update version",
    )
    parser.add_argument("--check", action="store_true",
                        help="with 'update': only report availability")
    parser.add_argument("--skip-tests", action="store_true",
                        help="with 'update': skip the unit-test smoke gate")
    args = parser.parse_args()

    # Enforce commercial licensing (no-op for default personal/trial use;
    # exits politely if SIMON_REQUIRE_LICENSE=true and the key is invalid).
    from simon.config import get_settings
    from simon.licensing import require_license_or_exit

    require_license_or_exit(get_settings())

    commands = {
        "server": cmd_server,
        "telegram": cmd_telegram,
        "slack": cmd_slack,
        "teams": cmd_teams,
        "voice": cmd_voice,
        "all": cmd_all,
    }
    if args.mode == "update":
        cmd_update(args)
    elif args.mode == "rollback":
        cmd_rollback(args)
    else:
        commands[args.mode]()


if __name__ == "__main__":
    main()
