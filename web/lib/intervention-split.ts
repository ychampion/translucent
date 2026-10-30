// Splits an intervention ReplayBundle (docs/CONTRACTS.md, `intervention`) into the page's summary and one file per
// physical trial. Pure: the caller decodes the receipt (planner and fit records) and the thermocouple traces. The
// original bundle stays the verifiable receipt; nothing here changes a value, it only selects and joins.
import { afterOff } from "./intervention";
import type {
  Action, InterventionSummary, InterventionTrialFile, IvClaim, IvComparison, IvDecision, IvFit, IvPolicy, IvScore,
  IvSeal, IvTrial, Outcome, Split,
} from "./intervention";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Raw = any;

/** One `intervention` ledger record from the receipt: {id, type, seq, data}. */
export interface ReceiptRecord { id: string; type: string; seq: number; data: Raw }

export interface SplitInput {
  fixture: string;
  bundle: Raw;
  /** The receipt's intervention records (planner and fit are used). Empty when the bundle has no receipt. */
  records: ReceiptRecord[];
  /** A thermocouple trace by artifact id. */
  series(artifact: string): { t: number[]; y: number[] } | null;
  /** Where the original file is and its digest, for the trust panel. */
  file: { path: string; sha256: string; bytes: number };
  /** A comparator campaign on the same trials (entry.comparator), already split. */
  comparator?: ComparatorInput;
}

const MODEL_WORD: Record<string, string> = { "muse-wonder-1.0": "Muse Wonder (muse-wonder-1.0)" };

export interface SplitOutput { summary: InterventionSummary; trials: InterventionTrialFile[] }

/** Another campaign's split output on the same physical trials, merged in as a comparator (its key names its bundle). */
export interface ComparatorInput { key: string; out: SplitOutput }

// ---------------------------------------------------------------- statistics, ported from translucent/intervention_bench.py

type Costs = InterventionSummary["protocol"]["costs"];

/** Recorded-action loss (decision_cost). Abstention is a charged escalation, never charged twice. */
export function decisionCost(row: { unsafe: number; missed: number; abstain: number; watch_time_s: number; ignited_during_watch: boolean }, c: Costs): number {
  const escalation = row.abstain ? c.abstain : row.missed ? c.unnecessary_escalation : 0;
  const unsafeCost = row.ignited_during_watch ? c.watch_ignition ?? c.unsafe : c.unsafe;
  return row.unsafe * unsafeCost + row.watch_time_s * c.watch_per_second + escalation;
}

/** Wilson 95% interval for k of n. */
export function wilson(k: number, n: number): [number, number] {
  if (!n) return [0, 1];
  const z = 1.959963984540054, p = k / n, d = 1 + (z * z) / n;
  const center = (p + (z * z) / (2 * n)) / d;
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / d;
  return [Math.max(0, center - half), Math.min(1, center + half)];
}

/**
 * Two-sided paired sign-flip test on per-trial cost differences, exact at any number of discordant trials: counts
 * the sign assignments whose absolute summed difference is at least the observed one. The ledger's own test is exact
 * through 20 discordant trials and seeded Monte Carlo above, so above 20 the two agree to Monte Carlo error.
 */
export function signFlip(differences: number[]): { p: number; discordant: number } {
  const SCALE = 1e6;
  const nz = differences.filter((v) => Math.abs(v) > 1e-10).map((v) => Math.round(Math.abs(v) * SCALE));
  if (!nz.length) return { p: 1, discordant: 0 };
  const observed = Math.abs(Math.round(differences.filter((v) => Math.abs(v) > 1e-10).reduce((a, b) => a + b, 0) * SCALE));
  let ways = new Map<number, number>([[0, 1]]);
  for (const m of nz) {
    const next = new Map<number, number>();
    for (const [s, n] of ways) {
      next.set(s + m, (next.get(s + m) ?? 0) + n);
      next.set(s - m, (next.get(s - m) ?? 0) + n);
    }
    ways = next;
  }
  let hit = 0;
  for (const [s, n] of ways) if (Math.abs(s) >= observed - 1) hit += n;
  return { p: Math.min(1, hit / 2 ** nz.length), discordant: nz.length };
}

