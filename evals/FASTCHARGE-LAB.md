# Fast-charge design with a learned thermal model: evaluation

All results are computational. The lab is a simulation with a hidden condition per case; no physical cell was tested. Every number below is computed by `python -m evals.fastcharge_lab report` from the run folders. No result is called optimal: the design search is a greedy step-down, not an optimiser with a proof.

## Method

Objective: fastest 10-80 % charge at 25 °C with the anode potential at or above +25 mV and the cell at or below 45 °C. Lab campaigns have 4 lab tests and 12 requests in all; the fixed-model arm has 8 simulations. Model arms have $2.50 each. After a campaign stops its final protocol is run once under the hidden condition (the assessment, outside the budget). Feasible means the hidden run reaches 80 %, its lowest anode potential is at least +25 mV and its peak cell temperature at most 45 °C. The anode potential is a simulation-only quantity: no lab measures it.

Model spend recorded: $41.17.

| Case | Hidden condition | What it tests |
|---|---|---|
| N | h 6 W/m²K, k 1, chamber +0 K, noise 0.15 K | The lab matches the engineer's model (h 6, k 1). |
| C | h 15 W/m²K, k 1, chamber +0 K, noise 0.15 K | Better cooling (h 15): the cell runs colder and the anode margin shrinks, which no lab test shows. |
| U | h 7 W/m²K, k 1, chamber +0 K, noise 1 K | Slightly better cooling (h 7) seen through a noisy thermocouple (1 K): the nominal design breaks the anode margin, so the first test matters; after one fit the remaining uncertainty does not change the design, and the right move is to stop learning. |
| M | h 6 W/m²K, k 1.6, chamber +0 K, noise 0.15 K | Extra thermal mass (k 1.6): family 1 (h only) cannot fit it and says so; family 2 (h and k) can. The test is whether the arm notices the inadequate fit instead of trusting its design. |
| O | h 6 W/m²K, k 1, chamber +3 K, noise 0.15 K | The chamber runs 3 K above its setpoint: outside the model family; fits should say so. |

## Case N: nominal

