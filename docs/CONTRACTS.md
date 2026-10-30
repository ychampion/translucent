# Contracts

Contract version: 2

The records in [`translucent/contracts.py`](../translucent/contracts.py) are shared by the ledger, the tools, the
API, replay bundles and the web app. The JSON Schema of a replay bundle is
[`replay-bundle.schema.json`](replay-bundle.schema.json).

Rules that keep old evidence readable:

- New fields are optional and have defaults. A bundle exported under version 1 (`contract_version: "1"`) still loads
  and verifies; the web app must render it without the newer fields.
- New record kinds are new `kind` values in the ledger's single insert-only `record` table. No table or trigger is
  added, so every existing ledger and exported `verification/ledger.sqlite` keeps passing `translucent verify`.
- Recorded gate reviews must re-check to the same verdict, checks and reason. `tests/test_fixture_compat.py` holds
  the expected `translucent verify` output for every exported fixture and checked-in ledger.

## Version 2 additions to the replay bundle

TypeScript shapes for the web mirror (`web/lib/contracts.ts`). Every field is optional on the reading side.

```ts
interface ReplayBundle {
  // ...version 1 fields...
  runs?: Run[];            // tool runs behind the bundled evidence (Run as in the v1 schema)
  forks?: Fork[];          // this campaign's own fork (when it is a what-if branch) and forks made from it
  protocol?: Protocol | null;  // the campaign's pre-registered metrics
  decisions?: Decision[];  // declared winners and "no winner yet" outcomes
  experiment?: ExperimentRecord | null;  // what the lab recorded alongside the video
}
```

