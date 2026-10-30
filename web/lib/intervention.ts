// Intervention campaigns: an agent decides, at recorded heater-off, whether stopping the heat is enough (stand down),
// whether to escalate, or whether to pay to watch 30/60 s more. The 19 MB ReplayBundle is the receipt; the build
// splits it (scripts/split-intervention.ts) into the summary and per-trial files typed here. Pure data and wording,
// shared by the page and the tests.

export type Split = "loco" | "prequential" | "lofo";
export const SPLITS: Split[] = ["loco", "prequential", "lofo"];

export const SPLIT_WORD: Record<Split, { name: string; short: string; note: string }> = {
  loco: {
    name: "Leave one condition out",
    short: "Held-out condition",
    note: "The primary split. Each trial is scored by a model trained without any trial of its cell format and charge.",
  },
  prequential: {
    name: "Prequential",
    short: "In trial order",
    note: "Trials arrive in Exp-number order. Each decision uses only trials revealed before it.",
  },
  lofo: {
    name: "Leave one format out",
    short: "Held-out format",
    note: "Known transfer failure: trained on one cell format (18650 or 21700), scored on the other.",
  },
};

/** Where a campaign's agent strategies came from, as the viewer should read it. */
export type InterventionSource = "scripted" | "live" | "comparator";

/** The intervention campaigns the app offers, by folder under public/fixtures. */
export interface InterventionEntry {
  key: string;
  name: string;
  /** One plain line for the home screen. */
  line: string;
  source: InterventionSource;
  /** The label in the source switch at the top of the page. */
  tab: string;
  /** Not listed on its own in the home screen or the menu (a comparator shown inside another campaign). */
  hidden?: boolean;
  /**
   * Another campaign's bundle, on the same trials, whose policy the build merges into this one as a comparator
   * (scripts/split-intervention.ts). Its receipt stays in its own bundle.
   */
  comparator?: string;
  /**
   * What `translucent verify` printed for this bundle. Copied from a real run, never written by hand. Omitted when
   * it has not been run on this export.
   */
  verified?: { command: string; output: string[]; exit: number; ranOn: string };
}

export const INTERVENTIONS: InterventionEntry[] = [
  {
    key: "nist-bench",
    name: "Battery heating toward failure",
    line: "The heater is cut. Stand down, escalate, or watch longer? Scored on 50 recorded NIST trials it hadn’t seen.",
    source: "scripted",
    tab: "Scripted benchmark",
    // `uv run translucent verify web/public/fixtures/nist-bench.json`, 2026-10-30, at 5c9524b: exit 1 after 15 min.
    // 22 more lines like the second follow, one per update record.
    verified: {
      command: "uv run translucent verify web/public/fixtures/nist-bench.json",
      output: [
        "126 findings re-checked, 126 match; 247 files intact, 1500 decisions re-checked",
        "int_3ad4150acba34cd68b629449d291fd13: Committed probability differs from its executable world model",
        "int_39eb6bdae86d4a17ac57094efa87f3a1: Compatibility predictions differ from executable models",
        "… 21 more records: Compatibility predictions differ from executable models",
      ],
      exit: 1,
      ranOn: "2026-10-30",
    },
  },
  {
    key: "nist-live",
    name: "Battery heating toward failure, live run",
    line: "The same 50 NIST trials, with strategies proposed live by Muse Wonder and compared with Muse Wonder alone.",
    source: "live",
    tab: "Live run: Muse Wonder planner",
    comparator: "nist-muse-only",
    // `uv run translucent verify web/public/fixtures/nist-live.json`, 2026-10-30: exit 0.
    verified: {
      command: "uv run translucent verify web/public/fixtures/nist-live.json",
      output: ["18 findings re-checked, 18 match; 247 files intact, 900 decisions re-checked"],
      exit: 0,
      ranOn: "2026-10-30",
    },
  },
  {
    key: "nist-muse-only",
    name: "Muse alone on the NIST trials",
    line: "Muse Wonder reads each trace with no tools and commits a call. The comparator for the live run.",
    source: "comparator",
    tab: "Muse alone",
    hidden: true,
    // `uv run translucent verify web/public/fixtures/nist-muse-only.json`, 2026-10-30: exit 0.
    verified: {
      command: "uv run translucent verify web/public/fixtures/nist-muse-only.json",
      output: ["0 findings re-checked, 0 match; 76 files intact, 450 decisions re-checked"],
      exit: 0,
      ranOn: "2026-10-30",
    },
  },
];
/** Split order: a comparator before the campaign that merges it. */
export const INTERVENTION_KEYS = [...INTERVENTIONS].sort((a, b) => Number(!!a.comparator) - Number(!!b.comparator)).map((x) => x.key);
export const interventionEntry = (key: string) => INTERVENTIONS.find((x) => x.key === key) ?? INTERVENTIONS[0];
/** The campaigns listed on the home screen, in the menu and in the source switch. */
export const INTERVENTION_NAV = INTERVENTIONS.filter((x) => !x.hidden);
export const interventionHref = (key: string, q?: { trial?: string; split?: Split }) => {
  const p = new URLSearchParams();
  if (q?.trial) p.set("trial", q.trial);
  if (q?.split && q.split !== "loco") p.set("split", q.split);
  const s = p.toString();
  return `/i/${key}/${s ? `?${s}` : ""}`;
};

