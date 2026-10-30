# Translucent

Translucent is a research agent for recorded battery experiments. It checks microscopy measurements and tests
intervention strategies on NIST heating-interruption records. Scientists and the agent work on the same evidence
record. The agent plans tests, measures the recording and proposes findings. A deterministic rulebook,
implemented in code with no model, decides which findings are proven. People steer the work, answer questions and
sign off or challenge findings. Their reviews remain beside the rulebook's verdict.

Each microscopy finding links to its frames, lab clock, voltage data and analysis code. This evidence page,
called a receipt, can be shared and checked offline with one command. Translucent checks the analysis of recorded
experiments. It does not establish that a battery is safe.

The [microscopy campaigns](#evaluation) measure filament growth and check short timing against voltage records.
The [NIST benchmark](#nist-heating-interruption) tests interventions after heater-off. No agent strategy beats
always-escalate after correction. Both demonstrations produce receipts that anyone can check offline.

## Battery research

In 2023, lithium-ion battery fires killed 18 people in New York City alone. Samsung recalled 3.1 million Galaxy
Note 7 phones over faulty batteries, at an estimated $5.3 billion. GM recalled all 143,000 Chevy Bolts in a battery
recall costing about $2 billion.

Solid-state cells replace the flammable liquid in today's batteries. Toyota aims to put them in cars in 2027–28.
In the lab, these cells still "invariably fail" when lithium filaments grow through the solid and short the cell
(Reisecker et al., Nature Communications 2023). Lithium plating also limits fast charging. Researchers call it
"the major degradation mechanism that impairs the safety and fast charge capabilities" of car batteries
(Lu et al., Nature Communications 2023).

Scientists film these processes under a microscope and measure the footage. Errors can enter through a playback
clock used as lab time, a scale bar from another experiment, swapped movies or an outline around the whole sample.
In one published movie, each captured image is 6.9 seconds of lab time but 0.4 seconds of playback. An analysis
that trusts the playback clock reports the filaments growing 18× faster than they did, with no warning. An agent
can carry errors like that into a finding. Translucent checks whether each measurement uses the recorded
clock and calibration, and whether the evidence supports the proposed finding.

## Research workflow

1. A scientist asks about a recorded experiment: "How fast do the lithium filaments grow, does the battery
   short when they bridge it, and does it recover when they retract?"
2. A Muse model plans tests. A Muse model locates objects in the frame, and SAM 3.1 outlines them.
3. Fixed code measures lengths, speeds and times using the lab's clock and scale bar. Playback speed is never
   used as lab time.
4. The rulebook re-computes each proposed finding from its recorded measurements. It returns proven, disproven,
   not settled by this data, or not enough evidence yet, with a reason.
5. When a finding is rejected, the agent reads the reason and runs the test needed to resolve it.
6. People inspect the evidence down to the frames and code. They can share a link or QR code and re-check it
   offline.

## Working together

Scientists can leave a note while the agent runs, pause it or stop it. A note such as "check both onset definitions"
reaches the agent before its next step. The agent asks people for information the records cannot supply, such as
the cell identity or the scale-bar length. If nobody answers in time, it continues with the available records.

A sign-off or challenge is recorded with the person's name beside the checker's verdict. Neither changes that
verdict. A challenge sends the finding back to the agent for re-examination. Failed model calls are retried
and metered. A stopped or interrupted campaign resumes from its evidence.

Scientists can upload a video with its scale, clock and voltage log, recording the source of each. The video's
frame rate is never used as a clock. See [docs/BRING-YOUR-OWN.md](docs/BRING-YOUR-OWN.md).

Claude Code or any MCP client can run Translucent's measurement tools and propose findings over MCP. The client
cannot write a measurement number or verdict directly. The same checks and rejection reasons apply. See
[docs/AGENTS.md](docs/AGENTS.md).

The Movie 4 campaign below records this collaboration over the HTTP API, live on Meta's models. Ana supplied a
note and signed off a finding. Ben challenged another, and the agent returned a narrower finding that the checker
proved.

## Evaluation

### Planted mistakes

Each test plants one mistake, then proposes the finding implied by the faulty data. The cases include a wrong
scale, playback timing, swapped video, invalid outlines and a voltage log that does not support a short. A check
must detect the faulty evidence to reject the finding.

| | Translucent | Normal pipeline (SAM + fixed measurement code) |
|---|---|---|
| Planted data mistakes caught | **24 of 24** | 1 of 24 |
| All planted faults, including faked or retracted records | **38 of 38** | 1 of 38 |

The normal pipeline catches a mistake only if it crashes. Its one catch was a length with no scale, which raised
an error. It does not check whether its inputs belong together. The other 14 faults concern fabricated
measurement records, numbers typed into analysis code, withdrawn evidence and an overreaching second opinion.
These have no equivalent in the baseline script, so they are reported separately.

### Model double-check

We asked Muse Spark Open, the model used for the second opinion, to check each finding produced by the planted
mistakes, 3 times each (120 real calls, $0.95). Given a finding and its numbers, it accepted **5 of 18** planted
mistakes (4 of 11 data mistakes) and rejected 1 of 2 correct findings. Given the full evidence record as text, it
caught all 18 and accepted both correct findings.

This experiment shows that the evidence supplied to the reviewer affects the result. The rulebook checks that
record for every finding without a model call. The other 20 planted mistakes were stopped before they produced a
finding for the model to judge.

### The 18× timing error

A test filament grows at a known speed using Movie 4's recorded clock and scale. Timing the same images by
playback produces the following error:

| | Speed |
|---|---|
| Truth | 6.203 µm/s |
| Translucent | **6.200 ± 0.018 µm/s** (95% interval), proven |
| Normal pipeline | 112.82 µm/s, 18.2× too fast: it timed playback frames (each image is 6.9 s of lab time, 0.4 s of playback) |

### Agreement with the paper

On the real Movie 4 voltage log, Translucent measures the first short at 89.6 ± 3.5 s and the second at 351 ± 7 s.
The paper's voltage data puts them at 86–93 s and 345–358 s (Reisecker et al., Nature Communications 2023).
Both measured windows overlap the paper's.

### Movie 4 team campaign

Muse Wonder planned the tests. Muse Spark located the filament lanes, and SAM 3.1 outlined them live. Ana left a
note before the first step describing the electrodes and the five lanes where the filaments grow. The agent
outlined those lanes.

From the voltage log, the rulebook proved the first short at 89.6 ± 3.5 s, recovery at 215 s and a second short at
351 ± 7 s. Ana signed off the first short: "Matches the voltage log I recorded."

Using the outlines with the lab's clock and scale, it proved growth at 55–72 µm/s (63.7 ± 4.0) and retraction at
48–73 µm/s. It also proved that the typical filament length stayed near 1440 µm (1439 ± 38).

Ben challenged the growth speed: "Is this speed from the growing phase only, before the short at 90 s?" The agent
computed the speed using only points before the short. The rulebook proved that narrower finding too.

The campaign did not prove that the filaments bridge the gap. It recorded a lesson: an outline measured from one
electrode can merge with the far electrode. The tip must be checked against both electrode edges first.

The campaign produced 12 findings; `translucent verify` re-checks 12 of 12 offline. Every analysis number passed
the input check described below.

### Detecting numbers typed by our own agent

The first Meta run of Movie 4 also "proved" a growth speed, a filament length and an electrode gap. Inspection of
the evidence showed that the planner's sandboxed code had copied numbers from earlier tool output and chosen its
own error bars. It had not read the recorded measurements it declared as inputs.

Analysis code now runs a second time with inputs changed by a fraction of a percent. Any output or error bar that
does not change with them is rejected. This failure is now a planted mistake in the evals. The earlier run remains
as evidence of the failure. Both microscopy campaigns were re-run with the check in place.

### Graphite during fast charging

On graphite during fast charging (Lu et al. 2023), the rulebook proved that cell voltage first fell below 0 V after
about 7.4 hours, 2.6 ± 0.7 hours before the paper's stated plating onset of 10 hours. The campaign did not prove
whether plating was visible earlier in the movie. It started with Movie 4's lessons loaded and recorded another:
the gold colour of lithiated graphite can be mistaken for plating.

### Visual second opinion

A vision model reviews one evidence image per proven finding. In 16 seeded cases tested 3 times each (48 real
calls, $0.21), it never agreed with a wrong finding (0 of 33). It missed a correct finding 20% of the time. This
review is advisory and cannot overrule the rulebook.

### Cost

On Meta's models, model calls for the Movie 4 team campaign cost $3.00. The graphite campaign cost $0.88.

### Known limits

- Without a recorded clock, repeated images are not removed. The rulebook still refuses the resulting speeds.
- The visual second opinion runs only after the rulebook returns "proven". A fault the rulebook misses gets only
  that one advisory review.
- Loading lessons has not improved the next campaign in the tested task. In a real memory test (Muse Spark Open,
  3 repeats per arm, $0.37), a lesson about bridging led the agent into unnecessary video analysis for a question
  about short timing. It took 11 steps instead of 6 and produced the same 3 findings. Every lesson is loaded
  regardless of the question. Selecting relevant lessons remains future work. See [evals/MEMORY.md](evals/MEMORY.md).

## NIST heating-interruption

When a lab or battery management system cuts the heat to a cell under thermal abuse, it must decide whether
stopping the heat is enough. NIST's heating-interruption experiments (Tam et al., Applied Thermal Engineering)
recorded cells that ignited after their surface temperature had fallen. In nine 21700 trials the surface cooled at
least 2 °C after heater-off and the cell still ignited; in Exp 81 it fell 14.6 °C over 182 s and the cell ignited
297 s after heater-off. Translucent tests decisions at that recorded heater-off point.

The agent records competing mechanistic hypotheses and proposes executable strategies. Each combines a world
model with a cost-based decision rule. It screens strategies on training folds only, then commits to stand down,
escalate, or pay to watch 30 or 60 s before the controller reveals the outcome. Outcomes are sealed as hashes.
The agent labels all forecasts simulated. Leave one condition out is the primary split; leave one format out
and prequential evaluation test transfer and sequential updates.

The registered costs are fixed before scoring: unsafe stand-down costs 100, ignition during a watch costs 100,
unnecessary escalation costs 10, and watching costs 0.05 per second. Escalating on a trial that ignites costs 0;
the experiments did not observe whether escalation would have helped. The seals establish the record's internal
order; the public dataset had already been studied.

`translucent verify` recomputes every fit, every decision, every score and every gate verdict offline without
keys. It rejects forged ledgers from an independent review with specific reasons: edited ignition times fail
the outcome checks, a fit trained on held-out labels fails the refit checks, and Muse decisions without receipts
fail the model-request checks. The 1500-decision scripted receipt verifies in about one minute.

No agent strategy beats always-escalate after Holm correction. On the 50-trial primary split, the best scripted
agent costs 3.72 per trial against 3.80 for always-escalate (Holm p = 1). These are mean registered costs;
they do not measure the effectiveness of an actual rescue.

Across two live Muse Wonder planner campaigns, costing about $4 each, the best strategies recorded 0 unsafe
stand-downs and 2–4 fewer missed rescues than always-escalate. The differences were never significant
(raw p = 0.125–0.5, Holm p = 1). A missed rescue here means escalating on a trial that did not ignite.

The deterministic gate refused the agent's own claim that it beat always-escalate at 3.4 versus 3.8 mean cost
(p = 0.5, Holm p = 1). The operating procedure the agent then wrote cites only supported claims.
On the same trials, Muse Wonder deciding alone made 4 and then 6 unsafe stand-downs; every procedure the agent
wrote through the harness made none. The pattern held in both campaigns, but it was not a registered comparison
and does not survive the correction (raw p = 0.125 and 0.031).

The gate records wins over the fixed 170 °C rule and the two physics models. Those wins reflect a decision-rule
asymmetry and baselines that never discriminate under their classification rules. The temperature cut is
illustrative and unsourced. Applying the common cost rule removes the fixed-temperature advantage; no
condition-clustered comparison survives correction. Post hoc, a 155 °C threshold picked on these same trials
costs 2.4. That is a descriptive check on how little signal is available, not evidence for a selected policy.

The agent runs from recorded observations through an executable model to committed interventions and sealed
outcomes on real data. Anyone with the receipt can check every number. The system refused to overclaim its result.

See [the intervention protocol](docs/INTERVENTION.md) and [benchmark results](evals/INTERVENE.md).
The exported [scripted receipt](web/public/fixtures/nist-bench.json) contains the evidence for offline checking.
[Run the benchmark or estimate a live campaign](#nist-interventions).

## Meta's models

Both microscopy campaigns ran on the Meta Model API. Each replay names the model used for each role. The live
server uses the same configuration (`TRANSLUCENT_PROFILE=meta`).

| Role | Model (`meta` profile, used in the microscopy campaigns) |
|---|---|
| Planner: selects tests, reads rejection reasons and asks people for missing information | Muse Wonder (Meta Model API) |
| Pointer: reads a gridded frame and returns pixel points on filaments or electrodes | Muse Spark (Meta Model API) |
| Outliner: converts points and boxes to outlines and tracks them | SAM 3.1 (Meta Model API), live, every call recorded |
| Second opinion: reviews one evidence image; advisory | Muse Spark Open, Meta's open model (Fireworks) |

The `dev` profile runs every role on Muse Spark Open through Fireworks and replays recorded SAM 3.1 calls. Tests
and evals use these recordings without paid calls.

## Model observations

- SAM 3.1 text prompts such as "dendrite" or "thin dark line" returned no frames on battery microscopy. Point and
  box prompts worked. On Movie 1, SAM's filament tip agreed with a classical tip detector to a median of 4.5 px.
  The Muse model therefore supplies points for SAM to outline.
- Video tracking worked forward only. Backward and both-direction tracking failed on the server side.
- In Movie 4, rapid growth between captured images broke tracking. We re-point SAM on every captured image at
  fixed roots, at $0.0025 per image.
- SAM output varies between runs. Every call is recorded, and replays use that recording. The default
  `TRANSLUCENT_SAM=strict` mode never re-calls SAM or substitutes a near-match.
- Muse tool calling requires `tool_choice: "auto"`. Muse Spark Open sometimes emits a tool call as chat-template
  text or invents measurement ids. The runtime hides the text and retries. It rejects invented ids.
- The Meta Model API rejected some JSON-schema regexes: a Python `\Z` gave "400 Invalid JSON schema". Tool schemas
  now use the portable subset. A rejected request ends a campaign with a recorded reason.
- The pointer once outlined the paper's plot beside the microscopy image in Movie 4. Frames now include the
  recorded layout and a grid in source-video pixels.

## Measurement and evidence

```mermaid
flowchart LR
    Q[Research question] --> O[Planner<br/>Muse model chooses the next test]
    O -->|"point at this"| W[Pointer<br/>Muse model reads a gridded frame]
    W -->|pixel points| S[SAM 3.1<br/>outlines the filament]
    O --> T[Measurement tools<br/>fixed code]
    S --> T
    R[(Recorded experiment<br/>video, clock, scale bar, voltage log)] --> T
    T -->|every number| L[(Evidence record<br/>append-only)]
    O -->|propose a finding| G{Rulebook<br/>fixed code, no model}
    L --> G
    G -->|proven| C[Second opinion<br/>vision model, advisory]
    G -->|turned down: reason| O
    C --> F[Proven finding<br/>linked evidence]
    F --> M[Lessons<br/>carried to the next campaign]
    M --> O
```

The planner runs the campaign and calls tools. The pointer only locates objects in one frame. Measurements use
decoded source frames and a stored frame-to-lab-time mapping. Repeated playback frames are collapsed into captured
images when a recorded clock is available. Operando microscopy records the battery as it charges and discharges.

The evidence ledger uses SQLite and content-addressed files. Records can be added but never edited. Each
measurement stores its run, input/output/code/prompt hashes, calibration, cost and uncertainty. Status changes are
new events. Withdrawing a run marks dependent findings as stale through their evidence lineage.

The deterministic checker in `translucent/gate.py` re-computes each finding from its cited measurements. It checks
that they trace to successful runs and have the correct units. A length requires a recorded scale; a speed
requires a recorded clock from the same recording. Findings that need independent corroboration must include it,
such as voltage collapse for a short.

The checker returns one of four outcomes: supported, refuted, inconclusive or not enough evidence. Inconclusive
means the data cannot distinguish the answers. Not enough evidence means a required measurement is missing.
A tool failure never refutes the science. A test too imprecise to distinguish values cannot return supported.

After the checker supports a finding, the vision model reviews one evidence image. This advisory second opinion cannot
override the checker. Lessons are stored with their evidence and loaded at the next campaign's start. Experiments
are recorded and replayed through the same instrument interface a live microscope would implement.

## Run it

Requirements: Python 3.11+ with [uv](https://docs.astral.sh/uv/), and [bun](https://bun.sh) for the web demo.
Browsing, replaying and verifying need no provider keys.

```sh
uv sync --group dev
```

### CLI

```sh
# Run a campaign on a recorded movie (paid, metered calls; --profile dev = Muse Spark Open on Fireworks,
# --profile meta = Muse Wonder + Muse Spark on the Meta Model API)
uv run translucent run --ledger runs/m4 --movie 4 --profile dev --budget 1 --steps 30 --memory on

# Export a campaign for the web demo or for sharing (no provider calls)
uv run translucent export <campaign-id> <out-dir> --ledger <ledger-dir>

# Read provider balances (read-only)
uv run translucent budget
```

### NIST interventions

These commands make no provider calls. Use a fresh output directory to preserve published reports.

```sh
# Rebuild the scripted benchmark across the registered splits and sensitivities
uv run translucent bench nist --out runs/nist-benchmark

# Verify the self-contained receipt without keys
uv run translucent verify web/public/fixtures/nist-bench.json

# Estimate a Muse Wonder planner campaign without contacting a provider
uv run translucent intervene nist --profile meta --budget 25 --split loco --dry-run
```

To replay a recorded planner campaign, use `--replay <recordings-dir>` with its original profile, budget, split
and step cap, and a fresh ledger directory. See
[the replay instructions](docs/API.md#nist-live-planner-and-recorded-replay).

### Offline verification

Verification re-checks every recorded finding in a ledger or exported bundle, including evidence file hashes and
event order. It exits 0 only if everything matches. Otherwise it prints one line per problem.

```sh
uv run --group dev translucent verify <ledger-dir | export-dir>   # optional: --campaign <id>
```

### Add a recording

Importing a recording needs no provider calls. See [docs/BRING-YOUR-OWN.md](docs/BRING-YOUR-OWN.md).

```sh
uv run translucent add-experiment --id cell-042 --video cell042.mp4 --title "Cell 042" --citation "Notebook p. 42"   --licence "Internal" --author "Ana" --scale-bar-px 100 --scale-bar-um 50 --scale-source "Calibration slide"   --timestamps frames.csv --clock-source "Acquisition log"
uv run translucent experiments                     # list
uv run translucent resume <campaign-id> --budget 0.5   # continue a stopped or interrupted campaign (paid)
```

### Agent access

To connect an agent over MCP, use `claude mcp add translucent -- uv run python -m translucent.mcp_server
--ledger <dir> --write` ([docs/AGENTS.md](docs/AGENTS.md)).

### API server

The API is read-only by default. People's actions, including notes, answers, sign-offs and uploads, need
`TRANSLUCENT_TEAM_TOKEN`. Starting campaigns over HTTP requires `TRANSLUCENT_LIVE=1` and an admin token.

```sh
uv run translucent serve --ledger <ledger-dir> --state <state-dir>   # http://127.0.0.1:8000/api/campaigns
```

A container recipe is in [`deploy/README.md`](deploy/README.md). Endpoints are in [docs/API.md](docs/API.md).

### Web demo

The static Next.js app replays exported campaigns from `web/public/fixtures/`.

```sh
cd web
bun install
bun run dev        # http://localhost:3000
bun run build      # static export to web/out; `bun run start` serves it
```

The home screen offers `m4-team` and `lu-live`, exported from real paid campaigns on Meta's models over the HTTP API
(Oct 29). The thread labels each as a recorded replay and names the model used for each role. The earlier Meta
runs, `m4-meta` and `lu-meta`, predate the analysis code check and remain as evidence. The Fireworks exports,
`m4-dev` and `lu-dev`, remain for comparison.

The `m1` and `lu` fixtures are hand-authored design narratives over real data. They are used only for UI
development and are not offered on the start screen.

### Read-only MCP server

The read-only server supports Claude Code or any stdio MCP client. Its tools are `list_campaigns`, `list_findings`,
`get_evidence` and `verify`. It never starts a campaign or contacts a provider.

```sh
claude mcp add --transport stdio translucent -- uv run --directory /absolute/path/to/translucent --no-sync \
  python -m translucent.mcp_server --ledger /absolute/path/to/ledger
```

### Evals

```sh
uv run python -m evals.run            # fault injection vs plain pipeline, offline -> evals/REPORT.md
uv run python -m evals.critic_eval    # critic harness, offline; --real --repeats 3 --budget-usd 2.5 is paid
uv run --group dev pytest tests -q
```

Every paid call is metered against hard provider caps (`translucent/budget.py`, `translucent/providers.py`).

## Project layout

```
translucent/      runtime (runtime.py), checker (gate.py), ledger (ledger.py), tools (tools.py),
                  SAM recording/replay (recordings.py), outline QC (qc.py), sandboxed analysis (analysis.py),
                  offline verify (verify.py), API (server.py), MCP (mcp_server.py), CLI (cli.py)
                  NIST intervention controller, executable world models and cost-based decision rules
evals/            fault injection, plain-pipeline baseline, critic eval, model double-check, memory test;
                  REPORT.md, CRITIC.md, LLM_JUDGE.md, MEMORY.md
                  NIST intervention benchmark (INTERVENE.md), generated results and ledgers
data/groundtruth/ published numbers digitised from the papers, with provenance (README.md)
web/              Next.js app (app/, components/, lib/)
                  scripted NIST receipt (public/fixtures/nist-bench.json)
scripts/          campaign runner, SAM recording, time bases; groundtruth/ derives the lab records from the papers
runs/             recorded campaign ledgers used by evals and the container
state/            live intervention ledgers and private planner recordings (git-ignored)
tests/            pytest suite
deploy/           API and web containers
docs/             API, agents over MCP, bringing your own recording, replay-bundle schema
                  intervention protocol (INTERVENTION.md), intervention bundle contract (CONTRACTS.md)
```

## Data and licences

The microscopy experiments come from two open-access papers. Their articles and supplementary movies are
published under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The videos are used unchanged.
The overlays and measurements are ours.

- Reisecker, V., Flatscher, F., Porz, L., et al. *Effect of pulse-current-based protocols on the lithium dendrite
  formation and evolution in all-solid-state batteries.* Nat. Commun. 14, 2432 (2023).
  [doi:10.1038/s41467-023-37476-y](https://doi.org/10.1038/s41467-023-37476-y). Supplementary Movies 1-5.
- Lu, X., Lagnoni, M., Bertei, A., et al. *Multiscale dynamics of charging and plating in graphite electrodes
  coupling operando microscopy and phase-field modelling.* Nat. Commun. 14, 5127 (2023).
  [doi:10.1038/s41467-023-40574-6](https://doi.org/10.1038/s41467-023-40574-6). Supplementary Movie 1 and
  Source Data.

Code licence: [MIT](LICENSE).
