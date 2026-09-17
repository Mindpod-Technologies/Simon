# Simon Billing

Stripe checkout → signed license key fulfillment. Zero Stripe SDK
dependency; works with no-code **Payment Links**.

## Flow

1. Customer clicks **Buy Pro** on the site → Stripe Payment Link.
2. Stripe fires `checkout.session.completed` to our webhook.
3. The webhook verifies the HMAC signature, maps the payment link to a
   plan, mints a `SIMON-<plan>-…` license key (vendor Ed25519 private key),
   stores it (idempotent on the session id), and emails it to the customer
   via Simon's Graph mailbox.
4. Stripe redirects the customer to `/success?session_id=…`, which shows
   the key (auto-polls until the webhook has processed).

## One-time setup

1. **Stripe dashboard → Payment Links**: create one per plan
   ($12/mo Pro, $49/mo Business or lifetime one-offs). Set each link's
   *after-payment redirect* to
   `https://<public-host>/success?session_id={CHECKOUT_SESSION_ID}`.
2. **Stripe dashboard → Developers → Webhooks**: add endpoint
   `https://<public-host>/stripe/webhook`, event
   `checkout.session.completed`. Copy the signing secret.
3. **Site**: paste the Payment Link URLs into
   `site/src/pages/Home.tsx` (`STRIPE_PAYMENT_LINK_PRO` /
   `STRIPE_PAYMENT_LINK_BUSINESS`) and rebuild the site.
4. **Server env** (`~/simon/.env`, never committed):

   ```
   STRIPE_WEBHOOK_SECRET=whsec_...
   STRIPE_PAYMENT_LINK_PLANS=plink_1AbC=pro,plink_9XyZ=business
   SIMON_LICENSE_PRIVATE_KEY=<vendor ed25519 private key hex, from ~/simon-vendor/keygen.py>
   BILLING_DB=data/billing.db        # optional
   ```

5. **launchd** (macOS): `deploy/com.simon.billing.plist`, rendered and
   loaded like the other Simon services. Runs on **:8792**.
6. **Public exposure**: Stripe must reach the webhook over HTTPS. On this
   Mac use Tailscale Funnel (see deploy/tailscale.md) or the VPS reverse
   proxy to forward `https://<host>/` → `localhost:8792`.

## Test mode

Stripe test-mode Payment Links + test webhook secret work identically —
use a test card (4242 4242 4242 4242) and watch `data/logs/billing.*.log`
and `sqlite3 data/billing.db 'select * from issued_licenses'`.
