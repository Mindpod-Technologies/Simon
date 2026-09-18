"""Job tools: let the agent accept long-running background assignments."""

from __future__ import annotations

import logging

from .. import jobs
from .base import Tool

log = logging.getLogger(__name__)


def _start_job(description: str) -> str:
    description = (description or "").strip()
    if len(description) < 10:
        return ("Error: job description too short — describe the task fully, "
                "including what the final deliverable should be.")
    from ..context import current_session
    job_id = jobs.create_job(description,
                             origin_session=current_session.get())
    log.info("job %d created via tool: %.80s", job_id, description)
    return (f"Job #{job_id} queued. It will run in the background and the "
            f"result will be delivered to the user when complete. "
            f"Acknowledge this to the user in character and carry on.")


def _job_status(job_id: int = 0) -> str:
    if job_id:
        job = jobs.get_job(int(job_id))
        if not job:
            return f"Error: no job with id {job_id}."
        lines = [f"Job #{job['id']} — {job['status']}",
                 f"Task: {job['description']}",
                 f"Created: {job['created_at']}"]
        if job.get("finished_at"):
            lines.append(f"Finished: {job['finished_at']}")
        if job.get("result"):
            lines.append(f"Result: {job['result'][:1500]}")
        if job.get("error"):
            lines.append(f"Error: {job['error']}")
        return "\n".join(lines)
    recent = jobs.list_jobs(limit=5)
    if not recent:
        return "No jobs on record."
    lines = ["Recent jobs (newest first):"]
    for j in recent:
        lines.append(f"#{j['id']} [{j['status']}] {j['description'][:70]} "
                     f"({j['created_at']})")
    return "\n".join(lines)


def _cancel_job(job_id: int) -> str:
    if jobs.cancel_job(int(job_id)):
        return f"Job #{job_id} cancelled."
    job = jobs.get_job(int(job_id))
    if not job:
        return f"Error: no job with id {job_id}."
    return (f"Job #{job_id} is already {job['status']} — only pending jobs "
            f"can be cancelled.")


def register_job_tools(registry, settings) -> None:
    """Register background-job tools on ``registry``."""
    registry.register(Tool(
        name="start_job",
        description=(
            "Queue a long-running background job (research, report writing, "
            "multi-step builds, anything taking more than a quick answer). "
            "The job runs autonomously and its result is delivered to the "
            "user when complete. Use this INSTEAD of attempting big tasks "
            "synchronously. Not for quick questions or one-line answers."),
        parameters={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": ("Full, self-contained task description, "
                                    "including what the final deliverable "
                                    "should contain."),
                },
            },
            "required": ["description"],
        },
        func=_start_job,
    ))
    registry.register(Tool(
        name="job_status",
        description=(
            "Check background jobs: pass a job_id for full details (status, "
            "result, error), or omit it to list the 5 most recent jobs."),
        parameters={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "Job id to inspect; 0 or omitted = list recent jobs.",
                },
            },
        },
        func=_job_status,
    ))
    registry.register(Tool(
        name="cancel_job",
        description="Cancel a pending background job that has not started yet.",
        parameters={
            "type": "object",
            "properties": {
                "job_id": {"type": "integer", "description": "Job id to cancel."},
            },
            "required": ["job_id"],
        },
        func=_cancel_job,
    ))
