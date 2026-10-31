# Fast-charge loop evaluation: frozen protocol

This protocol is fixed before any arm is scored. The commit that adds it is the freeze. After the first scored run, the
cell, parameter set, bounds, budgets, metrics and thresholds do not change. If a defect forces a change, every arm is
run again, and the change is recorded below with its commit and the reason.

## Question

Given a cell's records, a failing fast-charge protocol, an objective and constraints, does a planner that sees each
result before choosing the next experiment find a better feasible protocol within eight simulated experiments than
(a) the same model committing all eight experiments at once, (b) constrained Bayesian optimisation over the same
action space, and (c) the passive pipeline, which analyses the records and runs no experiments?

All results are computational. They are claims about the frozen model, not about a physical cell.

## System and executor

- Cell: LG M50 (21700, NMC811 and graphite-SiOx), O'Kane 2022 parameter set, DFN model with partially reversible
  lithium plating and a lumped thermal model with a heat-transfer coefficient of 6 W/m²K.
- Solver: PyBaMM 26.10.1.1, casadi 3.8.1, pybammsolvers 0.11.1, IDAKLU with default settings, default mesh. The
  executor's code digest is recorded in every campaign objective.
- Action: `simulate_protocol`, four constant-current steps over the SOC windows 10–27.5, 27.5–45, 45–62.5 and
  62.5–80 %, each from 0.2C to 1.5C on a 0.05C lattice, followed by constant voltage at 4.2 V if it is reached.
- Records supplied to every arm: the Chen et al. (2020) LG M50 characterisation records (three cells, imported with
  their checksums) and the simulated failing baseline.
- Failing baseline: 1.5C in every window at 25 °C: 28.2 min to 80 %, peak 51.2 °C, lowest anode potential +7.3 mV.
  It violates both constraints.

## Cases

| Case | Ambient | Temperature limit | Plating margin | Notes |
|---|---|---|---|---|
| A | 25 °C | 45 °C | +10 mV | |
| B | 25 °C | 45 °C | +10 mV, then +25 mV | The engineer raises the margin after the fourth completed experiment. |
| C | 15 °C | 45 °C | +10 mV | Held out. Outside the parameter set's fitted range; reported as extrapolation. |

The case B amendment is made by the evaluation harness acting as the registering engineer, with this fixed text:
"Raise the plating margin to +25 mV. The model's voltage gap to the records is 70–160 mV, so +10 mV is not a safe
margin." It is recorded as an amendment, as a person's amendment would be.

## Feasibility rule

A result is feasible when its status is ok, its lowest anode potential is at least the margin plus 0.5 mV, and its
peak temperature is at most the limit minus 0.1 K. The two offsets are the solver bounds declared by the system. A
result within a bound of a limit is marginal and counts as infeasible.

## Arms

1. **Loop.** Muse Wonder (Meta profile) with the campaign tools. It sees each result before its next turn and may
   define measurements, calibrate, propose, cancel and conclude.
2. **Same model without feedback.** The same model, records and tools. It commits all eight experiments in its first
   turn and is not called again. In case B the amendment still arrives after the fourth result, but the batch is fixed.
3. **Constrained Bayesian optimisation.** Gaussian-process surrogates for charge time, lowest anode potential and peak
   temperature over the same lattice; constrained expected improvement (Gardner et al., 2014); three Sobol points,
   then five steps. In case B it reads the amended margin from the ledger after the change.
4. **Passive pipeline.** Imports and analyses the records and runs no experiments. Its answer is the best protocol
   the records support, 0.3C constant current then constant voltage (140 min measured), together with its report of
   the failing baseline's violations.
5. **Reference row, not a competitor.** The manufacturer's maximum, 0.7C constant current then constant voltage,
   simulated: 60.0 min, 35.2 °C, +22.9 mV at 25 °C.

Every arm receives the same records, objective, constraints and action space. Each search arm has eight computational
experiments per run, calibration runs included. Each model arm has a model budget of $1.50 per run; an arm that stops
on budget keeps its best feasible result so far.

Approvals: each evaluation campaign carries an auto-approval rule, written by the registering engineer before the
campaign starts. The rule covers `simulate_protocol` and `calibrate_thermal` inside the declared bounds, up to the
experiment budget. No physical test is approved during the evaluation. A physical request stays pending and counts
toward intervention burden. A claim that needs a physical result ends unresolved.

Repeats: the loop and the no-feedback arm are run 3 times per case. Bayesian optimisation is run with seeds 0 to 4.
Medians are reported, and every run is listed.

Total model spend for the evaluation is limited to $25. The harness stops before a call that would pass this limit.

## Reference optima

These were found by a deterministic compass search on the 0.05C lattice, before this freeze.

| Case | Protocol (C, by SOC window) | Time to 80 % | Best feasible single step |
|---|---|---|---|
| A | 1.5, 1.5, 1.05, 1.1 | 33.55 min | 1.05C, 40.0 min |
| B | 1.5, 1.5, 1.05, 0.8 | 37.13 min | 0.60C, 70.0 min |
| C | 1.5, 1.5, 1.35, 0.85 | 34.13 min | 0.55C, 76.4 min |

## Metrics

Each metric is computed per run and summarised by its median across repeats.

1. Best feasible time to 80 % at the end of the budget, and regret against the reference optimum (primary).
2. Experiments run before the first feasible protocol that beats the case's best feasible single step.
3. Infeasible experiments, duplicate experiments, and requests refused for bounds or rejected.
4. Model spend, wall time and solver failures.
5. Case B only: the counterfactual replay result (below), and the number of infeasible runs after the change.
6. Intervention burden: approvals, amendments and answers a person had to give, including pending physical requests.

## Counterfactual replay (case B)

For each loop run in case B, the first four experiments and their results are kept as a recorded prefix. Starting
from the same prefix, the planner takes one turn with the amendment and one turn without it. The replay passes when
the two fifth proposals differ and the proposal made after the amendment predicts a lowest anode potential of at
least +25 mV.

## Success criteria for the loop

- Case A: the median best feasible time is within 1.0 min of the reference, and no worse than the Bayesian
  optimisation median or the no-feedback median. Ties are broken by fewer infeasible experiments.
- Case B: the counterfactual replay passes in at least 2 of 3 runs, and the median best feasible time under the new
  margin is within 1.5 min of reference B.
- Case C is reported separately as extrapolation and is not part of the criteria.
- Any other outcome is reported as a loss or a tie.

The only improvement that may be claimed is a computational one under the frozen model: the candidate against the
failing baseline (feasible against infeasible, and the time difference), and against the 0.7C reference row.

## When a run ends unresolved

A run ends unresolved, not with a candidate, when no protocol meets the feasibility rule within the budget, when the
best candidate is marginal, when the candidate needed a retry after a solver failure, when the claim would need an
action outside the bounds, or when the claim concerns a physical cell and no physical result was returned.

## Amendments

None.
