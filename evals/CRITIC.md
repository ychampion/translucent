# Critic eval: does the visual second opinion catch wrong findings?

After the deterministic gate accepts a finding, a vision model (the "critic", Muse Spark Open on Fireworks) looks at
one evidence image and answers SUPPORTED, REFUTED or INCONCLUSIVE. It is advisory: the gate decides. This eval
measures how far the demo can lean on it.

## Method
- **Seeded cases** (`evals/critic_cases/manifest.json`, 16 cases, images next to it). Each case has a statement, an
  image built from real repo data, the expected verdict and why.
  - Movie 4 cell-voltage plot, drawn by the runtime's own `_voltage_plot` with the finding's times marked in red.
    Event times come from `data/groundtruth/reisecker2023.json` (`movie4_voltage_events`): first short 86-93 s,
    voltage leaves 0 V 211-218 s, second short 345-358 s (not ~375 s).
  - 4 correct timing findings (90 s, 215 s, 351 s, the 90-215 s flat stretch).
  - 5 timing findings off by 49-105 s (180, 160, 320, 260, 400 s): expected REFUTED.
  - 2 wrong-direction findings ("rises" at 215 s, "falls" at 351 s): expected REFUTED.
  - Real Movie 1 frames (`tools._frame`): the last frame, where the filament spans the gap (correct statement);
    the same frame with "only a tenth of the gap" (wrong magnitude); the first frame with "spans the gap" (wrong).
  - 2 statements the image cannot show (temperature on the voltage plot, a speed from one frame): expected INCONCLUSIVE.
- **Same call as the runtime.** The harness sends `runtime.critic_messages(statement, image)` with the runtime's
  `max_tokens` and `reasoning_effort`, and reads the reply with `runtime.critic_verdict`.
- **Verdict parsing fails closed.** Only a first line that is one plain verdict word counts (markdown, a "Verdict:"
  label and an explanation after punctuation are fine). "Not supported", "SUPPORTED? No.", "SUPPORTED, but...",
  two verdicts, or a verdict on line 2 all read as INCONCLUSIVE, never as agreement.
- **Scores.** agreement (verdict = expected); **false-agree** (SUPPORTED on a wrong or not-readable finding: the
  number that matters); unclear (inconclusive or failed call); missed-correct (correct finding not supported); cost.

## Run
```
python -m evals.critic_eval                    # offline: scripted critic (harness check) and always-agree critic
python -m evals.critic_eval --build            # rebuild images + manifest from data/
python -m evals.critic_eval --real --repeats 3 --budget-usd 2.5   # paid; writes evals/critic_cases/results/<ts>.json
```
`--real` prints the worst-case cost (the amount the metered client reserves: 4000 tokens per image plus the full
`max_tokens` output, x1.25 safety) before any call and refuses to start if it exceeds `--budget-usd` or if no budget
is given. Every call goes through `providers.MeteredFireworks`, so the Fireworks hard cap and pause switch apply.
For 16 cases x 3 repeats the worst case is $2.14 ($0.045 per call); real spend should be well below that.

Offline (fake critics, no network): scripted 100% agreement, 0% false-agree; always-agree 100% false-agree on the
11 wrong or unreadable findings, which is what a critic that says yes to everything looks like.

## Caveats
- Frame images are full 1920x1080 PNGs (~3.4 MB, as the runtime sends frames); if the provider rejects the request
  size, those cases show up as `error` (counted as unclear), not as agreement.
- 16 cases x 3 repeats is a small sample: report false-agree with its count, not as a precise rate.