/** Holm step-down adjustment over a family of size m (m may exceed the p values given). */
export function holm(pvalues: Map<string, number>, m: number): Map<string, number> {
  const ordered = [...pvalues.keys()].sort((a, b) => pvalues.get(a)! - pvalues.get(b)! || (a < b ? -1 : 1));
  const out = new Map<string, number>();
  let running = 0;
  ordered.forEach((k, rank) => {
    running = Math.max(running, (m - rank) * pvalues.get(k)!);
    out.set(k, Math.min(1, running));
  });
  return out;
}

export const ALWAYS_ESCALATE_ID = "outcomes:always_escalate";

const r1 = (x: number) => Math.round(x * 10) / 10;
const SPLIT_SET = new Set(["loco", "lofo", "prequential"]);
const isSplit = (s: unknown): s is Split => typeof s === "string" && SPLIT_SET.has(s);

/** The fitted threshold for this trial's group, if the fit is per group. */
function groupThreshold(models: Raw, grouping: string, c: { format: string; soc: number }): number | null {
  if (!models || typeof models !== "object") return null;
  const keys = grouping === "format" ? [c.format] : grouping === "condition" ? [`${c.format}-${c.soc}`, `${c.format}:${c.soc}`, `${c.format}_${c.soc}`] : [];
  for (const k of [...keys, "pooled"]) {
    const t = models[k]?.threshold;
    if (typeof t === "number") return t;
  }
  return null;
}

