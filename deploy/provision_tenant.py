#!/usr/bin/env python3
"""provision_tenant.py — Simon Cloud tenant provisioning.

One customer = one isolated container = one subdomain.

Usage:
    python deploy/provision_tenant.py provision --email acme@corp.com --plan pro
    python deploy/provision_tenant.py list
    python deploy/provision_tenant.py stop    --slug acme
    python deploy/provision_tenant.py delete  --slug acme --yes

Environment (set on the host, e.g. /etc/simon-cloud.env):
    SIMON_CLOUD_DOMAIN      e.g. simonwork.app — tenants get <slug>.<domain>
    SIMON_CLOUD_ROOT        tenant home root (default: /opt/simon-cloud/tenants)
    SIMON_IMAGE             docker image (default: ghcr.io/mindpod-technologies/simon:latest)
    SIMON_CLOUD_PORT_BASE   first tenant port (default: 19000)
    SIMON_LICENSE_PRIVATE_KEY   mints each tenant's license key
    LLM_BASE_URL / LLM_MODEL / LLM_MODEL_FAST / LLM_API_KEY  hosted model pool
    SIMON_CLOUD_DRY_RUN=1   render everything, run nothing (tests/dev)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEMPLATE_DIR = Path(__file__).resolve().parent / "tenant"
STATE_FILE = "tenants.json"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def slugify(email: str) -> str:
    """Tenant slug from the customer email's domain (acme.com → acme)."""
    domain = email.split("@")[-1].lower()
    slug = re.sub(r"[^a-z0-9-]", "-", domain.split(".")[0])
    slug = slug.strip("-") or re.sub(r"[^a-z0-9-]", "-", email.lower())
    return slug[:40]


