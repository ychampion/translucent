# Translucent

**The fact-checker that makes AI lab assistants show their receipts.**

In 2023, lithium-ion battery fires killed 18 people in New York City alone. The batteries meant to replace
today's, solid-state cells that Toyota aims to put in cars in 2027–28, still fail in the lab when lithium filaments
grow straight through them and short them out. Scientists film that failure under a microscope and turn the footage
into numbers. Here is the trap: in one published movie, each captured image is 6.9 seconds of lab time but only 0.4
seconds of playback. A normal analysis pipeline that trusts the playback clock reports the lithium growing 18×
faster than it did, and gives no warning.

Translucent stops numbers like that. An AI assistant built on Meta's models works through a recorded experiment
(the video, the lab's own clock and scale bar, and the battery's voltage log) and proposes findings. A finding is
shown as proven only when plain, checkable code can trace it back to that recorded data, with an honest error bar.
Every finding comes with a receipt: the frames, the clock, the voltage and the code behind it, which anyone can
re-check offline with one command. Translucent analyses recorded lab experiments; it does not make batteries safe by
itself. It makes the numbers people build on harder to get wrong.

## Why it matters

- **Battery failures are costly and dangerous.** Lithium-ion battery fires killed 18 people in New York City in
  2023. Samsung recalled 3.1 million Galaxy Note 7 phones over faulty batteries, at an estimated $5.3 billion. GM
  recalled all 143,000 Chevy Bolts in a battery recall costing about $2 billion.
- **Solid-state batteries are the industry's big next bet** (Toyota targets 2027–28). They replace the flammable
  liquid inside today's cells, but in the lab they "invariably fail" when lithium filaments grow through the solid
  and short the cell (Reisecker et al., Nature Communications 2023).
- **Fast charging is held back by the same metal.** Researchers call lithium plating "the major degradation
  mechanism that impairs the safety and fast charge capabilities" of car batteries (Lu et al., Nature
  Communications 2023).

These failures are studied by filming them. Turning that footage into numbers is where mistakes slip in quietly:
the wrong clock, a scale bar taken from another experiment, two movies mixed up, an outline drawn around the whole
sample instead of the filament. AI assistants make the analysis fast, and they make those mistakes fast too, with
total confidence. Translucent is built so that a number like that can't reach you.

## What it does, in plain words

1. **You ask a question** about a recorded experiment: "How fast do the lithium filaments grow, does the battery
   short when they bridge it, and does it recover when they retract?"
2. **A Muse model plans the tests, a Muse model points at objects in the frame, and SAM 3.1 outlines them.**
3. **Code measures, not the model.** Lengths, speeds and times come from fixed code that uses the lab's own clock
   and scale bar, never the video's playback speed.
4. **A rulebook decides.** Fixed code with no AI in it re-computes every proposed finding from the recorded
   measurements. It answers proven, disproven, not settled by this data, or not enough evidence yet, and it gives a
   plain reason.
5. **When a finding is turned down, the assistant reads why** and runs the test that would settle it.
6. **Every finding has a receipt** you can click down to the frames and code, share as a link or QR code, and
   re-check offline.

## Working with it: people, agents and the checker

Scientists stay in charge. The agent measures and proposes; the checker alone decides; people steer and sign.

- **Steer while it runs.** Leave the agent a note ("check both onset definitions"), pause it, or stop it. Notes
  reach the agent before its next step.
- **It asks when only a person knows.** Which cell, how long the scale bar is, what the lab meant. If nobody answers
  in time, it carries on with what the records show.
- **Sign off or challenge each finding.** A sign-off or a challenge is recorded with your name beside the checker's
  verdict. It never overrules the checker. A challenge goes back to the agent, which must re-examine the finding.
- **Bring your own recording.** Upload a video with its lab records (scale, a clock, a voltage log), each with
  where it came from. The video's frame rate is never used as a clock. See [docs/BRING-YOUR-OWN.md](docs/BRING-YOUR-OWN.md).
- **Your own agent, same rules.** Claude Code, Codex or any MCP client can run Translucent's measurement tools and
  propose findings over MCP. It cannot write a number or a verdict directly, and it gets the same plain refusals.
  See [docs/AGENTS.md](docs/AGENTS.md).
- **Interruptions are survivable.** Model hiccups are retried and metered; a stopped or interrupted campaign resumes
  from its evidence.

The Movie 4 demo campaign ran this way, live on Meta's models, with two people working beside the agent over the
HTTP API: a note before the first step, a sign-off, and a challenge the agent answered with a narrower finding that
the checker then proved (see "The Movie 4 campaign, with people in the loop" below).

## Proof

**Planted mistakes.** We planted the ordinary mistakes of video analysis, one per test: a wrong scale, the playback
clock, a swapped video, junk outlines, a voltage log that doesn't back up a short. Each test then proposes exactly
what the faulty data shows, so only a check that notices the mistake can stop it.