// ---------------------------------------------------------------- the split files

export type Outcome = "rescued" | "ignited" | "delayed_ignition";
export type Action = "stand_down" | "escalate" | "abstain" | "observe";

export interface IvCondition { format: string; soc: number }

export interface IvWorldModel { family: string; features: string[]; grouping: string; constraint: string; threshold: number | null; candidates: number }

export interface IvPolicy {
  id: string;
  name: string;
  kind: "baseline" | "agent";
  family: string;
  world_model: IvWorldModel;
  decision_rule: { mode: string; threshold: number | null; observe_s: number; abstain_band: [number, number] | null };
  simulated: boolean;
  forecast_origin: string;
  proposed_seq: number;
  /** The planner record that proposed it, from the receipt (agent policies). */
  planner: string | null;
  /** Held-out decisions were committed for it. */
  scored: boolean;
  /**
   * Not a policy record of this campaign's ledger. "outcomes": a reference the build computes from the recorded
   * outcomes (always escalate, when the campaign registered no baseline). Otherwise the key of the comparator bundle
   * it was recorded in.
   */
  external?: string;
}

/** One physical experiment (Exp number), the same in every split. */
export interface IvTrial {
  key: string;
  exp: number;
  condition: IvCondition;
  chemistry: string | null;
  capacity_mAh: number | null;
  delay_after_vent_s: number;
  t_heater_on: number;
  t_vent: number;
  t_heater_off: number;
  heater_off_lag_s: number;
  t_ignition: number | null;
  truncated_at: number | null;
  flags: string[];
  event_sources: Record<string, string>;
  outcome: Outcome | null;
  corroboration: { image: string; vector: string; trace: string; agree: boolean } | null;
  corroboration_source: string | null;
  series_artifact: string;
  /** Last sample time of the recorded trace. */
  t_end: number;
  /** °C the trace fell from a post-off peak to a later minimum before any ignition (see afterOff). */
  drop_after_off: number | null;
}

/** A fold-scoped salted commitment of one trial's outcome. */
export interface IvSeal { trial_id: string; scope: string; sealed_hash: string; revealed_seq: number | null }

/** The fitted model a decision came from, from the receipt's fit record. */
export interface IvFit {
  family: string;
  features: string[];
  grouping: string;
  constraint: string;
  /** The threshold for this trial's group, when the model is a threshold. */
  threshold: number | null;
  /** Revealed training trials the fit saw. */
  n_train: number;
  /** Candidate specs compared by inner cross-validation before this one was selected. */
  candidates: number;
}

