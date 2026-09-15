"""Proactive jobs: morning briefing, reminders, user-defined recurring
schedules (created in chat), weekly evals, and weekly automation
suggestions."""

from __future__ import annotations

import datetime
import logging
from typing import Any, Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import memory, obs, schedules
from .config import Settings, get_settings

logger = logging.getLogger(__name__)

BRIEFING_HOUR = 7
BRIEFING_MINUTE = 30
REMINDER_POLL_SECONDS = 30
EVAL_DAY_OF_WEEK = "sun"
EVAL_HOUR = 19
EVAL_MINUTE = 12
STATUS_MINUTE = 17  # off-peak minute for hourly status updates
SCHEDULE_SYNC_SECONDS = 60
MAIL_CHECK_MINUTES = 5  # default inbox poll cadence (env-tunable)
SUGGEST_DAY_OF_WEEK = "sat"
SUGGEST_HOUR = 10
SUGGEST_MINUTE = 12

TASK_PROMPT = (
    "This is your scheduled recurring task. Execute it now, autonomously, "
    "and produce the deliverable as your reply — it will be sent to the "
    "user directly. Use tools when genuinely needed; answer from your own "
    "knowledge otherwise. Do not ask questions.\n\nTASK: {description}"
)

SUGGEST_PROMPT = (
    "Weekly self-improvement review. Look at the conversation history above "
    "and your remembered facts. Identify 1-3 RECURRING patterns — things the "
    "user asked for repeatedly, reports they request often, or regular "
    "chores — that you could automate as a scheduled task. For each, give "
    "one line: the proposed task, and the suggested schedule (e.g. 'Mondays "
    "at 09:00'). If nothing is worth automating, say so in one line. Do not "
    "create anything — these are proposals only. Keep it under 120 words."
)


