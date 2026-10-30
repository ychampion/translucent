# NIST intervention benchmark

The decisive comparator is always_escalate: the learned policies do not establish an advantage over it in the registered primary analysis. The primary differences against hard-rule baselines can arise from the decision-rule asymmetry, an illustrative temperature cut, and failed physics forecasts. Such differences alone do not establish that the agent learned a better intervention model.

This is a factual case study of ignition after the recorded heater cut. An alternative stop time has no observed outcome. No provider was called by this benchmark. Suppression efficacy was not recorded.

Amendment 2 changed the costs and primary endpoint: cf46b56 was committed at 2026-10-30 02:12:58 +05:30 and c71925d at 02:22:19 +05:30. Amendment 3 restricted the correction family and candidate cap to screened agent submissions: bbda321 was committed at 2026-10-30 02:50:31 +05:30 (2026-10-29 21:20:31 UTC). The first archived scoring run began at 2026-10-29 21:21:02 UTC, 31 seconds after amendment 3. These commits precede the archived scoring runs and any Muse Wonder campaign; git cannot establish that no earlier held-out result had been inspected. The dataset, exploratory analyses and feature library were already available. This amendment chain is not evidence of an unseen-data preregistration.

The registered primary endpoint remains mean decision cost: unsafe stand-down costs 100, ignition during a paid watch costs 100, unnecessary escalation costs 10, watching costs 0.05 per second, and abstention costs 10 as a deferred escalation. Abstention is counted as escalation for missed rescues and accuracy and is charged once. Cost-ratio sensitivities retain the registered escalation and watch costs.

The registered paired test uses the absolute sum of per-trial cost differences: all sign assignments through 20 nonzero pairs, otherwise 100,000 seeded assignments with a plus-one correction. Holm correction covers screened agent candidate submissions times registered baseline comparators. A missing Muse comparator contributes p=1. Baseline-versus-baseline differences are descriptive. The registered family is retained for every split and data sensitivity, but only the default-lag, with-inferred LOCO result is primary; all other splits and sensitivities are descriptive. Unsafe-only McNemar and the secondary two-part results are reported descriptively. Observed unsafe nonincrease does not establish statistical noninferiority.

Ledger registration counts: with_inferred-lag10-ratio10: 2 candidate submissions and 9 registered baselines; without_inferred-lag10-ratio10: 2 candidate submissions and 9 registered baselines; with_inferred-lag7-ratio10: 2 candidate submissions and 9 registered baselines; without_inferred-lag7-ratio10: 2 candidate submissions and 9 registered baselines; with_inferred-lag10-ratio5: 2 candidate submissions and 9 registered baselines; with_inferred-lag10-ratio20: 2 candidate submissions and 9 registered baselines.

Policies search generic feature families using training-fold CV. Only screened agent strategies count toward the fixed K=16 candidate cap; baseline registrations do not consume K. The ledger freezes registrations before the first held-out decision. LOFO reports the known format-transfer failure, and prequential commits in Exp-number order. Within-condition outcomes share a fitted model, so per-trial sign flips can overstate the evidence when paired losses are dependent. The condition-clustered sensitivity below addresses this dependence descriptively without changing the primary analysis.

The registered 10 s thermocouple lag is primary. The separately refitted 7 s sensitivity reflects the supply-lamp video marker; edited Power Off timing supports 9–10 s. Both lags are scored with and without inferred decision points. Rescued traces stop at render-safe reheat. Exp3 and Exp17 lack traces and are excluded; Exp67/73 are finite-window censored rescues, with exclusion scores in JSON; Exp33 has suspect SOC. Outcome-correlated data-quality flags are hidden from planner tools and Muse prompts, and retained in the ledger for audit and subset analysis.

The without_inferred subset selects on outcome: it excludes trials whose heater-off could not be located because ignition followed quickly. It is not a neutral data-quality sensitivity. Decision points inferred using paper-mean vent-relative delays are reconstructed by a different procedure from directly located cuts.

Paper Table 3 is a retrospective in-sample lookup using these outcomes, with an Exp44 inconsistency. Missing conditions use a default delay of -1 s, which implements always-escalate; that is an implementation choice. The separately refitted delay lookup is the validation comparator. The fixed_temperature baseline's 170 °C cut is illustrative, unsourced. The threshold sweep below does not select a replacement primary cut. temperature_refit reproduces the exploratory pooled threshold.

World models return probabilities and intervals; separate decision rules map them to actions. The primary agent strategies use the registered cost rule, whereas the frozen probability-producing baselines use their classification rules. Applying different decision rules under asymmetric costs is a substantive comparator choice. The post-hoc cost-rule sensitivity below applies the same registered cost rule to each baseline's own held-out probability, without refitting or adding observation.

Observation releases only a paid recorded window from the registered options. Ignition while waiting is unsafe regardless of the terminal decision, and watch time then stops at ignition. The optimistic perfect-information continuation bound and training watch-risk estimate used to choose observation are not calibrated guarantees.

Prequential updates require a prior reveal, no newly unsafe revealed trial, and no more than 0.01 deterioration in calibration or Brier score. Rejected fits remain unapplied. Every forecast carries simulated=true; claims use real recorded outcomes and preceding commitments.

The two-node solver uses same-condition continuous-heating priors and fits hidden core state to pre-off surface measurements. Hidden state and alternative-action forecasts remain simulated. A physics-prior feature competes only inside training folds. Proposed experiments rank mechanistic-model disagreement and include a human-reviewable heating and watch schedule. No proposed experiment was performed.

Binary intervals are Wilson 95%; losses and mean cost have condition-block bootstrap 95% intervals. Prediction intervals bootstrap training conditions conditional on the selected spec and are not calibrated guarantees.

Across the source interrupted-trial set, all 5 inferred heater-off decision points precede recorded ignition by 2–50 s. Excluding them removes ignition outcomes preferentially and changes the interpretation of watch-policy costs.

Passive continuous heating has 26/26 recorded ignitions (Wilson 95% 87.1–100.0%; 25 traces), a separate observed series without a paired decision point.

Cost assumption for this table: escalating on a trial that ignites costs 0; escalation efficacy was not observed.

