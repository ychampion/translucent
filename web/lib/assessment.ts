// Where a campaign stands, as three separate answers (docs M2 contracts §2, loop-compare/3):
//   feasibility: does the baseline meet this objective version's constraints; does the best candidate;
//   speed:       compared only with a reference that meets the same constraints (never the failing baseline);
//   objective:   a hard target is met / not met; "fastest feasible" stays open (there is no proven optimum).
// The server computes `view.assessment`. Older recordings have none: `legacyAssessment` derives a conservative one
// from the recorded comparisons, and never calls a feasibility recovery "improved" or "faster".
import {
  baselineFor, constraintLine, limitNames, num, opWord, OUTCOME_WORD, prose, targetLine, withUnit,
  type Basis, type Comparison, type Constraint, type LoopRequest, type LoopView,
} from "./loop";

export interface SpeedRef {
  label: string; source: "baseline" | "reference_search"; value: number; unit: string; objective_version: number;
  request_id: string | null;
}
export interface Assessment {
  basis: Basis;
  feasibility: {
    baseline: "fails" | "meets" | "unknown"; best: "meets" | "fails" | "unknown" | "none";
    recovered: boolean; best_request_id: string | null;
  };
  speed: {
    reference: SpeedRef | null; best: { request_id: string; value: number; unit: string } | null;
    delta: number | null; faster: boolean | null; status: "faster" | "not_faster" | "not_compared"; reason: string;
  };
  objective: { kind: string; status: "met" | "not_met" | "unknown" | "open"; target_limit: number | null };
  uncertainty: { solver: { rtol: number | null; atol: number | null; note: string } | null; model: string };
  headline: string;
  lines: string[];
}

/** The view's assessment, or one derived from its comparisons when the file predates assessments. */
export function assessmentOf(v: LoopView): { a: Assessment; derived: boolean } {
  return v.assessment ? { a: v.assessment, derived: false } : { a: legacyAssessment(v), derived: true };
}

/** Every limit of a comparison holds (an unknown check is not a pass). */
const allHold = (c: Comparison) => c.constraints.length > 0 && c.constraints.every((x) => x.ok === true);
const anyBroken = (c: Comparison) => c.constraints.some((x) => x.ok === false);

/** The best feasible comparison on the current objective version: lowest target when minimising, highest otherwise. */
function bestFeasible(v: LoopView): Comparison | null {
  const t = v.objective.target;
  const feasible = v.comparisons.filter((c) => c.objective_version === v.objective.version && allHold(c) && c.target.candidate != null
    && requestRole(v.requests.find((r) => r.request_id === c.request_id)) === "experiment");
  if (!feasible.length) return null;
  const better = (a: number, b: number) => (t.direction === "maximize" ? a > b : a < b);
  return feasible.reduce((x, y) => (better(y.target.candidate!, x.target.candidate!) ? y : x));
}

/**
 * A conservative assessment for a view without one (loop-compare/1 or /2). Feasibility comes from the recorded checks;
 * speed is compared only when the baseline itself meets the constraints (it is then a valid reference); a /2
 * "feasibility" verdict means the limits decided it, so speed is "not compared".
 */
