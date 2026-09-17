"""Tests for the background job harness (simon/jobs.py + job tools)."""

from __future__ import annotations

import pytest

from simon import jobs


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "test.db")


# ------------------------------------------------------------------- store

def test_create_and_get_job(db):
    job_id = jobs.create_job("Write the quarterly report", path=db)
    job = jobs.get_job(job_id, path=db)
    assert job is not None
    assert job["description"] == "Write the quarterly report"
    assert job["status"] == "pending"


def test_next_pending_claims_oldest_first(db):
    first = jobs.create_job("first task", path=db)
    jobs.create_job("second task", path=db)
    claimed = jobs.next_pending(path=db)
    assert claimed["id"] == first
    assert jobs.get_job(first, path=db)["status"] == "running"
    # Second claim gets the second job; third claim finds nothing.
    assert jobs.next_pending(path=db)["description"] == "second task"
    assert jobs.next_pending(path=db) is None


def test_finish_job_records_result(db):
    job_id = jobs.create_job("task", path=db)
    jobs.next_pending(path=db)
    jobs.finish_job(job_id, "done", result="the deliverable", path=db)
    job = jobs.get_job(job_id, path=db)
    assert job["status"] == "done"
    assert job["result"] == "the deliverable"
    assert job["finished_at"]


def test_cancel_only_pending(db):
    job_id = jobs.create_job("task", path=db)
    assert jobs.cancel_job(job_id, path=db) is True
    assert jobs.cancel_job(job_id, path=db) is False  # already cancelled
    other = jobs.create_job("running task", path=db)
    jobs.next_pending(path=db)
    assert jobs.cancel_job(other, path=db) is False  # running, not pending


def test_fail_stale_running(db):
    orphan = jobs.create_job("orphaned task", path=db)
    jobs.next_pending(path=db)  # marks it 'running' with no worker
    assert jobs.fail_stale_running(path=db) == 1
    job = jobs.get_job(orphan, path=db)
    assert job["status"] == "failed"
    assert "restart" in job["error"]
    assert jobs.fail_stale_running(path=db) == 0  # nothing stale left


def test_list_jobs_filter(db):
    jobs.create_job("a", path=db)
    jobs.create_job("b", path=db)
    assert len(jobs.list_jobs(path=db)) == 2
    assert len(jobs.list_jobs(status="pending", path=db)) == 2
    assert jobs.list_jobs(status="done", path=db) == []


# ------------------------------------------------------------------ worker

class _FakeAgent:
    def __init__(self, reply="job deliverable", fail=False):
        self.reply = reply
        self.fail = fail

    def handle(self, prompt: str) -> str:
        if self.fail:
            raise RuntimeError("boom")
        return self.reply


def test_runner_executes_job_and_notifies(db):
    delivered = []
    runner = jobs.JobRunner(
        agent_factory=lambda job_id: _FakeAgent("the finished report"),
        notify=delivered.append, db_path=db)
    job_id = jobs.create_job("write a report", path=db)
    assert runner.run_once() is True
    job = jobs.get_job(job_id, path=db)
    assert job["status"] == "done"
    assert job["result"] == "the finished report"
    # Two pushes: pickup ("On it…") then completion with the deliverable.
    assert len(delivered) == 2
    assert "picked up" in delivered[0].lower()
    assert "the finished report" in delivered[1]
    assert runner.run_once() is False  # queue drained


def test_runner_marks_failed_job_and_carries_on(db):
    delivered = []
    runner = jobs.JobRunner(
        agent_factory=lambda job_id: _FakeAgent(fail=True),
        notify=delivered.append, db_path=db)
    job_id = jobs.create_job("doomed task", path=db)
    runner.run_once()
    job = jobs.get_job(job_id, path=db)
    assert job["status"] == "failed"
    assert "boom" in job["error"]
    assert "picked up" in delivered[0].lower()   # pickup push
    assert "failed" in delivered[-1].lower()     # failure push


def test_runner_rejects_empty_result(db):
    delivered = []
    runner = jobs.JobRunner(
        agent_factory=lambda job_id: _FakeAgent(reply=""),
        notify=delivered.append, db_path=db)
    job_id = jobs.create_job("empty task", path=db)
    runner.run_once()
    assert jobs.get_job(job_id, path=db)["status"] == "failed"


def test_runner_fails_exhausted_tool_loop(db):
    """An agent that exhausted its tool budget must fail the job, not
    deliver its apology as if it were the work product."""
    class Exhausted(_FakeAgent):
        last_turn_exhausted = True
    delivered = []
    runner = jobs.JobRunner(
        agent_factory=lambda job_id: Exhausted("I do apologise, sir..."),
        notify=delivered.append, db_path=db)
    job_id = jobs.create_job("spiralling task", path=db)
    runner.run_once()
    job = jobs.get_job(job_id, path=db)
    assert job["status"] == "failed"
    assert "exhausted" in job["error"]


def test_job_agent_prompt_is_self_contained(db):
    seen = []
    class Spy(_FakeAgent):
        def handle(self, prompt):
            seen.append(prompt)
            return "done"
    runner = jobs.JobRunner(agent_factory=lambda i: Spy(),
                            notify=lambda t: None, db_path=db)
    jobs.create_job("the actual task text", path=db)
    runner.run_once()
    assert "the actual task text" in seen[0]
    assert "self-contained" in seen[0]


# ------------------------------------------------------------------- tools

def test_start_job_tool_creates_job(db, monkeypatch):
    created = []
    monkeypatch.setattr(jobs, "create_job",
                        lambda desc, **kw: created.append(desc) or 42)
    from simon.tools.jobs_tool import _start_job
    out = _start_job("Research the market and write a report")
    assert "Job #42" in out
    assert created == ["Research the market and write a report"]


def test_start_job_tool_rejects_vague_description(monkeypatch):
    from simon.tools.jobs_tool import _start_job
    assert "Error" in _start_job("stuff")


def test_job_tools_register():
    from simon.tools import ToolRegistry
    from simon.tools.jobs_tool import register_job_tools
    registry = ToolRegistry()
    register_job_tools(registry, settings=None)
    assert "start_job" in registry
    assert "job_status" in registry
    assert "cancel_job" in registry