| Data, lag, unsafe:escalation | Split | Policy | n | Mean cost [95%] | Unsafe [rate 95%] | Missed [rate 95%] | Abstain | Watch s | Brier [95%] | Log-loss [95%] | Accuracy [Wilson 95%] |
|---|---|---|---:|---|---|---|---:|---:|---|---|---|
| with_inferred-lag10-ratio10 | loco | always_escalate | 50 | 3.800 [2.192, 5.101] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.219, 0.510] | 2.626 [1.515, 3.524] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio10 | loco | paper_table3 | 50 | 10.400 [3.976, 17.843] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.043, 0.226] | 0.968 [0.297, 1.565] | 0.860 [0.738, 0.930] |
| with_inferred-lag10-ratio10 | loco | delay_lookup_refit | 50 | 14.200 [1.502, 29.798] | 6 [0.056, 0.238] | 11 [0.128, 0.352] | 0 | 0.0 | 0.277 [0.163, 0.401] | 0.818 [0.527, 1.142] | 0.660 [0.522, 0.776] |
| with_inferred-lag10-ratio10 | loco | fixed_temperature | 50 | 27.600 [14.136, 43.883] | 13 [0.159, 0.396] | 8 [0.083, 0.285] | 0 | 0.0 | 0.257 [0.214, 0.305] | 0.714 [0.618, 0.823] | 0.580 [0.442, 0.706] |
| with_inferred-lag10-ratio10 | loco | temperature_refit | 50 | 8.400 [2.280, 15.691] | 3 [0.021, 0.162] | 12 [0.143, 0.374] | 0 | 0.0 | 0.229 [0.154, 0.310] | 0.668 [0.495, 0.862] | 0.700 [0.562, 0.809] |
| with_inferred-lag10-ratio10 | loco | physics_only | 50 | 62.000 [48.995, 78.080] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.490, 0.781] | 4.283 [3.385, 5.394] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio10 | loco | two_node_physics | 50 | 62.000 [48.995, 78.080] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.605] | 1.729 [1.159, 2.378] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio10 | loco | always_observe_30 | 50 | 14.261 [6.423, 22.157] | 6 [0.056, 0.238] | 4 [0.032, 0.188] | 0 | 1461.0 | 0.133 [0.071, 0.189] | 0.443 [0.287, 0.595] | 0.800 [0.670, 0.888] |
| with_inferred-lag10-ratio10 | loco | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag10-ratio10 | loco | agent_threshold | 50 | 3.800 [2.192, 5.101] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.203 [0.110, 0.295] | 0.611 [0.383, 0.829] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio10 | loco | agent_logistic | 50 | 3.720 [2.065, 5.118] | 0 [0.000, 0.071] | 18 [0.241, 0.499] | 0 | 120.0 | 0.152 [0.091, 0.216] | 0.468 [0.315, 0.634] | 0.640 [0.501, 0.759] |
| with_inferred-lag10-ratio10 | lofo | always_escalate | 50 | 3.800 [2.298, 5.205] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.230, 0.521] | 2.626 [1.588, 3.596] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio10 | lofo | paper_table3 | 50 | 10.400 [3.817, 17.307] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.041, 0.238] | 0.968 [0.283, 1.646] | 0.860 [0.738, 0.930] |
| with_inferred-lag10-ratio10 | lofo | delay_lookup_refit | 50 | 62.000 [47.948, 77.023] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.310 [0.260, 0.376] | 0.821 [0.716, 0.964] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio10 | lofo | fixed_temperature | 50 | 27.600 [15.179, 40.899] | 13 [0.159, 0.396] | 8 [0.083, 0.285] | 0 | 0.0 | 0.333 [0.261, 0.404] | 0.921 [0.743, 1.101] | 0.580 [0.442, 0.706] |
| with_inferred-lag10-ratio10 | lofo | temperature_refit | 50 | 17.800 [5.208, 35.482] | 7 [0.070, 0.262] | 19 [0.259, 0.518] | 0 | 0.0 | 0.365 [0.244, 0.461] | 1.000 [0.707, 1.244] | 0.480 [0.348, 0.615] |
| with_inferred-lag10-ratio10 | lofo | physics_only | 50 | 62.000 [47.948, 77.023] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.479, 0.770] | 4.283 [3.313, 5.321] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio10 | lofo | two_node_physics | 50 | 62.000 [47.948, 77.023] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.599] | 1.729 [1.272, 2.325] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio10 | lofo | always_observe_30 | 50 | 25.661 [18.610, 34.265] | 11 [0.128, 0.352] | 11 [0.128, 0.352] | 0 | 1461.0 | 0.297 [0.260, 0.350] | 0.823 [0.744, 0.940] | 0.560 [0.423, 0.688] |
| with_inferred-lag10-ratio10 | lofo | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | 50 | 16.400 [6.934, 28.370] | 7 [0.070, 0.262] | 12 [0.143, 0.374] | 0 | 0.0 | 0.273 [0.223, 0.327] | 0.800 [0.669, 0.952] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | 50 | 3.830 [2.330, 5.222] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 30.0 | 0.350 [0.215, 0.467] | 1.676 [0.920, 2.355] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio10 | prequential | always_escalate | 50 | 3.800 [2.298, 5.205] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.230, 0.521] | 2.626 [1.588, 3.596] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio10 | prequential | paper_table3 | 50 | 10.400 [3.817, 17.307] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.041, 0.238] | 0.968 [0.283, 1.646] | 0.860 [0.738, 0.930] |
| with_inferred-lag10-ratio10 | prequential | delay_lookup_refit | 50 | 42.800 [33.623, 53.791] | 21 [0.294, 0.558] | 4 [0.032, 0.188] | 0 | 0.0 | 0.315 [0.265, 0.359] | 0.933 [0.736, 1.183] | 0.500 [0.366, 0.634] |
| with_inferred-lag10-ratio10 | prequential | fixed_temperature | 50 | 27.800 [15.179, 41.188] | 13 [0.159, 0.396] | 9 [0.098, 0.308] | 0 | 0.0 | 0.274 [0.197, 0.357] | 0.849 [0.578, 1.168] | 0.560 [0.423, 0.688] |
| with_inferred-lag10-ratio10 | prequential | temperature_refit | 50 | 15.600 [5.355, 25.913] | 7 [0.070, 0.262] | 8 [0.083, 0.285] | 0 | 0.0 | 0.251 [0.131, 0.372] | 0.835 [0.447, 1.243] | 0.700 [0.562, 0.809] |
| with_inferred-lag10-ratio10 | prequential | physics_only | 50 | 62.000 [47.948, 77.023] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.479, 0.770] | 4.283 [3.313, 5.321] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio10 | prequential | two_node_physics | 50 | 62.000 [47.948, 77.023] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.599] | 1.729 [1.272, 2.325] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio10 | prequential | always_observe_30 | 50 | 19.061 [9.982, 28.468] | 8 [0.083, 0.285] | 8 [0.083, 0.285] | 0 | 1461.0 | 0.235 [0.132, 0.333] | 0.787 [0.463, 1.125] | 0.680 [0.542, 0.792] |
| with_inferred-lag10-ratio10 | prequential | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | 50 | 6.200 [1.649, 11.087] | 2 [0.011, 0.135] | 11 [0.128, 0.352] | 0 | 0.0 | 0.300 [0.200, 0.394] | 1.117 [0.655, 1.676] | 0.740 [0.604, 0.841] |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | 50 | 4.490 [0.891, 8.807] | 1 [0.004, 0.105] | 12 [0.143, 0.374] | 0 | 90.0 | 0.194 [0.086, 0.307] | 0.724 [0.291, 1.262] | 0.740 [0.604, 0.841] |
| without_inferred-lag10-ratio10 | loco | always_escalate | 45 | 4.222 [2.346, 5.874] | 0 [0.000, 0.079] | 19 [0.290, 0.567] | 0 | 0.0 | 0.422 [0.235, 0.587] | 2.917 [1.622, 4.058] | 0.578 [0.433, 0.710] |
| without_inferred-lag10-ratio10 | loco | paper_table3 | 45 | 9.333 [3.158, 14.593] | 4 [0.035, 0.207] | 2 [0.012, 0.148] | 0 | 0.0 | 0.133 [0.044, 0.220] | 0.922 [0.304, 1.523] | 0.867 [0.738, 0.937] |
| without_inferred-lag10-ratio10 | loco | delay_lookup_refit | 45 | 15.778 [2.819, 28.012] | 6 [0.063, 0.262] | 11 [0.142, 0.387] | 0 | 0.0 | 0.285 [0.188, 0.398] | 0.828 [0.561, 1.122] | 0.622 [0.476, 0.749] |
| without_inferred-lag10-ratio10 | loco | fixed_temperature | 45 | 26.222 [13.191, 41.860] | 11 [0.142, 0.387] | 8 [0.093, 0.313] | 0 | 0.0 | 0.270 [0.236, 0.309] | 0.737 [0.664, 0.824] | 0.578 [0.433, 0.710] |
| without_inferred-lag10-ratio10 | loco | temperature_refit | 45 | 11.556 [4.880, 17.826] | 4 [0.035, 0.207] | 12 [0.160, 0.410] | 0 | 0.0 | 0.255 [0.191, 0.322] | 0.719 [0.577, 0.875] | 0.644 [0.498, 0.768] |
| without_inferred-lag10-ratio10 | loco | physics_only | 45 | 57.778 [41.255, 76.537] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.578 [0.413, 0.765] | 3.992 [2.850, 5.287] | 0.422 [0.290, 0.567] |
| without_inferred-lag10-ratio10 | loco | two_node_physics | 45 | 57.778 [41.255, 76.537] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.462 [0.333, 0.594] | 1.736 [1.085, 2.399] | 0.422 [0.290, 0.567] |
| without_inferred-lag10-ratio10 | loco | always_observe_30 | 45 | 9.056 [1.969, 17.500] | 3 [0.023, 0.179] | 4 [0.035, 0.207] | 0 | 1350.0 | 0.145 [0.076, 0.207] | 0.475 [0.303, 0.639] | 0.844 [0.712, 0.923] |
| without_inferred-lag10-ratio10 | loco | muse_only | 0 | not run | — | — | — | — | — | — | — |
| without_inferred-lag10-ratio10 | loco | agent_threshold | 45 | 4.222 [2.346, 5.874] | 0 [0.000, 0.079] | 19 [0.290, 0.567] | 0 | 0.0 | 0.236 [0.125, 0.340] | 0.689 [0.421, 0.927] | 0.578 [0.433, 0.710] |
| without_inferred-lag10-ratio10 | loco | agent_logistic | 45 | 3.333 [1.583, 5.294] | 0 [0.000, 0.079] | 15 [0.214, 0.479] | 0 | 0.0 | 0.156 [0.102, 0.206] | 0.462 [0.333, 0.586] | 0.667 [0.521, 0.786] |
| without_inferred-lag10-ratio10 | lofo | always_escalate | 45 | 4.222 [2.500, 5.894] | 0 [0.000, 0.079] | 19 [0.290, 0.567] | 0 | 0.0 | 0.422 [0.250, 0.589] | 2.917 [1.728, 4.072] | 0.578 [0.433, 0.710] |
| without_inferred-lag10-ratio10 | lofo | paper_table3 | 45 | 9.333 [2.411, 14.397] | 4 [0.035, 0.207] | 2 [0.012, 0.148] | 0 | 0.0 | 0.133 [0.043, 0.225] | 0.922 [0.297, 1.558] | 0.867 [0.738, 0.937] |
| without_inferred-lag10-ratio10 | lofo | delay_lookup_refit | 45 | 57.778 [41.057, 75.000] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.326 [0.258, 0.416] | 0.863 [0.712, 1.063] | 0.422 [0.290, 0.567] |
| without_inferred-lag10-ratio10 | lofo | fixed_temperature | 45 | 26.222 [15.221, 39.773] | 11 [0.142, 0.387] | 8 [0.093, 0.313] | 0 | 0.0 | 0.355 [0.294, 0.435] | 0.967 [0.802, 1.167] | 0.578 [0.433, 0.710] |
| without_inferred-lag10-ratio10 | lofo | temperature_refit | 45 | 13.111 [4.934, 26.944] | 4 [0.035, 0.207] | 19 [0.290, 0.567] | 0 | 0.0 | 0.359 [0.242, 0.464] | 0.974 [0.696, 1.219] | 0.489 [0.350, 0.630] |
| without_inferred-lag10-ratio10 | lofo | physics_only | 45 | 57.778 [41.057, 75.000] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.578 [0.411, 0.750] | 3.992 [2.837, 5.181] | 0.422 [0.290, 0.567] |
| without_inferred-lag10-ratio10 | lofo | two_node_physics | 45 | 57.778 [41.057, 75.000] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.462 [0.345, 0.595] | 1.736 [1.213, 2.367] | 0.422 [0.290, 0.567] |
| without_inferred-lag10-ratio10 | lofo | always_observe_30 | 45 | 21.722 [10.417, 34.057] | 8 [0.093, 0.313] | 11 [0.142, 0.387] | 0 | 1350.0 | 0.302 [0.259, 0.362] | 0.840 [0.742, 0.976] | 0.578 [0.433, 0.710] |
| without_inferred-lag10-ratio10 | lofo | muse_only | 0 | not run | — | — | — | — | — | — | — |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | 45 | 18.222 [7.937, 29.614] | 7 [0.077, 0.288] | 12 [0.160, 0.410] | 0 | 0.0 | 0.298 [0.243, 0.350] | 0.864 [0.736, 1.002] | 0.578 [0.433, 0.710] |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | 45 | 6.222 [3.432, 10.139] | 1 [0.004, 0.116] | 18 [0.270, 0.545] | 0 | 0.0 | 0.389 [0.285, 0.507] | 1.847 [0.987, 2.718] | 0.578 [0.433, 0.710] |
| without_inferred-lag10-ratio10 | prequential | always_escalate | 45 | 4.222 [2.500, 5.894] | 0 [0.000, 0.079] | 19 [0.290, 0.567] | 0 | 0.0 | 0.422 [0.250, 0.589] | 2.917 [1.728, 4.072] | 0.578 [0.433, 0.710] |
| without_inferred-lag10-ratio10 | prequential | paper_table3 | 45 | 9.333 [2.411, 14.397] | 4 [0.035, 0.207] | 2 [0.012, 0.148] | 0 | 0.0 | 0.133 [0.043, 0.225] | 0.922 [0.297, 1.558] | 0.867 [0.738, 0.937] |
| without_inferred-lag10-ratio10 | prequential | delay_lookup_refit | 45 | 45.333 [32.000, 59.972] | 20 [0.309, 0.588] | 4 [0.035, 0.207] | 0 | 0.0 | 0.344 [0.262, 0.422] | 1.008 [0.730, 1.311] | 0.467 [0.329, 0.609] |
| without_inferred-lag10-ratio10 | prequential | fixed_temperature | 45 | 26.444 [15.221, 40.227] | 11 [0.142, 0.387] | 9 [0.109, 0.338] | 0 | 0.0 | 0.284 [0.217, 0.345] | 0.885 [0.631, 1.188] | 0.556 [0.412, 0.691] |
| without_inferred-lag10-ratio10 | prequential | temperature_refit | 45 | 15.111 [6.136, 25.290] | 6 [0.063, 0.262] | 8 [0.093, 0.313] | 0 | 0.0 | 0.259 [0.148, 0.348] | 0.862 [0.504, 1.237] | 0.689 [0.543, 0.805] |
| without_inferred-lag10-ratio10 | prequential | physics_only | 45 | 57.778 [41.057, 75.000] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.578 [0.411, 0.750] | 3.992 [2.837, 5.181] | 0.422 [0.290, 0.567] |
| without_inferred-lag10-ratio10 | prequential | two_node_physics | 45 | 57.778 [41.057, 75.000] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.462 [0.345, 0.595] | 1.736 [1.213, 2.367] | 0.422 [0.290, 0.567] |
| without_inferred-lag10-ratio10 | prequential | always_observe_30 | 45 | 17.722 [8.964, 27.803] | 7 [0.077, 0.288] | 3 [0.023, 0.179] | 0 | 1350.0 | 0.199 [0.122, 0.269] | 0.708 [0.439, 0.996] | 0.778 [0.637, 0.875] |
| without_inferred-lag10-ratio10 | prequential | muse_only | 0 | not run | — | — | — | — | — | — | — |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | 45 | 10.889 [5.988, 16.220] | 4 [0.035, 0.207] | 9 [0.109, 0.338] | 0 | 0.0 | 0.257 [0.171, 0.352] | 1.034 [0.633, 1.434] | 0.711 [0.566, 0.823] |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | 45 | 8.667 [1.591, 16.915] | 3 [0.023, 0.179] | 9 [0.109, 0.338] | 0 | 0.0 | 0.252 [0.174, 0.341] | 0.899 [0.521, 1.331] | 0.733 [0.590, 0.840] |
| with_inferred-lag7-ratio10 | loco | always_escalate | 50 | 3.800 [2.192, 5.101] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.219, 0.510] | 2.626 [1.515, 3.524] | 0.620 [0.482, 0.741] |
| with_inferred-lag7-ratio10 | loco | paper_table3 | 50 | 10.400 [3.976, 17.843] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.043, 0.226] | 0.968 [0.297, 1.565] | 0.860 [0.738, 0.930] |
| with_inferred-lag7-ratio10 | loco | delay_lookup_refit | 50 | 14.200 [1.502, 29.798] | 6 [0.056, 0.238] | 11 [0.128, 0.352] | 0 | 0.0 | 0.277 [0.163, 0.401] | 0.818 [0.527, 1.142] | 0.660 [0.522, 0.776] |
| with_inferred-lag7-ratio10 | loco | fixed_temperature | 50 | 27.600 [14.136, 43.883] | 13 [0.159, 0.396] | 8 [0.083, 0.285] | 0 | 0.0 | 0.257 [0.214, 0.305] | 0.714 [0.618, 0.823] | 0.580 [0.442, 0.706] |
| with_inferred-lag7-ratio10 | loco | temperature_refit | 50 | 8.400 [2.280, 15.691] | 3 [0.021, 0.162] | 12 [0.143, 0.374] | 0 | 0.0 | 0.229 [0.154, 0.310] | 0.668 [0.495, 0.862] | 0.700 [0.562, 0.809] |
| with_inferred-lag7-ratio10 | loco | physics_only | 50 | 62.000 [48.995, 78.080] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.490, 0.781] | 4.283 [3.385, 5.394] | 0.380 [0.259, 0.518] |
| with_inferred-lag7-ratio10 | loco | two_node_physics | 50 | 62.000 [48.995, 78.080] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.605] | 1.729 [1.159, 2.378] | 0.380 [0.259, 0.518] |
| with_inferred-lag7-ratio10 | loco | always_observe_30 | 50 | 14.261 [6.363, 21.967] | 6 [0.056, 0.238] | 4 [0.032, 0.188] | 0 | 1461.0 | 0.125 [0.065, 0.203] | 0.419 [0.269, 0.616] | 0.800 [0.670, 0.888] |
| with_inferred-lag7-ratio10 | loco | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag7-ratio10 | loco | agent_threshold | 50 | 3.800 [2.192, 5.101] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.232 [0.148, 0.308] | 0.690 [0.489, 0.869] | 0.620 [0.482, 0.741] |
| with_inferred-lag7-ratio10 | loco | agent_logistic | 50 | 3.880 [2.137, 5.571] | 0 [0.000, 0.071] | 17 [0.224, 0.478] | 0 | 480.0 | 0.168 [0.076, 0.275] | 0.517 [0.274, 0.808] | 0.660 [0.522, 0.776] |
| with_inferred-lag7-ratio10 | lofo | always_escalate | 50 | 3.800 [2.298, 5.205] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.230, 0.521] | 2.626 [1.588, 3.596] | 0.620 [0.482, 0.741] |
| with_inferred-lag7-ratio10 | lofo | paper_table3 | 50 | 10.400 [3.817, 17.307] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.041, 0.238] | 0.968 [0.283, 1.646] | 0.860 [0.738, 0.930] |
| with_inferred-lag7-ratio10 | lofo | delay_lookup_refit | 50 | 62.000 [47.948, 77.023] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.310 [0.260, 0.376] | 0.821 [0.716, 0.964] | 0.380 [0.259, 0.518] |
| with_inferred-lag7-ratio10 | lofo | fixed_temperature | 50 | 27.600 [15.179, 40.899] | 13 [0.159, 0.396] | 8 [0.083, 0.285] | 0 | 0.0 | 0.333 [0.261, 0.404] | 0.921 [0.743, 1.101] | 0.580 [0.442, 0.706] |
| with_inferred-lag7-ratio10 | lofo | temperature_refit | 50 | 17.800 [5.208, 35.482] | 7 [0.070, 0.262] | 19 [0.259, 0.518] | 0 | 0.0 | 0.365 [0.244, 0.461] | 1.000 [0.707, 1.244] | 0.480 [0.348, 0.615] |
| with_inferred-lag7-ratio10 | lofo | physics_only | 50 | 62.000 [47.948, 77.023] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.479, 0.770] | 4.283 [3.313, 5.321] | 0.380 [0.259, 0.518] |
| with_inferred-lag7-ratio10 | lofo | two_node_physics | 50 | 62.000 [47.948, 77.023] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.599] | 1.729 [1.272, 2.325] | 0.380 [0.259, 0.518] |
| with_inferred-lag7-ratio10 | lofo | always_observe_30 | 50 | 24.861 [17.096, 33.894] | 11 [0.128, 0.352] | 7 [0.070, 0.262] | 0 | 1461.0 | 0.261 [0.197, 0.320] | 0.746 [0.598, 0.887] | 0.640 [0.501, 0.759] |
| with_inferred-lag7-ratio10 | lofo | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | 50 | 18.400 [8.194, 31.117] | 8 [0.083, 0.285] | 12 [0.143, 0.374] | 0 | 0.0 | 0.285 [0.229, 0.336] | 0.840 [0.678, 0.999] | 0.600 [0.462, 0.724] |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | 50 | 3.600 [2.106, 5.000] | 0 [0.000, 0.071] | 18 [0.241, 0.499] | 0 | 0.0 | 0.321 [0.211, 0.408] | 1.591 [0.804, 2.269] | 0.640 [0.501, 0.759] |
| with_inferred-lag7-ratio10 | prequential | always_escalate | 50 | 3.800 [2.298, 5.205] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.230, 0.521] | 2.626 [1.588, 3.596] | 0.620 [0.482, 0.741] |
| with_inferred-lag7-ratio10 | prequential | paper_table3 | 50 | 10.400 [3.817, 17.307] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.041, 0.238] | 0.968 [0.283, 1.646] | 0.860 [0.738, 0.930] |
| with_inferred-lag7-ratio10 | prequential | delay_lookup_refit | 50 | 42.800 [33.623, 53.791] | 21 [0.294, 0.558] | 4 [0.032, 0.188] | 0 | 0.0 | 0.315 [0.265, 0.359] | 0.933 [0.736, 1.183] | 0.500 [0.366, 0.634] |
| with_inferred-lag7-ratio10 | prequential | fixed_temperature | 50 | 27.800 [15.179, 41.188] | 13 [0.159, 0.396] | 9 [0.098, 0.308] | 0 | 0.0 | 0.274 [0.197, 0.357] | 0.849 [0.578, 1.168] | 0.560 [0.423, 0.688] |
| with_inferred-lag7-ratio10 | prequential | temperature_refit | 50 | 17.600 [8.424, 26.851] | 8 [0.083, 0.285] | 8 [0.083, 0.285] | 0 | 0.0 | 0.265 [0.159, 0.372] | 0.869 [0.508, 1.246] | 0.680 [0.542, 0.792] |
| with_inferred-lag7-ratio10 | prequential | physics_only | 50 | 62.000 [47.948, 77.023] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.479, 0.770] | 4.283 [3.313, 5.321] | 0.380 [0.259, 0.518] |
| with_inferred-lag7-ratio10 | prequential | two_node_physics | 50 | 62.000 [47.948, 77.023] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.599] | 1.729 [1.272, 2.325] | 0.380 [0.259, 0.518] |
| with_inferred-lag7-ratio10 | prequential | always_observe_30 | 50 | 19.061 [9.982, 28.468] | 8 [0.083, 0.285] | 8 [0.083, 0.285] | 0 | 1461.0 | 0.235 [0.132, 0.333] | 0.787 [0.463, 1.125] | 0.680 [0.542, 0.792] |
| with_inferred-lag7-ratio10 | prequential | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | 50 | 11.800 [5.694, 18.476] | 5 [0.043, 0.214] | 9 [0.098, 0.308] | 0 | 0.0 | 0.259 [0.151, 0.392] | 1.029 [0.512, 1.646] | 0.720 [0.583, 0.825] |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | 50 | 4.010 [0.685, 7.795] | 1 [0.004, 0.105] | 9 [0.098, 0.308] | 0 | 210.0 | 0.195 [0.086, 0.313] | 0.732 [0.280, 1.260] | 0.800 [0.670, 0.888] |
| without_inferred-lag7-ratio10 | loco | always_escalate | 45 | 4.222 [2.346, 5.874] | 0 [0.000, 0.079] | 19 [0.290, 0.567] | 0 | 0.0 | 0.422 [0.235, 0.587] | 2.917 [1.622, 4.058] | 0.578 [0.433, 0.710] |
| without_inferred-lag7-ratio10 | loco | paper_table3 | 45 | 9.333 [3.158, 14.593] | 4 [0.035, 0.207] | 2 [0.012, 0.148] | 0 | 0.0 | 0.133 [0.044, 0.220] | 0.922 [0.304, 1.523] | 0.867 [0.738, 0.937] |
| without_inferred-lag7-ratio10 | loco | delay_lookup_refit | 45 | 15.778 [2.819, 28.012] | 6 [0.063, 0.262] | 11 [0.142, 0.387] | 0 | 0.0 | 0.285 [0.188, 0.398] | 0.828 [0.561, 1.122] | 0.622 [0.476, 0.749] |
| without_inferred-lag7-ratio10 | loco | fixed_temperature | 45 | 26.222 [13.191, 41.860] | 11 [0.142, 0.387] | 8 [0.093, 0.313] | 0 | 0.0 | 0.270 [0.236, 0.309] | 0.737 [0.664, 0.824] | 0.578 [0.433, 0.710] |
| without_inferred-lag7-ratio10 | loco | temperature_refit | 45 | 11.556 [4.880, 17.826] | 4 [0.035, 0.207] | 12 [0.160, 0.410] | 0 | 0.0 | 0.255 [0.191, 0.322] | 0.719 [0.577, 0.875] | 0.644 [0.498, 0.768] |
| without_inferred-lag7-ratio10 | loco | physics_only | 45 | 57.778 [41.255, 76.537] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.578 [0.413, 0.765] | 3.992 [2.850, 5.287] | 0.422 [0.290, 0.567] |
| without_inferred-lag7-ratio10 | loco | two_node_physics | 45 | 57.778 [41.255, 76.537] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.462 [0.333, 0.594] | 1.736 [1.085, 2.399] | 0.422 [0.290, 0.567] |
| without_inferred-lag7-ratio10 | loco | always_observe_30 | 45 | 9.056 [2.053, 17.278] | 3 [0.023, 0.179] | 4 [0.035, 0.207] | 0 | 1350.0 | 0.137 [0.071, 0.221] | 0.452 [0.287, 0.661] | 0.844 [0.712, 0.923] |
| without_inferred-lag7-ratio10 | loco | muse_only | 0 | not run | — | — | — | — | — | — | — |
| without_inferred-lag7-ratio10 | loco | agent_threshold | 45 | 4.222 [2.346, 5.874] | 0 [0.000, 0.079] | 19 [0.290, 0.567] | 0 | 0.0 | 0.185 [0.084, 0.293] | 0.565 [0.328, 0.819] | 0.578 [0.433, 0.710] |
| without_inferred-lag7-ratio10 | loco | agent_logistic | 45 | 3.333 [1.583, 5.294] | 0 [0.000, 0.079] | 15 [0.214, 0.479] | 0 | 0.0 | 0.149 [0.090, 0.199] | 0.449 [0.311, 0.571] | 0.667 [0.521, 0.786] |
| without_inferred-lag7-ratio10 | lofo | always_escalate | 45 | 4.222 [2.500, 5.894] | 0 [0.000, 0.079] | 19 [0.290, 0.567] | 0 | 0.0 | 0.422 [0.250, 0.589] | 2.917 [1.728, 4.072] | 0.578 [0.433, 0.710] |
| without_inferred-lag7-ratio10 | lofo | paper_table3 | 45 | 9.333 [2.411, 14.397] | 4 [0.035, 0.207] | 2 [0.012, 0.148] | 0 | 0.0 | 0.133 [0.043, 0.225] | 0.922 [0.297, 1.558] | 0.867 [0.738, 0.937] |
| without_inferred-lag7-ratio10 | lofo | delay_lookup_refit | 45 | 57.778 [41.057, 75.000] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.326 [0.258, 0.416] | 0.863 [0.712, 1.063] | 0.422 [0.290, 0.567] |
| without_inferred-lag7-ratio10 | lofo | fixed_temperature | 45 | 26.222 [15.221, 39.773] | 11 [0.142, 0.387] | 8 [0.093, 0.313] | 0 | 0.0 | 0.355 [0.294, 0.435] | 0.967 [0.802, 1.167] | 0.578 [0.433, 0.710] |
| without_inferred-lag7-ratio10 | lofo | temperature_refit | 45 | 13.111 [4.934, 26.944] | 4 [0.035, 0.207] | 19 [0.290, 0.567] | 0 | 0.0 | 0.359 [0.242, 0.464] | 0.974 [0.696, 1.219] | 0.489 [0.350, 0.630] |
| without_inferred-lag7-ratio10 | lofo | physics_only | 45 | 57.778 [41.057, 75.000] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.578 [0.411, 0.750] | 3.992 [2.837, 5.181] | 0.422 [0.290, 0.567] |
| without_inferred-lag7-ratio10 | lofo | two_node_physics | 45 | 57.778 [41.057, 75.000] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.462 [0.345, 0.595] | 1.736 [1.213, 2.367] | 0.422 [0.290, 0.567] |
| without_inferred-lag7-ratio10 | lofo | always_observe_30 | 45 | 20.833 [9.036, 33.744] | 8 [0.093, 0.313] | 7 [0.077, 0.288] | 0 | 1350.0 | 0.263 [0.193, 0.329] | 0.756 [0.593, 0.913] | 0.667 [0.521, 0.786] |
| without_inferred-lag7-ratio10 | lofo | muse_only | 0 | not run | — | — | — | — | — | — | — |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | 45 | 20.444 [9.645, 33.035] | 8 [0.093, 0.313] | 12 [0.160, 0.410] | 0 | 0.0 | 0.313 [0.254, 0.369] | 0.912 [0.763, 1.069] | 0.556 [0.412, 0.691] |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | 45 | 6.222 [3.432, 10.139] | 1 [0.004, 0.116] | 18 [0.270, 0.545] | 0 | 0.0 | 0.404 [0.307, 0.517] | 1.909 [1.078, 2.762] | 0.578 [0.433, 0.710] |
| without_inferred-lag7-ratio10 | prequential | always_escalate | 45 | 4.222 [2.500, 5.894] | 0 [0.000, 0.079] | 19 [0.290, 0.567] | 0 | 0.0 | 0.422 [0.250, 0.589] | 2.917 [1.728, 4.072] | 0.578 [0.433, 0.710] |
| without_inferred-lag7-ratio10 | prequential | paper_table3 | 45 | 9.333 [2.411, 14.397] | 4 [0.035, 0.207] | 2 [0.012, 0.148] | 0 | 0.0 | 0.133 [0.043, 0.225] | 0.922 [0.297, 1.558] | 0.867 [0.738, 0.937] |
| without_inferred-lag7-ratio10 | prequential | delay_lookup_refit | 45 | 45.333 [32.000, 59.972] | 20 [0.309, 0.588] | 4 [0.035, 0.207] | 0 | 0.0 | 0.344 [0.262, 0.422] | 1.008 [0.730, 1.311] | 0.467 [0.329, 0.609] |
| without_inferred-lag7-ratio10 | prequential | fixed_temperature | 45 | 26.444 [15.221, 40.227] | 11 [0.142, 0.387] | 9 [0.109, 0.338] | 0 | 0.0 | 0.284 [0.217, 0.345] | 0.885 [0.631, 1.188] | 0.556 [0.412, 0.691] |
| without_inferred-lag7-ratio10 | prequential | temperature_refit | 45 | 15.111 [6.136, 25.290] | 6 [0.063, 0.262] | 8 [0.093, 0.313] | 0 | 0.0 | 0.259 [0.148, 0.348] | 0.862 [0.504, 1.237] | 0.689 [0.543, 0.805] |
| without_inferred-lag7-ratio10 | prequential | physics_only | 45 | 57.778 [41.057, 75.000] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.578 [0.411, 0.750] | 3.992 [2.837, 5.181] | 0.422 [0.290, 0.567] |
| without_inferred-lag7-ratio10 | prequential | two_node_physics | 45 | 57.778 [41.057, 75.000] | 26 [0.433, 0.710] | 0 [0.000, 0.079] | 0 | 0.0 | 0.462 [0.345, 0.595] | 1.736 [1.213, 2.367] | 0.422 [0.290, 0.567] |
| without_inferred-lag7-ratio10 | prequential | always_observe_30 | 45 | 17.722 [8.964, 27.803] | 7 [0.077, 0.288] | 3 [0.023, 0.179] | 0 | 1350.0 | 0.198 [0.121, 0.270] | 0.707 [0.430, 0.992] | 0.778 [0.637, 0.875] |
| without_inferred-lag7-ratio10 | prequential | muse_only | 0 | not run | — | — | — | — | — | — | — |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | 45 | 10.889 [5.988, 16.220] | 4 [0.035, 0.207] | 9 [0.109, 0.338] | 0 | 0.0 | 0.232 [0.123, 0.336] | 0.975 [0.574, 1.401] | 0.711 [0.566, 0.823] |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | 45 | 8.889 [1.591, 17.130] | 3 [0.023, 0.179] | 10 [0.125, 0.363] | 0 | 0.0 | 0.247 [0.162, 0.341] | 0.897 [0.499, 1.330] | 0.711 [0.566, 0.823] |
| with_inferred-lag10-ratio5 | loco | always_escalate | 50 | 3.800 [2.192, 5.101] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.219, 0.510] | 2.626 [1.515, 3.524] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio5 | loco | paper_table3 | 50 | 5.400 [2.028, 9.031] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.043, 0.226] | 0.968 [0.297, 1.565] | 0.860 [0.738, 0.930] |
| with_inferred-lag10-ratio5 | loco | delay_lookup_refit | 50 | 8.200 [1.502, 15.691] | 6 [0.056, 0.238] | 11 [0.128, 0.352] | 0 | 0.0 | 0.277 [0.163, 0.401] | 0.818 [0.527, 1.142] | 0.660 [0.522, 0.776] |
| with_inferred-lag10-ratio5 | loco | fixed_temperature | 50 | 14.600 [8.474, 22.228] | 13 [0.159, 0.396] | 8 [0.083, 0.285] | 0 | 0.0 | 0.257 [0.214, 0.305] | 0.714 [0.618, 0.823] | 0.580 [0.442, 0.706] |
| with_inferred-lag10-ratio5 | loco | temperature_refit | 50 | 5.400 [2.252, 8.530] | 3 [0.021, 0.162] | 12 [0.143, 0.374] | 0 | 0.0 | 0.229 [0.154, 0.310] | 0.668 [0.495, 0.862] | 0.700 [0.562, 0.809] |
| with_inferred-lag10-ratio5 | loco | physics_only | 50 | 31.000 [24.497, 39.040] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.490, 0.781] | 4.283 [3.385, 5.394] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio5 | loco | two_node_physics | 50 | 31.000 [24.497, 39.040] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.605] | 1.729 [1.159, 2.378] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio5 | loco | always_observe_30 | 50 | 8.261 [4.117, 12.123] | 6 [0.056, 0.238] | 4 [0.032, 0.188] | 0 | 1461.0 | 0.133 [0.071, 0.189] | 0.443 [0.287, 0.595] | 0.800 [0.670, 0.888] |
| with_inferred-lag10-ratio5 | loco | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag10-ratio5 | loco | agent_threshold | 50 | 6.000 [1.234, 13.195] | 4 [0.032, 0.188] | 10 [0.112, 0.330] | 0 | 0.0 | 0.217 [0.105, 0.322] | 0.657 [0.375, 0.929] | 0.720 [0.583, 0.825] |
| with_inferred-lag10-ratio5 | loco | agent_logistic | 50 | 2.800 [1.468, 4.138] | 0 [0.000, 0.071] | 14 [0.175, 0.417] | 0 | 0.0 | 0.145 [0.099, 0.193] | 0.439 [0.326, 0.555] | 0.720 [0.583, 0.825] |
| with_inferred-lag10-ratio5 | lofo | always_escalate | 50 | 3.800 [2.298, 5.205] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.230, 0.521] | 2.626 [1.588, 3.596] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio5 | lofo | paper_table3 | 50 | 5.400 [1.943, 8.946] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.041, 0.238] | 0.968 [0.283, 1.646] | 0.860 [0.738, 0.930] |
| with_inferred-lag10-ratio5 | lofo | delay_lookup_refit | 50 | 31.000 [23.974, 38.512] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.310 [0.260, 0.376] | 0.821 [0.716, 0.964] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio5 | lofo | fixed_temperature | 50 | 14.600 [9.027, 21.029] | 13 [0.159, 0.396] | 8 [0.083, 0.285] | 0 | 0.0 | 0.333 [0.261, 0.404] | 0.921 [0.743, 1.101] | 0.580 [0.442, 0.706] |
| with_inferred-lag10-ratio5 | lofo | temperature_refit | 50 | 10.800 [4.468, 19.499] | 7 [0.070, 0.262] | 19 [0.259, 0.518] | 0 | 0.0 | 0.365 [0.244, 0.461] | 1.000 [0.707, 1.244] | 0.480 [0.348, 0.615] |
| with_inferred-lag10-ratio5 | lofo | physics_only | 50 | 31.000 [23.974, 38.512] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.479, 0.770] | 4.283 [3.313, 5.321] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio5 | lofo | two_node_physics | 50 | 31.000 [23.974, 38.512] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.599] | 1.729 [1.272, 2.325] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio5 | lofo | always_observe_30 | 50 | 14.661 [11.567, 18.166] | 11 [0.128, 0.352] | 11 [0.128, 0.352] | 0 | 1461.0 | 0.297 [0.260, 0.350] | 0.823 [0.744, 0.940] | 0.560 [0.423, 0.688] |
| with_inferred-lag10-ratio5 | lofo | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | 50 | 9.400 [5.013, 14.816] | 7 [0.070, 0.262] | 12 [0.143, 0.374] | 0 | 0.0 | 0.273 [0.223, 0.327] | 0.800 [0.669, 0.952] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | 50 | 3.600 [2.106, 5.000] | 0 [0.000, 0.071] | 18 [0.241, 0.499] | 0 | 0.0 | 0.329 [0.234, 0.418] | 1.600 [0.847, 2.279] | 0.640 [0.501, 0.759] |
| with_inferred-lag10-ratio5 | prequential | always_escalate | 50 | 3.800 [2.298, 5.205] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.230, 0.521] | 2.626 [1.588, 3.596] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio5 | prequential | paper_table3 | 50 | 5.400 [1.943, 8.946] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.041, 0.238] | 0.968 [0.283, 1.646] | 0.860 [0.738, 0.930] |
| with_inferred-lag10-ratio5 | prequential | delay_lookup_refit | 50 | 21.800 [17.370, 27.247] | 21 [0.294, 0.558] | 4 [0.032, 0.188] | 0 | 0.0 | 0.315 [0.265, 0.359] | 0.933 [0.736, 1.183] | 0.500 [0.366, 0.634] |
| with_inferred-lag10-ratio5 | prequential | fixed_temperature | 50 | 14.800 [9.027, 21.321] | 13 [0.159, 0.396] | 9 [0.098, 0.308] | 0 | 0.0 | 0.274 [0.197, 0.357] | 0.849 [0.578, 1.168] | 0.560 [0.423, 0.688] |
| with_inferred-lag10-ratio5 | prequential | temperature_refit | 50 | 8.600 [3.558, 13.731] | 7 [0.070, 0.262] | 8 [0.083, 0.285] | 0 | 0.0 | 0.251 [0.131, 0.372] | 0.835 [0.447, 1.243] | 0.700 [0.562, 0.809] |
| with_inferred-lag10-ratio5 | prequential | physics_only | 50 | 31.000 [23.974, 38.512] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.479, 0.770] | 4.283 [3.313, 5.321] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio5 | prequential | two_node_physics | 50 | 31.000 [23.974, 38.512] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.599] | 1.729 [1.272, 2.325] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio5 | prequential | always_observe_30 | 50 | 11.061 [6.965, 15.444] | 8 [0.083, 0.285] | 8 [0.083, 0.285] | 0 | 1461.0 | 0.235 [0.132, 0.333] | 0.787 [0.463, 1.125] | 0.680 [0.542, 0.792] |
| with_inferred-lag10-ratio5 | prequential | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | 50 | 11.000 [5.626, 16.073] | 9 [0.098, 0.308] | 10 [0.112, 0.330] | 0 | 0.0 | 0.311 [0.205, 0.409] | 1.142 [0.678, 1.682] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | 50 | 4.800 [2.207, 6.933] | 3 [0.021, 0.162] | 9 [0.098, 0.308] | 0 | 0.0 | 0.204 [0.124, 0.293] | 0.835 [0.418, 1.377] | 0.760 [0.626, 0.857] |
| with_inferred-lag10-ratio20 | loco | always_escalate | 50 | 3.800 [2.192, 5.101] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.219, 0.510] | 2.626 [1.515, 3.524] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio20 | loco | paper_table3 | 50 | 20.400 [7.874, 35.490] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.043, 0.226] | 0.968 [0.297, 1.565] | 0.860 [0.738, 0.930] |
| with_inferred-lag10-ratio20 | loco | delay_lookup_refit | 50 | 26.200 [1.502, 57.210] | 6 [0.056, 0.238] | 11 [0.128, 0.352] | 0 | 0.0 | 0.277 [0.163, 0.401] | 0.818 [0.527, 1.142] | 0.660 [0.522, 0.776] |
| with_inferred-lag10-ratio20 | loco | fixed_temperature | 50 | 53.600 [25.882, 87.128] | 13 [0.159, 0.396] | 8 [0.083, 0.285] | 0 | 0.0 | 0.257 [0.214, 0.305] | 0.714 [0.618, 0.823] | 0.580 [0.442, 0.706] |
| with_inferred-lag10-ratio20 | loco | temperature_refit | 50 | 14.400 [2.280, 30.125] | 3 [0.021, 0.162] | 12 [0.143, 0.374] | 0 | 0.0 | 0.229 [0.154, 0.310] | 0.668 [0.495, 0.862] | 0.700 [0.562, 0.809] |
| with_inferred-lag10-ratio20 | loco | physics_only | 50 | 124.000 [97.989, 156.159] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.490, 0.781] | 4.283 [3.385, 5.394] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio20 | loco | two_node_physics | 50 | 124.000 [97.989, 156.159] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.605] | 1.729 [1.159, 2.378] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio20 | loco | always_observe_30 | 50 | 26.261 [10.820, 42.365] | 6 [0.056, 0.238] | 4 [0.032, 0.188] | 0 | 1461.0 | 0.133 [0.071, 0.189] | 0.443 [0.287, 0.595] | 0.800 [0.670, 0.888] |
| with_inferred-lag10-ratio20 | loco | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag10-ratio20 | loco | agent_threshold | 50 | 3.800 [2.192, 5.101] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.183 [0.105, 0.271] | 0.566 [0.375, 0.784] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio20 | loco | agent_logistic | 50 | 3.800 [2.192, 5.101] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.148 [0.104, 0.198] | 0.448 [0.335, 0.568] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio20 | lofo | always_escalate | 50 | 3.800 [2.298, 5.205] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.230, 0.521] | 2.626 [1.588, 3.596] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio20 | lofo | paper_table3 | 50 | 20.400 [7.564, 34.269] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.041, 0.238] | 0.968 [0.283, 1.646] | 0.860 [0.738, 0.930] |
| with_inferred-lag10-ratio20 | lofo | delay_lookup_refit | 50 | 124.000 [95.896, 154.046] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.310 [0.260, 0.376] | 0.821 [0.716, 0.964] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio20 | lofo | fixed_temperature | 50 | 53.600 [27.762, 81.009] | 13 [0.159, 0.396] | 8 [0.083, 0.285] | 0 | 0.0 | 0.333 [0.261, 0.404] | 0.921 [0.743, 1.101] | 0.580 [0.442, 0.706] |
| with_inferred-lag10-ratio20 | lofo | temperature_refit | 50 | 31.800 [5.990, 67.795] | 7 [0.070, 0.262] | 19 [0.259, 0.518] | 0 | 0.0 | 0.365 [0.244, 0.461] | 1.000 [0.707, 1.244] | 0.480 [0.348, 0.615] |
| with_inferred-lag10-ratio20 | lofo | physics_only | 50 | 124.000 [95.896, 154.046] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.479, 0.770] | 4.283 [3.313, 5.321] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio20 | lofo | two_node_physics | 50 | 124.000 [95.896, 154.046] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.599] | 1.729 [1.272, 2.325] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio20 | lofo | always_observe_30 | 50 | 47.661 [32.490, 66.582] | 11 [0.128, 0.352] | 11 [0.128, 0.352] | 0 | 1461.0 | 0.297 [0.260, 0.350] | 0.823 [0.744, 0.940] | 0.560 [0.423, 0.688] |
| with_inferred-lag10-ratio20 | lofo | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | 50 | 3.800 [2.298, 5.205] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.273 [0.223, 0.327] | 0.800 [0.669, 0.952] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | 50 | 62.400 [21.346, 120.385] | 15 [0.191, 0.438] | 12 [0.143, 0.374] | 0 | 0.0 | 0.534 [0.375, 0.723] | 2.918 [2.000, 4.047] | 0.460 [0.330, 0.596] |
| with_inferred-lag10-ratio20 | prequential | always_escalate | 50 | 3.800 [2.298, 5.205] | 0 [0.000, 0.071] | 19 [0.259, 0.518] | 0 | 0.0 | 0.380 [0.230, 0.521] | 2.626 [1.588, 3.596] | 0.620 [0.482, 0.741] |
| with_inferred-lag10-ratio20 | prequential | paper_table3 | 50 | 20.400 [7.564, 34.269] | 5 [0.043, 0.214] | 2 [0.011, 0.135] | 0 | 0.0 | 0.140 [0.041, 0.238] | 0.968 [0.283, 1.646] | 0.860 [0.738, 0.930] |
| with_inferred-lag10-ratio20 | prequential | delay_lookup_refit | 50 | 84.800 [66.228, 106.888] | 21 [0.294, 0.558] | 4 [0.032, 0.188] | 0 | 0.0 | 0.315 [0.265, 0.359] | 0.933 [0.736, 1.183] | 0.500 [0.366, 0.634] |
| with_inferred-lag10-ratio20 | prequential | fixed_temperature | 50 | 53.800 [27.762, 81.298] | 13 [0.159, 0.396] | 9 [0.098, 0.308] | 0 | 0.0 | 0.274 [0.197, 0.357] | 0.849 [0.578, 1.168] | 0.560 [0.423, 0.688] |
| with_inferred-lag10-ratio20 | prequential | temperature_refit | 50 | 29.600 [9.087, 50.986] | 7 [0.070, 0.262] | 8 [0.083, 0.285] | 0 | 0.0 | 0.251 [0.131, 0.372] | 0.835 [0.447, 1.243] | 0.700 [0.562, 0.809] |
| with_inferred-lag10-ratio20 | prequential | physics_only | 50 | 124.000 [95.896, 154.046] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.620 [0.479, 0.770] | 4.283 [3.313, 5.321] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio20 | prequential | two_node_physics | 50 | 124.000 [95.896, 154.046] | 31 [0.482, 0.741] | 0 [0.000, 0.071] | 0 | 0.0 | 0.480 [0.372, 0.599] | 1.729 [1.272, 2.325] | 0.380 [0.259, 0.518] |
| with_inferred-lag10-ratio20 | prequential | always_observe_30 | 50 | 35.061 [16.687, 54.554] | 8 [0.083, 0.285] | 8 [0.083, 0.285] | 0 | 1461.0 | 0.235 [0.132, 0.333] | 0.787 [0.463, 1.125] | 0.680 [0.542, 0.792] |
| with_inferred-lag10-ratio20 | prequential | muse_only | 0 | not run | — | — | — | — | — | — | — |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | 50 | 10.200 [1.649, 19.573] | 2 [0.011, 0.135] | 11 [0.128, 0.352] | 0 | 0.0 | 0.340 [0.219, 0.461] | 1.213 [0.710, 1.780] | 0.740 [0.604, 0.841] |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | 50 | 14.600 [1.879, 33.393] | 3 [0.021, 0.162] | 13 [0.159, 0.396] | 0 | 0.0 | 0.272 [0.174, 0.375] | 1.005 [0.516, 1.544] | 0.680 [0.542, 0.792] |

