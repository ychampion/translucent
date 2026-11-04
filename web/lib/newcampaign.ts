// The New campaign form's data and rules: what the server offers (systems with their objective template, datasets,
// planners with their readiness), and the POST .../campaigns body built from the engineer's choices.
// Rules the form keeps (docs M2 contracts §1-2):
//   - the default objective is "Find the fastest protocol meeting these constraints."; a hard time target is an
//     explicit, labelled choice, and nothing here relaxes it;
//   - Bayesian optimisation (free, no model) is the default planner; a paid model is an explicit opt-in;
//   - a planner the server says is not ready cannot be launched, with the server's reason, and is never replaced.
import { withUnit } from "./loop";

// ---------------------------------------------------------------- what the server serves

export interface TemplateConstraint { quantity: string; op: "<=" | ">="; limit: number; unit: string; label?: string }
/** SystemSpec.objective_template (SEMANTICS). Parsed leniently: every part is optional except the target. */
export interface ObjectiveTemplate {
  target: { quantity: string; unit: string; direction: "minimize" | "maximize"; label?: string; margin?: number };
  constraints: TemplateConstraint[];
  /** The quantity a hard target may be set on, and how a statement words it ("a 10-80 % charge in at most {limit:g} {unit}"). */
  hard_target?: { quantity: string; unit: string; op?: string; label?: string; words?: string; default?: number | null } | null;
  /** The system's reference search (shown, not edited): label, method, parameter. */
  reference_search?: { label?: string; method?: string; [k: string]: unknown } | null;
  /** One statement, or one per kind; a hard target's has "{target}". */
  statement?: string | Partial<Record<"fastest_feasible" | "hard_target", string>>;
  quantities?: { name: string; unit: string; source: string }[];
  /** The system's own statement of what it cannot quantify. */
  model_uncertainty?: string;
  budget?: { experiments?: number } | null;
}

export interface SpecQuantity { name: string; unit: string; kind: string; label?: string }
export interface SystemSpecFull {
  id: string; version?: string; title?: string; description?: string;
  quantities: SpecQuantity[];
  actions: { name: string; label?: string; executor: string; parameters: { name: string; unit?: string; label?: string; lo?: number | null; hi?: number | null }[]; max_wall_s?: number }[];
  solver?: { name: string; version: string; validity?: string } | null;
  objective_template?: unknown;
}

export interface PlannerInfo {
  id: string; kind: string; label: string; paid: boolean; ready: boolean; reason: string | null;
  model?: string; profile?: string; cap_usd?: number; spent_usd?: number; remaining_usd?: number; default_budget_usd?: number;
  providers?: { provider: string; cap_usd: number; spent_usd: number; remaining_usd: number }[];
}

export interface DatasetSummary { id: string; title: string; author?: string; system: string; records: number; created_at?: number }
export interface DatasetChannel { column?: string; quantity: string; unit: string; n?: number; t_range?: [number, number] }
export interface DatasetRecord {
  id: string; role: string; specimen?: { id?: string; properties?: Record<string, unknown> } | null;
  configuration?: Record<string, unknown>; configuration_units?: Record<string, string>;
  conditions?: Record<string, unknown>; conditions_units?: Record<string, string>;
  channels: DatasetChannel[]; scalars?: { quantity: string; value: number; unit: string }[];
  provenance?: { source?: string; notes?: string | null } | null;
}
export interface DatasetDetail {
  id: string; title: string; author?: string; citation?: string; licence?: string; system: string; records: DatasetRecord[];
}

