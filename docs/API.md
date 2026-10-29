# HTTP API

```sh
uv run translucent serve --ledger <ledger-dir> --state <state-dir>   # http://127.0.0.1:8000
```

Run one server per state directory; an OS lock enforces it. With uvicorn directly, set `TRANSLUCENT_LEDGER_DIR` and
`TRANSLUCENT_STATE_DIR`. Container instructions are in [`deploy/README.md`](../deploy/README.md).

## Configuration

| Variable | Effect |
|---|---|
| `TRANSLUCENT_LIVE=1` and `TRANSLUCENT_ADMIN_TOKEN` | Allow starting and resuming campaigns (paid model calls). Without both, those endpoints refuse. |
| `TRANSLUCENT_TEAM_TOKEN` | Allow people's actions: notes, answers, sign-offs and challenges, pause/stop, uploads. |
| `TRANSLUCENT_PROFILE` | Default model profile, `dev` or `meta`. |
| `TRANSLUCENT_CORS_ORIGINS` | Comma-separated browser origins. Default: none; a wildcard is rejected. |

Protected requests send `Authorization: Bearer <token>`. Tokens are compared in constant time.

## Reading

| Endpoint | Returns |
|---|---|
| `GET /api/session` | Whether live runs and team actions are enabled, and the default model. |
| `GET /api/experiments` | Recorded experiments in the ledger. |
| `GET /api/campaigns` | Campaigns, each with a nullable `job` (queued, running, paused, finished, failed, interrupted). |
| `GET /api/campaigns/{id}` | One campaign and its job. |
| `GET /api/campaigns/{id}/events` | Server-sent events. Each event's ledger sequence is its SSE `id` and `data.seq`; send `Last-Event-ID` to receive only later events. The stream ends when the campaign does. |
| `GET /api/campaigns/{id}/bundle` | The replay bundle the web app renders (cached; refreshed on new records). |
| `GET /api/campaigns/{id}/people` | Questions with their status, notes, and people's sign-offs and challenges. |
| `GET /api/artifacts/{sha256-id}` | Recorded evidence bytes with their media type. Only existing ledger ids are served. |
| `GET /api/budget` | Local spend balances per provider. Never contacts a provider. |

## Running campaigns (admin token)

`POST /api/campaigns` with `{"objective": "...", "experiment_id": "reisecker2023/movie4", "profile": "meta",
"budget": 2.0}` returns 202 with the campaign and its queued job. The experiment must already be in the ledger;
paths are never taken from a request. Only one campaign runs at a time (409 otherwise).

`POST /api/campaigns/{id}/resume` with `{"budget": 2.0}` (optional `"profile"`) continues a stopped or interrupted
campaign from its evidence, with a fresh budget on top of what it has spent.

Jobs are stored as JSON under `<state>/jobs/`. A server restart marks running jobs as interrupted.

## People's actions (team token)

Every action carries `"author"`, and is recorded in the ledger under that name.

| Endpoint | Body |
|---|---|
| `POST /api/campaigns/{id}/notes` | `{"author": "Ana", "text": "..."}`. Reaches the agent before its next step. |
| `POST /api/campaigns/{id}/answers` | `{"author": "Ana", "question_id": "...", "text": "..."}`. Only open questions. |
| `POST /api/campaigns/{id}/reviews` | `{"author": "Ben", "claim_id": "...", "verdict": "agree" \| "challenge", "reason": "..."}`. A challenge sends the agent back to re-examine. Neither changes the checker's verdict. |
| `POST /api/campaigns/{id}/control` | `{"author": "Ana", "action": "pause" \| "resume" \| "stop"}` |
| `PUT /api/uploads/{upload_id}/{filename}`, then `POST /api/experiments` | Add your own recording with its lab records; see [BRING-YOUR-OWN.md](BRING-YOUR-OWN.md). |

## Agent-written analysis code

The `run_analysis_code` tool takes Python `code`, `inputs` (measurement or artifact ids) and `outputs`
(`[{name, unit, label}]`). Inputs appear under `/work/inputs/` (`inputs.json` maps ids to files); the code writes
`/work/outputs/<name>.json`, either a number or `{"value": 2.5, "uncertainty": {"value": 0.1, "kind": "ci95",
"method": "fit"}}`.

It runs in a fresh sandbox with networking blocked, no credentials and a 120 s lifetime. Limits: 1 MiB of code, 64
inputs, 32 MiB of input bytes, 32 scalar outputs. The code then runs a second time on the recorded inputs moved by a
fraction of a percent; an output or uncertainty that doesn't move with them was typed in, and is refused.

## Offline verification

```sh
uv run --group dev translucent verify <ledger-dir | export-dir>   # optional: --campaign <id>
```

Replays every recorded checker review against the evidence available when it was made, and checks file hashes and
event order. Exit code 0 only when everything matches; otherwise one plain line per problem. An export
(`translucent export <campaign-id> <out-dir> --ledger <ledger-dir>`) carries what it needs to be verified with no
provider keys; keep its folder together.

Verification shows the evidence is consistent, not when it was recorded: a fully rewritten ledger with a matching
manifest needs an external checkpoint to detect.

## MCP

A stdio MCP server exposes `list_campaigns`, `list_findings`, `get_evidence` and `verify` over one ledger, and with
`--write` the measurement tools and `propose_finding`. See [AGENTS.md](AGENTS.md).
