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
  failure?: { attempt: number; reason: string; retryable: boolean };
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

/** The list's objective is the statement's first 140 characters (api_v1, loop_view). */
export const OBJECTIVE_CUT = 140;
/** A summary's objective, ending at a word with "…" when the server cut it ("…vs Li/L" -> "…vs …"). */
export function objectiveLine(text: string, cut = OBJECTIVE_CUT): string {
  if (text.length < cut) return text;
  const at = text.lastIndexOf(" ");
  return `${(at > 0 ? text.slice(0, at) : text).replace(/[\s,;:–-]+$/, "")}…`;
}
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

/** The objective's own label for a constraint (units tidied), else "Max temperature ≤ 45 °C". */
export function constraintLine(c: Constraint): string {
  return c.label?.trim() ? prose(c.label.trim()) : `${humanize(c.quantity)} ${opWord(c.op)} ${withUnit(c.limit, c.unit)}`;
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

/** An id as words, keeping engineering abbreviations: "c_rate_1" -> "C-rate 1", "cv_entered" -> "CV entered". */
export function idWords(name: string): string {
  const s = humanize(name)
    .replace(/\bc rate\b/gi, "C-rate").replace(/\b(soc|soh|cv|cc|ocv|dod)\b/gi, (m) => m.toUpperCase())
    .replace(/^Min /, "Lowest ").replace(/^Max /, "Peak ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Server text made readable: "45 degC" -> "45 °C", "1.3 1/h" -> "1.3C", ">=" -> "≥". Words are left alone. */
export function prose(text: string): string {
  return text
    .replace(/(\d)\s*1\/h\b/g, "$1C")
    .replace(/\bdegC\b/g, "°C").replace(/\bdegF\b/g, "°F")
    .replace(/(\d)\s*°C/g, "$1 °C")
    .replace(/\s*>=\s*/g, " ≥ ").replace(/\s*<=\s*/g, " ≤ ").replace(/\+\/-/g, "±");
}

/** Display only: a number stays on one line with its unit or its relation ("80 %", "45 °C", "≥ 10 mV"). */
export function unbroken(text: string): string {
  return text
    .replace(/([≥≤<>=±]) (?=[−-]?\d)/g, "$1 ")
    .replace(/(\d) (?=(%|°C|°F|mV|V|mA|A|Ah|mAh|min|s|h|K|W)(?![\w]))/g, "$1 ");
}

// ---------------------------------------------------------------- labels from the system spec

/** The parts of a SystemSpec (GET /ledgers/{lid}/systems) the page words things with. */
export interface SystemSpecLite {
  id: string;
  quantities: { name: string; label?: string; unit?: string }[];
  actions: { name: string; label?: string; parameters: { name: string; label?: string }[] }[];
}

/** How the page names quantities, parameters and actions: the system's own labels when it has them, else the ids as words. */
export interface Words {
  /** "Peak lumped cell temperature" (the label without its parenthetical note). */
  quantity(q: string): string;
  /** The parenthetical note of a quantity's label ("indicative, unvalidated"), or "". */
  note(q: string): string;
  /** "Ambient temperature"; a stepped parameter's group label for "c_rate_2" is "Charge C-rate". */
  param(name: string): string;
  action(name: string): string;
}

const NOTE = /\s*\(([^)]*)\)\s*$/;
const stripNote = (label: string) => label.replace(NOTE, "").trim();

/** Words for one campaign: the objective's target label first, then the system spec's labels, then the ids. */
export function wordsFor(v: Pick<LoopView, "objective" | "campaign">, specs?: SystemSpecLite[] | null): Words {
  const spec = specs?.find((s) => s.id === v.campaign.system.id) ?? null;
  const q = new Map((spec?.quantities ?? []).filter((x) => x.label).map((x) => [x.name, x.label!]));
  const p = new Map<string, string>(), a = new Map<string, string>();
  for (const act of spec?.actions ?? []) {
    if (act.label) a.set(act.name, act.label);
    for (const par of act.parameters) if (par.label && !p.has(par.name)) p.set(par.name, par.label);
  }
  const t = v.objective.target;
  return {
    quantity: (name) => (name === t.quantity && t.label ? t.label : q.has(name) ? stripNote(q.get(name)!) : idWords(name)),
    note: (name) => (name === t.quantity ? "" : q.get(name)?.match(NOTE)?.[1] ?? ""),
    // "Charge C-rate, 10-27.5 % state of charge" -> "Charge C-rate": the window is the step's, the name the group's.
    param: (name) => (p.has(name) ? stripNote(p.get(name)!.split(",")[0]) : idWords(name.replace(/_\d+$/, ""))),
    action: (name) => a.get(name) ?? idWords(name),
  };
}

/** Words from the ids alone (no spec, no objective): what a test or a bare request gets. */
const ID_WORDS: Words = { quantity: idWords, note: () => "", param: (n) => idWords(n.replace(/_\d+$/, "")), action: idWords };

const paramValue = (p: Param) => (typeof p.value === "number" ? withUnit(p.value, p.unit) : String(p.value));

/**
 * "Charge C-rate 1.3C → 1.05C → 0.95C → 0.75C · Ambient temperature 25 °C". Numbered parameters of one unit
 * (c_rate_1..4) are one stepped group, read in step order; a lone parameter is "Switch SOC 0.5".
 */
export function paramsLine(params: Record<string, Param>, words: Words = ID_WORDS): string {
  const groups = new Map<string, { label: string; items: [number, Param][] }>();
  const singles: [string, Param][] = [];
  for (const [k, p] of Object.entries(params)) {
    const m = k.match(/^(.*)_(\d+)$/);
    if (m) {
      const key = `${m[1]}|${p.unit}`;
      const g = groups.get(key) ?? { label: words.param(k), items: [] };
      g.items.push([Number(m[2]), p]);
      groups.set(key, g);
    } else singles.push([k, p]);
  }
  const out: string[] = [];
  for (const [key, g] of groups) {
    if (g.items.length < 2) { singles.unshift([`${key.split("|")[0]}_${g.items[0][0]}`, g.items[0][1]]); continue; }
    out.push(`${g.label} ${g.items.sort((x, y) => x[0] - y[0]).map(([, p]) => paramValue(p)).join(" → ")}`);
  }
  for (const [k, p] of singles) out.push(`${/_\d+$/.test(k) ? idWords(k) : words.param(k)} ${paramValue(p)}`);
  return out.join(" · ");
}

/** A request's one-line title: its action and parameters. */
export const requestTitle = (r: Pick<LoopRequest, "action" | "params">, words: Words = ID_WORDS) => {
  const p = paramsLine(r.params, words);
  return p ? `${words.action(r.action)}: ${p}` : words.action(r.action);
};

// ---------------------------------------------------------------- the verdict, in words

const lowerFirst = (s: string) => (/^[A-Z][a-z]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s);
const listWords = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);
const BASIS_LEAD: Record<string, string> = { computational: "In simulation", physical: "On the bench", retrospective: "In the records" };

/**
 * Whether a better verdict was decided by the limits (loop-compare/2 rule 5, "feasibility"): the candidate meets
 * every limit while the baseline breaks one, so its target difference, even a worse one, is the cost of meeting them.
 * The view leaves `decided_by` out, so this reads the recorded verdict: the comparator records candidate_better with a
 * target that is not better only under rule 5 (rules 3-4 need a better target). Nothing here re-decides it.
 */
export function decidedByLimits(c: Comparison): boolean {
  return c.verdict === "candidate_better" && (c.target.decided_by === "feasibility" || c.target.better !== true);
}

const trimNum = (s: string) => (s.includes(".") && !s.includes("e") ? s.replace(/\.?0+$/, "") : s).replace(/^-/, "−");
/**
 * A value beside its limit, at a precision that keeps their order: 9.996 against ≥ 10 reads "9.996 mV", never
 * "10 mV" (which would read as holding). Otherwise as `withUnit`.
 */
export function limitValue(value: number | null, limit: number, unit: string): string {
  if (value == null) return withUnit(value, unit);
  let a = num(value), b = num(limit);
  const read = (x: string) => Number(x.replace("−", "-"));
  for (let p = 4; p <= 8 && Math.sign(read(a) - read(b)) !== Math.sign(value - limit); p++) {
    a = trimNum(value.toPrecision(p)); b = trimNum(limit.toPrecision(p));
  }
  if (unit === "1/h") return `${a}C`;
  return unitWord(unit) ? `${a} ${unitWord(unit)}` : a;
}

/** "lowest anode potential 25.1 mV (limit ≥ 10 mV)": the check's own recorded limit, the objective's relation and unit. */
function checkWords(v: LoopView, ch: Check, words: Words): string {
  const con = constraintOf(v, ch.quantity);
  const unit = con?.unit ?? "";
  const limit = con ? ` (limit ${opWord(con.op)} ${withUnit(ch.limit, unit)})` : "";
  return `${lowerFirst(words.quantity(ch.quantity))} ${ch.value == null ? "not reached" : limitValue(ch.value, ch.limit, unit)}${limit}`;
}

/**
 * The recorded comparison as an engineer would say it, from its numbers (never by rewording the server's sentence,
 * and never deciding anything the verdict did not): "In simulation, the candidate's time from 10 to 80 % SOC is
 * 43.1 min against 28.2 min for the baseline. Both limits hold: … It counts as better because the baseline breaks
 * both limits; meeting them costs 14.9 min."
 */
export function verdictSentence(v: LoopView, c: Comparison, words: Words = wordsFor(v)): string {
  const t = v.objective.target;
  const lead = BASIS_LEAD[c.basis] ?? "So far";
  const name = lowerFirst(words.quantity(t.quantity));
  const out: string[] = [];
  out.push(c.target.candidate == null
    ? `${lead}, the candidate returned no ${name}.`
    : `${lead}, the candidate’s ${name} is ${withUnit(c.target.candidate, t.unit)}${c.target.baseline != null ? ` against ${withUnit(c.target.baseline, t.unit)} for the baseline` : ""}.`);
  const holds = c.constraints.filter((x) => x.ok === true), broken = c.constraints.filter((x) => x.ok === false);
  // ok null: not measured (no value and no window), else its uncertainty straddles the limit.
  const missing = c.constraints.filter((x) => x.ok == null && x.value == null && x.not_reached_within == null);
  const close = c.constraints.filter((x) => x.ok == null && !missing.includes(x));
  if (broken.length) out.push(`${broken.length === c.constraints.length && broken.length > 1 ? "Both limits are broken" : broken.length > 1 ? "Limits broken" : "A limit is broken"}: ${listWords(broken.map((x) => checkWords(v, x, words)))}.`);
  if (holds.length) out.push(`${holds.length === c.constraints.length ? (holds.length === 2 ? "Both limits hold" : holds.length > 2 ? "Every limit holds" : "The limit holds") : holds.length > 1 ? "These hold" : "This one holds"}: ${listWords(holds.map((x) => checkWords(v, x, words)))}.`);
  if (close.length) out.push(`Too close to the limit to call: ${listWords(close.map((x) => checkWords(v, x, words)))}.`);
  if (missing.length) out.push(`Not measured: ${listWords(missing.map((x) => lowerFirst(words.quantity(x.quantity))))}.`);
  // delta is the recorded difference in the better direction (positive: better).
  const delta = c.target.delta;
  const by = (d: number) => withUnit(Math.abs(d), t.unit);
  if (decidedByLimits(c)) {
    const base = c.objective_version === v.objective.version ? baselineFor(v, c.basis) : null;
    const failed = base?.constraints.filter((x) => x.ok === false) ?? [];
    const which = failed.length === v.objective.constraints.length && failed.length === 2 ? "both limits"
      : failed.length > 1 ? "those limits" : failed.length ? `the ${lowerFirst(words.quantity(failed[0].quantity))} limit` : "a limit";
    out.push(`It counts as better because the baseline breaks ${which}${delta != null && delta < 0 ? `; meeting ${failed.length > 1 ? "them" : "it"} costs ${by(delta)}` : ""}.`);
  } else if (c.verdict === "candidate_better") {
    out.push(delta == null ? "Better than the baseline."
      : delta > 0 ? `Better than the baseline by ${by(delta)}.`
      : `It counts as better: ${delta < 0 ? `${by(delta)} worse, ` : ""}within the ${by(t.margin)} the objective accepts.`);
  } else if (c.verdict === "violates_constraints") {
    out.push("A broken limit rules it out, whatever the target says.");
  } else if (c.verdict === "not_better") {
    // Not better by more than the margin; or, when the difference alone clears the margin, its own absolute limit missed.
    out.push(t.limit != null && delta != null && delta > t.margin ? `It misses the target’s own limit of ${withUnit(t.limit, t.unit)}.`
      : t.margin > 0 ? `Not better than the baseline by more than the ${by(t.margin)} margin.`
      : t.margin < 0 ? `Not better than the baseline, even allowing the ${by(t.margin)} the objective accepts.`
      : "Not better than the baseline.");
  } else if (c.verdict === "inconclusive") {
    out.push("Within the uncertainty it is too close to call, so it decides nothing yet.");
  } else {
    out.push("It can’t be compared with the baseline.");
  }
  return out.join(" ");
}

/** Why a comparison went the way it did, short: the broken limits, else the server's reasons tidied. */
export function reasonsLine(v: LoopView, c: Comparison, words: Words = wordsFor(v)): string {
  const broken = c.constraints.filter((x) => x.ok === false);
  if (broken.length) return `breaks ${listWords(broken.map((x) => checkWords(v, x, words)))}`;
  return c.reasons.map(prose).join("; ");
}

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

/** Baseline policies (planner kind domain_baseline, agent "baseline-<policy>") as people say them. */
const POLICY_WORD: Record<string, string> = { "constrained-bo": "constrained Bayesian optimisation" };

/**
 * The campaign's planner named by its kind (campaign.planner), so a fixed policy is not called an agent:
 * "constrained Bayesian optimisation", "Muse Wonder", or an outside client's own name. `name` is an agent actor's name.
 */
export function plannerWord(v: Pick<LoopView, "campaign">, name?: string): string {
  const { kind, models } = v.campaign.planner;
  const id = name || models.planner || "";
  if (kind === "domain_baseline") {
    const policy = id.replace(/^baseline-/, "");
    return POLICY_WORD[policy] ?? (policy ? `the ${policy.replace(/[-_]+/g, " ")} baseline policy` : "the baseline policy");
  }
  if (kind === "muse" || kind === "muse_no_feedback") {
    const model = (models.planner || name || "").split("/").pop()!.replace(/-\d+(\.\d+)*$/, "");
    return model ? model.split(/[-_]+/).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ") : "Muse Wonder";
  }
  if (kind === "scripted") return "a scripted planner";
  if (kind === "external") return id || "an outside client";
  return id || "the agent";
}

const PLANNER_KINDS = new Set(["domain_baseline", "muse", "muse_no_feedback", "scripted", "external"]);

/** Whether the planner is an agent (a model, or an outside client planning itself), not a fixed policy or a script. */
export const plannerIsAgent = (v: Pick<LoopView, "campaign">) => !["domain_baseline", "scripted"].includes(v.campaign.planner.kind);

/** `actorWord`, with an agent actor named by the campaign's planner ("Proposed by constrained Bayesian optimisation"). */
export function actorIn(v: Pick<LoopView, "campaign">, actor: string | null | undefined): string {
  return actor?.startsWith("agent:") && PLANNER_KINDS.has(v.campaign.planner.kind)
    ? plannerWord(v, actor.slice("agent:".length)) : actorWord(actor);
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
  if (!v || VALID.has(v)) return null;
  if (v === "extrapolation") return "Extrapolation: outside the model’s validated range. Treat this number as a lead, not a result.";
  return `Validity: ${humanize(v).toLowerCase()}`;
}
/** Validity words that mean the inputs lie inside the model's range: nothing to warn about. */
const VALID = new Set(["ok", "valid", "interpolation", "within_range", "in_range", "inside_range"]);

/** Category outputs other than validity, as "Status ok · CV entered no". */
export function categoriesLine(r: Pick<LoopResult, "categories"> | null | undefined): string {
  return Object.entries(r?.categories ?? {}).filter(([k]) => k !== "validity").map(([k, v]) => `${idWords(k)} ${humanize(v).toLowerCase()}`).join(" · ");
}
