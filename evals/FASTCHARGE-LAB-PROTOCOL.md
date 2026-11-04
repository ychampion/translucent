# Fast-charge design with a learned thermal model: frozen protocol

This protocol was frozen before any arm was scored. The commit that adds it is the freeze. After the first scored
run, the cases, arms, budgets, assessment, metrics and criteria do not change. If a defect forces a change, every
affected arm is run again, and the change is recorded under "Amendments" with its commit and the reason.

Runs made before the freeze checked the harness (shakedown). They are not scored and are not reported.

## Question

An engineer's thermal model (h = 6 W/m²K, calibrated in another fixture) plans a fast charge that is safe in the
model. The cell is now tested in a fixture whose thermal behaviour the model may not know. Within four lab tests, does
a planner that chooses tests, fits and checks thermal models and designs under what the evidence still allows, deliver
a final protocol that meets the engineer's limits in the hidden fixture, and how fast is it, compared with (a) the
same planner without diagnostics, (b) the current loop on the fixed model, (c) one calibration, and (d) a fixed
adaptive script with the same actions and caps?

All results are computational. The lab is a separate simulation with a hidden condition per case. Nothing here is a
claim about a physical cell.

## System

- System `pybamm-fastcharge-lab` v1 (`translucent/systems/lgm50_lab.py`, solver `lgm50_lab_solver.py`): LG M50,
  O'Kane 2022 parameter set, DFN with partially reversible plating and a lumped thermal model; four constant-current
  steps over the SOC windows 10–27.5, 27.5–45, 45–62.5 and 62.5–80 %, each from 0.2C to 1.5C on a 0.05C lattice,
  then constant voltage at 4.2 V if it is reached.
- Thermal model family (developer-provided): v0 nominal (h 6 W/m²K, thermal-mass scale k 1); family 1, h fitted;
  family 2, h and k fitted. A fit returns the estimate, its 95 % region and at most five members; forecasts and the
  design search run every member and report the worst.
- Actions offered to the planner: `lab_charge`, `forecast_protocol`, `fit_thermal`, `check_model`,
  `rank_measurements`, `optimize_protocol`. `bench_charge` (a real bench run by a person) is not offered: there is no
  bench in this evaluation. Bound parameters (model definitions, sources, the lab's commitment, the objective's
  limits) are filled by Translucent at proposal and re-derived at admission.
- The simulated lab: a sealed condition per campaign (h, k, chamber offset, noise 0.15 K on surface temperature and
  1 mV on voltage, a random seed), written by the harness at campaign creation. The campaign holds only its sha256
  commitment. No result, artifact or planner tool shows it.

## Objective

Fastest 10–80 % charge (`fastest_feasible`, loop-compare/3) at an ambient setpoint of 25 °C, with the lowest anode
potential at or above +25 mV vs Li/Li+ and the cell at or below 45 °C. Registered by the engineer, never amended
during a run.

## Cases (hidden lab conditions)

| Case | h (W/m²K) | k | Chamber offset | Lab noise | What it tests |
|---|---|---|---|---|---|
| N | 6 | 1 | 0 K | 0.15 K | Nominal: the lab matches the engineer's model. A good agent should not change the design for nothing. |
| C | 15 | 1 | 0 K | 0.15 K | Changed cooling: the cell runs colder and the anode margin shrinks. A lab test shows only the colder surface. |
| U | 7 | 1 | 0 K | 1.0 K | Uncertainty that does not change the design: the nominal design breaks the anode margin under this truth, so the first test matters; after one fit the remaining uncertainty does not change the design, and the right move is to stop learning. |
| M | 6 | 1.6 | 0 K | 0.15 K | Thermal mass, outside the one-parameter family: family 1 (h only) cannot fit it and says so (`fit_adequate` = no); family 2 (h and k) can. The test is whether the arm notices the inadequate fit and moves to family 2 instead of trusting an inadequate model's design. |
| O | 6 | 1 | +3 K | 0.15 K | Omitted mechanism: the chamber runs 3 K above its setpoint, outside the model family; fits should come back with `fit_adequate` = no. |

