"""Customer portal: license-key auth, tenant stats, support cases."""

import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from billing import keys as billing_keys, store as billing_store
from portal import cases, server, stats
from simon import licensing


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setenv("SIMON_LICENSE_PRIVATE_KEY",
                       priv.private_bytes_raw().hex())
    monkeypatch.setattr(licensing, "_VENDOR_PUBLIC_KEY_HEX",
                        priv.public_key().public_bytes_raw().hex())
    billing_db = str(tmp_path / "billing.db")
    key = billing_keys.make_key("pro", "ops@acme.com", "2099-01-01")
    billing_store.record_issued("cs_1", "ops@acme.com", "pro", key,
                                path=billing_db)
    portal_db = str(tmp_path / "portal.db")
    monkeypatch.setenv("PORTAL_DB", portal_db)
    app = server.create_app(billing_db=billing_db)
    return TestClient(app), key, tmp_path


def test_login_with_license_key(stack):
    client, key, _ = stack
    r = client.post("/login", data={"key": key}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    r = client.get("/", cookies=r.cookies)
    assert r.status_code == 200 and "ops@acme.com" in r.text
    assert "pro" in r.text


def test_login_rejects_unknown_key(stack):
    client, _, _ = stack
    r = client.post("/login", data={"key": "SIMON-pro-bogus.xx"})
    assert "isn't on record" in r.text


def test_dashboard_requires_auth(stack):
    client, _, _ = stack
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_support_case_create_and_list_own_only(stack):
    client, key, _ = stack
    r = client.post("/login", data={"key": key}, follow_redirects=False)
    cookies = r.cookies
    r = client.post("/cases", data={
        "subject": "Slack bot stopped answering",
        "body": "Since this morning no replies in #general at all."},
        cookies=cookies, follow_redirects=False)
    assert r.status_code == 303
    mine = cases.list_cases("ops@acme.com")
    assert len(mine) == 1 and mine[0]["case_ref"].startswith("SC-")
    # another customer sees nothing
    assert cases.list_cases("someone@else.com") == []
    # and cannot read my case directly
    ref = mine[0]["case_ref"]
    assert cases.get_case(ref, "someone@else.com") is None
    assert cases.get_case(ref, "ops@acme.com")["subject"].startswith("Slack")


def test_case_validation(stack):
    client, key, _ = stack
    r = client.post("/login", data={"key": key}, follow_redirects=False)
    r = client.post("/cases", data={"subject": "hi", "body": "x"},
                    cookies=r.cookies)
    assert r.status_code == 400


def test_case_status_lifecycle(tmp_path):
    db = str(tmp_path / "portal.db")
    ref = cases.create_case("ops@acme.com", "pro", "Setup help",
                            "Need onboarding call please.", path=db)
    assert cases.set_status(ref, "in_progress", path=db)
    assert cases.set_status(ref, "resolved", path=db)
    assert cases.get_case(ref, "ops@acme.com", path=db)["status"] == "resolved"
    with pytest.raises(ValueError):
        cases.set_status(ref, "weird", path=db)


# --- stats aggregation -------------------------------------------------------

def _seed_tenant_db(tmp_path):
    d = tmp_path / "tenants" / "acme"
    (d / "data").mkdir(parents=True)
    db = d / "data" / "simon.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
    CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT NOT NULL,
        kind TEXT NOT NULL, interface TEXT DEFAULT '', session_id TEXT DEFAULT '',
        model TEXT DEFAULT '', route_reason TEXT DEFAULT '',
        latency_ms INTEGER DEFAULT 0, detail TEXT DEFAULT '');
    CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT,
        created_at TEXT DEFAULT (datetime('now')));
    CREATE TABLE schedules (id INTEGER PRIMARY KEY, active INTEGER DEFAULT 1);
    CREATE TABLE approvals (id INTEGER PRIMARY KEY, status TEXT,
        created_at TEXT DEFAULT (datetime('now')));
    """)
    conn.executemany(
        "INSERT INTO events (ts, kind, interface, model, latency_ms)"
        " VALUES (datetime('now'), 'turn', ?, ?, ?)",
        [("web", "gpt-oss:20b", 12000), ("telegram", "qwen3:8b", 3000),
         ("web", "kimi-k3", 9000)])
    conn.execute("INSERT INTO jobs (status) VALUES ('done')")
    conn.execute("INSERT INTO jobs (status) VALUES ('done')")
    conn.execute("INSERT INTO jobs (status) VALUES ('failed')")
    conn.execute("INSERT INTO schedules (active) VALUES (1)")
    conn.execute("INSERT INTO approvals (status) VALUES ('pending')")
    conn.commit()
    conn.close()
    return str(d)


def test_tenant_stats_aggregates(tmp_path):
    d = _seed_tenant_db(tmp_path)
    st = stats.tenant_stats(d)
    assert st["hosted"] is True
    assert st["turns_total"] == 3
    assert st["turns_by_channel"] == {"web": 2, "telegram": 1}
    assert st["turns_by_model"]["kimi-k3"] == 1
    assert st["avg_latency_s"] == 8.0
    assert st["jobs"] == {"done": 2, "failed": 1}
    assert st["automations_active"] == 1
    assert st["approvals"]["pending"] == 1
    assert st["last_active"]


def test_tenant_stats_missing_db_is_honest_empty(tmp_path):
    st = stats.tenant_stats(str(tmp_path / "nope"))
    assert st == {"hosted": False}


def test_self_host_dashboard_shows_license_only(stack, monkeypatch):
    client, key, _ = stack
    monkeypatch.setenv("SIMON_CLOUD_ROOT", "/nonexistent")
    r = client.post("/login", data={"key": key}, follow_redirects=False)
    r = client.get("/", cookies=r.cookies)
    assert "self-hosted" in r.text and "never phone home" in r.text
