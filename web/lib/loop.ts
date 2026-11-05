// Loop campaigns: the LoopView (view_version 1) a live campaign and a recorded export share, the CampaignSummary the
// campaign list shows, and the pure helpers the loop screen words them with. The shapes follow
// translucent/loop_view.py (the server computes every field; nothing here invents one).
import type { Assessment } from "./assessment";
import { withBase } from "./base";

// ---------------------------------------------------------------- the view

export type Basis = "physical" | "computational" | "retrospective";
export type RequestState =
  | "awaiting_approval" | "queued" | "running" | "awaiting_result" | "completed" | "failed" | "rejected" | "cancelled";
export type Verdict = "candidate_better" | "not_better" | "violates_constraints" | "inconclusive" | "not_comparable";
/** loop-compare/1-2 outcomes, and loop-compare/3's (each with an "_in_simulation" form when computational). */
export type Outcome3 = "objective_met" | "faster_than_reference" | "feasible_only" | "no_feasible_result" | "unresolved";
export type Outcome = "improved" | "improved_in_simulation" | "not_improved" | "unresolved"
  | Outcome3 | `${Outcome3}_in_simulation`;
export type CampaignStatus =
  | "running" | "waiting_for_approval" | "waiting_for_result" | "concluded" | "stopped" | "finished";
export type NextKind = "awaiting_approval" | "running" | "awaiting_result" | "planner_thinking" | "planner_stopped" | "none";

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
  | { kind: "planner_stopped"; status: "budget"; title: string }
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
  /** M2: system reference runs and the baseline re-run are listed but marked. Absent in older views. */
  role?: "experiment" | "baseline" | "reference";
  /** The objective version the request was proposed under (absent in older views). */
  objective_version?: number;
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
export interface Conclusion {
  outcome: Outcome; comparison_id: string | null; text: string; unresolved_reasons: string[];
  /** loop-compare/3: the three answers, frozen at conclusion. */
  findings?: Pick<Assessment, "feasibility" | "speed" | "objective">;
}

export interface LoopView {
  view_version: 1;
  source: LoopSource;
  campaign: {
    id: string; title: string; created_at: number; status: CampaignStatus; memory: string;
    system: { id: string; version: string; title: string; solver: { name: string; version: string; validity: string } | null };
    planner: { kind: string; models: { planner?: string | null; vision?: string | null } };
  };
  objective: {
    version: number; statement: string; target: Target; constraints: Constraint[]; amendments: Amendment[];
    /** M2 (optional, so older recordings load): the objective's kind, a one-line summary of the CURRENT version, and
     *  the version whose registered statement `statement` is. */
    kind?: "fastest_feasible" | "hard_target" | "legacy"; summary?: string; statement_version?: number;
  };
  baseline: Baseline[];
  current_result: CurrentResult | null;
  next_action: NextAction;
  requests: LoopRequest[];
  comparisons: Comparison[];
  measurements_defined: { name: string; op: string; unit: string; defined_by: string }[];
  spend: Spend;
  conclusion: Conclusion | null;
  events_cursor: number;
  /** M2: feasibility, speed and objective as three answers (absent in older recordings; see lib/assessment.ts). */
  assessment?: Assessment;
  /** A system that keeps model versions (pybamm-fastcharge-lab): what the campaign believes and why. Absent otherwise. */
  learning?: Learning;
  /** A campaign opened from the engineer's raw records: what came from the files, what the model assumes, what the
   *  engineer set. Absent for every other campaign. */
  investigation?: Investigation;
}

// ---------------------------------------------------------------- thermal learning (thermal-learning/1)
// LabAdapter.campaign_summary in translucent/systems/lgm50_lab.py. Every field optional: older or partial views load.