export interface IvDecision {
  id: string;
  trial_key: string;
  policy_id: string;
  split: Split;
  p_ignite: number;
  interval: [number, number];
  decision: Action;
  initial_action: Action;
  observe_s: number;
  watch_time_s: number;
  ignited_during_watch: boolean;
  committed_seq: number;
  terminal_committed_seq: number;
  revealed_seq: number | null;
  correct: boolean | null;
  unsafe: number;
  simulated: boolean;
  /** Registered decision cost of this trial, from the score's per-trial costs. */
  cost: number | null;
  fit: IvFit | null;
  /** The comparator bundle whose ledger this decision is in, when not this campaign's (sequence numbers are its own). */
  ledger?: string;
}

export interface IvScore {
  id: string;
  seq: number;
  policy_id: string;
  split: Split;
  status: string;
  reason: string | null;
  n: number;
  unsafe: number;
  missed: number;
  abstain: number;
  accuracy: number | null;
  accuracy_interval: [number, number] | null;
  brier: number | null;
  watch_time_s: number;
  total_cost: number;
  mean_cost: number | null;
  mean_cost_interval: [number, number] | null;
  /**
   * "ledger": a score record exported from the ledger. "gate": the score the gate computed and recorded in its review
   * of a claim. "computed": the build computed it from the exported committed decisions and recorded outcomes with the
   * registered costs, because the export has no record of it.
   */
  source: "ledger" | "gate" | "computed";
}

export interface IvComparison {
  id: string;
  seq: number;
  a: string;
  b: string;
  split: Split;
  a_mean_cost: number;
  b_mean_cost: number;
  mean_cost_difference: number;
  p: number;
  p_holm: number;
  method: string;
  discordant: number;
  family_size: number;
  candidates_submitted: number;
  baselines_submitted: number;
  a_unsafe: number;
  b_unsafe: number;
  a_missed: number;
  b_missed: number;
  missed_p_holm: number | null;
  /** As IvScore.source: a ledger comparison record, the gate's review of a claim, or computed by the build. */
  source: "ledger" | "gate" | "computed";
}

export interface IvUpdate { id: string; seq: number; policy_id: string; after_trial_key: string; accepted: boolean; reason: string }

export interface IvClaim {
  id: string;
  seq: number;
  type: string;
  predicate: Record<string, unknown>;
  verdict: string | null;
  reason: string | null;
  review_seq: number | null;
}

export interface IvHypothesis { id: string; seq: number; mechanism: string; text: string; policies: string[]; world_model: { family: string; features: string[] } }

export interface IvNextExperiment {
  id: string;
  seq: number;
  condition: IvCondition;
  heater_off_after_vent_s: number;
  p_ignite: number;
  interval: [number, number];
  disagreement_bits: number;
  per_hypothesis: { hypothesis_id: string; p_ignite: number }[];
  why: string;
  schedule: string;
  status: string;
  simulated: boolean;
  limits: string[];
}

export interface IvOrigin {
  /** Where the agent's strategies came from: a scripted search, or a live planner model. */
  kind: "scripted" | "live" | "unknown";
  /** The planner names recorded for the agent's proposals. */
  planners: string[];
  budget_usd: number | null;
}

