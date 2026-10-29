# Translucent evals — fault injection vs a plain pipeline

Generated 2026-10-29T16:17:33 by `uv run python -m evals.run` (323.8 s, offline, TRANSLUCENT_SAM=strict, sockets disabled). Raw results: `evals/results/20261029T161733.json`.

**Translucent catches 38 of 38 injected faults; the plain pipeline catches 1.**

On the 24 data-path faults (calibration, time base, video identity, masks, corroboration) that a plain script also faces: Translucent 24/24, baseline 1/24. The other 14 (fabricated records, retraction, critic) concern the evidence ledger itself; a plain script has no analogue, so it cannot catch them by construction.

How to read it: each case builds a fresh ledger, plants one fault, and runs the real tools (`tools.call`) or, where the fault is in a record itself, inserts that record (`via=record`). Then the *naive claim* is proposed: exactly what the faulty evidence shows (value ± its uncertainty, padded). So only a check that notices the fault can stop it, never a number that happens to disagree. *Caught* = the faulty evidence did not become an earned finding. The baseline (`evals/baseline.py`) catches a fault only if it crashes or outputs a non-finite number.

Where Movie 4 needs clean filament masks, a scripted SAM output (one thin filament growing 1 px per recorded second, ±1.5 px jitter) goes through the real `track_objects` code, because only junk point-prompt masks are recorded for Movie 4. Movie 1 uses its real recorded SAM track; the junk-mask case uses the 49 real Movie 4 recordings.

| # | Fault class | Variant | via | Translucent | How | Baseline |
|---|---|---|---|---|---|---|
| 1 | spatial calibration | SI scale bar (1.57x) on record next to the right one | record | **caught** | gate:unsupported | not caught |
| 2 | spatial calibration | SI scale computed in the sandbox | tool | **caught** | tool_failure | not caught |
| 3 | spatial calibration | scale from another experiment | tool | **caught** | tool_failure | not caught |
| 4 | spatial calibration | length in um with no calibration | record | **caught** | gate:unsupported | caught (error: TypeError: unsupported operand ty) |
| 5 | spatial calibration | clock passed as the scale | tool | **caught** | tool_failure | not caught |
| 6 | time base | playback frames, Movie 1 (no recorded clock) | tool | **caught** | gate:unsupported | not caught |
| 7 | time base | playback frames although Movie 4 has a clock | tool | **caught** | gate:unsupported | not caught |
| 8 | time base | playback-timed speed recomputed in the sandbox | tool | **caught** | gate:unsupported | not caught |
| 9 | time base | playback timing recorded as a 'time_base' | record | **caught** | gate:unsupported | not caught |
| 10 | swapped video | Movie 4 voltage corroborates a Movie 1 short | record | **caught** | gate:unsupported | not caught |
| 11 | swapped video | Movie 4 clock applied to a Movie 1 track | tool | **caught** | tool_failure | not caught |
| 12 | swapped video | Movie 1 imported under Movie 4's experiment id | tool | **caught** | import refused | not caught |
| 13 | junk masks | real Movie 4 point-prompt masks (whole-sample outlines) | tool | **caught** | tool_flag | not caught |
| 14 | junk masks | box covers most of the image | tool | **caught** | tool_flag | not caught |
| 15 | junk masks | electrode-shaped box | tool | **caught** | tool_flag | not caught |
| 16 | junk masks | low-confidence outlines | tool | **caught** | tool_flag | not caught |
| 17 | junk masks | two filaments merged into one outline | tool | **caught** | tool_flag | not caught |
| 18 | fabricated measurement | cites a run that does not exist | record | **caught** | admission | not caught |
| 19 | fabricated measurement | cites a failed run | tool | **caught** | admission | not caught |
| 20 | fabricated measurement | cites a nonexistent artifact | record | **caught** | admission | not caught |
| 21 | fabricated measurement | cites a nonexistent calibration | record | **caught** | admission | not caught |
| 22 | fabricated measurement | finding cites a made-up measurement id | tool | **caught** | admission | not caught |
| 23 | fabricated measurement | derived: value from a failed sandbox run | tool | **caught** | admission | not caught |
| 24 | fabricated measurement | numbers typed into the analysis code | tool | **caught** | tool_failure | not caught |
| 25 | fabricated measurement | attached to someone else's ok run | record | **caught** | admission | not caught |
| 26 | missing corroboration | short time claimed alone | record | **caught** | gate:unsupported | not caught |
| 27 | missing corroboration | corroborated by the recovery, not the collapse | tool | **caught** | gate:unsupported | not caught |
| 28 | missing corroboration | short time computed in the sandbox (derived:short_time) | tool | **caught** | gate:unsupported | not caught |
| 29 | retracted evidence | voltage run invalidated after the short was earned | tool | **caught** | stale | not caught |
| 30 | retracted evidence | scale run invalidated after a speed was earned | tool | **caught** | stale | not caught |
| 31 | retracted evidence | SAM run invalidated after a speed was earned | tool | **caught** | stale | not caught |
| 32 | critic overreach | critic supports a claim the gate refutes | tool | **caught** | gate:refuted | not caught |
| 33 | critic overreach | critic supports a claim the gate finds unsupported | tool | **caught** | gate:unsupported | not caught |
| 34 | critic overreach | critic review on a claim the gate never saw | record | **caught** | status:proposed | not caught |
| 35 | junk masks | merged outline, 3 px gap (box narrow enough to pass as one filament) | tool | **caught** | tool_flag | not caught |
| 36 | junk masks | single wide filament must not be flagged as merged; claim overstates its speed 2x | tool | **caught** | gate:refuted | not caught |
| 37 | junk masks | two outlines separated by only two pixels, joined at the electrode | tool | **caught** | tool_flag | not caught |
| 38 | junk masks | two tilted outlines with different fronts and a threefold speed ratio | tool | **caught** | tool_flag | not caught |