/** The system's objective template, or null when the server does not serve one (older servers) or it is malformed. */
export function templateOf(spec: SystemSpecFull | null | undefined): ObjectiveTemplate | null {
  const t = spec?.objective_template as Partial<ObjectiveTemplate> | null | undefined;
  if (!t || typeof t !== "object" || !t.target || typeof t.target !== "object" || typeof t.target.quantity !== "string") return null;
  const unit = t.target.unit ?? spec?.quantities.find((q) => q.name === t.target!.quantity)?.unit ?? "";
  return {
    target: { quantity: t.target.quantity, unit, direction: t.target.direction === "maximize" ? "maximize" : "minimize", label: t.target.label, margin: typeof t.target.margin === "number" ? t.target.margin : undefined },
    constraints: Array.isArray(t.constraints) ? t.constraints.filter((c) => c && typeof c.quantity === "string" && (c.op === "<=" || c.op === ">=") && Number.isFinite(c.limit)) : [],
    hard_target: t.hard_target && typeof t.hard_target === "object" ? t.hard_target : null,
    reference_search: t.reference_search && typeof t.reference_search === "object" ? t.reference_search : null,
    statement: typeof t.statement === "string" || (t.statement && typeof t.statement === "object") ? t.statement : undefined,
    quantities: Array.isArray(t.quantities) ? t.quantities.filter((q) => q && typeof q.name === "string") : undefined,
    model_uncertainty: typeof t.model_uncertainty === "string" ? t.model_uncertainty : undefined,
    budget: t.budget ?? null,
  };
}

/** Scalar quantities a target or a constraint can name. */
export const scalarQuantities = (spec: SystemSpecFull | null | undefined) => (spec?.quantities ?? []).filter((q) => q.kind === "scalar");

/** A quantity's label without its parenthetical note. */
export const quantityLabel = (spec: SystemSpecFull | null | undefined, name: string) =>
  (spec?.quantities.find((q) => q.name === name)?.label || name.replace(/_/g, " ")).replace(/\s*\([^)]*\)\s*$/, "").trim();

// ---------------------------------------------------------------- planners

/** The planner chosen when the form opens: Bayesian optimisation when the server offers it ready. Never a paid one. */
export function defaultPlanner(list: PlannerInfo[]): PlannerInfo | null {
  return list.find((p) => p.id === "bo" && p.ready && !p.paid) ?? list.find((p) => p.ready && !p.paid) ?? null;
}

/** Whether a planner can be launched, and why not. A not-ready planner is never swapped for another one. */
export function plannerGate(p: PlannerInfo | null | undefined): { ok: boolean; reason: string | null } {
  if (!p) return { ok: false, reason: "Choose a planner." };
  if (!p.ready) return { ok: false, reason: p.reason?.trim() || "The server says this planner is not ready." };
  return { ok: true, reason: null };
}

/** "Bayesian optimisation, no model" / "Muse Wonder (model muse-wonder-1.0, paid)". */
export function plannerIdentity(p: PlannerInfo): string {
  if (p.kind === "domain_baseline") return "Bayesian optimisation, no model";
  if (p.paid) return `${p.label}${p.model ? ` (model ${p.model}, paid)` : " (paid)"}`;
  return p.label;
}

/**
 * The planners the form offers when the server doesn't list them (an older server: no planners endpoint). Only the
 * free Bayesian-optimisation planner, which such a server opens with an engineer's key; no model is offered.
 */
export const FALLBACK_PLANNERS: PlannerInfo[] = [
  { id: "bo", kind: "domain_baseline", label: "Bayesian optimisation (no model)", paid: false, ready: true, reason: null },
];

// ---------------------------------------------------------------- the form

export interface DraftConstraint { quantity: string; op: "<=" | ">="; limit: string; unit: string; label: string; templateLimit?: number }
export interface Draft {
  title: string;
  system: string;
  /** Where the evidence starts: a dataset already in the ledger, or the engineer's own raw files (RECORDS_SYSTEM only). */
  source?: "dataset" | "records";
  /** With records: what the files are, their columns, units and test conditions, as the engineer writes it. */
  documentation?: string;
  dataset: string;
  baseline: string;            // record id (role baseline) in the dataset
  baselineSimulation: boolean; // re-run the baseline's configuration on the solver at registration
  target: { quantity: string; unit: string; direction: "minimize" | "maximize"; label: string };
  mode: "fastest_feasible" | "hard_target";
  hardLimit: string;
  constraints: DraftConstraint[];
  planner: string;
  experiments: string;
  modelUsd: string;
  autoApprove: boolean;
  autoMaxWall: string;
  autoMaxCount: string;
  memory: "on" | "off";
}