export interface InterventionSummary {
  version: 1;
  fixture: string;
  bundle: { path: string; sha256: string; bytes: number };
  receipt: { campaign_id: string; ledger_sha256: string; ledger_bytes: number; blobs: number } | null;
  campaign: { id: string; title: string; objective: string };
  origin: IvOrigin;
  system: { id: string; title: string; citation: string; doi: string; licence: string; sensor: string; decision_point: string };
  protocol: {
    primary_metric: string;
    splits: Split[];
    heldout_cap_K: number;
    costs: { unsafe: number; watch_ignition: number; watch_per_second: number; unnecessary_escalation: number; abstain: number };
    observe_options: number[];
    heater_off_lag_s: number;
    amendment: number | null;
    registered_seq: number;
    primary_test: { name: string; correction_family: string } | null;
    funnel: {
      proposed: number; screened_out: number; submitted: number; pending: number;
      baselines_registered: number | null; candidate_models_proposed: number; candidate_models_screened_out: number;
      screen: string; unit: string;
    };
  };
  policies: IvPolicy[];
  trials: IvTrial[];
  scores: IvScore[];
  comparisons: IvComparison[];
  /** Prequential split, trials in reveal order: each policy's per-trial cost and unsafe flag. */
  prequential: { order: string[]; rows: { policy_id: string; cost: number[]; unsafe: number[] }[] };
  updates: IvUpdate[];
  hypotheses: IvHypothesis[];
  procedure: { text: string; policy_id: string | null; claim_ids: string[]; limits: string[] } | null;
  next_experiments: IvNextExperiment[];
  claims: IvClaim[];
  references: { name: string; n: number; ignited: number; ignition_interval: [number, number]; limit: string }[];
  /** Post-hoc sensitivity records in the bundle, left out of the scorecard and comparisons. */
  post_hoc: { scores: number; comparisons: number };
}

export interface InterventionTrialFile {
  key: string;
  /** Thermocouple trace: seconds since heater-on, °C. */
  t: number[];
  y: number[];
  seals: Partial<Record<Split, IvSeal>>;
  decisions: IvDecision[];
}

// ---------------------------------------------------------------- words

const POLICY_WORD: Record<string, string> = {
  always_escalate: "Always escalate",
  paper_table3: "Paper Table 3 lookup",
  delay_lookup_refit: "Delay lookup, refitted",
  fixed_temperature: "Fixed temperature threshold",
  temperature_refit: "Temperature threshold, refitted",
  physics_only: "One-node physics",
  two_node_physics: "Two-node physics",
  always_observe_30: "Always watch 30 s",
  muse_only: "Muse alone",
  agent_threshold: "Agent: threshold search",
  agent_logistic: "Agent: logistic search",
  generic_constant_candidate: "Agent: constant candidate",
};
/** A short note beside a policy's name, where its name alone could mislead. */
const POLICY_NOTE: Record<string, string> = {
  paper_table3: "retrospective: the published lookup was built from these outcomes",
  fixed_temperature: "escalate above 170 °C",
  muse_only: "registered comparator, not run",
};
/** Notes for a policy that was run, where they differ from the note above. */
const POLICY_NOTE_RUN: Record<string, string> = {
  muse_only: "Muse Wonder reading each trace with no tools; a separate live run",
  always_escalate: "computed from the recorded outcomes; not a ledger policy",
};
export const policyWord = (p: Pick<IvPolicy, "name"> | undefined) =>
  !p ? "Unknown policy" : POLICY_WORD[p.name] ?? p.name.replace(/_/g, " ");
export function policyNote(p: (Pick<IvPolicy, "name"> & Partial<Pick<IvPolicy, "scored" | "external">>) | undefined): string | null {
  if (!p) return null;
  if (p.name === "always_escalate") return p.external === "outcomes" ? POLICY_NOTE_RUN.always_escalate : null;
  if (p.scored && POLICY_NOTE_RUN[p.name]) return POLICY_NOTE_RUN[p.name];
  return POLICY_NOTE[p.name] ?? null;
}

const FEATURE_WORD: Record<string, string> = {
  level: "temperature at heater-off",
  soc: "state of charge",
  format: "cell format",
  delay_after_vent_s: "delay after vent",
  time_since_vent: "time since vent",
  rise_since_vent: "rise since vent",
  time_heated: "time heated",
  post_slope: "slope during the watch",
};
export function featureWord(f: string): string {
  if (FEATURE_WORD[f]) return FEATURE_WORD[f];
  const m = f.match(/^(slope|rise)_(\d+)$/);
  if (m) return m[1] === "slope" ? `slope over the last ${m[2]} s` : `rise over the last ${m[2]} s`;
  return f.replace(/_/g, " ");
}