| | Translucent | Normal pipeline (SAM + fixed measurement code) |
|---|---|---|
| Planted data mistakes caught | **24 of 24** | 1 of 24 |
| All planted faults, including faked or retracted records | **38 of 38** | 1 of 38 |

The normal pipeline catches a mistake only if it crashes; its one catch is a length with no scale, which raised an
error. That is how a script behaves: it has no notion of whether its inputs belong together. The other 14 faults
(made-up measurement records, numbers typed into analysis code, evidence withdrawn after the fact, an over-eager
second opinion) have no equivalent in a plain script, so we report them separately.

**Why not just ask the model to double-check?** We asked Muse Spark Open, the model that gives our second opinion,
to double-check each finding the planted mistakes produced, 3 times each (120 real calls, $0.95). Given a finding and
its numbers, the way a reviewer usually sees them, it let **5 of 18** planted mistakes through
(4 of 11 data mistakes) and turned down 1 of 2 correct findings. Given Translucent's full evidence record as text,
it caught all 18 and accepted both. The record is what makes checking possible, and the rulebook runs that check on
every finding, the same way every time, at no cost. The other 20 planted mistakes never became a finding to judge,
because Translucent stopped them earlier.

**The 18× mistake, measured.** On a test filament growing at a known speed over Movie 4's real clock and scale:

| | Speed |
|---|---|
| Truth | 6.203 µm/s |
| Translucent | **6.200 ± 0.018 µm/s** (95% interval), proven |
| Normal pipeline | 112.82 µm/s, 18.2× too fast: it timed playback frames (each image is 6.9 s of lab time, 0.4 s of playback) |

**It agrees with the published paper.** On the real Movie 4 voltage log, Translucent measures the first short at
89.6 ± 3.5 s and the second at 351 ± 7 s. The paper's own voltage data puts them at 86–93 s and 345–358 s
(Reisecker et al., Nature Communications 2023). Both measured windows overlap the paper's.

**The Movie 4 campaign, with people in the loop.** Muse Wonder planned, Muse Spark pointed and SAM 3.1 outlined the
filament lanes live, while two people worked beside the agent:

- **Ana left a note from the bench** before the first step: where the electrodes are and the five lanes the
  filaments grow in. The agent outlined those lanes.
- **From the voltage log,** the rulebook proved the first short at 89.6 ± 3.5 s, the recovery at 215 s and a second
  short at 351 ± 7 s. Ana signed off the first short: "Matches the voltage log I recorded."
- **From the outlines,** on the lab's clock and scale, it proved the filaments grew at 55–72 µm/s (63.7 ± 4.0) and
  shrank back at 48–73 µm/s, and that their typical length stayed near 1440 µm (1439 ± 38).
- **Ben challenged the growth speed:** "Is this speed from the growing phase only, before the short at 90 s?" The
  agent went back, computed the speed from points before the short only, and the rulebook proved that narrower
  finding too.
- **It did not prove the filaments bridge the gap.** It saved a lesson instead: a length measured from one electrode
  can include an outline merged with the far one, so check the tip against both electrode edges first.

12 findings; `translucent verify` re-checks 12 of 12 offline. Every analysis number passed the input check described
next.

**We caught our own agent typing numbers in.** The first Meta run of Movie 4 also "proved" a growth speed, a filament
length and an electrode gap. Its receipts showed the planner's sandboxed code had copied numbers from earlier tool
output and picked its own error bars instead of reading the recorded measurements it declared. Analysis code now runs a second time on
inputs moved by a fraction of a percent, and any output or error bar that doesn't move with them is refused. It is
a planted mistake in the evals now. That run is kept only as evidence: we re-ran both demo campaigns with the check
in place.

**A second battery.** On graphite during fast charging (Lu et al. 2023), the rulebook proved that the cell voltage
first fell below 0 V after about 7.4 hours, 2.6 ± 0.7 hours before the paper's stated plating onset of 10 hours.
Whether plating is visible earlier in the movie stayed unproven. The run started with Movie 4's lessons loaded and
saved a new one: the gold colour of lithiated graphite can be mistaken for plating.

**A second opinion that knows its limits.** A vision model looks at one evidence image per proven finding. Tested
on 16 seeded cases, 3 times each (48 real calls, $0.21), it never agreed with a wrong finding (0 of 33), but it
missed a correct one 20% of the time, so it advises and can't overrule the rulebook.

**Cost.** On Meta's models, the Movie 4 campaign with the team cost $3.00 in model calls and the graphite one $0.88.

**Known weak spots,** kept in the open:
- Without a recorded clock, repeated images are not removed. The rulebook still refuses those speeds.
- The second opinion is only asked after the rulebook says "proven", so a fault the rulebook missed would reach it
  with nothing behind it.
