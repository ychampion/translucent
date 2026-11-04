# Deploy

One host, two containers: the API (`Dockerfile`, no model keys, live campaigns off) and the web app (static export
behind Caddy, which proxies `/api` so browser and API share one origin). It listens on `127.0.0.1:8090` only.

```bash
deploy/seed-ledger.sh runs/fw-chain8          # recorded campaigns the API serves; people's sign-offs land here
docker compose -f deploy/compose.yaml up -d --build
curl -s 127.0.0.1:8090/api/health             # {"ok":true}
# one key per person; each is shown once and only its hash is kept (in ./state/api/credentials.json)
docker compose -f deploy/compose.yaml exec api translucent keys add --name Ana --role engineer
docker compose -f deploy/compose.yaml exec api translucent keys add --name Vic --role viewer
curl -s -H "Authorization: Bearer $KEY" 127.0.0.1:8090/api/campaigns | head -c 300
```

Every read and write needs a key. Roles: `viewer` reads; `engineer` also opens campaigns with the free planners,
approves, submits results, notes, uploads and lowers budgets; `admin` also raises budgets and starts paid runs;
`planner` is an agent's key (it proposes and cancels its own requests, never a person's work). `--ledger ID`
(repeatable) limits a key to some ledgers. `translucent keys list` shows them (never the keys); `translucent keys
revoke ID` ends a key and its browser sessions at once. In the web app a person signs in with their key once; the
session lasts 12 hours and survives a restart. `TRANSLUCENT_TEAM_TOKEN` (32 characters or more; the server refuses a shorter one at start) still works as one engineer key, named by
`TRANSLUCENT_TEAM_NAME`; changing its value (or `TRANSLUCENT_ADMIN_TOKEN`'s) ends the browser sessions opened with
the old one. An open campaign update stream stops within a few seconds of its key being revoked or its session
ending. After 10 wrong keys in a minute from one address, further wrong keys get 429; a right key always works.

Proxy chain. Each hop trusts only the one before it, at fixed addresses on the compose network `edge`
(172.29.80.0/24): the API trusts Caddy (172.29.80.10, `FORWARDED_ALLOW_IPS`), and Caddy trusts the bridge gateway
(172.29.80.1), where a front door on this host connects from through the published port. Caddy sends the API a
single `X-Forwarded-For` value, the client address it resolved right to left, so no client can choose the address
the failed-key limit counts. If the subnet clashes with another network on the host, change it in `compose.yaml`
(subnet, gateway, Caddy's address, `FORWARDED_ALLOW_IPS`) and in the Caddyfile's `trusted_proxies` together. The
API's schema and docs pages (`/docs`, `/redoc`, `/openapi.json`) are off; `TRANSLUCENT_API_DOCS=1` turns them on.

Dataset import by path. With `TRANSLUCENT_IMPORT_ROOT` set (mount the folder into the API container), a path import
reads files under it. Lay it out per ledger when keys are limited to some ledgers:

```
<import root>/
  lab-a/            # only imports into ledger lab-a read here, with paths relative to this folder
    screen-07/manifest files...
  lab-b/
    ...
```

As soon as the import root holds a folder named for any ledger the server serves, every ledger reads only its own
`<import root>/<ledger id>/` (a ledger with no folder reads nothing), so a key for `lab-a` cannot import files staged
for `lab-b`. With no such folder, the whole import root is shared by every key that may import. A path outside the
folder, a missing file and a link all get the same refusal. Create the ledger before staging files for it: a folder
for a ledger that does not exist yet does not switch the layout to per ledger.

The API refuses to start without a ledger. To start empty instead of seeding, add `--create-ledger` to the API
service's command.

Going public is a separate decision: point a domain at the host, then use `deploy/nginx-site.conf` (hosts that
already run nginx) or any reverse proxy with response buffering off (campaign updates stream).

Paid campaigns stay off in this image: it has no model keys. Run them where the keys are
(`TRANSLUCENT_LIVE=1 translucent serve` with an `admin` key from `translucent keys add --role admin`, or
`TRANSLUCENT_ADMIN_TOKEN=...`), or give the host its own capped keys. Only an admin key starts one.
