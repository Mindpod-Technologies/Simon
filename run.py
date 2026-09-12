#!/usr/bin/env python3
"""Simon entry point: python run.py [server|telegram|voice|all]."""

from __future__ import annotations

import argparse
import asyncio
import sys


def cmd_server() -> None:
    """Run the FastAPI web interface on 0.0.0.0:8788."""
    import uvicorn

    from simon.config import get_settings
    from simon.interfaces.web import create_app

    settings = get_settings()
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

    # Scheduler delivers proactive messages through Telegram when configured,
    # otherwise it just logs.
    telegram_notify = None
    if settings.telegram_bot_token:
        from simon.interfaces.telegram_bot import make_notify
        telegram_notify = make_notify(settings)

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
    asyncio.run(_run_all())


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
        choices=["server", "telegram", "slack", "teams", "voice", "all"],
        help="server: web UI on 0.0.0.0:8788 | telegram: bot | "
             "slack: Slack bot (Socket Mode) | teams: MS Teams bot | "
             "voice: local voice CLI | all: server+configured interfaces+scheduler",
    )
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
    commands[args.mode]()


if __name__ == "__main__":
    main()