Totals: Translucent **38/38**, baseline **1/38**.

| Fault class | Cases | Translucent | Baseline |
|---|---|---|---|
| spatial calibration | 5 | 5 | 1 |
| time base | 4 | 4 | 0 |
| swapped video | 3 | 3 | 0 |
| junk masks | 9 | 9 | 0 |
| fabricated measurement | 8 | 8 | 0 |
| missing corroboration | 3 | 3 | 0 |
| retracted evidence | 3 | 3 | 0 |
| critic overreach | 3 | 3 | 0 |

## Clean cases (no fault)

| Case | Truth | Translucent | Baseline |
|---|---|---|---|
| Movie 4, scripted clean filament (1 px per recorded s) on the real clock + scale | 6.203 um/s | 6.200 ± 0.018 um/s (ci95); claim 'within 10% of truth' -> supported | 112.82 ± 0.31 um/s (per playback frame, 18.2x truth) |
| Movie 1, real recorded SAM track (frame 60 point), no recorded clock | unknown (no clock on record; paper gives 2-7 s per image) | 741.75 ± 91.07 um per playback-s over 50 playback frames (repeats not removed without a clock); claim -> unsupported (no recorded clock) | 741.75 ± 91.07 um/s reported as real speed (50 playback frames) |
| Movie 4 voltage log: first short | 90 ± 3 s (paper's voltage data) | 89.55 ± 3.45 s; claim 80-100 s -> supported | 93.0 s (first logged sample below 0.5 V, no uncertainty) |

The clean path works: on Movie 4's real clock and scale Translucent recovers the injected speed and earns it, while the per-playback-frame script is off by ~18x (each image is 6.9 s of real time but 0.4 s of playback). On Movie 1 both produce the same number, but Translucent refuses to earn it because no clock is on record.

## Not caught / weak spots

- Without a recorded clock, `measure_growth` does not remove repeated images (Movie 1: 50 playback frames = 5 captured images), so the playback speed it reports to the agent is per frame, not per image. The gate still refuses it.
- The critic can't override the gate (3/3), but in the runtime it is only consulted after the gate says supported. So a fault the gate misses (above) reaches the critic with nothing behind it.

## Requests

Requests 1-9 from the first run (2026-10-28) are now in core: calibrations must belong to the measured recording
(tool and gate), corroboration must come from the same recording, scale and clock count only from the experiment's
records tools, conflicting scales on record refuse the finding, the `derived:` prefix no longer skips corroboration,
runs list the measurements they produced, staleness follows artifact lineage, objects whose outlines are often the
wrong shape are not measured, and import ties each experiment's records to its exact video bytes.

Open:
- **Merged masks** (`box_merged_filaments`): no cheap rule yet; candidates are an across-axis jump against the
  object's own early frames, or overlap checks between prompted objects.
- **Data:** record lane-box `segment_images` outputs for Movie 4 when Meta resumes, so the velocity cases can use
  real clean masks instead of the scripted filament.
## Memory ablation (scaffold)

`uv run python -m evals.ablation` (fake model, offline) or `--real --profile dev --n 3` (paid; real result in evals/MEMORY.md). Fake-mode smoke result, which proves only that the plumbing works (the scripted agent skips steps when it sees a lesson): memory on 3 steps / lesson used 1; memory off 6 steps / lesson used 0.

