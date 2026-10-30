# HTTP API

```sh
uv run translucent serve --ledger <ledger-dir> --state <state-dir>   # http://127.0.0.1:8000
```

## NIST live planner and recorded replay

The intervention command runs locally through the existing metered providers.
The `meta` planner is Muse Wonder (`muse-wonder-1.0`); `dev` uses Muse Spark Open
on Fireworks. Credentials stay in the environment used by the provider client
(`MODEL_API_KEY` or `META_API_KEY`, or `FIREWORKS_API_KEY` for `dev`). No keys are
needed for estimates or replay.

```sh
uv run translucent intervene nist --profile meta --budget 25 --split loco --dry-run
uv run translucent intervene nist --profile meta --budget 25 --split prequential --dry-run
uv run translucent intervene nist --profile meta --budget 25 --split loco --ledger /tmp/nist-live --recordings /tmp/nist-live-recordings
uv run translucent intervene nist --muse-only --profile meta --budget 10 --split loco --ledger /tmp/nist-muse-only --recordings /tmp/nist-muse-recordings
uv run translucent intervene nist --profile meta --budget 25 --split loco --ledger /tmp/nist-replay --replay /tmp/nist-live-recordings
uv run translucent intervene nist --muse-only --profile meta --budget 10 --split loco --ledger /tmp/nist-muse-replay --replay /tmp/nist-muse-recordings
```

Use fresh ledger and recording directories for each run. Copy the entire private
recording directory to replay on another machine with the same source checkout.
The profile, budget, split, step cap and other recorded settings must match.
`--recordings` defaults to `<ledger>/recordings`; the omitted `--ledger` default
creates a uniquely named directory under `state/`. `--steps` sets the per-fold
tool-call cap (120 by default); `--split` also accepts `lofo` and `prequential`.

The live planner records three competing mechanistic hypotheses before registering
strategies. `prepare_training` batches unscored training commitments and reveals;
`execute_fold` executes the submitted, screened specs through individual
commit/observe/reveal records. These tools reduce conversation overhead while the
controller still checks every executable prediction and paid window. LOCO/LOFO
strategies stay frozen, and each new fold gets fresh model history and seals.

Muse-only uses one tool-free call per held-out trial and one additional call if it
chooses a 30 or 60 second observation. Its input is the declared metadata and a
numeric temperature table with at most 128 evenly spaced samples, including the
available endpoints. The second call includes only the paid post-off window.
Its completed campaign records scored `muse_only` baseline results in place of an
unrun stub. The earlier scripted benchmark remains unchanged; independently run
campaigns are not silently pooled into its comparison family.

Every attempt is recorded with request/response digests, model, usage and cost,
including retries, finish reasons, reasoning tokens and refusals. Recordings publish atomically, and paid spending is retained if recording fails. The campaign cap reserves a conservative request
ceiling before contacting the provider, in addition to the existing global
provider cap and pause switch. Estimates state token assumptions; they exclude
retries and repair replies and do not promise completion within a given budget.
Exit code 0 means completion; a clean budget, step, refusal or stalled stop returns
2 with its recorded reason. A budget stop during a watch can leave an unfinished
episode: no final judgment or outcome is invented, and such a ledger cannot
support complete-coverage claims or pass scored-outcome verification. All evidence
already recorded is retained.