Every cost and metric traces to committed decision and reveal ids. Condition scores, exclusions, watch ignitions, intervals and per-trial differences remain in the receipt. Only a registered primary policy_beats claim with a supported gate review receives 'yes' below; every other verdict is descriptive.

Cost assumption for this table: escalating on a trial that ignites costs 0; escalation efficacy was not observed.

| Data, lag, ratio | Split | Agent | Baseline | Agent / baseline mean cost | Registered paired p | Registered Holm p | Family | Lower cost supported? | Secondary two-part | Unsafe McNemar p (descriptive) | Clustered p (descriptive) | Clustered Holm p (descriptive) | Condition blocks |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|---:|---:|---:|
| with_inferred-lag10-ratio10 | loco | agent_threshold | always_escalate | 3.800 / 3.800 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_threshold | paper_table3 | 3.800 / 10.400 | 0.16118 | 1 | 18 | descriptive | descriptive | 0.0625 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_threshold | delay_lookup_refit | 3.800 / 14.200 | 0.03125 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_threshold | fixed_temperature | 3.800 / 27.600 | 0.00027 | 0.00351 | 18 | yes | descriptive | 0.00024414 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_threshold | temperature_refit | 3.800 / 8.400 | 0.25 | 1 | 18 | descriptive | descriptive | 0.25 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_threshold | physics_only | 3.800 / 62.000 | 9.9999e-06 | 0.00018 | 18 | yes | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_threshold | two_node_physics | 3.800 / 62.000 | 9.9999e-06 | 0.00018 | 18 | yes | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_threshold | always_observe_30 | 3.800 / 14.261 | 0.03188 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.078125 | 0.85938 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_logistic | always_escalate | 3.720 / 3.800 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_logistic | paper_table3 | 3.720 / 10.400 | 0.13373 | 1 | 18 | descriptive | descriptive | 0.0625 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_logistic | delay_lookup_refit | 3.720 / 14.200 | 0.03125 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_logistic | fixed_temperature | 3.720 / 27.600 | 0.00021 | 0.00294 | 18 | yes | descriptive | 0.00024414 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_logistic | temperature_refit | 3.720 / 8.400 | 0.25 | 1 | 18 | descriptive | descriptive | 0.25 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_logistic | physics_only | 3.720 / 62.000 | 9.9999e-06 | 0.00018 | 18 | yes | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_logistic | two_node_physics | 3.720 / 62.000 | 9.9999e-06 | 0.00018 | 18 | yes | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | loco | agent_logistic | always_observe_30 | 3.720 / 14.261 | 0.0317 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.0625 | 0.75 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | always_escalate | 16.400 / 3.800 | 0.015625 | 0.15625 | 18 | descriptive | descriptive | 0.015625 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | paper_table3 | 16.400 / 10.400 | 0.2904 | 1 | 18 | descriptive | descriptive | 0.72656 | 0.39844 | 1 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | delay_lookup_refit | 16.400 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.1921e-07 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | fixed_temperature | 16.400 / 27.600 | 0.24684 | 1 | 18 | descriptive | descriptive | 0.26318 | 0.36719 | 1 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | temperature_refit | 16.400 / 17.800 | 0.79644 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | physics_only | 16.400 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.1921e-07 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | two_node_physics | 16.400 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.1921e-07 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | always_observe_30 | 16.400 / 25.661 | 0.03205 | 0.2564 | 18 | descriptive | descriptive | 0.21875 | 0.046875 | 0.46875 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | always_escalate | 3.830 / 3.800 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | paper_table3 | 3.830 / 10.400 | 0.1617 | 1 | 18 | descriptive | descriptive | 0.0625 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | delay_lookup_refit | 3.830 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | fixed_temperature | 3.830 / 27.600 | 0.00027 | 0.00324 | 18 | descriptive | descriptive | 0.00024414 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | temperature_refit | 3.830 / 17.800 | 0.015625 | 0.15625 | 18 | descriptive | descriptive | 0.015625 | 0.25 | 1 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | physics_only | 3.830 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | two_node_physics | 3.830 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | always_observe_30 | 3.830 / 25.661 | 0.00088999 | 0.0097899 | 18 | descriptive | descriptive | 0.00097656 | 0.015625 | 0.23438 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | always_escalate | 6.200 / 3.800 | 0.5 | 1 | 18 | descriptive | descriptive | 0.5 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | paper_table3 | 6.200 / 10.400 | 0.45323 | 1 | 18 | descriptive | descriptive | 0.45312 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | delay_lookup_refit | 6.200 / 42.800 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 3.8147e-06 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | fixed_temperature | 6.200 / 27.800 | 0.0032196 | 0.0315 | 18 | descriptive | descriptive | 0.003418 | 0.0625 | 0.5625 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | temperature_refit | 6.200 / 15.600 | 0.0625 | 0.4375 | 18 | descriptive | descriptive | 0.0625 | 0.25 | 1 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | physics_only | 6.200 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 3.7253e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | two_node_physics | 6.200 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 3.7253e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | always_observe_30 | 6.200 / 19.061 | 0.00080999 | 0.0089099 | 18 | descriptive | descriptive | 0.03125 | 0.015625 | 0.17188 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | always_escalate | 4.490 / 3.800 | 0.99316 | 1 | 18 | descriptive | descriptive | 1 | 0.9375 | 1 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | paper_table3 | 4.490 / 10.400 | 0.21921 | 1 | 18 | descriptive | descriptive | 0.21875 | 0.25 | 1 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | delay_lookup_refit | 4.490 / 42.800 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.9073e-06 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | fixed_temperature | 4.490 / 27.800 | 0.00047302 | 0.0056763 | 18 | descriptive | descriptive | 0.00048828 | 0.03125 | 0.3125 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | temperature_refit | 4.490 / 15.600 | 0.03125 | 0.25 | 18 | descriptive | descriptive | 0.03125 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | physics_only | 4.490 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.8626e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | two_node_physics | 4.490 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.8626e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | always_observe_30 | 4.490 / 19.061 | 0.00315 | 0.0315 | 18 | descriptive | descriptive | 0.015625 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_threshold | always_escalate | 4.222 / 4.222 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_threshold | paper_table3 | 4.222 / 9.333 | 0.28118 | 1 | 18 | descriptive | descriptive | 0.125 | 0.125 | 1 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_threshold | delay_lookup_refit | 4.222 / 15.778 | 0.03125 | 0.36182 | 18 | descriptive | descriptive | 0.03125 | 0.25 | 1 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_threshold | fixed_temperature | 4.222 / 26.222 | 0.00111 | 0.01443 | 18 | descriptive | descriptive | 0.00097656 | 0.03125 | 0.40625 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_threshold | temperature_refit | 4.222 / 11.556 | 0.125 | 1 | 18 | descriptive | descriptive | 0.125 | 0.25 | 1 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_threshold | physics_only | 4.222 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.9802e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_threshold | two_node_physics | 4.222 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.9802e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_threshold | always_observe_30 | 4.222 / 9.056 | 0.2505 | 1 | 18 | descriptive | descriptive | 0.25 | 0.4375 | 1 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_logistic | always_escalate | 3.333 / 4.222 | 0.125 | 1 | 18 | descriptive | descriptive | 1 | 0.25 | 1 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_logistic | paper_table3 | 3.333 / 9.333 | 0.14807 | 1 | 18 | descriptive | descriptive | 0.125 | 0.125 | 1 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_logistic | delay_lookup_refit | 3.333 / 15.778 | 0.030151 | 0.36182 | 18 | descriptive | descriptive | 0.03125 | 0.25 | 1 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_logistic | fixed_temperature | 3.333 / 26.222 | 0.00097466 | 0.013645 | 18 | descriptive | descriptive | 0.00097656 | 0.015625 | 0.21875 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_logistic | temperature_refit | 3.333 / 11.556 | 0.11719 | 1 | 18 | descriptive | descriptive | 0.125 | 0.15625 | 1 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_logistic | physics_only | 3.333 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.9802e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_logistic | two_node_physics | 3.333 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.9802e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | loco | agent_logistic | always_observe_30 | 3.333 / 9.056 | 0.23836 | 1 | 18 | descriptive | descriptive | 0.25 | 0.32812 | 1 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | always_escalate | 18.222 / 4.222 | 0.015625 | 0.15625 | 18 | descriptive | descriptive | 0.015625 | 0.125 | 1 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | paper_table3 | 18.222 / 9.333 | 0.12601 | 1 | 18 | descriptive | descriptive | 0.45312 | 0.17969 | 1 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | delay_lookup_refit | 18.222 / 57.778 | 2e-05 | 0.0003 | 18 | descriptive | descriptive | 3.8147e-06 | 0.03125 | 0.40625 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | fixed_temperature | 18.222 / 26.222 | 0.45125 | 1 | 18 | descriptive | descriptive | 0.48068 | 0.52344 | 1 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | temperature_refit | 18.222 / 13.111 | 0.54883 | 1 | 18 | descriptive | descriptive | 0.54883 | 0.75 | 1 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | physics_only | 18.222 / 57.778 | 2e-05 | 0.0003 | 18 | descriptive | descriptive | 3.8147e-06 | 0.03125 | 0.40625 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | two_node_physics | 18.222 / 57.778 | 2e-05 | 0.0003 | 18 | descriptive | descriptive | 3.8147e-06 | 0.03125 | 0.40625 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | always_observe_30 | 18.222 / 21.722 | 0.25153 | 1 | 18 | descriptive | descriptive | 1 | 0.26562 | 1 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | always_escalate | 6.222 / 4.222 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | paper_table3 | 6.222 / 9.333 | 0.51771 | 1 | 18 | descriptive | descriptive | 0.375 | 0.46875 | 1 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | delay_lookup_refit | 6.222 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 5.9605e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | fixed_temperature | 6.222 / 26.222 | 0.0064499 | 0.077399 | 18 | descriptive | descriptive | 0.0063477 | 0.015625 | 0.23438 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | temperature_refit | 6.222 / 13.111 | 0.21875 | 1 | 18 | descriptive | descriptive | 0.375 | 0.5 | 1 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | physics_only | 6.222 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 5.9605e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | two_node_physics | 6.222 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 5.9605e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | always_observe_30 | 6.222 / 21.722 | 0.01171 | 0.12881 | 18 | descriptive | descriptive | 0.015625 | 0.015625 | 0.23438 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | always_escalate | 10.889 / 4.222 | 0.12549 | 1 | 18 | descriptive | descriptive | 0.125 | 0.125 | 1 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | paper_table3 | 10.889 / 9.333 | 0.69971 | 1 | 18 | descriptive | descriptive | 1 | 0.70312 | 1 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | delay_lookup_refit | 10.889 / 45.333 | 4e-05 | 0.00055999 | 18 | descriptive | descriptive | 3.0518e-05 | 0.015625 | 0.20312 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | fixed_temperature | 10.889 / 26.444 | 0.089069 | 0.97976 | 18 | descriptive | descriptive | 0.11847 | 0.125 | 1 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | temperature_refit | 10.889 / 15.111 | 0.59961 | 1 | 18 | descriptive | descriptive | 0.6875 | 0.59375 | 1 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | physics_only | 10.889 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 4.7684e-07 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | two_node_physics | 10.889 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 4.7684e-07 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | always_observe_30 | 10.889 / 17.722 | 0.20135 | 1 | 18 | descriptive | descriptive | 0.375 | 0.1875 | 1 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | always_escalate | 8.667 / 4.222 | 0.25073 | 1 | 18 | descriptive | descriptive | 0.25 | 0.5 | 1 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | paper_table3 | 8.667 / 9.333 | 0.99893 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | delay_lookup_refit | 8.667 / 45.333 | 4e-05 | 0.00055999 | 18 | descriptive | descriptive | 1.5259e-05 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | fixed_temperature | 8.667 / 26.444 | 0.015381 | 0.18457 | 18 | descriptive | descriptive | 0.021484 | 0.03125 | 0.375 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | temperature_refit | 8.667 / 15.111 | 0.33594 | 1 | 18 | descriptive | descriptive | 0.375 | 0.4375 | 1 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | physics_only | 8.667 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.3842e-07 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | two_node_physics | 8.667 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.3842e-07 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | always_observe_30 | 8.667 / 17.722 | 0.1683 | 1 | 18 | descriptive | descriptive | 0.28906 | 0.15625 | 1 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_threshold | always_escalate | 3.800 / 3.800 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_threshold | paper_table3 | 3.800 / 10.400 | 0.16118 | 1 | 18 | descriptive | descriptive | 0.0625 | 0.125 | 1 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_threshold | delay_lookup_refit | 3.800 / 14.200 | 0.03125 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.5 | 1 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_threshold | fixed_temperature | 3.800 / 27.600 | 0.00027 | 0.00378 | 18 | descriptive | descriptive | 0.00024414 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_threshold | temperature_refit | 3.800 / 8.400 | 0.25 | 1 | 18 | descriptive | descriptive | 0.25 | 0.5 | 1 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_threshold | physics_only | 3.800 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_threshold | two_node_physics | 3.800 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_threshold | always_observe_30 | 3.800 / 14.261 | 0.03188 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.0625 | 0.6875 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_logistic | always_escalate | 3.880 / 3.800 | 0.77637 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_logistic | paper_table3 | 3.880 / 10.400 | 0.14195 | 1 | 18 | descriptive | descriptive | 0.0625 | 0.125 | 1 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_logistic | delay_lookup_refit | 3.880 / 14.200 | 0.03125 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.5 | 1 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_logistic | fixed_temperature | 3.880 / 27.600 | 0.00029 | 0.00378 | 18 | descriptive | descriptive | 0.00024414 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_logistic | temperature_refit | 3.880 / 8.400 | 0.25 | 1 | 18 | descriptive | descriptive | 0.25 | 0.5 | 1 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_logistic | physics_only | 3.880 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_logistic | two_node_physics | 3.880 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | loco | agent_logistic | always_observe_30 | 3.880 / 14.261 | 0.03127 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.039062 | 0.46875 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | always_escalate | 18.400 / 3.800 | 0.0078125 | 0.078125 | 18 | descriptive | descriptive | 0.0078125 | 0.125 | 1 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | paper_table3 | 18.400 / 10.400 | 0.18086 | 1 | 18 | descriptive | descriptive | 0.50781 | 0.30469 | 1 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | delay_lookup_refit | 18.400 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.3842e-07 | 0.03125 | 0.40625 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | fixed_temperature | 18.400 / 27.600 | 0.33627 | 1 | 18 | descriptive | descriptive | 0.35928 | 0.46875 | 1 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | temperature_refit | 18.400 / 17.800 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | physics_only | 18.400 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.3842e-07 | 0.03125 | 0.40625 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | two_node_physics | 18.400 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.3842e-07 | 0.03125 | 0.40625 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | always_observe_30 | 18.400 / 24.861 | 0.1126 | 0.90079 | 18 | descriptive | descriptive | 0.375 | 0.16406 | 1 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | always_escalate | 3.600 / 3.800 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | paper_table3 | 3.600 / 10.400 | 0.13151 | 0.92056 | 18 | descriptive | descriptive | 0.0625 | 0.125 | 1 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | delay_lookup_refit | 3.600 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | fixed_temperature | 3.600 / 27.600 | 0.00018 | 0.00216 | 18 | descriptive | descriptive | 0.00024414 | 0.015625 | 0.23438 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | temperature_refit | 3.600 / 17.800 | 0.0078125 | 0.078125 | 18 | descriptive | descriptive | 0.015625 | 0.125 | 1 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | physics_only | 3.600 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | two_node_physics | 3.600 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | always_observe_30 | 3.600 / 24.861 | 0.00099999 | 0.011 | 18 | descriptive | descriptive | 0.00097656 | 0.015625 | 0.23438 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | always_escalate | 11.800 / 3.800 | 0.062805 | 0.51749 | 18 | descriptive | descriptive | 0.0625 | 0.0625 | 0.625 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | paper_table3 | 11.800 / 10.400 | 0.73724 | 1 | 18 | descriptive | descriptive | 1 | 0.76562 | 1 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | delay_lookup_refit | 11.800 / 42.800 | 8.9999e-05 | 0.00108 | 18 | descriptive | descriptive | 0.00014496 | 0.015625 | 0.1875 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | fixed_temperature | 11.800 / 27.800 | 0.057499 | 0.51749 | 18 | descriptive | descriptive | 0.076813 | 0.0625 | 0.625 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | temperature_refit | 11.800 / 17.600 | 0.44629 | 1 | 18 | descriptive | descriptive | 0.50781 | 0.46875 | 1 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | physics_only | 11.800 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.9802e-08 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | two_node_physics | 11.800 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.9802e-08 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | always_observe_30 | 11.800 / 19.061 | 0.12566 | 0.87961 | 18 | descriptive | descriptive | 0.45312 | 0.32812 | 1 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | always_escalate | 4.010 / 3.800 | 0.99905 | 1 | 18 | descriptive | descriptive | 1 | 0.9375 | 1 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | paper_table3 | 4.010 / 10.400 | 0.21838 | 1 | 18 | descriptive | descriptive | 0.21875 | 0.25 | 1 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | delay_lookup_refit | 4.010 / 42.800 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.9073e-06 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | fixed_temperature | 4.010 / 27.800 | 0.00036621 | 0.0040283 | 18 | descriptive | descriptive | 0.00048828 | 0.03125 | 0.34375 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | temperature_refit | 4.010 / 17.600 | 0.015625 | 0.15625 | 18 | descriptive | descriptive | 0.015625 | 0.0625 | 0.625 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | physics_only | 4.010 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.8626e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | two_node_physics | 4.010 / 62.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.8626e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | always_observe_30 | 4.010 / 19.061 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 0.015625 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_threshold | always_escalate | 4.222 / 4.222 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_threshold | paper_table3 | 4.222 / 9.333 | 0.28118 | 1 | 18 | descriptive | descriptive | 0.125 | 0.125 | 1 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_threshold | delay_lookup_refit | 4.222 / 15.778 | 0.03125 | 0.36182 | 18 | descriptive | descriptive | 0.03125 | 0.25 | 1 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_threshold | fixed_temperature | 4.222 / 26.222 | 0.00111 | 0.01443 | 18 | descriptive | descriptive | 0.00097656 | 0.03125 | 0.40625 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_threshold | temperature_refit | 4.222 / 11.556 | 0.125 | 1 | 18 | descriptive | descriptive | 0.125 | 0.25 | 1 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_threshold | physics_only | 4.222 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.9802e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_threshold | two_node_physics | 4.222 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.9802e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_threshold | always_observe_30 | 4.222 / 9.056 | 0.2505 | 1 | 18 | descriptive | descriptive | 0.25 | 0.40625 | 1 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_logistic | always_escalate | 3.333 / 4.222 | 0.125 | 1 | 18 | descriptive | descriptive | 1 | 0.25 | 1 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_logistic | paper_table3 | 3.333 / 9.333 | 0.14807 | 1 | 18 | descriptive | descriptive | 0.125 | 0.125 | 1 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_logistic | delay_lookup_refit | 3.333 / 15.778 | 0.030151 | 0.36182 | 18 | descriptive | descriptive | 0.03125 | 0.25 | 1 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_logistic | fixed_temperature | 3.333 / 26.222 | 0.00097466 | 0.013645 | 18 | descriptive | descriptive | 0.00097656 | 0.015625 | 0.21875 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_logistic | temperature_refit | 3.333 / 11.556 | 0.11719 | 1 | 18 | descriptive | descriptive | 0.125 | 0.15625 | 1 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_logistic | physics_only | 3.333 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.9802e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_logistic | two_node_physics | 3.333 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.9802e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | loco | agent_logistic | always_observe_30 | 3.333 / 9.056 | 0.23339 | 1 | 18 | descriptive | descriptive | 0.25 | 0.29688 | 1 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | always_escalate | 20.444 / 4.222 | 0.0078125 | 0.085938 | 18 | descriptive | descriptive | 0.0078125 | 0.125 | 1 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | paper_table3 | 20.444 / 9.333 | 0.070992 | 0.63892 | 18 | descriptive | descriptive | 0.28906 | 0.15625 | 1 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | delay_lookup_refit | 20.444 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 7.6294e-06 | 0.03125 | 0.4375 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | fixed_temperature | 20.444 / 26.222 | 0.59484 | 1 | 18 | descriptive | descriptive | 0.62906 | 0.67188 | 1 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | temperature_refit | 20.444 / 13.111 | 0.3877 | 1 | 18 | descriptive | descriptive | 0.3877 | 0.625 | 1 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | physics_only | 20.444 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 7.6294e-06 | 0.03125 | 0.4375 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | two_node_physics | 20.444 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 7.6294e-06 | 0.03125 | 0.4375 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | always_observe_30 | 20.444 / 20.833 | 0.72465 | 1 | 18 | descriptive | descriptive | 1 | 0.78906 | 1 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | always_escalate | 6.222 / 4.222 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | paper_table3 | 6.222 / 9.333 | 0.51771 | 1 | 18 | descriptive | descriptive | 0.375 | 0.46875 | 1 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | delay_lookup_refit | 6.222 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 5.9605e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | fixed_temperature | 6.222 / 26.222 | 0.0064499 | 0.077399 | 18 | descriptive | descriptive | 0.0063477 | 0.015625 | 0.23438 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | temperature_refit | 6.222 / 13.111 | 0.21875 | 1 | 18 | descriptive | descriptive | 0.375 | 0.5 | 1 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | physics_only | 6.222 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 5.9605e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | two_node_physics | 6.222 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 5.9605e-08 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | always_observe_30 | 6.222 / 20.833 | 0.01548 | 0.1548 | 18 | descriptive | descriptive | 0.015625 | 0.09375 | 1 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | always_escalate | 10.889 / 4.222 | 0.12549 | 1 | 18 | descriptive | descriptive | 0.125 | 0.125 | 1 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | paper_table3 | 10.889 / 9.333 | 0.69971 | 1 | 18 | descriptive | descriptive | 1 | 0.70312 | 1 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | delay_lookup_refit | 10.889 / 45.333 | 4e-05 | 0.00051999 | 18 | descriptive | descriptive | 3.0518e-05 | 0.015625 | 0.20312 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | fixed_temperature | 10.889 / 26.444 | 0.089069 | 0.97976 | 18 | descriptive | descriptive | 0.11847 | 0.125 | 1 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | temperature_refit | 10.889 / 15.111 | 0.59961 | 1 | 18 | descriptive | descriptive | 0.6875 | 0.59375 | 1 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | physics_only | 10.889 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 4.7684e-07 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | two_node_physics | 10.889 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 4.7684e-07 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | always_observe_30 | 10.889 / 17.722 | 0.20135 | 1 | 18 | descriptive | descriptive | 0.375 | 0.1875 | 1 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | always_escalate | 8.889 / 4.222 | 0.25 | 1 | 18 | descriptive | descriptive | 0.25 | 0.5 | 1 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | paper_table3 | 8.889 / 9.333 | 0.99947 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | delay_lookup_refit | 8.889 / 45.333 | 2e-05 | 0.00028 | 18 | descriptive | descriptive | 1.5259e-05 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | fixed_temperature | 8.889 / 26.444 | 0.017822 | 0.21387 | 18 | descriptive | descriptive | 0.021484 | 0.046875 | 0.5625 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | temperature_refit | 8.889 / 15.111 | 0.35547 | 1 | 18 | descriptive | descriptive | 0.375 | 0.46875 | 1 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | physics_only | 8.889 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.3842e-07 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | two_node_physics | 8.889 / 57.778 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 2.3842e-07 | 0.0078125 | 0.14062 | 8 |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | always_observe_30 | 8.889 / 17.722 | 0.20391 | 1 | 18 | descriptive | descriptive | 0.28906 | 0.1875 | 1 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_threshold | always_escalate | 6.000 / 3.800 | 0.375 | 1 | 18 | descriptive | descriptive | 0.125 | 1 | 1 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_threshold | paper_table3 | 6.000 / 5.400 | 0.91188 | 1 | 18 | descriptive | descriptive | 1 | 0.9375 | 1 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_threshold | delay_lookup_refit | 6.000 / 8.200 | 0.25 | 1 | 18 | descriptive | descriptive | 0.5 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_threshold | fixed_temperature | 6.000 / 14.600 | 0.0039062 | 0.050781 | 18 | descriptive | descriptive | 0.0039062 | 0.0625 | 0.75 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_threshold | temperature_refit | 6.000 / 5.400 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_threshold | physics_only | 6.000 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.4901e-08 | 0.015625 | 0.25 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_threshold | two_node_physics | 6.000 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.4901e-08 | 0.015625 | 0.25 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_threshold | always_observe_30 | 6.000 / 8.261 | 0.43027 | 1 | 18 | descriptive | descriptive | 0.72656 | 0.60938 | 1 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_logistic | always_escalate | 2.800 / 3.800 | 0.0625 | 0.625 | 18 | descriptive | descriptive | 1 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_logistic | paper_table3 | 2.800 / 5.400 | 0.32648 | 1 | 18 | descriptive | descriptive | 0.0625 | 0.15625 | 1 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_logistic | delay_lookup_refit | 2.800 / 8.200 | 0.030762 | 0.35544 | 18 | descriptive | descriptive | 0.03125 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_logistic | fixed_temperature | 2.800 / 14.600 | 0.00073999 | 0.01036 | 18 | descriptive | descriptive | 0.00024414 | 0.015625 | 0.25 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_logistic | temperature_refit | 2.800 / 5.400 | 0.22266 | 1 | 18 | descriptive | descriptive | 0.25 | 0.25 | 1 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_logistic | physics_only | 2.800 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_logistic | two_node_physics | 2.800 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio5 | loco | agent_logistic | always_observe_30 | 2.800 / 8.261 | 0.02962 | 0.35544 | 18 | descriptive | descriptive | 0.03125 | 0.015625 | 0.25 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | always_escalate | 9.400 / 3.800 | 0.040405 | 0.32324 | 18 | descriptive | descriptive | 0.015625 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | paper_table3 | 9.400 / 5.400 | 0.20573 | 1 | 18 | descriptive | descriptive | 0.72656 | 0.21875 | 1 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | delay_lookup_refit | 9.400 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.1921e-07 | 0.03125 | 0.40625 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | fixed_temperature | 9.400 / 14.600 | 0.27606 | 1 | 18 | descriptive | descriptive | 0.26318 | 0.36719 | 1 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | temperature_refit | 9.400 / 10.800 | 0.77328 | 1 | 18 | descriptive | descriptive | 1 | 0.9375 | 1 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | physics_only | 9.400 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.1921e-07 | 0.03125 | 0.40625 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | two_node_physics | 9.400 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 1.1921e-07 | 0.03125 | 0.40625 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | always_observe_30 | 9.400 / 14.661 | 0.03205 | 0.28845 | 18 | descriptive | descriptive | 0.21875 | 0.046875 | 0.46875 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | always_escalate | 3.600 / 3.800 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | paper_table3 | 3.600 / 5.400 | 0.50604 | 1 | 18 | descriptive | descriptive | 0.0625 | 0.40625 | 1 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | delay_lookup_refit | 3.600 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | fixed_temperature | 3.600 / 14.600 | 0.00227 | 0.02497 | 18 | descriptive | descriptive | 0.00024414 | 0.015625 | 0.23438 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | temperature_refit | 3.600 / 10.800 | 0.0078125 | 0.078125 | 18 | descriptive | descriptive | 0.015625 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | physics_only | 3.600 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | two_node_physics | 3.600 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | always_observe_30 | 3.600 / 14.661 | 0.00071999 | 0.0086399 | 18 | descriptive | descriptive | 0.00097656 | 0.015625 | 0.23438 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | always_escalate | 11.000 / 3.800 | 0.021484 | 0.21484 | 18 | descriptive | descriptive | 0.0039062 | 0.0625 | 0.625 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | paper_table3 | 11.000 / 5.400 | 0.096786 | 0.87108 | 18 | descriptive | descriptive | 0.34375 | 0.17188 | 1 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | delay_lookup_refit | 11.000 / 21.800 | 0.0011292 | 0.01355 | 18 | descriptive | descriptive | 0.00048828 | 0.015625 | 0.1875 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | fixed_temperature | 11.000 / 14.800 | 0.31445 | 1 | 18 | descriptive | descriptive | 0.34375 | 0.28125 | 1 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | temperature_refit | 11.000 / 8.600 | 0.33594 | 1 | 18 | descriptive | descriptive | 0.6875 | 0.375 | 1 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | physics_only | 11.000 / 31.000 | 2e-05 | 0.00032 | 18 | descriptive | descriptive | 4.7684e-07 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | two_node_physics | 11.000 / 31.000 | 2e-05 | 0.00032 | 18 | descriptive | descriptive | 4.7684e-07 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | always_observe_30 | 11.000 / 11.061 | 0.99983 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | always_escalate | 4.800 / 3.800 | 0.71802 | 1 | 18 | descriptive | descriptive | 0.25 | 0.5625 | 1 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | paper_table3 | 4.800 / 5.400 | 0.86456 | 1 | 18 | descriptive | descriptive | 0.72656 | 0.90625 | 1 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | delay_lookup_refit | 4.800 / 21.800 | 3e-05 | 0.00042 | 18 | descriptive | descriptive | 7.6294e-06 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | fixed_temperature | 4.800 / 14.800 | 0.0045166 | 0.049683 | 18 | descriptive | descriptive | 0.0063477 | 0.03125 | 0.34375 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | temperature_refit | 4.800 / 8.600 | 0.10938 | 0.875 | 18 | descriptive | descriptive | 0.125 | 0.1875 | 1 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | physics_only | 4.800 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 7.4506e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | two_node_physics | 4.800 / 31.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 7.4506e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | always_observe_30 | 4.800 / 11.061 | 4e-05 | 0.00051999 | 18 | descriptive | descriptive | 0.0625 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_threshold | always_escalate | 3.800 / 3.800 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_threshold | paper_table3 | 3.800 / 20.400 | 0.062509 | 0.50007 | 18 | descriptive | descriptive | 0.0625 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_threshold | delay_lookup_refit | 3.800 / 26.200 | 0.03125 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_threshold | fixed_temperature | 3.800 / 53.600 | 0.00025 | 0.0035 | 18 | descriptive | descriptive | 0.00024414 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_threshold | temperature_refit | 3.800 / 14.400 | 0.25 | 1 | 18 | descriptive | descriptive | 0.25 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_threshold | physics_only | 3.800 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_threshold | two_node_physics | 3.800 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_threshold | always_observe_30 | 3.800 / 26.261 | 0.03185 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.078125 | 0.9375 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_logistic | always_escalate | 3.800 / 3.800 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_logistic | paper_table3 | 3.800 / 20.400 | 0.062509 | 0.50007 | 18 | descriptive | descriptive | 0.0625 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_logistic | delay_lookup_refit | 3.800 / 26.200 | 0.03125 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_logistic | fixed_temperature | 3.800 / 53.600 | 0.00025 | 0.0035 | 18 | descriptive | descriptive | 0.00024414 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_logistic | temperature_refit | 3.800 / 14.400 | 0.25 | 1 | 18 | descriptive | descriptive | 0.25 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_logistic | physics_only | 3.800 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_logistic | two_node_physics | 3.800 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | loco | agent_logistic | always_observe_30 | 3.800 / 26.261 | 0.03185 | 0.375 | 18 | descriptive | descriptive | 0.03125 | 0.078125 | 0.9375 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | always_escalate | 3.800 / 3.800 | 1 | 1 | 18 | descriptive | descriptive | 1 | 1 | 1 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | paper_table3 | 3.800 / 20.400 | 0.062509 | 0.43757 | 18 | descriptive | descriptive | 0.0625 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | delay_lookup_refit | 3.800 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | fixed_temperature | 3.800 / 53.600 | 0.00025 | 0.00275 | 18 | descriptive | descriptive | 0.00024414 | 0.03125 | 0.4375 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | temperature_refit | 3.800 / 31.800 | 0.015625 | 0.125 | 18 | descriptive | descriptive | 0.015625 | 0.25 | 1 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | physics_only | 3.800 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | two_node_physics | 3.800 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 9.3132e-10 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | always_observe_30 | 3.800 / 47.661 | 0.00079999 | 0.0079999 | 18 | descriptive | descriptive | 0.00097656 | 0.015625 | 0.23438 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | always_escalate | 62.400 / 3.800 | 7.9999e-05 | 0.00095999 | 18 | descriptive | descriptive | 6.1035e-05 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | paper_table3 | 62.400 / 20.400 | 0.00443 | 0.03987 | 18 | descriptive | descriptive | 0.021271 | 0.19531 | 1 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | delay_lookup_refit | 62.400 / 124.000 | 5e-05 | 0.00074999 | 18 | descriptive | descriptive | 3.0518e-05 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | fixed_temperature | 62.400 / 53.600 | 0.55983 | 1 | 18 | descriptive | descriptive | 0.81453 | 0.73438 | 1 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | temperature_refit | 62.400 / 31.800 | 0.11616 | 0.69695 | 18 | descriptive | descriptive | 0.11532 | 0.46875 | 1 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | physics_only | 62.400 / 124.000 | 5e-05 | 0.00074999 | 18 | descriptive | descriptive | 3.0518e-05 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | two_node_physics | 62.400 / 124.000 | 5e-05 | 0.00074999 | 18 | descriptive | descriptive | 3.0518e-05 | 0.125 | 1 | 8 |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | always_observe_30 | 62.400 / 47.661 | 0.34369 | 1 | 18 | descriptive | descriptive | 0.34375 | 0.52344 | 1 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | always_escalate | 10.200 / 3.800 | 0.5 | 1 | 18 | descriptive | descriptive | 0.5 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | paper_table3 | 10.200 / 20.400 | 0.45296 | 1 | 18 | descriptive | descriptive | 0.45312 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | delay_lookup_refit | 10.200 / 84.800 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 3.8147e-06 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | fixed_temperature | 10.200 / 53.800 | 0.0032196 | 0.035416 | 18 | descriptive | descriptive | 0.003418 | 0.0625 | 0.625 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | temperature_refit | 10.200 / 29.600 | 0.0625 | 0.5 | 18 | descriptive | descriptive | 0.0625 | 0.25 | 1 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | physics_only | 10.200 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 3.7253e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | two_node_physics | 10.200 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 3.7253e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | always_observe_30 | 10.200 / 35.061 | 0.00080999 | 0.0097199 | 18 | descriptive | descriptive | 0.03125 | 0.015625 | 0.1875 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | always_escalate | 14.600 / 3.800 | 0.25 | 1 | 18 | descriptive | descriptive | 0.25 | 0.5 | 1 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | paper_table3 | 14.600 / 20.400 | 0.72656 | 1 | 18 | descriptive | descriptive | 0.72656 | 0.75 | 1 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | delay_lookup_refit | 14.600 / 84.800 | 2e-05 | 0.00026 | 18 | descriptive | descriptive | 7.6294e-06 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | fixed_temperature | 14.600 / 53.800 | 0.0062561 | 0.062561 | 18 | descriptive | descriptive | 0.0063477 | 0.03125 | 0.34375 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | temperature_refit | 14.600 / 29.600 | 0.21875 | 1 | 18 | descriptive | descriptive | 0.21875 | 0.375 | 1 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | physics_only | 14.600 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 7.4506e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | two_node_physics | 14.600 / 124.000 | 9.9999e-06 | 0.00018 | 18 | descriptive | descriptive | 7.4506e-09 | 0.0078125 | 0.14062 | 8 |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | always_observe_30 | 14.600 / 35.061 | 0.03701 | 0.33309 | 18 | descriptive | descriptive | 0.125 | 0.085938 | 0.77344 | 8 |