`measurements` now holds the full evidence behind every finding, which may include measurements recorded by another
campaign (a what-if branch cites its parent's outlines). `artifacts` also holds the files the bundled runs read or
wrote: analysis code (`kind: "code"`), the error summary of a failed run (`kind: "text"`, label "Error trace": the
exception type and message only, at most 2 KB, paths made relative, secrets removed), the image a
second opinion looked at (`kind: "plot"`, on the critic review's `artifact_ids`) and the campaign's setup record
(`kind: "report"`, label "Setup").

### Records that changed meaning, not shape

- A `run_analysis_code` run lists its code as its last `input_ids` entry, an artifact whose id is
  `sha256:<code_sha>`.
- A failed run (`status: "tool_failure"`) lists its error trace in `output_ids`. A failed run's output never backs a
  measurement.
- A critic `Review` lists the image it was shown in `artifact_ids`.
- The `campaign.started` event cites the setup record in `refs` and `data.setup_id`. The setup record is JSON:

```ts
interface Setup {
  code: { files: Record<string, string>;      // sha256 of gate.py, tools.py, analysis.py, instruments.py, verify.py
          git: { commit: string; changed_since_commit: boolean } | { unavailable: string } };
  python: string;
  packages: Record<string, string>;
  profile: string;
  models: { planner: string; pointer: string; second_opinion: string };
  memory: "on" | "off";
  budget_usd: number;
  recordings: { video_id: string; experiment: string | null;
                record_sha256?: Record<string, string> | { unavailable: string } }[];
}
```

`translucent verify` prints one informational line when a checked campaign has a setup record: whether the gate code
re-checking the findings is the code they were recorded with. It is never a problem.

## Evidence graph

`GET /api/campaigns/{id}/graph` and `translucent.graph.build` (a pure function over the bundle's lists; the web app
can port it) return:

```ts
interface Graph {
  campaign_id: string;
  nodes: { id: string; kind: "question" | "finding" | "review" | "measurement" | "run" | "artifact" | "recording" |
           "lesson" | "note"; label: string; [extra: string]: unknown }[];
  edges: { from: string; to: string; kind: "answers" | "cites" | "replaces" | "forked_from" | "reviewed_by" |
           "shows" | "produced_by" | "calibrated_by" | "computed_from" | "read" | "output_of" | "derived_from" |
           "taught" }[];
}
```

Node extras: a finding has `status` and `predicate`; a run has `status`, `model`, `provider`, `cost_usd`, `code_sha`;
a measurement has `quantity`, `value` (display text), `unit`, `subject`; an artifact or recording has
`artifact_kind`, `media_type`, `citation`; a review has `reviewer`, `verdict`, `author`, `model`. The question node's
id is `question:<campaign id>`; a person's note is `note:<campaign id>:<seq>`.

## Compare ("what changed")

`translucent.diff` diffs two campaigns, two findings or two runs, from a ledger or from two exported bundles
(`View.from_bundle`; a v1 bundle's runs are rebuilt from its `tool.started`/`tool.finished` events). The expected
output for the fixture pair `m4-meta` vs `m4-team` is `tests/data/diff-m4-meta-vs-m4-team.json`; a web port should
reproduce it exactly. Numbers are formatted like Python's `:.4g` (values, ranges) and `:.2g` (uncertainties).

```ts
interface CampaignDiff {
  kind: "campaign"; a: string; b: string;
  models: { a: Record<string, string>; b: Record<string, string> };
  tool_calls: { a: number; b: number; same_first_calls: number; a_next: string | null; b_next: string | null };
  cost_usd: { a: number; b: number };
  lessons_loaded: { a: string[]; b: string[] };
  findings: { a: string; b: string; a_statement: string; b_statement: string; a_verdict: string;
              b_verdict: string; sentences: string[]; changes: Change[] }[];
  only_a: { id: string; statement: string; verdict: string }[];
  only_b: { id: string; statement: string; verdict: string }[];
  sentences: string[];
}
interface FindingDiff { kind: "finding"; a: string; b: string; changes: Change[]; sentences: string[] }
interface RunDiff { kind: "run"; a: string; b: string; changes: Change[]; sentences: string[] }
type Change = { field: string; a?: unknown; b?: unknown; [extra: string]: unknown };
```

Findings pair when they use the same test type and cite the same quantities (a derived number counts by its unit),
their accepted ranges have the same sign and centres within half their size; nearest range first, a finding's
latest version before the one it replaced, then recording order.

## What-if branches (record kind `fork`)

```ts
interface Override {
  quantity: "spatial_scale" | "time_base_interval" | "voltage_offset_s" | "short_threshold_v" | "finding_window";
  value: number | number[];      // finding_window: [lo, hi] or one tolerance
  uncertainty: number;           // default 0, at least 0; every number finite (NaN and Infinity are refused)
  unit: string;                  // spatial_scale: "px/mm" | "um/px"; seconds; "V"; or the finding's unit
  replaces: string | null;       // the measurement it stands in for, when there is one
}
interface Fork {
  id: string;                    // "fork_<hex>"
  campaign_id: string;           // the branch campaign
  parent_campaign_id: string;
  claim_id: string | null;       // the parent's finding being re-examined
  author: string;                // always a person
  reason: string;
  overrides: Override[];
  created_at: number;
}
```

New event type `fork.created` (actor `person:<author>`, `refs: [fork id, claim id, branch campaign id]`,
`data.fork`), recorded in both the branch and the parent. A rerun branch's stream is `fork.created`,
`campaign.started` (`data: {mode: "rerun", fork_id}`), the re-run tools' usual events, `claim.proposed`,
`claim.reviewed`, and `campaign.finished` with `data: {status: "done" | "failed" | "needs_agent", reason,
claim_id, verdict, reran: string[], cited: string[]}`.

- A branch's finding cites the parent's clean measurements directly (they are in the branch bundle's
  `measurements` and `runs`), and has `supersedes: null`: a branch is an alternative, not a replacement.
- Runs re-executed under a changed assumption carry `params.assumption = <fork id>`. Their measurements have
  `valid_domain: "declared by <author> in a what-if branch: <reason>"`, which the checker's "scale and timing
  recorded" detail repeats.
- The branch bundle also carries the re-examined parent finding and its reviews' evidence (not in `claims`; its
  measurements and runs are in the lists), so the original and the what-if can be shown side by side. In the
  evidence graph the branch's finding has a `forked_from` edge to it.
- An exported branch's `verification/ledger.sqlite` may hold only the records this campaign's evidence needs
  (`translucent.replay.export(..., evidence_only=True)`); it verifies the same way.

`translucent verify` reports, only when forks exist: "Fork <id> overrides a quantity its finding never used" and
"Branch measurement claims a what-if it was not created under (run <id>)". A value a person declared in a what-if (a
run with `params.assumption`) counts as a calibration on record only for findings of that branch: a finding in any
other campaign that leans on it fails "scale and timing recorded". For a branch's finding, its declared scale and a
scale recorded outside the branch are never in conflict; within the branch a disagreement is still one.

## Pre-registered metrics and decisions (record kinds `protocol` and `decision`)

```ts
interface Metric {
  id: string;                    // "growth" or "metric_1"
  name: string;
  quantities: string[];          // accepted measurement quantities; "derived:*" accepts any computed one
  unit: string;
  predicate: { type: "value_within" | "events_coincide" | "trend"; params: Record<string, number | string>;
               measurement_ids: [] };
  min_methods: number;           // default 2
  needs_verification: boolean;   // default true
}
interface Protocol { id: string; campaign_id: string; author: string; metrics: Metric[]; created_at: number }
interface Decision {
  id: string;                    // "decision_<hex>"
  campaign_id: string;
  metric_id: string;
  outcome: "winner" | "tie" | "tradeoff" | "insufficient_evidence";
  winner_claim_id: string | null;
  verification_claim_id: string | null;
  attempt_claim_ids: string[];   // every attempt at the metric, on protocol or not, refused or not
  replication_run_ids: string[]; // every replication run of the winner, failed ones included
  checks: Check[];               // "metric pre-registered", "methods attempted", "winner on protocol",
                                 // "verified by an independent run", "every replication succeeded", "methods agree"
  created_at: number;
}
```

New optional fields on existing records: `Claim.metric_id?: string | null` (the metric a finding attempts) and
`Lesson.contradicts?: string | null` (a lesson this one's evidence speaks against).

- A protocol is admitted only before its campaign has any event of type `campaign.started` or any record other than
  the campaign itself, and only if every metric parses under the strict grammar above. The `campaign.started` event
  cites it in `refs` and `data.protocol_id`.
- A finding is on protocol when its predicate type and params equal the metric's and it cites a measurement of an
  accepted quantity. The checker's verdict never depends on this.
- A method is the tool and analysis code (`code_sha`) of the run that produced the finding's metric measurement.
- Independent: the runs a value passed through (following measurement inputs, calibrations and the runs that made
  each file, preferring the maker in the consuming run's own step, else makers recorded before it) share nothing
  but the experiment's own record lookups.
- Replication runs carry `step_id: "replicate:<claim id>:<n>"`.

## The recorded experiment (`ReplayBundle.experiment`)

```ts
interface SetupFact {
  status: "available" | "unavailable";
  value: string | null;          // e.g. "161.2 ± 4 px/mm", "490 frame times, bound ± 0.5 s", "1200 readings"
  source: string | null;         // a figure, a log, a lab's manifest
  method: string | null;
  declared_by: string | null;    // the person who declared it, for a lab's own manifest
  reason: string | null;         // when unavailable, e.g. "no recorded clock: playback frame timing is not real time"
}
interface ExperimentRecord {
  id: string;                    // "reisecker2023/movie4", "lu2023/movie1" or "user/<id>"
  title: string | null;          // the published caption, or the manifest's title
  citation: string | null;
  licence: string | null;
  author: string | null;         // the manifest's author for a lab's own experiment
  scale: SetupFact;
  clock: SetupFact;
  voltage: SetupFact;
  layout: string | null;         // where the sample sits in the frame
  notes: string | null;
  files: Record<string, string>; // recorded file name -> sha256
}
```

Filled by `translucent.replay.export` from the experiment's records (`translucent.instruments.record`); absent when
the records are missing or changed since registration. It is description, not evidence: verification does not depend
on it.

## Memory a campaign starts with (`memory.loaded`)

A campaign loads only lessons that concern it, at most 6:

- **same recording**: scope `recording:<video sha>` or `setup:<video sha>`, or evidence traced to this video;
- **same source**: scope `source:<paper>` (e.g. `source:reisecker2023`), or evidence from another movie of the same
  source;
- **method**: scope `method:<anything>`.

Lessons learned on another paper's movie with any other scope are not loaded. Confidence is derived on read:
`supported` when the lesson's evidence spans two or more campaigns (or the same text was learned in another
campaign), `rejected` when a later lesson names it in `contradicts` and cites a finding the checker supported or
refuted, otherwise `tentative`. Rejected lessons are never loaded; supported ones come first. The agent sees them as
untrusted priors to check before use. A `setup:<video sha>` lesson is offered with its measurement ids, so the agent
can cite a confirmed calibration directly; the checker still decides whether it is a trusted record.

The `memory.loaded` event's `data` gains `why: [{id, reason: "same recording" | "same source" | "method",
confidence: "supported" | "tentative"}]` beside `lessons` (their texts) and `refs` (their ids).

## Intervention campaigns (`ReplayBundle.intervention`, optional v2)

The existing movie records and checker retain their meanings. `gate.py` stays byte-identical so its old setup
receipts and CLI code notes also remain unchanged. Activating the intervention controller registers a small
dispatch adapter: legacy claims go to the original function; the new record kind goes to the intervention gate.
Intervention work uses a new ledger record kind,
`intervention`, with an immutable `type`, `data`, campaign event `seq`, previous record hash and SHA-256 digest.
Each insert and its `intervention.recorded` event commit atomically. The protocol precedes `campaign.started`.
The original protocol machinery also registers the primary decision-cost metric before any work starts. The owner's second pre-registration amendment replaces the original unsafe-only primary before any Muse Wonder campaign is scored. Amendment 3, registered on 2026-10-30 at `2fed2df` before live planner scoring, fixes multiplicity to submitted agent strategies times frozen baseline comparators: comparators do not consume K or inflate the agent search count. With two agent submissions and nine comparators the family has 18 members.

An intervention bundle has `video: ""`, `video_size: [0, 0]`, `fps: 0` and this optional field. Existing v2 bundles
load with `intervention: null`. Nested objects permit additional documented provenance keys; the outer contract
is frozen and rejects unknown fields.

```ts
intervention: {
  system: {id, title, citation, doi, licence, sensor, decision_point},
  protocol: {primary_metric, secondary_metrics[], splits[], heldout_cap_K, min_trials, registered_seq,
             costs: {unsafe, watch_ignition, watch_per_second, unnecessary_escalation, abstain},
             amendment, heater_off_lag_s, unsafe_ratio,
             probability_atol, numerical_version, verification_version, require_baseline_screens, bootstrap_generator, seed,
             primary_test: {name, exact_max_discordant, resamples, seed, correction_family},
             sensitivity_plan, preregistration_reasons[],
             observe_options: [30, 60], calibration_tolerance, calibration_metric, calibration_bins[],
             funnel: {screen, max_inner_unsafe_fraction, min_train_trials,
                      proposed, screened_out, submitted, pending,
                      candidate_models_proposed, candidate_models_screened_out, unit}},
  trials: [{id, trial_key, scope, condition: {format, soc}, delay_after_vent_s, series_artifact,
            t_heater_on, t_vent, t_heater_off, t_ignition: number|null, truncated_at: number|null,
            flags[], event_sources, outcome: string|null, seal_version, sealed_hash, revealed_seq: number|null,
            corroboration: {image, vector, trace, agree}|null,
            corroboration_sources: [{path, sha256}], corroboration_source: string|null}],
  hypotheses: [{id, text, mechanism, policies[], world_model, seq}],
  policies: [{id, name, kind: "baseline"|"agent", family, features[], spec, world_model,
              decision_rule, simulated, proposed_seq}],
  decisions: [{id, trial_id, policy_id, split, p_ignite, interval: [lo, hi], decision,
               initial_action, observe_s, watch_time_s, ignited_during_watch,
               committed_seq, terminal_committed_seq, revealed_seq, correct, unsafe,
               fit_id, final_decision_id}],
  scores: [{id, seq, policy_id, split, subset, n, unsafe, missed, abstain,
            brier, logloss, accuracy, accuracy_interval,
            unsafe_interval, missed_interval, abstain_interval, brier_interval, logloss_interval,
            watch_time_s, watch_requested_s, ignited_during_watch, total_cost, mean_cost, mean_cost_interval,
            per_trial_costs: [{trial_key, cost, decision_id, reveal_id}], decision_ids[], reveal_ids[], status,
            partial?: true, campaign_status?: string, pending_decision_ids?: string[]}],
  comparisons: [{id, seq, a, b, split, subset, a_only_correct, b_only_correct,
                 p, p_holm, candidates_submitted, baselines_submitted, family_size, metric,
                 a_mean_cost, b_mean_cost, mean_cost_difference, unsafe_mcnemar_p, missed_p, missed_p_holm,
                 a_unsafe, b_unsafe, a_missed, b_missed, secondary_two_part,
                 discordant, method, assignments, seed, monte_carlo_se,
                 per_trial_differences: [{trial_key, difference}]}],
  updates: [{id, policy_id, after_trial, accepted, reason, seq,
             previous_fit_id, candidate_fit_id, applied_fit_id,
             previous_rule, candidate_rule, old_predictions[], new_predictions[]}],
  procedure: {text, rule, claim_ids[], limits[]} | null,
  next_experiments: [{id, condition, heater_off_after_vent_s, p_ignite, interval,
                     why, status: "proposed", simulated: true, disagreement_bits,
                     per_hypothesis: [{hypothesis_id, p_ignite}], schedule, design_id, seq}],
  folds: [{id, scope, split, train_ids[], test_ids[], seq}],
  claims: [{id, predicate, seq}], reviews: [{id, claim_id, verdict, reason, computed, seq}],
  references: [{id, name, n, ignited, ignition_interval, traces[], source, limit, seq}],
  physics_priors: [{id, condition, ensemble[], source_trials[], sources[], simulated: true, seq}],
  receipt: {version: 1, campaign_id,
            ledger: {sha256, bytes, encoding: "gzip+base64", data},
            blobs: {"sha256:<hex>": {sha256, bytes, encoding: "gzip+base64", data}}} | null
}
```

`trials[].id` identifies a fold-scoped salted commitment; `trial_key` identifies the physical experiment. Each
LOCO/LOFO fold gets fresh seals so its training tools cannot borrow another fold's held-out label. Prequential
folds share seals and reveal in Exp-number order. Scores deduplicate physical trials within policy and split. Runtime `advance` iterates the registered condition or format groups with fresh sealed scopes; training reveal and fitting repeat inside each scope, while held-out submissions stay frozen. The scripted final claim aggregates all folds.
Outcome-dependent ignition, truncation, censor flags and corroboration labels appear in this projection only after reveal. `corroboration.agree` means the supplied Muse Spark image reading, vector-layer label and binary trace ignition detector agree; it does not establish a mechanism or indefinite rescue. The visual source README distinguishes binary ignition agreement from delayed-ignition trace shape. No private
sealed-store file is exported. New trials record `seal_version: 2`: the seal is
`sha256(canonical({outcome, t_ignition, flags, truncated_at, event_sources}) || UTF8(salt))`.
Reveal admission and offline verification check the complete payload. Legacy trials without a version,
or explicitly marked v1, retain `sha256(UTF8(outcome + salt))`. NIST verification also reconstructs events
and truncation from the content-addressed source workbook and checks that paid observations do not show
an ignition before the revealed ignition time. A paid watch on an ignited trial requires that time.

A scored episode starts with a committed `stand_down`, `escalate`, `abstain` or `observe` action. Observations
release only recorded samples through heater-off plus the paid 30 or 60 seconds. A watch requires a terminal
stand-down/escalate commitment before label reveal. Ignition inside that window is unsafe regardless of the
terminal action, and its probability loss uses the initial forecast. Otherwise loss uses the terminal forecast.
`observe_s` is the requested window; `watch_time_s` stops at ignition if it occurs earlier. Cumulative watch time,
unsafe decisions, missed rescues and abstentions contribute the fixed protocol costs. Registered costs are 100 for unsafe stand-down or ignition during a watch, 10 for unnecessary escalation, 0.05 per watched second, and 10 for abstention (treated as escalation). Escalating on an ignited trial costs zero. The 5:1 and 20:1 unsafe-to-escalation sensitivities retain the same watch cost. Escalation classifies risk;
there is no recorded outcome for suppression by the agent. In-progress exports can contain raw unrevealed
commitments with `correct: null`; they cannot support scored claims.

Abstention must follow an optional `decision_rule.abstain_band: [lo, hi]` declared before scoring, applied
to the executable world-model probability. A planner cannot override a frozen action with an arbitrary
abstention. Terminal decisions after a watch remain stand-down or escalation. Performance claims require
complete coverage of the registered split's physical trial universe, so omitting difficult trials cannot
produce a supported partial-score claim. Intermediate scores remain available while the campaign runs.

Every model forecast, including a learned statistical probability, is labelled `simulated: true`.
Real reveal outcomes carry `simulated: false`; only those outcomes can ground scored evidence.
A policy spec declares a world-model family, allowed features, grouping, training constraint and optional nested
candidate set. Its separate decision rule maps probability to an action. Generic feature windows are generated
before scoring. Post-off features require a paid observation or revealed training feedback. A held-out submission
is a learning strategy with its frozen inner search and screen, counted once across folds. Inner candidate fits
are counted separately. Only screened agent strategies consume K. Frozen baselines are registered comparators, including the unrun Muse-only replay comparator; they do not consume the agent cap. In LOCO/LOFO every agent learning strategy and decision rule must be proposed before the first outcome reveal in any fold. This prevents training feedback from an earlier fold from choosing a global strategy that later scores that same physical trial. Fitting and inner selection still use each fold's revealed training data. Submissions close at the first scored decision, so later held-out feedback cannot
shrink or change the correction family for an earlier claim. The funnel's counts are a projection of immutable proposals, screens and submissions;
registration's zero counters are never edited. Its minimum training count is descriptive: the registered cold-start
branch escalates before feedback, and all nonempty revealed agent folds undergo inner CV.
New protocols record `verification_version: 3` and `require_baseline_screens: true`. Frozen baseline
screens document comparator registration with zero candidate models and always survive; they cannot
select or remove comparators using outcomes. A separate empty registration fold anchors these screens.
Opening a planner campaign registers the same nine frozen comparators as the benchmark, and the
controller executes available comparators before each held-out reveal. They are read-only to the planner;
the Muse comparator remains not run unless its paid, recorded runner supplies receipts.

Prequential candidate fits receive only earlier revealed trials. Both the old and candidate model/rule pairs are
re-executed on that history. An update is retained only if it adds no unsafe errors and neither five-bin expected
calibration error nor Brier score worsens by more than 0.01. Every acceptance and rejection names the measured
feedback and applied fit. A rejected update also retains the old bootstrap training partition for prediction
intervals. Planner commitments must match the executable world-model probability and interval and declared
action rule, with an explicit costed abstention allowed. Offline verification rebuilds every fit from the registered specification, exact training ids, revealed
training features, costs and seed. It compares the stored model's structure and values with that refit,
re-runs every training screen and its counts, and then re-executes point forecasts and action rules from
each fit and its own paid observation window. Non-prequential fold registration must precede every
training and test reveal; terminal decisions retain all parent identities, fit, fold, split and scored status. The current implementation keeps the decision rule fixed and revalidates it; it does
not silently optimize cost weights or the action threshold after an outcome.

New gate predicates are `policy_performance` (`policy_id`, `split`, `subset`, exact `n` and `unsafe`) and
`policy_beats` (`a`, `b`, `split`, `subset`). Performance reviews return the recomputed cost and all secondary
scores as well as the asserted counts. The comparison predicate tests the primary mean decision cost, using
paired per-trial cost differences from committed decisions and real revealed outcomes. A two-sided sign-flip
test enumerates every one of the 2^d flips when at most 20 differences are nonzero; otherwise it uses 100,000
seeded resamples. Its p-values are Holm-corrected over the ledger's submitted agent-strategy count times its frozen comparator count, including the unrun Muse-only comparator. Baseline-vs-baseline diagnostics do not enlarge this agent comparison family. A supported superiority claim requires lower mean cost and adjusted p < 0.05.

Post-hoc descriptive sensitivities extend the existing `scores` and `comparisons`
arrays without changing this registered analysis. Such records carry
`post_hoc: true`, `descriptive: true`, `analysis_version: 1`,
`outside_registered_holm_family: true`, `analysis_timing` and an explicit
`escalation_assumption`. They are not new submissions, prospective commitments,
or evidence of a new supported gate claim. Offline verification independently
recomputes each record from earlier committed forecasts and revealed outcomes.

- `sensitivity: "cost_rule"` scores apply the registered cost decision rule to
  each named baseline's original pre-off probabilities without refitting or
  watching. They retain the original `policy_id`, source decision and reveal ids,
  and add a distinct `variant_id`, `decision_rule`, `min_p` and `max_p`.
  Comparisons with agents use the same paired sign-flip mechanics, but their
  descriptive p values are unadjusted (`p_holm: null`) and outside the primary
  Holm family.
- `sensitivity: "condition_clustered"` comparisons flip whole-condition cost
  sums. They add `n_blocks`, `n_discordant_blocks` and
  `per_block_differences: [{condition, difference}]`. The descriptive Holm
  calculation uses the same agent-by-baseline family, including missing
  comparators at p=1; it cannot change the registered per-trial verdict.
- `sensitivity: "fixed_threshold_sweep"` scores add `threshold_c`, a distinct
  `variant_id`, and `threshold_provenance: "illustrative, unsourced"`. All cuts
  from 140 to 200 C in 5 C steps are reported. The primary 170 C cut is retained;
  no replacement is selected. Forecast metrics remain those of the original
  baseline probabilities; only the retrospective action and loss are varied.

These analyses are labelled "post-hoc sensitivity (added after the primary
results were known)". A `post_hoc_analysis_disclosure` reference records their
timing, the primary decision-rule asymmetry, and the unsourced temperature cut.
All cost summaries assume escalating on a trial that ignites costs zero;
escalation efficacy was not observed.
Unsafe-error McNemar is descriptive only. The secondary predicate `policy_no_more_unsafe_fewer_missed` (`a`, `b`, `split`, `subset`) reports no more observed unsafe
stand-downs and fewer missed rescues, with exact McNemar on missed-rescue discordances; the observed safety
inequality is not a noninferiority guarantee. It uses the same ledger-counted correction family. `a_only_correct` and `b_only_correct` remain the descriptive safety
discordances for compatibility. The full report gives the minimum unidirectional discordant counts needed for
nominal and family-adjusted significance; a small study can remain inconclusive despite large observed differences.
An insufficient sample, simulated evidence or unrecorded action has a plain unsupported reason.
Each predicate is schema-validated before recomputation. Required fields are `type, policy_id, split, n,
unsafe` for performance and `type, a, b, split` for either comparison; aliases `policy_id` and `baseline_id`
may replace `a` and `b`. Missing fields name the claim type, field and full required list. Only deliberate
claim-rule violations become unsupported verdicts; implementation and storage errors propagate. Only
incomplete paired observations contribute p=1 to the registered family. Reports label a win supported
only when a recorded `policy_beats` review supports it.

A procedure cites supported performance claims and renders the declared world-model spec, decision rule,
registered costs and observation options. It includes observed unsafe errors and missed rescues and never turns
a count claim into a zero-risk guarantee. Heater-off lag is registered (10 s default, 7 s sensitivity); inferred-trial
and cost sensitivities are separate registered refits. No forecast or proposed experiment is treated as measured.

The single-file fixture embeds the same SQLite receipt and content-addressed sources that folder exports use.
Its event list retains the sequenced record id/type/digest; the receipt has the full original events, model fits,
selection audits and observation records. Verification checks the receipt hashes, original insert-only triggers,
record/event hash chain, seals, commit/reveal ordering, fold separation, fit/update provenance and arithmetic;
then it regenerates every bundle field and requires equality. Decoding uses bounded decompression and fixed
content-addressed paths. GET projections omit the embedded receipt. This proves internal ordering and consistency,
not an external timestamp or secrecy from a privileged operator reading public source data.

New NIST protocols register `probability_atol: 1e-9` and `numerical_version: 2`.
Verification applies an absolute tolerance to executable probabilities and float leaves of refitted
models, including compatibility histories. Model structure, integer and categorical leaves remain exact. Older NIST receipts without the field use
the same bound. The measured kernel-induced maximum difference was
`3.430589146091734e-14`; the bound admits binary64 recomputation noise while
rejecting a `1e-6` change. Actions, paid watch durations, measured outcomes,
unsafe classifications and update acceptance must still match exactly. A
threshold crossing fails even within the probability bound and names the
trial, committed probability and recomputed probability. Existing legacy
campaigns use their original verification path and output.
At a stored interval endpoint, bounded recomputation noise may extend the
interval used for rule validation just enough to contain the recomputed
probability. The committed interval remains unchanged and must contain its
committed probability; the action still uses the recomputed probability.

Executable regression uses centered closed forms and fixed 16-step float64
IRLS with explicit sums and a scalar Cholesky solve. Fits do not call native
BLAS/LAPACK or adaptive optimizers. Bootstrap draws use NumPy `PCG64`; each fit
records its seed. Numerical version 2 also uses explicit reductions for score
losses. Verification caches parsed records, immutable source observations and
fully keyed arithmetic only within one read-only snapshot. All hashes,
provenance, decisions, compatibility verdicts and claims are still checked;
caches are discarded before another verification.

Live intervention recordings extend the request/response recording convention with
`kind: "chat"`, the full `request`, its `request_sha256`, normalized `response`,
`raw_response`, `provider`, requested/returned `model`, `usage`, `cost_usd`, an
optional `error`, `recorded_at` and a digest over the complete entry. Ordered
`chat/000001.json` files preserve separately metered retry attempts. The private
`manifest.json` fixes run settings and replay entropy; `decisions.json` retains the
exact original decision record bodies for strict replay comparison. New manifests name a separate
`run.json` completion receipt with status, reason, tool-call count, spending and recording count; replay
requires exact equality. Older manifests remain readable without this added receipt, but missing or
truncated tapes always fail replay. Paid costs are accounted before any recording serialization or I/O;
recordings publish atomically with a temporary file and `os.replace`. None contains
provider credentials. Replay validates every request and recording digest, consumes
the full tape and constructs no live client.

The ledger additionally records these attempts as `planner` records with
`phase: "model_request"`. Muse-only decisions cite a preceding prediction receipt
through mandatory `model_receipt_id` for every Muse-family decision; a completed bundle projection also includes
`terminal_model_receipt_id`. Offline verification rebuilds the allowed numeric
input from the recorded trace and paid window, reparses the raw model response and
requires the committed action, probability and interval to match. A receipt must bind exactly one earlier
`planner/model_request` by digest, complete request and complete response. Version 2 receipts retain retry
attempt digests and aggregate the cost of all attempts; no request or interval is fabricated. This opaque
model path is reserved for the frozen `muse_only` baseline. Agent strategies must
still use executable world models and decision rules. A fixed Muse comparator is
not a learned prequential update; its input contains neither prior labels nor
cross-trial conversation. Its structural fit records retain fold and reveal
provenance without claiming that the model learned from those records.

A new live session's ID, insertion and event clocks use private ledger-local replay
state. Existing campaigns retain their original random IDs and wall clocks. This
allows a fresh replay ledger to reproduce decision bodies exactly while preserving
insert-only admission, seal checks and the original checker bytes.

Controller stops distinguish model-caused `ToolRejected` errors (returned through the tool error channel)
from integrity, I/O and SQLite failures (`controller_error`). Recording failures are `recording_error` and
retain paid spending. A real `message.refusal` alone is `refused`; HTTP 4xx other than 429 is
`provider_rejected` with its code. `finish_reason` and reasoning tokens are recorded; a length stop is
`length_limit`. Scores after an early stop carry `partial: true` and the campaign status. Incomplete
episodes are listed as pending and cannot ground performance claims or a supported procedure.
