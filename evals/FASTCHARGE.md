# Fast-charge loop evaluation

All results are computational: claims about the frozen model, not about a physical cell. The protocol was fixed before any arm was scored. Every number below is computed from the run ledgers.

## Method

Each run is a separate campaign on the LG M50 records with the failing 1.5C baseline re-run on the solver. The engineer registers the objective and an auto-approval rule for up to eight computational experiments before the campaign starts. Search arms have eight computational experiments, calibration included; model arms have a model budget of $1.50 per run. A result is feasible when its status is ok, its lowest anode potential is at least the margin plus 0.5 mV and its peak temperature is at most the limit minus 0.1 K; a marginal result counts as infeasible. Best feasible times use the margin in force at the end of the run; infeasible counts use the margin in force when each result arrived. Experiments at an ambient other than the case's are outside the case's action space: they are counted and never taken as candidates. Medians treat a run without a feasible result as infinitely slow.

Total model spend recorded: $12.67 of the $25 limit. A further $6.48 was spent on 14 runs set aside before scoring and repeated in full; it counts toward the limit and is not reported.

## Case A: 25 °C, +10 mV

Reference optimum: 1.5, 1.5, 1.05, 1.1 C, 33.55 min. Best feasible single step: 1.05C, 40.0 min.

Failing baseline (1.5C in every window, simulated): 28.20 min, 51.2 °C, +7.3 mV. It violates the plating margin and the temperature limit.

| Arm | Run | Best feasible t80 (min) | Regret (min) | Protocol | Before beating single step | Infeasible | Duplicates | Refused (bounds) | Model $ | Wall (min) | Solver failures | Burden | Ends |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Constrained BO | seed 0 | 42.62 | +9.07 | 1.4, 1.3, 1.1, 0.6 C | never | 3 | 0 | 0 | 0.00 | 1.1 | 0 | 0 | candidate |
| Constrained BO | seed 1 | 46.28 | +12.73 | 0.95, 0.8, 0.95, 0.95 C | never | 2 | 0 | 0 | 0.00 | 1.2 | 0 | 0 | candidate |
| Constrained BO | seed 2 | 43.81 | +10.26 | 0.8, 0.95, 1.15, 1 C | never | 2 | 0 | 0 | 0.00 | 1.0 | 0 | 0 | candidate |
| Constrained BO | seed 3 | 36.98 | +3.43 | 1.25, 1.3, 1, 1.05 C | 5 | 2 | 0 | 0 | 0.00 | 1.1 | 0 | 0 | candidate |
| Constrained BO | seed 4 | 36.40 | +2.85 | 1.25, 1.5, 1, 1 C | 6 | 4 | 0 | 0 | 0.00 | 1.1 | 0 | 0 | candidate |
| Loop | repeat 1 | 33.55 | -0.00 | 1.5, 1.5, 1.05, 1.1 C | 1 | 4 | 0 | 1 | 1.13 | 4.8 | 0 | 0 | candidate |
| Loop | repeat 2 | 34.05 | +0.50 | 1.5, 1.5, 1, 1.1 C | 2 | 3 | 0 | 0 | 1.00 | 4.5 | 0 | 0 | candidate |
| Loop | repeat 3 | 33.79 | +0.24 | 1.5, 1.45, 1.1, 1.05 C | 0 | 5 | 0 | 2 | 1.16 | 4.8 | 0 | 0 | candidate |
| Same model, no feedback | repeat 1 | 36.29 | +2.74 | 1.5, 1.3, 1.1, 0.9 C | 4 | 4 | 0 | 0 | 0.25 | 1.6 | 0 | 0 | candidate |
| Same model, no feedback | repeat 2 | 36.79 | +3.24 | 1.4, 1.3, 1.1, 0.9 C | 0 | 3 | 0 | 0 | 0.21 | 1.5 | 0 | 0 | candidate |
| Same model, no feedback | repeat 3 | 34.96 | +1.41 | 1.5, 1.35, 1.15, 0.95 C | 1 | 2 | 0 | 0 | 0.24 | 1.6 | 0 | 0 | candidate |

Medians:

| Arm | Runs | Best feasible t80 (min) | Regret (min) | Before beating single step | Infeasible | Model $ | Wall (min) | Burden |
|---|---|---|---|---|---|---|---|---|
| Loop | 3 | 33.79 | +0.24 | 1 | 4 | 1.13 | 4.8 | 0 |
| Same model, no feedback | 3 | 36.29 | +2.74 | 1 | 3 | 0.24 | 1.6 | 0 |
| Constrained BO | 5 | 42.62 | +9.07 | never | 2 | 0.00 | 1.1 | 0 |

Reference row, not a competitor: 0.7C constant current then constant voltage at 25 °C, simulated: 60.00 min, 35.2 °C, +23.0 mV; feasible under this case's rule.
Passive pipeline: it runs no experiment. The best protocol the records support is 0.3C constant current then constant voltage, 140.0 min from 10 to 80 % measured (median of 12 charges on 3 cells). The records hold no anode potential, so the plating margin cannot be checked on them; its report of the failing baseline is the simulated baseline above.

Success criteria:

