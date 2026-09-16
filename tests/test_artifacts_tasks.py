"""Tests for artifacts, charts, tasks and the facts API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from simon import artifacts, charts, memory, tasks
from simon.interfaces.web import create_app


class FakeSettings:
    def __init__(self, workspace):
        self.simon_workspace_dir = str(workspace)

    def model_copy(self, update=None):
        import copy
        clone = copy.copy(self)
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone


@pytest.fixture()
def settings(tmp_path):
    return FakeSettings(tmp_path / "workspace")


# ------------------------------------------------------------------ artifacts

def test_list_artifacts_skips_internals(settings):
    root = artifacts.workspace_root(settings)
    (root / "documents").mkdir(parents=True)
    (root / "documents" / "report.md").write_text("# hi")
    (root / "charts").mkdir()
    (root / "charts" / "c.png").write_bytes(b"png")
    (root / "uploads").mkdir()
    (root / "uploads" / "up.pdf").write_bytes(b"pdf")
    (root / "uploads" / "up.pdf.txt").write_text("extracted")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "x.txt").write_text("secret")

    files = artifacts.list_artifacts(settings)
    paths = [f["path"] for f in files]
    assert "documents/report.md" in paths
    assert "charts/c.png" in paths
    assert not any(p.startswith("uploads/") for p in paths)
    assert not any(p.startswith(".hidden") for p in paths)
    kinds = {f["path"]: f["kind"] for f in files}
    assert kinds["charts/c.png"] == "chart"
    assert kinds["documents/report.md"] == "document"


def test_resolve_artifact_blocks_traversal(settings):
    root = artifacts.workspace_root(settings)
    (root / "a.txt").write_text("ok")
    assert artifacts.resolve_artifact(settings, "a.txt") is not None
    assert artifacts.resolve_artifact(settings, "../memory.py") is None
    assert artifacts.resolve_artifact(settings, "missing.txt") is None


# --------------------------------------------------------------------- charts

def test_create_chart_writes_png_with_marker(settings):
    out = charts._create_chart(
        "Revenue", "bar", ["Q1", "Q2"],
        [{"name": "2026", "values": [3, 4]}], settings=settings)
    assert "[artifact:charts/" in out
    pngs = list(charts.charts_dir(settings).glob("*.png"))
    assert len(pngs) == 1
    assert pngs[0].stat().st_size > 1000  # a real rendered image


def test_create_chart_validates_input(settings):
    assert "Error" in charts._create_chart(
        "", "bar", ["a"], [{"values": [1]}], settings=settings)
    assert "Error" in charts._create_chart(
        "t", "radar", ["a"], [{"values": [1]}], settings=settings)
    assert "Error" in charts._create_chart(
        "t", "bar", ["a", "b"], [{"values": [1]}], settings=settings)


def test_create_chart_pie(settings):
    out = charts._create_chart(
        "Share", "pie", ["A", "B", "C"],
        [{"values": [50, 30, 20]}], settings=settings)
    assert "[artifact:charts/" in out


# ---------------------------------------------------------------------- tasks

def test_task_lifecycle(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    task = tasks.create_task("Website Redesign", settings)
    assert task["slug"] == "website-redesign"
    assert task["session_id"] == "task-website-redesign"
    assert tasks.workspace_for("website-redesign", settings).is_dir()

    listed = tasks.list_tasks(settings)
    assert listed[0]["slug"] == "website-redesign"

    # Idempotent re-create
    again = tasks.create_task("Website Redesign", settings)
    assert again["slug"] == "website-redesign"

    ws = tasks.session_workspace("task-website-redesign", settings)
    assert ws is not None and ws.name == "website-redesign"
    assert tasks.session_workspace("owner", settings) is None

    assert tasks.delete_task("website-redesign", settings)
    assert tasks.list_tasks(settings) == []
    assert not (tasks.tasks_root(settings) / "website-redesign").exists()
    assert tasks.delete_task("website-redesign", settings) is False


def test_task_rejects_empty_name(settings):
    with pytest.raises(ValueError):
        tasks.create_task("   ", settings)


# -------------------------------------------------------------- web endpoints

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()

    # Auth gate: pretend the owner password is set and hold a valid session
    from simon import auth, settings_api
    stored = auth.hash_password("test-owner-pass")
    real_read = settings_api.read_env
    monkeypatch.setattr(
        settings_api, "read_env",
        lambda path=None: ({"SIMON_OWNER_PASSWORD_HASH": stored}, []))

    class S(FakeSettings):
        simon_web_default_session = ""
        simon_allow_shell = False

        def canonical_session(self, interface, supplied):
            return supplied

    s = S(tmp_path / "workspace")
    app = create_app(s)
    c = TestClient(app)
    c.cookies.set(auth.COOKIE_NAME, auth.make_session(stored))
    return c, s


def test_facts_endpoints(client):
    c, _ = client
    assert c.post("/api/facts", json={
        "key": "Favourite Colour", "value": "teal"}).status_code == 200
    data = c.get("/api/facts").json()
    assert any(f["key"] == "favourite_colour" and f["value"] == "teal"
               for f in data["facts"])
    assert c.delete("/api/facts/favourite_colour").status_code == 200
    assert c.delete("/api/facts/favourite_colour").status_code == 404


def test_tasks_endpoints(client):
    c, _ = client
    res = c.post("/api/tasks", json={"name": "Q4 Plan"})
    assert res.status_code == 200
    slug = res.json()["slug"]
    assert slug == "q4-plan"
    listed = c.get("/api/tasks").json()["tasks"]
    assert any(t["slug"] == slug for t in listed)
    assert c.delete(f"/api/tasks/{slug}").status_code == 200
    assert c.delete(f"/api/tasks/{slug}").status_code == 404


def test_artifacts_endpoints(client):
    c, s = client
    root = artifacts.workspace_root(s)
    (root / "charts").mkdir(parents=True)
    (root / "charts" / "x.png").write_bytes(b"\x89PNG fake")
    assert any(f["path"] == "charts/x.png"
               for f in c.get("/api/artifacts").json()["files"])
    res = c.get("/api/artifacts/charts/x.png")
    assert res.status_code == 200
    assert res.content == b"\x89PNG fake"
    assert c.get("/api/artifacts/../secret").status_code in {404, 422}


def test_history_endpoint(client):
    c, _ = client
    memory.add_message("sess-1", "user", "hello")
    memory.add_message("sess-1", "assistant", "good day")
    data = c.get("/api/history", params={"session_id": "sess-1"}).json()
    assert data["session_id"] == "sess-1"
    assert [m["content"] for m in data["messages"]] == ["hello", "good day"]


def test_task_session_scopes_artifacts(client):
    c, s = client
    c.post("/api/tasks", json={"name": "Scoped"})
    task_ws = tasks.workspace_for("scoped", s)
    (task_ws / "only-here.txt").write_text("scoped file")
    data = c.get("/api/artifacts",
                 params={"session_id": "task-scoped"}).json()
    assert any(f["path"] == "only-here.txt" for f in data["files"])
    # Main workspace does NOT see the task file
    main = c.get("/api/artifacts").json()
    assert not any(f["path"] == "only-here.txt" for f in main["files"])


def test_chart_and_document_filenames_have_no_spaces(settings):
    """Spaces in filenames break the [artifact:path] marker (single-token
    path) — created artifact names must be space-free."""
    out = charts._create_chart(
        "Launch Budget", "bar", ["A"], [{"values": [1]}], settings=settings)
    marker = out.split("[artifact:")[1].rstrip("]")
    assert " " not in marker
    from simon import docs
    out = docs._create_document("My Big Report", "content here",
                                settings=settings)
    marker = out.split("[artifact:")[1].rstrip("]")
    assert " " not in marker
