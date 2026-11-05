// The turning points of a records investigation, derived from its recording: the engineer's records and question, the
// model check that failed on the other cell, the pooled fit that could not explain both, the measurement Muse chose
// for the lab's own fixture, and the held-out check behind the design that is still untested.
//
// Every number is read from the recorded view (requests, results, model versions, lab tests, the conclusion). The
// short connecting lines are paraphrases of what the recording says, each tied to the request it comes from; Muse's
// own words are quoted only by lifting a sentence out of the recorded rationale. A recording without this shape
// (no failed held-out check, no pooled fit, no lab test after it) gets no walkthrough: nothing is filled in.
import {
  isRecordTest, labPrediction, num, plainWords, plannerWord, prose, versionOf, withUnit, cRatesLine, fitValue,
  type LabTest, type LoopRequest, type LoopView, type ModelVersion,
} from "./loop";

export interface WalkFact { label: string; value: string; tone?: "good" | "bad" }
export interface WalkLink { label: string; requestId: string }
export interface WalkStep {
  n: number;
  /** A few words for the step list; `title` heads the step itself. */
  short: string;
  title: string;
  /** What the agent believed going in. */
  believed: string;
  /** What it saw that changed (or confirmed) that belief. */
  observed: string;
  /** What Muse chose to do next. */
  chose: string;
  /** What the numerical tools computed at this step: numbers only, each from a recorded result. */
  computed: WalkFact[];
  /** Muse's own words, one sentence lifted from the recorded rationale (request ids taken out), or null. */
  quote: { text: string; requestId: string } | null;
  /** Where the workspace goes when this step is chosen: a tab, the request to select and whether its rationale opens. */
  focus: { tab: "overview" | "experiments"; requestId: string | null; rationale: boolean };
  /** The requests this step rests on, to open in Experiments. */
  links: WalkLink[];
}
export interface WalkOutcome {
  rates: string; minutes: number | null; members: number | null; peak: number | null; peakLimit: number | null;
  anode: number | null; anodeLimit: number | null; modelVersion: number; requestId: string | null;
  /** Simulated lab tests used, and the allowance when the recording states it. */
  labTests: { used: number; cap: number | null; numbers: number[] };
  experiments: { used: number; budget: number };
  /** Whether any lab test ran the design's exact protocol. */
  designTested: boolean;
  /** What the conclusion leaves unresolved, in its own words (request ids taken out). */
  unresolved: string[];
}
export interface Walkthrough { planner: string; solver: string | null; approvals: string; steps: WalkStep[]; outcome: WalkOutcome }

// ---------------------------------------------------------------- reading the recording

const reqOf = (v: LoopView, id: string | null | undefined) => (id ? v.requests.find((r) => r.request_id === id) : undefined);
const numberOf = (v: LoopView, id: string | null | undefined) => (id ? v.requests.findIndex((r) => r.request_id === id) + 1 : 0);
const measured = (r: LoopRequest | undefined, q: string) => r?.result?.measurements.find((m) => m.quantity === q)?.value ?? null;
const predicted = (r: LoopRequest | undefined, q: string) => r?.prediction?.[q] ?? null;
const missed = (r: LoopRequest | undefined, q: string) => r?.result?.prediction_error?.[q]?.inside === false;
const pctWord = (x: number | null | undefined) => (x == null ? "–" : `${num(Math.round(x * 1000) / 10)} %`);
/** "46 °C, range 38–54": a sealed prediction inside a sentence that is already in parentheses. */
const predictedPlain = (p: { value: number | null; lo?: number | null; hi?: number | null; unit: string }) =>
  `${withUnit(p.value, p.unit)}${p.lo != null && p.hi != null ? `, range ${num(p.lo)}–${num(p.hi)}` : ""}`;
/** "0.4 K (0.1 to 1 K)": a sealed prediction in a sentence with no parentheses of its own. */
const predictedParen = (p: { value: number | null; lo?: number | null; hi?: number | null; unit: string }) =>
  `${withUnit(p.value, p.unit)}${p.lo != null && p.hi != null ? ` (${num(p.lo)} to ${withUnit(p.hi, p.unit)})` : ""}`;