Condition-clustered p values flip the sign of the total paired cost difference for each held-out condition block, rather than each trial. Their Holm values use the same candidate-by-baseline family size, including missing comparators at p=1, but are descriptive robustness checks outside the registered primary test.

With d discordant units the best possible exact two-sided p is 2^(1-d). At alpha=0.05, at least 6 unanimous discordances are needed without correction. a 18-comparison family needs at least 10 at the first Holm step. Mixed signs can require more. Sharing a fitted model within a condition does not create independent inferential units. Monte Carlo p values have finite resolution and the JSON records sampling standard error.

The primary LOCO sensitivity has 8 condition blocks: the smallest possible two-sided clustered p is 0.0078125, or 0.14062 at the first Holm step. No clustered comparison survives correction.

## post-hoc sensitivity (added after the primary results were known)

These baseline variants apply decide(..., mode="cost") to their own original held-out probabilities with no refit and no watch. They are descriptive, marked post_hoc=true in the ledger, and outside the registered Holm family. Their paired sign-flip p values use the registered test mechanics without a new confirmatory correction family. They do not amend or replace the primary analysis.

Cost assumption for this table: escalating on a trial that ignites costs 0; escalation efficacy was not observed.

| Data, lag, ratio | Split | Cost-rule baseline | n | Unsafe | Missed | Mean cost | Interpretation |
|---|---|---|---:|---:|---:|---:|---|
| with_inferred-lag10-ratio10 | loco | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio10 | loco | temperature_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio10 | loco | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio10 | loco | physics_only | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | loco | two_node_physics | 50 | 5 | 12 | 12.400 | descriptive |
| with_inferred-lag10-ratio10 | loco | paper_table3 | 50 | 5 | 2 | 10.400 | descriptive |
| with_inferred-lag10-ratio10 | lofo | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio10 | lofo | temperature_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio10 | lofo | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio10 | lofo | physics_only | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | lofo | two_node_physics | 50 | 5 | 12 | 12.400 | descriptive |
| with_inferred-lag10-ratio10 | lofo | paper_table3 | 50 | 5 | 2 | 10.400 | descriptive |
| with_inferred-lag10-ratio10 | prequential | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio10 | prequential | temperature_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio10 | prequential | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio10 | prequential | physics_only | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | prequential | two_node_physics | 50 | 5 | 12 | 12.400 | descriptive |
| with_inferred-lag10-ratio10 | prequential | paper_table3 | 50 | 5 | 2 | 10.400 | descriptive |
| without_inferred-lag10-ratio10 | loco | fixed_temperature | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag10-ratio10 | loco | temperature_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag10-ratio10 | loco | delay_lookup_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag10-ratio10 | loco | physics_only | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | loco | two_node_physics | 45 | 5 | 12 | 13.778 | descriptive |
| without_inferred-lag10-ratio10 | loco | paper_table3 | 45 | 4 | 2 | 9.333 | descriptive |
| without_inferred-lag10-ratio10 | lofo | fixed_temperature | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag10-ratio10 | lofo | temperature_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag10-ratio10 | lofo | delay_lookup_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag10-ratio10 | lofo | physics_only | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | lofo | two_node_physics | 45 | 5 | 12 | 13.778 | descriptive |
| without_inferred-lag10-ratio10 | lofo | paper_table3 | 45 | 4 | 2 | 9.333 | descriptive |
| without_inferred-lag10-ratio10 | prequential | fixed_temperature | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag10-ratio10 | prequential | temperature_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag10-ratio10 | prequential | delay_lookup_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag10-ratio10 | prequential | physics_only | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | prequential | two_node_physics | 45 | 5 | 12 | 13.778 | descriptive |
| without_inferred-lag10-ratio10 | prequential | paper_table3 | 45 | 4 | 2 | 9.333 | descriptive |
| with_inferred-lag7-ratio10 | loco | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag7-ratio10 | loco | temperature_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag7-ratio10 | loco | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag7-ratio10 | loco | physics_only | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | loco | two_node_physics | 50 | 5 | 12 | 12.400 | descriptive |
| with_inferred-lag7-ratio10 | loco | paper_table3 | 50 | 5 | 2 | 10.400 | descriptive |
| with_inferred-lag7-ratio10 | lofo | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag7-ratio10 | lofo | temperature_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag7-ratio10 | lofo | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag7-ratio10 | lofo | physics_only | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | lofo | two_node_physics | 50 | 5 | 12 | 12.400 | descriptive |
| with_inferred-lag7-ratio10 | lofo | paper_table3 | 50 | 5 | 2 | 10.400 | descriptive |
| with_inferred-lag7-ratio10 | prequential | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag7-ratio10 | prequential | temperature_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag7-ratio10 | prequential | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag7-ratio10 | prequential | physics_only | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | prequential | two_node_physics | 50 | 5 | 12 | 12.400 | descriptive |
| with_inferred-lag7-ratio10 | prequential | paper_table3 | 50 | 5 | 2 | 10.400 | descriptive |
| without_inferred-lag7-ratio10 | loco | fixed_temperature | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag7-ratio10 | loco | temperature_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag7-ratio10 | loco | delay_lookup_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag7-ratio10 | loco | physics_only | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | loco | two_node_physics | 45 | 5 | 12 | 13.778 | descriptive |
| without_inferred-lag7-ratio10 | loco | paper_table3 | 45 | 4 | 2 | 9.333 | descriptive |
| without_inferred-lag7-ratio10 | lofo | fixed_temperature | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag7-ratio10 | lofo | temperature_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag7-ratio10 | lofo | delay_lookup_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag7-ratio10 | lofo | physics_only | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | lofo | two_node_physics | 45 | 5 | 12 | 13.778 | descriptive |
| without_inferred-lag7-ratio10 | lofo | paper_table3 | 45 | 4 | 2 | 9.333 | descriptive |
| without_inferred-lag7-ratio10 | prequential | fixed_temperature | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag7-ratio10 | prequential | temperature_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag7-ratio10 | prequential | delay_lookup_refit | 45 | 0 | 19 | 4.222 | descriptive |
| without_inferred-lag7-ratio10 | prequential | physics_only | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | prequential | two_node_physics | 45 | 5 | 12 | 13.778 | descriptive |
| without_inferred-lag7-ratio10 | prequential | paper_table3 | 45 | 4 | 2 | 9.333 | descriptive |
| with_inferred-lag10-ratio5 | loco | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio5 | loco | temperature_refit | 50 | 0 | 16 | 3.200 | descriptive |
| with_inferred-lag10-ratio5 | loco | delay_lookup_refit | 50 | 4 | 15 | 7.000 | descriptive |
| with_inferred-lag10-ratio5 | loco | physics_only | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | loco | two_node_physics | 50 | 24 | 7 | 25.400 | descriptive |
| with_inferred-lag10-ratio5 | loco | paper_table3 | 50 | 5 | 2 | 5.400 | descriptive |
| with_inferred-lag10-ratio5 | lofo | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio5 | lofo | temperature_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio5 | lofo | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio5 | lofo | physics_only | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | lofo | two_node_physics | 50 | 24 | 7 | 25.400 | descriptive |
| with_inferred-lag10-ratio5 | lofo | paper_table3 | 50 | 5 | 2 | 5.400 | descriptive |
| with_inferred-lag10-ratio5 | prequential | fixed_temperature | 50 | 1 | 15 | 4.000 | descriptive |
| with_inferred-lag10-ratio5 | prequential | temperature_refit | 50 | 6 | 12 | 8.400 | descriptive |
| with_inferred-lag10-ratio5 | prequential | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio5 | prequential | physics_only | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | prequential | two_node_physics | 50 | 24 | 7 | 25.400 | descriptive |
| with_inferred-lag10-ratio5 | prequential | paper_table3 | 50 | 5 | 2 | 5.400 | descriptive |
| with_inferred-lag10-ratio20 | loco | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio20 | loco | temperature_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio20 | loco | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio20 | loco | physics_only | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | loco | two_node_physics | 50 | 5 | 12 | 22.400 | descriptive |
| with_inferred-lag10-ratio20 | loco | paper_table3 | 50 | 5 | 2 | 20.400 | descriptive |
| with_inferred-lag10-ratio20 | lofo | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio20 | lofo | temperature_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio20 | lofo | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio20 | lofo | physics_only | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | lofo | two_node_physics | 50 | 5 | 12 | 22.400 | descriptive |
| with_inferred-lag10-ratio20 | lofo | paper_table3 | 50 | 5 | 2 | 20.400 | descriptive |
| with_inferred-lag10-ratio20 | prequential | fixed_temperature | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio20 | prequential | temperature_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio20 | prequential | delay_lookup_refit | 50 | 0 | 19 | 3.800 | descriptive |
| with_inferred-lag10-ratio20 | prequential | physics_only | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | prequential | two_node_physics | 50 | 5 | 12 | 22.400 | descriptive |
| with_inferred-lag10-ratio20 | prequential | paper_table3 | 50 | 5 | 2 | 20.400 | descriptive |