/** Model parameters by name: h (W/m^2/K) and k (thermal-mass scale, no unit). */
export type ModelParams = Record<string, number>;
export interface ModelCheck {
  request_id?: string; result_id?: string; lab_tests?: number[];
  heldout_rmse_K?: number | null; heldout_max_error_K?: number | null; coverage?: number | null;
}
export interface ModelVersion {
  version: number; family?: string; estimate?: ModelParams; members?: (ModelParams & { delta_chi2?: number })[];
  region_95?: Record<string, [number, number]> | null;
  validity?: { ambient_degC?: [number, number]; c_rate?: [number, number]; rest_min?: [number, number] | number[]; lab_tests?: number[] } | null;
  /** null for v0, which no held-out test has checked. */
  adequate?: boolean | null; consistent_with_nominal?: boolean | null; fit_rmse_K?: number | null;
  result_id?: string | null; request_id?: string | null; source?: string | null; sources?: number[]; checks?: ModelCheck[];
  /** Whether the version may stand behind a design, and why (recordings made under the standing rule). */
  standing?: { supported: boolean; reason?: string | null } | null;
}
export interface LabTrace {
  t_min: number[]; observed_degC: (number | null)[];
  forecast_degC?: (number | null)[] | null; forecast_lo_degC?: (number | null)[] | null; forecast_hi_degC?: (number | null)[] | null;
}
/** A record test's stretch of the engineer's record: one constant-current segment, not a charge protocol. */
export interface RecordStretch {
  ref?: string; segment?: number; mode?: string; t_start_s?: number; t_end_s?: number;
  /** A (magnitude), C-rate (1/h), min, °C, K, °C, and the current's largest departure from its median (relative). */
  test_current?: number | null; test_c_rate?: number | null; test_duration?: number | null;
  temperature_start?: number | null; temperature_rise?: number | null; ambient_temperature?: number | null; current_departure?: number | null;
}
export interface LabTest {
  n: number; request_id?: string; result_id?: string; action?: string; basis?: string;
  protocol?: Record<string, number | null>;
  /** Present for a record test: the stretch it is; its protocol is then empty. */
  record?: RecordStretch | null;
  observed?: { max_temperature?: number | null; temperature_end?: number | null; charge_time_80?: number | null };
  prediction?: unknown;
  /** The last forecast of the same protocol made before the test ran, and the model version it came from. */
  forecast?: { request_id?: string; result_id?: string; model_version: number; max_temperature?: number | null; min_anode_potential?: number | null } | null;
  trace?: LabTrace | null;
}
export interface Design {
  request_id?: string; result_id?: string; model_version: number; design_found?: boolean;
  /** Whether the version's fit explains its own tests within the noise; null for v0, which has no fit. */
  model_adequate?: boolean | null;
  /** Whether a model with standing stands behind the design (fit adequate and met the rule on tests it did not
   *  fit); absent in recordings made before the rule. `standing` says why, in words. */
  model_supported?: boolean; standing?: string;
  c_rates?: (number | null)[] | null; charge_time_80?: number | null;
  /** Forecast, worst member; never measured. */
  min_anode_potential?: number | null; max_temperature?: number | null;
  limits?: { margin_mV?: number; t_limit_C?: number };
}
export interface RankOption {
  option: number; label?: string; c_rates?: number[]; rest_min?: number; value_min?: number | null;
  disagreement_K?: number | null; duration_min?: number | null;
}
export interface Ranking {
  request_id?: string; result_id?: string; model_version?: number;
  /** The option worth running, or 0 when none is worth its cost. */
  best_measurement?: number | null; verdict?: string | null; decision_risk_min?: number | null; options?: RankOption[];
}
export interface NextExperiment {
  request_id: string; action: string; state: string; params?: Record<string, unknown>;
  rationale?: string | null; expected_effect?: string | null; prediction?: Record<string, unknown>;
}
export interface Learning {
  kind?: string; versions?: ModelVersion[]; model_in_use?: number;
  lab_tests?: LabTest[]; latest_lab_test?: number | null; next_experiment?: NextExperiment | null;
  designs?: Design[]; first_design?: Design | null; best_design?: Design | null; ranking?: Ranking | null;
  /** The latest design a model with standing stands behind; null when none does yet. */
  recommended_design?: Design | null; standing_rule?: string;
  /** What the lab measures and what is only forecast: the panels' footnote. */
  limits?: string;
  /** Set when every supported family was fitted to the lab's tests and none may stand behind a design. */
  model_gap?: ModelGap | null;
}
export interface ModelGap {
  established: boolean; pattern: string; summary: string;
  needed: { kind: "evidence" | "capability"; what: string; establishes: string }[];
}

/** Why a design is not a recommendation, or null when it is one (or the recording predates the rule and its model
 *  fits its own tests). */
export function designCaveat(d: Design): string | null {
  if (d.model_supported === false) return `Not a recommendation: ${d.standing ?? "its model has no standing"}. Treat it as a lead, not a result.`;
  if (d.model_supported === undefined && d.model_adequate === false) return "This design rests on a model version that does not fit its own tests within the noise. Treat it as a lead, not a result.";
  return null;
}

/** The design the cards lead with, and a later design shown only as a lead. With the standing rule, the recommended
 *  design leads (else the latest, flagged); a later design without standing never hides an earlier recommendation.
 *  A recording made before the rule shows its best design as before. */
export function designShown(l: Learning | null | undefined): { main: Design | null; lead: Design | null } {
  const best = l?.best_design ?? null;
  if (!l || !("recommended_design" in l)) return { main: best, lead: null };
  const main = l.recommended_design ?? best;
  const lead = best && main && best.request_id !== main.request_id ? best : null;
  return { main, lead };
}

/** One line on a later design that is not a recommendation, shown under the recommended one. */
export function leadLine(d: Design): string {
  return `A later design under v${d.model_version} (${cRatesLine(d.c_rates)}) is a lead, not a recommendation: ${d.standing ?? "its model has no standing"}.`;
}

/** The design card's heading: a design without a model standing behind it is never called the best. */
export function designHeading(d: Design): string {
  return d.model_supported === false ? "Latest design, not a recommendation" : "Best design";
}

// ---------------------------------------------------------------- investigation from the engineer's records
// records.investigation in translucent/records.py. Every field optional: a view before the agent imported loads.