const FAMILY_WORD: Record<string, string> = {
  threshold: "threshold",
  logistic: "logistic regression",
  constant: "constant probability",
  table3: "published lookup table",
  thermal: "one-node Arrhenius heat balance",
  two_node: "two-node Arrhenius heat balance",
  search: "search over candidate models",
  muse: "Muse model reading",
};
export const familyWord = (f: string) => FAMILY_WORD[f] ?? f.replace(/_/g, " ");

const GROUP_WORD: Record<string, string> = { pooled: "pooled", format: "per cell format", condition: "per condition" };

/** "threshold on slope over the last 20 s, per cell format" */
export function modelLine(m: { family: string; features: string[]; grouping: string }): string {
  const on = m.features.length ? ` on ${m.features.map(featureWord).join(", ")}` : "";
  const g = m.grouping && m.grouping !== "pooled" ? `, ${GROUP_WORD[m.grouping] ?? m.grouping}` : "";
  return `${familyWord(m.family)}${on}${g}`;
}

export const ACTION_WORD: Record<Action, string> = {
  stand_down: "Stand down",
  escalate: "Escalate",
  abstain: "Abstain (counted as escalation)",
  observe: "Watch",
};

/** "Watch 30 s, then escalate" or "Escalate". */
export function actionLine(d: Pick<IvDecision, "decision" | "initial_action" | "observe_s">): string {
  if (d.initial_action === "observe" && d.observe_s > 0) return `Watch ${d.observe_s} s, then ${ACTION_WORD[d.decision].toLowerCase()}`;
  return ACTION_WORD[d.decision];
}

export const OUTCOME_WORD: Record<Outcome, string> = {
  rescued: "No ignition after heater-off",
  ignited: "Ignited",
  delayed_ignition: "Cooled, then ignited",
};

export const pct = (p: number) => `${Math.round(p * 100)}%`;
export const pctRange = (r: [number, number] | null | undefined) => (r ? `${Math.round(r[0] * 100)}–${Math.round(r[1] * 100)}%` : "–");
export const fixed = (v: number | null | undefined, d = 2) => (v == null ? "–" : v.toFixed(d));
export function pWord(p: number): string {
  if (p >= 0.995) return "1.00";
  if (p < 0.001) return "< 0.001";
  // Three decimals: an exact sign-flip p of 1/8 reads 0.125, not 0.13.
  return p.toFixed(3);
}
export const shortHash = (h: string) => h.replace(/^sha256:/, "").slice(0, 12);
export const expWord = (t: Pick<IvTrial, "exp">) => `Exp ${t.exp}`;
export const conditionWord = (c: IvCondition) => `${c.format}, ${c.soc}% charge`;

const FLAG_WORD: Record<string, string> = {
  heater_off_inferred: "heater-off time inferred",
  thermocouple_loss: "thermocouple lost at ignition",
  render_safe_reheat: "trace ends at render-safe reheat",
  suspect_soc: "state of charge suspect",
  censored: "finite watch window (censored)",
};
export const flagWord = (f: string) => FLAG_WORD[f] ?? f.replace(/_/g, " ");

// ---------------------------------------------------------------- shaping

export interface CallVerdict { kind: "right" | "unsafe" | "watch_ignition" | "missed" | "unknown"; word: string; line: string }