Cost assumption for this table: escalating on a trial that ignites costs 0; escalation efficacy was not observed.

| Data, lag, ratio | Split | Agent | Cost-rule baseline | Agent / baseline mean cost | Paired p | Interpretation |
|---|---|---|---|---:|---:|---|
| with_inferred-lag10-ratio10 | loco | agent_threshold | fixed_temperature | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_logistic | fixed_temperature | 3.720 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_threshold | temperature_refit | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_logistic | temperature_refit | 3.720 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_threshold | delay_lookup_refit | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_logistic | delay_lookup_refit | 3.720 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_threshold | physics_only | 3.800 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_logistic | physics_only | 3.720 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_threshold | two_node_physics | 3.800 / 12.400 | 0.0625 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_logistic | two_node_physics | 3.720 / 12.400 | 0.062256 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_threshold | paper_table3 | 3.800 / 10.400 | 0.16118 | descriptive |
| with_inferred-lag10-ratio10 | loco | agent_logistic | paper_table3 | 3.720 / 10.400 | 0.13373 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | fixed_temperature | 16.400 / 3.800 | 0.015625 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | fixed_temperature | 3.830 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | temperature_refit | 16.400 / 3.800 | 0.015625 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | temperature_refit | 3.830 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | delay_lookup_refit | 16.400 / 3.800 | 0.015625 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | delay_lookup_refit | 3.830 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | physics_only | 16.400 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | physics_only | 3.830 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | two_node_physics | 16.400 / 12.400 | 0.5 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | two_node_physics | 3.830 / 12.400 | 0.0625 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_threshold | paper_table3 | 16.400 / 10.400 | 0.2904 | descriptive |
| with_inferred-lag10-ratio10 | lofo | agent_logistic | paper_table3 | 3.830 / 10.400 | 0.1617 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | fixed_temperature | 6.200 / 3.800 | 0.5 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | fixed_temperature | 4.490 / 3.800 | 0.99316 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | temperature_refit | 6.200 / 3.800 | 0.5 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | temperature_refit | 4.490 / 3.800 | 0.99316 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | delay_lookup_refit | 6.200 / 3.800 | 0.5 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | delay_lookup_refit | 4.490 / 3.800 | 0.99316 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | physics_only | 6.200 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | physics_only | 4.490 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | two_node_physics | 6.200 / 12.400 | 0.2896 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | two_node_physics | 4.490 / 12.400 | 0.1453 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_threshold | paper_table3 | 6.200 / 10.400 | 0.45323 | descriptive |
| with_inferred-lag10-ratio10 | prequential | agent_logistic | paper_table3 | 4.490 / 10.400 | 0.21921 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_threshold | fixed_temperature | 4.222 / 4.222 | 1 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_logistic | fixed_temperature | 3.333 / 4.222 | 0.125 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_threshold | temperature_refit | 4.222 / 4.222 | 1 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_logistic | temperature_refit | 3.333 / 4.222 | 0.125 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_threshold | delay_lookup_refit | 4.222 / 4.222 | 1 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_logistic | delay_lookup_refit | 3.333 / 4.222 | 0.125 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_threshold | physics_only | 4.222 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_logistic | physics_only | 3.333 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_threshold | two_node_physics | 4.222 / 13.778 | 0.0625 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_logistic | two_node_physics | 3.333 / 13.778 | 0.056885 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_threshold | paper_table3 | 4.222 / 9.333 | 0.28118 | descriptive |
| without_inferred-lag10-ratio10 | loco | agent_logistic | paper_table3 | 3.333 / 9.333 | 0.14807 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | fixed_temperature | 18.222 / 4.222 | 0.015625 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | fixed_temperature | 6.222 / 4.222 | 1 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | temperature_refit | 18.222 / 4.222 | 0.015625 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | temperature_refit | 6.222 / 4.222 | 1 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | delay_lookup_refit | 18.222 / 4.222 | 0.015625 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | delay_lookup_refit | 6.222 / 4.222 | 1 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | physics_only | 18.222 / 57.778 | 2e-05 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | physics_only | 6.222 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | two_node_physics | 18.222 / 13.778 | 0.5 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | two_node_physics | 6.222 / 13.778 | 0.21875 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_threshold | paper_table3 | 18.222 / 9.333 | 0.12601 | descriptive |
| without_inferred-lag10-ratio10 | lofo | agent_logistic | paper_table3 | 6.222 / 9.333 | 0.51771 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | fixed_temperature | 10.889 / 4.222 | 0.12549 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | fixed_temperature | 8.667 / 4.222 | 0.25073 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | temperature_refit | 10.889 / 4.222 | 0.12549 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | temperature_refit | 8.667 / 4.222 | 0.25073 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | delay_lookup_refit | 10.889 / 4.222 | 0.12549 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | delay_lookup_refit | 8.667 / 4.222 | 0.25073 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | physics_only | 10.889 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | physics_only | 8.667 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | two_node_physics | 10.889 / 13.778 | 0.61201 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | two_node_physics | 8.667 / 13.778 | 0.42308 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_threshold | paper_table3 | 10.889 / 9.333 | 0.69971 | descriptive |
| without_inferred-lag10-ratio10 | prequential | agent_logistic | paper_table3 | 8.667 / 9.333 | 0.99893 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_threshold | fixed_temperature | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_logistic | fixed_temperature | 3.880 / 3.800 | 0.77637 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_threshold | temperature_refit | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_logistic | temperature_refit | 3.880 / 3.800 | 0.77637 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_threshold | delay_lookup_refit | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_logistic | delay_lookup_refit | 3.880 / 3.800 | 0.77637 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_threshold | physics_only | 3.800 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_logistic | physics_only | 3.880 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_threshold | two_node_physics | 3.800 / 12.400 | 0.0625 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_logistic | two_node_physics | 3.880 / 12.400 | 0.061638 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_threshold | paper_table3 | 3.800 / 10.400 | 0.16118 | descriptive |
| with_inferred-lag7-ratio10 | loco | agent_logistic | paper_table3 | 3.880 / 10.400 | 0.14195 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | fixed_temperature | 18.400 / 3.800 | 0.0078125 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | fixed_temperature | 3.600 / 3.800 | 1 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | temperature_refit | 18.400 / 3.800 | 0.0078125 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | temperature_refit | 3.600 / 3.800 | 1 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | delay_lookup_refit | 18.400 / 3.800 | 0.0078125 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | delay_lookup_refit | 3.600 / 3.800 | 1 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | physics_only | 18.400 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | physics_only | 3.600 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | two_node_physics | 18.400 / 12.400 | 0.25 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | two_node_physics | 3.600 / 12.400 | 0.0625 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_threshold | paper_table3 | 18.400 / 10.400 | 0.18086 | descriptive |
| with_inferred-lag7-ratio10 | lofo | agent_logistic | paper_table3 | 3.600 / 10.400 | 0.13151 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | fixed_temperature | 11.800 / 3.800 | 0.062805 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | fixed_temperature | 4.010 / 3.800 | 0.99905 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | temperature_refit | 11.800 / 3.800 | 0.062805 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | temperature_refit | 4.010 / 3.800 | 0.99905 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | delay_lookup_refit | 11.800 / 3.800 | 0.062805 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | delay_lookup_refit | 4.010 / 3.800 | 0.99905 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | physics_only | 11.800 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | physics_only | 4.010 / 62.000 | 9.9999e-06 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | two_node_physics | 11.800 / 12.400 | 0.88521 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | two_node_physics | 4.010 / 12.400 | 0.091069 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_threshold | paper_table3 | 11.800 / 10.400 | 0.73724 | descriptive |
| with_inferred-lag7-ratio10 | prequential | agent_logistic | paper_table3 | 4.010 / 10.400 | 0.21838 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_threshold | fixed_temperature | 4.222 / 4.222 | 1 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_logistic | fixed_temperature | 3.333 / 4.222 | 0.125 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_threshold | temperature_refit | 4.222 / 4.222 | 1 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_logistic | temperature_refit | 3.333 / 4.222 | 0.125 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_threshold | delay_lookup_refit | 4.222 / 4.222 | 1 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_logistic | delay_lookup_refit | 3.333 / 4.222 | 0.125 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_threshold | physics_only | 4.222 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_logistic | physics_only | 3.333 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_threshold | two_node_physics | 4.222 / 13.778 | 0.0625 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_logistic | two_node_physics | 3.333 / 13.778 | 0.056885 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_threshold | paper_table3 | 4.222 / 9.333 | 0.28118 | descriptive |
| without_inferred-lag7-ratio10 | loco | agent_logistic | paper_table3 | 3.333 / 9.333 | 0.14807 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | fixed_temperature | 20.444 / 4.222 | 0.0078125 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | fixed_temperature | 6.222 / 4.222 | 1 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | temperature_refit | 20.444 / 4.222 | 0.0078125 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | temperature_refit | 6.222 / 4.222 | 1 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | delay_lookup_refit | 20.444 / 4.222 | 0.0078125 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | delay_lookup_refit | 6.222 / 4.222 | 1 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | physics_only | 20.444 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | physics_only | 6.222 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | two_node_physics | 20.444 / 13.778 | 0.25 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | two_node_physics | 6.222 / 13.778 | 0.21875 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_threshold | paper_table3 | 20.444 / 9.333 | 0.070992 | descriptive |
| without_inferred-lag7-ratio10 | lofo | agent_logistic | paper_table3 | 6.222 / 9.333 | 0.51771 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | fixed_temperature | 10.889 / 4.222 | 0.12549 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | fixed_temperature | 8.889 / 4.222 | 0.25 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | temperature_refit | 10.889 / 4.222 | 0.12549 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | temperature_refit | 8.889 / 4.222 | 0.25 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | delay_lookup_refit | 10.889 / 4.222 | 0.12549 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | delay_lookup_refit | 8.889 / 4.222 | 0.25 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | physics_only | 10.889 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | physics_only | 8.889 / 57.778 | 9.9999e-06 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | two_node_physics | 10.889 / 13.778 | 0.61201 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | two_node_physics | 8.889 / 13.778 | 0.465 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_threshold | paper_table3 | 10.889 / 9.333 | 0.69971 | descriptive |
| without_inferred-lag7-ratio10 | prequential | agent_logistic | paper_table3 | 8.889 / 9.333 | 0.99947 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_threshold | fixed_temperature | 6.000 / 3.800 | 0.375 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_logistic | fixed_temperature | 2.800 / 3.800 | 0.0625 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_threshold | temperature_refit | 6.000 / 3.200 | 0.17969 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_logistic | temperature_refit | 2.800 / 3.200 | 0.625 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_threshold | delay_lookup_refit | 6.000 / 7.000 | 0.0625 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_logistic | delay_lookup_refit | 2.800 / 7.000 | 0.0625 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_threshold | physics_only | 6.000 / 31.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_logistic | physics_only | 2.800 / 31.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_threshold | two_node_physics | 6.000 / 25.400 | 0.0003 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_logistic | two_node_physics | 2.800 / 25.400 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_threshold | paper_table3 | 6.000 / 5.400 | 0.91188 | descriptive |
| with_inferred-lag10-ratio5 | loco | agent_logistic | paper_table3 | 2.800 / 5.400 | 0.32648 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | fixed_temperature | 9.400 / 3.800 | 0.040405 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | fixed_temperature | 3.600 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | temperature_refit | 9.400 / 3.800 | 0.040405 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | temperature_refit | 3.600 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | delay_lookup_refit | 9.400 / 3.800 | 0.040405 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | delay_lookup_refit | 3.600 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | physics_only | 9.400 / 31.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | physics_only | 3.600 / 31.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | two_node_physics | 9.400 / 25.400 | 3e-05 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | two_node_physics | 3.600 / 25.400 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_threshold | paper_table3 | 9.400 / 5.400 | 0.20573 | descriptive |
| with_inferred-lag10-ratio5 | lofo | agent_logistic | paper_table3 | 3.600 / 5.400 | 0.50604 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | fixed_temperature | 11.000 / 4.000 | 0.0097656 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | fixed_temperature | 4.800 / 4.000 | 0.60938 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | temperature_refit | 11.000 / 8.400 | 0.43262 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | temperature_refit | 4.800 / 8.400 | 0.056641 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | delay_lookup_refit | 11.000 / 3.800 | 0.021484 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | delay_lookup_refit | 4.800 / 3.800 | 0.71802 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | physics_only | 11.000 / 31.000 | 2e-05 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | physics_only | 4.800 / 31.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | two_node_physics | 11.000 / 25.400 | 0.00259 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | two_node_physics | 4.800 / 25.400 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_threshold | paper_table3 | 11.000 / 5.400 | 0.096786 | descriptive |
| with_inferred-lag10-ratio5 | prequential | agent_logistic | paper_table3 | 4.800 / 5.400 | 0.86456 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_threshold | fixed_temperature | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_logistic | fixed_temperature | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_threshold | temperature_refit | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_logistic | temperature_refit | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_threshold | delay_lookup_refit | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_logistic | delay_lookup_refit | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_threshold | physics_only | 3.800 / 124.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_logistic | physics_only | 3.800 / 124.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_threshold | two_node_physics | 3.800 / 22.400 | 0.0625 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_logistic | two_node_physics | 3.800 / 22.400 | 0.0625 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_threshold | paper_table3 | 3.800 / 20.400 | 0.062509 | descriptive |
| with_inferred-lag10-ratio20 | loco | agent_logistic | paper_table3 | 3.800 / 20.400 | 0.062509 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | fixed_temperature | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | fixed_temperature | 62.400 / 3.800 | 7.9999e-05 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | temperature_refit | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | temperature_refit | 62.400 / 3.800 | 7.9999e-05 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | delay_lookup_refit | 3.800 / 3.800 | 1 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | delay_lookup_refit | 62.400 / 3.800 | 7.9999e-05 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | physics_only | 3.800 / 124.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | physics_only | 62.400 / 124.000 | 5e-05 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | two_node_physics | 3.800 / 22.400 | 0.0625 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | two_node_physics | 62.400 / 22.400 | 0.0019531 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_threshold | paper_table3 | 3.800 / 20.400 | 0.062509 | descriptive |
| with_inferred-lag10-ratio20 | lofo | agent_logistic | paper_table3 | 62.400 / 20.400 | 0.00443 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | fixed_temperature | 10.200 / 3.800 | 0.5 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | fixed_temperature | 14.600 / 3.800 | 0.25 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | temperature_refit | 10.200 / 3.800 | 0.5 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | temperature_refit | 14.600 / 3.800 | 0.25 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | delay_lookup_refit | 10.200 / 3.800 | 0.5 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | delay_lookup_refit | 14.600 / 3.800 | 0.25 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | physics_only | 10.200 / 124.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | physics_only | 14.600 / 124.000 | 9.9999e-06 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | two_node_physics | 10.200 / 22.400 | 0.2896 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | two_node_physics | 14.600 / 22.400 | 0.59903 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_threshold | paper_table3 | 10.200 / 20.400 | 0.45296 | descriptive |
| with_inferred-lag10-ratio20 | prequential | agent_logistic | paper_table3 | 14.600 / 20.400 | 0.72656 | descriptive |