The case table above is the only place the cases are defined (in code: `CASES` in `evals/fastcharge_lab.py`).

## Arms

Budgets are matched. Every lab campaign has at most 4 lab tests and 12 requests in all (lab tests included); the model
arms have $2.50 of model spend each. The assessment run is outside every budget. The fixed-model arm keeps the M2
loop's 8-simulation budget and has no lab; its budget is not matched to the lab arms and is reported as such.

1. **adaptive_meta.** Muse (planner kind `muse`, Meta profile) with every action listed above. Paid.
2. **no_diagnostics_meta.** Muse with `lab_charge`, `forecast_protocol` and `optimize_protocol` only
   (`objective.planner.actions`). No fits, checks or rankings, so only model version 0 exists. Paid.
3. **fixed_model_meta.** The current loop: Muse on `pybamm-fastcharge` v1 (`simulate_protocol` only, h fixed at 6),
   `fastest_feasible` with the same +25 mV and 45 °C limits, 8 simulations, no lab. Its final protocol does not
   depend on the case: one run per repeat is assessed under every case. Its final protocol is the comparisons'
   incumbent (loop-compare/3) when that incumbent meets the constraints; otherwise it has no design. Paid.
4. **calibrate_once.** Numerical, no model calls: optimize under v0; a lab test of that design with a 60 min rest; fit
   family 1 on it; optimize under the fitted version; stop.
5. **adaptive_numeric.** Numerical, no model calls, the same actions and caps as arm 1. The fixed policy, as coded
   in `evals/fastcharge_lab.py` (`adaptive_numeric`):
   1. Optimize under v0 (design D0). If no design is found, stop.
   2. Lab test of D0 with a 60 min rest (test 1).
   3. Fit family 2 on test 1.
   4. If that fit is not adequate (or failed), fit family 1 on test 1 as well. The candidates are the fitted
      versions; the current version is the candidate with the lower fit RMSE.
   5. `rank_measurements` for D0 under the current version, the composed test being 1C in every window with a 60 min
      rest.
   6. If the recommended option is not 0, its value is above 0 and a lab test and a request are left, run that test
      (test 2): option 1 is D0 with a 60 min rest, 2 is D0 with no rest, 3 is 1C with a 60 min rest, 4 is 0.5C with a
      60 min rest, 5 is the composed test.
   7. If test 2 ran: `check_model` of every candidate on test 2; the current version becomes the candidate with the
      lowest held-out RMSE; then refit that candidate's family on every lab test so far, which becomes the current
      version. If test 2 did not run, there is no check and no refit.
   8. Optimize under the latest adequate version, else the latest version.
   9. Lab test of the last design found, with no rest (temperature confirmation).
   10. If the confirmation's measured peak temperature is above 45 °C and two requests are left: refit the current
       family on every lab test, then optimize under the latest adequate version (else the latest).
   11. Stop. A step for which the budget has no room is skipped and recorded.