Replay constructs no provider, consumes the exact ordered request tape and checks
the committed decision record bodies byte for byte, including IDs, sequences and
hashes. The new `run.json` completion receipt also requires exact status, reason, tool-call count, spend and recording-count equality. A missing or truncated tape fails even if no decisions had been committed. A mismatch fails closed. The private manifest contains replay entropy for
record IDs and salted seals; planner tools and public exports cannot read it.
It is controller state, not an external timestamp or protection against an
operator who can read the public dataset.

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
| `GET /api/campaigns/{id}/graph` | The campaign's evidence graph: question, findings, reviews, measurements, runs, files and recording as nodes, with typed edges. Shapes in [CONTRACTS.md](CONTRACTS.md#evidence-graph). |
| `GET /api/findings/{claim_id}/evidence` | Everything a finding rests on, transitively: `claim`, `measurements`, `runs`, `artifacts`, `reviews`, `problems` (broken links), its current `status`, and `signoffs`: each person's review with `evidence_digest` (the finding and the evidence it was proposed on, with the runs invalidated by the time of the review), `current_digest`, `current` (true when nothing has changed since) and a plain `text` ("Ana reviewed this exact evidence", or "Evidence changed since Ana reviewed it (run ... invalidated)"). |
| `GET /api/compare?a=<id>&b=<id>` | What changed between two campaigns, two findings or two runs (ids of the same kind): structured `changes` plus plain `sentences`. For campaigns, findings are paired by test type and cited quantities. 400 for mixed kinds, 404 for an unknown id. |
| `GET /api/campaigns/{id}/next` | Deterministic advice computed from the ledger, never a verdict: `advice` (each `rule`, `text`, `refs`: a finding refused for a missing scale or clock, two attempts failing the same check, a supported finding resting on one method, a person's challenge nothing has answered yet, a loaded lesson nothing cites), and `answer`: the final answer's numbers with a unit (`numbers`) and the ones that match no supported finding's value, uncertainty or range (`untraced`). |
| `GET /api/campaigns/{id}/protocol` | The campaign's pre-registered metrics and method board: `protocol` (or `null`), `pre_registered` (the protocol provably precedes the start), and per metric its `attempts` (finding, `method`, `value`, `unit`, `uncertainty`, checker `status`, `on_protocol` with a `protocol_note`, `replications`) and `decisions`. |
| `GET /api/campaigns/{id}/forks` | What-if branches made from this campaign: each `fork`, its `branch` campaign, `status` (`done`, `failed`, `needs_agent`, or the agent job's status), `reason`, and the branch's `findings` with their status. |
| `GET /api/findings/{claim_id}/branches` | A finding's alternatives: `chain` (findings it replaces or that replace it), `forks` (what-if branches with their findings and status), `same_quantity` (findings in other campaigns that test the same quantity). |
| `GET /api/artifacts/{sha256-id}` | Recorded evidence bytes with their media type. Only existing ledger ids are served. |
| `GET /api/budget` | Local spend balances per provider. Never contacts a provider. |

## Running campaigns (admin token)

`POST /api/campaigns` with `{"objective": "...", "experiment_id": "reisecker2023/movie4", "profile": "meta",
"budget": 2.0}` returns 202 with the campaign and its queued job. The experiment must already be in the ledger;
paths are never taken from a request. Only one campaign runs at a time (409 otherwise).

An optional `"protocol"` fixes the success metrics before the agent starts:

```json
{"author": "Ana", "metrics": [{"id": "growth", "name": "Growth speed before the first short",
  "quantities": ["growth_velocity", "derived:*"], "unit": "um/s",
  "test": {"type": "value_within", "params": {"lo": 55, "hi": 72, "unit": "um/s"}},
  "min_methods": 2, "needs_verification": true}]}
```

The test is one of the checker's own predicate types with exactly its typed parameters (`value_within`: `lo`, `hi`,
`unit`; `events_coincide`: `tol`, optional `unit`; `trend`: `direction`, optional `steady_tolerance`); anything else,
free text included, is a 422 and no campaign is created. The protocol is recorded before the job is queued, and the
campaign's `campaign.started` event cites it. The agent then gives `propose_finding` a `metric_id`; a finding whose
test differs from the metric's (for example a window chosen after seeing the data) is still checked as usual but is
"off protocol" and can never win. The agent's `declare_winner` tool records a decision for a metric:

- `winner` when enough distinct methods (tool and analysis code) were attempted on protocol, the winner is on
  protocol and supported, a supported verification finding re-derived the winner's value through runs it shares
  with the winner only the experiment's own records, every replication of the winner succeeded, and no supported
  on-protocol attempt by another method disagrees beyond the combined uncertainty;
- `tie` when another method's attempt passes the same way and agrees;
- `tradeoff` when it passes but another metric of the protocol was won by a different method;
- `insufficient_evidence` otherwise ("no winner yet"), with the checks that failed. It is recorded too.

A replication (`translucent.rerun.replicate`) re-executes every step behind a finding except the experiment's own
record lookups, as new runs with the same tools and arguments; SAM answers are replayed from their recording and
analysis code runs again in the sandbox. Every replication run is recorded under step `replicate:<finding>:<n>`,
failed ones included, and a failed one blocks a winner.

`POST /api/campaigns/{id}/resume` with `{"budget": 2.0}` (optional `"profile"`) continues a stopped or interrupted
campaign from its evidence, with a fresh budget on top of what it has spent.

Jobs are stored as JSON under `<state>/jobs/`. A server restart marks running jobs as interrupted.

## People's actions (team token)

Every action carries `"author"`, and is recorded in the ledger under that name.

People's text (notes, answers, review reasons, what-if reasons), the free-text fields of an experiment manifest, and
agent-written analysis code have secret-looking strings (API keys, bearer tokens, private keys, `password=...`)
replaced by `[secret removed]` before anything is recorded; plain hex hashes and content addresses are kept. The
ledger is insert-only, so this happens at intake.

Notes, answers, reviews, control and what-ifs accept an `Idempotency-Key` header (1-128 letters, digits or `_.:-`).
The same key with the same body returns the first response without recording anything again; the same key with a
different body is a 409. Keys are kept per token, method and path under `<state>/idempotency/`.

| Endpoint | Body |
|---|---|
| `POST /api/campaigns/{id}/notes` | `{"author": "Ana", "text": "..."}`. Reaches the agent before its next step. |
| `POST /api/campaigns/{id}/answers` | `{"author": "Ana", "question_id": "...", "text": "..."}`. Only open questions. |
| `POST /api/campaigns/{id}/reviews` | `{"author": "Ben", "claim_id": "...", "verdict": "agree" \| "challenge", "reason": "..."}`. A challenge sends the agent back to re-examine. Neither changes the checker's verdict. |
| `POST /api/campaigns/{id}/forks` | A what-if: `{"author": "Ben", "claim_id": "...", "reason": "...", "overrides": [{"quantity": "spatial_scale", "value": 164.4, "uncertainty": 1.6, "unit": "px/mm"}], "mode": "rerun"}`. See below. |
| `POST /api/campaigns/{id}/control` | `{"author": "Ana", "action": "pause" \| "resume" \| "stop"}` |
| `PUT /api/uploads/{upload_id}/{filename}`, then `POST /api/experiments` | Add your own recording with its lab records; see [BRING-YOUR-OWN.md](BRING-YOUR-OWN.md). |

## What-if branches

A person can ask what happens to a finding if one assumption behind it changes. The parent campaign is never
changed: the what-if is a new branch campaign plus a fork record, and both campaigns' event streams get a
`fork.created` annotation.

Overrides (each at most once per what-if): `spatial_scale` (`px/mm` or `um/px`), `time_base_interval` (seconds
between captured images), `short_threshold_v` (`V`), `voltage_offset_s` (`s`), and `finding_window` (`[lo, hi]` or a
tolerance, in the finding's unit). Values must be finite numbers, uncertainties at least 0, the scale, the time
between images and the short threshold greater than 0, a range's `lo` at most its `hi`, and units as listed (a range
in the finding's own unit). A what-if must change something the finding depends on. Anything else is a 422, and
nothing is recorded. A finding takes at most 20 what-ifs (429 after that).

- `"mode": "rerun"` re-runs only the steps the override reaches, in their original order, with no model calls, then
  proposes the same finding in the branch for the checker. Everything else is cited from the parent unchanged. The
  changed value enters as a declared calibration ("declared by Ben in a what-if branch: ..."), so the branch's checks
  say what they are conditional on. If a step the override reaches needs a model or SAM, the branch stops with
  `status: "needs_agent"` and a plain reason; if a step no longer produces its result (e.g. a declared scale too
  loose to measure with), `status: "failed"`.
  - A short rerun of local steps only (calibration lookups, voltage reads, growth measurement; at most 12 steps)
    needs the team token and runs within the request (201 with the finished branch).
  - A rerun that reaches agent-written analysis code (which runs in the remote sandbox), or a longer one, is a queued
    job under the same rules as `POST /api/campaigns`: the administrator token, live opt-in and a `budget` (403 or
    422 otherwise; 409 while another live campaign is queued, running or paused). The response is 201 with
    `status: "queued"` and the job (`job.rerun` is the fork id); the branch's `campaign.finished` event carries the
    result.
- `"mode": "agent"` (team token plus the administrator token and live opt-in, and a `budget`) records the declared
  values and queues the agent on the branch like any paid campaign.

The agent itself can never change an assumption: the tools' hidden `assumption` argument is accepted only from a
person's what-if, and only in that what-if's own branch. The MCP server offers `list_branches` (read-only).

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
event order. When a campaign has a protocol, it also checks that the protocol was recorded before the campaign started
and re-decides every recorded decision on the ledger as it stood; the summary then ends with ", N decisions
re-checked" (it is unchanged for ledgers without decisions). Exit code 0 only when everything matches; otherwise one plain line per problem. An export
(`translucent export <campaign-id> <out-dir> --ledger <ledger-dir>`) carries what it needs to be verified with no
provider keys; keep its folder together.

Verification shows the evidence is consistent, not when it was recorded: a fully rewritten ledger with a matching
manifest needs an external checkpoint to detect.

## MCP

A stdio MCP server exposes `list_campaigns`, `list_findings`, `get_evidence` and `verify` over one ledger, and with
`--write` the measurement tools and `propose_finding`. See [AGENTS.md](AGENTS.md).

## Recorded NIST interventions

`uv run translucent bench nist --out /path/to/output` runs offline without provider keys. The default output directory remains `evals`; the command prints the destination and announces replacement of an existing `INTERVENE.md`. Markdown is written as UTF-8 with LF line endings on every platform. Optional `--split loco|lofo|prequential` and
`--with-inferred|--without-inferred` select a registered split or a refitted sensitivity. The default runs all
three splits with and without the five inferred heater-off trials, the 7-second heater-off lag sensitivity, and the 5:1 and 20:1 cost-ratio sensitivities. The registered default is 10-second lag with costs 100 unsafe, 10 unnecessary escalation and 0.05 per watched second. It writes `INTERVENE.md`, stamped JSON
results and local ledgers under the selected output directory. The report also includes explicitly post-hoc, ledger-recomputed cost-rule baseline variants, condition-clustered sign flips and an unsourced fixed-threshold sweep; these do not change the registered primary family. `uv run translucent verify LEDGER` rechecks the decisions,
seals, paid observations, updates, scores and gate claims. The self-contained scripted fixture verifies directly:

```sh
uv run translucent verify web/public/fixtures/nist-bench.json
uv run translucent export CAMPAIGN OUT --ledger LEDGER
```

For intervention campaigns the existing `/api/campaigns/{id}/bundle` and `/graph` endpoints work without a video.
These additional GET endpoints are read-only and make no model calls:

- `/api/campaigns/{id}/intervention`: the optional v2 intervention projection, without its receipt.
- `/api/campaigns/{id}/intervention/{section}`: `trials`, `hypotheses`, `policies`, `decisions`, `scores`,
  `comparisons`, `updates`, `next_experiments`, `procedure` or `protocol`.

An absent campaign or section returns 404. POST is not supported. Revealed outcomes appear only after their
recorded reveal; planner access goes through the narrower scoped tools below. The server is an operator's audit
surface, not an outcome-hiding sandbox for a planner. Campaign comparisons include policy/split/subset score
changes and observation costs. The evidence graph connects hypotheses, policies, decisions, real outcomes,
artifacts and gate claims.

With `translucent mcp --write --ledger LEDGER`, use existing `open_campaign` with `experiment: "nist:loco"`
(or `nist:lofo`, `nist:prequential`), then `list_tools` and `run_tool` for that campaign. Its tool schema exposes
`list_trials`, `get_trace`, `compute_features`, `record_hypothesis`, `propose_policy`, `evaluate_policy`,
`submit_policy`, `predict`, `commit_decision`, `observe`, `reveal`, `advance`, `score`, `propose_claim`,
`forecast_actions`, `next_experiments` and `write_procedure`. `advance` requires the current held-out fold to be revealed. For LOCO/LOFO it creates fresh seals for the next condition/format and requires the new fold's training labels to be committed and revealed again. Submitted policy specs remain frozen. Prequential advancement registers the next Exp-number partition. The scripted planner completes every fold before its final performance claims. For LOCO/LOFO, propose the complete learning strategies before revealing any training label; subsequent fitting uses the current fold's revealed training data. Frozen comparators do not consume the agent-only submission cap K. `predict` returns separate `world_model` and `decision_policy` objects. Commit those executable outputs;
a changed probability or another policy’s observation is refused. Explicit abstentions are costed. `forecast_actions` records
a simulated two-node stop/watch/alternative-delay forecast from pre-off data, with no outcome access. `observe` requires a
prior watch commitment; post-off trace/feature requests name that policy and paid duration. No tool accepts a
filesystem path or arbitrary training ids. MCP ownership and `--write` rules remain in force. Finish with
`write_procedure`, which accepts supported claim ids and renders the declared model and decision rule with observed risk, decision cost and limits. Comparison claims use the registered paired mean-cost sign-flip test and Holm correction over submitted agent strategies times frozen baseline comparators (two × nine = 18 in the scripted campaign); unsafe McNemar remains descriptive. Trial corroboration labels appear in audit projections only after reveal.

`InterventionCampaigner` accepts `ScriptedPlanner` and exact `RecordedPlanner` replay for scripted
campaigns. For a metered planner use `translucent intervene nist --profile meta --budget USD` (default
120 tool calls per fold), or add `--muse-only` for the tool-free comparator. `--dry-run` estimates calls
and cost without contacting a provider. `--replay RECORDINGS` validates the complete request tape,
committed decision bytes and the recorded run status/counts/spend with no provider client. Each fold's
fresh planner context names the frozen registrations, submitted strategies and next steps. Frozen
comparators are registered automatically and committed by the controller before held-out reveal;
the planner cannot submit or change them. Model instruction rejections return tool errors; controller,
recording, provider rejection and token-length stops retain distinct statuses. Early scores are marked
partial. Legacy loose Muse summary files under `data/recordings/intervention` are refused because they
lack bound model-request receipts; an absent cache remains `not_run`. No recording or provider credential
is required by the benchmark. See [INTERVENTION.md](INTERVENTION.md)
for scientific limits and [CONTRACTS.md](CONTRACTS.md) for the optional bundle field.