export interface UploadedFile { name: string; sha256?: string; bytes?: number }
export interface InstrumentChannel {
  quantity: string; column?: string | null; unit?: string; n?: number; t_range_s?: number[] | null;
  uncertainty?: { value?: number | null; kind?: string | null; method?: string | null } | null;
}
/** One imported record as its manifest states it: what the files established, column by column. */
export interface InstrumentFacts {
  record: number; ref: string;
  specimen?: { id?: string | null; batch?: string | null; properties?: Record<string, unknown> } | null;
  configuration?: Record<string, unknown>; configuration_units?: Record<string, string>;
  conditions?: Record<string, unknown>; conditions_units?: Record<string, string>;
  time?: { unit?: string; origin?: string | null; offset_s?: number | null; source?: string | null; uncertainty_s?: number | null } | null;
  channels?: InstrumentChannel[]; ignored_columns?: string[];
}
export interface InvestigationTest {
  test: number; kind: string; request_id?: string;
  record?: number; record_ref?: string; segment?: number; mode?: string; start_soc?: number;
}
export interface Investigation {
  uploads?: UploadedFile[]; documentation?: string | null;
  instrument_facts?: InstrumentFacts[];
  /** Record number -> its segments, one readable line each. */
  segments?: Record<string, string[]>;
  tests?: InvestigationTest[];
  missing_information?: { item: string; why_it_matters?: string | null }[];
  model_assumptions?: {
    prior?: { version?: number; source?: string | null; [k: string]: unknown } | null;
    families?: Record<string, string>; record_test_states?: string[];
  } | null;
  /** A pointer to the objective's constraints (the server's words), or the constraints themselves. */
  engineer_constraints?: string | Constraint[] | null;
}

// ---------------------------------------------------------------- the list and the series

export interface LedgerInfo { id: string; title: string; campaigns: number; datasets: number }
/** The assessment's three answers, compact (translucent/loop_view.py summary_fields), for a concluded loop campaign. */
export interface SummaryFindings {
  basis: Basis;
  feasibility: { baseline: "meets" | "fails" | "unknown"; best: "meets" | "fails" | "unknown" | "none"; recovered: boolean };
  speed: { status: "faster" | "not_faster" | "not_compared" };
  objective: { kind: "fastest_feasible" | "hard_target" | "legacy"; status: "met" | "not_met" | "unknown" | "open" };
}
export interface CampaignSummary {
  id: string; ledger: string; kind: "loop" | "video" | "intervention"; title: string; status: CampaignStatus;
  created_at: number; updated_at: number; objective: string; next_action: NextKind; outcome: Outcome | null; spend_usd: number;
  /** M2, a concluded loop campaign: the assessment's headline and what the outcome rests on. */
  assessment_headline?: string;
  findings?: SummaryFindings;
}
export interface CampaignPage { items: CampaignSummary[]; next_cursor: number | null }

/** The list's objective: a loop campaign's current-version summary, else the statement; 140 characters (api_v1, loop_view). */
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

/** A file the receipt carries whole (an uploaded record), as its original bytes; null when the recording lacks it. */
export async function blobBytes(blobs: Record<string, PackedFile>, sha256: string): Promise<Uint8Array | null> {
  const packed = blobs[sha256.startsWith("sha256:") ? sha256 : `sha256:${sha256}`];
  if (!packed) return null;
  const raw = Uint8Array.from(atob(packed.data), (c) => c.charCodeAt(0));
  if (!(packed.encoding ?? "").includes("gzip")) return raw;
  const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** At most `max` points, evenly strided (as the server's series endpoint downsamples). */
export function thin(s: Series, max = 1200): Series {
  if (s.t.length <= max) return s;
  const step = Math.ceil(s.t.length / max);
  return { ...s, t: s.t.filter((_, i) => i % step === 0), y: s.y.filter((_, i) => i % step === 0), downsampled: true };
}

// ---------------------------------------------------------------- numbers and units

const UNIT_WORD: Record<string, string> = { degC: "°C", degF: "°F", delta_degC: "K", "1/h": "C", "1": "", percent: "%" };
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
  const known = NAME_WORD[name];
  if (known) return known.charAt(0).toUpperCase() + known.slice(1);
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
    // The target's label without its note: "(a forecast: the slowest member)" is true of a design search's output, not
    // of a lab test's observed time, and the same name heads both. The objective itself prints the label whole.
    quantity: (name) => (name === t.quantity && t.label ? stripNote(t.label) : q.has(name) ? stripNote(q.get(name)!) : idWords(name)),
    note: (name) => (name === t.quantity ? t.label?.match(NOTE)?.[1] ?? "" : q.get(name)?.match(NOTE)?.[1] ?? ""),
    // "Charge C-rate, 10-27.5 % state of charge" -> "Charge C-rate": the window is the step's, the name the group's.
    // "Model version: 0 is nominal, n the n-th thermal fit" -> "Model version": the gloss after a colon is not the name.
    param: (name) => (p.has(name) ? stripNote(p.get(name)!.split(",")[0].split(":")[0]) : idWords(name.replace(/_\d+$/, ""))),
    action: (name) => a.get(name) ?? idWords(name),
  };
}

/** Words from the ids alone (no spec, no objective): what a test or a bare request gets. */
const ID_WORDS: Words = { quantity: idWords, note: () => "", param: (n) => idWords(n.replace(/_\d+$/, "")), action: idWords };