- Median best feasible time 33.79 min against the reference 33.55 min (allowed 1.0 min): met.
- The loop is better than constrained BO (median best feasible time, then median infeasible experiments).
- The loop is better than the same model without feedback (median best feasible time, then median infeasible experiments).
- Outcome: success.

The improvement that may be claimed is computational, under the frozen model: the loop's median best feasible protocol takes 33.79 min and meets every constraint; the failing baseline takes 28.20 min and breaks a constraint, so feasibility costs +5.58 min. It is 26.21 min faster than the 0.7C reference row.

## Case B: 25 °C, +10 mV raised to +25 mV after the fourth experiment

Reference optimum: 1.5, 1.5, 1.05, 0.8 C, 37.13 min. Best feasible single step: 0.60C, 70.0 min.

Failing baseline (1.5C in every window, simulated): 28.20 min, 51.2 °C, +7.3 mV. It violates the plating margin and the temperature limit.

| Arm | Run | Best feasible t80 (min) | Regret (min) | Protocol | Before beating single step | Infeasible | Duplicates | Refused (bounds) | Model $ | Wall (min) | Solver failures | Burden | Ends |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Constrained BO | seed 0 | 42.62 | +5.49 | 1.4, 1.3, 1.1, 0.6 C | 1 | 3 | 0 | 0 | 0.00 | 1.2 | 0 | 1 | candidate |
| Constrained BO | seed 1 | 52.62 | +15.49 | 1.5, 0.8, 0.7, 0.6 C | 6 | 3 | 0 | 0 | 0.00 | 1.1 | 0 | 1 | candidate |
| Constrained BO | seed 2 | 57.67 | +20.54 | 0.85, 0.9, 0.6, 0.65 C | 4 | 2 | 0 | 0 | 0.00 | 1.0 | 0 | 1 | candidate |
| Constrained BO | seed 3 | 41.43 | +4.30 | 1.2, 1.15, 1.1, 0.75 C | 1 | 3 | 0 | 0 | 0.00 | 1.0 | 0 | 1 | candidate |
| Constrained BO | seed 4 | 39.38 | +2.25 | 1.4, 1.15, 1.2, 0.75 C | 3 | 4 | 0 | 0 | 0.00 | 1.0 | 0 | 1 | candidate |
| Loop | repeat 1 | 37.27 | +0.14 | 1.5, 1.25, 1.2, 0.8 C | 5 | 2 | 0 | 1 | 1.18 | 4.6 | 0 | 1 | candidate |
| Loop | repeat 2 | 37.17 | +0.04 | 1.5, 1.4, 1.1, 0.8 C | 4 | 4 | 0 | 0 | 1.02 | 4.2 | 0 | 1 | candidate |
| Loop | repeat 3 | 37.03 | -0.10 | 1.5, 1.35, 1.15, 0.8 C | 0 | 5 | 0 | 2 | 1.25 | 5.2 | 0 | 1 | candidate |
| Same model, no feedback | repeat 1 | 44.29 | +7.16 | 1.3, 1.1, 0.9, 0.7 C | 7 | 6 | 0 | 0 | 0.25 | 1.5 | 0 | 1 | candidate |
| Same model, no feedback | repeat 2 | 39.50 | +2.37 | 1.5, 1.5, 1, 0.7 C | 2 | 3 | 0 | 0 | 0.26 | 1.6 | 0 | 1 | candidate |
| Same model, no feedback | repeat 3 | 44.29 | +7.16 | 1.3, 1.1, 0.9, 0.7 C | 6 | 4 | 0 | 0 | 0.21 | 1.4 | 0 | 1 | candidate |

Medians:

| Arm | Runs | Best feasible t80 (min) | Regret (min) | Before beating single step | Infeasible | Model $ | Wall (min) | Burden | Infeasible after change |
|---|---|---|---|---|---|---|---|---|---|
| Loop | 3 | 37.17 | +0.04 | 4 | 4 | 1.18 | 4.6 | 1 | 2 |
| Same model, no feedback | 3 | 44.29 | +7.16 | 6 | 4 | 0.25 | 1.5 | 1 | 3 |
| Constrained BO | 5 | 42.62 | +5.49 | 3 | 3 | 0.00 | 1.0 | 1 | 1 |

Reference row, not a competitor: 0.7C constant current then constant voltage at 25 °C, simulated: 60.00 min, 35.2 °C, +23.0 mV; not feasible under this case's rule.
Passive pipeline: it runs no experiment. The best protocol the records support is 0.3C constant current then constant voltage, 140.0 min from 10 to 80 % measured (median of 12 charges on 3 cells). The records hold no anode potential, so the plating margin cannot be checked on them; its report of the failing baseline is the simulated baseline above.

Counterfactual replay (loop runs): one turn from the same four-experiment prefix with the amendment and one without it.

| Repeat | Proposal with amendment | Predicted lowest anode potential (mV) | Proposal without | Passes |
|---|---|---|---|---|
| 1 | 1.5, 1.35, 1.25, 0.75 C | +27.8 | 1.5, 1.3, 1.15, 1.1 C | yes |
| 2 | 1.5, 1.3, 1, 0.7 C | +28.0 | 1.5, 1.3, 1.1, 1.1 C | yes |
| 3 | 1.5, 1.4, 1.15, 0.8 C | +25.0 | 1.5, 1.4, 1.05, 1.1 C | yes |