export function splitIntervention(input: SplitInput): SplitOutput {
  const { bundle } = input;
  const iv = bundle?.intervention;
  if (!iv) throw new Error("This bundle has no intervention field");

  const recordById = new Map(input.records.map((r) => [r.id, r]));
  const trialById = new Map<string, Raw>((iv.trials as Raw[]).map((t) => [t.id, t]));
  const keyOfTrial = (id: string): string | null => trialById.get(id)?.trial_key ?? null;

  // -------- where the agent's strategies came from
  const proposals = input.records.filter((r) => r.type === "planner" && r.data?.call?.arguments?.name && (r.data.call.tool === "propose_policy" || r.data.call.tool == null));
  // The model the live planner called, from its recorded model requests.
  const models = [...new Set(input.records.filter((r) => r.type === "planner" && typeof r.data?.model === "string").map((r) => r.data.model as string))];
  const liveName = models.length === 1 ? MODEL_WORD[models[0]] ?? models[0] : "live planner";
  const plannerOf = (name: string): string | null => {
    const r = proposals.find((x) => x.data.call.arguments.name === name);
    if (!r) return null;
    if (typeof r.data.planner === "string") return r.data.planner;
    // A tool call the live planner made, in a receipt whose model requests name the model it called.
    return r.data.context_sha256 || (r.data.phase === "tool_call" && models.length) ? liveName : null;
  };

  // -------- policies
  const decided = new Set((iv.decisions as Raw[]).map((d) => d.policy_id));
  const policies: IvPolicy[] = (iv.policies as Raw[]).map((p) => {
    const wm = p.world_model ?? p.spec ?? {};
    return {
      id: p.id,
      name: p.name,
      kind: p.kind === "agent" ? "agent" : "baseline",
      family: p.family,
      world_model: {
        family: wm.family ?? p.family, features: wm.features ?? [], grouping: wm.grouping ?? "pooled",
        constraint: wm.constraint ?? "none", threshold: wm.threshold ?? null, candidates: (wm.candidates ?? []).length,
      },
      decision_rule: {
        mode: p.decision_rule?.mode ?? "", threshold: p.decision_rule?.threshold ?? null,
        observe_s: p.decision_rule?.observe_s ?? 0, abstain_band: p.decision_rule?.abstain_band ?? null,
      },
      simulated: p.simulated !== false,
      forecast_origin: p.forecast_origin ?? "model",
      proposed_seq: p.proposed_seq ?? 0,
      planner: p.kind === "agent" ? plannerOf(p.name) : null,
      scored: decided.has(p.id),
    };
  });
  const planners = [...new Set(policies.filter((p) => p.kind === "agent" && p.planner).map((p) => p.planner!))];
  const origin: InterventionSummary["origin"] = {
    kind: !planners.length ? "unknown" : planners.every((p) => /scripted/i.test(p)) ? "scripted" : "live",
    planners,
    budget_usd: typeof bundle.campaign?.budget_usd === "number" ? bundle.campaign.budget_usd : null,
  };

  // -------- physical trials: one per Exp number, from the copy whose outcome is revealed (prequential first)
  const byKey = new Map<string, Raw>();
  for (const t of iv.trials as Raw[]) {
    const have = byKey.get(t.trial_key);
    const better = !have || (!have.outcome && t.outcome) || (t.scope === "prequential" && !!t.outcome);
    if (better) byKey.set(t.trial_key, t);
  }
  const traces = new Map<string, { t: number[]; y: number[] }>();
  const trials: IvTrial[] = [...byKey.values()].map((t) => {
    const s = input.series(t.series_artifact);
    if (s) traces.set(t.trial_key, s);
    return {
      key: t.trial_key,
      exp: t.exp ?? Number(String(t.trial_key).replace(/\D/g, "")),
      condition: { format: String(t.condition.format), soc: Number(t.condition.soc) },
      chemistry: t.chemistry ?? null,
      capacity_mAh: t.capacity_mAh ?? null,
      delay_after_vent_s: t.delay_after_vent_s,
      t_heater_on: t.t_heater_on,
      t_vent: t.t_vent,
      t_heater_off: t.t_heater_off,
      heater_off_lag_s: t.heater_off_lag_s ?? iv.protocol?.heater_off_lag_s ?? 0,
      t_ignition: t.t_ignition ?? null,
      truncated_at: t.truncated_at ?? null,
      flags: t.flags ?? [],
      event_sources: t.event_sources ?? {},
      outcome: (t.outcome ?? null) as Outcome | null,
      corroboration: t.corroboration ?? null,
      corroboration_source: t.corroboration_source ?? null,
      series_artifact: t.series_artifact,
      t_end: s && s.t.length ? s.t[s.t.length - 1] : t.truncated_at ?? t.t_ignition ?? t.t_heater_off,
      drop_after_off: s ? r1(afterOff(s, { t_heater_off: t.t_heater_off, t_ignition: t.t_ignition ?? null })?.drop ?? 0) : null,
    };
  }).sort((a, b) => a.exp - b.exp);

  // -------- scores and per-trial costs (subset "all")
  // Post-hoc sensitivity records (added after the primary results were known) are not the registered scorecard.
  const allScores = (iv.scores as Raw[]).filter((s) => s.subset === "all" && isSplit(s.split) && !s.post_hoc);
  const costOf = new Map<string, number>();
  for (const s of allScores) for (const c of s.per_trial_costs ?? []) costOf.set(`${s.policy_id}|${s.split}|${c.trial_key}`, c.cost);
  const scores: IvScore[] = allScores.map((s) => ({
    id: s.id, seq: s.seq, policy_id: s.policy_id, split: s.split, status: s.status, reason: s.reason ?? null,
    n: s.n, unsafe: s.unsafe, missed: s.missed, abstain: s.abstain ?? 0,
    accuracy: s.accuracy ?? null, accuracy_interval: s.accuracy == null ? null : s.accuracy_interval ?? null,
    brier: s.brier ?? null, watch_time_s: s.watch_time_s ?? 0, total_cost: s.total_cost ?? 0,
    mean_cost: s.mean_cost ?? null, mean_cost_interval: s.mean_cost_interval ?? null, source: "ledger",
  }));

  // -------- scores the gate computed while reviewing a claim: its review record carries the full score
  const reviews = (iv.reviews as Raw[] ?? []).slice().sort((a, b) => a.seq - b.seq);
  for (const r of reviews) {
    const c = r.computed;
    if (!c || typeof c.policy_id !== "string" || c.status !== "scored" || c.subset !== "all" || !isSplit(c.split)) continue;
    const at = scores.findIndex((x) => x.policy_id === c.policy_id && x.split === c.split);
    if (at >= 0 && scores[at].source !== "gate") continue;
    const sc: IvScore = {
      id: r.id, seq: r.seq, policy_id: c.policy_id, split: c.split, status: "scored", reason: null, n: c.n,
      unsafe: c.unsafe, missed: c.missed, abstain: c.abstain ?? 0, accuracy: c.accuracy ?? null, accuracy_interval: c.accuracy == null ? null : c.accuracy_interval ?? null,
      brier: c.brier ?? null, watch_time_s: c.watch_time_s ?? 0, total_cost: c.total_cost ?? 0,
      mean_cost: c.mean_cost ?? null, mean_cost_interval: c.mean_cost_interval ?? null, source: "gate",
    };
    if (at >= 0) scores[at] = sc; else scores.push(sc);
    for (const pc of c.per_trial_costs ?? []) costOf.set(`${c.policy_id}|${c.split}|${pc.trial_key}`, pc.cost);
  }

  // -------- scores the export does not record: computed from the committed decisions and the recorded outcomes
  const costs: Costs = iv.protocol?.costs;
  const outcomeOf = (trialId: string): Outcome | null => (trialById.get(trialId)?.outcome ?? null) as Outcome | null;
  const scored = new Set(scores.map((x) => `${x.policy_id}|${x.split}`));
  const groups = new Map<string, Raw[]>();
  for (const d of iv.decisions as Raw[]) {
    if (!isSplit(d.split) || scored.has(`${d.policy_id}|${d.split}`)) continue;
    const k = `${d.policy_id}|${d.split}`;
    groups.set(k, [...(groups.get(k) ?? []), d]);
  }
  const computedScore = (policy_id: string, split: Split, rows: { key: string; unsafe: number; missed: number; abstain: number; correct: boolean; watch_time_s: number; cost: number }[]): IvScore => {
    const n = rows.length, k = rows.filter((r) => r.correct).length, total = rows.reduce((a, r) => a + r.cost, 0);
    for (const r of rows) costOf.set(`${policy_id}|${split}|${r.key}`, r.cost);
    return {
      id: `computed:${policy_id}:${split}`, seq: 0, policy_id, split, status: "scored", reason: null, n,
      unsafe: rows.reduce((a, r) => a + r.unsafe, 0), missed: rows.reduce((a, r) => a + r.missed, 0), abstain: rows.reduce((a, r) => a + r.abstain, 0),
      accuracy: n ? k / n : null, accuracy_interval: n ? wilson(k, n) : null, brier: null,
      watch_time_s: rows.reduce((a, r) => a + r.watch_time_s, 0), total_cost: total, mean_cost: n ? total / n : null, mean_cost_interval: null,
      source: "computed",
    };
  };
  const computedKeys = new Map<Split, Set<string>>();
  if (costs) {
    for (const [k, ds] of groups) {
      const [policy_id, split] = k.split("|") as [string, Split];
      if (ds.some((d) => !outcomeOf(d.trial_id) || d.revealed_seq == null)) continue;
      const keys = new Set<string>();
      const rows = ds.map((d) => {
        const key = keyOfTrial(d.trial_id)!;
        if (keys.has(key)) throw new Error(`Repeated held-out trial ${key} for ${policy_id} on ${split}`);
        keys.add(key);
        const y = outcomeOf(d.trial_id) !== "rescued";
        const early = !!d.ignited_during_watch;
        const row = {
          key, unsafe: Number(early || (d.decision === "stand_down" && y)), missed: Number((d.decision === "escalate" || d.decision === "abstain") && !y),
          abstain: Number(d.decision === "abstain"), watch_time_s: d.watch_time_s ?? 0, ignited_during_watch: early,
          correct: !early && ((d.decision !== "stand_down" && y) || (d.decision === "stand_down" && !y)),
        };
        return { ...row, cost: decisionCost(row, costs) };
      });
      scores.push(computedScore(policy_id, split, rows));
      for (const key of keys) computedKeys.set(split, (computedKeys.get(split) ?? new Set()).add(key));
    }
    // A campaign that registered no baseline: always escalate is computed from the recorded outcomes, as a reference.
    if (scores.some((x) => x.source === "computed") && !policies.some((p) => p.name === "always_escalate")) {
      for (const [split, keySet] of computedKeys) {
        const keys = [...keySet].sort();
        const outcome = (key: string) => byKey.get(key)?.outcome as Outcome | undefined;
        if (!keys.length || keys.some((key) => !outcome(key))) continue;
        scores.push(computedScore(ALWAYS_ESCALATE_ID, split, keys.map((key) => {
          const y = outcome(key) !== "rescued";
          const row = { key, unsafe: 0, missed: Number(!y), abstain: 0, watch_time_s: 0, ignited_during_watch: false, correct: y };
          return { ...row, cost: decisionCost(row, costs) };
        })));
      }
      if (scores.some((x) => x.policy_id === ALWAYS_ESCALATE_ID)) {
        policies.push({
          id: ALWAYS_ESCALATE_ID, name: "always_escalate", kind: "baseline", family: "constant",
          world_model: { family: "constant", features: [], grouping: "pooled", constraint: "none", threshold: null, candidates: 0 },
          decision_rule: { mode: "always_escalate", threshold: null, observe_s: 0, abstain_band: null },
          simulated: false, forecast_origin: "none", proposed_seq: 0, planner: null, scored: true, external: "outcomes",
        });
      }
    }
  }

  // -------- a comparator campaign on the same trials: its policy, its ledger score, its decisions
  const cmp = input.comparator;
  const merged = new Set<string>();
  if (cmp) {
    const theirs = new Map(cmp.out.summary.trials.map((t) => [t.key, t]));
    if (theirs.size !== trials.length || trials.some((t) => theirs.get(t.key)?.outcome !== t.outcome)) {
      throw new Error(`Comparator ${cmp.key} is not on the same trials with the same recorded outcomes`);
    }
    // Only what this campaign did not run itself: a policy it registered but did not run (a stub) is replaced by the
    // comparator's run of it; baselines both campaigns ran stay this campaign's own.
    for (const p of cmp.out.summary.policies.filter((x) => x.scored)) {
      const host = policies.findIndex((x) => x.name === p.name);
      if (host >= 0 && policies[host].scored) continue;
      if (policies.some((x) => x.id === p.id)) throw new Error(`Comparator policy ${p.id} is already in this campaign`);
      if (host >= 0) {
        const stub = policies[host].id;
        policies.splice(host, 1);
        for (let i = scores.length - 1; i >= 0; i--) if (scores[i].policy_id === stub) scores.splice(i, 1);
      }
      policies.push({ ...p, kind: "baseline", external: cmp.key });
      merged.add(p.id);
      for (const sc of cmp.out.summary.scores.filter((x) => x.policy_id === p.id)) scores.push(sc);
    }
    for (const f of cmp.out.trials) for (const d of f.decisions) {
      if (d.cost != null && merged.has(d.policy_id)) costOf.set(`${d.policy_id}|${d.split}|${f.key}`, d.cost);
    }
  }

  // -------- decisions, by physical trial
  const fitOf = (id: string | null, c: { format: string; soc: number }): IvFit | null => {
    const r = id ? recordById.get(id) : undefined;
    const spec = r?.data?.model?.spec;
    if (!spec) return null;
    return {
      family: spec.family, features: spec.features ?? [], grouping: spec.grouping ?? "pooled", constraint: spec.constraint ?? "none",
      threshold: spec.family === "threshold" ? groupThreshold(r!.data.model.models, spec.grouping, c) ?? spec.threshold ?? null : null,
      n_train: (r!.data.train_ids ?? []).length,
      candidates: (r!.data.model.selection ?? []).length,
    };
  };
  const files = new Map<string, InterventionTrialFile>();
  const fileFor = (key: string) => {
    let f = files.get(key);
    if (!f) {
      const s = traces.get(key);
      f = { key, t: (s?.t ?? []).map(r1), y: (s?.y ?? []).map(r1), seals: {}, decisions: [] };
      files.set(key, f);
    }
    return f;
  };
  for (const t of trials) fileFor(t.key);
  for (const d of iv.decisions as Raw[]) {
    if (!isSplit(d.split)) continue;
    const tr = trialById.get(d.trial_id);
    if (!tr) continue;
    const key: string = tr.trial_key;
    const f = fileFor(key);
    if (!f.seals[d.split as Split]) {
      f.seals[d.split as Split] = { trial_id: tr.id, scope: tr.scope, sealed_hash: tr.sealed_hash, revealed_seq: tr.revealed_seq ?? null } satisfies IvSeal;
    }
    const cond = { format: String(tr.condition.format), soc: Number(tr.condition.soc) };
    f.decisions.push({
      id: d.id, trial_key: key, policy_id: d.policy_id, split: d.split, p_ignite: d.p_ignite, interval: d.interval,
      decision: d.decision as Action, initial_action: (d.initial_action ?? d.decision) as Action, observe_s: d.observe_s ?? 0,
      watch_time_s: d.watch_time_s ?? 0, ignited_during_watch: !!d.ignited_during_watch,
      committed_seq: d.committed_seq, terminal_committed_seq: d.terminal_committed_seq ?? d.committed_seq,
      revealed_seq: d.revealed_seq ?? null, correct: d.correct ?? null, unsafe: d.unsafe ?? 0, simulated: d.simulated !== false,
      cost: costOf.get(`${d.policy_id}|${d.split}|${key}`) ?? null,
      fit: fitOf(d.fit_id ?? null, cond),
    } satisfies IvDecision);
  }

  if (cmp) for (const f of cmp.out.trials) for (const d of f.decisions) if (merged.has(d.policy_id)) fileFor(f.key).decisions.push({ ...d, ledger: cmp.key });

  // -------- prequential running totals, trials in reveal order
  const preqTrials = (iv.trials as Raw[]).filter((t) => t.scope === "prequential")
    .sort((a, b) => (a.revealed_seq ?? Infinity) - (b.revealed_seq ?? Infinity) || (a.exp ?? 0) - (b.exp ?? 0));
  const order: string[] = preqTrials.map((t) => t.trial_key);
  const unsafeOf = new Map<string, number>();
  for (const d of iv.decisions as Raw[]) if (d.split === "prequential") unsafeOf.set(`${d.policy_id}|${keyOfTrial(d.trial_id)}`, d.unsafe ?? 0);
  const prequential = {
    order,
    rows: scores.filter((s) => s.split === "prequential" && s.status === "scored").map((s) => ({
      policy_id: s.policy_id,
      cost: order.map((k) => costOf.get(`${s.policy_id}|prequential|${k}`) ?? 0),
      unsafe: order.map((k) => unsafeOf.get(`${s.policy_id}|${k}`) ?? 0),
    })),
  };

  // -------- comparisons, updates, claims
  const comparisons: IvComparison[] = (iv.comparisons as Raw[]).filter((c) => c.subset === "all" && isSplit(c.split) && !c.post_hoc).map((c) => ({
    id: c.id, seq: c.seq, a: c.a, b: c.b, split: c.split, a_mean_cost: c.a_mean_cost, b_mean_cost: c.b_mean_cost,
    mean_cost_difference: c.mean_cost_difference, p: c.p, p_holm: c.p_holm, method: c.method, discordant: c.discordant,
    family_size: c.family_size, candidates_submitted: c.candidates_submitted, baselines_submitted: c.baselines_submitted,
    a_unsafe: c.a_unsafe, b_unsafe: c.b_unsafe, a_missed: c.a_missed, b_missed: c.b_missed, missed_p_holm: c.missed_p_holm ?? null,
    source: "ledger",
  }));
  // No comparison records, but computed or comparator baselines: each agent strategy against each baseline, paired
  // on the same trials, Holm-corrected over the whole family (strategies x baselines), as the ledger's own test is.
  if (!comparisons.length) {
    for (const split of SPLIT_SET as Set<Split>) {
      const here = scores.filter((x) => x.split === split && x.status === "scored");
      const agents = here.filter((x) => policies.find((p) => p.id === x.policy_id)?.kind === "agent");
      const bases = here.filter((x) => policies.find((p) => p.id === x.policy_id)?.kind === "baseline");
      if (!agents.length || !bases.length) continue;
      const vec = (id: string) => trials.map((t) => costOf.get(`${id}|${split}|${t.key}`));
      const m = agents.length * bases.length;
      const raw: Omit<IvComparison, "p_holm">[] = [];
      for (const a of agents) for (const b of bases) {
        const ca = vec(a.policy_id), cb = vec(b.policy_id);
        if (ca.some((v) => v == null) || cb.some((v) => v == null)) continue;
        const delta = ca.map((v, i) => v! - cb[i]!);
        const t = signFlip(delta);
        raw.push({
          id: `computed:${a.policy_id}:${b.policy_id}:${split}`, seq: 0, a: a.policy_id, b: b.policy_id, split,
          a_mean_cost: a.mean_cost!, b_mean_cost: b.mean_cost!, mean_cost_difference: delta.reduce((x, y) => x + y, 0) / delta.length,
          p: t.p, method: "exact paired sign-flip, computed by the build", discordant: t.discordant, family_size: m,
          candidates_submitted: agents.length, baselines_submitted: bases.length,
          a_unsafe: a.unsafe, b_unsafe: b.unsafe, a_missed: a.missed, b_missed: b.missed, missed_p_holm: null, source: "computed",
        });
      }
      const adjusted = holm(new Map(raw.map((c) => [c.id, c.p])), m);
      for (const c of raw) comparisons.push({ ...c, p_holm: adjusted.get(c.id)! });
    }
    // Where the gate tested a pair while reviewing a claim, its review record is the comparison shown.
    for (const r of reviews) {
      const c = r.computed;
      if (!c || typeof c.a !== "string" || typeof c.b !== "string" || c.subset !== "all" || !isSplit(c.split) || typeof c.p !== "number") continue;
      const gate: IvComparison = {
        id: r.id, seq: r.seq, a: c.a, b: c.b, split: c.split, a_mean_cost: c.a_mean_cost, b_mean_cost: c.b_mean_cost,
        mean_cost_difference: c.mean_cost_difference, p: c.p, p_holm: c.p_holm, method: c.method, discordant: c.discordant,
        family_size: c.family_size, candidates_submitted: c.candidates_submitted, baselines_submitted: c.baselines_submitted,
        a_unsafe: c.a_unsafe, b_unsafe: c.b_unsafe, a_missed: c.a_missed, b_missed: c.b_missed, missed_p_holm: c.missed_p_holm ?? null,
        source: "gate",
      };
      const at = comparisons.findIndex((x) => x.a === c.a && x.b === c.b && x.split === c.split);
      if (at >= 0) comparisons[at] = gate; else comparisons.push(gate);
    }
  }
  const agentIds = new Set(policies.filter((p) => p.kind === "agent").map((p) => p.id));
  const updates = (iv.updates as Raw[]).filter((u) => agentIds.has(u.policy_id)).map((u) => ({
    id: u.id, seq: u.seq, policy_id: u.policy_id, after_trial_key: keyOfTrial(u.after_trial) ?? String(u.after_trial),
    accepted: !!u.accepted, reason: u.reason ?? "",
  })).sort((a, b) => a.seq - b.seq);
  const reviewOf = new Map<string, Raw>((iv.reviews as Raw[] ?? []).map((r) => [r.claim_id, r]));
  const claims: IvClaim[] = (iv.claims as Raw[] ?? []).map((c) => {
    const r = reviewOf.get(c.id);
    return { id: c.id, seq: c.seq, type: c.predicate?.type ?? "", predicate: c.predicate ?? {}, verdict: r?.verdict ?? null, reason: r?.reason ?? null, review_seq: r?.seq ?? null };
  });

  const p = iv.protocol ?? {};
  const fn = p.funnel ?? {};
  const summary: InterventionSummary = {
    version: 1,
    fixture: input.fixture,
    bundle: input.file,
    receipt: iv.receipt ? {
      campaign_id: iv.receipt.campaign_id, ledger_sha256: iv.receipt.ledger?.sha256 ?? "", ledger_bytes: iv.receipt.ledger?.bytes ?? 0,
      blobs: Object.keys(iv.receipt.blobs ?? {}).length,
    } : null,
    campaign: { id: bundle.campaign?.id ?? "", title: bundle.campaign?.title ?? "", objective: bundle.campaign?.objective ?? "" },
    origin,
    system: iv.system,
    protocol: {
      primary_metric: p.primary_metric, splits: (p.splits ?? []).filter(isSplit), heldout_cap_K: p.heldout_cap_K,
      costs: p.costs, observe_options: p.observe_options ?? [], heater_off_lag_s: p.heater_off_lag_s ?? 0,
      amendment: p.amendment ?? null, registered_seq: p.registered_seq ?? 0,
      primary_test: p.primary_test ? { name: p.primary_test.name, correction_family: p.primary_test.correction_family } : null,
      funnel: {
        proposed: fn.proposed ?? 0, screened_out: fn.screened_out ?? 0, submitted: fn.submitted ?? 0, pending: fn.pending ?? 0,
        baselines_registered: fn.baselines_registered ?? null,
        candidate_models_proposed: fn.candidate_models_proposed ?? 0, candidate_models_screened_out: fn.candidate_models_screened_out ?? 0,
        screen: fn.screen ?? "", unit: fn.unit ?? "",
      },
    },
    policies,
    trials,
    scores,
    comparisons,
    prequential,
    updates,
    hypotheses: (iv.hypotheses as Raw[] ?? []).map((h) => ({
      id: h.id, seq: h.seq, mechanism: h.mechanism, text: h.text, policies: h.policies ?? [],
      world_model: { family: h.world_model?.family ?? "", features: h.world_model?.features ?? [] },
    })),
    procedure: iv.procedure ? {
      text: iv.procedure.text, policy_id: iv.procedure.rule?.policy_id ?? null,
      claim_ids: iv.procedure.claim_ids ?? [], limits: iv.procedure.limits ?? [],
    } : null,
    next_experiments: (iv.next_experiments as Raw[] ?? []).map((x) => ({
      id: x.id, seq: x.seq, condition: { format: String(x.condition.format), soc: Number(x.condition.soc) },
      heater_off_after_vent_s: x.heater_off_after_vent_s, p_ignite: x.p_ignite, interval: x.interval,
      disagreement_bits: x.disagreement_bits, per_hypothesis: x.per_hypothesis ?? [], why: x.why ?? "",
      schedule: x.schedule ?? "", status: x.status ?? "proposed", simulated: x.simulated !== false, limits: x.limits ?? [],
    })),
    claims,
    post_hoc: { scores: (iv.scores as Raw[]).filter((x) => x.post_hoc).length, comparisons: (iv.comparisons as Raw[]).filter((x) => x.post_hoc).length },
    references: (iv.references as Raw[] ?? []).filter((r) => typeof r.n === "number").map((r) => ({
      name: r.name, n: r.n, ignited: r.ignited, ignition_interval: r.ignition_interval, limit: r.limit ?? "",
    })),
  };
  for (const f of files.values()) f.decisions.sort((a, b) => a.committed_seq - b.committed_seq);
  return { summary, trials: [...files.values()] };
}
