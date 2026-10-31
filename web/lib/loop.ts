// Loop campaigns: the LoopView (view_version 1) a live campaign and a recorded export share, the CampaignSummary the
// campaign list shows, and the pure helpers the loop screen words them with. The shapes follow
// translucent/loop_view.py (the server computes every field; nothing here invents one).

// ---------------------------------------------------------------- the view

export type Basis = "physical" | "computational" | "retrospective";
export type RequestState =
  | "awaiting_approval" | "queued" | "running" | "awaiting_result" | "completed" | "failed" | "rejected" | "cancelled";
export type Verdict = "candidate_better" | "not_better" | "violates_constraints" | "inconclusive" | "not_comparable";
export type Outcome = "improved" | "improved_in_simulation" | "not_improved" | "unresolved";
export type CampaignStatus =
  | "running" | "waiting_for_approval" | "waiting_for_result" | "concluded" | "stopped" | "finished";
export type NextKind = "awaiting_approval" | "running" | "awaiting_result" | "planner_thinking" | "none";

export interface Verified { command: string; summary: string; ok: boolean; ran_on: string }
export type LoopSource =
  | { kind: "live"; api: string; ledger: string; as_of_seq: number; generated_at: number }
  | { kind: "recorded"; file: string; sha256: string; exported_at: number; verified: Verified | null };

export interface Value { value: number | null; unit: string; h: number; not_reached_within?: unknown[] }
export interface Check { quantity: string; value: number | null; limit: number; ok: boolean | null; not_reached_within?: unknown }
export interface Predicted { value: number | null; lo?: number | null; hi?: number | null; unit: string }
export interface Param { value: number | string | boolean; unit: string }

export interface Target {
  quantity: string; unit: string; direction: "minimize" | "maximize" | string; margin: number; label: string; limit?: number;
}
export interface Constraint { quantity: string; op: string; limit: number; unit: string; label?: string | null }
export interface Amendment { version: number; seq: number; author: string; reason: string; changes: string[] }

export interface Baseline {
  basis: Basis; refs: string[]; values: Record<string, Value>; constraints: Check[]; fails: string[];
}
export interface CurrentResult { comparison_id: string; request_id: string; verdict: Verdict; basis: Basis; sentence: string }

export type NextAction =
  | {
    kind: "awaiting_approval"; request_id: string; version: number; title: string; content_hash: string;
    rationale: string; expected_effect: string; prediction: Record<string, Predicted>;
    cost: { usd?: number; wall_s?: number } & Record<string, unknown>; executor: string; actions: string[];
  }
  | { kind: "running" | "awaiting_result"; request_id: string; version: number; title: string; executor: string; actions: string[] }
  | { kind: "planner_thinking"; title: string }
  | { kind: "none" };

export interface Measurement { id: string; quantity: string; value: number; unit: string; h: number }
export interface SeriesRef { quantity: string; unit: string; url: string; n: number }
export interface PredictionError { predicted: number; measured: number; inside: boolean | null }
export interface LoopResult {
  result_id: string; basis: Basis; measurements: Measurement[]; series: SeriesRef[];
  prediction_error: Record<string, PredictionError>;
  /** Category outputs (quantity -> word), e.g. {"status": "ok", "validity": "extrapolation"}; not measurements. */
  categories?: Record<string, string>;
}
export interface TimelineStep { state: RequestState | "superseded"; seq: number; t: number; actor: string }
export interface LoopRequest {
  request_id: string; version: number; state: RequestState; action: string; executor: string;
  params: Record<string, Param>; proposed_by: string; approved_by: string | null; content_hash: string;
  rationale: string; prediction: Record<string, Predicted>; timeline: TimelineStep[]; result: LoopResult | null;
  rejections: { seq: number; reasons: string[] }[];
}
export interface Comparison {
  id: string; request_id: string; objective_version: number; basis: Basis;
  /** decided_by "feasibility" (loop-compare/2): better because it meets limits the baseline breaks, whatever the
   *  target difference, which is then the cost of meeting them. */
  target: { baseline: number | null; candidate: number | null; delta: number | null; better: boolean | null;
            decided_by?: "feasibility" };
  constraints: Check[]; verdict: Verdict; reasons: string[];
}
export interface Spend { model_usd: number; model_budget_usd: number; experiments_used: number; experiments_budget: number }
export interface Conclusion { outcome: Outcome; comparison_id: string | null; text: string; unresolved_reasons: string[] }

