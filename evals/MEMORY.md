# Memory test with a real model: memory on vs off

Run 2026-10-29 with `uv run python -m evals.ablation --real --profile dev --n 3` (Muse Spark Open on Fireworks for
every role, SAM replayed in strict mode). Raw results: `evals/results/ablation-real-20261029T212224.json`. Total cost
$0.37 for 7 campaigns.

**Setup.** Campaign 1 asks when Movie 4's cell shorts and recovers, and whether the electrical record agrees with
the video. It ran once with memory on and recorded one lesson. Its ledger was then copied for each of 3 repeats per
arm. Campaign 2 asks only "When does the cell short? State it with an uncertainty the lab's records support", with
memory on (the lesson is loaded) or off. Each campaign has a $1 budget and at most 12 steps.

**The lesson campaign 1 recorded:** "In operando battery microscopy, a voltage drop to near zero alone is not proof
of a filament bridging event without synchronized visual measurement of filament length."

| Campaign 2 | Steps | Tool runs | Tool failures | Findings earned | Turned down | Cost |
|---|---:|---:|---:|---:|---:|---:|
| Memory on (mean of 3) | 11.3 | 17.7 | 2 | 3 | 0.33 | $0.063 |
| Memory off (mean of 3) | 6.3 | 10.3 | 0 | 3 | 0.67 | $0.040 |

**Result: memory did not help on this task.** Both arms earned the same 3 findings. With the lesson loaded, the agent
did more work: in the first repeat it inspected the video six times, and in every repeat it tried to outline filaments (the two failures are outline calls
with no recording to replay, an artefact of strict replay) before answering a question the voltage log settles on
its own. The lesson is sound for its own question, whether a filament bridged the gap, but it is not about short
timing, and every lesson in the ledger is loaded whatever the question.

**What it means.** Recording lessons with their evidence works. Loading all of them into every campaign does not
make the next campaign better, and can make it slower. The next step is to load only lessons relevant to the new
question, then measure again on equal budgets, including on a second experiment (`--movie 1`).

n = 3 per arm on one task: a measured direction, not a statistical result.