export const FASTEST_STATEMENT = "Find the fastest protocol meeting these constraints.";

/** A draft prefilled from the system (its template, else its first time-like scalar as the target, no constraints). */
export function draftFor(spec: SystemSpecFull, dataset: DatasetDetail | null): Pick<Draft, "system" | "target" | "constraints" | "experiments" | "baseline" | "baselineSimulation" | "dataset" | "hardLimit"> {
  const t = templateOf(spec);
  const scalars = scalarQuantities(spec);
  const fallbackTarget = scalars.find((q) => /^(s|min|h)$/.test(q.unit)) ?? scalars[0];
  const target = t?.target ?? (fallbackTarget ? { quantity: fallbackTarget.name, unit: fallbackTarget.unit, direction: "minimize" as const } : { quantity: "", unit: "", direction: "minimize" as const });
  const base = dataset?.records.find((r) => r.role === "baseline");
  return {
    system: spec.id,
    dataset: dataset?.id ?? "",
    baseline: base?.id ?? "",
    baselineSimulation: !!spec.solver,
    target: { ...target, label: target.label ?? quantityLabel(spec, target.quantity) },
    constraints: (t?.constraints ?? []).map((c) => ({ quantity: c.quantity, op: c.op, limit: String(c.limit), unit: c.unit, label: c.label ?? "", templateLimit: c.limit })),
    experiments: String(t?.budget?.experiments ?? 8),
    hardLimit: t?.hard_target?.default != null ? String(t.hard_target.default) : "",
  };
}

const finite = (s: string) => s.trim() !== "" && Number.isFinite(Number(s));

/** Plain problems that keep the form from launching; empty when it can launch. */
export function draftProblems(d: Draft, planner: PlannerInfo | null, ctx: { canWrite: boolean; role?: string | null }): string[] {
  const out: string[] = [];
  if (!ctx.canWrite) out.push(ctx.role === "viewer" ? "Viewers can read campaigns but not open one." : "Sign in as an engineer to open a campaign.");
  if (!d.title.trim()) out.push("Give the campaign a title.");
  if (!d.system) out.push("Choose a system.");
  if (d.source === "records") {
    if (d.system !== RECORDS_SYSTEM) out.push("Starting from your records needs the lab system.");
    // the lab system has no baseline policy: only an agent can read the records and build the investigation
    if (planner && planner.kind === "domain_baseline") out.push("Starting from your records needs an agent planner: Bayesian optimisation cannot read records.");
  } else {
    if (!d.dataset) out.push("Choose a dataset.");
    if (!d.baseline) out.push("The dataset needs a baseline record.");
  }
  if (!d.target.quantity) out.push("Choose the target quantity.");
  if (d.mode === "hard_target" && !finite(d.hardLimit)) out.push("Set the hard target’s value, or choose “fastest meeting the constraints”.");
  d.constraints.forEach((c, i) => { if (!c.quantity || !finite(c.limit)) out.push(`Constraint ${i + 1} needs a quantity and a number.`); });
  if (!/^\d+$/.test(d.experiments.trim()) || Number(d.experiments) < 1 || Number(d.experiments) > 100) out.push("Experiments: a whole number from 1 to 100.");
  const gate = plannerGate(planner);
  if (!gate.ok) out.push(gate.reason!);
  if (planner?.paid) {
    if (!finite(d.modelUsd) || Number(d.modelUsd) <= 0) out.push("Set a model budget in dollars.");
    else if (planner.remaining_usd != null && Number(d.modelUsd) > planner.remaining_usd) out.push(`The model budget is above what remains under the cap ($${planner.remaining_usd.toFixed(2)}).`);
  }
  if (d.autoApprove && (!finite(d.autoMaxWall) || Number(d.autoMaxWall) <= 0 || !/^\d+$/.test(d.autoMaxCount.trim()) || Number(d.autoMaxCount) < 1)) {
    out.push("Auto-approval needs a run-time limit and a count.");
  }
  return out;
}

