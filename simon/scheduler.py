"""Proactive jobs: morning briefing, reminders, user-defined recurring
schedules (created in chat), weekly evals, and weekly automation
suggestions."""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
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
ONBOARDING_POLL_MINUTES = 20
EVAL_DAY_OF_WEEK = "sun"
EVAL_HOUR = 19
EVAL_MINUTE = 12
STATUS_MINUTE = 17  # off-peak minute for hourly status updates
SCHEDULE_SYNC_SECONDS = 60
MAIL_CHECK_MINUTES = 5  # default inbox poll cadence (env-tunable)

# A mail briefing must BE a briefing — question-shaped replies ("How can I
# help you with these?") leaked to the owner as alerts 2026-09-26.
_DODGE_BRIEFING_RE = re.compile(
    r"\b(how (can|may) i (help|assist)|how (would|do) you like|"
    r"would you like me to|let me know what)\b",
    re.IGNORECASE)
SUGGEST_DAY_OF_WEEK = "sat"
SUGGEST_HOUR = 10
SUGGEST_MINUTE = 12
MAINTENANCE_MINUTE = 23  # every 2 hours, off-peak minute (owner directive)

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
                 notify: Optional[Callable[[str], None]] = None,
                 notify_for: Optional[Callable[[str, str], None]] = None
                 ) -> None:
        """Create the scheduler.

        ``agent_factory`` is a zero-arg callable returning an Agent (or a
        per-briefing new agent). ``notify`` receives owner-operational text
        to deliver; ``notify_for`` (session, text) delivers per-person —
        schedule results and reminders go to whoever asked for them.
        """
        self.settings = settings or get_settings()
        self.agent_factory = agent_factory
        self.notify = notify or (lambda text: logger.info("notify: %s", text))
        self.notify_for = notify_for
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
        # Onboarding moment: settled new sessions get tailored proposals.
        self._scheduler.add_job(
            self._onboarding_proposals,
            IntervalTrigger(minutes=ONBOARDING_POLL_MINUTES),
            id="onboarding_proposals",
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
        # Self-maintenance (owner directive 2026-09-28): every 2 hours —
        # service health with auto-restart, deterministic eval battery,
        # error/job/approval hygiene, digested to the owner.
        if getattr(self.settings, "simon_maintenance_enabled", True):
            self._scheduler.add_job(
                self._maintenance_pass,
                CronTrigger(hour="*/2", minute=MAINTENANCE_MINUTE),
                id="maintenance",
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
            # Maintenance digest folded in (owner directive 2026-09-28):
            # Monday covers the whole weekend, other days the last 24h.
            from . import maintenance
            import datetime as _dt
            hours = 72.0 if _dt.date.today().weekday() == 0 else 24.0
            maint = maintenance.summarize_runs(hours)
            agent = self.agent_factory()
            briefing = await asyncio.to_thread(
                agent.handle,
                "Good morning. Please prepare my morning briefing: today's "
                "date, any relevant facts you remember, and anything I "
                "should attend to today. Fold this maintenance summary in "
                "briefly — 2-3 lines at most, flagging anything that needs "
                "my attention:\n\n" + maint
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
                    self._deliver(reminder.get("session_id", ""),
                                  f"Reminder: {reminder['text']}")
                finally:
                    memory.delete_reminder(reminder["id"])
        except Exception:
            logger.exception("Reminder poll failed")

    async def _onboarding_proposals(self) -> None:
        """The first-day-on-the-job move: settled new sessions get 3–5
        tailored proposals from their own history, delivered per-person."""
        if self.agent_factory is None:
            return
        from . import onboarding
        try:
            for row in onboarding.due_for_proposals():
                try:
                    context = onboarding.build_context(row["session_id"])
                    agent = self.agent_factory()
                    proposals = await asyncio.to_thread(
                        agent.handle, onboarding.PROMPT.format(context=context))
                    if proposals:
                        self._deliver(row["session_id"], proposals)
                        logger.info("onboarding proposals delivered to %s",
                                    row["session_id"])
                    onboarding.mark_proposed(row["session_id"])
                except Exception:
                    logger.exception("onboarding proposals failed for %s",
                                     row.get("session_id"))
                    onboarding.mark_proposed(row["session_id"])
        except Exception:
            logger.exception("onboarding proposals poll failed")

    def _deliver(self, session_id: str, text: str) -> None:
        """Per-person delivery when wired, else the owner channel."""
        if self.notify_for is not None:
            try:
                self.notify_for(session_id, text)
                return
            except Exception:  # noqa: BLE001
                logger.exception("notify_for failed; falling back to owner")
        self.notify(text)

    async def _mail_check(self) -> None:
        """Poll Simon's M365 inbox; interrupt only for GENUINELY new mail.

        Deterministic-first (2026-09-29): the OLD design asked the model to
        dedupe against its own session history — trimming lost it, so the
        same weekly digest was re-briefed 19× in a week and question-shaped
        replies leaked to the owner as mail alerts. Now: read the inbox
        directly, filter to message ids never reported (mailwatch table),
        and only involve the model to WORD the briefing — with a raw
        from/subject/date floor if it dodges. No new mail = no model call
        at all.
        """
        if self.agent_factory is None:
            return
        from . import agent as agent_mod
        if agent_mod.interactive_session_active():
            logger.info("mail check deferred — interactive session active")
            return
        try:
            from . import mailwatch
            from .tools.graph_mail import read_recent_emails_graph
            raw = await asyncio.to_thread(
                read_recent_emails_graph, self.settings, 10)
            if raw.startswith("graph mail:") or raw == "Inbox is empty.":
                return
            ids = mailwatch.parse_ids(raw)
            new_ids = mailwatch.unseen(ids)
            if not new_ids:
                return
            new_blocks = mailwatch.blocks_for_ids(raw, new_ids)
            agent = self.agent_factory()
            reply = await asyncio.to_thread(
                agent.handle,
                "Background mail check (autonomous). These inbox messages "
                "are NEW (never reported to the owner). Write a tight "
                "briefing per message (from, subject, what it needs) — "
                "plain text, no questions back to me. If none are worth "
                "interrupting the owner for, reply with exactly: "
                "MAIL_CHECK_QUIET\n\n" + new_blocks)
            if reply and "MAIL_CHECK_QUIET" in reply:
                mailwatch.mark_reported(new_ids)  # judged uninteresting
                return
            # Fabrication check: the briefing must anchor to the REAL
            # blocks (a sender or subject token). A reply that names none of
            # them is inventing mail — replace it with the raw floor.
            anchors = [w for w in re.findall(r"[A-Za-z0-9@.\-]{4,}",
                                             new_blocks)
                       if w.lower() not in ("from", "date", "unread",
                                            "http", "https")]
            anchored = bool(reply) and any(
                a.lower() in reply.lower() for a in anchors)
            if reply and not _DODGE_BRIEFING_RE.search(reply) and anchored:
                self.notify(f"\U0001F4EC Mail check: {reply}")
                mailwatch.mark_reported(new_ids)
                return
            # The model dodged or invented content — deliver the raw floor:
            # real senders and subjects, no prose.
            logger.warning("mail briefing dodged/unanchored — raw floor")
            self.notify("\U0001F4EC New mail, sir:\n\n" + new_blocks[:1500])
            mailwatch.mark_reported(new_ids)
        except Exception:
            logger.exception("Mail check failed")

    def _activity_digest(self, hours: float = 1.0) -> str:
        """Delegates to the shared digest module (simon/digest.py) — the
        same ground truth the agent's status-question grounding uses."""
        from . import digest
        return digest.activity_digest(hours)

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
            status = await asyncio.to_thread(
                agent.handle,
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
            result = await asyncio.to_thread(
                agent.handle,
                TASK_PROMPT.format(description=row["description"]))
            # COMPLETION GATE (Core 2.0 M3): a scheduled result that claims
            # completed work with zero receipts is a fabrication — deliver an
            # honest failure instead of a made-up report.
            looks_dishonest = getattr(agent, "_looks_dishonest", None)
            if (result and callable(looks_dishonest)
                    and not getattr(agent, "last_turn_tools", [])
                    and looks_dishonest(result)):
                logger.warning("scheduled task %d: claimed-action result "
                               "with zero receipts — gate retry", schedule_id)
                retry = await asyncio.to_thread(
                    agent.handle,
                    "SYSTEM GATE: your previous reply claimed completed "
                    "actions or retrieved content, but no tool was actually "
                    "called — so nothing happened. Either call the tools "
                    "now and report their real output, or state plainly "
                    "which step you could not perform and why.")
                if retry and (getattr(agent, "last_turn_tools", [])
                              or not looks_dishonest(retry)):
                    result = retry
                else:
                    result = ("I must be honest, sir: this scheduled task "
                              "claimed results it never actually produced "
                              "(no tool receipts), so I have discarded the "
                              "fabricated output rather than deliver it. "
                              "I shall retry on the next run.")
            if result:
                self._deliver(
                    row.get("session_id", ""),
                    f"Scheduled task — «{row['description'][:70]}»"
                    f"\n\n{result}")
        except Exception:
            logger.exception("scheduled task %d failed", schedule_id)

    async def _maintenance_pass(self) -> None:
        """2-hourly self-maintenance: health, evals, hygiene — one digest."""
        # Interactive priority: skip this tick while the owner is talking;
        # the next tick is at most 2 hours away.
        from . import agent as agent_mod
        if agent_mod.interactive_session_active():
            logger.info("maintenance deferred — interactive session active")
            return
        from . import maintenance
        from .agent import Agent
        from .tools import build_default_registry

        def _factory(session_id: str) -> Agent:
            return Agent(self.settings,
                         registry=build_default_registry(self.settings),
                         session_id=session_id, interface="maintenance")

        try:
            digest = await asyncio.to_thread(
                maintenance.run_maintenance, self.settings, None,
                maintenance._probe_port, maintenance._restart_service,
                _factory)
            self.notify(f"🔧 {digest}")
        except Exception:
            logger.exception("maintenance pass failed")

    async def _weekly_suggestions(self) -> None:
        """Analyse recent usage and propose automations the user didn't ask for."""
        if self.agent_factory is None:
            return
        try:
            agent = self.agent_factory()
            suggestions = await asyncio.to_thread(agent.handle, SUGGEST_PROMPT)
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