/** The longest string parameter printed inline; a longer one, or an object, reads "(recorded with the request)". */
export const PARAM_INLINE_MAX = 40;
export const PARAM_RECORDED = "(recorded with the request)";
/** A request parameter's value as printed: never an object (or JSON text of one) or a long string inline. */
export function paramText(p: Param): string {
  const v = p.value as unknown;
  if (typeof v === "number") return withUnit(v, p.unit);
  if (v === null || v === undefined) return "–";
  if (typeof v === "object") return PARAM_RECORDED;
  const s = String(v);
  if (s.length > PARAM_INLINE_MAX || /^\s*[[{]/.test(s)) return PARAM_RECORDED;
  return s;
}
/** "test 3", "tests 1 and 2": a set of tests named by a bitmask, test n being bit n-1 (fit_on, test_on). Null when the
 *  value is not a positive whole number. */
export function testsWord(mask: unknown): string | null {
  if (typeof mask !== "number" || !Number.isInteger(mask) || mask < 1 || mask >= 2 ** 31) return null;
  const ns: number[] = [];
  for (let i = 0; i < 31; i++) if (mask & (1 << i)) ns.push(i + 1);
  return ns.length === 1 ? `test ${ns[0]}` : `tests ${ns.slice(0, -1).join(", ")} and ${ns[ns.length - 1]}`;
}

/**
 * Parameters whose number is a code, said as what it stands for: the tests a fit or check names (a bitmask), how a
 * record segment was read (1 a discharge, 2 a charge), the thermal model family. Each applies only beside the parameters
 * it belongs with, so another system's "mode" or "family" is left as recorded.
 */
const CODED: Record<string, { label: string; with: string[]; text(n: number): string | null }> = {
  fit_on: { label: "Fitted on", with: [], text: testsWord },
  test_on: { label: "Checked on", with: [], text: testsWord },
  mode: { label: "Segment read as", with: ["record", "segment"], text: (n) => (n === 1 ? "a discharge" : n === 2 ? "a charge" : null) },
  family: { label: "Model family", with: ["fit_on"], text: (n) => (n === 1 ? "cooling" : n === 2 ? "cooling and thermal mass" : null) },
  start_soc: { label: "Starting state of charge", with: ["record", "segment"], text: (n) => (n >= 0 && n <= 1 ? `${num(n * 100)} %` : null) },
};
/** A coded parameter's label and value in words, or null when `name` is not one (or its value is not a known code). */
export function codedParam(name: string, p: Param, params: Record<string, Param>): { label: string; text: string } | null {
  const c = CODED[name];
  if (!c || !c.with.every((k) => k in params) || typeof p.value !== "number") return null;
  const text = c.text(p.value);
  return text ? { label: c.label, text } : null;
}
const paramValue = paramText;

/**
 * "Charge C-rate 1.3C → 1.05C → 0.95C → 0.75C · Ambient temperature 25 °C". Numbered parameters of one unit
 * (c_rate_1..4) are one stepped group, read in step order; a lone parameter is "Switch SOC 0.5". A parameter too
 * long to print inline (a model, a list of sources) is left out; the request's parameter table carries it.
 */
export function paramsLine(params: Record<string, Param>, words: Words = ID_WORDS): string {
  const groups = new Map<string, { label: string; items: [number, Param][] }>();
  const singles: [string, Param][] = [];
  for (const [k, p] of Object.entries(params)) {
    if (paramText(p) === PARAM_RECORDED) continue;   // a model or a source list: the request's table shows it
    const coded = codedParam(k, p, params);
    if (coded) { singles.push([k, { ...p, value: coded.text, unit: "" }]); continue; }
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
  for (const [k, p] of singles) {
    const coded = codedParam(k, params[k], params);
    out.push(`${coded ? coded.label : /_\d+$/.test(k) ? idWords(k) : words.param(k)} ${paramValue(p)}`);
  }
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

const OUTCOME3_WORD: Record<Outcome3, string> = {
  objective_met: "Objective met", faster_than_reference: "Faster than the reference", feasible_only: "Feasible only",
  no_feasible_result: "No feasible result", unresolved: "Unresolved",
};
export const OUTCOME_WORD: Record<Outcome, string> = {
  improved: "Improved", improved_in_simulation: "Improved in simulation", not_improved: "Not improved",
  ...Object.fromEntries(Object.entries(OUTCOME3_WORD).flatMap(([k, w]) => [[k, w], [`${k}_in_simulation`, `${w} in simulation`]])),
} as Record<Outcome, string>;

/** Snake-case names the server writes into its sentences, as a reader says them. */
const NAME_WORD: Record<string, string> = {
  model_version: "model version", charge_time_80: "time from 10 to 80 % SOC", min_anode_potential: "lowest anode potential",
  max_temperature: "peak temperature", temperature_end: "temperature at the end", surface_temperature: "surface temperature",
  margin_mV: "anode-potential margin", t_limit_C: "temperature limit", start_soc: "starting state of charge",
  rest_min: "rest", heldout_rmse_K: "held-out RMSE", fit_rmse_K: "fit RMSE",
};

/**
 * Prose with the ledger's ids made readable: a request id becomes the request's number ("#9"); result and comparison
 * ids, which name nothing a reader can open here, are dropped. The ids stay in each request's technical details.
 */
export function idsAsNumbers(text: string, requests: readonly { request_id: string }[]): string {
  return text
    .replace(/\brq_[0-9a-f]{8,}\b/g, (id) => { const i = requests.findIndex((r) => r.request_id === id); return i >= 0 ? `#${i + 1}` : ""; })
    .replace(/[ \t]*\b(?:res|cmp)_[0-9a-f]{8,}\b/g, "")
    .replace(/\(\s*[,;:]?\s*\)/g, "").replace(/\(\s+/g, "(").replace(/[ \t]+([,.;:)])/g, "$1")
    .replace(/([(,;:])\s*[,;:]/g, "$1").replace(/[ \t]{2,}/g, " ");
}

/**
 * Server prose with its code names turned into words: "c_rate_1 1.5C" -> "C-rate, step 1 1.5C", "model_version 5" ->
 * "model version 5", "improved_in_simulation" -> "improved in simulation". Ids with a hex part (cmp_99d455cb) and file
 * names are left alone. Numbers are never touched.
 */
export function plainWords(text: string): string {
  return text.replace(/\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b/g, (w, at: number, all: string) => {
    if (/_[0-9a-f]{6,}$/.test(w) || /^\.\w/.test(all.slice(at + w.length)) || /[./]$/.test(all.slice(0, at))) return w;
    if (NAME_WORD[w]) return NAME_WORD[w];
    if ((OUTCOME_WORD as Record<string, string>)[w]) return OUTCOME_WORD[w as Outcome].toLowerCase();
    const step = /^c_rate_(\d+)$/i.exec(w);
    if (step) return `C-rate, step ${step[1]}`;
    const s = idWords(w);
    return /^[A-Z][a-z]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s;
  });
}
/**
 * A concluded campaign's outcome as the campaign list says it, e.g. "Feasibility recovered (simulation)". The recorded
 * outcome is never rewritten, but a loop-compare/1-2 "improved" whose speed was not shown faster (the limits decided
 * it) is said as the feasibility answer it is, from the server's findings; without findings (an older server) it is
 * said as "Concluded", never "Improved". `title` is the assessment's headline, for a tooltip.
 */
export function summaryOutcome(c: Pick<CampaignSummary, "outcome" | "findings" | "assessment_headline">): { word: string; tone: string; title: string } | null {
  const o = c.outcome;
  if (!o) return null;
  const sim = o.endsWith("_in_simulation") ? " (simulation)" : "";
  const base = o.replace(/_in_simulation$/, "");
  const f = c.findings;
  const title = c.assessment_headline ?? `Recorded outcome: ${o}`;
  const recovered = { word: `Feasibility recovered${sim}`, tone: "feasible", title };
  if (base === "improved") {
    if (f?.speed.status === "faster") return { word: `Improved${sim}`, tone: "improved", title };
    if (f?.feasibility.recovered) return recovered;
    if (f?.feasibility.best === "meets") return { word: `Feasible${sim}, speed not compared`, tone: "feasible", title };
    return { word: `Concluded${sim}`, tone: "unresolved", title };
  }
  if (base === "feasible_only" && f?.feasibility.recovered) return recovered;
  const word = base === "not_improved" ? "Not improved" : OUTCOME3_WORD[base as Outcome3] ?? base;
  const tone = base === "objective_met" || base === "faster_than_reference" ? "improved"
    : base === "feasible_only" ? "feasible" : base === "no_feasible_result" ? "not_improved" : base;
  return { word: `${word}${sim}`, tone, title };
}
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

/** Can a person act on this view? Never on a recording; on a live one only signed in as an engineer or administrator. */
export function canAct(mode: "live" | "recorded", role: string | null | undefined): boolean {
  return mode === "live" && (role === "engineer" || role === "admin");
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
  awaiting_result: "Waiting for a person’s result", planner_thinking: "The agent is choosing the next experiment",
  planner_stopped: "The planner stopped: its model budget is spent", none: "",
};
export const KIND_WORD: Record<CampaignSummary["kind"], string> = { loop: "Experiment loop", video: "Recording analysis", intervention: "Intervention" };

/** The loop screen's address for a live campaign, or for a recording under /fixtures/loop/. */
export const loopHref = (ledger: string, id: string) => `/w/?ledger=${encodeURIComponent(ledger)}&id=${encodeURIComponent(id)}`;
export const recordingHref = (name: string) => `/w/?recording=${encodeURIComponent(name)}`;

/** A recording's name as the address carries it: letters, digits, dot, dash and underscore only (no paths). */
export const RECORDING_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$/;
/** The recorded fast-charge campaign: public/fixtures/loop/fastcharge.loop.json, evaluation case B. */
/** What /w/ shows, from its query: a live campaign, a recording by name, or the open-a-file page. */
export type LoopWhere = { mode: "live"; ledger: string; id: string } | { mode: "recording"; name: string } | { mode: "file" };
export function loopWhere(search: string): LoopWhere {
  const q = new URLSearchParams(search);
  const ledger = q.get("ledger"), id = q.get("id"), rec = q.get("recording");
  if (ledger && id) return { mode: "live", ledger, id };
  if (rec && RECORDING_NAME.test(rec)) return { mode: "recording", name: rec };
  return { mode: "file" };
}
/** One key per campaign shown: the screen remounts when it changes, so no state of one campaign survives into another. */
export function loopWhereKey(w: LoopWhere): string {
  return w.mode === "live" ? `live:${w.ledger}/${w.id}` : w.mode === "recording" ? `recording:${w.name}` : "file";
}

export const FASTCHARGE_HREF = "/w/?recording=fastcharge";
/** A demonstration of case B run once through the web app (Oct 31, 2026): public/fixtures/loop/fastcharge-demo.loop.json. */
export const FASTCHARGE_DEMO_HREF = "/w/?recording=fastcharge-demo";
/** Fast charge against a simulated lab, learning a thermal model as it goes: public/fixtures/loop/fastcharge-lab.loop.json. */
export const FASTCHARGE_LAB_HREF = "/w/?recording=fastcharge-lab";
/** A scored records-intake run (published LG M50 cell exports to a design, every request approved by a standing rule): public/fixtures/loop/records-to-design.loop.json. */
export const RECORDS_TO_DESIGN_HREF = "/w/?recording=records-to-design";
/** The recorded loop campaigns as a visitor meets them: one name each, for the header, the tab title, the sidebar and
 *  the demo lists. The fixture's own title (an evaluation name) stays in the record's provenance. */
export const RECORDINGS: { name: string; href: string; title: string }[] = [
  { name: "records-to-design", href: RECORDS_TO_DESIGN_HREF, title: "Raw records to a design" },
  { name: "fastcharge-lab", href: FASTCHARGE_LAB_HREF, title: "Learning the lab’s thermal model" },
  { name: "fastcharge", href: FASTCHARGE_HREF, title: "Faster LG M50 charge" },
  { name: "fastcharge-demo", href: FASTCHARGE_DEMO_HREF, title: "Case B, browser demonstration" },
];
/** A recording's human title from its name or its file name ("fastcharge-lab.loop.json"), or null. */
export const recordingTitle = (nameOrFile: string | null | undefined) =>
  RECORDINGS.find((r) => r.name === (nameOrFile ?? "").replace(/\.loop\.json$/, ""))?.title ?? null;

export const recordingUrl = (name: string) => withBase(`/fixtures/loop/${name.replace(/\.loop\.json$/, "")}.loop.json`);

/**
 * The constraint limit to draw on a series chart: a constraint in the series' unit whose quantity names the series
 * (max_temperature on a temperature series). Null when none fits; the chart never guesses a conversion.
 */
export function limitFor(v: LoopView, seriesQuantity: string, seriesUnit: string): { value: number; label: string; quantity: string } | null {
  const c = v.objective.constraints.find((x) => x.unit === seriesUnit && limitNames(x.quantity, seriesQuantity));
  return c ? { value: c.limit, label: `${opWord(c.op)} ${withUnit(c.limit, c.unit)}`, quantity: c.quantity } : null;
}

/**
 * Whether a constraint's quantity limits a series: "max_temperature" limits "temperature" and "cell_temperature";
 * "min_anode_potential" limits "anode_potential". The series must name the constrained quantity, not just share a word.
 */
export function limitNames(constraintQuantity: string, seriesQuantity: string): boolean {
  const q = constraintQuantity.replace(/^[a-z]+:/, "").replace(/^(min|max|peak|lowest)_/, "");
  return seriesQuantity === q || seriesQuantity.endsWith(`_${q}`);
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

// ---------------------------------------------------------------- thermal learning: words and chart data

/** A step's C-rates as "1.5 · 1.35 · 0.9 · 0.5C"; a missing step reads "–". */
export function cRatesLine(c: (number | null)[] | null | undefined): string {
  if (!c || !c.length) return "–";
  // one rate at every step is a constant-current charge: "1C throughout", not "1 · 1 · 1 · 1C"
  if (c.length > 1 && c.every((x) => x != null && Number.isFinite(x) && x === c[0])) return `${num(c[0])}C throughout`;
  return `${c.map((x) => (x == null || !Number.isFinite(x) ? "–" : num(x))).join(" · ")}C`;
}

/** A fitted parameter to four significant digits: 14.967 -> "14.97", 6 -> "6". */
export function fitValue(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "–";
  const s = Math.abs(n) >= 1000 ? Math.round(n).toString() : n.toPrecision(4);
  return (s.includes(".") && !s.includes("e") ? s.replace(/\.?0+$/, "") : s).replace(/^-/, "−");
}

const PARAM_WORD: Record<string, { label: string; unit: string }> = {
  h: { label: "Heat transfer h", unit: "W/m²/K" },
  k: { label: "Thermal-mass scale k", unit: "" },
};
/** A model parameter and its 95 % region: "Heat transfer h 14.97 W/m²/K (95 %: 14.9–15.1)". */
export function paramLine(name: string, value: number, region?: [number, number] | null): string {
  const w = PARAM_WORD[name] ?? { label: name, unit: "" };
  const v = `${fitValue(value)}${w.unit ? ` ${w.unit}` : ""}`;
  return `${w.label} ${v}${region ? ` (95 %: ${fitValue(region[0])}–${fitValue(region[1])})` : ""}`;
}

/** The version numbered n, or undefined. */
export const versionOf = (l: Learning, n: number | null | undefined) => (n == null ? undefined : l.versions?.find((v) => v.version === n));
/** The lab test numbered n, or undefined. */
export const labTestOf = (l: Learning, n: number | null | undefined) => (n == null ? undefined : l.lab_tests?.find((t) => t.n === n));

/** The planner's own sealed prediction of a lab test's peak temperature, made with the request, or null. */
export function labPrediction(t: Pick<LabTest, "prediction">): Predicted | null {
  const p = t.prediction as Partial<Predicted> | null | undefined;
  return p && typeof p === "object" && typeof p.value === "number" && Number.isFinite(p.value) ? { value: p.value, lo: p.lo ?? null, hi: p.hi ?? null, unit: p.unit ?? "degC" } : null;
}

/**
 * What was said about a test before it ran, once and from its data: a model version's forecast of the protocol (drawn
 * on the chart), else the planner's sealed prediction of its peak (a number, not a curve), else nothing. A forecast is
 * claimed only when one is recorded.
 */
export function beforeTestNote(t: Pick<LabTest, "forecast" | "prediction" | "record">, planner = "the planner"): { kind: "forecast" | "prediction" | "none"; text: string } {
  if (t.forecast) return { kind: "forecast", text: `Forecast under model v${t.forecast.model_version}, made before the ${t.record ? "stretch was read" : "test ran"}.` };
  if (t.record) return { kind: "none", text: "No forecast of this stretch was made before it was read." };
  const p = labPrediction(t);
  if (p) return { kind: "prediction", text: `No model forecast of this protocol was recorded; before it ran, ${planner} predicted a peak of ${predictedLine(p)}.` };
  return { kind: "none", text: "No forecast of this protocol was made before the test." };
}

/**
 * What was computed about a test after it ran: a held-out check that set a model version not fitted to it against the
 * result. That is a replay made after the observation, not a forecast made before it, and is said as such; null when
 * no check covers the test.
 */
export function afterTestNote(l: Pick<Learning, "versions">, t: Pick<LabTest, "n" | "record">, requests: readonly { request_id: string }[]):
  { version: number; requestNumber: number; rmse: number | null; maxError: number | null; text: string } | null {
  for (const x of l.versions ?? []) {
    const c = (x.checks ?? []).find((k) => k.lab_tests?.includes(t.n));
    if (!c) continue;
    const i = requests.findIndex((r) => r.request_id === c.request_id) + 1;
    const rmse = c.heldout_rmse_K ?? null, maxError = c.heldout_max_error_K ?? null;
    const what = [rmse != null ? `RMSE ${withUnit(rmse, "K")}` : "", maxError != null ? `worst error ${withUnit(maxError, "K")}` : ""].filter(Boolean).join(", ");
    return {
      version: x.version, requestNumber: i, rmse, maxError,
      text: `${t.record ? "After it was read" : "After it ran"}, a held-out check${i ? ` (#${i})` : ""} set model v${x.version}, which was not fitted to it, against the result${what ? `: ${what}` : ""}.`,
    };
  }
  return null;
}

/** The worst member's anode potential against the margin: "26.3 mV, 1.28 mV above the 25 mV margin". */
export function marginLine(d: Pick<Design, "min_anode_potential" | "limits">): string {
  const a = d.min_anode_potential, m = d.limits?.margin_mV;
  if (a == null) return "–";
  if (m == null) return withUnit(a, "mV");
  const gap = a - m;
  return `${withUnit(a, "mV")}, ${num(Math.abs(gap))} mV ${gap >= 0 ? "above" : "below"} the ${num(m)} mV margin`;
}

/**
 * What the next experiment is: a request already pending, else the ranking's answer ("none" when no test is worth
 * its cost; "run" names the option), else "unranked".
 */
export type NextStep =
  | { kind: "pending"; next: NextExperiment }
  | { kind: "run"; option: RankOption | undefined; ranking: Ranking }
  | { kind: "none"; ranking: Ranking }
  | { kind: "unranked" };
export function nextStepOf(l: Learning): NextStep {
  if (l.next_experiment) return { kind: "pending", next: l.next_experiment };
  const r = l.ranking;
  if (!r) return { kind: "unranked" };
  const best = r.best_measurement;
  if (best != null && best > 0) return { kind: "run", option: r.options?.find((o) => o.option === best), ranking: r };
  if (best === 0) return { kind: "none", ranking: r };
  // No best named: decide from the options' values, net of the test's cost.
  const worth = (r.options ?? []).filter((o) => (o.value_min ?? 0) > 0).sort((a, b) => (b.value_min ?? 0) - (a.value_min ?? 0));
  return worth.length ? { kind: "run", option: worth[0], ranking: r } : { kind: "none", ranking: r };
}

/**
 * Whether the assessment's best result (a request) was made under an older model version than the learning's best
 * design: its `model_version` parameter is a number below the design's. False without learning, a best design, or
 * the parameter.
 */
export function bestIsOlderModel(v: Pick<LoopView, "learning" | "requests">, bestRequestId: string | null | undefined): boolean {
  const d = v.learning?.best_design;
  if (!d || !bestRequestId || bestRequestId === d.request_id) return false;
  const mv = v.requests.find((r) => r.request_id === bestRequestId)?.params?.model_version?.value;
  return typeof mv === "number" && mv < d.model_version;
}

/** A shaded band's runs: contiguous stretches where both edges are known, in seconds. */
export interface Band { t: number[]; lo: number[]; hi: number[] }
export interface TraceChart {
  observed: Series;
  forecast: Series | null;
  band: Band[];
  /** The band's widest point in K: 0 when the version has one member (the band is the forecast line). */
  spread: number;
}

/**
 * The prediction-vs-observation chart's data from a lab test's trace: observed and forecast as series in seconds
 * (the chart's time unit), nulls dropped, and the members' lo-hi band split where an edge is missing. Null without a
 * trace or observed points.
 */
export function traceChart(trace: LabTrace | null | undefined): TraceChart | null {
  if (!trace || !Array.isArray(trace.t_min) || !Array.isArray(trace.observed_degC)) return null;
  const pick = (ys: (number | null)[] | null | undefined): Series | null => {
    if (!ys) return null;
    const t: number[] = [], y: number[] = [];
    trace.t_min.forEach((tm, i) => {
      const v = ys[i];
      if (v != null && Number.isFinite(v) && Number.isFinite(tm)) { t.push(tm * 60); y.push(v); }
    });
    return t.length ? { t, y, unit: "degC", t_unit: "s", n: t.length, downsampled: false } : null;
  };
  const observed = pick(trace.observed_degC);
  if (!observed) return null;
  const band: Band[] = [];
  let spread = 0;
  const lo = trace.forecast_lo_degC, hi = trace.forecast_hi_degC;
  if (lo && hi) {
    let run: Band | null = null;
    trace.t_min.forEach((tm, i) => {
      const a = lo[i], b = hi[i];
      if (a == null || b == null || !Number.isFinite(a) || !Number.isFinite(b)) { run = null; return; }
      if (!run) { run = { t: [], lo: [], hi: [] }; band.push(run); }
      run.t.push(tm * 60); run.lo.push(Math.min(a, b)); run.hi.push(Math.max(a, b));
      spread = Math.max(spread, Math.abs(b - a));
    });
  }
  return { observed, forecast: pick(trace.forecast_degC), band, spread };
}

// ---------------------------------------------------------------- investigation: words

/** Whether a lab test is a stretch of the engineer's record (a record test) rather than a lab charge. */
export const isRecordTest = (t: Pick<LabTest, "record">): boolean => t.record != null && typeof t.record === "object";

/** A record test in one line: "lgm50-cells/cell02 segment 27 · discharge at 0.5C for 118 min · from 26.8 °C, +7.56 K". */
export function recordStretchLine(r: RecordStretch): string {
  const where = [r.ref, r.segment != null ? `segment ${r.segment}` : ""].filter(Boolean).join(" ") || "record";
  const run = [r.mode ?? "", r.test_c_rate != null ? `at ${withUnit(r.test_c_rate, "1/h")}` : "", r.test_duration != null ? `for ${withUnit(r.test_duration, "min")}` : ""]
    .filter(Boolean).join(" ");
  const heat = r.temperature_start != null || r.temperature_rise != null
    ? `from ${withUnit(r.temperature_start, "degC")}, ${r.temperature_rise != null && r.temperature_rise >= 0 ? "+" : ""}${withUnit(r.temperature_rise, "K")}` : "";
  return [where, run, heat].filter(Boolean).join(" · ");
}

/** File sizes in binary units, as the server's limits are: "312 B", "584 KiB", "1.2 MiB". */
export function bytesWord(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "–";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KiB`;
  return `${num(n / (1024 * 1024))} MiB`;
}

/** A time span in seconds, in the unit that reads best: "0–48.2 h", "120–1800 s". */
export function spanWord(range: number[] | null | undefined): string {
  if (!range || range.length < 2 || !range.every(Number.isFinite)) return "–";
  const d = Math.abs(range[1] - range[0]);
  const [k, u] = d >= 7200 ? [3600, "h"] : d >= 600 ? [60, "min"] : [1, "s"];
  return `${num(range[0] / k)}–${num(range[1] / k)} ${u}`;
}

/** A channel's declared uncertainty, or that the manifest declared none (never a number it did not state). */
export function uncertaintyWord(u: InstrumentChannel["uncertainty"], unit = ""): string {
  if (u?.value != null && Number.isFinite(u.value)) return `± ${withUnit(u.value, unit)}${u.kind && u.kind !== "none" ? ` (${u.kind.replace(/_/g, " ")})` : ""}`;
  return "not declared";
}

/** The model's prior as parameter lines (every number but its version): "Heat transfer h 10 W/m²/K". */
export function priorLines(prior: NonNullable<Investigation["model_assumptions"]>["prior"]): string[] {
  if (!prior) return [];
  return Object.entries(prior).filter(([k, x]) => k !== "version" && typeof x === "number" && Number.isFinite(x)).map(([k, x]) => paramLine(k, x as number));
}

/** The constraints the engineer set: the investigation's own list when it carries one, else the objective's (the server
 *  points there: only an engineer's amendment changes them). */
export function engineerConstraints(v: Pick<LoopView, "objective" | "investigation">): Constraint[] {
  const own = v.investigation?.engineer_constraints;
  return Array.isArray(own) ? own : v.objective.constraints;
}