/**
 * A constraint's label after its limit changed: the template's own label with the old number replaced when the label
 * names it exactly once ("…at or above +10 mV" -> "…at or above +25 mV"), else a plain one from the quantity.
 */
export function relabel(label: string, oldLimit: number | undefined, newLimit: number, fallback: string): string {
  if (label.trim() && oldLimit === newLimit) return label;
  if (label.trim() && oldLimit != null) {
    const re = new RegExp(`(?<![\\d.])${String(oldLimit).replace(".", "\\.")}(?:\\.0+)?(?![\\d.])`, "g");
    const hits = label.match(re);
    if (hits && hits.length === 1) return label.replace(re, String(newLimit));
  }
  return fallback;
}

/** "Lowest anode potential at or above 25 mV" (ASCII units, as the server writes them). */
export const plainConstraintLabel = (qLabel: string, op: "<=" | ">=", limit: number, unit: string) =>
  `${qLabel.charAt(0).toUpperCase()}${qLabel.slice(1)} ${op === ">=" ? "at or above" : "at or below"} ${limit}${unit ? ` ${unit}` : ""}`;

/**
 * The POST .../campaigns body. No author: the server takes identity from the credential. `kind` is sent only to a
 * server that serves objective templates (one that speaks loop-compare/3); an older one refuses unknown fields.
 */
export function openBody(d: Draft, spec: SystemSpecFull, planner: PlannerInfo): { title: string; objective: Record<string, unknown>; memory: "on" | "off" } {
  const t = templateOf(spec);
  const hard = d.mode === "hard_target";
  const limit = hard ? Number(d.hardLimit) : null;
  const constraints = d.constraints.map((c) => {
    const n = Number(c.limit);
    return { quantity: c.quantity, op: c.op, limit: n, unit: c.unit,
      label: relabel(c.label, c.templateLimit, n, plainConstraintLabel(quantityLabel(spec, c.quantity), c.op, n, c.unit)) };
  });
  const names = [d.target.quantity, ...constraints.map((c) => c.quantity)];
  const quantities = [...new Set(names)].map((name) => t?.quantities?.find((q) => q.name === name)
    ?? { name, unit: name === d.target.quantity ? d.target.unit : constraints.find((c) => c.quantity === name)!.unit, source: "result" });
  const statement = statementFor(d, spec, t, limit);
  const experiments = Number(d.experiments);
  const records = d.source === "records";
  const objective: Record<string, unknown> = {
    statement,
    system: { id: spec.id },
    // with records the agent imports the files itself: no dataset or baseline yet, and the server lists the uploads
    datasets: records ? [] : [d.dataset],
    baseline: records ? [] : [`${d.dataset}/${d.baseline}`],
    baseline_simulation: records ? false : d.baselineSimulation,
    ...(records ? { documentation: d.documentation ?? "" } : {}),
    target: { quantity: d.target.quantity, unit: d.target.unit, direction: d.target.direction, ...(t?.target.margin != null && t.target.quantity === d.target.quantity ? { margin: t.target.margin } : {}), ...(limit != null ? { limit } : {}) },
    constraints,
    quantities,
    budget: { model_usd: planner.paid ? Number(d.modelUsd) : 0, experiments, experiment_usd: 0 },
    planner: { kind: planner.kind, profile: planner.paid ? planner.profile ?? null : null, client: null },
    auto_approve: d.autoApprove
      ? [{ executor: "computational", max_usd: 0, max_wall_s: Number(d.autoMaxWall), max_count: Number(d.autoMaxCount) }]
      : [],
  };
  if (t) objective.kind = hard ? "hard_target" : "fastest_feasible";
  return { title: d.title.trim(), objective, memory: d.memory };
}

/** "{limit:g}" and "{unit}" filled as Python's format would ("30", not "30.0"). */
const fill = (words: string, limit: number, unit: string) => words.replace(/\{limit(:g)?\}/g, String(limit)).replace(/\{unit\}/g, unit);

