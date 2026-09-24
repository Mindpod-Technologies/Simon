# Simon Cloud — Deployment Runbook (you are customer #1)

Everything is built; this is the go-live sequence. ~60–90 minutes total.
Where it says **YOU**, only you can do it (accounts, logins, secrets).
Everything else, I can do with you live or hands-off once I have access.

## 0. Prerequisites (gather first)

- [ ] **A domain for tenants** — pick one: `simonwork.app` (fresh) or
      `cloud.mindpodtech.com` (subdomain of yours). Everything below says
      `<DOMAIN>`.
- [ ] **Azure VM** — Ubuntu 24.04, Standard B2s (2 vCPU / 4 GB) is fine to
      start; B2ms (8 GB) if you'll host 5+ tenants. Public IPv4. Ports
      80/443/22 open.
- [ ] **Stripe account** with two Payment Links (Pro, Business) created in
      the dashboard (test mode first!).
- [ ] **The vendor private key** (~/simon-vendor/keygen.py on your Mac —
      the `SIMON_LICENSE_PRIVATE_KEY` hex, never travels to the repo).

## 1. Azure VM

**YOU:** portal.azure.com → Create VM → Ubuntu 24.04 → Standard B2s →
SSH key auth. Note the public IP (`<VPS_IP>`).

Or give me the service principal (`AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` /
`AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID`, Contributor on
`rg-simon-cloud`) and I provision it for you.

## 2. DNS

**YOU:** at your DNS provider, add two A records pointing at `<VPS_IP>`:

```
<DOMAIN>            A  <VPS_IP>
*.<DOMAIN>          A  <VPS_IP>     ← wildcard: every tenant subdomain
```

Verify from anywhere: `dig tenant-test.<DOMAIN>` → should answer `<VPS_IP>`.

## 3. One-paste bootstrap

SSH in (`ssh azureuser@<VPS_IP>`), then as root:

```bash
export SIMON_CLOUD_DOMAIN=<DOMAIN>
export SIMON_LICENSE_PRIVATE_KEY=<vendor ed25519 hex>
export LLM_API_KEY=<frontier pool key — Kimi K3 or Azure OpenAI>
curl -fsSL https://raw.githubusercontent.com/Mindpod-Technologies/Simon/main/deploy/setup_simon_cloud.sh | bash
```

This installs Docker + Caddy, checks wildcard DNS, builds the Simon image,
and starts the **billing** (:8792) and **portal** (:8793) services as
systemd units. If anything's missing it tells you exactly what and waits.

Fill the rest of the host env afterwards:

```bash
nano /opt/simon-cloud/host.env   # STRIPE_WEBHOOK_SECRET, STRIPE_PAYMENT_LINK_PLANS, LLM_*
systemctl restart simon-billing simon-portal
```

## 4. Stripe wiring

**YOU** (Stripe dashboard):

1. **Webhook:** Developers → Webhooks → Add endpoint
   `https://billing.<DOMAIN>/stripe/webhook`, event
   `checkout.session.completed`. Copy the `whsec_…` signing secret into
   `host.env` (`STRIPE_WEBHOOK_SECRET`).
2. **Payment Links:** for each plan, set *after-payment redirect* to
   `https://billing.<DOMAIN>/success?session_id={CHECKOUT_SESSION_ID}`
   and paste the link URLs into `host.env` as
   `STRIPE_PAYMENT_LINK_PLANS=plink_xxx=pro,plink_yyy=business`.
3. Restart billing: `systemctl restart simon-billing`.

Then **I** wire the site's Buy buttons to the live Payment Links (repo
`site/src/pages/Home.tsx`).

## 5. First tenant — YOU (the acceptance test)

```bash
cd /opt/simon-cloud/simon-src
python3 deploy/provision_tenant.py provision \
  --email jarasf@mindpodtech.com --plan business
```

It prints: tenant URL (`https://mindpodtech.<DOMAIN>`), owner password,
port. Save the password somewhere safe (it authenticates your tenant).

Open `https://mindpodtech.<DOMAIN>` → set up your password → you're in —
your own Simon, on your own subdomain, isolated container, your memory.

Smoke checks:
- [ ] Chat works; approval gate asks before sending email
- [ ] `/status`-equivalent panels populate (WORK tray, automations)
- [ ] `python3 deploy/provision_tenant.py list` shows the tenant
- [ ] Portal: `https://portal.<DOMAIN>` → sign in with the license key from
      the fulfillment email → your tenant's live stats appear

## 6. First real sale (test mode)

Stripe **test** Payment Link → card `4242 4242 4242 4242` → any email:
- [ ] Success page shows a SIMON-pro-… key within seconds
- [ ] The key arrives by email from simon@mindpodtech.com
- [ ] The tenant auto-provisions (`provision_tenant.py list` on the VPS)
- [ ] The new tenant URL answers and demands sign-in

All four green → flip Stripe links to **live mode** (same steps, live keys).

## 7. Go-live checklist

- [ ] Site Buy buttons → live Stripe Payment Links (I commit this)
- [ ] `SIMON_CLOUD_PROVISION=1` in host.env (auto-tenants on payment)
- [ ] Weekly evals run per tenant (built into each Simon; results push to
      YOUR Telegram via your own install)
- [ ] OmniRoute gateway on the VPS as the model pool (compression +
      fallback + cost dashboards) — optional day-one, recommended week-one
- [ ] Announce: the repo README + site now carry working buy links

## Rollback / safety

- Tenants are disposable: `provision_tenant.py delete --slug x --yes`
  removes one; `systemctl stop simon-billing simon-portal` stops commerce;
  the VM is the only state.
- Backups: `tar czf` of `/opt/simon-cloud/tenants` nightly (add a cron;
  one line, I can add it to the bootstrap on request).
- The Mac in your office is untouched by all of this — your personal Simon
  keeps running as-is.

---

*Everything referenced here is in the repo: `deploy/setup_simon_cloud.sh`,
`deploy/provision_tenant.py`, `deploy/tenant/`, `billing/`, `portal/`.*