export function legacyAssessment(v: LoopView): Assessment {
  const t = v.objective.target;
  const current = v.comparisons.filter((c) => c.objective_version === v.objective.version);
  const best = bestFeasible(v);
  const latest = current[current.length - 1] ?? null;
  const basis: Basis = best?.basis ?? latest?.basis ?? v.baseline.find((b) => Object.keys(b.values).length)?.basis ?? "computational";
  const base = baselineFor(v, basis);
  // The baseline against this version's limits: a comparison's own baseline checks are not in the view, so read the
  // baseline's recorded checks (they are evaluated against the current version by the server's view).
  const baseChecks = base?.constraints ?? [];
  const baseline: Assessment["feasibility"]["baseline"] = !baseChecks.length ? "unknown"
    : baseChecks.some((c) => c.ok === false) ? "fails" : baseChecks.every((c) => c.ok === true) ? "meets" : "unknown";
  const bestState: Assessment["feasibility"]["best"] = best ? "meets"
    : !current.length ? "none" : current.some(anyBroken) ? "fails" : "unknown";
  const recovered = baseline === "fails" && bestState === "meets";

  // Speed: only against a reference meeting the same constraints. The failing baseline never is one.
  const baseValue = base?.values[t.quantity]?.value ?? null;
  let speed: Assessment["speed"];
  if (baseline === "meets" && baseValue != null && best?.target.candidate != null) {
    const delta = t.direction === "maximize" ? best.target.candidate - baseValue : baseValue - best.target.candidate;
    const faster = delta > Math.max(0, t.margin);
    speed = {
      reference: { label: "Baseline (meets the constraints)", source: "baseline", value: baseValue, unit: t.unit, objective_version: v.objective.version, request_id: null },
      best: { request_id: best.request_id, value: best.target.candidate, unit: t.unit }, delta, faster,
      status: faster ? "faster" : "not_faster",
      reason: "Compared with the baseline, which meets this version’s constraints.",
    };
  } else {
    speed = {
      reference: null, best: best ? { request_id: best.request_id, value: best.target.candidate!, unit: t.unit } : null,
      delta: null, faster: null, status: "not_compared",
      reason: baseline === "fails"
        ? "The baseline breaks the constraints, so it is not a reference, and this recording has no reference run that meets them."
        : baseline === "unknown" ? "No reference meeting the same constraints is recorded." : "No feasible candidate to compare yet.",
    };
  }

  const limit = t.limit ?? null;
  const objective: Assessment["objective"] = limit == null
    ? { kind: v.objective.kind ?? "legacy", status: "open", target_limit: null }
    : {
      kind: v.objective.kind ?? "legacy", target_limit: limit,
      status: !best ? (current.length ? "not_met" : "unknown")
        : (t.direction === "maximize" ? best.target.candidate! >= limit : best.target.candidate! <= limit) ? "met" : "not_met",
    };

  const sim = basis === "computational" ? " in simulation" : "";
  const headline = recovered ? `Feasibility recovered${sim}: the baseline breaks the constraints, the best candidate meets them. Speed not compared.`
    : bestState === "meets" ? `A candidate meets the constraints${sim}.${speed.status === "faster" ? " It is faster than the baseline, which also meets them." : speed.status === "not_faster" ? " It is not faster than the baseline." : ""}`
      : bestState === "fails" ? `No candidate meets the constraints yet${sim}.` : "No result yet.";
  return {
    basis, feasibility: { baseline, best: bestState, recovered, best_request_id: best?.request_id ?? null }, speed, objective,
    uncertainty: { solver: null, model: v.campaign.system.solver?.validity ? `Not quantified in this recording. Model validity: ${prose(v.campaign.system.solver.validity)}` : "Not quantified in this recording." },
    headline, lines: [],
  };
}

// ---------------------------------------------------------------- the three answers, in words

export function feasibilityWords(a: Assessment): { answer: string; tone: "good" | "bad" | "none"; detail: string } {
  const f = a.feasibility;
  const base = f.baseline === "fails" ? "The baseline fails the constraints" : f.baseline === "meets" ? "The baseline meets the constraints" : "The baseline’s constraints are not checked";
  const best = f.best === "meets" ? "the best candidate meets them" : f.best === "fails" ? "no candidate meets them yet" : f.best === "none" ? "no candidate has a result yet" : "the candidates are too close to call";
  return {
    answer: f.recovered ? "Recovered" : f.best === "meets" ? "Meets" : f.best === "fails" ? "Not yet" : "Open",
    tone: f.best === "meets" ? "good" : f.best === "fails" ? "bad" : "none",
    detail: `${base}; ${best}.`,
  };
}

export function speedWords(a: Assessment): { answer: string; tone: "good" | "bad" | "none"; detail: string } {
  const s = a.speed;
  if (s.status === "not_compared") return { answer: "Not compared", tone: "none", detail: s.reason };
  const ref = s.reference!;
  const by = s.delta != null ? withUnit(Math.abs(s.delta), ref.unit) : "";
  const refWord = ref.source === "reference_search" && !/reference search/i.test(ref.label)
    ? `${ref.label} (best found by the reference search, v${ref.objective_version})` : ref.label;
  // the server's reason often restates the whole comparison; keep only its verdict clause then
  const reason = s.reason.includes(ref.label) ? s.reason.slice(s.reason.lastIndexOf(": ") + 2) : s.reason;
  const clause = reason ? ` ${reason.charAt(0).toUpperCase()}${reason.slice(1)}${/[.!?]$/.test(reason) ? "" : "."}` : "";
  return {
    answer: s.status === "faster" ? `Faster by ${by}` : "Not faster",
    tone: s.status === "faster" ? "good" : "bad",
    detail: `${s.best ? withUnit(s.best.value, s.best.unit) : "–"} against ${withUnit(ref.value, ref.unit)} for ${refWord}.${clause}`,
  };
}

export function objectiveWords(a: Assessment, v: LoopView): { answer: string; tone: "good" | "bad" | "none"; detail: string } {
  const o = a.objective, t = v.objective.target;
  if (o.status === "open") {
    return { answer: "Open", tone: "none", detail: "Fastest feasible: there is no proven optimum, so this stays open. The best result so far is the best found, not the best possible." };
  }
  const target = o.target_limit != null ? `${targetLine(t)} to ${withUnit(o.target_limit, t.unit)}` : targetLine(t);
  return {
    answer: o.status === "met" ? "Met" : o.status === "not_met" ? "Not met" : "Unknown",
    tone: o.status === "met" ? "good" : o.status === "not_met" ? "bad" : "none",
    detail: `Hard target: ${target}.`,
  };
}