const kelvin = (x: number | null | undefined) => withUnit(x, "K");
const cellName = (t: LabTest) => (t.record?.ref ?? `test ${t.n}`).split("/").pop()!;
/** "lgm50_cell02", "lgm50_cell03" -> "cell02", "cell03": the shared prefix before the last "_" adds nothing. */
function shortNames(names: string[]): string[] {
  const pre = names.map((n) => n.slice(0, n.lastIndexOf("_") + 1));
  return pre.every((p) => p && p === pre[0]) ? names.map((n) => n.slice(pre[0].length)) : names;
}
const sameRates = (a: (number | null)[] | null | undefined, b: (number | null)[] | null | undefined) =>
  !!a?.length && !!b?.length && a.length === b.length && a.every((x, i) => x != null && b[i] != null && Math.abs(x - (b[i] as number)) < 1e-9);
const labRates = (t: LabTest) => ["c_rate_1", "c_rate_2", "c_rate_3", "c_rate_4"].map((k) => (typeof t.protocol?.[k] === "number" ? (t.protocol[k] as number) : null));
const range = (r: [number, number] | undefined) => (r ? `${fitValue(r[0])}–${fitValue(r[1])}` : "–");
const overlaps = (a: [number, number] | undefined, b: [number, number] | undefined) => !a || !b || (a[0] <= b[1] && b[0] <= a[1]);

