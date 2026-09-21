"""Simon Cloud customer portal: per-business dashboard + support cases.

Customers sign in with the license key from their purchase email (they
already hold it — no new credential to manage). Hosted tenants see live
usage stats read straight from their container's database on this host;
self-host customers see license status and support (we never phone home).

Runs on :8793. Public via Caddy: portal.<SIMON_CLOUD_DOMAIN>.
"""

from .server import create_app  # noqa: F401