// ---------------------------------------------------------------- the concluded outcome

/**
 * The conclusion's outcome as the page says it. A loop-compare/1-2 "improved" outcome whose speed was never compared
 * against a valid reference (the limits decided it) is said as the feasibility answer it is, with the recorded value
 * kept beside it; /3 outcomes are said as they are.
 */
export function outcomeWords(v: Pick<LoopView, "conclusion">, a: Assessment): { word: string; recorded: string | null } {
  const o = v.conclusion?.outcome;
  if (!o) return { word: "", recorded: null };
  if ((o === "improved" || o === "improved_in_simulation") && a.speed.status !== "faster") {
    const sim = o === "improved_in_simulation" ? " in simulation" : "";
    return { word: a.feasibility.recovered ? `Feasibility recovered${sim}` : `Feasible${sim}, speed not compared`, recorded: o };
  }
  return { word: OUTCOME_WORD[o] ?? o, recorded: null };
}

// ---------------------------------------------------------------- the objective's header line

/**
 * What the header shows: the current version's one-line summary (the server's `objective.summary`, else built from the
 * current target and constraints), and the registered statement only when it differs, as "as registered, vN". An old
 * +10 mV statement after a +25 mV amendment is never shown as the current constraint.
 */
export function objectiveHeader(v: Pick<LoopView, "objective">): { summary: string; registered: { text: string; version: number } | null } {
  const o = v.objective;
  const summary = o.summary?.trim() || derivedSummary(o.target, o.constraints, o.target.limit);
  const statement = o.statement?.trim() ?? "";
  const registered = statement && statement !== summary ? { text: statement, version: o.statement_version ?? 1 } : null;
  return { summary, registered };
}

function derivedSummary(t: LoopView["objective"]["target"], cs: Constraint[], limit?: number | null): string {
  const what = limit != null ? `${targetLine(t)} to ${withUnit(limit, t.unit)}` : targetLine(t);
  if (!cs.length) return what;
  const parts = cs.map((c) => `${c.quantity.replace(/^(min|max)_/, "").replace(/_/g, " ")} ${c.op === ">=" ? "≥" : c.op === "<=" ? "≤" : c.op} ${withUnit(c.limit, c.unit)}`);
  return `${what} with ${parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0]}`;
}

// ---------------------------------------------------------------- requests: experiments, baseline runs, references

/** A request's role: the server's, else the baseline re-run (registration policy) or an experiment. */
export function requestRole(r: Pick<LoopRequest, "role" | "proposed_by"> | undefined): "experiment" | "baseline" | "reference" {
  if (!r) return "experiment";
  if (r.role) return r.role;
  return r.proposed_by === "policy:registration" ? "baseline" : "experiment";
}

export const ROLE_TAG: Record<"experiment" | "baseline" | "reference", string> = {
  experiment: "", baseline: "Baseline run", reference: "Reference run",
};

/** The limits a quantity had over the objective's versions, from the recorded checks (for the chart's amendment mark). */
export function limitHistory(v: LoopView, quantity: string): { version: number; limit: number }[] {
  const byVersion = new Map<number, number>();
  for (const c of v.comparisons) {
    const ch = c.constraints.find((x) => x.quantity === quantity);
    if (ch && !byVersion.has(c.objective_version)) byVersion.set(c.objective_version, ch.limit);
  }
  const cur = v.objective.constraints.find((c) => c.quantity === quantity);
  if (cur) byVersion.set(v.objective.version, cur.limit);
  return [...byVersion.entries()].sort((a, b) => a[0] - b[0]).map(([version, limit]) => ({ version, limit }));
}

/** "Plating margin: anode potential ≥ 25 mV", for a constraint chip. */
export const constraintChip = (c: Constraint) => constraintLine(c);

/** "37.3 min" for a target value. */
export const targetValue = (v: LoopView, value: number | null | undefined) => withUnit(value ?? null, v.objective.target.unit);

export { num };

/** Constraint lines for a series: the current limit, and earlier limits changed by amendment (marked "vN"). */
export function seriesLimits(v: LoopView, seriesQuantity: string, unit: string): { value: number; label: string; past: boolean }[] {
  const c = v.objective.constraints.find((x) => x.unit === unit && limitNames(x.quantity, seriesQuantity));
  if (!c) return [];
  const hist = limitHistory(v, c.quantity);
  const out = [{ value: c.limit, label: `${opWord(c.op)} ${withUnit(c.limit, c.unit)}${hist.length > 1 ? ` (v${v.objective.version})` : ""}`, past: false }];
  for (const h of hist) if (h.version !== v.objective.version && h.limit !== c.limit && !out.some((o) => o.value === h.limit)) {
    out.push({ value: h.limit, label: `${opWord(c.op)} ${withUnit(h.limit, c.unit)} until amended (v${h.version})`, past: true });
  }
  return out;
}

