# Team share: https://deliverymigration.now3.amagi.tv

## What you need from Amagi (DNS / platform)

Ask whoever owns `*.now3.amagi.tv` to create:

- **Name:** `deliverymigration.now3.amagi.tv`
- **Type:** A or CNAME → the VM / ingress that will run this app
- **TLS:** company cert or ingress TLS (same as other now3 apps)

Until DNS exists, you can temporarily share a Cloudflare quick tunnel (random `*.trycloudflare.com` URL). That is **not** the amagi.tv name.

## Run on the host

```bash
cd /path/to/migration-dashboard
source .venv/bin/activate
# config.yaml: server.host 0.0.0.0, auth.enabled true
python cli.py serve
```

Optional HTTPS front-end (if DNS already points here):

```bash
caddy run --config deploy/Caddyfile
```

## Credentials (share with the team)

| | |
|--|--|
| URL | https://deliverymigration.now3.amagi.tv |
| User | `migration` |
| Password | value of `auth.password` in `config.yaml` (or `DASHBOARD_PASSWORD`) |

Browser shows a login prompt (HTTP Basic Auth). Same user/password for everyone is fine for an internal tracker; rotate the password when someone leaves.

## Temporary tunnel (no DNS yet)

```bash
# App on 8790 first
cloudflared tunnel --url http://127.0.0.1:8790
```

Send the printed `https://….trycloudflare.com` URL + the same basic-auth user/password.