The numerical arms propose as an agent (`agent:<arm>`); the objective's auto-approval rule, written by the engineer at
registration, approves computational requests up to 12. After stopping, a numerical arm concludes the campaign as its
agent. The Muse arms run turns in the harness process until the campaign concludes, the budget is spent or the planner
stops; up to three planner questions are answered with a fixed text ("No person answers questions during this
evaluation...").

Repeats: 3 per arm and case (arm 3: 3 runs, each assessed under every case). Every run is listed.

Model spend: the paid arms start only with `--live` and `--cap-usd`. Before every provider call the harness adds the
spend recorded in every run ledger under the output folder to the call's worst case and stops the turn if the sum
would pass the cap. Paid runs need the owner's authorization.

## Final protocol and assessment

- Lab arms: the final protocol is `view.learning.best_design` (the last design found by `optimize_protocol`, with the
  model version it used). No design found: the run is scored as "no design", infeasible.
- Fixed-model arm: as above, the comparisons' incumbent when it meets the constraints.
- Assessment (the harness only, after the campaign stops; never visible to the planner): the final protocol runs once
  under the hidden condition, `lgm50_lab_solver.charge((rates, 25 + offset, 0, h, k, None))`, with no noise and no
  rest. Recorded: true t80, lowest anode potential, peak cell temperature, and feasibility: the run reaches 80 %, its
  lowest anode potential is at least +25 mV and its peak temperature at most 45 °C, exactly the objective's limits
  (no solver-bound offsets). Violations name the limit and the amount.
- Held-out prediction: the forecast of the final protocol under the model version the design used
  (`lgm50_lab_solver.forecast`, at 25 °C) against the assessment run: RMSE of the lumped cell temperature over the
  charge (the estimate member), peak-temperature error (worst member and estimate) and lowest-anode-potential error
  (worst member and estimate). The anode error is a simulation-only quantity: no lab measures it.
- The sealed condition is copied into the run folder (`revealed-condition.json`) only after scoring.

## Metrics

Per run: final protocol and its model version; true t80; feasible; violations; lab tests used; requests and
computational runs by action; failed runs; model spend (0 for the numerical arms); campaign wall time; human
interventions (questions, answers, notes, rejections); model versions made; case N: whether a non-nominal version was
adopted and the largest change of any window's C-rate from the campaign's own v0 design; cases M and O: whether a fit came
back inadequate and whether the final design's model version is adequate; case U: lab tests used after the first fit;
held-out prediction errors.

Per arm and case: feasible repeats; median true t80 over the feasible repeats; medians of lab tests, requests, wall
time and errors; sums of failed runs, interventions and spend.

## Acceptance criteria

Each is decided per case from the per-arm summary. A criterion is reported as not decided while one of its arms has
no runs or fewer than its 3 repeats.

1. **Changed cooling (case C).** Arm 1 is feasible in at least 2 of 3 repeats, and arm 3's final protocol is
   infeasible under case C in at least 2 of 3 repeats.
2. **What Muse adds (every case), arm 1 against arm 5.** "Adds" only when arm 1 is feasible in more repeats than arm
   5, or in as many (at least one) with a median true t80 at least 1.0 min faster. "Worse" when arm 1 is feasible in
   fewer repeats, or in as many with a median at least 1.0 min slower. Otherwise "equal". The same rule compares arm 1
   with arm 2, arm 5 with arm 4 and arm 5 with arm 3.
3. **No change for nothing (case N).** For every lab arm, the final design differs from that campaign's v0 design by
   at most one 0.05C step in every window in at least 2 of 3 repeats.
4. **Stops learning (case U).** For every arm that can fit (1, 4, 5): after the campaign's first fit, at most one
   further lab test (a temperature confirmation of the final design), and the final protocol is feasible, in at least
   2 of 3 repeats. The lab tests used after the first fit are reported for every run.
5. **Thermal mass handled (case M).** For every arm that can fit (1, 4, 5): the final design was made under a model
   version whose fit is adequate, and it is feasible, in at least 2 of 3 repeats. Whether a fit came back inadequate
   is reported for every run.
6. **Omitted mechanism flagged (case O).** For every arm that can fit (1, 4, 5), at least one fit returns
   `fit_adequate` = no in at least 2 of 3 repeats.

Any other outcome is reported as it is: equal or worse. No result is called optimal; the design search is a greedy
step-down with no optimality guarantee, and feasibility is decided on one hidden-condition run.

## What this does not show

Surface-temperature fits do not validate core temperature, anode potential, plating or high-rate behaviour. The
simulated lab is the same model family as the forecasts with a different h, k or chamber temperature: an identifiable
mismatch by construction, except case O. Robustness across members is not a physical safety guarantee. The numerical
arms' repeats differ only in the lab's noise seed; when a fit lands on the same estimate the repeats coincide, so
they add little information. No physical test is requested or authorized.

## Amendments

### Amendment 1 (commit: (this amendment's commit))

Defect H1, found by review after scoring had started: `fit_thermal` searched h on a coarse grid (3.3 % per step).
For a fit within the noise, the 95 % region collapsed to a single grid point, which held the true h in 0 of 50 test
fits. In the affected runs 15 of the 21 adequate fits had one member and the other 6 had two or three, so forecasts,
rankings and robust designs under those versions carried little or none of the fit's uncertainty. (Inadequate fits,
whose sigma is inflated by their residual, had three to five members.) Fixed: the fit uses a local fine grid, widened until it holds the region; in
tests the region now holds the true h in at least 44 of 50 fits.