- Lessons don't yet make the next campaign better. In a real memory test (Muse Spark Open, 3 repeats per arm,
  $0.37), a lesson about bridging pulled the agent into video work it didn't need for a short-timing question: 11
  steps instead of 6, the same 3 findings. Every lesson is loaded whatever the question; loading only relevant ones
  comes next. See [evals/MEMORY.md](evals/MEMORY.md).

## Meta's models

Both demo campaigns ran on the Meta Model API, and each replay shows which model played each role. The live server
runs the same setup (`TRANSLUCENT_PROFILE=meta`).

| Role | Model (`meta` profile, used in the demo campaigns) |
|---|---|
| Planner: chooses the next test, reads why a finding was turned down, asks people when only they know | **Muse Wonder** (Meta Model API) |
| Pointer: looks at a gridded frame and returns pixel points on filaments or electrodes | **Muse Spark** (Meta Model API) |
| Outliner: turns points and boxes into outlines and tracks them | **SAM 3.1** (Meta Model API), live, every call recorded |
| Second opinion on one evidence image, advisory | Muse Spark Open, Meta's open model (Fireworks) |

The `dev` profile runs every role on Muse Spark Open through Fireworks and replays recorded SAM 3.1 calls, which
keeps tests and evals free.

## Notes on the models

- **SAM 3.1 text prompts find nothing on battery microscopy.** Prompts such as "dendrite" or "thin dark line"
  returned no frames. **Point and box prompts work:** on Movie 1, SAM's filament tip agrees with a classical tip
  detector to a median of 4.5 px. So a Muse model points and SAM outlines.
- **Video tracking runs forward only.** Backward and both-direction tracking failed on the server side.
- **Fast growth between captured images breaks tracking.** On Movie 4 the filament jumps too far between images, so
  we re-point SAM on every captured image at fixed roots, at $0.0025 per image.
- **SAM output varies from run to run.** Every call is recorded and replays re-use the recording; the default
  `TRANSLUCENT_SAM=strict` mode never re-calls SAM and never substitutes a near-match.
- **Muse tool calling needs `tool_choice: "auto"`,** and Muse Spark Open sometimes writes a tool call as plain
  chat-template text or invents measurement ids. The runtime hides the first and asks again, and refuses the second.
- **The Meta Model API rejects some ordinary JSON-schema regexes** (a Python `\Z` gave "400 Invalid JSON schema").
  Tool schemas now use the portable subset, and a refused request ends a campaign cleanly with a plain reason.
- **Vision models need the frame's layout.** Movie 4's frames put the paper's plot beside the microscope, and the
  pointer once outlined the plot. Frames now arrive with the recorded layout and a grid in source-video pixels.

## How it works (technical)

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
    C --> F[Proven finding<br/>with a receipt]
    F --> M[Lessons<br/>carried to the next campaign]
    M --> O
```

Precise terms, each with a plain gloss:

- **Orchestrator / worker** (the planner and the pointer above). The orchestrator model plans the campaign and calls
  tools; the worker model only answers "where is it?" on one frame.
- **Insert-only evidence ledger** (a record you can add to but never edit). SQLite plus content-addressed files.
  Every measurement carries its run, input/output/code/prompt hashes, calibration, cost and uncertainty. Status
  changes are new events. Withdrawing a run marks every finding that depends on it as stale, following lineage.
- **Deterministic checker, `translucent/gate.py`** (the rulebook: same input, same answer, no model). It
  re-computes the finding from the cited measurements and checks that each traces to a successful run, the units
  are right, a length rests on a recorded scale and a speed on a recorded clock from the same recording, and that
  required **corroboration** (an independent second signal, such as the voltage collapse for a short) is present.
  It returns one of four outcomes: supported, refuted, **inconclusive** (the data can't tell the answers apart),
  or **not enough evidence** (a needed measurement is missing). A tool failure never refutes the science, and a test
  too loose to tell values apart is never "supported".
- **Time base** (which clock a frame belongs to). Measurements run on decoded source frames with a stored
  frame-to-lab-time mapping; repeated playback frames are collapsed into captured images.
- **Operando microscopy** (filming a battery while it charges and discharges).
- **Second opinion (critic).** A vision model reviews one evidence image after the checker supports a finding. It is
  advisory and can never override the checker.
- **Memory.** Lessons are recorded with their evidence and loaded at the start of the next campaign.
- **Instrument interface.** Experiments are recorded and replayed through the same interface a live microscope
  would implement.

## Run it

Requirements: Python 3.11+ with [uv](https://docs.astral.sh/uv/), and [bun](https://bun.sh) for the web demo.
Browsing, replaying and verifying need no provider keys.

```sh
uv sync --group dev
```

**CLI**

```sh
# Run a campaign on a recorded movie (paid, metered calls; --profile dev = Muse Spark Open on Fireworks,
# --profile meta = Muse Wonder + Muse Spark on the Meta Model API)
uv run translucent run --ledger runs/m4 --movie 4 --profile dev --budget 1 --steps 30 --memory on

