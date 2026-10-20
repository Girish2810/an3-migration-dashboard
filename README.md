# AN3 Migration Dashboard

Shared migration tracker for the team.

## Local / shared host

```bash
cd migration-dashboard
source .venv/bin/activate
python cli.py serve
```

- Listens on `0.0.0.0:8790` (see `config.yaml`)
- Target URL: **https://deliverymigration.now3.amagi.tv** (needs Amagi DNS — see `deploy/SHARE.md`)

## Team login (HTTP Basic Auth)

| | |
|--|--|
| User | `migration` |
| Password | `auth.password` in `config.yaml` (or env `DASHBOARD_PASSWORD`) |

## Deploy notes

See [deploy/SHARE.md](deploy/SHARE.md) for DNS, Caddy, and temporary Cloudflare tunnel.
