"""Simon billing: Stripe checkout fulfillment → signed license keys.

Flow: customer buys via a Stripe Payment Link → Stripe calls our webhook →
we verify the signature, mint a SIMON-<plan> license key (vendor private
key from env, never in the repo), store it, and email it to the customer.
The /success page shows the key as well.

All config comes from environment (see README in this package):
    STRIPE_WEBHOOK_SECRET       whsec_... from the Stripe dashboard
    STRIPE_PAYMENT_LINK_PLANS   "plink_abc=pro,plink_xyz=business"
    SIMON_LICENSE_PRIVATE_KEY   vendor Ed25519 private key hex (server-only)
    BILLING_DB                  SQLite path (default: data/billing.db)
    GRAPH_*                     Simon's mailbox, used to email keys
"""