# Export a campaign for the web demo or for sharing (no provider calls)
uv run translucent export <campaign-id> <out-dir> --ledger <ledger-dir>

# Read provider balances (read-only)
uv run translucent budget
```

**Verify a ledger or an exported bundle offline.** This re-checks every recorded finding with the checker, along
with evidence file hashes and event order. It exits 0 only if everything matches. Otherwise it prints one plain
line per problem.

```sh
uv run --group dev translucent verify <ledger-dir | export-dir>   # optional: --campaign <id>
```

**Add your own recording** (no provider calls; see [docs/BRING-YOUR-OWN.md](docs/BRING-YOUR-OWN.md)):

```sh
uv run translucent add-experiment --id cell-042 --video cell042.mp4 --title "Cell 042" --citation "Notebook p. 42"   --licence "Internal" --author "Ana" --scale-bar-px 100 --scale-bar-um 50 --scale-source "Calibration slide"   --timestamps frames.csv --clock-source "Acquisition log"
uv run translucent experiments                     # list
uv run translucent resume <campaign-id> --budget 0.5   # continue a stopped or interrupted campaign (paid)
```

**Plug in your own agent** over MCP: `claude mcp add translucent -- uv run python -m translucent.mcp_server
--ledger <dir> --write` ([docs/AGENTS.md](docs/AGENTS.md)).

**API server.** Read-only by default. People's actions (notes, answers, sign-offs, uploads) need
`TRANSLUCENT_TEAM_TOKEN`. Starting campaigns over HTTP requires `TRANSLUCENT_LIVE=1` and an admin token.

```sh
uv run translucent serve --ledger <ledger-dir> --state <state-dir>   # http://127.0.0.1:8000/api/campaigns
```

A container recipe is in [`deploy/README.md`](deploy/README.md). Endpoints are in [docs/API.md](docs/API.md).

**Web demo.** This is a static Next.js app that replays exported campaigns from `web/public/fixtures/`.

```sh
cd web
bun install
bun run dev        # http://localhost:3000
bun run build      # static export to web/out; `bun run start` serves it
```

`m4-team` and `lu-live` are exported from real paid campaigns on Meta's models, run over the HTTP API (Oct 29), and
they are the recordings the home screen offers. The thread marks each as a replay of a recorded run and names the
model behind each role. `m4-meta` and `lu-meta` are the earlier Meta runs from before the analysis code check, kept
as evidence; the Fireworks exports, `m4-dev` and `lu-dev`, are kept for comparison.
The `m1` and `lu` fixtures are hand-authored design narratives over real data, kept for UI development only and not
offered on the start screen.

**MCP server.** This is a read-only server for Claude Code or any stdio MCP client. Its tools are
`list_campaigns`, `list_findings`, `get_evidence` and `verify`. It never starts a campaign or contacts a provider.

```sh
claude mcp add --transport stdio translucent -- uv run --directory /absolute/path/to/translucent --no-sync \
  python -m translucent.mcp_server --ledger /absolute/path/to/ledger
```

**Evals**

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
evals/            fault injection, plain-pipeline baseline, critic eval, model double-check, memory test;
                  REPORT.md, CRITIC.md, LLM_JUDGE.md, MEMORY.md
data/groundtruth/ published numbers digitised from the papers, with provenance (README.md)
web/              Next.js app (app/, components/, lib/)
scripts/          campaign runner, SAM recording, time bases; groundtruth/ derives the lab records from the papers
runs/             recorded campaign ledgers used by evals and the container
tests/            pytest suite
deploy/           API and web containers
docs/             API, agents over MCP, bringing your own recording, replay-bundle schema
```

## Data and licences

The experiments come from two open-access papers. Their articles and supplementary movies are published under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The videos are used unchanged, and the
overlays and measurements are ours.

- Reisecker, V., Flatscher, F., Porz, L., et al. *Effect of pulse-current-based protocols on the lithium dendrite
  formation and evolution in all-solid-state batteries.* Nat. Commun. 14, 2432 (2023).
  [doi:10.1038/s41467-023-37476-y](https://doi.org/10.1038/s41467-023-37476-y). Supplementary Movies 1-5.
- Lu, X., Lagnoni, M., Bertei, A., et al. *Multiscale dynamics of charging and plating in graphite electrodes
  coupling operando microscopy and phase-field modelling.* Nat. Commun. 14, 5127 (2023).
  [doi:10.1038/s41467-023-40574-6](https://doi.org/10.1038/s41467-023-40574-6). Supplementary Movie 1 and
  Source Data.

Code licence: [MIT](LICENSE).
