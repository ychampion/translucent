# Use Translucent from a coding agent

Translucent is a referee for findings about recorded experiments. Your MCP client chooses measurements and proposes findings; trusted tools produce the numbers, record their runs and evidence, and send each finding through the deterministic gate. The client cannot insert a measurement, write a review, or choose a verdict. Failed checks include the same plain explanations shown in the web viewer.

Start from an existing ledger containing imported videos. `list_experiments` returns their experiment ids, video ids, labels and citations; pass the experiment id to `open_campaign`. MCP does not import arbitrary files; a loop campaign's datasets are imported only from the server's `--import-root` (see Loop campaigns). Run the server from the Translucent checkout, or give `uv` its absolute `--directory`.

## Claude Code

From the checkout:

```sh
claude mcp add translucent -- uv run python -m translucent.mcp_server --ledger /absolute/path/to/ledger --write
```

To pin the checkout:

```sh
claude mcp add translucent -- uv run --directory /absolute/path/to/translucent python -m translucent.mcp_server --ledger /absolute/path/to/ledger --write
```

## Other clients, including Muse Code

Use any MCP client that runs stdio servers. Configure the executable as `uv` with these arguments and environment,
replacing the absolute paths. A client that reads a TOML server table takes them in this form:

```toml
[mcp_servers.translucent]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/translucent", "python", "-m", "translucent.mcp_server", "--ledger", "/absolute/path/to/ledger", "--write"]
env = { TRANSLUCENT_SAM = "strict" }
```

These are standard stdio instructions; Muse Code has not been tested. The server reads one JSON-RPC message per line and writes only JSON-RPC replies to stdout. Initialize with a `clientInfo` containing a nonempty `name` and `version`, then send `notifications/initialized` before calling tools. That client identity is recorded with `via: "mcp"` in `campaign.started`, whose actor is `agent:<name>`. It is a client-supplied identity, not authentication.

## Available tools

Without `--write`, only `list_campaigns`, `list_findings`, `get_evidence`, `verify`, `list_branches` and the loop-campaign reads (below) are advertised. Adding `--write` also offers:

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

SAM uses `TRANSLUCENT_SAM` exactly as campaigns do. The default is `strict`: only an exact recorded reply can be used. Missing replies fail as recorded tool runs. `nearest` is a development replay whose substitutions cannot become evidence. The server never enables live mode itself. Keep `TRANSLUCENT_SAM=strict` for offline work. `budget_usd` is recorded on the campaign and held to the same provider caps as on HTTP; this MCP adapter does not run the campaign's provider-budget loop.

## Loop campaigns

A loop campaign is the workflow Translucent's own planner runs, open to your agent: register an objective on a
system, propose experiments, wait while a person approves each exact version, read the results and comparisons, and
conclude. The tools come from the same capability table as the HTTP routes ([API.md](API.md#loop-campaigns-api-v1-ledgers-id)),
against the same ledger and rules.

```sh
uv run python -m translucent.mcp_server --ledger /absolute/path/to/ledger --write --import-root /absolute/path/to/data
```

| Tool | Who | Arguments | Result |
| --- | --- | --- | --- |
| `list_systems`, `list_datasets`, `get_dataset`, `list_loop_campaigns` | everyone | `dataset_id`; `cursor`, `limit` | systems; dataset summaries and records; campaigns |
| `get_campaign_view`, `get_request`, `read_series` | everyone | `campaign_id`; `request_id`; `ref`, `quantity`, `t0?`, `t1?`, `max_points?` | the campaign's view; one request; a series |
| `import_dataset` | `--write` | `manifest` (the JSON), `files`: a folder or `{name: path}` under `--import-root` | the `DatasetRecord` |
| `open_loop_campaign` | `--write` | `title`, `objective` | `{campaign_id, view}`; you are the planner (`external`), the session owns it |
| `define_measurement` | `--write` | `campaign_id`, `name`, `spec` (ops/1) | the record and its measurements |
| `propose_experiment` | `--write` | `campaign_id`, `action`, `params`, `rationale`, `prediction`, `expected_effect`, `evidence_ids?`, `specimen?` | the request with its `content_hash` |
| `wait_for_request` | `--write` | `campaign_id`, `request_id`, `timeout_s` (at most 60) | the request, `settled` false while it still waits for a person; approved computational work runs in this process |
| `cancel_request`, `conclude_campaign` | `--write` | `request_id`, `reason`; `text`, `unresolved_reasons?` | the request; the conclusion with its computed outcome |
| `approve_request`, `reject_request`, `revise_request`, `submit_result`, `amend_objective`, `add_note` | `--operator` | as the HTTP routes; result files are paths under `--import-root` | as the HTTP routes |

Every write tool except `submit_result` and `add_note` takes an optional `idempotency_key`; the same key and
arguments return the first result, a different call under a used key is refused.

**Who approves.** Your client's name is self-declared, so it is never a person's identity. Without `--operator`, the
person tools are not offered: a person approves your requests over HTTP or in the web app, and `wait_for_request`
returns once they have and the work has run. The local operator who starts the server may add
`--operator "Ana Ruiz"`: they vouch for that name, as holding the team token does over HTTP, and approvals, results
and amendments are recorded as `person:Ana Ruiz`, never under the client's name. `open_loop_campaign` registers the
objective under the operator's name, or else the client's, and can never pre-approve experiments.

**Files.** `import_dataset` and `submit_result` read only files under `--import-root`, named relative to it or by an
absolute path inside it; anything outside it, or reached through a link, is refused, and without `--import-root`
nothing is read.

**Budgets.** `open_campaign` and `open_loop_campaign` budgets must be finite, 0 or more, and not above the caps of
the providers the server's profile calls (`TRANSLUCENT_PROFILE`: dev $25, meta $170), as on HTTP.

## Ownership and concurrent writers

An MCP session can write only to campaigns it opened, loop campaigns included. Reconnecting with the same client name does not restore ownership. A local operator may deliberately add `--adopt` together with `--write` to allow writes to existing unfinished campaigns. Adoption does not bypass evidence ownership or the gate. Once any `campaign.finished` event exists, further write calls on that campaign are refused, including with `--adopt`; read tools remain available.

Several processes may write one ledger at once: the API server, any number of MCP sessions and the CLI. Loop campaign records are appended under the ledger's own admission in one transaction, so concurrent writers cannot fork a campaign's record chain or run a request twice. A video or intervention campaign is written by one process at a time: an MCP session takes the campaign's lock (`<ledger>/locks/campaign-<hash>.lock`) for each write call and is refused while another process holds it, and the API worker holds it for as long as it runs the campaign. Locks release on process death. The ledger must be on a local disk of one host; network filesystems are not supported. Each MCP write opens one ledger connection and closes it before replying.

Earlier versions made an MCP writer hold the API's `server.lock` and a ledger-wide `mcp.lock`, so it refused to start beside an API server or a second MCP writer. They no longer do; `--state` is still accepted and ignored.

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