Success criteria:

- Median best feasible time 37.17 min against the reference 37.13 min (allowed 1.5 min): met.
- The counterfactual replay passes in 3 of the loop runs (needed: 2).
- Outcome: success.

The improvement that may be claimed is computational, under the frozen model: the loop's median best feasible protocol takes 37.17 min and meets every constraint; the failing baseline takes 28.20 min and breaks a constraint, so feasibility costs +8.97 min. It is 22.83 min faster than the 0.7C reference row.

## Case C: 15 °C, +10 mV (extrapolation, held out)

Reference optimum: 1.5, 1.5, 1.35, 0.85 C, 34.13 min. Best feasible single step: 0.55C, 76.4 min.

Failing baseline (1.5C in every window, simulated): 28.72 min, 44.6 °C, -2.0 mV. It violates the plating margin.

| Arm | Run | Best feasible t80 (min) | Regret (min) | Protocol | Before beating single step | Infeasible | Duplicates | Refused (bounds) | Model $ | Wall (min) | Solver failures | Burden | Ends |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Constrained BO | seed 0 | 45.98 | +11.86 | 1.35, 1.3, 1.15, 0.5 C | 1 | 2 | 0 | 0 | 0.00 | 1.0 | 0 | 0 | candidate |
| Constrained BO | seed 1 | 56.81 | +22.68 | 0.7, 0.95, 0.9, 0.55 C | 3 | 2 | 0 | 0 | 0.00 | 1.0 | 0 | 0 | candidate |
| Constrained BO | seed 2 | 57.67 | +23.54 | 0.85, 0.9, 0.65, 0.6 C | 3 | 2 | 0 | 0 | 0.00 | 1.0 | 0 | 0 | candidate |
| Constrained BO | seed 3 | 43.67 | +9.54 | 1.3, 0.95, 1.1, 0.7 C | 1 | 5 | 0 | 0 | 0.00 | 1.0 | 0 | 0 | candidate |
| Constrained BO | seed 4 | 38.15 | +4.02 | 1.35, 1.45, 1.15, 0.75 C | 1 | 3 | 0 | 0 | 0.00 | 1.0 | 0 | 0 | candidate |
| Loop | repeat 1 | 34.13 | +0.00 | 1.5, 1.5, 1.35, 0.85 C | 0 | 2 | 0 | 3 | 1.22 | 4.1 | 0 | 0 | candidate |
| Loop | repeat 2 | 34.13 | +0.00 | 1.5, 1.5, 1.35, 0.85 C | 0 | 4 | 0 | 2 | 1.05 | 3.6 | 0 | 0 | candidate |
| Loop | repeat 3 | 34.13 | +0.00 | 1.5, 1.5, 1.35, 0.85 C | 4 | 5 | 0 | 1 | 1.24 | 4.5 | 0 | 0 | candidate |
| Same model, no feedback | repeat 1 | 39.50 | +5.37 | 1.5, 1.5, 1, 0.7 C | 3 | 6 | 0 | 0 | 0.23 | 1.4 | 0 | 0 | candidate |
| Same model, no feedback | repeat 2 | 39.50 | +5.37 | 1.5, 1.5, 1, 0.7 C | 1 | 5 | 0 | 0 | 0.23 | 1.4 | 0 | 0 | candidate |
| Same model, no feedback | repeat 3 | none | none | none | never | 8 | 0 | 0 | 0.24 | 1.5 | 0 | 0 | unresolved: no protocol meets the feasibility rule within the budget |

Medians:

| Arm | Runs | Best feasible t80 (min) | Regret (min) | Before beating single step | Infeasible | Model $ | Wall (min) | Burden |
|---|---|---|---|---|---|---|---|---|
| Loop | 3 | 34.13 | +0.00 | 0 | 4 | 1.22 | 4.1 | 0 |
| Same model, no feedback | 3 | 39.50 | +5.37 | 3 | 6 | 0.23 | 1.4 | 0 |
| Constrained BO | 5 | 45.98 | +11.86 | 1 | 2 | 0.00 | 1.0 | 0 |

Reference row, not a competitor: 0.7C constant current then constant voltage at 15 °C, simulated: 60.02 min, 27.4 °C, +5.9 mV; not feasible under this case's rule.
Passive pipeline: it runs no experiment. The best protocol the records support is 0.3C constant current then constant voltage, 140.0 min from 10 to 80 % measured (median of 12 charges on 3 cells). The records hold no anode potential, so the plating margin cannot be checked on them; its report of the failing baseline is the simulated baseline above.

Success criteria:

Case C is reported separately as extrapolation, with no success criterion.

The improvement that may be claimed is computational, under the frozen model: the loop's median best feasible protocol takes 34.13 min and meets every constraint; the failing baseline takes 28.72 min and breaks a constraint, so feasibility costs +5.41 min. It is 25.89 min faster than the 0.7C reference row.