/** Was the committed call right, against the recorded outcome? Abstention counts as escalation. */
export function callVerdict(d: IvDecision, t: Pick<IvTrial, "outcome">): CallVerdict {
  if (d.correct == null || !t.outcome) return { kind: "unknown", word: "Not revealed", line: "The outcome is not revealed in this export." };
  const ignited = t.outcome !== "rescued";
  if (d.ignited_during_watch) {
    return { kind: "watch_ignition", word: "Unsafe", line: "The cell ignited while it was still watching. The protocol counts that as unsafe." };
  }
  if (d.decision === "stand_down") {
    return ignited
      ? { kind: "unsafe", word: "Unsafe stand-down", line: "It judged stopping the heat enough. The cell ignited." }
      : { kind: "right", word: "Right call", line: "It stood down, and the cell did not ignite in the recorded window." };
  }
  return ignited
    ? { kind: "right", word: "Right call", line: "It escalated, and the cell ignited." }
    : { kind: "missed", word: "Missed rescue", line: "It escalated, but the cell did not ignite: stopping the heat was enough." };
}

/**
 * What the trace did after heater-off, before any ignition: the largest fall from a post-off peak to a later minimum
 * (how far it cooled), and how long after heater-off it ignited.
 */
export function afterOff(file: Pick<InterventionTrialFile, "t" | "y">, trial: Pick<IvTrial, "t_heater_off" | "t_ignition">) {
  const { t, y } = file;
  const i0 = t.findIndex((x) => x >= trial.t_heater_off);
  if (i0 < 0) return null;
  const end = trial.t_ignition ?? Infinity;
  let peak = y[i0], tPeak = t[i0], runPeak = y[i0], tRun = t[i0];
  let min = y[i0], tMin = t[i0], drop = 0;
  for (let i = i0; i < t.length && t[i] < end; i++) {
    if (y[i] > runPeak) { runPeak = y[i]; tRun = t[i]; }
    if (runPeak - y[i] > drop) { drop = runPeak - y[i]; peak = runPeak; tPeak = tRun; min = y[i]; tMin = t[i]; }
  }
  return {
    atOff: y[i0],
    peak,
    tPeak,
    min,
    tMin,
    /** °C it fell from a post-off peak to a later minimum, before any ignition. */
    drop,
    /** Seconds from heater-off to recorded ignition. */
    ignitionAfterOff: trial.t_ignition == null ? null : trial.t_ignition - trial.t_heater_off,
  };
}

export const policyById = (s: InterventionSummary, id: string) => s.policies.find((p) => p.id === id);
export const policyByName = (s: InterventionSummary, name: string) => s.policies.find((p) => p.name === name);

export interface ScoreRow { policy: IvPolicy; score: IvScore }

/** Each policy's score on a split, lowest mean cost first; policies that were not run go last. */
export function scorecard(s: InterventionSummary, split: Split): ScoreRow[] {
  const rows: ScoreRow[] = [];
  for (const score of s.scores) {
    if (score.split !== split) continue;
    const policy = policyById(s, score.policy_id);
    if (policy) rows.push({ policy, score });
  }
  const key = (r: ScoreRow) => (r.score.mean_cost == null ? Infinity : r.score.mean_cost);
  return rows.sort((a, b) => key(a) - key(b) || a.policy.proposed_seq - b.policy.proposed_seq);
}

export interface SplitVerdict {
  /** Lowest observed mean cost under the registered costs (ties included). */
  best: ScoreRow[];
  /** "always escalate" on this split, when it was scored. */
  escalate: ScoreRow | null;
  /** Each agent policy against always escalate. */
  agentVsEscalate: { policy: IvPolicy; c: IvComparison; beats: boolean }[];
  /** Any agent policy with lower mean cost and Holm-adjusted p < alpha against any comparator. */
  agentWins: { policy: IvPolicy; against: IvPolicy; c: IvComparison }[];
  familySize: number | null;
}