class Provisioner:
    def __init__(self, dry_run: bool = False, root: str = ""):
        self.domain = _env("SIMON_CLOUD_DOMAIN", "simonwork.app")
        self.root = Path(root or _env("SIMON_CLOUD_ROOT",
                                      "/opt/simon-cloud/tenants"))
        self.image = _env("SIMON_IMAGE",
                          "ghcr.io/mindpod-technologies/simon:latest")
        self.port_base = int(_env("SIMON_CLOUD_PORT_BASE", "19000"))
        self.dry_run = dry_run or _env("SIMON_CLOUD_DRY_RUN") == "1"

    # ---- state ------------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILE

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {"tenants": {}}

    def _save_state(self, state: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2))

    # ---- provisioning ------------------------------------------------------
    def provision(self, email: str, plan: str) -> dict:
        """Render + start an isolated tenant. Idempotent per slug."""
        if "@" not in email:
            raise ValueError(f"bad customer email: {email!r}")
        if plan not in ("pro", "business"):
            raise ValueError("plan must be pro or business")
        slug = slugify(email)
        state = self._load_state()
        existing = state["tenants"].get(slug)
        if existing:
            return {**existing, "note": "already provisioned"}

        port = self._next_port(state)
        license_key = self._mint_license(email, plan)
        owner_password = secrets.token_urlsafe(10)
        tenant_dir = self.root / slug
        self._render(tenant_dir, slug=slug, email=email, plan=plan,
                     port=port, license_key=license_key,
                     owner_password=owner_password)
        self._write_caddy_snippet(tenant_dir, slug, port)
        if not self.dry_run:
            self._compose(tenant_dir, "up", "-d")
        record = {
            "slug": slug, "email": email, "plan": plan, "port": port,
            "url": f"https://{slug}.{self.domain}",
            "owner_password": owner_password, "dir": str(tenant_dir),
        }
        state["tenants"][slug] = record
        self._save_state(state)
        return record

    def _next_port(self, state: dict) -> int:
        used = {t["port"] for t in state["tenants"].values()}
        port = self.port_base
        while port in used:
            port += 1
        return port

    def _mint_license(self, email: str, plan: str) -> str:
        from billing.keys import make_key, expiry_for
        return make_key(plan, email, expiry_for(plan))

    def _render(self, tenant_dir: Path, **vals) -> None:
        from simon.auth import hash_password
        tenant_dir.mkdir(parents=True, exist_ok=True)
        (tenant_dir / "data").mkdir(exist_ok=True)
        (tenant_dir / "workspace").mkdir(exist_ok=True)
        env_text = (TEMPLATE_DIR / "tenant.env.template").read_text()
        compose = (TEMPLATE_DIR / "docker-compose.tenant.yml").read_text()
        replacements = {
            "{{LICENSE_KEY}}": vals["license_key"],
            "{{PASSWORD_HASH}}": hash_password(vals["owner_password"]),
            "{{TENANT_SLUG}}": vals["slug"],
            "{{LLM_BASE_URL}}": _env("LLM_BASE_URL",
                                     "https://api.moonshot.ai/v1"),
            "{{LLM_MODEL}}": _env("LLM_MODEL", "kimi-k3"),
            "{{LLM_MODEL_FAST}}": _env("LLM_MODEL_FAST", "kimi-k3"),
            "{{LLM_API_KEY}}": _env("LLM_API_KEY", ""),
        }
        for k, v in replacements.items():
            env_text = env_text.replace(k, v)
        compose = compose.replace("${SIMON_IMAGE}", self.image)
        compose = compose.replace("${TENANT_PORT}", str(vals["port"]))
        (tenant_dir / ".env").write_text(env_text)
        (tenant_dir / "docker-compose.yml").write_text(compose)

    def _write_caddy_snippet(self, tenant_dir: Path, slug: str,
                             port: int) -> None:
        """Per-tenant Caddy include: subdomain → loopback port, auto-HTTPS."""
        snippet = (
            f"{slug}.{self.domain} {{\n"
            f"    reverse_proxy 127.0.0.1:{port}\n"
            f"}}\n"
        )
        (tenant_dir / "Caddyfile").write_text(snippet)
        # Operators import tenant Caddyfiles from the main Caddyfile:
        #   import /opt/simon-cloud/tenants/*/Caddyfile

    def _compose(self, tenant_dir: Path, *args: str) -> None:
        subprocess.run(["docker", "compose", *args], cwd=tenant_dir,
                       check=True)

    # ---- lifecycle ----------------------------------------------------------
    def list(self) -> list[dict]:
        return list(self._load_state()["tenants"].values())

    def stop(self, slug: str) -> None:
        record = self._require(slug)
        if not self.dry_run:
            self._compose(Path(record["dir"]), "stop")

    def delete(self, slug: str) -> None:
        record = self._require(slug)
        if not self.dry_run:
            self._compose(Path(record["dir"]), "down", "-v")
        shutil.rmtree(record["dir"], ignore_errors=True)
        state = self._load_state()
        state["tenants"].pop(slug, None)
        self._save_state(state)

    def _require(self, slug: str) -> dict:
        record = self._load_state()["tenants"].get(slug)
        if not record:
            raise ValueError(f"unknown tenant {slug!r}")
        return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Simon Cloud provisioning")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("provision")
    p.add_argument("--email", required=True)
    p.add_argument("--plan", default="pro", choices=["pro", "business"])
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("list")
    s = sub.add_parser("stop"); s.add_argument("--slug", required=True)
    d = sub.add_parser("delete"); d.add_argument("--slug", required=True)
    d.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    prov = Provisioner(dry_run=getattr(args, "dry_run", False))
    if args.cmd == "provision":
        record = prov.provision(args.email, args.plan)
        print(json.dumps(record, indent=2))
    elif args.cmd == "list":
        print(json.dumps(prov.list(), indent=2))
    elif args.cmd == "stop":
        prov.stop(args.slug)
        print(f"stopped {args.slug}")
    elif args.cmd == "delete":
        if not args.yes:
            print("refusing without --yes", file=sys.stderr)
            sys.exit(2)
        prov.delete(args.slug)
        print(f"deleted {args.slug}")


if __name__ == "__main__":
    main()