| Data, lag, ratio | Split | Physics baseline | Minimum p | Maximum p | Interpretation |
|---|---|---|---:|---:|---|
| with_inferred-lag10-ratio10 | loco | physics_only | 0 | 0 | descriptive |
| with_inferred-lag10-ratio10 | loco | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag10-ratio10 | lofo | physics_only | 0 | 0 | descriptive |
| with_inferred-lag10-ratio10 | lofo | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag10-ratio10 | prequential | physics_only | 0 | 0 | descriptive |
| with_inferred-lag10-ratio10 | prequential | two_node_physics | 0 | 0.25 | descriptive |
| without_inferred-lag10-ratio10 | loco | physics_only | 0 | 0 | descriptive |
| without_inferred-lag10-ratio10 | loco | two_node_physics | 0 | 0.25 | descriptive |
| without_inferred-lag10-ratio10 | lofo | physics_only | 0 | 0 | descriptive |
| without_inferred-lag10-ratio10 | lofo | two_node_physics | 0 | 0.25 | descriptive |
| without_inferred-lag10-ratio10 | prequential | physics_only | 0 | 0 | descriptive |
| without_inferred-lag10-ratio10 | prequential | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag7-ratio10 | loco | physics_only | 0 | 0 | descriptive |
| with_inferred-lag7-ratio10 | loco | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag7-ratio10 | lofo | physics_only | 0 | 0 | descriptive |
| with_inferred-lag7-ratio10 | lofo | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag7-ratio10 | prequential | physics_only | 0 | 0 | descriptive |
| with_inferred-lag7-ratio10 | prequential | two_node_physics | 0 | 0.25 | descriptive |
| without_inferred-lag7-ratio10 | loco | physics_only | 0 | 0 | descriptive |
| without_inferred-lag7-ratio10 | loco | two_node_physics | 0 | 0.25 | descriptive |
| without_inferred-lag7-ratio10 | lofo | physics_only | 0 | 0 | descriptive |
| without_inferred-lag7-ratio10 | lofo | two_node_physics | 0 | 0.25 | descriptive |
| without_inferred-lag7-ratio10 | prequential | physics_only | 0 | 0 | descriptive |
| without_inferred-lag7-ratio10 | prequential | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag10-ratio5 | loco | physics_only | 0 | 0 | descriptive |
| with_inferred-lag10-ratio5 | loco | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag10-ratio5 | lofo | physics_only | 0 | 0 | descriptive |
| with_inferred-lag10-ratio5 | lofo | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag10-ratio5 | prequential | physics_only | 0 | 0 | descriptive |
| with_inferred-lag10-ratio5 | prequential | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag10-ratio20 | loco | physics_only | 0 | 0 | descriptive |
| with_inferred-lag10-ratio20 | loco | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag10-ratio20 | lofo | physics_only | 0 | 0 | descriptive |
| with_inferred-lag10-ratio20 | lofo | two_node_physics | 0 | 0.25 | descriptive |
| with_inferred-lag10-ratio20 | prequential | physics_only | 0 | 0 | descriptive |
| with_inferred-lag10-ratio20 | prequential | two_node_physics | 0 | 0.25 | descriptive |

