# Learning the LG M50 thermal model from the Chen 2020 discharge records: a held-out study

`python -m evals.thermal_records --out evals/results/thermal-records.json` (numerical; PyBaMM 26.10.1.1, DFN with
lumped thermal, OKane2022, linux-x86_64). Every number below is quoted from `evals/results/thermal-records.json`; the
tables are printed by `python -m evals.thermal_records --tables evals/results/thermal-records.json`. The splits, the
fit target, the interval rule and the identifiability rule were fixed in the module docstring before any fit ran.

## What was done

- **Data.** Three cells (02, 03, 04), each discharged from full at 0.5, 1 and 1.5C: nine trajectories. The measured
  quantity is the surface thermocouple, as its rise above the first point of the discharge (6.8-8.8 K at 0.5C,
  15.5-19.2 K at 1C, 25.9-31.7 K at 1.5C).
- **Model.** DFN, lumped thermal, each trajectory at its median recorded current and median chamber temperature,
  from full to 2.5 V. Two thermal parameters: the heat transfer coefficient h and a thermal-mass scale k, which
  multiplies all five specific heat capacities. Prior: the parameter set as shipped, h = 10 W/m^2/K, k = 1.
  F1: h free, k = 1. F2: h and k free.
- **Fit target.** The whole surface-temperature trajectory, not only its peak: model rise minus measured rise at
  200 points evenly spaced in discharged capacity, weighted so each trajectory counts the same. Capacity rather
  than time, so that model and cell are compared at the same state of charge.
- **Grid.** h from 1 to 50 (19 values) by k from 0.5 to 2.5 (11 values), every trajectory solved at every point:
  1,881 DFN solves, none failed. Each estimate was then re-solved; every number at an estimate comes from that
  re-solve.
- **Uncertainty.** 95 % profile interval over the grid. The residuals are smooth, so the serial correlation is
  corrected for: the effective number of independent points is 7.6-16.1 per fit, against 1,200-1,800 raw points.
- **Splits.** Whole trajectories are held out, never part of one. A: leave one cell out (fit two cells at all
  three rates, predict the third), three rotations. B: fit 0.5 and 1C on all cells, predict 1.5C.

## Held-out results

| split | model | h | h 95 % | k | k 95 % | train RMSE (K) | held-out RMSE (K) | held-out mean abs max-rise error (K) | held-out voltage RMSE (mV) |
|---|---|---|---|---|---|---|---|---|---|
| A-cell02 | prior | 10 | fixed | 1 | fixed | 2.77 | 2.23 | 4.68 | 119.9 |
| A-cell02 | F1 | 7.34 | [6.09, 8.86] | 1 | fixed | 1.93 | 1.42 | 1.89 | 128.0 |
| A-cell02 | F2 | 6.15 | [4.08, 7.95] | 1.339 | [0.97, 1.73] | 1.69 | 1.08 | 1.17 | 126.9 |
| A-cell03 | prior | 10 | fixed | 1 | fixed | 2.97 | **1.63** | 3.36 | 113.5 |
| A-cell03 | F1 | 6.79 | [5.81, 8.04] | 1 | fixed | 1.61 | 2.29 | 0.70 | 123.6 |
| A-cell03 | F2 | 5.80 | [4.51, 6.96] | 1.301 | [1.01, 1.57] | 1.39 | 1.90 | 0.90 | 122.4 |
| A-cell04 | prior | 10 | fixed | 1 | fixed | 1.95 | 3.56 | 7.14 | 111.9 |
| A-cell04 | F1 | 8.25 | [6.99, 9.69] | 1 | fixed | 1.52 | 2.49 | 5.43 | 117.1 |
| A-cell04 | F2 | 6.86 | [5.25, 8.26] | 1.391 | [1.12, 1.68] | 1.16 | 2.41 | 4.66 | 116.1 |
| B-rate1.5 | prior | 10 | fixed | 1 | fixed | 1.73 | 3.79 | 7.13 | 147.3 |
| B-rate1.5 | F1 | 8.18 | [6.96, 9.59] | 1 | fixed | 1.40 | **2.58** | 4.45 | 155.6 |
| B-rate1.5 | F2 | 6.39 | [4.92, 7.67] | 1.685 | [1.33, 2.02] | 1.05 | 3.31 | 4.65 | 145.0 |
| in-sample (not held out) | F1 | 7.43 | [6.52, 8.55] | 1 | fixed | 1.77 | - | - | - |
| in-sample (not held out) | F2 | 6.37 | [4.65, 7.53] | 1.331 | [1.10, 1.60] | 1.52 | - | - | - |

Max-rise error is modelled minus measured (negative: the model runs cooler than the cell).

