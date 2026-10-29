# Use Translucent from a coding agent

Translucent is a referee for findings about recorded experiments. Your MCP client chooses measurements and proposes findings; trusted tools produce the numbers, record their runs and evidence, and send each finding through the deterministic gate. The client cannot insert a measurement, write a review, or choose a verdict. Failed checks include the same plain explanations shown in the web viewer.

Start from an existing ledger containing imported videos. `list_experiments` returns their experiment ids, video ids, labels and citations; pass the experiment id to `open_campaign`. MCP does not import arbitrary files. Run the server from the Translucent checkout, or give `uv` its absolute `--directory`.

## Claude Code

From the checkout:

```sh
claude mcp add translucent -- uv run python -m translucent.mcp_server --ledger /absolute/path/to/ledger --write
```

To pin the checkout and an API state directory:

```sh
claude mcp add translucent -- uv run --directory /absolute/path/to/translucent python -m translucent.mcp_server --ledger /absolute/path/to/ledger --write --state /absolute/path/to/api-state
```

## Codex

Add this to `~/.codex/config.toml`, replacing the absolute paths:

```toml
[mcp_servers.translucent]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/translucent", "python", "-m", "translucent.mcp_server", "--ledger", "/absolute/path/to/ledger", "--write", "--state", "/absolute/path/to/api-state"]
env = { TRANSLUCENT_SAM = "strict" }
```

## Other clients, including Muse Code

Use any MCP client that runs stdio servers. Configure the executable as `uv` and the arguments as in the Codex example. These are standard stdio instructions; Muse Code has not been tested. The server reads one JSON-RPC message per line and writes only JSON-RPC replies to stdout. Initialize with a `clientInfo` containing a nonempty `name` and `version`, then send `notifications/initialized` before calling tools. That client identity is recorded with `via: "mcp"` in `campaign.started`, whose actor is `agent:<name>`. It is a client-supplied identity, not authentication.

## Available tools

Without `--write`, only `list_campaigns`, `list_findings`, `get_evidence` and `verify` are advertised. Adding `--write` also offers:

| Tool | Arguments | Result |
| --- | --- | --- |
| `list_experiments` | none | Imported experiment and video ids, labels, citations |
| `open_campaign` | `objective`, `experiment`, `budget_usd` | Campaign created by the runtime; zero budget works for offline measurements |
| `list_tools` | `campaign_id` | Measurement tools and their JSON argument schemas |
| `run_tool` | `campaign_id`, `tool`, `arguments` | Recorded run id, status, summary, output ids, result data and image paths |
| `propose_finding` | `campaign_id`, `type`, `measurement_ids`, `params`, `statement`, optional `supersedes` | Recorded run, finding id, gate verdict, reason and failed checks with explanations |
| `record_lesson` | `campaign_id`, `text`, `scope`, `evidence_ids` | Recorded lesson for future campaigns |
| `finish_campaign` | `campaign_id`, `answer`, `not_proven` | A `campaign.finished` event with status `finished` and actor `agent:<name>` |

`list_tools` currently offers `inspect_video`, `lookup_calibration`, `track_objects`, `segment_images`, `measure_growth` and `read_cell_voltage`, from `tools.REGISTRY`. It excludes `propose_finding`, `record_lesson` and `run_analysis_code`; the first two have dedicated MCP tools, and analysis code requires the Modal sandbox. `run_tool` cannot call those excluded tools. Unknown fields, including nested measurement-tool arguments, are refused before execution.

For a timing comparison, use `type: "value_within"`, a recorded event's measurement id, and `params: {"lo": 80, "hi": 100, "unit": "s"}`. Read the verdict rather than treating a successful tool run as an accepted finding. To correct a finding, cite its id in `supersedes`. Measurement and superseded finding ids must belong to the same campaign. Frame images are returned as local file paths, never inline base64; the client needs access to the server's filesystem to view them.

SAM uses `TRANSLUCENT_SAM` exactly as campaigns do. The default is `strict`: only an exact recorded reply can be used. Missing replies fail as recorded tool runs. `nearest` is a development replay whose substitutions cannot become evidence. The server never enables live mode itself. Keep `TRANSLUCENT_SAM=strict` for offline work. `budget_usd` is recorded on the campaign; this MCP adapter does not run the campaign's provider-budget loop.

## Ownership and concurrent writers

An MCP session can write only to campaigns it opened. Reconnecting with the same client name does not restore ownership. A local operator may deliberately add `--adopt` together with `--write` to allow writes to existing unfinished campaigns. Adoption does not bypass evidence ownership or the gate. Once any `campaign.finished` event exists, further write calls on that campaign are refused, including with `--adopt`; read tools remain available.

Only one process may write a ledger at a time. Stop the API server and other campaign writers before starting MCP with `--write`. Each MCP write opens one ledger connection and closes it before replying; no ledger connection is held between calls.

The MCP writer holds the API's existing `server.lock` for its lifetime and refuses startup if that lock is held. Use **the same `--state` directory as `translucent serve --state`**; its default is `TRANSLUCENT_STATE_DIR` or `ops/sol/state/api`, resolved from the working directory. The API cannot discover arbitrary state directories from a ledger, so a custom API state path must be supplied explicitly. MCP also holds a ledger-local `mcp.lock`, preventing two MCP writers even if they choose different state directories. Locks release on shutdown or process death. Runtime/CLI writers do not use these locks and must be stopped separately. Read-only MCP can remain connected while another process writes.

## Example session: correcting playback timing

This illustrative ten-line session assumes credible filament outlines and a resolved recorded clock are available:

```text
Agent: Open a campaign on Movie 4 to measure filament growth.
Translucent: Campaign opened; here are its measurement tools.
Agent: Read the image scale, outline a filament, and measure its growth using playback frames.
Translucent: The growth measurement is recorded with playback timing, not real time.
Agent: Propose that speed as the filament's physical growth rate.
Translucent: Refused: the time axis came from playback speed, not the recorded clock.
Agent: Read the recorded clock and measure growth again using that clock.
Translucent: The new measurement uses the recorded clock and image scale.
Agent: Propose a specific speed range supported by that measurement, replacing the first finding.
Translucent: Supported; the finding, checks and evidence are recorded.
```

Finish with an answer citing earned finding ids and a plain statement of what was not proven. Use `get_evidence` to inspect a finding's evidence and `verify` to re-check it offline. The same check is available locally with `uv run translucent verify /absolute/path/to/ledger`.
