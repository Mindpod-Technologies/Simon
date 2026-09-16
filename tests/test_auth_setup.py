"""Tests for owner auth (simon/auth.py) and the first-run setup wizard
(simon/setup_api.py + the auth gate in simon/interfaces/web.py)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from simon import auth, memory, settings_api, setup_api
from simon.interfaces.web import create_app


class FakeSettings:
    simon_web_default_session = ""
    simon_allow_shell = False

    def __init__(self, workspace):
        self.simon_workspace_dir = str(workspace)

    def model_copy(self, update=None):
        import copy
        clone = copy.copy(self)
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone

    def canonical_session(self, interface, supplied):
        return supplied


@pytest.fixture(autouse=True)
def _reset_lockout():
    auth._failures = 0
    auth._locked_until = 0.0
    yield
    auth._failures = 0
    auth._locked_until = 0.0


def _patch_env(monkeypatch, tmp_path, values: dict[str, str]):
    """Point the live .env reads/writes at a tmp file."""
    env = tmp_path / ".env"
    env.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    real_read = settings_api.read_env
    real_write = settings_api.write_env
    monkeypatch.setattr(settings_api, "read_env",
                        lambda path=env: real_read(env))
    monkeypatch.setattr(settings_api, "write_env",
                        lambda updates, path=env: real_write(updates, env))
    return env


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    app = create_app(FakeSettings(tmp_path / "workspace"))
    return TestClient(app)


# ------------------------------------------------------------------ auth core

def test_hash_verify_roundtrip():
    stored = auth.hash_password("correct horse battery staple")
    assert stored.startswith("pbkdf2$200000$")
    assert auth.verify_password("correct horse battery staple", stored)
    assert not auth.verify_password("wrong", stored)
    assert not auth.verify_password("anything", "garbage")


def test_session_roundtrip_and_tamper():
    stored = auth.hash_password("hunter2-hunter2")
    token = auth.make_session(stored)
    assert auth.check_session(token, stored)
    assert not auth.check_session(token + "x", stored)          # tampered sig
    assert not auth.check_session("", stored)
    assert not auth.check_session(token, "")                    # no password
    other = auth.hash_password("different-password")
    assert not auth.check_session(token, other)                 # pw change kills it


def test_session_expiry():
    stored = auth.hash_password("hunter2-hunter2")
    past = str(int(time.time()) - 10)
    import hashlib, hmac
    sig = hmac.new(stored.encode(), past.encode(),
                   hashlib.sha256).hexdigest()
    assert not auth.check_session(f"{past}.{sig}", stored)


def test_lockout_after_five_failures():
    for _ in range(5):
        auth.record_login(False)
    assert auth.login_throttled()
    auth.record_login(True)  # success clears (post-lockout bookkeeping)
    auth._locked_until = 0.0
    assert not auth.login_throttled()


def test_is_public_path():
    assert auth.is_public_path("/login")
    assert auth.is_public_path("/static/app.js")
    assert not auth.is_public_path("/api/chat")
    assert not auth.is_public_path("/api/setup/save")           # not public…
    assert auth.is_public_path("/api/setup/save", first_run=True)  # …except first run


# -------------------------------------------------------------------- the gate

def test_first_run_funnels_to_setup(client, monkeypatch, tmp_path):
    _patch_env(monkeypatch, tmp_path, {})
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup"
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 401
    assert r.json().get("setup_required") is True
    # Wizard itself is reachable without any session
    assert client.get("/setup").status_code == 200
    assert client.get("/api/setup/status").status_code == 200


def test_password_set_requires_login(client, monkeypatch, tmp_path):
    stored = auth.hash_password("owner-pass-123")
    _patch_env(monkeypatch, tmp_path, {"SIMON_OWNER_PASSWORD_HASH": stored})
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert client.post("/api/facts", json={"key": "k", "value": "v"}
                       ).status_code == 401
    assert client.get("/login").status_code == 200


def test_login_sets_cookie_and_unlocks(client, monkeypatch, tmp_path):
    stored = auth.hash_password("owner-pass-123")
    _patch_env(monkeypatch, tmp_path, {"SIMON_OWNER_PASSWORD_HASH": stored})
    r = client.post("/api/login", json={"password": "nope-nope-nope"})
    assert r.status_code == 401
    r = client.post("/api/login", json={"password": "owner-pass-123"})
    assert r.status_code == 200
    assert auth.COOKIE_NAME in r.cookies
    # TestClient keeps the cookie — protected endpoints now work
    assert client.post("/api/facts", json={"key": "k", "value": "v"}
                       ).status_code == 200
    # Tampered cookie is rejected
    client.cookies.set(auth.COOKIE_NAME, "1.forged")
    assert client.post("/api/facts", json={"key": "k", "value": "v"}
                       ).status_code == 401


def test_login_lockout_http(client, monkeypatch, tmp_path):
    stored = auth.hash_password("owner-pass-123")
    _patch_env(monkeypatch, tmp_path, {"SIMON_OWNER_PASSWORD_HASH": stored})
    for _ in range(5):
        client.post("/api/login", json={"password": "bad-password"})
    r = client.post("/api/login", json={"password": "owner-pass-123"})
    assert r.status_code == 429  # even the right password is locked out


def test_logout_clears_session(client, monkeypatch, tmp_path):
    stored = auth.hash_password("owner-pass-123")
    _patch_env(monkeypatch, tmp_path, {"SIMON_OWNER_PASSWORD_HASH": stored})
    client.post("/api/login", json={"password": "owner-pass-123"})
    assert client.post("/api/logout").status_code == 200
    # Cookie cleared client-side; a stale one must not validate anyway
    client.cookies.set(auth.COOKIE_NAME, "")
    assert client.post("/api/facts", json={"key": "k", "value": "v"}
                       ).status_code == 401


# --------------------------------------------------------------- setup wizard

def test_setup_save_validates(client, monkeypatch, tmp_path):
    env = _patch_env(monkeypatch, tmp_path, {})
    # too-short password
    r = client.post("/api/setup/save",
                    json={"password": "short", "updates": {}})
    assert r.status_code == 400
    # non-whitelisted key
    r = client.post("/api/setup/save",
                    json={"updates": {"EVIL_KEY": "x"}})
    assert r.status_code == 400
    assert "not allowed" in r.json()["error"]


def test_setup_save_writes_password_and_keys(client, monkeypatch, tmp_path):
    env = _patch_env(monkeypatch, tmp_path, {})
    r = client.post("/api/setup/save", json={
        "password": "owner-pass-123",
        "updates": {"LLM_MODEL": "gpt-oss:20b",
                    "TELEGRAM_BOT_TOKEN": "123:abc"},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["password_set"] is True
    values, _ = settings_api.read_env(env)
    assert values["LLM_MODEL"] == "gpt-oss:20b"
    assert values["TELEGRAM_BOT_TOKEN"] == "123:abc"
    assert auth.verify_password("owner-pass-123",
                                values["SIMON_OWNER_PASSWORD_HASH"])
    # password can only be set once — after first run the gate closes
    # /api/setup/save entirely (401), and the endpoint itself refuses too
    r = client.post("/api/setup/save", json={"password": "another-pass-1"})
    assert r.status_code in (400, 401)


def test_setup_save_blocked_after_first_run(client, monkeypatch, tmp_path):
    """Once a password exists the gate closes /api/setup/save entirely."""
    stored = auth.hash_password("owner-pass-123")
    _patch_env(monkeypatch, tmp_path, {"SIMON_OWNER_PASSWORD_HASH": stored})
    r = client.post("/api/setup/save", json={"updates": {"LLM_MODEL": "x"}})
    assert r.status_code == 401  # gated, not reachable without a session


def test_setup_pull_validates_model_name(client, monkeypatch, tmp_path):
    _patch_env(monkeypatch, tmp_path, {})
    r = client.post("/api/setup/pull_model",
                    json={"model": "bad name; rm -rf /"})
    assert r.status_code == 400


# ---------------------------------------------------------------- monitor app

def test_monitor_gated_like_main_app(monkeypatch, tmp_path):
    from simon import monitor_app
    stored = auth.hash_password("owner-pass-123")
    monkeypatch.setattr(
        settings_api, "read_env",
        lambda path=None: ({"SIMON_OWNER_PASSWORD_HASH": stored}, []))
    c = TestClient(monitor_app.create_app())
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/login")
    assert c.get("/api/stats").status_code == 401
    # Valid session cookie (shared with the main app) unlocks it
    c.cookies.set(auth.COOKIE_NAME, auth.make_session(stored))
    assert c.get("/").status_code == 200
    assert c.get("/api/stats").status_code == 200


def test_monitor_first_run_points_at_setup(monkeypatch, tmp_path):
    from simon import monitor_app
    monkeypatch.setattr(settings_api, "read_env",
                        lambda path=None: ({}, []))
    c = TestClient(monitor_app.create_app())
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/setup")