export interface LoopView {
  view_version: 1;
  source: LoopSource;
  campaign: {
    id: string; title: string; created_at: number; status: CampaignStatus; memory: string;
    system: { id: string; version: string; title: string; solver: { name: string; version: string; validity: string } | null };
    planner: { kind: string; models: { planner?: string | null; vision?: string | null } };
  };
  objective: { version: number; statement: string; target: Target; constraints: Constraint[]; amendments: Amendment[] };
  baseline: Baseline[];
  current_result: CurrentResult | null;
  next_action: NextAction;
  requests: LoopRequest[];
  comparisons: Comparison[];
  measurements_defined: { name: string; op: string; unit: string; defined_by: string }[];
  spend: Spend;
  conclusion: Conclusion | null;
  events_cursor: number;
}

// ---------------------------------------------------------------- the list and the series

export interface LedgerInfo { id: string; title: string; campaigns: number; datasets: number }
export interface CampaignSummary {
  id: string; ledger: string; kind: "loop" | "video" | "intervention"; title: string; status: CampaignStatus;
  created_at: number; updated_at: number; objective: string; next_action: NextKind; outcome: Outcome | null; spend_usd: number;
}
export interface CampaignPage { items: CampaignSummary[]; next_cursor: number | null }
export interface Series { t: number[]; y: number[]; unit: string; t_unit: string; n: number; downsampled: boolean }

// ---------------------------------------------------------------- recorded files

/** An embedded evidence file of a loop export's receipt. */
export interface PackedFile { sha256: string; bytes: number; encoding?: string; data: string }
/** A `<name>.loop.json` (translucent/loop_export.py): the recorded view and the receipt verify re-checks. */
export interface LoopExport {
  loop_export_version: 1; view: LoopView;
  receipt: { campaign_id: string; ledger_id: string; ledger: PackedFile; blobs: Record<string, PackedFile> };
}

/** What a recorded file holds: the view, the receipt's blobs (series bytes), and whether there was a receipt at all. */
export interface Recording { view: LoopView; blobs: Record<string, PackedFile>; receipt: boolean }

/**
 * A loop export, or a bare LoopView saved to a file. Anything else is refused with a sentence a person can act on.
 * Whatever the file's own source says, a file is a recording: the page never shows it as live.
 */
export function parseLoopFile(data: unknown): Recording {
  const o = data as Partial<LoopExport> & Partial<LoopView>;
  if (o && typeof o === "object" && "loop_export_version" in o) {
    if (o.loop_export_version !== 1) throw new Error(`This loop export is version ${String(o.loop_export_version)}; this page reads version 1.`);
    checkView(o.view);
    return { view: o.view as LoopView, blobs: o.receipt?.blobs ?? {}, receipt: !!o.receipt };
  }
  checkView(o);
  return { view: o as LoopView, blobs: {}, receipt: false };
}

function checkView(v: unknown): asserts v is LoopView {
  const o = v as Partial<LoopView> | null;
  if (!o || typeof o !== "object" || !("view_version" in o)) throw new Error("This file is not a loop campaign export.");
  if (o.view_version !== 1) throw new Error(`This loop view is version ${String(o.view_version)}; this page reads version 1.`);
  if (!o.campaign || !o.objective || !Array.isArray(o.requests)) throw new Error("This loop view is missing its campaign, objective or requests.");
}

