"""Background job harness: assign Simon long-running work, delivered later.

This is what turns Simon from a conversationalist into an employee: the user
assigns a big task ("research X and write me a report"), Simon acknowledges
immediately, a background worker executes it with a full agent (tools,
browser, sub-agents), and the finished result is delivered to the owner's
notification channel when done.

Storage: one ``jobs`` table in the same SQLite database as memory. The
worker is a single daemon thread (local models make true parallelism
pointless — one job at a time). Never raises: a crashed job is marked
'failed' and reported, the worker carries on.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from . import memory, obs

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    origin_interface TEXT DEFAULT '',
    origin_session TEXT DEFAULT '',
    result TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
"""

JOB_PROMPT = (
    "You have been assigned the following background job. Work on it "
    "autonomously and produce a COMPLETE, self-contained deliverable as "
    "your final reply — the user receives your final message directly, with "
    "no other context. Do not ask clarifying questions; make reasonable "
    "assumptions and state them. Use tools ONLY when they genuinely help "
    "(current facts, real files); general-knowledge tasks should be "
    "answered from your own expertise without any tool calls. Keep it under "
    "800 words unless the task genuinely demands more."
    "\n\nJOB: {description}"
)


# ------------------------------------------------------------------- store

def init_db(path: Optional[str] = None) -> None:
    conn = memory._connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def create_job(description: str, origin_interface: str = "",
               origin_session: str = "", path: Optional[str] = None) -> int:
    """Create a pending job and return its id."""
    init_db(path)
    conn = memory._connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO jobs (description, origin_interface, origin_session)"
            " VALUES (?, ?, ?)",
            (description, origin_interface, origin_session),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_job(job_id: int, path: Optional[str] = None) -> Optional[dict]:
    init_db(path)
    conn = memory._connect(path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?",
                           (job_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def list_jobs(limit: int = 10, status: Optional[str] = None,
              path: Optional[str] = None) -> list[dict]:
    """Most recent jobs first; optionally filter by status."""
    init_db(path)
    conn = memory._connect(path)
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def next_pending(path: Optional[str] = None) -> Optional[dict]:
    """Atomically claim the oldest pending job and mark it running."""
    init_db(path)
    conn = memory._connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "UPDATE jobs SET status = 'running',"
            " started_at = datetime('now') WHERE id = ?",
            (row["id"],))
        conn.commit()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_job(job_id: int, status: str, result: str = "", error: str = "",
               path: Optional[str] = None) -> None:
    conn = memory._connect(path)
    try:
        conn.execute(
            "UPDATE jobs SET status = ?, result = ?, error = ?,"
            " finished_at = datetime('now') WHERE id = ?",
            (status, result, error, job_id))
        conn.commit()
    finally:
        conn.close()


def cancel_job(job_id: int, path: Optional[str] = None) -> bool:
    """Cancel a pending job. Returns False if it already started/finished."""
    init_db(path)
    conn = memory._connect(path)
    try:
        cur = conn.execute(
            "UPDATE jobs SET status = 'cancelled',"
            " finished_at = datetime('now')"
            " WHERE id = ? AND status = 'pending'", (job_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def fail_stale_running(path: Optional[str] = None) -> int:
    """Mark orphaned 'running' jobs as failed (worker was restarted).

    Called at JobRunner startup: at that moment no job can legitimately be
    running in this process, so any 'running' row belongs to a dead worker.
    Failing them (rather than requeueing) avoids surprise re-deliveries.
    """
    init_db(path)
    conn = memory._connect(path)
    try:
        cur = conn.execute(
            "UPDATE jobs SET status = 'failed',"
            " error = 'interrupted by a Simon restart',"
            " finished_at = datetime('now') WHERE status = 'running'")
        conn.commit()
        if cur.rowcount:
            logger.info("marked %d stale running job(s) as failed",
                        cur.rowcount)
        return cur.rowcount
    finally:
        conn.close()


# ------------------------------------------------------------------ worker

class JobRunner:
    """Single-threaded background worker executing pending jobs.

    ``agent_factory`` receives the job id and must return a fresh Agent
    (per-job session, interface="job"). ``notify`` delivers the result to
    the owner. ``db_path`` overrides the database location (tests).
    """

    def __init__(self, settings: Any = None,
                 agent_factory: Optional[Callable[[int], Any]] = None,
                 notify: Optional[Callable[[str], None]] = None,
                 notify_for: Optional[Callable[[str, str], None]] = None,
                 poll_seconds: float = 5.0,
                 db_path: Optional[str] = None) -> None:
        self.agent_factory = agent_factory
        self.notify = notify or (lambda text: logger.info("notify: %s", text))
        self.notify_for = notify_for
        self.poll_seconds = poll_seconds
        self.db_path = db_path
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _deliver(self, session_id: str, text: str) -> None:
        """Per-person delivery when wired, else the owner channel."""
        if self.notify_for is not None:
            try:
                self.notify_for(session_id, text)
                return
            except Exception:  # noqa: BLE001
                logger.exception("notify_for failed; falling back to owner")
        self.notify(text)

    def start(self) -> None:
        """Start the polling loop in a daemon thread."""
        init_db(self.db_path)
        fail_stale_running(self.db_path)
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="simon-jobrunner")
        self._thread.start()
        logger.info("JobRunner started (poll every %.0fs)", self.poll_seconds)

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("JobRunner iteration failed")
            self._stop.wait(self.poll_seconds)

    def run_once(self) -> bool:
        """Claim and execute one pending job, if any. Returns True if one ran."""
        job = next_pending(self.db_path)
        if job is None:
            return False
        self._execute(job)
        return True

    def _execute(self, job: dict) -> None:
        job_id, description = job["id"], job["description"]
        origin = job.get("origin_session", "")
        logger.info("job %d started: %.80s", job_id, description)
        t0 = time.monotonic()
        self._deliver(origin, f"On it — picked up job #{job_id}: "
                              f"«{description[:80]}». "
                              f"I'll report back when it's done.")
        try:
            if self.agent_factory is None:
                raise RuntimeError("no agent_factory configured")
            agent = self.agent_factory(job_id)
            result = agent.handle(JOB_PROMPT.format(description=description))
            result = (result or "").strip()
            if getattr(agent, "last_turn_exhausted", False):
                raise RuntimeError(
                    "job agent exhausted its tool-call budget without "
                    "producing a deliverable")
            if not result:
                raise RuntimeError("job produced an empty result")
            finish_job(job_id, "done", result=result, path=self.db_path)
            duration = int(time.monotonic() - t0)
            obs.record_event("job", interface="job",
                             session_id=f"job-{job_id}", latency_ms=duration * 1000,
                             job_id=job_id, status="done",
                             description=description[:200],
                             result_len=len(result))
            self._deliver(origin, f"Job #{job_id} complete — "
                                  f"«{description[:80]}»\n\n{result}")
        except Exception as exc:  # noqa: BLE001 - a crashed job must not kill the worker
            logger.exception("job %d failed", job_id)
            finish_job(job_id, "failed", error=repr(exc), path=self.db_path)
            duration = int(time.monotonic() - t0)
            obs.record_event("job", interface="job",
                             session_id=f"job-{job_id}", latency_ms=duration * 1000,
                             job_id=job_id, status="failed",
                             description=description[:200], error=repr(exc))
            self._deliver(origin, f"My apologies — job #{job_id} "
                                  f"(«{description[:80]}») failed: {exc}")
