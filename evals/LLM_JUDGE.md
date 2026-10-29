# Evidence-only model double-check

Generated from `evals/results/llm-judge-real-20261029.json`.

Harness checks are scripted, not model-quality evidence. Paid results are reported only under `real`.

| Judge | Evidence | Split | Cases | Judged | Caught | Waved through | Trusted caught | No proposal |
|---|---|---|---:|---:|---:|---:|---:|---:|
| real | numbers | overall | 38 | 18 | 13 | 5 | 38 | 20 |
| real | numbers | data_path | 24 | 11 | 7 | 4 | 24 | 13 |
| real | numbers | ledger_only | 14 | 7 | 6 | 1 | 14 | 7 |
| real | full | overall | 38 | 18 | 18 | 0 | 38 | 20 |
| real | full | data_path | 24 | 11 | 11 | 0 | 24 | 13 |
| real | full | ledger_only | 14 | 7 | 7 | 0 | 14 | 7 |

| Judge | Evidence | Clean accepted | Clean false rejections | Call errors | Incomplete cases |
|---|---|---:|---:|---:|---:|
| real | numbers | 1 | 1 | 0 | 0 |
| real | full | 2 | 0 | 0 | 0 |

`numbers` includes the statement, its predicate bounds, cited quantities/values/units/uncertainties, and recording labels. `full` adds recorded measurements (including competing calibrations), run tools, status, inputs/parameters/outputs, artifact ancestry, sources, experiment ids, clock sources, and raw run invalidations. Both are text-only; no evidence image is attached.

Proposals and evidence are captured before admission/review. Retractions add only the later raw invalidation entries. Cases stopped before any proposal remain visible as unjudged; no synthetic finding or measurement is substituted. These rows cannot measure model quality. Case errors and interrupted runs also remain separate. The full ledger is not a raw blob dump: artifact metadata and measurement series are included, mask pixels and tool summaries are not.

A case is accepted in a repeat if any of its proposals receives SUPPORTED. Strict majority over repeats accepts it; ties fail closed. Any other parsed response catches a negative case and rejects a clean control. Transport errors count fail closed and are also reported separately. JSON includes per-class scores, each repeat's binary agreement, and repeat consistency. Scripted answers use labels outside the prompt; they are not a reasoning baseline.

Identifiers are consistently aliased within a prompt. Fixture-only uncertainty text and synthetic outline labels are neutralized; scientific values, uncertainty kinds, run status and provenance links are retained. Campaigns, reviews, gate results and case descriptions never enter prompts.

Worst-case estimate for 3 repeat(s): **$14.122800**, 120 calls. This covers every captured proposal and both clean controls in both variants; no-proposal cases incur no call. The shared estimator includes SDK retry allowance even though the shared run loop disables automatic retries.

```sh
uv run --group dev python -m evals.llm_judge --repeats 3 --out /tmp/llm-judge.json
uv run --group dev python -m evals.llm_judge --estimate-only --repeats 3
# Paid, for the orchestrator only; choose a budget >= the printed estimate:
uv run --group dev python -m evals.llm_judge --real --repeats 3 --budget-usd 20 --out /tmp/llm-judge-real.json
# Generate this summary from an existing JSON without rerunning:
uv run --group dev python -m evals.llm_judge --from-results /tmp/llm-judge.json --summary evals/LLM_JUDGE.md
```

`--out` writes JSON and appends an adjacent `.jsonl` journal, flushing each response. Use a fresh output name for each run. `--summary PATH` regenerates the summary from the written JSON. Real runs use the runtime's Fireworks Muse Spark Open critic, unchanged 6000-token ceiling and medium reasoning setting, through `critic_eval.run` and `MeteredFireworks`. The call loop, budget checks, retry accounting, parser and journal are shared with `critic_eval`; only its message builder is temporarily adapted for this single-threaded CLI.