Both physics baselines fail to discriminate under their primary classification rules: physics_only gives p=0 everywhere (maximum 0), and two_node_physics never exceeds 0.25. Their primary wins are evidence of failed comparators, not of useful learned discrimination.

The fixed-temperature sweep is descriptive and post-hoc. It varies the illustrative, unsourced temperature cut across the declared grid on the same held-out trials. No threshold is selected from this sweep and the primary 170 °C rule is unchanged.

Cost assumption for this table: escalating on a trial that ignites costs 0; escalation efficacy was not observed.

| Data, lag, ratio | Split | Threshold °C | n | Unsafe | Missed | Mean cost | Interpretation |
|---|---|---:|---:|---:|---:|---:|---|
| with_inferred-lag10-ratio10 | loco | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag10-ratio10 | loco | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag10-ratio10 | loco | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag10-ratio10 | loco | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag10-ratio10 | loco | 160 | 50 | 3 | 10 | 8.000 | descriptive |
| with_inferred-lag10-ratio10 | loco | 165 | 50 | 9 | 8 | 19.600 | descriptive |
| with_inferred-lag10-ratio10 | loco | 170 | 50 | 13 | 8 | 27.600 | descriptive |
| with_inferred-lag10-ratio10 | loco | 175 | 50 | 22 | 6 | 45.200 | descriptive |
| with_inferred-lag10-ratio10 | loco | 180 | 50 | 27 | 0 | 54.000 | descriptive |
| with_inferred-lag10-ratio10 | loco | 185 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | loco | 190 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | loco | 195 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | loco | 200 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 160 | 50 | 3 | 10 | 8.000 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 165 | 50 | 9 | 8 | 19.600 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 170 | 50 | 13 | 8 | 27.600 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 175 | 50 | 22 | 6 | 45.200 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 180 | 50 | 27 | 0 | 54.000 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 185 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 190 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 195 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | lofo | 200 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 160 | 50 | 3 | 10 | 8.000 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 165 | 50 | 9 | 8 | 19.600 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 170 | 50 | 13 | 8 | 27.600 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 175 | 50 | 22 | 6 | 45.200 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 180 | 50 | 27 | 0 | 54.000 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 185 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 190 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 195 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag10-ratio10 | prequential | 200 | 50 | 31 | 0 | 62.000 | descriptive |
| without_inferred-lag10-ratio10 | loco | 140 | 45 | 0 | 18 | 4.000 | descriptive |
| without_inferred-lag10-ratio10 | loco | 145 | 45 | 0 | 17 | 3.778 | descriptive |
| without_inferred-lag10-ratio10 | loco | 150 | 45 | 0 | 14 | 3.111 | descriptive |
| without_inferred-lag10-ratio10 | loco | 155 | 45 | 0 | 12 | 2.667 | descriptive |
| without_inferred-lag10-ratio10 | loco | 160 | 45 | 3 | 10 | 8.889 | descriptive |
| without_inferred-lag10-ratio10 | loco | 165 | 45 | 7 | 8 | 17.333 | descriptive |
| without_inferred-lag10-ratio10 | loco | 170 | 45 | 11 | 8 | 26.222 | descriptive |
| without_inferred-lag10-ratio10 | loco | 175 | 45 | 20 | 6 | 45.778 | descriptive |
| without_inferred-lag10-ratio10 | loco | 180 | 45 | 25 | 0 | 55.556 | descriptive |
| without_inferred-lag10-ratio10 | loco | 185 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | loco | 190 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | loco | 195 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | loco | 200 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 140 | 45 | 0 | 18 | 4.000 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 145 | 45 | 0 | 17 | 3.778 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 150 | 45 | 0 | 14 | 3.111 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 155 | 45 | 0 | 12 | 2.667 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 160 | 45 | 3 | 10 | 8.889 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 165 | 45 | 7 | 8 | 17.333 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 170 | 45 | 11 | 8 | 26.222 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 175 | 45 | 20 | 6 | 45.778 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 180 | 45 | 25 | 0 | 55.556 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 185 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 190 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 195 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | lofo | 200 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 140 | 45 | 0 | 18 | 4.000 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 145 | 45 | 0 | 17 | 3.778 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 150 | 45 | 0 | 14 | 3.111 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 155 | 45 | 0 | 12 | 2.667 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 160 | 45 | 3 | 10 | 8.889 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 165 | 45 | 7 | 8 | 17.333 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 170 | 45 | 11 | 8 | 26.222 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 175 | 45 | 20 | 6 | 45.778 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 180 | 45 | 25 | 0 | 55.556 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 185 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 190 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 195 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag10-ratio10 | prequential | 200 | 45 | 26 | 0 | 57.778 | descriptive |
| with_inferred-lag7-ratio10 | loco | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag7-ratio10 | loco | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag7-ratio10 | loco | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag7-ratio10 | loco | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag7-ratio10 | loco | 160 | 50 | 3 | 10 | 8.000 | descriptive |
| with_inferred-lag7-ratio10 | loco | 165 | 50 | 7 | 8 | 15.600 | descriptive |
| with_inferred-lag7-ratio10 | loco | 170 | 50 | 13 | 8 | 27.600 | descriptive |
| with_inferred-lag7-ratio10 | loco | 175 | 50 | 22 | 6 | 45.200 | descriptive |
| with_inferred-lag7-ratio10 | loco | 180 | 50 | 27 | 0 | 54.000 | descriptive |
| with_inferred-lag7-ratio10 | loco | 185 | 50 | 30 | 0 | 60.000 | descriptive |
| with_inferred-lag7-ratio10 | loco | 190 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | loco | 195 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | loco | 200 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 160 | 50 | 3 | 10 | 8.000 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 165 | 50 | 7 | 8 | 15.600 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 170 | 50 | 13 | 8 | 27.600 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 175 | 50 | 22 | 6 | 45.200 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 180 | 50 | 27 | 0 | 54.000 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 185 | 50 | 30 | 0 | 60.000 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 190 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 195 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | lofo | 200 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 160 | 50 | 3 | 10 | 8.000 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 165 | 50 | 7 | 8 | 15.600 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 170 | 50 | 13 | 8 | 27.600 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 175 | 50 | 22 | 6 | 45.200 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 180 | 50 | 27 | 0 | 54.000 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 185 | 50 | 30 | 0 | 60.000 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 190 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 195 | 50 | 31 | 0 | 62.000 | descriptive |
| with_inferred-lag7-ratio10 | prequential | 200 | 50 | 31 | 0 | 62.000 | descriptive |
| without_inferred-lag7-ratio10 | loco | 140 | 45 | 0 | 18 | 4.000 | descriptive |
| without_inferred-lag7-ratio10 | loco | 145 | 45 | 0 | 17 | 3.778 | descriptive |
| without_inferred-lag7-ratio10 | loco | 150 | 45 | 0 | 14 | 3.111 | descriptive |
| without_inferred-lag7-ratio10 | loco | 155 | 45 | 0 | 12 | 2.667 | descriptive |
| without_inferred-lag7-ratio10 | loco | 160 | 45 | 3 | 10 | 8.889 | descriptive |
| without_inferred-lag7-ratio10 | loco | 165 | 45 | 5 | 8 | 12.889 | descriptive |
| without_inferred-lag7-ratio10 | loco | 170 | 45 | 11 | 8 | 26.222 | descriptive |
| without_inferred-lag7-ratio10 | loco | 175 | 45 | 20 | 6 | 45.778 | descriptive |
| without_inferred-lag7-ratio10 | loco | 180 | 45 | 25 | 0 | 55.556 | descriptive |
| without_inferred-lag7-ratio10 | loco | 185 | 45 | 25 | 0 | 55.556 | descriptive |
| without_inferred-lag7-ratio10 | loco | 190 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | loco | 195 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | loco | 200 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 140 | 45 | 0 | 18 | 4.000 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 145 | 45 | 0 | 17 | 3.778 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 150 | 45 | 0 | 14 | 3.111 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 155 | 45 | 0 | 12 | 2.667 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 160 | 45 | 3 | 10 | 8.889 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 165 | 45 | 5 | 8 | 12.889 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 170 | 45 | 11 | 8 | 26.222 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 175 | 45 | 20 | 6 | 45.778 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 180 | 45 | 25 | 0 | 55.556 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 185 | 45 | 25 | 0 | 55.556 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 190 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 195 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | lofo | 200 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 140 | 45 | 0 | 18 | 4.000 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 145 | 45 | 0 | 17 | 3.778 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 150 | 45 | 0 | 14 | 3.111 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 155 | 45 | 0 | 12 | 2.667 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 160 | 45 | 3 | 10 | 8.889 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 165 | 45 | 5 | 8 | 12.889 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 170 | 45 | 11 | 8 | 26.222 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 175 | 45 | 20 | 6 | 45.778 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 180 | 45 | 25 | 0 | 55.556 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 185 | 45 | 25 | 0 | 55.556 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 190 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 195 | 45 | 26 | 0 | 57.778 | descriptive |
| without_inferred-lag7-ratio10 | prequential | 200 | 45 | 26 | 0 | 57.778 | descriptive |
| with_inferred-lag10-ratio5 | loco | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag10-ratio5 | loco | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag10-ratio5 | loco | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag10-ratio5 | loco | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag10-ratio5 | loco | 160 | 50 | 3 | 10 | 5.000 | descriptive |
| with_inferred-lag10-ratio5 | loco | 165 | 50 | 9 | 8 | 10.600 | descriptive |
| with_inferred-lag10-ratio5 | loco | 170 | 50 | 13 | 8 | 14.600 | descriptive |
| with_inferred-lag10-ratio5 | loco | 175 | 50 | 22 | 6 | 23.200 | descriptive |
| with_inferred-lag10-ratio5 | loco | 180 | 50 | 27 | 0 | 27.000 | descriptive |
| with_inferred-lag10-ratio5 | loco | 185 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | loco | 190 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | loco | 195 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | loco | 200 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 160 | 50 | 3 | 10 | 5.000 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 165 | 50 | 9 | 8 | 10.600 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 170 | 50 | 13 | 8 | 14.600 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 175 | 50 | 22 | 6 | 23.200 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 180 | 50 | 27 | 0 | 27.000 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 185 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 190 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 195 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | lofo | 200 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 160 | 50 | 3 | 10 | 5.000 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 165 | 50 | 9 | 8 | 10.600 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 170 | 50 | 13 | 8 | 14.600 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 175 | 50 | 22 | 6 | 23.200 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 180 | 50 | 27 | 0 | 27.000 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 185 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 190 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 195 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio5 | prequential | 200 | 50 | 31 | 0 | 31.000 | descriptive |
| with_inferred-lag10-ratio20 | loco | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag10-ratio20 | loco | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag10-ratio20 | loco | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag10-ratio20 | loco | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag10-ratio20 | loco | 160 | 50 | 3 | 10 | 14.000 | descriptive |
| with_inferred-lag10-ratio20 | loco | 165 | 50 | 9 | 8 | 37.600 | descriptive |
| with_inferred-lag10-ratio20 | loco | 170 | 50 | 13 | 8 | 53.600 | descriptive |
| with_inferred-lag10-ratio20 | loco | 175 | 50 | 22 | 6 | 89.200 | descriptive |
| with_inferred-lag10-ratio20 | loco | 180 | 50 | 27 | 0 | 108.000 | descriptive |
| with_inferred-lag10-ratio20 | loco | 185 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | loco | 190 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | loco | 195 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | loco | 200 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 160 | 50 | 3 | 10 | 14.000 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 165 | 50 | 9 | 8 | 37.600 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 170 | 50 | 13 | 8 | 53.600 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 175 | 50 | 22 | 6 | 89.200 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 180 | 50 | 27 | 0 | 108.000 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 185 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 190 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 195 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | lofo | 200 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 140 | 50 | 0 | 18 | 3.600 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 145 | 50 | 0 | 17 | 3.400 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 150 | 50 | 0 | 14 | 2.800 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 155 | 50 | 0 | 12 | 2.400 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 160 | 50 | 3 | 10 | 14.000 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 165 | 50 | 9 | 8 | 37.600 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 170 | 50 | 13 | 8 | 53.600 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 175 | 50 | 22 | 6 | 89.200 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 180 | 50 | 27 | 0 | 108.000 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 185 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 190 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 195 | 50 | 31 | 0 | 124.000 | descriptive |
| with_inferred-lag10-ratio20 | prequential | 200 | 50 | 31 | 0 | 124.000 | descriptive |

