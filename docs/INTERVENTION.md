# Recorded heating-interruption research

Translucent asks: at the recorded heater cut, is ignition still likely enough to escalate? This benchmark scores
that decision against recorded single-cell outcomes. It cannot measure the efficacy of escalation, establish a
causal effect for another stop time on the same cell, or authorize a physical test. Proposed schedules are for
human review and remain simulated proposals.

The committed input is [NIST mds2-4027](https://doi.org/10.18434/mds2-4027), used with its NIST open licence and
original disclaimer. Outcome labels are transcribed from the table colours in [Tam et al., *Applied Thermal
Engineering* 298, 131107](https://doi.org/10.1016/j.applthermaleng.2026.131107). The paper PDF is not redistributed.
The workbook has 77 experiments, two failed thermocouples and 26 continuous-heating controls. Exp3 and Exp17 are
dropped, leaving 50 interrupted traces, including 19 finite-window rescues. All 26 continuous trials ignited;
25 have traces. The two cell formats also differ in chemistry and capacity, so format is not an isolated effect.

The library reconstructs heater-off from a 20-second slope break minus the registered thermocouple lag (10 seconds by default, with a 7-second sensitivity). When
that fails, it uses the condition mean vent time plus the published delay. Exp28, 35, 38, 43 and 46 carry this
inferred-off flag. Vent time is reconstructed heater-off minus delay. These are external annotations from the
complete recording, not online event detectors. Every derived event carries its source. The 12 rescued traces
that were later reheated are cut at the render-safe V-shaped minimum. Exp67 and Exp73 are censored finite-window
rescues; a sensitivity excludes them. Exp33 has suspect SOC. There is no claim of indefinite stability. The supplied Muse Spark image reading corroborates the vector-layer outcome labels against the trace ignition detector; labels and agreement are exposed only after reveal. Muse Spark and SAM 3.1 video timing motivates the registered 7-second lag sensitivity. Only CSV/JSON facts and model text recordings are redistributed; publisher images are excluded.

The protocol is registered before scoring. The owner's second amendment fixes mean decision cost as the primary endpoint before any Muse Wonder campaign is scored. Unsafe stand-downs, missed rescues, Brier and clipped log-loss, accuracy with Wilson 95% intervals, abstentions and observation cost are reported for every policy. The splits are
leave-one-condition-out, leave-one-format-out and prequential Exp-number order, never random trials. Each inferred
trial sensitivity refits from scratch. Condition-block bootstrap intervals reflect this small, grouped study;
prediction intervals are conditional on the selected feature spec and do not cover all selection or event error.

The generic feature library generates level, slopes, rises, curvature and vent-relative measurements over
10, 20, 30, 60 and 120 seconds, plus declared metadata. A nested training-only LOCO screen selects candidates.
The validation reference logistic using level, format, a 30-second slope and a 60-second rise is reproduced in a
regression test; its feature choice does not select the agent's candidate from held-out outcomes. The frozen
published Table-3 lookup is explicitly retrospective. The separate fold-refitted delay lookup and temperature
threshold reproduce the validation headline numbers: unsafe/missed 6/11 and 3/12; the reference logistic is 2/5.

World models predict ignition probability and an interval. All model forecasts are labelled `simulated: true`;
recorded reveal outcomes carry `simulated: false`. Separate decision rules apply registered costs:
100 for unsafe waiting or stand-down, 0.05 per watched second, 10 per unnecessary escalation and 10 per abstention.
Escalation on a trial that ignited costs zero. A 30-second watch costs 1.5 and a 60-second watch costs 3, so
observation can be useful. The decision rule compares expected action costs; watch values are explicitly
optimistic forecasts, and inner revealed-data CV measures the full watch-and-terminal policy before submission.
The generic post-off features use only the window the policy paid for. Always-observe-30 followed by a trained
threshold is a separate baseline. Ignition during a watch counts as unsafe even if the terminal action escalates.
The report refits cost sensitivities at unsafe-to-escalation ratios 5:1 and 20:1 with the watch cost fixed. A later
probability after an observed ignition never masquerades as a pre-ignition forecast.

The one-node model is a deliberately simple thermal baseline. The two-node solver infers an unmeasured core
state from surface temperature, using a grid of Arrhenius priors fitted only on same-condition continuous trials.
Its kinetic parameters, source traces and ignition annotations are recorded before interrupted scoring. Core
state, forecasts for stopping/observing and alternative-delay forecasts are all `simulated: true`. The model is
non-identifiable and its ensemble intervals are not calibrated physical guarantees. Its ignition failures are
reported in [the benchmark](../evals/INTERVENE.md); a hybrid physics-prior feature competes only inside training
folds. No forecast alone can support a claim.

In prequential runs, a new model is a candidate after measured feedback. The old and new model/rule pairs are
executed on all revealed history. The candidate adds no unsafe errors and cannot worsen five-bin calibration
error or Brier score beyond 0.01. Rejected candidates are recorded and the previous fit remains active. The action
rule stays fixed and is revalidated, rather than being fitted to held-out outcomes. This conservative check can
freeze a poor but compatible model; that limitation is visible in the update history and scores.

The scripted research loop has no outcome-file access. It forms three competing hypotheses: surface temperature
tracks internal state; self-heating can continue after heater-off; stored energy through SOC controls ignition.
Their explicit world models disagree on proposed conditions and stop delays. Proposals rank the Bernoulli
Jensen-Shannon divergence under equal hypothesis weights, an information gain about hypothesis identity rather
than high outcome entropy alone. The design record contains the grid, training/reveal ids, fitted models and
all candidate predictions. Top proposals give each hypothesis's probability and a schedule: heater on at 41 W,
detect vent, heater off at vent plus the proposed delay, watch at least 400 seconds, then reheat to render safe.
Those schedules have not been executed or validated for safety.

Only screened agent strategies enter held-out scoring, capped at K=16. The shipped campaign submits two
agent strategies and registers nine frozen comparators, including the unrun Muse-only replay comparator.
Comparators do not consume K. The owner's third pre-registration amendment, dated 2026-10-30 at `2fed2df`
before live planner scoring, fixes the correction family to agent submissions times comparators: 2 × 9 = 18.
This counts the actual policy search and keeps fixed references from inflating the agent-search penalty.
All LOCO/LOFO agent learning strategies and decision rules are proposed before any outcome is revealed;
fitting and generic candidate selection then use only each fold's revealed training data. This also prevents
feedback from an earlier fold from selecting a global strategy that later scores those same physical trials.
Submissions close before the first scored decision. Inner candidate fits are counted separately.

Primary comparisons use the paired mean-cost difference: enumerate every sign flip for at most 20 nonzero
paired differences, otherwise draw 100,000 seeded flips. Holm correction covers the 18 registered agent–baseline
comparisons. Unsafe-error McNemar remains descriptive; the secondary two-part claim requires no more observed
unsafe errors and fewer missed rescues, with exact McNemar on missed rescues. This observed safety inequality
does not prove noninferiority. Six unanimous nonzero paired differences are needed for nominal p < 0.05 under the two-sided sign-flip test; ten are needed for an 18-member family's first Holm threshold. Unequal costs and mixed signs may need more.
The full loop says plainly when it fails to beat a simpler baseline after correction. LOFO remains a known
transfer failure. Probability loss is useful even when action costs hide it.

Each fold has salted outcome commitments. Training labels require a prior unscored commitment and reveal;
held-out decisions and paid watches precede reveal. Offline verification rechecks seals, order, partitions,
updates, scores, exact tests and gate verdicts. The self-contained fixture carries source artifacts and the
original insert-only SQLite receipt. This is an internal consistency proof, not external timestamp notarization.
A privileged operator can read the public paper and raw recordings; secrecy is enforced only at the scoped
planner tool boundary. The public read-only API and exported receipt are audit surfaces.

The benchmark does not call a provider. Scripted planners and exact recorded replay exercise the same scoped
tools as a later metered planner. Muse-only is marked not run until exact pre-off-summary recordings exist.
The resulting procedure renders the declared world model and decision rule with supported measured counts, cost evidence, claim ids and limits; supported counts do not establish a safe operating guarantee. See [API.md](API.md) for commands and [CONTRACTS.md](CONTRACTS.md) for the bundle and ledger contracts.

## Related work and alternatives

The owner's supplied literature validation motivates the architecture: [Asking the World](https://arxiv.org/abs/2609.39135)
uses executable worlds and probes; [RSI-Anything](https://arxiv.org/abs/2610.13912) motivates compatibility-checked
model/policy retention; [AbsorbEvo](https://arxiv.org/abs/2610.01119) motivates cheap screening before expensive
submissions; [CausaLab](https://arxiv.org/abs/2605.26029) distinguishes predictive success from recovering a
mechanism. Here, simulated probes and mechanism discrimination are explicitly separated from measured evidence.
These connections are design inspirations, not replications of those systems or claims about their performance.

Other battery data have different targets. [Attia et al.](https://doi.org/10.1038/s41586-020-1994-5) report nine
charging protocols with five final validation lifetimes each; many optimization-round lifetimes were predictions.
[Severson et al.](https://doi.org/10.1038/s41560-019-0356-8) concern cycle-life prediction.
The [2026 biaxial-stress study](https://doi.org/10.1038/s41586-026-10734-x) has few figure-level cells and no broad
compression-level sweep. These are possible separate case studies, not interchangeable labels for heating rescue.
Simulators such as PyBaMM may supply priors, but their outputs cannot become real outcome evidence in this gate.
