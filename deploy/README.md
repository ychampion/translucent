# Deploy

One host, two containers: the API (`Dockerfile`, no model keys, live campaigns off) and the web app (static export
behind Caddy, which proxies `/api` so browser and API share one origin). It listens on `127.0.0.1:8090` only.

```bash
deploy/seed-ledger.sh runs/fw-chain8          # recorded campaigns the API serves; people's sign-offs land here
export TRANSLUCENT_TEAM_TOKEN=$(openssl rand -hex 24)   # key people use to leave notes, answer and sign off
docker compose -f deploy/compose.yaml up -d --build
curl -s 127.0.0.1:8090/api/campaigns | head -c 300
```

Going public is a separate decision: point a domain at the host, then use `deploy/nginx-site.conf` (hosts that
already run nginx) or any reverse proxy with response buffering off (campaign updates stream).

Paid campaigns stay off in this image: it has no model keys. Run them where the keys are
(`TRANSLUCENT_LIVE=1 TRANSLUCENT_ADMIN_TOKEN=... translucent serve`), or give the host its own capped keys.