| Arm | Repeat | Final protocol | True t80 (min) | Lowest anode (mV, sim.) | Peak T (°C) | Feasible | Violations | Lab tests | Requests | Failed | Model versions | Model $ | Wall (min) | T RMSE (K) | Peak T error (K) | Anode error (mV, sim.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1. Adaptive (Muse) | 1 | 1.5, 1.5, 1.05, 0.8 C (v1) | 37.12 | +25.8 | 44.8 | yes | none | 3 | 8 | 0 | 1 | 0.78 | 12.2 | 0.03 | -0.01 | -0.1 |
| 1. Adaptive (Muse) | 2 | 1.5, 1.5, 1.05, 0.8 C (v3) | 37.12 | +25.8 | 44.8 | yes | none | 3 | 12 | 0 | 3 | 1.76 | 17.5 | 0.02 | +0.00 | -0.1 |
| 1. Adaptive (Muse) | 3 | 1.5, 1.5, 1.05, 0.8 C (v2) | 37.12 | +25.8 | 44.8 | yes | none | 2 | 12 | 0 | 2 | 1.96 | 12.3 | 0.02 | -0.01 | -0.1 |
| 2. No diagnostics (Muse) | 1 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +25.8 | 44.8 | yes | none | 4 | 11 | 0 | 0 | 1.97 | 7.1 | 0.00 | +0.00 | +0.0 |
| 2. No diagnostics (Muse) | 2 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +25.8 | 44.8 | yes | none | 4 | 12 | 0 | 0 | 1.59 | 6.3 | 0.00 | +0.00 | +0.0 |
| 2. No diagnostics (Muse) | 3 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +25.8 | 44.8 | yes | none | 4 | 11 | 0 | 0 | 1.32 | 6.1 | 0.00 | +0.00 | +0.0 |
| 3. Fixed model (Muse, M2 loop) | 1 | 1.5, 1.35, 1.15, 0.8 C (v0) | 37.03 | +26.2 | 44.8 | yes | none | 0 | 8 | 0 | 0 | 1.19 | 6.3 | 0.00 | -0.00 | +0.0 |
| 3. Fixed model (Muse, M2 loop) | 2 | 1.5, 1.45, 1.1, 0.8 C (v0) | 36.91 | +26.1 | 44.8 | yes | none | 0 | 8 | 0 | 0 | 1.21 | 4.7 | 0.00 | +0.00 | -0.0 |
| 3. Fixed model (Muse, M2 loop) | 3 | 1.5, 1.45, 1.1, 0.8 C (v0) | 36.91 | +26.1 | 44.8 | yes | none | 0 | 8 | 0 | 0 | 0.99 | 3.8 | 0.00 | +0.00 | -0.0 |
| 4. Calibrate once (numerical) | 1 | 1.5, 1.5, 1.05, 0.8 C (v1) | 37.12 | +25.8 | 44.8 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 3.5 | 0.02 | +0.03 | -0.0 |
| 4. Calibrate once (numerical) | 2 | 1.5, 1.5, 1.05, 0.8 C (v1) | 37.12 | +25.8 | 44.8 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 2.4 | 0.02 | +0.03 | -0.0 |
| 4. Calibrate once (numerical) | 3 | 1.5, 1.5, 1.05, 0.8 C (v1) | 37.12 | +25.8 | 44.8 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 2.4 | 0.01 | +0.02 | -0.0 |
| 5. Adaptive policy (numerical) | 1 | 1.5, 1.5, 1.05, 0.8 C (v2) | 37.12 | +25.8 | 44.8 | yes | none | 2 | 8 | 0 | 2 | 0.00 | 6.5 | 0.04 | -0.01 | -0.1 |
| 5. Adaptive policy (numerical) | 2 | 1.5, 1.5, 1.05, 0.8 C (v1) | 37.12 | +25.8 | 44.8 | yes | none | 2 | 6 | 0 | 1 | 0.00 | 4.7 | 0.04 | -0.01 | -0.1 |
| 5. Adaptive policy (numerical) | 3 | 1.5, 1.5, 1.05, 0.8 C (v1) | 37.12 | +25.8 | 44.8 | yes | none | 2 | 6 | 0 | 1 | 0.00 | 5.3 | 0.02 | +0.05 | -0.0 |

Per arm (medians over repeats; true t80 over the feasible repeats only):

| Arm | Runs | Feasible | Median true t80 (min) | Median lab tests | Median requests | Failed runs | Model $ | Median wall (min) | Interventions | Median T RMSE (K) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1. Adaptive (Muse) | 3 | 3 | 37.12 | 3 | 12 | 0 | 4.49 | 12.3 | 0 | 0.02 |
| 2. No diagnostics (Muse) | 3 | 3 | 37.12 | 4 | 11 | 0 | 4.87 | 6.3 | 0 | 0.00 |
| 3. Fixed model (Muse, M2 loop) | 3 | 3 | 36.91 | 0 | 8 | 0 | 3.39 | 4.7 | 0 | 0.00 |
| 4. Calibrate once (numerical) | 3 | 3 | 37.12 | 1 | 4 | 0 | 0.00 | 2.4 | 0 | 0.02 |
| 5. Adaptive policy (numerical) | 3 | 3 | 37.12 | 2 | 6 | 0 | 0.00 | 5.3 | 0 | 0.04 |

Criteria:

- Adaptive (Muse) is equal to the adaptive numerical policy: feasible in 3 of 3 against 3 of 3; median true t80 37.12 against 37.12 min.
- Adaptive (Muse) is equal to Muse without diagnostics: feasible in 3 of 3 against 3 of 3; median true t80 37.12 against 37.12 min.
- The adaptive numerical policy is equal to calibrate-once: feasible in 3 of 3 against 3 of 3; median true t80 37.12 against 37.12 min.
- The adaptive numerical policy is equal to the fixed model: feasible in 3 of 3 against 3 of 3; median true t80 37.12 against 36.91 min.
- 1. Adaptive (Muse): final design within one 0.05C step of the nominal design in at least 2 of 3: met (0 of 3 changed more).
- 2. No diagnostics (Muse): final design within one 0.05C step of the nominal design in at least 2 of 3: met (0 of 3 changed more).
- 4. Calibrate once (numerical): final design within one 0.05C step of the nominal design in at least 2 of 3: met (0 of 3 changed more).
- 5. Adaptive policy (numerical): final design within one 0.05C step of the nominal design in at least 2 of 3: met (0 of 3 changed more).

## Case C: changed cooling

| Arm | Repeat | Final protocol | True t80 (min) | Lowest anode (mV, sim.) | Peak T (°C) | Feasible | Violations | Lab tests | Requests | Failed | Model versions | Model $ | Wall (min) | T RMSE (K) | Peak T error (K) | Anode error (mV, sim.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1. Adaptive (Muse) | 1 | 1.5, 1.35, 0.9, 0.5 C (v1) | 47.44 | +26.3 | 37.5 | yes | none | 3 | 7 | 0 | 1 | 0.78 | 8.5 | 0.01 | +0.02 | -0.1 |
| 1. Adaptive (Muse) | 2 | 1.5, 1.35, 0.9, 0.5 C (v1) | 47.44 | +26.3 | 37.5 | yes | none | 3 | 7 | 0 | 1 | 0.67 | 7.8 | 0.01 | +0.02 | -0.2 |
| 1. Adaptive (Muse) | 3 | 1.5, 1.35, 0.9, 0.5 C (v1) | 47.44 | +26.3 | 37.5 | yes | none | 2 | 7 | 0 | 1 | 0.60 | 10.9 | 0.01 | +0.02 | -0.1 |
| 2. No diagnostics (Muse) | 1 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.15 | +6.9 | 38.9 | no | min_anode_potential by 18.124 mV | 2 | 4 | 0 | 0 | 0.39 | 3.0 | 7.09 | +5.96 | +18.9 |
| 2. No diagnostics (Muse) | 2 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.15 | +6.9 | 38.9 | no | min_anode_potential by 18.124 mV | 2 | 5 | 0 | 0 | 0.67 | 3.8 | 7.09 | +5.96 | +18.9 |
| 2. No diagnostics (Muse) | 3 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.15 | +6.9 | 38.9 | no | min_anode_potential by 18.124 mV | 4 | 12 | 0 | 0 | 1.52 | 7.1 | 7.09 | +5.96 | +18.9 |
| 3. Fixed model (Muse, M2 loop) | 1 | 1.5, 1.35, 1.15, 0.8 C (v0) | 37.05 | +7.1 | 37.5 | no | min_anode_potential by 17.873 mV | 0 | 8 | 0 | 0 | 1.19 | 6.3 | 6.95 | +7.28 | +19.0 |
| 3. Fixed model (Muse, M2 loop) | 2 | 1.5, 1.45, 1.1, 0.8 C (v0) | 36.93 | +7.0 | 38.4 | no | min_anode_potential by 17.983 mV | 0 | 8 | 0 | 0 | 1.21 | 4.7 | 7.06 | +6.31 | +19.1 |
| 3. Fixed model (Muse, M2 loop) | 3 | 1.5, 1.45, 1.1, 0.8 C (v0) | 36.93 | +7.0 | 38.4 | no | min_anode_potential by 17.983 mV | 0 | 8 | 0 | 0 | 0.99 | 3.8 | 7.06 | +6.31 | +19.1 |
| 4. Calibrate once (numerical) | 1 | 1.5, 1.35, 0.9, 0.5 C (v1) | 47.44 | +26.3 | 37.5 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 4.0 | 0.00 | +0.02 | -0.1 |
| 4. Calibrate once (numerical) | 2 | 1.5, 1.35, 0.9, 0.5 C (v1) | 47.44 | +26.3 | 37.5 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 2.9 | 0.00 | +0.03 | -0.1 |
| 4. Calibrate once (numerical) | 3 | 1.5, 1.35, 0.9, 0.5 C (v1) | 47.44 | +26.3 | 37.5 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 2.5 | 0.01 | +0.04 | -0.1 |
| 5. Adaptive policy (numerical) | 1 | 1.5, 1.35, 0.9, 0.5 C (v1) | 47.44 | +26.3 | 37.5 | yes | none | 2 | 6 | 0 | 1 | 0.00 | 4.7 | 0.00 | +0.04 | -0.1 |
| 5. Adaptive policy (numerical) | 2 | 1.5, 1.35, 0.9, 0.5 C (v1) | 47.44 | +26.3 | 37.5 | yes | none | 2 | 6 | 0 | 1 | 0.00 | 4.7 | 0.01 | +0.04 | -0.1 |
| 5. Adaptive policy (numerical) | 3 | 1.5, 1.35, 0.9, 0.5 C (v1) | 47.44 | +26.3 | 37.5 | yes | none | 2 | 6 | 0 | 1 | 0.00 | 4.9 | 0.02 | +0.04 | -0.3 |

Per arm (medians over repeats; true t80 over the feasible repeats only):

| Arm | Runs | Feasible | Median true t80 (min) | Median lab tests | Median requests | Failed runs | Model $ | Median wall (min) | Interventions | Median T RMSE (K) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1. Adaptive (Muse) | 3 | 3 | 47.44 | 3 | 7 | 0 | 2.05 | 8.5 | 0 | 0.01 |
| 2. No diagnostics (Muse) | 3 | 0 | none | 2 | 5 | 0 | 2.57 | 3.8 | 0 | 7.09 |
| 3. Fixed model (Muse, M2 loop) | 3 | 0 | none | 0 | 8 | 0 | 3.39 | 4.7 | 0 | 7.06 |
| 4. Calibrate once (numerical) | 3 | 3 | 47.44 | 1 | 4 | 0 | 0.00 | 2.9 | 0 | 0.00 |
| 5. Adaptive policy (numerical) | 3 | 3 | 47.44 | 2 | 6 | 0 | 0.00 | 4.7 | 0 | 0.01 |

Criteria:

- Adaptive (Muse) is equal to the adaptive numerical policy: feasible in 3 of 3 against 3 of 3; median true t80 47.44 against 47.44 min.
- Adaptive (Muse) adds over Muse without diagnostics: feasible in 3 of 3 against 0 of 3; median true t80 47.44 against none min.
- The adaptive numerical policy is equal to calibrate-once: feasible in 3 of 3 against 3 of 3; median true t80 47.44 against 47.44 min.
- The adaptive numerical policy adds over the fixed model: feasible in 3 of 3 against 0 of 3; median true t80 47.44 against none min.
- Changed cooling, adaptive (Muse) feasible in at least 2 of 3 where the fixed model is not: met (adaptive feasible 3, fixed model infeasible 3).

## Case U: uncertainty that does not change the design

| Arm | Repeat | Final protocol | True t80 (min) | Lowest anode (mV, sim.) | Peak T (°C) | Feasible | Violations | Lab tests | Requests | Failed | Model versions | Model $ | Wall (min) | T RMSE (K) | Peak T error (K) | Anode error (mV, sim.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1. Adaptive (Muse) | 1 | 1.5, 1.5, 1.15, 0.7 C (v2) | 38.13 | +28.4 | 44.1 | yes | none | 3 | 11 | 0 | 3 | 1.32 | 17.4 | 0.07 | +0.31 | -0.3 |
| 1. Adaptive (Muse) | 2 | 1.5, 1.5, 1.2, 0.75 C (v2) | 36.75 | +26.1 | 44.6 | yes | none | 4 | 12 | 0 | 2 | 1.48 | 19.2 | 0.03 | +0.12 | -0.3 |
| 1. Adaptive (Muse) | 3 | 1.5, 1.5, 1.2, 0.75 C (v2) | 36.75 | +26.1 | 44.6 | yes | none | 4 | 12 | 0 | 2 | 1.50 | 25.4 | 0.03 | +0.20 | -0.5 |
| 2. No diagnostics (Muse) | 1 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +22.3 | 44.0 | no | min_anode_potential by 2.67 mV | 4 | 12 | 0 | 0 | 1.70 | 7.5 | 1.20 | +0.84 | +3.5 |
| 2. No diagnostics (Muse) | 2 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +22.3 | 44.0 | no | min_anode_potential by 2.67 mV | 3 | 12 | 0 | 0 | 1.99 | 7.7 | 1.20 | +0.84 | +3.5 |
| 2. No diagnostics (Muse) | 3 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +22.3 | 44.0 | no | min_anode_potential by 2.67 mV | 4 | 10 | 0 | 0 | 1.09 | 5.4 | 1.20 | +0.84 | +3.5 |
| 3. Fixed model (Muse, M2 loop) | 1 | 1.5, 1.35, 1.15, 0.8 C (v0) | 37.03 | +22.7 | 43.4 | no | min_anode_potential by 2.317 mV | 0 | 8 | 0 | 0 | 1.19 | 6.3 | 1.17 | +1.40 | +3.5 |
| 3. Fixed model (Muse, M2 loop) | 2 | 1.5, 1.45, 1.1, 0.8 C (v0) | 36.91 | +22.6 | 43.6 | no | min_anode_potential by 2.397 mV | 0 | 8 | 0 | 0 | 1.21 | 4.7 | 1.19 | +1.17 | +3.5 |
| 3. Fixed model (Muse, M2 loop) | 3 | 1.5, 1.45, 1.1, 0.8 C (v0) | 36.91 | +22.6 | 43.6 | no | min_anode_potential by 2.397 mV | 0 | 8 | 0 | 0 | 0.99 | 3.8 | 1.19 | +1.17 | +3.5 |
| 4. Calibrate once (numerical) | 1 | 1.5, 1.5, 1.2, 0.75 C (v1) | 36.75 | +26.1 | 44.6 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 2.6 | 0.05 | +0.15 | -0.6 |
| 4. Calibrate once (numerical) | 2 | 1.5, 1.5, 1.2, 0.7 C (v1) | 37.75 | +28.8 | 44.6 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 2.4 | 0.08 | +0.11 | -0.7 |
| 4. Calibrate once (numerical) | 3 | 1.5, 1.5, 1.2, 0.7 C (v1) | 37.75 | +28.8 | 44.6 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 2.4 | 0.06 | +0.14 | -0.7 |
| 5. Adaptive policy (numerical) | 1 | 1.5, 1.5, 1.2, 0.75 C (v3) | 36.75 | +26.1 | 44.6 | yes | none | 3 | 11 | 0 | 3 | 0.00 | 7.2 | 0.04 | +0.13 | -0.4 |
| 5. Adaptive policy (numerical) | 2 | 1.5, 1.5, 1.2, 0.75 C (v2) | 36.75 | +26.1 | 44.6 | yes | none | 2 | 8 | 0 | 2 | 0.00 | 6.5 | 0.03 | +0.23 | -0.5 |
| 5. Adaptive policy (numerical) | 3 | 1.5, 1.5, 1.2, 0.7 C (v2) | 37.75 | +28.8 | 44.6 | yes | none | 2 | 8 | 0 | 2 | 0.00 | 7.0 | 0.04 | +0.12 | -0.7 |

Per arm (medians over repeats; true t80 over the feasible repeats only):

| Arm | Runs | Feasible | Median true t80 (min) | Median lab tests | Median requests | Failed runs | Model $ | Median wall (min) | Interventions | Median T RMSE (K) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1. Adaptive (Muse) | 3 | 3 | 36.75 | 4 | 12 | 0 | 4.30 | 19.2 | 0 | 0.03 |
| 2. No diagnostics (Muse) | 3 | 0 | none | 4 | 12 | 0 | 4.78 | 7.5 | 0 | 1.20 |
| 3. Fixed model (Muse, M2 loop) | 3 | 0 | none | 0 | 8 | 0 | 3.39 | 4.7 | 0 | 1.19 |
| 4. Calibrate once (numerical) | 3 | 3 | 37.75 | 1 | 4 | 0 | 0.00 | 2.4 | 0 | 0.06 |
| 5. Adaptive policy (numerical) | 3 | 3 | 36.75 | 2 | 8 | 0 | 0.00 | 7.0 | 0 | 0.04 |

Criteria:

- Adaptive (Muse) is equal to the adaptive numerical policy: feasible in 3 of 3 against 3 of 3; median true t80 36.75 against 36.75 min.
- Adaptive (Muse) adds over Muse without diagnostics: feasible in 3 of 3 against 0 of 3; median true t80 36.75 against none min.
- The adaptive numerical policy adds over calibrate-once: feasible in 3 of 3 against 3 of 3; median true t80 36.75 against 37.75 min.
- The adaptive numerical policy adds over the fixed model: feasible in 3 of 3 against 0 of 3; median true t80 36.75 against none min.
- 1. Adaptive (Muse): stops learning (at most 1 lab test after the first fit) with a feasible design in at least 2 of 3: not met (stopped 0, stopped and feasible 0 of 3; median lab tests after the first fit 3).
- 4. Calibrate once (numerical): stops learning (at most 1 lab test after the first fit) with a feasible design in at least 2 of 3: met (stopped 3, stopped and feasible 3 of 3; median lab tests after the first fit 0).
- 5. Adaptive policy (numerical): stops learning (at most 1 lab test after the first fit) with a feasible design in at least 2 of 3: met (stopped 2, stopped and feasible 2 of 3; median lab tests after the first fit 1).

## Case M: thermal mass (outside the one-parameter family)

| Arm | Repeat | Final protocol | True t80 (min) | Lowest anode (mV, sim.) | Peak T (°C) | Feasible | Violations | Lab tests | Requests | Failed | Model versions | Model $ | Wall (min) | T RMSE (K) | Peak T error (K) | Anode error (mV, sim.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1. Adaptive (Muse) | 1 | 1.5, 1.4, 1.15, 0.75 C (v1) | 37.63 | +27.0 | 41.6 | yes | none | 2 | 7 | 0 | 1 | 0.65 | 13.4 | 0.02 | +0.01 | -0.1 |
| 1. Adaptive (Muse) | 2 | 1.5, 1.4, 1.15, 0.75 C (v2) | 37.63 | +27.0 | 41.6 | yes | none | 3 | 9 | 0 | 2 | 0.97 | 17.6 | 0.01 | +0.06 | -0.2 |
| 1. Adaptive (Muse) | 3 | 1.5, 1.4, 1.15, 0.75 C (v1) | 37.63 | +27.0 | 41.6 | yes | none | 3 | 7 | 0 | 1 | 0.62 | 9.0 | 0.02 | +0.00 | -0.1 |
| 2. No diagnostics (Muse) | 1 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +22.1 | 41.2 | no | min_anode_potential by 2.856 mV | 4 | 12 | 0 | 0 | 1.57 | 6.5 | 3.34 | +3.61 | +3.7 |
| 2. No diagnostics (Muse) | 2 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +22.1 | 41.2 | no | min_anode_potential by 2.856 mV | 4 | 12 | 0 | 0 | 1.38 | 6.0 | 3.34 | +3.61 | +3.7 |
| 2. No diagnostics (Muse) | 3 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +22.1 | 41.2 | no | min_anode_potential by 2.856 mV | 4 | 11 | 0 | 0 | 1.24 | 5.9 | 3.34 | +3.61 | +3.7 |
| 3. Fixed model (Muse, M2 loop) | 1 | 1.5, 1.35, 1.15, 0.8 C (v0) | 37.03 | +23.9 | 41.4 | no | min_anode_potential by 1.058 mV | 0 | 8 | 0 | 0 | 1.19 | 6.3 | 3.30 | +3.44 | +2.2 |
| 3. Fixed model (Muse, M2 loop) | 2 | 1.5, 1.45, 1.1, 0.8 C (v0) | 36.91 | +24.0 | 41.4 | no | min_anode_potential by 1.017 mV | 0 | 8 | 0 | 0 | 1.21 | 4.7 | 3.35 | +3.35 | +2.2 |
| 3. Fixed model (Muse, M2 loop) | 3 | 1.5, 1.45, 1.1, 0.8 C (v0) | 36.91 | +24.0 | 41.4 | no | min_anode_potential by 1.017 mV | 0 | 8 | 0 | 0 | 0.99 | 3.8 | 3.35 | +3.35 | +2.2 |
| 4. Calibrate once (numerical) | 1 | 1.5, 1.3, 0.9, 0.6 C (v1) | 44.24 | +32.7 | 39.3 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 2.2 | 2.82 | +5.36 | -5.1 |
| 4. Calibrate once (numerical) | 2 | 1.5, 1.3, 0.9, 0.6 C (v1) | 44.24 | +32.7 | 39.3 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 2.2 | 2.83 | +5.38 | -5.1 |
| 4. Calibrate once (numerical) | 3 | 1.5, 1.3, 0.9, 0.6 C (v1) | 44.24 | +32.7 | 39.3 | yes | none | 1 | 4 | 0 | 1 | 0.00 | 2.4 | 2.85 | +5.38 | -5.1 |
| 5. Adaptive policy (numerical) | 1 | 1.5, 1.4, 1.15, 0.75 C (v1) | 37.63 | +27.0 | 41.6 | yes | none | 2 | 6 | 0 | 1 | 0.00 | 4.9 | 0.01 | +0.01 | -0.2 |
| 5. Adaptive policy (numerical) | 2 | 1.5, 1.4, 1.15, 0.75 C (v1) | 37.63 | +27.0 | 41.6 | yes | none | 2 | 6 | 0 | 1 | 0.00 | 4.3 | 0.03 | +0.02 | -0.2 |
| 5. Adaptive policy (numerical) | 3 | 1.5, 1.4, 1.15, 0.75 C (v1) | 37.63 | +27.0 | 41.6 | yes | none | 2 | 6 | 0 | 1 | 0.00 | 5.0 | 0.02 | +0.06 | -0.2 |

Per arm (medians over repeats; true t80 over the feasible repeats only):

| Arm | Runs | Feasible | Median true t80 (min) | Median lab tests | Median requests | Failed runs | Model $ | Median wall (min) | Interventions | Median T RMSE (K) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1. Adaptive (Muse) | 3 | 3 | 37.63 | 3 | 7 | 0 | 2.23 | 13.4 | 0 | 0.02 |
| 2. No diagnostics (Muse) | 3 | 0 | none | 4 | 12 | 0 | 4.19 | 6.0 | 0 | 3.34 |
| 3. Fixed model (Muse, M2 loop) | 3 | 0 | none | 0 | 8 | 0 | 3.39 | 4.7 | 0 | 3.35 |
| 4. Calibrate once (numerical) | 3 | 3 | 44.24 | 1 | 4 | 0 | 0.00 | 2.2 | 0 | 2.83 |
| 5. Adaptive policy (numerical) | 3 | 3 | 37.63 | 2 | 6 | 0 | 0.00 | 4.9 | 0 | 0.02 |

Criteria:

- Adaptive (Muse) is equal to the adaptive numerical policy: feasible in 3 of 3 against 3 of 3; median true t80 37.63 against 37.63 min.
- Adaptive (Muse) adds over Muse without diagnostics: feasible in 3 of 3 against 0 of 3; median true t80 37.63 against none min.
- The adaptive numerical policy adds over calibrate-once: feasible in 3 of 3 against 3 of 3; median true t80 37.63 against 44.24 min.
- The adaptive numerical policy adds over the fixed model: feasible in 3 of 3 against 0 of 3; median true t80 37.63 against none min.
- 1. Adaptive (Muse): final design under an adequate model version and feasible in at least 2 of 3: met (adequate 3, feasible 3 of 3).
- 4. Calibrate once (numerical): final design under an adequate model version and feasible in at least 2 of 3: not met (adequate 0, feasible 3 of 3).
- 5. Adaptive policy (numerical): final design under an adequate model version and feasible in at least 2 of 3: met (adequate 3, feasible 3 of 3).

## Case O: omitted mechanism

| Arm | Repeat | Final protocol | True t80 (min) | Lowest anode (mV, sim.) | Peak T (°C) | Feasible | Violations | Lab tests | Requests | Failed | Model versions | Model $ | Wall (min) | T RMSE (K) | Peak T error (K) | Anode error (mV, sim.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1. Adaptive (Muse) | 1 | 1.5, 1.2, 0.95, 0.8 C (v3) | 39.93 | +28.0 | 44.4 | yes | none | 3 | 11 | 0 | 3 | 1.17 | 25.7 | 1.18 | +0.33 | -1.1 |
| 1. Adaptive (Muse) | 2 | 1.5, 1, 0.9, 0.75 C (v3) | 43.17 | +29.0 | 42.5 | yes | none | 4 | 11 | 0 | 3 | 1.15 | 20.9 | 1.18 | +2.20 | -1.7 |
| 1. Adaptive (Muse) | 3 | 1.5, 1.1, 0.95, 0.8 C (v3) | 40.72 | +27.5 | 43.5 | yes | none | 4 | 10 | 0 | 3 | 1.32 | 21.5 | 0.85 | +0.96 | -1.0 |
| 2. No diagnostics (Muse) | 1 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +30.3 | 46.9 | no | max_temperature by 1.94 K | 4 | 12 | 0 | 0 | 1.38 | 7.8 | 2.27 | -2.10 | -4.5 |
| 2. No diagnostics (Muse) | 2 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +30.3 | 46.9 | no | max_temperature by 1.94 K | 4 | 10 | 0 | 0 | 1.70 | 6.8 | 2.27 | -2.10 | -4.5 |
| 2. No diagnostics (Muse) | 3 | 1.5, 1.5, 1.05, 0.8 C (v0) | 37.12 | +30.3 | 46.9 | no | max_temperature by 1.94 K | 4 | 12 | 0 | 0 | 1.56 | 6.6 | 2.27 | -2.10 | -4.5 |
| 3. Fixed model (Muse, M2 loop) | 1 | 1.5, 1.35, 1.15, 0.8 C (v0) | 37.03 | +30.6 | 46.9 | no | max_temperature by 1.908 K | 0 | 8 | 0 | 0 | 1.19 | 6.3 | 2.28 | -2.09 | -4.5 |
| 3. Fixed model (Muse, M2 loop) | 2 | 1.5, 1.45, 1.1, 0.8 C (v0) | 36.91 | +30.6 | 46.9 | no | max_temperature by 1.851 K | 0 | 8 | 0 | 0 | 1.21 | 4.7 | 2.27 | -2.09 | -4.4 |
| 3. Fixed model (Muse, M2 loop) | 3 | 1.5, 1.45, 1.1, 0.8 C (v0) | 36.91 | +30.6 | 46.9 | no | max_temperature by 1.851 K | 0 | 8 | 0 | 0 | 0.99 | 3.8 | 2.27 | -2.09 | -4.4 |
| 4. Calibrate once (numerical) | 1 | 1.5, 1.3, 0.9, 0.8 C (v1) | 39.87 | +27.9 | 45.3 | no | max_temperature by 0.304 K | 1 | 4 | 0 | 1 | 0.00 | 2.4 | 1.28 | -0.43 | -0.9 |
| 4. Calibrate once (numerical) | 2 | 1.5, 1.3, 0.9, 0.8 C (v1) | 39.87 | +27.9 | 45.3 | no | max_temperature by 0.304 K | 1 | 4 | 0 | 1 | 0.00 | 2.5 | 1.28 | -0.43 | -0.9 |
| 4. Calibrate once (numerical) | 3 | 1.5, 1.3, 0.9, 0.8 C (v1) | 39.87 | +27.9 | 45.3 | no | max_temperature by 0.304 K | 1 | 4 | 0 | 1 | 0.00 | 2.7 | 1.28 | -0.43 | -0.9 |
| 5. Adaptive policy (numerical) | 1 | 1.5, 1.2, 0.95, 0.8 C (v3) | 39.93 | +28.0 | 44.4 | yes | none | 3 | 11 | 0 | 3 | 0.00 | 6.3 | 1.20 | +0.41 | -0.6 |
| 5. Adaptive policy (numerical) | 2 | 1.5, 1.2, 0.95, 0.8 C (v3) | 39.93 | +28.0 | 44.4 | yes | none | 3 | 11 | 0 | 3 | 0.00 | 6.3 | 1.22 | +0.40 | -0.6 |
| 5. Adaptive policy (numerical) | 3 | 1.5, 1.2, 0.95, 0.8 C (v3) | 39.93 | +28.0 | 44.4 | yes | none | 3 | 11 | 0 | 3 | 0.00 | 6.5 | 1.20 | +0.47 | -0.6 |

Per arm (medians over repeats; true t80 over the feasible repeats only):

| Arm | Runs | Feasible | Median true t80 (min) | Median lab tests | Median requests | Failed runs | Model $ | Median wall (min) | Interventions | Median T RMSE (K) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1. Adaptive (Muse) | 3 | 3 | 40.72 | 4 | 11 | 0 | 3.64 | 21.5 | 0 | 1.18 |
| 2. No diagnostics (Muse) | 3 | 0 | none | 4 | 12 | 0 | 4.65 | 6.8 | 0 | 2.27 |
| 3. Fixed model (Muse, M2 loop) | 3 | 0 | none | 0 | 8 | 0 | 3.39 | 4.7 | 0 | 2.27 |
| 4. Calibrate once (numerical) | 3 | 0 | none | 1 | 4 | 0 | 0.00 | 2.5 | 0 | 1.28 |
| 5. Adaptive policy (numerical) | 3 | 3 | 39.93 | 3 | 11 | 0 | 0.00 | 6.3 | 0 | 1.20 |

Criteria:

- Adaptive (Muse) is equal to the adaptive numerical policy: feasible in 3 of 3 against 3 of 3; median true t80 40.72 against 39.93 min.
- Adaptive (Muse) adds over Muse without diagnostics: feasible in 3 of 3 against 0 of 3; median true t80 40.72 against none min.
- The adaptive numerical policy adds over calibrate-once: feasible in 3 of 3 against 0 of 3; median true t80 39.93 against none min.
- The adaptive numerical policy adds over the fixed model: feasible in 3 of 3 against 0 of 3; median true t80 39.93 against none min.
- 1. Adaptive (Muse): an inadequate fit flagged in at least 2 of 3: met (3 of 3).
- 4. Calibrate once (numerical): an inadequate fit flagged in at least 2 of 3: met (3 of 3).
- 5. Adaptive policy (numerical): an inadequate fit flagged in at least 2 of 3: met (3 of 3).