| Data, lag, ratio | Candidate proposals | Screened out | Candidates submitted / cap | Baselines registered | Inner models / screened out | Updates accepted / rejected |
|---|---:|---:|---:|---:|---:|---:|
| with_inferred-lag10-ratio10 | 3 | 1 | 2 / 16 | 9 | 4428 / 123 | 343 / 147 |
| without_inferred-lag10-ratio10 | 3 | 1 | 2 / 16 | 9 | 4053 / 87 | 330 / 110 |
| with_inferred-lag7-ratio10 | 3 | 1 | 2 / 16 | 9 | 4428 / 123 | 339 / 151 |
| without_inferred-lag7-ratio10 | 3 | 1 | 2 / 16 | 9 | 4053 / 87 | 338 / 102 |
| with_inferred-lag10-ratio5 | 3 | 1 | 2 / 16 | 9 | 4428 / 123 | 350 / 140 |
| with_inferred-lag10-ratio20 | 3 | 1 | 2 / 16 | 9 | 4428 / 123 | 343 / 147 |

At the registered primary default-lag, with-inferred LOCO evaluation, the recorded gate verdicts are:
agent_logistic: supported lower mean cost than fixed_temperature, physics_only, two_node_physics; superiority not established against always_escalate, paper_table3, delay_lookup_refit, temperature_refit, always_observe_30. These verdicts retain the registered hard-rule comparators; the descriptive cost-rule and clustered comparisons limit their scientific interpretation.
agent_threshold: supported lower mean cost than fixed_temperature, physics_only, two_node_physics; superiority not established against always_escalate, paper_table3, delay_lookup_refit, temperature_refit, always_observe_30. These verdicts retain the registered hard-rule comparators; the descriptive cost-rule and clustered comparisons limit their scientific interpretation.

Muse-only has 0 recorded held-out predictions. A missing comparator is not evidence of superiority.

The decisive comparator is always_escalate: the learned policies do not establish an advantage over it in the registered primary analysis. A cost-aware rule can outperform classification rules under the registered asymmetric costs without learning useful physical discrimination. The clustered test and cost-rule-baseline sensitivity must accompany any account of the primary wins.

LOFO is a known transfer failure; format, chemistry and cell design cannot be separated here. Inspect unsafe errors and missed rescues alongside cost. Published-data reuse, reconstructed events, finite observation windows, uncertain kinetic priors and indistinguishable boundary pairs limit the claims. These are experimental operating procedures, not validated fire-safety instructions.

Reproduce offline into a chosen directory: `uv run translucent bench nist --out /path/to/output`. The command prints the paths it wrote. The default remains `evals`; use a separate directory to preserve committed reports. Select one split with `--split loco`, `--split lofo` or `--split prequential`. `--with-inferred` or `--without-inferred`, `--lag-s 7` or `--lag-s 10`, and `--unsafe-ratio 5`, `10` or `20` restrict the registered sensitivity plan. Ledger paths in result JSON exist after rerunning; they are not committed. Verify a generated ledger with `uv run translucent verify PATH`, or verify the committed self-contained web fixture directly.