| split | held-out trajectory | measured max rise (K) | prior error (K) | F1 error (K) | F2 error (K) | prior RMSE (K) | F1 RMSE (K) | F2 RMSE (K) |
|---|---|---|---|---|---|---|---|---|
| A-cell02 | cell02@0.5C | 7.56 | -3.14 | -1.89 | -1.26 | 0.89 | 1.09 | 1.24 |
| A-cell02 | cell02@1C | 16.74 | -4.36 | -1.40 | -0.42 | 1.83 | 1.53 | 1.09 |
| A-cell02 | cell02@1.5C | 27.91 | -6.55 | -2.38 | -1.83 | 3.28 | 1.58 | 0.88 |
| A-cell03 | cell03@0.5C | 6.81 | -2.40 | -0.79 | -0.20 | 0.82 | 1.60 | 1.81 |
| A-cell03 | cell03@1C | 15.52 | -3.14 | +0.61 | +1.44 | 1.44 | 2.61 | 2.37 |
| A-cell03 | cell03@1.5C | 25.91 | -4.55 | +0.69 | +1.05 | 2.29 | 2.53 | 1.38 |
| A-cell04 | cell04@0.5C | 8.76 | -4.34 | -3.59 | -2.98 | 1.29 | 1.00 | 0.80 |
| A-cell04 | cell04@1C | 19.17 | -6.78 | -4.98 | -3.95 | 3.05 | 2.04 | 1.45 |
| A-cell04 | cell04@1.5C | 31.70 | -10.30 | -7.73 | -7.05 | 5.21 | 3.67 | 3.82 |
| B-rate1.5 | cell02@1.5C | 27.91 | -6.55 | -3.87 | -4.07 | 3.28 | 1.96 | 2.77 |
| B-rate1.5 | cell03@1.5C | 25.91 | -4.55 | -1.86 | -2.07 | 2.29 | 1.79 | 1.33 |
| B-rate1.5 | cell04@1.5C | 31.70 | -10.30 | -7.61 | -7.81 | 5.21 | 3.60 | 4.84 |

## What the numbers say

1. **The prior runs cool.** With h = 10 the model under-predicts every one of the nine peak rises, by 2.4-10.3 K,
   most at 1.5C. Every fit moves h down, to 5.8-8.3 W/m^2/K, and h = 10 lies outside every 95 % interval.
2. **Fitting helps on two of the three held-out cells, not on the third.** Held-out trajectory RMSE, prior -> F1 ->
   F2: cell 02 2.23 -> 1.42 -> 1.08 K; cell 04 3.56 -> 2.49 -> 2.41 K; cell 03 **1.63 -> 2.29 -> 1.90 K, worse
   than the prior**, although its peak error falls (3.36 -> 0.70 -> 0.90 K): the fitted model gets cell 03's peak
   right and the shape of its rise wrong, too hot at mid-discharge (1C RMSE 1.44 -> 2.61 K). Averaged over the three
   rotations: 2.47 -> 2.07 -> 1.80 K (RMSE) and 5.06 -> 2.67 -> 2.24 K (mean absolute peak error).
3. **Cell-to-cell spread is as large as what fitting gains.** Cell 04 heats 5.8 K more than cell 03 at 1.5C (3.6 K at 1C)
   under the same protocol. One shared (h, k) cannot cover that: fitted on cells 02 and 03, the model still
   under-predicts cell 04's 1.5C peak by 7.7 K (F1) and 7.1 K (F2).
4. **Rate extrapolation: F1 helps, F2 does not add to it.** Fitted on 0.5 and 1C, the prior's held-out 1.5C RMSE of
   3.79 K becomes 2.58 K with F1 and 3.31 K with F2; mean absolute peak error 7.13 -> 4.45 (F1) and 4.65 K (F2). The
   extra parameter lowers the training RMSE (1.40 -> 1.05 K) and raises the held-out one: it fits the low-rate
   shape, and that does not carry to 1.5C. F1 and F2 still under-predict every 1.5C peak, by 1.9-7.8 K.
5. **The shipped h = 6 (fitted on the mean peak rise only).** On all nine trajectories at k = 1 (grid row, in
   sample) h = 6 gives an RMSE of 2.25 K and a mean absolute peak error of 1.52 K; the trajectory fit F1 gives
   h = 7.43 [6.52, 8.55] and RMSE 1.77 K, with h = 6 outside its interval. Matching the peak and matching the
   trajectory pull h to different values, a sign that the lumped model's heat-generation shape over the discharge
   is off, not only its cooling.

## Is k identifiable from discharge data?

By the frozen rule (both ends of the 95 % interval inside the 0.5-2.5 grid), yes in every split: k's intervals
close at 0.97-1.73 (A-cell02), 1.01-1.57 (A-cell03), 1.12-1.68 (A-cell04), 1.33-2.02 (B-rate1.5). Read with the
rest of the evidence, k is **not identifiable as a physical thermal mass** from these data: its estimate moves with
the split (1.30-1.39 leaving out a cell, 1.69 leaving out a rate), h and k trade off (F2's h is always 1.0-1.8 W/m^2/K
below F1's), and adding k makes the withheld-rate prediction worse. The data constrain a combination of h and k
that reshapes the lumped transient to fit the rates it saw; they do not pin k separately from errors in the
modelled heat generation.

## Context and limits

- **Voltage.** Terminal-voltage RMSE on the held-out trajectories is 112-156 mV for every model, slightly higher
  after fitting (for example 113.5 -> 123.6 mV on cell 03). The thermal fit does not repair the electrochemical
  mismatch, and the electrochemistry is part of what sets the heat generation the thermal parameters are fitted
  against.
- **Surface only.** The fit and every metric use the surface thermocouple and the lumped (uniform) cell
  temperature. Agreement on the surface says nothing about the core temperature, the anode potential or lithium
  plating; none of them is measured in these records.
- **Small study.** Three cells, three rates, one ambient (26.9-28.0 degC chamber median), discharge only. The
  intervals rest on an effective sample of 7.6-16.1 independent points per fit. Charging, other temperatures and
  other fixtures are outside what this shows.
- **Ambient.** The model holds the chamber's median temperature; the chamber itself drifts by up to 1.5 K during a
  1.5C discharge, which the model does not see.
