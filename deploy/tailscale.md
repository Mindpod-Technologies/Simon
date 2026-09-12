# Remote access with Tailscale

The web UI has no built-in auth, so **don't open port 8788 to the internet**.
Tailscale gives you a private, encrypted network (a "tailnet") between your
devices — no open ports, no firewall rules, no TLS certificates to manage.

## Setup

1. Install Tailscale on each device and sign in to the same tailnet:
   - **Mac Mini**: `brew install --cask tailscale` (App Store app also fine),
     or `brew install tailscale && sudo tailscaled` for headless.
   - **VPS**: `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`
   - **Phone**: Tailscale app (iOS/Android), same account.
2. Find the machine's tailnet IP:
   ```bash
   tailscale ip -4        # e.g. 100.x.y.z
   ```
3. With MagicDNS enabled (default in the Tailscale admin console), the machine
   also gets a name like `simon-vps.<your-tailnet>.ts.net`.

## Reach the web UI

Simon already binds `0.0.0.0:8788`, so once Tailscale is up just browse to:

```
http://<tailscale-ip>:8788
# or, with MagicDNS:
http://simon-vps.<your-tailnet>.ts.net:8788
```

No router port-forwarding needed — traffic never touches the public internet.

Optional: `sudo tailscale serve --bg 8788` proxies the UI over **HTTPS** at
`https://simon-vps.<your-tailnet>.ts.net` with a real, automatic certificate.

## Why this beats opening ports

- Zero exposed attack surface: nothing listens on the public internet.
- WireGuard encryption and device identity built in.
- Works behind CGNAT and restrictive firewalls.

## Alternative: Caddy + basic auth (public HTTPS)

If you genuinely need public access without Tailscale:

```caddyfile
simon.example.com {
    basicauth {
        simon <bcrypt-hash>   # caddy hash-password
    }
    reverse_proxy 127.0.0.1:8788
}
```

Caddy handles TLS via Let's Encrypt automatically. Even then, keep
`SIMON_ALLOW_SHELL=false` and the Telegram allowlist tight — see the README
SECURITY section.