export function splitVerdict(s: InterventionSummary, split: Split, alpha = 0.05): SplitVerdict {
  const rows = scorecard(s, split).filter((r) => r.score.mean_cost != null);
  const low = rows.length ? rows[0].score.mean_cost! : null;
  const best = low == null ? [] : rows.filter((r) => Math.abs(r.score.mean_cost! - low) < 1e-9);
  const esc = policyByName(s, "always_escalate");
  const escalate = esc ? rows.find((r) => r.policy.id === esc.id) ?? null : null;
  const cs = s.comparisons.filter((c) => c.split === split);
  const agentVsEscalate = esc
    ? cs.filter((c) => c.b === esc.id && policyById(s, c.a)?.kind === "agent")
        .map((c) => ({ policy: policyById(s, c.a)!, c, beats: c.a_mean_cost < c.b_mean_cost && c.p_holm < alpha }))
    : [];
  const agentWins = cs
    .filter((c) => policyById(s, c.a)?.kind === "agent" && c.a_mean_cost < c.b_mean_cost && c.p_holm < alpha)
    .map((c) => ({ policy: policyById(s, c.a)!, against: policyById(s, c.b)!, c }))
    .filter((x) => x.against);
  return { best, escalate, agentVsEscalate, agentWins, familySize: cs[0]?.family_size ?? null };
}

/** Plain sentences for a split's result. They say when no agent policy beats always escalate. */
export function verdictLines(s: InterventionSummary, split: Split): string[] {
  const v = splitVerdict(s, split);
  const out: string[] = [];
  if (v.best.length) {
    const names = v.best.map((r) => policyWord(r.policy)).join(" and ");
    const cost = v.best[0].score.mean_cost!.toFixed(2);
    const esc = v.escalate && !v.best.includes(v.escalate) ? ` Always escalate costs ${v.escalate.score.mean_cost!.toFixed(2)}.` : "";
    out.push(`Lowest mean cost under the registered costs: ${names}, ${cost} per trial.${esc}`);
  }
  if (v.agentVsEscalate.length) {
    const winners = v.agentVsEscalate.filter((x) => x.beats);
    const minP = Math.min(...v.agentVsEscalate.map((x) => x.c.p_holm));
    if (winners.length) {
      out.push(`${winners.map((x) => policyWord(x.policy)).join(" and ")} beats always escalate after Holm correction (adjusted p ${pWord(Math.min(...winners.map((x) => x.c.p_holm)))}).`);
    } else {
      out.push(`No agent policy beats always escalate after Holm correction (smallest adjusted p ${pWord(minP)}${v.familySize ? `, family of ${v.familySize}` : ""}).`);
    }
  }
  return out;
}

/** Running totals over the prequential trial order. */
export function cumulative(xs: number[]): number[] {
  let sum = 0;
  return xs.map((x) => (sum += x));
}

/** Trials in Exp-number order, with the one to open first (Exp 81, the paper's showcase, when present). */
export function trialOrder(s: InterventionSummary): IvTrial[] {
  return [...s.trials].sort((a, b) => a.exp - b.exp);
}
export const DEFAULT_TRIAL = "Exp81";

/** Agent policies that committed held-out decisions, in proposal order. */
export const agentPolicies = (s: InterventionSummary) =>
  s.policies.filter((p) => p.kind === "agent" && p.scored).sort((a, b) => a.proposed_seq - b.proposed_seq);

/** The agent policy the procedure rests on, else the first scored agent policy. */
export function headlineAgent(s: InterventionSummary): IvPolicy | null {
  const agents = agentPolicies(s);
  return agents.find((p) => p.id === s.procedure?.policy_id) ?? agents[0] ?? null;
}

/** "Scripted search, no model calls" or the live planner's name. */
export function originWord(o: IvOrigin): { tag: string; line: string } {
  if (o.kind === "scripted") {
    return {
      tag: "Scripted",
      line: `The agent’s strategies came from a scripted search (${o.planners.join(", ")}). No model was called${o.budget_usd === 0 ? "; model budget $0" : ""}.`,
    };
  }
  if (o.kind === "live") {
    const name = o.planners.find((p) => /muse|wonder|spark/i.test(p)) ?? o.planners[0] ?? "a live planner model";
    return { tag: "Live", line: `The agent’s strategies were proposed live by ${name}.` };
  }
  return { tag: "Origin not recorded", line: "This export does not record where the agent’s strategies came from." };
}