/**
 * The registered statement: the template's for the kind (a hard target's "{target}" filled from its words), else
 * "Find the fastest protocol meeting these constraints." or "Meet <target> while meeting these constraints."
 */
export function statementFor(d: Pick<Draft, "mode" | "target">, spec: SystemSpecFull, t: ObjectiveTemplate | null, limit: number | null): string {
  const hard = d.mode === "hard_target" && limit != null;
  const own = typeof t?.statement === "string" ? (hard ? undefined : t.statement) : t?.statement?.[hard ? "hard_target" : "fastest_feasible"];
  const targetWord = d.target.label || quantityLabel(spec, d.target.quantity);
  const target = hard
    ? (t?.hard_target?.words && t.hard_target.quantity === d.target.quantity ? fill(t.hard_target.words, limit!, d.target.unit)
      : `${targetWord.charAt(0).toLowerCase()}${targetWord.slice(1)} ${d.target.direction === "maximize" ? "at or above" : "at or below"} ${limit} ${d.target.unit}`)
    : "";
  if (own) return own.replace("{target}", target);
  return hard ? `Meet ${target} while meeting these constraints.` : FASTEST_STATEMENT;
}

// ---------------------------------------------------------------- starting from the engineer's records

/** The system a campaign can start from raw records on (translucent/records.py reads them for its planner). */
export const RECORDS_SYSTEM = "pybamm-fastcharge-lab";
/** The server's limits (translucent/records.py): files per campaign, bytes per file, documentation characters. */
export const RECORDS_MAX_FILES = 32;
export const RECORDS_MAX_BYTES = 64 * 1024 * 1024;
export const RECORDS_MAX_DOC = 20000;

const PLAIN_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const RESERVED = new Set(["manifest.json", "record.json", "record.sha256"]);
const DEVICE = /^(con|prn|aux|nul|com\d|lpt\d|conin\$|conout\$|clock\$)$/i;

/** Why the server would refuse this file name (the upload endpoint's rule), or null when it is plain. */
export function fileNameProblem(name: string): string | null {
  if (!PLAIN_NAME.test(name)) return `${name}: rename it to plain letters, digits, dot, dash or underscore (no spaces), starting with a letter or digit, at most 128 characters`;
  if (RESERVED.has(name)) return `${name}: this name is reserved; rename the file`;
  if (DEVICE.test(name.split(".")[0])) return `${name}: a device name on Windows, not a file name; rename the file`;
  return null;
}

/** Plain problems with the chosen files and documentation; empty when they can be uploaded. */
export function recordsProblems(files: { name: string; size: number }[], documentation: string): string[] {
  const out: string[] = [];
  if (files.length === 0) out.push("Choose the record files.");
  if (files.length > RECORDS_MAX_FILES) out.push(`Choose at most ${RECORDS_MAX_FILES} files (${files.length} chosen).`);
  const seen = new Set<string>();
  for (const f of files) {
    const p = fileNameProblem(f.name);
    if (p) out.push(p);
    if (f.size > RECORDS_MAX_BYTES) out.push(`${f.name}: larger than ${RECORDS_MAX_BYTES / (1024 * 1024)} MiB.`);
    if (f.size === 0) out.push(`${f.name}: the file is empty.`);
    if (seen.has(f.name)) out.push(`${f.name}: two files have this name.`);
    seen.add(f.name);
  }
  if (!documentation.trim()) out.push("Describe the files: what they are, their columns and units, the test conditions.");
  if (documentation.length > RECORDS_MAX_DOC) out.push(`The documentation is ${documentation.length} characters; at most ${RECORDS_MAX_DOC}.`);
  return out;
}

/** A fresh upload id: 32 hex digits, as the upload endpoint takes them. */
export function newUploadId(): string {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID().replaceAll("-", "");
  const b = new Uint8Array(16);
  c.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

/** "≤ 45 °C" for the review step. */
export const limitWords = (op: string, limit: string, unit: string) => `${op === ">=" ? "≥" : "≤"} ${finite(limit) ? withUnit(Number(limit), unit) : "–"}`;