/** The artifact id in a series URL (".../artifacts/sha256:..."), or null. */
export function artifactIdOf(url: string): string | null {
  const m = url.match(/\/artifacts\/(sha256:[0-9a-f]{64})$/);
  return m ? m[1] : null;
}

/** One series from a recording's receipt (gzip + base64 JSON {t, y, unit}), or null when the file does not carry it. */
export async function seriesFromBlob(blobs: Record<string, PackedFile>, url: string): Promise<Series | null> {
  const id = artifactIdOf(url);
  const packed = id ? blobs[id] : undefined;
  if (!packed) return null;
  const bytes = Uint8Array.from(atob(packed.data), (c) => c.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const body = JSON.parse(await new Response(stream).text()) as { t?: number[]; y?: number[]; unit?: string };
  if (!Array.isArray(body.t) || !Array.isArray(body.y)) return null;
  return { t: body.t, y: body.y, unit: body.unit ?? "", t_unit: "s", n: body.t.length, downsampled: false };
}

/** At most `max` points, evenly strided (as the server's series endpoint downsamples). */
export function thin(s: Series, max = 1200): Series {
  if (s.t.length <= max) return s;
  const step = Math.ceil(s.t.length / max);
  return { ...s, t: s.t.filter((_, i) => i % step === 0), y: s.y.filter((_, i) => i % step === 0), downsampled: true };
}

// ---------------------------------------------------------------- numbers and units

const UNIT_WORD: Record<string, string> = { degC: "°C", degF: "°F", "1/h": "C", "1": "", percent: "%" };
export const unitWord = (u: string) => UNIT_WORD[u] ?? u;

/** A number to read at a glance: whole above 100, one decimal above 10, else up to three significant digits. */
export function num(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "–";
  const a = Math.abs(n);
  const s = a >= 100 ? Math.round(n).toString() : a >= 10 ? n.toFixed(1) : a >= 1 ? n.toFixed(2) : n.toPrecision(2);
  return (s.includes(".") ? s.replace(/\.?0+$/, "") : s).replace(/^-/, "−");
}

/** "43.9 °C", "2.5C" (a C-rate sticks to its number), "1620 s". */
export function withUnit(n: number | null | undefined, unit: string): string {
  const u = unitWord(unit);
  if (n == null) return "–";
  if (unit === "1/h") return `${num(n)}C`;
  return u ? `${num(n)} ${u}` : num(n);
}

/** "2880 s ± 5". */
export function valueLine(v: Value): string {
  if (v.value == null) return "not reached";
  return showsH(v.value, v.h) ? `${withUnit(v.value, v.unit)} ± ${num(v.h)}` : withUnit(v.value, v.unit);
}

/** Whether an uncertainty is worth printing: not zero, and not negligible against the value (a solver's 1e-9). */
export const showsH = (value: number, h: number) => h > 0 && h >= Math.abs(value) * 1e-4;

/** "1650 s (1550–1750)". */
export function predictedLine(p: Predicted): string {
  if (p.value == null) return "–";
  const range = p.lo != null && p.hi != null ? ` (${num(p.lo)}–${num(p.hi)})` : "";
  return `${withUnit(p.value, p.unit)}${range}`;
}

const OP_WORD: Record<string, string> = { "<=": "≤", ">=": "≥", "<": "<", ">": ">", "==": "=" };
export const opWord = (op: string) => OP_WORD[op] ?? op;

/** The objective's own label for a constraint, else "max_temperature ≤ 45 °C". */
export function constraintLine(c: Constraint): string {
  return c.label?.trim() || `${humanize(c.quantity)} ${opWord(c.op)} ${withUnit(c.limit, c.unit)}`;
}

/** "Lower time to 80% charge" / "Higher capacity retention". */
export function targetLine(t: Target): string {
  const word = t.direction === "minimize" ? "Lower" : t.direction === "maximize" ? "Higher" : "Reach";
  const label = t.label || humanize(t.quantity);
  return `${word} ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
}

/** "simulate_protocol" -> "Simulate protocol". */
export function humanize(name: string): string {
  const s = name.replace(/^derived:/, "").replace(/_/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** "c_rate_1 2.5C · switch_soc 0.5". */
export function paramsLine(params: Record<string, Param>): string {
  return Object.entries(params)
    .map(([k, p]) => `${k} ${typeof p.value === "number" ? withUnit(p.value, p.unit) : String(p.value)}`)
    .join(" · ");
}

/** A request's one-line title: its action and parameters. */
export const requestTitle = (r: Pick<LoopRequest, "action" | "params">) => {
  const p = paramsLine(r.params);
  return p ? `${humanize(r.action)}: ${p}` : humanize(r.action);
};

// ---------------------------------------------------------------- who and what state

/** "person:Ana" -> "Ana", "agent:muse-wonder-1.0" -> "the agent (muse-wonder-1.0)", "policy:auto/1" -> "auto-approved by policy". */
export function actorWord(actor: string | null | undefined): string {
  if (!actor) return "";
  const [kind, ...rest] = actor.split(":");
  const name = rest.join(":");
  if (kind === "person") return name;
  if (kind === "agent") return name ? `the agent (${name})` : "the agent";
  if (actor === "policy:registration") return "the objective (baseline run)";
  if (kind === "policy") return "auto-approved by policy";
  if (kind === "system") return name === "runner" ? "the runner" : `the system (${name})`;
  return actor;
}

export const STATE_WORD: Record<RequestState, string> = {
  awaiting_approval: "Proposed, awaiting approval", queued: "Approved, queued", running: "Running",
  awaiting_result: "Approved, awaiting a person’s result", completed: "Completed", failed: "Failed",
  rejected: "Rejected", cancelled: "Cancelled",
};
/** Short words for the state badge. */
export const STATE_SHORT: Record<RequestState, string> = {
  awaiting_approval: "Awaiting approval", queued: "Queued", running: "Running", awaiting_result: "Awaiting result",
  completed: "Completed", failed: "Failed", rejected: "Rejected", cancelled: "Cancelled",
};
/** good / bad / wait / done / off: the badge's tone. */
export function stateTone(s: RequestState): "wait" | "busy" | "good" | "bad" | "off" {
  if (s === "awaiting_approval" || s === "awaiting_result") return "wait";
  if (s === "queued" || s === "running") return "busy";
  if (s === "completed") return "good";
  if (s === "failed") return "bad";
  return "off";
}

/** "Runs in simulation" / "A person runs it" (the executor decides who returns the result). */
export const executorWord = (e: string) => e === "computational" ? "Runs in simulation" : e === "human" ? "A person runs it and returns the result" : humanize(e);

export const BASIS_WORD: Record<string, string> = { computational: "Simulated", physical: "Measured", retrospective: "From records" };

// ---------------------------------------------------------------- outcome and the best candidate

export const OUTCOME_WORD: Record<Outcome, string> = {
  improved: "Improved", improved_in_simulation: "Improved in simulation", not_improved: "Not improved", unresolved: "Unresolved",
};
export const VERDICT_WORD: Record<Verdict, string> = {
  candidate_better: "Better than the baseline", not_better: "Not better than the baseline",
  violates_constraints: "Breaks a constraint", inconclusive: "Inconclusive within the uncertainty",
  not_comparable: "Not comparable with the baseline",
};

/** The outcome a comparison points to (the conclusion's rule: a simulated improvement is its own outcome). */
export function outcomeOfVerdict(c: Pick<Comparison, "verdict" | "basis">): Outcome {
  if (c.verdict === "candidate_better") return c.basis === "computational" ? "improved_in_simulation" : "improved";
  if (c.verdict === "not_better" || c.verdict === "violates_constraints") return "not_improved";
  return "unresolved";
}

/**
 * The comparison the screen leads with: the one the server names as the current result (or the conclusion's), else the
 * best on the current objective version (a better candidate first, the largest improvement first), else the latest.
 */
export function bestComparison(v: LoopView): Comparison | null {
  const named = v.current_result?.comparison_id ?? v.conclusion?.comparison_id ?? null;
  if (named) {
    const c = v.comparisons.find((x) => x.id === named);
    if (c) return c;
  }
  const current = v.comparisons.filter((c) => c.objective_version === v.objective.version);
  if (!current.length) return null;
  const better = current.filter((c) => c.verdict === "candidate_better");
  if (better.length) return better.reduce((a, b) => ((b.target.delta ?? -Infinity) > (a.target.delta ?? -Infinity) ? b : a));
  return current[current.length - 1];
}

/** The outcome to show, and whether it is final (concluded) or only the standing so far. Null before any result. */
export function standing(v: LoopView): { outcome: Outcome; final: boolean } | null {
  if (v.conclusion) return { outcome: v.conclusion.outcome, final: true };
  const c = bestComparison(v);
  return c ? { outcome: outcomeOfVerdict(c), final: false } : null;
}

/** The baseline measured on the same basis as the candidate (a simulated candidate is compared with a simulated baseline). */
export function baselineFor(v: LoopView, basis: Basis | undefined): Baseline | null {
  return v.baseline.find((b) => b.basis === basis)
    ?? v.baseline.find((b) => Object.keys(b.values).length > 0)    // before any comparison: the one with numbers
    ?? v.baseline[0] ?? null;
}

/** The candidate's change against the baseline, signed (candidate − baseline), and whether it is the better way. */
export function change(c: Comparison): { delta: number; better: boolean | null } | null {
  const { baseline, candidate, better } = c.target;
  if (baseline == null || candidate == null) return null;
  return { delta: candidate - baseline, better };
}

/** The constraint a check measures (by quantity), for its label and unit. */
export const constraintOf = (v: LoopView, quantity: string) => v.objective.constraints.find((c) => c.quantity === quantity);

/** Requests newest first by their last step, so the live one is on top. */
export function byRecency(v: LoopView): LoopRequest[] {
  const last = (r: LoopRequest) => r.timeline[r.timeline.length - 1]?.seq ?? 0;
  return [...v.requests].sort((a, b) => last(b) - last(a));
}

/** How many requests are in each state, in a fixed reading order (only the states that occur). */
export function stateCounts(v: LoopView): [RequestState, number][] {
  const order: RequestState[] = ["awaiting_approval", "queued", "running", "awaiting_result", "completed", "failed", "rejected", "cancelled"];
  return order.map((s) => [s, v.requests.filter((r) => r.state === s).length] as [RequestState, number]).filter(([, n]) => n > 0);
}

/** Model spend and experiments against their budgets: "$0.31 of $5", "1 of 4". */
export function spendLines(s: Spend): { model: string; experiments: string; modelShare: number; experimentShare: number } {
  const usd = (n: number) => `$${n < 10 ? n.toFixed(2).replace(/\.00$/, "") : Math.round(n)}`;
  return {
    model: s.model_budget_usd > 0 ? `${usd(s.model_usd)} of ${usd(s.model_budget_usd)}` : usd(s.model_usd),
    experiments: s.experiments_budget > 0 ? `${s.experiments_used} of ${s.experiments_budget}` : String(s.experiments_used),
    modelShare: s.model_budget_usd > 0 ? Math.min(1, s.model_usd / s.model_budget_usd) : 0,
    experimentShare: s.experiments_budget > 0 ? Math.min(1, s.experiments_used / s.experiments_budget) : 0,
  };
}

/** Can a person act on this view? Never on a recording, and on a live one only with a team key and a name. */
export function canAct(mode: "live" | "recorded", key: string, name: string): boolean {
  return mode === "live" && !!key.trim() && !!name.trim();
}

/** "3 s ago", "4 min ago", "2 h ago". */
export function ago(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

/** The banner's words: "LIVE · acme · updated 3 s ago" or "RECORDED · fastcharge.loop.json · verified". */
export function bannerLine(mode: "live" | "recorded", v: LoopView, opts: { ledger?: string; secondsSince?: number; file?: string; receipt?: boolean }): { tag: string; line: string } {
  if (mode === "live") {
    return { tag: "LIVE", line: [opts.ledger ?? (v.source.kind === "live" ? v.source.ledger : ""), opts.secondsSince != null ? `updated ${ago(opts.secondsSince)}` : ""].filter(Boolean).join(" · ") };
  }
  const src = v.source.kind === "recorded" ? v.source : null;
  const file = opts.file ?? src?.file ?? "file";
  const check = !opts.receipt ? "a saved view, no receipt to verify"
    : src?.verified ? (src.verified.ok ? `verified ${src.verified.ran_on}: ${src.verified.summary}` : `verify failed ${src.verified.ran_on}: ${src.verified.summary}`)
      : `not verified here; check it with translucent verify ${file}`;
  return { tag: "RECORDED", line: `${file} · ${check}` };
}

/** Plain words for a campaign's state in the list. */
export const STATUS_WORD: Record<CampaignStatus, string> = {
  running: "Running", waiting_for_approval: "Waiting for approval", waiting_for_result: "Waiting for a result",
  concluded: "Concluded", stopped: "Stopped", finished: "Finished",
};
export const NEXT_WORD: Record<NextKind, string> = {
  awaiting_approval: "An experiment needs your approval", running: "An experiment is running",
  awaiting_result: "Waiting for a person’s result", planner_thinking: "The agent is choosing the next experiment", none: "",
};
export const KIND_WORD: Record<CampaignSummary["kind"], string> = { loop: "Experiment loop", video: "Recording analysis", intervention: "Intervention" };

/** The loop screen's address for a live campaign, or for a recording under /fixtures/loop/. */
export const loopHref = (ledger: string, id: string) => `/w/?ledger=${encodeURIComponent(ledger)}&id=${encodeURIComponent(id)}`;
export const recordingHref = (name: string) => `/w/?recording=${encodeURIComponent(name)}`;

/** A recording's name as the address carries it: letters, digits, dot, dash and underscore only (no paths). */
export const RECORDING_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$/;
export const recordingUrl = (name: string) => `/fixtures/loop/${name.replace(/\.loop\.json$/, "")}.loop.json`;

/**
 * The constraint limit to draw on a series chart: a constraint in the series' unit whose quantity names the series
 * (max_temperature on a temperature series). Null when none fits; the chart never guesses a conversion.
 */
export function limitFor(v: LoopView, seriesQuantity: string, seriesUnit: string): { value: number; label: string } | null {
  const c = v.objective.constraints.find((x) => x.unit === seriesUnit && x.quantity.split(/[_:]/).includes(seriesQuantity));
  return c ? { value: c.limit, label: `${opWord(c.op)} ${withUnit(c.limit, c.unit)}` } : null;
}

/**
 * A result's warning to show beside its numbers: the solver ran outside its validated range (validity =
 * extrapolation). Null when the result says nothing of the kind.
 */
export function validityWarning(r: Pick<LoopResult, "categories"> | null | undefined): string | null {
  const v = r?.categories?.validity;
  if (!v || v === "ok" || v === "valid" || v === "interpolation") return null;
  if (v === "extrapolation") return "Extrapolation: outside the model’s validated range. Treat this number as a lead, not a result.";
  return `Validity: ${humanize(v)}`;
}

/** Category outputs other than validity, as "Status ok · Cv entered no". */
export function categoriesLine(r: Pick<LoopResult, "categories"> | null | undefined): string {
  return Object.entries(r?.categories ?? {}).filter(([k]) => k !== "validity").map(([k, v]) => `${humanize(k)} ${v}`).join(" · ");
}
