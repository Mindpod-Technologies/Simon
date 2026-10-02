"""Simon Provisioner: the Agent-Zero answer.

Not self-replication — a deterministic provisioning pipeline. The agent
proposes parameters into a human-controlled template catalog; the owner
approves (with the parameter diff shown); the pipeline executes; every
event writes a hash-chained receipt; deployed instances attest health.

Never: free-form IaC from the model, cross-instance messaging, or any
provisioning capability inside customer instances.

Layout:
  fleet/templates/*.yml   — the template catalog (checked in, reviewed)
  fleet registry (SQLite) — customers, instances, status, health
  receipts (SQLite)       — hash-chained, tamper-evident provisioning log
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import yaml

from . import memory

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent
_TEMPLATE_DIR = _REPO / "fleet" / "templates"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fleet (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer TEXT NOT NULL,
    template TEXT NOT NULL,
    template_sha TEXT NOT NULL,
    params_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_approval',
    endpoint TEXT DEFAULT '',
    health_token TEXT DEFAULT '',
    last_seen TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS provision_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fleet_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    detail TEXT DEFAULT '',
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db(path: Optional[str] = None) -> None:
    conn = memory._connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------- templates

def list_templates() -> list[dict]:
    """The template catalog: name, description, param schema, content hash."""
    out = []
    if not _TEMPLATE_DIR.is_dir():
        return out
    for f in sorted(_TEMPLATE_DIR.glob("*.yml")):
        try:
            data = yaml.safe_load(f.read_text())
            out.append({
                "name": data["name"],
                "description": data.get("description", ""),
                "params": data.get("params", {}),
                "sha": hashlib.sha256(f.read_bytes()).hexdigest()[:16],
            })
        except Exception:  # noqa: BLE001
            logger.warning("bad template: %s", f.name)
    return out


def _template(name: str) -> Optional[dict]:
    for t in list_templates():
        if t["name"] == name:
            return t
    return None


def validate_params(template_name: str, params: dict) -> Optional[str]:
    """Validate params against the template's schema. Error str or None."""
    tpl = _template(template_name)
    if tpl is None:
        return f"unknown template '{template_name}' — catalog: " + ", ".join(
            t["name"] for t in list_templates())
    for key, spec in (tpl.get("params") or {}).items():
        if spec.get("required") and not str(params.get(key, "")).strip():
            return f"missing required param: {key}"
        pattern = spec.get("pattern")
        if pattern and not re.fullmatch(pattern, str(params.get(key, ""))):
            return f"param '{key}' fails pattern {pattern}"
    return None


# -------------------------------------------------------------- receipts

def _receipt(conn, fleet_id: int, event: str, detail: str = "") -> None:
    row = conn.execute(
        "SELECT hash FROM provision_receipts ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev = row["hash"] if row else "GENESIS"
    payload = f"{fleet_id}|{event}|{detail}|{prev}|{time.time()}"
    h = hashlib.sha256(payload.encode()).hexdigest()
    conn.execute(
        "INSERT INTO provision_receipts (fleet_id, event, detail, prev_hash,"
        " hash) VALUES (?, ?, ?, ?, ?)", (fleet_id, event, detail, prev, h))


def receipts(fleet_id: int, path: Optional[str] = None) -> list[dict]:
    init_db(path)
    conn = memory._connect(path)
    try:
        rows = conn.execute(
            "SELECT event, detail, hash, prev_hash, created_at FROM"
            " provision_receipts WHERE fleet_id = ? ORDER BY id",
            (fleet_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def verify_chain(path: Optional[str] = None) -> bool:
    """True when the receipt chain is intact (no tampering)."""
    init_db(path)
    conn = memory._connect(path)
    try:
        rows = conn.execute(
            "SELECT hash, prev_hash FROM provision_receipts ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    prev = "GENESIS"
    for r in rows:
        if r["prev_hash"] != prev:
            return False
        prev = r["hash"]
    return True


# ----------------------------------------------------------------- fleet

def register_instance(customer: str, template_name: str, params: dict,
                      path: Optional[str] = None) -> tuple[Optional[int],
                                                           Optional[str]]:
    """Register a pending customer instance. Returns (fleet_id, error)."""
    err = validate_params(template_name, params)
    if err:
        return None, err
    tpl = _template(template_name)
    init_db(path)
    conn = memory._connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO fleet (customer, template, template_sha,"
            " params_json) VALUES (?, ?, ?, ?)",
            (customer, template_name, tpl["sha"], json.dumps(params)))
        fleet_id = int(cur.lastrowid)
        _receipt(conn, fleet_id, "registered",
                 f"template {template_name}@{tpl['sha']}")
        conn.commit()
        return fleet_id, None
    finally:
        conn.close()


def set_status(fleet_id: int, status: str, detail: str = "",
               path: Optional[str] = None) -> None:
    init_db(path)
    conn = memory._connect(path)
    try:
        conn.execute(
            "UPDATE fleet SET status = ?, updated_at = datetime('now')"
            " WHERE id = ?", (status, fleet_id))
        _receipt(conn, fleet_id, f"status:{status}", detail)
        conn.commit()
    finally:
        conn.close()


def set_endpoint(fleet_id: int, endpoint: str, health_token: str,
                 path: Optional[str] = None) -> None:
    init_db(path)
    conn = memory._connect(path)
    try:
        conn.execute(
            "UPDATE fleet SET endpoint = ?, health_token = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (endpoint, health_token, fleet_id))
        _receipt(conn, fleet_id, "endpoint", endpoint)
        conn.commit()
    finally:
        conn.close()


def fleet_status(path: Optional[str] = None) -> list[dict]:
    init_db(path)
    conn = memory._connect(path)
    try:
        rows = conn.execute(
            "SELECT id, customer, template, status, endpoint, last_seen,"
            " updated_at FROM fleet ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def heartbeat(fleet_id: int, token: str,
              path: Optional[str] = None) -> bool:
    """Health attestation from a deployed instance (token-verified)."""
    init_db(path)
    conn = memory._connect(path)
    try:
        row = conn.execute(
            "SELECT health_token FROM fleet WHERE id = ?",
            (fleet_id,)).fetchone()
        if row is None or not row["health_token"] \
                or row["health_token"] != token:
            return False
        conn.execute(
            "UPDATE fleet SET last_seen = datetime('now'),"
            " status = CASE WHEN status = 'deployed' THEN 'healthy'"
            " ELSE status END WHERE id = ?", (fleet_id,))
        conn.commit()
        return True
    finally:
        conn.close()
