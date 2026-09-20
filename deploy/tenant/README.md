# Simon Cloud — hosted single-tenant deployments

One customer = one isolated container = one subdomain. This is the
resale/hosting path; the self-hosted license path (DMG/EXE + key) is
unchanged and lives in the main repo.

## Architecture

```
Stripe checkout → billing webhook (:8792) → provision_tenant.py
                                              │
            /opt/simon-cloud/tenants/<slug>/  ├─ .env (license, password hash, tenant creds)
                                              ├─ docker-compose.yml (isolated, capped)
                                              ├─ data/ workspace/ (their memory & files)
                                              └─ Caddyfile → <slug>.<SIMON_CLOUD_DOMAIN>
```

## Host setup (once)

1. Docker + Caddy on the VPS.
2. DNS wildcard: `*.<domain>` → the VPS.
3. Main Caddyfile: `import /opt/simon-cloud/tenants/*/Caddyfile` — auto-HTTPS
   per tenant.
4. Host env (e.g. `/etc/simon-cloud.env`): `SIMON_CLOUD_DOMAIN`,
   `SIMON_LICENSE_PRIVATE_KEY`, `LLM_BASE_URL/LLM_MODEL/LLM_API_KEY`
   (hosted model pool), `SIMON_CLOUD_PROVISION=1` on the billing service.

## Daily ops

```bash
python deploy/provision_tenant.py provision --email ops@acme.com --plan pro
python deploy/provision_tenant.py list
python deploy/provision_tenant.py stop --slug acme
python deploy/provision_tenant.py delete --slug acme --yes
```

`provision` prints the tenant URL + generated owner password — deliver the
password to the customer through a secure channel (never in the same email
as anything else). On success the customer gets: license key email +
`https://<slug>.<domain>` asking them to sign in.

## Guardrails baked in

- Every tenant: `SIMON_REQUIRE_LICENSE=true`, approvals ON, shell OFF.
- Containers: loopback-only ports, 1 GB RAM / 1.5 CPU / 256 pids caps,
  `no-new-privileges`, own volumes — no tenant sees another's memory.
- Provisioning is idempotent per customer domain and never blocks a Stripe
  fulfillment (a failure logs and the license key still ships).

## Zero-to-cloud bootstrap

On a fresh Ubuntu/Debian VPS, as root:

```bash
export SIMON_CLOUD_DOMAIN=simonwork.app
export SIMON_LICENSE_PRIVATE_KEY=<vendor ed25519 hex>
export LLM_API_KEY=<hosted frontier pool key>
curl -fsSL https://raw.githubusercontent.com/Mindpod-Technologies/Simon/main/deploy/setup_simon_cloud.sh | bash
```

The script installs Docker + Caddy, pre-flight-checks wildcard DNS, writes
the host env, builds the Simon image, installs the billing service
(systemd), and wires the Caddyfile (billing.<domain> + tenant import).
Idempotent — re-run it anytime; it preserves existing config.