class Scheduler:
    """APScheduler-based job runner for Simon.

    - Morning briefing at 07:30 daily: asks the agent for a briefing and
      delivers it via ``notify``.
    - Polls :func:`simon.memory.due_reminders` every 30 seconds; each due
      reminder is delivered via ``notify`` and then deleted.
    """

    def __init__(self, settings: Optional[Settings] = None,
                 agent_factory: Optional[Callable[[], Any]] = None,
                 notify: Optional[Callable[[str], None]] = None) -> None:
        """Create the scheduler.

        ``agent_factory`` is a zero-arg callable returning an Agent (or a
        per-briefing new agent). ``notify`` receives the text to deliver.
        """
        self.settings = settings or get_settings()
        self.agent_factory = agent_factory
        self.notify = notify or (lambda text: logger.info("notify: %s", text))
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        """Register jobs and start the underlying AsyncIOScheduler."""
        memory.init_db()
        self._scheduler.add_job(
            self._morning_briefing,
            CronTrigger(hour=BRIEFING_HOUR, minute=BRIEFING_MINUTE),
            id="morning_briefing",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._poll_reminders,
            IntervalTrigger(seconds=REMINDER_POLL_SECONDS),
            id="poll_reminders",
            replace_existing=True,
        )
        # Inbox watch: poll Simon's M365 mailbox; the agent dedupes against
        # its own session history and only interrupts for new, actionable
        # mail. Requires Graph credentials (graph_mail tool).
        if (getattr(self.settings, "graph_client_secret", "")
                and getattr(self.settings, "simon_mail_check_enabled", True)):
            self._scheduler.add_job(
                self._mail_check,
                IntervalTrigger(minutes=getattr(
                    self.settings, "simon_mail_check_minutes",
                    MAIL_CHECK_MINUTES)),
                id="mail_check",
                replace_existing=True,
            )
        self._scheduler.add_job(
            self._weekly_evals,
            CronTrigger(day_of_week=EVAL_DAY_OF_WEEK, hour=EVAL_HOUR,
                        minute=EVAL_MINUTE),
            id="weekly_evals",
            replace_existing=True,
        )
        if getattr(self.settings, "simon_hourly_status", False):
            self._scheduler.add_job(
                self._hourly_status,
                CronTrigger(minute=STATUS_MINUTE, hour="7-23"),
                id="hourly_status",
                replace_existing=True,
            )
        # User-defined recurring schedules, created in chat via the
        # schedule_task tool — synced from SQLite so they go live without a
        # restart and survive reboots.
        self._scheduler.add_job(
            self._sync_schedules,
            IntervalTrigger(seconds=SCHEDULE_SYNC_SECONDS),
            id="sync_schedules",
            replace_existing=True,
        )
        if getattr(self.settings, "simon_weekly_suggestions", False):
            self._scheduler.add_job(
                self._weekly_suggestions,
                CronTrigger(day_of_week=SUGGEST_DAY_OF_WEEK,
                            hour=SUGGEST_HOUR, minute=SUGGEST_MINUTE),
                id="weekly_suggestions",
                replace_existing=True,
            )
        self._scheduler.start()
        self._sync_schedules()
        logger.info("Scheduler started (briefing %02d:%02d, poll every %ds)",
                    BRIEFING_HOUR, BRIEFING_MINUTE, REMINDER_POLL_SECONDS)

    def shutdown(self) -> None:
        """Stop the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown()

    async def _morning_briefing(self) -> None:
        """Generate and deliver the morning briefing."""
        if self.agent_factory is None:
            return
        try:
            agent = self.agent_factory()
            briefing = agent.handle(
                "Good morning. Please prepare my morning briefing: today's "
                "date, any relevant facts you remember, and anything I "
                "should attend to today."
            )
            if briefing:
                self.notify(briefing)
        except Exception:
            logger.exception("Morning briefing failed")

    async def _poll_reminders(self) -> None:
        """Deliver and delete any reminders that are due."""
        try:
            now_iso = datetime.datetime.now().isoformat(timespec="seconds")
            for reminder in memory.due_reminders(now_iso):
                try:
                    self.notify(f"Reminder, sir: {reminder['text']}")
                finally:
                    memory.delete_reminder(reminder["id"])
        except Exception:
            logger.exception("Reminder poll failed")

    async def _mail_check(self) -> None:
        """Poll Simon's M365 inbox; interrupt only for new, actionable mail.

        The agent's persistent session history is the dedupe: it knows what
        it already reported and must answer MAIL_CHECK_QUIET otherwise.
        """
        if self.agent_factory is None:
            return
        # Interactive priority: on this single-GPU machine a background mail
        # run evicts the chat model and stalls the user's next message —
        # skip this tick if the owner is mid-conversation; the next tick is
        # only minutes away.
        from . import agent as agent_mod
        if agent_mod.interactive_session_active():
            logger.info("mail check deferred — interactive session active")
            return
        try:
            agent = self.agent_factory()
            reply = agent.handle(
                "Background mail check (autonomous). Use read_recent_emails "
                "(count 5) on the simon@mindpodtech.com mailbox. Report ONLY "
                "messages you have NOT already reported in this session. If "
                "there is new mail needing the owner's attention, reply with "
                "a tight briefing per message (from, subject, what it "
                "needs). If there is nothing new, or nothing worth "
                "interrupting for, reply with exactly: MAIL_CHECK_QUIET"
            )
            if reply and "MAIL_CHECK_QUIET" not in reply:
                self.notify(f"\U0001F4EC Mail check: {reply}")
        except Exception:
            logger.exception("Mail check failed")

    def _activity_digest(self, hours: float = 1.0) -> str:
        """Deterministic digest of REAL recent activity from the event log.

        The hourly status must be grounded in facts, not the model's
        imagination: this digest is the ONLY source the status prompt
        allows. Covers conversations, tool calls, and job state changes in
        the window; eval sessions are excluded.
        """
        import json as _json

        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(hours=hours)).isoformat()
        try:
            events = [e for e in obs.recent_events(limit=300, kind="turn")
                      if e.get("ts", "") >= cutoff
                      and not (e.get("session_id") or "").startswith("eval")]
        except Exception:  # noqa: BLE001 - obs must never break scheduling
            events = []
        lines = ["REAL activity log for the last hour "
                 "(from Simon's event table — report ONLY from this):"]
        if not events:
            lines.append("- No conversations and no tool calls this hour.")
        else:
            by_interface: dict[str, int] = {}
            tools: dict[str, int] = {}
            for e in events:
                iface = e.get("interface") or "?"
                by_interface[iface] = by_interface.get(iface, 0) + 1
                try:
                    detail = _json.loads(e.get("detail") or "{}")
                    # record_event wraps the payload: {"detail": "<json>"}
                    inner = detail.get("detail", detail)
                    if isinstance(inner, str):
                        inner = _json.loads(inner)
                    for t in inner.get("tools", []):
                        tools[t] = tools.get(t, 0) + 1
                except Exception:  # noqa: BLE001
                    pass
            lines.append("- Conversations: " + ", ".join(
                f"{k} ×{v}" for k, v in sorted(by_interface.items())))
            lines.append("- Tools used: " + (
                ", ".join(f"{k} ×{v}" for k, v in sorted(tools.items()))
                if tools else "none"))
        try:
            conn = memory._connect()
            try:
                rows = conn.execute(
                    "SELECT id, status, description, finished_at FROM jobs "
                    "WHERE created_at >= ? OR started_at >= ? OR "
                    "finished_at >= ?",
                    (cutoff[:19].replace("T", " "),
                     cutoff[:19].replace("T", " "),
                     cutoff[:19].replace("T", " "))).fetchall()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            rows = []
        if rows:
            lines.append("- Background jobs: " + "; ".join(
                f"#{r[0]} {r[1]} — {r[2][:50]}" for r in rows[:5]))
        else:
            lines.append("- No background jobs ran.")
        return "\n".join(lines)

    async def _hourly_status(self) -> None:
        """Generate and deliver the hourly status update (owner directive).

        The stored fact ``status_update_channel`` requires hourly status
        updates in the owner's Telegram chat. Runs 07:17–23:17 daily.
        Grounded in the real activity digest — the model formats facts,
        it does not get to invent progress.
        """
        if self.agent_factory is None:
            return
        try:
            digest = self._activity_digest(hours=1.0)
            agent = self.agent_factory()
            status = agent.handle(
                "Scheduled hourly status update. Below is the REAL activity "
                "log for the last hour. Report ONLY from it — 3-5 short "
                "bullet points: what actually happened, what is running, "
                "and anything genuinely waiting on the user (only if it was "
                "asked for in a conversation this hour). If the log shows "
                "no real activity, say exactly that in one line — do NOT "
                "invent progress, and do not repeat previous updates. Keep "
                "the whole update under 120 words.\n\n" + digest
            )
            if status:
                self.notify(f"Hourly status, sir:\n\n{status}")
        except Exception:
            logger.exception("Hourly status update failed")

    def _sync_schedules(self) -> None:
        """Sync active user schedules from SQLite into APScheduler."""
        try:
            active = schedules.list_schedules(active_only=True)
            wanted = {}
            for row in active:
                job_id = f"user_task_{row['id']}"
                trigger_kwargs: dict[str, Any] = {
                    "hour": row["hour"], "minute": row["minute"]}
                if row.get("day_of_week"):
                    trigger_kwargs["day_of_week"] = row["day_of_week"]
                wanted[job_id] = (row, CronTrigger(**trigger_kwargs))
            existing = {j.id for j in self._scheduler.get_jobs()
                        if j.id.startswith("user_task_")}
            for job_id, (row, trigger) in wanted.items():
                if job_id not in existing:
                    self._scheduler.add_job(
                        self._run_scheduled_task, trigger, id=job_id,
                        args=[row["id"]], replace_existing=True)
                    logger.info("user schedule %s live: %s (%s)", job_id,
                                row["description"][:60],
                                schedules.describe(row))
            for job_id in existing - set(wanted):
                self._scheduler.remove_job(job_id)
                logger.info("user schedule %s removed", job_id)
        except Exception:
            logger.exception("schedule sync failed")

    async def _run_scheduled_task(self, schedule_id: int) -> None:
        """Execute one user-defined recurring task and deliver the result."""
        if self.agent_factory is None:
            return
        # Interactive priority: yield when the owner is mid-conversation;
        # the task fires on the next sync instead of stalling their chat.
        from . import agent as agent_mod
        if agent_mod.interactive_session_active():
            logger.info("scheduled task %d deferred — interactive session "
                        "active", schedule_id)
            return
        row = next((r for r in schedules.list_schedules(active_only=True)
                    if r["id"] == schedule_id), None)
        if row is None:
            return  # deactivated between sync and fire
        try:
            agent = self.agent_factory()
            result = agent.handle(
                TASK_PROMPT.format(description=row["description"]))
            if result:
                self.notify(f"Scheduled task, sir — «{row['description'][:70]}»"
                            f"\n\n{result}")
        except Exception:
            logger.exception("scheduled task %d failed", schedule_id)

    async def _weekly_suggestions(self) -> None:
        """Analyse recent usage and propose automations the user didn't ask for."""
        if self.agent_factory is None:
            return
        try:
            agent = self.agent_factory()
            suggestions = agent.handle(SUGGEST_PROMPT)
            if suggestions:
                self.notify("Weekly automation review, sir:\n\n" + suggestions)
        except Exception:
            logger.exception("Weekly suggestions failed")

    async def _weekly_evals(self) -> None:
        """Run the behavioural eval suite weekly and report the result.

        Executes evals/run_evals.py as a subprocess (full agent stack, real
        models — takes a few minutes) and delivers the summary line plus any
        failing assertions via ``notify``. The run is also recorded as an
        'eval' event, visible in the monitoring portal.
        """
        import asyncio
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        try:
            proc = await asyncio.create_subprocess_exec(
                str(repo / ".venv" / "bin" / "python"),
                str(repo / "evals" / "run_evals.py"),
                cwd=str(repo),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            text = out.decode(errors="replace")
            summary = next(
                (line.strip("= ").strip() for line in reversed(
                    text.splitlines()) if line.startswith("==")),
                "eval run finished without a summary line")
            failures = [line.strip().lstrip("└─").strip()
                        for line in text.splitlines()
                        if line.strip().startswith("└─")]
            if failures:
                message = (f"Weekly self-evaluation, sir: {summary}.\n"
                           "Failing checks:\n- " + "\n- ".join(failures[:5]))
            else:
                message = (f"Weekly self-evaluation, sir: {summary}. "
                           "All systems nominal.")
            self.notify(message)
        except Exception:
            logger.exception("Weekly eval run failed")