/** Request, result and comparison ids out of recorded prose, with the parentheses that carried them. */
export function withoutIds(s: string): string {
  return s
    .replace(/\s*\((?:[^()]|\([^()]*\))*\b(?:rq|res|cmp)_[0-9a-f]{8,}(?:[^()]|\([^()]*\))*\)/g, "")
    .replace(/\s*\b(?:rq|res|cmp)_[0-9a-f]{8,}\b:?/g, "")
    .replace(/\s+([,.;:])/g, "$1").replace(/\s{2,}/g, " ").trim();
}
/** The rationale's sentences, ids removed. A decimal point or "e.g." does not end a sentence. */
export function sentences(text: string | null | undefined): string[] {
  return (text ?? "").split(/\n+/).flatMap((p) => p.split(/(?<=[.!?])\s+(?=[A-Z])/)).map((s) => withoutIds(s.trim())).filter(Boolean);
}
/** One recorded sentence as a quotation: units tidied, cut at a colon that opens a long list of figures. */
function quoted(s: string): string {
  const cut = s.indexOf(": ");
  const body = cut > 40 && s.length - cut > 60 ? `${s.slice(0, cut)}.` : s;
  return plainWords(prose(body.replace(/W\/m2\/K/g, "W/m²/K"))).replace(/\s*\.$/, ".");
}
/** Whether the recorded rationale says this (the paraphrase that rests on it is shown only then). */
const says = (r: LoopRequest | undefined, re: RegExp) => re.test(r?.rationale ?? "");
function quoteFrom(r: LoopRequest | undefined, re: RegExp): WalkStep["quote"] {
  const s = sentences(r?.rationale).find((x) => re.test(x));
  return s && r ? { text: quoted(s), requestId: r.request_id } : null;
}
/** "Hypothesis: X; ..." or "Hypothesis: X. ..." -> "X." */
function hypothesisOf(r: LoopRequest | undefined): WalkStep["quote"] {
  const m = r?.rationale?.match(/Hypothesis:\s*([^;.\n—]+?)\s*(?:[;.\n—]|$)/);
  return m && r ? { text: `${plainWords(prose(m[1].trim()))}.`, requestId: r.request_id } : null;
}
/** The adequacy rule's RMSE limit, as a version's recorded standing states it ("rule 0.3 K"). */
function ruleK(versions: ModelVersion[]): number | null {
  for (const x of versions) {
    const m = x.standing?.reason?.match(/rule ([\d.]+) K/);
    if (m) return Number(m[1]);
  }
  return null;
}
/** "the lab-test budget is spent (2 of 2, ...)" in the conclusion: the allowance, when the recording states one. */
function labCap(v: LoopView): number | null {
  const texts = [v.conclusion?.text ?? "", ...(v.conclusion?.unresolved_reasons ?? [])];
  for (const t of texts) {
    const m = t.match(/lab-test budget is spent[^()]*\((\d+) of (\d+)/);
    if (m) return Number(m[2]);
  }
  return null;
}
const TOOL: Record<string, string> = {
  record_test: "Record reader", fit_thermal: "Thermal fit", check_model: "Held-out check", lab_charge: "Simulated lab", optimize_protocol: "Design search",
};
const toolOf = (r: LoopRequest | undefined) => (r ? TOOL[r.action] ?? r.action.replace(/_/g, " ") : "");
const fitFact = (x: ModelVersion, rule: number | null): WalkFact => ({
  label: `${TOOL.fit_thermal}, model v${x.version} (${x.family ?? "fit"})`,
  value: `fit RMSE ${kelvin(x.fit_rmse_K)}${rule != null ? `, rule ${kelvin(rule)}` : ""}: ${x.adequate ? "meets it" : "misses it"}`,
  tone: x.adequate ? "good" : "bad",
});

// ---------------------------------------------------------------- the five steps

export function walkthroughOf(v: LoopView): Walkthrough | null {
  const L = v.learning, inv = v.investigation;
  if (!L?.versions?.length || !inv?.uploads?.length || !v.conclusion) return null;
  const versions = [...L.versions].sort((a, b) => a.version - b.version);
  const tests = L.lab_tests ?? [];
  const records = tests.filter(isRecordTest);
  const labs = tests.filter((t) => !isRecordTest(t));
  const rule = ruleK(versions);
  const planner = plannerWord(v);

  // 2: the first version a held-out check failed: checked, and without standing
  const failed = versions.find((x) => x.checks?.length && x.standing?.supported === false);
  const check = failed?.checks?.[0];
  const checkReq = reqOf(v, check?.request_id);
  if (!failed || !check || !checkReq?.result || records.length < 2) return null;
  const fitOn = failed.sources ?? [];
  const heldOut = check.lab_tests ?? [];
  // 3: after it, one fit pooled over both (not adequate), and the held-out cell fitted alone
  const pooled = versions.find((x) => x.version > failed.version && x.adequate === false && [...fitOn, ...heldOut].every((n) => x.sources?.includes(n)));
  const alone = versions.find((x) => x.version > failed.version && !!x.sources?.length && x.sources.every((n) => heldOut.includes(n)));
  const pooledReq = reqOf(v, pooled?.request_id);
  if (!pooled || !pooledReq) return null;
  // 4: the first lab test after the pooled fit, and the version fitted to it
  const lab = labs.find((t) => numberOf(v, t.request_id) > numberOf(v, pooled.request_id));
  const labReq = reqOf(v, lab?.request_id);
  const labV = lab ? versions.find((x) => !!x.sources?.length && x.sources.every((n) => n === lab.n)) : undefined;
  if (!lab || !labReq?.result || !labV) return null;
  // 5: the version in use with a passing check, the test it was checked on, and the design it stands behind
  const inUse = versionOf(L, L.model_in_use);
  const pass = inUse?.standing?.supported ? inUse.checks?.[0] : undefined;
  const passReq = reqOf(v, pass?.request_id);
  const passTest = tests.find((t) => pass?.lab_tests?.includes(t.n));
  const design = L.recommended_design ?? L.best_design;
  const designReq = reqOf(v, design?.request_id);
  if (!inUse || !pass || !passReq?.result || !passTest || !design) return null;

  const [nameA, nameB] = shortNames([records.find((t) => fitOn.includes(t.n)) ?? records[0], records.find((t) => heldOut.includes(t.n)) ?? records[1]].map(cellName));
  const recA = records.find((t) => fitOn.includes(t.n)) ?? records[0], recB = records.find((t) => heldOut.includes(t.n)) ?? records[1];
  const v0 = versions.find((x) => x.version === 0);
  const firstReq = reqOf(v, recA.request_id) ?? v.requests[0];
  const recordFits = versions.filter((x) => x.version > 0 && x.version <= failed.version);
  const limitT = v.objective.constraints.find((c) => c.quantity === "max_temperature")?.limit ?? design.limits?.t_limit_C ?? null;
  const limitA = v.objective.constraints.find((c) => c.quantity === "min_anode_potential")?.limit ?? design.limits?.margin_mV ?? null;
  const cap = labCap(v);
  const tested = labs.some((t) => sameRates(labRates(t), design.c_rates));

  // 1 ------------------------------------------------------------
  const step1: WalkStep = {
    n: 1,
    short: "Records and question",
    title: "The engineer’s records and question",
    believed: `Nothing yet about this lab’s fixture: the only model is the parameter set as shipped${v0?.estimate?.h != null ? ` (heat transfer ${num(v0.estimate.h)} W/m²/K)` : ""}, calibrated to no fixture. The question above is the engineer’s; only an engineer’s amendment can change it.`,
    observed: `${inv.uploads.length} uploaded files, one per cell. Each holds a ${withUnit(recA.record?.test_c_rate, "1/h")} discharge; read as tests, ${nameA} warmed by ${kelvin(recA.record?.temperature_rise)} and ${nameB} by ${kelvin(recB.record?.temperature_rise)} at the same current.`,
    chose: `Fit a thermal model to ${nameA} only and hold ${nameB} back, so the model can be checked on a cell it did not fit.`,
    computed: [
      ...records.map((t) => ({ label: `${TOOL.record_test}, test ${t.n} (${cellName(t)})`, value: `${withUnit(t.record?.test_c_rate, "1/h")} for ${withUnit(t.record?.test_duration, "min")}, warmed ${kelvin(t.record?.temperature_rise)}` })),
      ...recordFits.map((x) => fitFact(x, rule)),
    ],
    quote: hypothesisOf(firstReq),
    // the records' requests (#1 to #4), not the investigation's list of missing information
    focus: { tab: "experiments", requestId: records[0]?.request_id ?? null, rationale: false },
    links: [...records.map((t) => ({ label: `Test ${t.n}, ${cellName(t)}`, requestId: t.request_id! })).filter((l) => l.requestId),
      ...recordFits.filter((x) => x.request_id).map((x) => ({ label: `Fit, model v${x.version}`, requestId: x.request_id! }))],
  };

  // 2 ------------------------------------------------------------
  const cov = measured(checkReq, "heldout_coverage"), rmse = measured(checkReq, "heldout_rmse"), maxErr = measured(checkReq, "heldout_max_error");
  const pCov = predicted(checkReq, "heldout_coverage"), pRmse = predicted(checkReq, "heldout_rmse");
  const allMissed = ["heldout_coverage", "heldout_rmse", "heldout_max_error"].every((q) => missed(checkReq, q));
  const step2: WalkStep = {
    n: 2,
    short: "Fails on the other cell",
    title: `The model fails on the other cell`,
    believed: `Model v${failed.version} (${failed.family}) fitted ${nameA} closely, fit RMSE ${kelvin(failed.fit_rmse_K)}. ${planner} expected it to hold on ${nameB} too: it predicted coverage ${pCov ? predictedParen({ ...pCov, unit: "" }) : "–"} and RMSE ${pRmse ? predictedParen({ ...pRmse, unit: "K" }) : "–"}.`,
    observed: `On ${nameB}, which it had not fitted, it did not hold: only ${pctWord(cov)} of the held-out temperature samples fell inside the model’s range, RMSE ${kelvin(rmse)}, worst error ${kelvin(maxErr)}.${allMissed ? ` All three are outside what ${planner} predicted.` : ""}`,
    chose: `Pool both cells into one fit, to test whether any single set of thermal parameters explains both.`,
    computed: [
      { label: `${toolOf(checkReq)}, model v${failed.version} on test ${heldOut.join(", ")}`, value: `coverage ${num(cov)}, RMSE ${kelvin(rmse)}, max error ${kelvin(maxErr)}`, tone: "bad" },
      { label: `Model v${failed.version}`, value: failed.standing?.supported === false ? "no standing for a design" : "–", tone: "bad" },
    ],
    quote: quoteFrom(pooledReq, /^I now believe/),
    focus: { tab: "experiments", requestId: checkReq.request_id, rationale: false },
    links: [{ label: "The check", requestId: checkReq.request_id }, { label: `${planner}’s response`, requestId: pooledReq.request_id }],
  };

  // 3 ------------------------------------------------------------
  const disjoint = alone ? Object.keys(alone.region_95 ?? {}).filter((k) => !overlaps(alone.region_95?.[k], failed.region_95?.[k])) : [];
  const aloneReq = reqOf(v, alone?.request_id);
  const step3: WalkStep = {
    n: 3,
    short: "No model fits both cells",
    title: "No single model fits both cells",
    believed: says(pooledReq, /pool/i)
      ? `${planner} took the failure for a difference between the cells, and expected one fit pooled over both to find a compromise that explains each.`
      : `Model v${failed.version} fitted only ${nameA}; a fit over both cells comes next.`,
    observed: `The pooled fit, model v${pooled.version}, missed the rule: fit RMSE ${kelvin(pooled.fit_rmse_K)}${rule != null ? ` against ${kelvin(rule)}` : ""}.${alone
      ? ` Fitted alone, ${nameB} (model v${alone.version}) met it at ${kelvin(alone.fit_rmse_K)}, but ${disjoint.length ? `its parameter ranges do not overlap ${nameA}’s` : "with different parameters"}.` : ""}`
      + (says(labReq, /cell-to-cell variation/i) && says(labReq, /no record-based version has standing/i)
        ? ` The hypothesis changed: the two cells need different thermal parameters, so no model fitted to the records can stand for the lab’s fixture.` : ""),
    chose: `Stop learning from the records and measure the lab’s own fixture.`,
    computed: [
      fitFact(pooled, rule),
      ...(alone ? [fitFact(alone, rule)] : []),
      ...disjoint.map((k) => ({ label: `95 % range of ${k === "h" ? "heat transfer h" : k === "k" ? "thermal-mass scale k" : k}`, value: `${nameA} ${range(failed.region_95?.[k])}${k === "h" ? " W/m²/K" : ""} vs ${nameB} ${range(alone!.region_95?.[k])}${k === "h" ? " W/m²/K" : ""}: no overlap` })),
    ],
    quote: quoteFrom(labReq, /standing/i),
    focus: { tab: "experiments", requestId: pooledReq.request_id, rationale: true },
    links: [{ label: `Pooled fit, model v${pooled.version}`, requestId: pooledReq.request_id }, ...(aloneReq ? [{ label: `${nameB} alone, model v${alone!.version}`, requestId: aloneReq.request_id }] : [])],
  };

  // 4 ------------------------------------------------------------
  const rates = labRates(lab);
  const uniform = rates.every((x) => x != null && x === rates[0]);
  const p = labPrediction(lab);
  const peak = lab.observed?.max_temperature ?? null;
  const labVReq = reqOf(v, labV.request_id);
  const step4: WalkStep = {
    n: 4,
    short: "A measurement of the fixture",
    title: `${planner} chooses a measurement of the lab’s fixture`,
    believed: says(labReq, /no record-based version has standing/i)
      ? `No model from the records has standing in the lab${/fixture may differ/i.test(v.objective.statement) ? ", whose fixture the objective says may differ from the records’" : ""}.`
      : `The models so far were fitted to the records, not to the lab.`,
    chose: `A ${uniform ? withUnit(rates[0], "1/h") : cRatesLine(rates)} charge at ${withUnit(lab.protocol?.ambient ?? null, "degC")} ambient, then a ${withUnit(lab.protocol?.rest_min ?? null, "min")} logged rest.${says(labReq, /largest thermal signal/i) && says(labReq, /cooling trace/i)
      ? " The same rate as the record discharges gives the largest heating signal, and the rest’s cooling trace pins down thermal mass against cooling." : ""}${says(labReq, /measurement, not a design/i)
      ? ` ${planner} said in advance it is a measurement, not a design, and may exceed the limit.` : ""}`,
    observed: `The simulated lab returned a peak of ${withUnit(peak, "degC")}${limitT != null && peak != null ? `, ${peak > limitT ? "above" : "within"} the ${withUnit(limitT, "degC")} limit` : ""}${p ? ` (${planner} had predicted ${predictedPlain(p)})` : ""}, and ${withUnit(lab.observed?.temperature_end ?? null, "degC")} after the rest.`,
    computed: [
      { label: `${TOOL.lab_charge}, test ${lab.n}`, value: `peak ${withUnit(peak, "degC")}, ${withUnit(lab.observed?.charge_time_80 ?? null, "min")} to 80 %, ${withUnit(lab.observed?.temperature_end ?? null, "degC")} at the end`, tone: limitT != null && peak != null && peak > limitT ? "bad" : undefined },
      { ...fitFact(labV, rule), label: `${TOOL.fit_thermal} to test ${lab.n}, model v${labV.version}` },
      ...Object.entries(labV.estimate ?? {}).map(([k, x]) => ({ label: k === "h" ? "Heat transfer h" : k === "k" ? "Thermal-mass scale k" : k, value: `${fitValue(x)}${k === "h" ? " W/m²/K" : ""}` })),
    ],
    quote: quoteFrom(labReq, /discriminating|largest thermal signal/i),
    focus: { tab: "experiments", requestId: labReq.request_id, rationale: true },
    links: [{ label: `Lab test ${lab.n}`, requestId: labReq.request_id }, ...(labVReq ? [{ label: `Fit, model v${labV.version}`, requestId: labVReq.request_id }] : [])],
  };

  // 5 ------------------------------------------------------------
  const passTestReq = reqOf(v, passTest.request_id);
  const pRates = labRates(passTest);
  const pUniform = pRates.every((x) => x != null && x === pRates[0]);
  const hRmse = measured(passReq, "heldout_rmse"), hMax = measured(passReq, "heldout_max_error"), hCov = measured(passReq, "heldout_coverage");
  const members = measured(designReq, "members");
  const passP = labPrediction(passTest);
  const step5: WalkStep = {
    n: 5,
    short: "Held-out check and a design",
    title: "Checked on a test it did not fit, then a design",
    believed: `Model v${inUse.version} fits test ${(inUse.sources ?? []).join(", ")}${/tests? it did not fit/i.test(L.standing_rule ?? "") ? ", but a design may rest only on a model that also passes a check on a test it did not fit" : ""}.`,
    chose: `A ${pUniform ? withUnit(pRates[0], "1/h") : cRatesLine(pRates)} lab charge with the same rest, as a test v${inUse.version} had not seen; after it passed, a search for the fastest charge every member of v${inUse.version} says meets the limits.`,
    // The model's numbers here come from the held-out check, computed after the lab returned: a replay, not a forecast
    // made before the test (none was recorded). The rule bounds the RMSE; the worst error is reported beside it.
    observed: `At ${pUniform ? withUnit(pRates[0], "1/h") : cRatesLine(pRates)} the lab peaked at ${withUnit(passTest.observed?.max_temperature ?? null, "degC")}${passP ? ` (${planner} had predicted ${predictedPlain(passP)})` : ""}. Afterwards, model v${inUse.version}, which was not fitted to this test, was checked against it: RMSE ${kelvin(hRmse)}${rule != null && hRmse != null ? `, ${hRmse <= rule ? "inside" : "outside"} the ${kelvin(rule)} rule` : ""}, with a worst single error of ${kelvin(hMax)}.`,
    computed: [
      { label: `${toolOf(passReq)}, model v${inUse.version} on test ${passTest.n}`, value: `coverage ${num(hCov)}, RMSE ${kelvin(hRmse)}, max error ${kelvin(hMax)}`, tone: "good" },
      { label: `${TOOL.optimize_protocol}, model v${design.model_version}`, value: `${cRatesLine(design.c_rates)}, ${withUnit(design.charge_time_80, "min")} from 10 to 80 %${members != null ? ` for the slowest of ${num(members)} members` : ""}` },
      { label: "Peak temperature, forecast", value: `${withUnit(design.max_temperature, "degC")}${limitT != null && design.max_temperature != null ? `, ${fitValue(Math.round((limitT - design.max_temperature) * 1000) / 1000)} °C under the limit` : ""}` },
      { label: "Lowest anode potential, forecast only", value: `${withUnit(design.min_anode_potential, "mV")}${limitA != null ? ` against ≥ ${withUnit(limitA, "mV")}` : ""}` },
    ],
    quote: quoteFrom(designReq, /^I now believe/),
    focus: { tab: "experiments", requestId: designReq?.request_id ?? passReq.request_id, rationale: false },
    links: [...(passTestReq ? [{ label: `Lab test ${passTest.n}`, requestId: passTestReq.request_id }] : []), { label: "The check", requestId: passReq.request_id },
      ...(designReq ? [{ label: "The design search", requestId: designReq.request_id }] : [])],
  };

  const approvers = new Set(v.requests.map((r) => r.approved_by).filter(Boolean) as string[]);
  const approvals = [...approvers].every((a) => a.startsWith("policy:"))
    ? "a standing rule approved every request" : [...approvers].some((a) => a.startsWith("person:")) ? "a person approved each request" : "each request was approved before it ran";
  return {
    planner, solver: v.campaign.system.solver?.name ?? null, approvals,
    steps: [step1, step2, step3, step4, step5],
    outcome: {
      rates: cRatesLine(design.c_rates), minutes: design.charge_time_80 ?? null, members, peak: design.max_temperature ?? null, peakLimit: limitT,
      anode: design.min_anode_potential ?? null, anodeLimit: limitA, modelVersion: design.model_version, requestId: designReq?.request_id ?? null,
      labTests: { used: labs.length, cap, numbers: labs.map((t) => t.n) },
      experiments: { used: v.spend.experiments_used, budget: v.spend.experiments_budget },
      designTested: tested,
      unresolved: v.conclusion.unresolved_reasons.map((r) => plainWords(prose(withoutIds(r)))),
    },
  };
}