Fixed in the same change: M2, the ranking (`rank_measurements`) and the held-out coverage (`check_model`) now use the
noise sigma the fit used; M1, a revision of a request can no longer change its action. Request titles no longer
print bound JSON (display only).

Affected runs: arms 4 (calibrate_once) and 5 (adaptive_numeric), every run made before the fix. They are kept apart
as the affected runs (`affected-H1`), reported separately from the corrected evaluation and never pooled with it. The
corrected evaluation reruns both arms on every case with 3 repeats on the fixed code. No paid arm had run.

Nothing else changed: no case, threshold, baseline, budget or criterion.

Correction (recorded with Amendment 2): the request-title change above was not display only. The assessment headline,
which names the best request by its title, is part of the planner's context. No paid arm had run before Amendment 1
and the numerical arms do not read the headline, so no scored run saw the old titles.

### Amendment 2 (commit: (this amendment's commit))

Defect D2, found in repeat 1 of the paid arms: `rank_measurements` had a 600 s wall-time limit. In case O, arm 1
(repeat 1) asked to value a 0.2C composed test (a 210 min charge) under a five-member version. On the machine that
runs the paid arms the call ran past 600 s and was recorded as a failed request (one of the run's 12). Re-executing
that request with its recorded inputs and no limit took 932 s on the same machine (the result: option 1, value 1.28 min). The same limit binds on a slow machine and not
on a fast one: the numerical arms' ranking calls took 97–147 s on the evaluation server, the paid arms' completed
calls 264–402 s on the laptop.

Fixed: the ranking's wall-time limit is 2400 s, about 2.5 times that (`translucent/systems/lgm50_lab.py`, the `rank_measurements`
action), a resource limit only. It is the same for every arm that can call the action (1 and 5; arm 4 never ranks;
arm 2 is not offered the action; arm 3 runs another system). Nothing else changed: not the ranking's objective, its
candidate tests, its draws or seed, the auto-approval rule (it reads the request's cost estimate, 240 s, which is
unchanged), the planner's tools or context, any case, threshold, budget, baseline or criterion.

Affected runs: `O-adaptive_meta-1` only. It is kept, with its cost, as the affected run (`affected-D2`), reported
separately and never pooled; it is replaced by one run of the same arm, case and repeat on the corrected code, and
the replacement is scored whatever its result.

Why the other repeat-1 runs stand: the limit only changes a request whose execution passes 600 s. In every other
repeat-1 run, paid and numerical, no execution of any action reached its limit (the longest ranking took 402 s), and
the solver is deterministic for given inputs, so every recorded result is what the corrected code returns. The limit is not shown to the planner: the action list it sees has no wall-time field, and the
failure text is in the affected run alone.

Also recorded, not changed (D3): the assessment headline names the request that loop-compare/3 picks as best, which
can be an earlier design than `learning.best_design` (case U, arm 1, repeat 1: the version 1 design, not version 2's).
The headline is in the planner's context, so it is left as it is for every scored run; the web view shows
`learning.best_design` and notes when the two differ (display only, no planner input).

Freeze: before repeats 2 and 3, the evaluation's code and configuration (`translucent/`, `evals/fastcharge_lab.py`,
this protocol) are recorded by sha256 in `FREEZE.sha256` next to the runs and checked unchanged after the last run.
