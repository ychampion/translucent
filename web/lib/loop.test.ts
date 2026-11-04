import { afterEach, describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";
import { ApiError, detailText, loadRecording, v1 } from "./api";
import {
  actorWord, artifactIdOf, bannerLine, baselineFor, bestComparison, byRecency, canAct, change, constraintLine, limitFor,
  num, outcomeOfVerdict, paramsLine, parseLoopFile, predictedLine, RECORDING_NAME, recordingUrl, requestTitle,
  categoriesLine, seriesFromBlob, spendLines, validityWarning, valueLine, standing, stateCounts, targetLine, thin, withUnit,
  actorIn, decidedByLimits, idWords, limitValue, OBJECTIVE_CUT, objectiveLine, plannerIsAgent, plannerWord, prose, reasonsLine,
  unbroken, verdictSentence, wordsFor,
  bestIsOlderModel, designCaveat, designHeading, designShown, leadLine, PARAM_RECORDED, paramText, cRatesLine, FASTCHARGE_LAB_HREF, labTestOf, marginLine, nextStepOf, paramLine, fitValue, traceChart, versionOf,
  bytesWord, engineerConstraints, isRecordTest, priorLines, recordStretchLine, spanWord, uncertaintyWord,
  type Comparison, type Learning, type LoopView, type SystemSpecLite,
} from "./loop";

// The S4b sample view (tests/data/loop_view_sample.json): a concluded toy campaign, one rejected and one completed request.
const SAMPLE = JSON.parse(readFileSync(join(import.meta.dir, "..", "..", "tests", "data", "loop_view_sample.json"), "utf-8")) as LoopView;
const sample = () => structuredClone(SAMPLE);

describe("reading files", () => {
  test("a bare view is a recording without a receipt", () => {
    const r = parseLoopFile(sample());
    expect(r.receipt).toBe(false);
    expect(r.view.campaign.id).toBe("loop_8f1f34a18db3efe1");
  });
  test("a loop export carries its view and the receipt's blobs", () => {
    const blobs = { "sha256:ab": { sha256: "x", bytes: 1, data: "" } };
    const r = parseLoopFile({ loop_export_version: 1, view: sample(), receipt: { campaign_id: "c", ledger_id: "l", ledger: blobs["sha256:ab"], blobs } });
    expect(r.receipt).toBe(true);
    expect(Object.keys(r.blobs)).toEqual(["sha256:ab"]);
  });
  test("other files and other versions are refused plainly", () => {
    expect(() => parseLoopFile({ hello: 1 })).toThrow("not a loop campaign export");
    expect(() => parseLoopFile({ ...sample(), view_version: 2 })).toThrow("version 2");
    expect(() => parseLoopFile({ loop_export_version: 2, view: sample() })).toThrow("version 2");
    expect(() => parseLoopFile(null)).toThrow();
  });
  test("recording names never carry a path", () => {
    expect(RECORDING_NAME.test("fastcharge")).toBe(true);
    expect(RECORDING_NAME.test("../secrets")).toBe(false);
    expect(RECORDING_NAME.test("a/b")).toBe(false);
    expect(recordingUrl("fastcharge")).toBe("/fixtures/loop/fastcharge.loop.json");
    expect(recordingUrl("fastcharge.loop.json")).toBe("/fixtures/loop/fastcharge.loop.json");
  });
  test("a series comes out of the receipt's gzip + base64 blob", async () => {
    const id = `sha256:${"8".repeat(64)}`;
    const data = gzipSync(Buffer.from(JSON.stringify({ t: [0, 1, 2], y: [25, 26, 27.5], unit: "degC" }))).toString("base64");
    const s = await seriesFromBlob({ [id]: { sha256: "", bytes: 0, data } }, `/api/v1/ledgers/demo/artifacts/${id}`);
    expect(s).toEqual({ t: [0, 1, 2], y: [25, 26, 27.5], unit: "degC", t_unit: "s", n: 3, downsampled: false });
    expect(await seriesFromBlob({}, `/api/v1/ledgers/demo/artifacts/${id}`)).toBeNull();
    expect(artifactIdOf("/api/v1/ledgers/x/artifacts/nope")).toBeNull();
  });
  test("thin keeps at most the asked number of points", () => {
    const n = 5000, s = { t: [...Array(n).keys()], y: [...Array(n).keys()], unit: "s", t_unit: "s", n, downsampled: false };
    const t = thin(s, 1000);
    expect(t.t.length).toBeLessThanOrEqual(1000);
    expect(t.downsampled).toBe(true);
    expect(thin(s, n)).toBe(s);
  });
});

describe("the standing", () => {
  test("the conclusion's comparison leads, and its outcome is final", () => {
    const v = sample();
    expect(bestComparison(v)?.id).toBe("cmp_300dc84912798e9ec90919a4");
    expect(standing(v)).toEqual({ outcome: "improved_in_simulation", final: true });
    expect(baselineFor(v, "computational")?.basis).toBe("computational");
    expect(change(bestComparison(v)!)).toEqual({ delta: 1620 - 2880, better: true });
  });
  test("before a conclusion: the best candidate on the current objective, outcome so far", () => {
    const v = sample();
    v.conclusion = null;
    v.current_result = null;
    const worse: Comparison = { ...v.comparisons[0], id: "cmp_worse", verdict: "not_better", target: { baseline: 2880, candidate: 3000, delta: -120, better: false } };
    const old: Comparison = { ...v.comparisons[0], id: "cmp_old", objective_version: 0, target: { ...v.comparisons[0].target, delta: 9999 } };
    v.comparisons = [old, v.comparisons[0], worse];
    expect(bestComparison(v)?.id).toBe("cmp_300dc84912798e9ec90919a4");
    expect(standing(v)).toEqual({ outcome: "improved_in_simulation", final: false });
    v.comparisons = [worse];
    expect(standing(v)).toEqual({ outcome: "not_improved", final: false });
    v.comparisons = [];
    expect(standing(v)).toBeNull();
  });
  test("a simulated improvement is its own outcome; inconclusive is unresolved", () => {
    expect(outcomeOfVerdict({ verdict: "candidate_better", basis: "computational" })).toBe("improved_in_simulation");
    expect(outcomeOfVerdict({ verdict: "candidate_better", basis: "physical" })).toBe("improved");
    expect(outcomeOfVerdict({ verdict: "violates_constraints", basis: "physical" })).toBe("not_improved");
    expect(outcomeOfVerdict({ verdict: "inconclusive", basis: "physical" })).toBe("unresolved");
    expect(outcomeOfVerdict({ verdict: "not_comparable", basis: "computational" })).toBe("unresolved");
  });
  test("the baseline on the candidate's basis, else the first", () => {
    const v = sample();
    v.baseline = [{ ...v.baseline[0], basis: "physical" }, v.baseline[0]];
    expect(baselineFor(v, "computational")?.basis).toBe("computational");
    expect(baselineFor(v, "retrospective")?.basis).toBe("physical");
    v.baseline = [];
    expect(baselineFor(v, "physical")).toBeNull();
  });
  test("before any comparison, the baseline that has numbers: a record without the quantity, re-run on the solver", () => {
    const v = sample();
    v.baseline = [{ ...v.baseline[0], basis: "physical", values: {}, constraints: [], fails: [] }, v.baseline[0]];
    expect(baselineFor(v, undefined)?.basis).toBe("computational");
  });
});

describe("words", () => {
  test("numbers and units read at a glance", () => {
    expect(num(1620)).toBe("1620");
    expect(num(43.94)).toBe("43.9");
    expect(num(2.5)).toBe("2.5");
    expect(num(-55)).toBe("−55");
    expect(num(null)).toBe("–");
    expect(withUnit(43.9, "degC")).toBe("43.9 °C");
    expect(withUnit(2.5, "1/h")).toBe("2.5C");
    expect(withUnit(0.5, "1")).toBe("0.5");
    expect(valueLine({ value: 2880, unit: "s", h: 5 })).toBe("2880 s ± 5");
    expect(valueLine({ value: 5760, unit: "s", h: 1e-9 })).toBe("5760 s");
    expect(predictedLine({ value: 1650, lo: 1550, hi: 1750, unit: "s" })).toBe("1650 s (1550–1750)");
  });
  test("objective, constraints and requests", () => {
    const v = sample();
    expect(targetLine(v.objective.target)).toBe("Lower time to 80% charge");
    expect(constraintLine({ quantity: "max_temperature", op: "<=", limit: 45, unit: "degC" })).toBe("Max temperature ≤ 45 °C");
    expect(constraintLine(v.objective.constraints[0])).toBe(v.objective.constraints[0].label!);
    const done = v.requests[1];
    expect(paramsLine(done.params)).toBe("C-rate 1 2.5C · Switch SOC 0.5");
    expect(requestTitle(done)).toBe("Simulate protocol: C-rate 1 2.5C · Switch SOC 0.5");
  });
  test("who did it, in the product's words", () => {
    expect(actorWord("person:Ana")).toBe("Ana");
    expect(actorWord("agent:muse-wonder-1.0")).toBe("the agent (muse-wonder-1.0)");
    expect(actorWord("policy:auto/1")).toBe("auto-approved by policy");
    expect(actorWord("system:runner")).toBe("the runner");
    expect(actorWord(null)).toBe("");
  });
  test("requests newest first, and counted by state", () => {
    const v = sample();
    expect(byRecency(v).map((r) => r.state)).toEqual(["completed", "rejected"]);
    expect(stateCounts(v)).toEqual([["completed", 1], ["rejected", 1]]);
  });
  test("spend against budgets", () => {
    expect(spendLines(sample().spend)).toEqual({ model: "$0.31 of $5", experiments: "1 of 4", modelShare: 0.062, experimentShare: 0.25 });
    expect(spendLines({ model_usd: 0, model_budget_usd: 0, experiments_used: 2, experiments_budget: 0 }).experiments).toBe("2");
  });
  test("the limit drawn on a series: same unit, the series named in the constraint", () => {
    const v = sample();
    expect(limitFor(v, "temperature", "degC")).toMatchObject({ value: 45, label: "≤ 45 °C" });
    expect(limitFor(v, "temperature", "degF")).toBeNull();
    expect(limitFor(v, "voltage", "V")).toBeNull();
  });
});

describe("category outputs", () => {
  test("an extrapolated result is flagged; a valid one is not", () => {
    expect(validityWarning({ categories: { status: "ok", validity: "extrapolation", cv_entered: "no" } })).toContain("Extrapolation");
    expect(validityWarning({ categories: { validity: "ok" } })).toBeNull();
    expect(validityWarning({})).toBeNull();
    expect(validityWarning(null)).toBeNull();
    expect(categoriesLine({ categories: { status: "ok", validity: "extrapolation", cv_entered: "no" } })).toBe("Status ok · CV entered no");
  });
  test("inside the model's range is not a warning", () => {
    expect(validityWarning({ categories: { validity: "within_range" } })).toBeNull();
    expect(validityWarning({ categories: { validity: "out_of_bounds" } })).toBe("Validity: out of bounds");
  });
});

// The fast-charge case: a four-step protocol, two limits the baseline breaks, a candidate that meets both at the price
// of a longer charge (loop-compare/2 decides by the limits).
function fastCharge(): { v: LoopView; c: Comparison; specs: SystemSpecLite[] } {
  const v = sample();
  v.campaign.system = { ...v.campaign.system, id: "lgm50" };
  v.objective = {
    version: 1, amendments: [],
    statement: "Charge from 10 to 80 % faster while the cell stays at or below 45 degC (anode >= +10 mV).",
    target: { quantity: "charge_time_80", unit: "min", direction: "minimize", margin: 0, label: "Time from 10 to 80 % SOC" },
    constraints: [
      { quantity: "min_anode_potential", op: ">=", limit: 10, unit: "mV", label: "Plating margin: anode potential at or above +10 mV" },
      { quantity: "max_temperature", op: "<=", limit: 45, unit: "degC", label: "Cell at or below 45 degC" },
    ],
  };
  v.baseline = [{
    basis: "computational", refs: ["chen2020"], fails: [],
    values: { charge_time_80: { value: 28.203, unit: "min", h: 0.05 } },
    constraints: [
      { quantity: "min_anode_potential", value: 7.304, limit: 10, ok: false },
      { quantity: "max_temperature", value: 51.191, limit: 45, ok: false },
    ],
  }];
  const c: Comparison = {
    id: "cmp_fc", request_id: "rq_fc", objective_version: 1, basis: "computational", verdict: "candidate_better", reasons: [],
    target: { baseline: 28.203, candidate: 43.13, delta: -14.927, better: false },
    constraints: [
      { quantity: "min_anode_potential", value: 25.068, limit: 10, ok: true },
      { quantity: "max_temperature", value: 40.654, limit: 45, ok: true },
    ],
  };
  const specs: SystemSpecLite[] = [{
    id: "lgm50",
    quantities: [
      { name: "min_anode_potential", label: "Lowest anode potential vs Li/Li+ (plating onset below 0)" },
      { name: "max_temperature", label: "Peak cell temperature" },
      { name: "plated_lithium", label: "Capacity lost to plated lithium (indicative, unvalidated)" },
    ],
    actions: [{
      name: "simulate_protocol", label: "Simulate a four-step fast charge",
      parameters: [1, 2, 3, 4].map((i) => ({ name: `c_rate_${i}`, label: `Charge C-rate, step ${i} state of charge` }))
        .concat([{ name: "ambient", label: "Ambient temperature (cell starts at ambient)" }]),
    }],
  }];
  return { v, c, specs };
}

describe("the verdict in words", () => {
  test("server text reads with units and symbols", () => {
    expect(prose("ambient 25 degC, c_rate_1 1.3 1/h, limit >= 10; max <= 45 degC")).toBe("ambient 25 °C, c_rate_1 1.3C, limit ≥ 10; max ≤ 45 °C");
    expect(prose("at or below 45 degC.")).toBe("at or below 45 °C.");
    expect(constraintLine({ quantity: "max_temperature", op: "<=", limit: 45, unit: "degC", label: "Cell at or below 45 degC" })).toBe("Cell at or below 45 °C");
  });
  test("labels come from the system spec, then the ids", () => {
    const { v, specs } = fastCharge();
    const w = wordsFor(v, specs);
    expect(w.quantity("charge_time_80")).toBe("Time from 10 to 80 % SOC");
    expect(w.quantity("min_anode_potential")).toBe("Lowest anode potential vs Li/Li+");
    expect(w.note("plated_lithium")).toBe("indicative, unvalidated");
    expect(w.quantity("max_voltage")).toBe("Peak voltage");
    expect(w.action("simulate_protocol")).toBe("Simulate a four-step fast charge");
    expect(idWords("cv_entered")).toBe("CV entered");
  });
  test("stepped parameters read in step order, with C-rates and °C", () => {
    const { v, specs } = fastCharge();
    const params = {
      ambient: { value: 25, unit: "degC" }, c_rate_2: { value: 1.05, unit: "1/h" }, c_rate_1: { value: 1.3, unit: "1/h" },
      c_rate_3: { value: 0.95, unit: "1/h" }, c_rate_4: { value: 0.75, unit: "1/h" },
    };
    expect(paramsLine(params, wordsFor(v, specs))).toBe("Charge C-rate 1.3C → 1.05C → 0.95C → 0.75C · Ambient temperature 25 °C");
    expect(requestTitle({ action: "simulate_protocol", params }, wordsFor(v, specs)))
      .toBe("Simulate a four-step fast charge: Charge C-rate 1.3C → 1.05C → 0.95C → 0.75C · Ambient temperature 25 °C");
    expect(paramsLine(params)).toBe("C-rate 1.3C → 1.05C → 0.95C → 0.75C · Ambient 25 °C");
  });
  test("better by the limits: the longer charge is the price of meeting them, said once, rounded, simulated", () => {
    const { v, c, specs } = fastCharge();
    expect(decidedByLimits(c)).toBe(true);
    const s = verdictSentence(v, c, wordsFor(v, specs));
    expect(s).toBe(
      "In simulation, the candidate’s time from 10 to 80 % SOC is 43.1 min against 28.2 min for the baseline. "
      + "Both limits hold: lowest anode potential vs Li/Li+ 25.1 mV (limit ≥ 10 mV) and peak cell temperature 40.7 °C (limit ≤ 45 °C). "
      + "It counts as better because the baseline breaks both limits; meeting them costs 14.9 min.",
    );
    expect(s).not.toMatch(/degC|1\/h|>=|<=|_/);
  });
  test("a plain improvement, a broken limit and an inconclusive result", () => {
    const { v, c } = fastCharge();
    v.baseline[0].constraints = v.baseline[0].constraints.map((x) => ({ ...x, ok: true }));
    const better: Comparison = { ...c, target: { baseline: 28.203, candidate: 20, delta: 8.2, better: true } };
    expect(decidedByLimits(better)).toBe(false);
    expect(verdictSentence(v, better)).toContain("Better than the baseline by 8.2 min.");
    const broken: Comparison = { ...c, verdict: "violates_constraints", constraints: [{ quantity: "min_anode_potential", value: 3.378, limit: 10, ok: false }, c.constraints[1]] };
    const s = verdictSentence(v, broken);
    expect(s).toContain("A limit is broken: lowest anode potential 3.38 mV (limit ≥ 10 mV).");
    expect(s).toContain("This one holds: peak temperature 40.7 °C (limit ≤ 45 °C).");
    expect(s).toContain("A broken limit rules it out");
    expect(reasonsLine(v, broken)).toBe("breaks lowest anode potential 3.38 mV (limit ≥ 10 mV)");
    expect(verdictSentence(v, { ...c, verdict: "inconclusive" })).toContain("Within the uncertainty it is too close to call");
    expect(verdictSentence(v, { ...c, basis: "physical" })).toMatch(/^On the bench, /);
  });
  test("on screen a number keeps its unit and its relation on one line", () => {
    expect(unbroken("from 10 to 80 % SOC (limit ≥ 10 mV) at 45 °C in 43.1 min")).toBe("from 10 to 80 % SOC (limit ≥ 10 mV) at 45 °C in 43.1 min");
    expect(unbroken("2 steps")).toBe("2 steps");
  });

  test("the words follow the recorded verdict and never re-decide it", () => {
    const { v, c } = fastCharge();
    // The view leaves decided_by out: a better verdict whose target is not better is rule 5, even when the view's
    // baseline shows no broken limit (another basis, another objective version); never "better by" a slower charge.
    v.baseline[0].constraints = [];
    expect(decidedByLimits(c)).toBe(true);
    const s = verdictSentence(v, c);
    expect(s).toContain("It counts as better because the baseline breaks a limit; meeting it costs 14.9 min.");
    expect(s).not.toMatch(/Better than the baseline by/);
    // Feasibility with a faster candidate (better false only for the margin): nothing is a cost.
    const faster: Comparison = { ...c, target: { baseline: 28.203, candidate: 27.5, delta: 0.703, better: false } };
    expect(verdictSentence(v, faster)).not.toMatch(/costs/);
    // A negative margin accepts a slower candidate: said as worse, within what the objective accepts.
    v.objective.target.margin = -2;
    const slack: Comparison = { ...c, target: { baseline: 28.2, candidate: 29.2, delta: -1, better: true } };
    expect(decidedByLimits(slack)).toBe(false);
    expect(verdictSentence(v, slack)).toContain("It counts as better: 1 min worse, within the 2 min the objective accepts.");
    expect(verdictSentence(v, { ...slack, verdict: "not_better", target: { ...slack.target, delta: -5, better: false } }))
      .toContain("Not better than the baseline, even allowing the 2 min the objective accepts.");
    // Not better although the difference clears the margin: the target's own limit was missed.
    v.objective.target.margin = 0;
    v.objective.target.limit = 20;
    const missed: Comparison = { ...c, verdict: "not_better", target: { baseline: 28.2, candidate: 24, delta: 4.2, better: false } };
    expect(verdictSentence(v, missed)).toContain("It misses the target’s own limit of 20 min.");
    expect(verdictSentence(v, missed)).not.toMatch(/Not better than the baseline/);
  });
  test("inconclusive and missing limits say which, without a cause the record does not give", () => {
    const { v, c } = fastCharge();
    const straddle: Comparison = { ...c, verdict: "inconclusive", target: { ...c.target, better: null },
      constraints: [{ quantity: "min_anode_potential", value: 10.2, limit: 10, ok: null }, c.constraints[1]] };
    const s = verdictSentence(v, straddle);
    expect(s).toContain("Too close to the limit to call: lowest anode potential 10.2 mV (limit ≥ 10 mV).");
    expect(s).toContain("decides nothing yet");
    expect(s).not.toMatch(/counts as better|Better than/);
    const lacking: Comparison = { ...c, verdict: "not_comparable", constraints: [{ quantity: "min_anode_potential", value: null, limit: 10, ok: null }] };
    expect(verdictSentence(v, lacking)).toContain("Not measured: lowest anode potential.");
    expect(verdictSentence(v, lacking)).toContain("It can’t be compared with the baseline.");
  });
  test("a value beside its limit keeps its side of the limit when rounded", () => {
    expect(limitValue(9.996, 10, "mV")).toBe("9.996 mV");
    expect(limitValue(10.0004, 10, "mV")).toBe("10.0004 mV");
    expect(limitValue(45.04, 45, "degC")).toBe("45.04 °C");
    expect(limitValue(25.068, 10, "mV")).toBe("25.1 mV");
    expect(limitValue(10, 10, "mV")).toBe("10 mV");
    expect(limitValue(-0.0049, 0, "mV")).toBe("−0.0049 mV");
    expect(limitValue(null, 10, "mV")).toBe("–");
    const { v, c } = fastCharge();
    const edge: Comparison = { ...c, verdict: "violates_constraints", constraints: [{ quantity: "min_anode_potential", value: 9.996, limit: 10, ok: false }] };
    expect(verdictSentence(v, edge)).toContain("lowest anode potential 9.996 mV (limit ≥ 10 mV)");
  });
  test("the planner is named by its kind: a policy is not an agent", () => {
    const { v } = fastCharge();
    v.campaign.planner = { kind: "domain_baseline", models: { planner: "baseline-constrained-bo", vision: null } };
    expect(plannerWord(v)).toBe("constrained Bayesian optimisation");
    expect(plannerIsAgent(v)).toBe(false);
    expect(actorIn(v, "agent:baseline-constrained-bo")).toBe("constrained Bayesian optimisation");
    expect(actorIn(v, "person:Ana")).toBe("Ana");
    v.campaign.planner = { kind: "domain_baseline", models: { planner: "baseline-always-escalate" } };
    expect(plannerWord(v)).toBe("the always escalate baseline policy");
    v.campaign.planner = { kind: "muse", models: { planner: "muse-wonder-1.0" } };
    expect(actorIn(v, "agent:muse-wonder-1.0")).toBe("Muse Wonder");
    expect(plannerIsAgent(v)).toBe(true);
    v.campaign.planner = { kind: "external", models: { planner: "lab-client" } };
    expect(actorIn(v, "agent:lab-client")).toBe("lab-client");
    v.campaign.planner = { kind: "", models: {} };
    expect(actorIn(v, "agent:x")).toBe("the agent (x)");
  });
  test("a server-cut objective ends at a word", () => {
    const cut = "Charge the LG M50 from 10 to 80 % faster than the failing 1.5C four-step protocol while the anode potential stays at or above +10 mV vs Li/L";
    expect(cut.length).toBe(OBJECTIVE_CUT);
    expect(objectiveLine(cut)).toBe("Charge the LG M50 from 10 to 80 % faster than the failing 1.5C four-step protocol while the anode potential stays at or above +10 mV vs…");
    expect(objectiveLine("Charge faster.")).toBe("Charge faster.");
  });
});

describe("live versus recorded", () => {
  test("a recording never offers write controls; live needs an engineer or administrator session", () => {
    expect(canAct("recorded", "engineer")).toBe(false);
    expect(canAct("live", "viewer")).toBe(false);
    expect(canAct("live", null)).toBe(false);
    expect(canAct("live", "engineer")).toBe(true);
    expect(canAct("live", "admin")).toBe(true);
  });
  test("the banner says which, and how a recording was checked", () => {
    const v = sample();
    expect(bannerLine("live", v, { ledger: "acme", secondsSince: 3 })).toEqual({ tag: "LIVE", line: "acme · updated 3 s ago" });
    // a file whose own source says live is still shown as a recording
    expect(bannerLine("recorded", v, { file: "x.loop.json", receipt: false }).tag).toBe("RECORDED");
    expect(bannerLine("recorded", v, { file: "x.loop.json", receipt: false }).line).toContain("no receipt");
    v.source = { kind: "recorded", file: "fc.loop.json", sha256: "s", exported_at: 0, verified: { command: "translucent verify fc.loop.json", summary: "12 checks", ok: true, ran_on: "2026-11-03" } };
    expect(bannerLine("recorded", v, { receipt: true }).line).toBe("fc.loop.json · verified 2026-11-03: 12 checks");
    v.source = { ...v.source, verified: null };
    expect(bannerLine("recorded", v, { receipt: true }).line).toContain("translucent verify fc.loop.json");
  });
});

// ---------------------------------------------------------------- the client's calls, against a stand-in fetch

type Call = { url: string; init: RequestInit };
const realFetch = globalThis.fetch;
function stubFetch(reply: (c: Call) => Response): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = (async (url: string | URL, init?: RequestInit) => {
    const c = { url: String(url), init: init ?? {} };
    calls.push(c);
    return reply(c);
  }) as typeof fetch;
  return calls;
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { globalThis.fetch = realFetch; });

describe("api", () => {
  test("approve sends the content hash and an idempotency key on the session cookie; identity comes from the credential", async () => {
    const calls = stubFetch(() => json({ request_id: "rq_1", version: 1, state: "queued", content_hash: "h" }));
    const hash = "a".repeat(64);
    await v1.approve("acme", "loop_1", "rq_1", { content_hash: hash }, "web:fixed");
    const [c] = calls;
    expect(c.url).toEndWith("/api/v1/ledgers/acme/campaigns/loop_1/requests/rq_1/approve");
    expect(c.init.method).toBe("POST");
    expect(JSON.parse(String(c.init.body))).toEqual({ note: "", content_hash: hash });
    const h = c.init.headers as Record<string, string>;
    expect(h.Authorization).toBeUndefined();
    expect(c.init.credentials).toBe("include");
    expect(h["Idempotency-Key"]).toBe("web:fixed");
  });
  test("a fresh idempotency key fits the server's rule", async () => {
    const calls = stubFetch(() => json({}));
    await v1.reject("acme", "loop_1", "rq_1", { content_hash: "b".repeat(64), reason: "Above 3C" });
    expect((calls[0].init.headers as Record<string, string>)["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/);
  });
  test("a stale hash is a 409 with the server's sentence", async () => {
    stubFetch(() => json({ detail: "request rq_1 is at version 2; review it" }, 409));
    const err = await v1.approve("acme", "loop_1", "rq_1", { content_hash: "c".repeat(64) }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.stale).toBe(true);
    expect(err.message).toBe("request rq_1 is at version 2; review it");
  });
  test("reads carry no key header and name their ledger", async () => {
    const calls = stubFetch((c) => json(c.url.endsWith("/ledgers") ? [] : { items: [], next_cursor: null }));
    await v1.ledgers();
    await v1.campaigns("lab", 7);
    await v1.series("lab", "loop_1", "rq_1", "temperature", 500);
    expect(calls.map((c) => new URL(c.url).pathname + new URL(c.url).search)).toEqual([
      "/api/v1/ledgers", "/api/v1/ledgers/lab/campaigns?cursor=7",
      "/api/v1/ledgers/lab/campaigns/loop_1/series?ref=rq_1&quantity=temperature&max_points=500",
    ]);
    for (const c of calls) expect((c.init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(v1.eventsUrl("lab", "loop_1")).toEndWith("/api/v1/ledgers/lab/campaigns/loop_1/events");
  });
  test("the server's sentence from every error shape", () => {
    expect(detailText({ detail: "Campaign concluded at seq 9" })).toBe("Campaign concluded at seq 9");
    expect(detailText({ detail: { message: "The result was refused", result_rejected: {} } })).toBe("The result was refused");
    expect(detailText({ detail: [{ loc: ["body", "content_hash"], msg: "String should match pattern" }] })).toBe("content_hash: String should match pattern");
    expect(detailText(null)).toBeNull();
  });
  test("a recording loads from /fixtures/loop/, never from the API", async () => {
    const calls = stubFetch(() => json({ loop_export_version: 1, view: SAMPLE, receipt: { campaign_id: "c", ledger_id: "l", ledger: { sha256: "", bytes: 0, data: "" }, blobs: {} } }));
    const r = await loadRecording("fastcharge");
    expect(calls[0].url).toBe("/fixtures/loop/fastcharge.loop.json");
    expect(r.receipt).toBe(true);
    stubFetch(() => new Response("", { status: 404 }));
    expect(await loadRecording("missing").catch((e) => e.message)).toBe("No recording named missing.");
  });
});

// A recorded lab smoke campaign (tests/data/loop_lab_smoke.loop.json): hidden lab condition h=15, v0 nominal, v1
// fitted; its first design was made under v0 and carries the model as a parameter, its best under v1.
const LAB = parseLoopFile(JSON.parse(readFileSync(join(import.meta.dir, "..", "..", "tests", "data", "loop_lab_smoke.loop.json"), "utf-8"))).view;
// The published lab recording (public/fixtures/loop/fastcharge-lab.loop.json), an evaluation campaign.
const PUBLISHED_LAB = parseLoopFile(JSON.parse(readFileSync(join(import.meta.dir, "..", "public", "fixtures", "loop", "fastcharge-lab.loop.json"), "utf-8"))).view;
const lab = () => structuredClone(LAB.learning!) as Learning;

describe("thermal learning", () => {
  test("older recordings carry no learning; the lab recordings do", () => {
    expect(SAMPLE.learning).toBeUndefined();
    expect(LAB.learning?.kind).toBe("thermal-learning/1");
    expect(PUBLISHED_LAB.learning?.kind).toBe("thermal-learning/1");
    expect(PUBLISHED_LAB.learning?.best_design?.model_version).toBe(1);
    expect(PUBLISHED_LAB.requests.every((r) => !requestTitle(r).includes("{"))).toBe(true);
    expect(FASTCHARGE_LAB_HREF).toBe("/w/?recording=fastcharge-lab");
    expect(RECORDING_NAME.test("fastcharge-lab")).toBe(true);
  });
  test("C-rates read as one line, a missing step as a dash", () => {
    expect(cRatesLine([1.5, 1.35, 0.9, 0.5])).toBe("1.5 · 1.35 · 0.9 · 0.5C");
    expect(cRatesLine([1.5, null, 1.05, 0.8])).toBe("1.5 · – · 1.05 · 0.8C");
    expect(cRatesLine(null)).toBe("–");
    expect(cRatesLine([])).toBe("–");
  });
  test("fitted parameters keep four significant digits and their 95 % region", () => {
    expect(fitValue(14.967)).toBe("14.97");
    expect(fitValue(6)).toBe("6");
    expect(fitValue(null)).toBe("–");
    expect(paramLine("h", 14.967, [14.5, 15.4])).toBe("Heat transfer h 14.97 W/m²/K (95 %: 14.5–15.4)");
    expect(paramLine("k", 1)).toBe("Thermal-mass scale k 1");
  });
  test("the anode margin reads above or below the limit", () => {
    expect(marginLine({ min_anode_potential: 26.284, limits: { margin_mV: 25 } })).toBe("26.3 mV, 1.28 mV above the 25 mV margin");
    expect(marginLine({ min_anode_potential: 20, limits: { margin_mV: 25 } })).toBe("20 mV, 5 mV below the 25 mV margin");
    expect(marginLine({ min_anode_potential: null })).toBe("–");
  });
  test("versions and lab tests are found by number", () => {
    const l = lab();
    expect(versionOf(l, l.model_in_use)?.estimate?.h).toBe(14.967);
    expect(versionOf(l, 7)).toBeUndefined();
    expect(labTestOf(l, l.latest_lab_test)?.n).toBe(2);
    expect(labTestOf(l, 1)?.forecast?.model_version).toBe(0);
  });
  test("next experiment: a pending request first, else the ranking says none is worth its cost", () => {
    const l = lab();
    const none = nextStepOf(l);
    expect(none.kind).toBe("none");
    if (none.kind === "none") expect(none.ranking.verdict).toContain("no test would change the design");
    l.ranking!.best_measurement = 3;
    const run = nextStepOf(l);
    expect(run.kind === "run" && run.option?.label).toBe("1C constant current, then a 60 min rest");
    // No best named: the options' values decide.
    l.ranking!.best_measurement = null;
    expect(nextStepOf(l).kind).toBe("none");
    l.ranking!.options![1].value_min = 2.5;
    const byValue = nextStepOf(l);
    expect(byValue.kind === "run" && byValue.option?.option).toBe(2);
    l.next_experiment = { request_id: "rq_x", action: "lab_charge", state: "awaiting_approval" };
    expect(nextStepOf(l).kind).toBe("pending");
    expect(nextStepOf({}).kind).toBe("unranked");
  });
  test("an object or a long string parameter is never printed inline", () => {
    expect(paramText({ value: 25, unit: "degC" })).toBe("25 °C");
    expect(paramText({ value: "nominal", unit: "" })).toBe("nominal");
    expect(paramText({ value: { h: 6, k: 1 } as unknown as string, unit: "" })).toBe(PARAM_RECORDED);
    expect(paramText({ value: "x".repeat(41), unit: "" })).toBe(PARAM_RECORDED);
    expect(paramText({ value: "x".repeat(40), unit: "" })).toBe("x".repeat(40));
    expect(paramText({ value: '{"h":6}', unit: "" })).toBe(PARAM_RECORDED);
    // The lab recording's first design carries its whole model as a parameter.
    const first = LAB.requests[0];
    const line = paramsLine(first.params);
    expect(line).not.toContain(PARAM_RECORDED);   // left to the request's table, not the one-line summary
    expect(line).not.toContain("{");
    expect(LAB.requests.every((r) => !requestTitle(r).includes("{"))).toBe(true);
  });
  test("the assessment's best is flagged when made under an older model version than the best design", () => {
    const first = LAB.learning!.first_design!.request_id!, best = LAB.learning!.best_design!.request_id!;
    expect(bestIsOlderModel(LAB, first)).toBe(true);
    expect(bestIsOlderModel(LAB, best)).toBe(false);
    expect(bestIsOlderModel(LAB, null)).toBe(false);
    expect(bestIsOlderModel({ ...LAB, learning: undefined }, first)).toBe(false);
    expect(bestIsOlderModel(SAMPLE, SAMPLE.requests[0].request_id)).toBe(false);
  });
  test("a design without a model standing behind it is a lead, never the best design", () => {
    const base = { model_version: 2, c_rates: [1.5, 1.4, 1.1, 0.8] };
    const lead = { ...base, model_adequate: true, model_supported: false, standing: "model version 2 has not been checked on tests it did not fit" };
    expect(designCaveat(lead)).toBe("Not a recommendation: model version 2 has not been checked on tests it did not fit. Treat it as a lead, not a result.");
    expect(designHeading(lead)).toBe("Latest design, not a recommendation");
    const backed = { ...base, model_adequate: true, model_supported: true, standing: "met the rule" };
    expect(designCaveat(backed)).toBeNull();
    expect(designHeading(backed)).toBe("Best design");
    // a recording made before the rule: only an inadequate fit is flagged, in the fit's own terms
    expect(designCaveat({ ...base, model_adequate: false })).toContain("does not fit its own tests within the noise");
    expect(designCaveat({ ...base, model_adequate: null })).toBeNull();
    expect(designHeading(LAB.learning!.best_design!)).toBe("Best design");
  });
  test("a later design without standing never hides an earlier recommendation", () => {
    const A = { request_id: "rq_a", model_version: 1, c_rates: [1.5, 1.35, 0.9, 0.5], model_supported: true, standing: "met" };
    const B = { request_id: "rq_b", model_version: 2, c_rates: [1.5, 1.5, 1.2, 0.8], model_supported: false, standing: "model version 2 has not been checked on tests it did not fit" };
    expect(designShown({ best_design: B, recommended_design: A })).toEqual({ main: A, lead: B });
    expect(leadLine(B)).toBe("A later design under v2 (1.5 · 1.5 · 1.2 · 0.8C) is a lead, not a recommendation: model version 2 has not been checked on tests it did not fit.");
    expect(designShown({ best_design: B, recommended_design: null })).toEqual({ main: B, lead: null });
    expect(designShown({ best_design: A, recommended_design: A })).toEqual({ main: A, lead: null });
    // a recording made before the rule: its best design, as before
    expect(designShown(LAB.learning)).toEqual({ main: LAB.learning!.best_design!, lead: null });
    expect(designShown(undefined)).toEqual({ main: null, lead: null });
  });
  test("the chart data: seconds, observed and forecast, the members' band", () => {
    const c = traceChart(lab().lab_tests![0].trace)!;
    expect(c.observed.t[1]).toBe(60);
    expect(c.observed.y[0]).toBe(24.76);
    expect(c.observed.unit).toBe("degC");
    expect(c.forecast!.y[0]).toBe(25);
    expect(c.band.length).toBe(1);
    expect(c.spread).toBe(0);   // v0 has one member: the band is the forecast line
  });
  test("nulls split the band and drop from the lines; no trace, no chart", () => {
    const c = traceChart({
      t_min: [0, 1, 2, 3, 4], observed_degC: [25, 26, null, 28, 29],
      forecast_degC: [25, 26.5, 27.5, null, null], forecast_lo_degC: [24, 25, null, 27, 28], forecast_hi_degC: [26, 27, 28, 30, 31],
    })!;
    expect(c.observed.t).toEqual([0, 60, 180, 240]);
    expect(c.forecast!.t).toEqual([0, 60, 120]);
    expect(c.band.map((b) => b.t)).toEqual([[0, 60], [180, 240]]);
    expect(c.spread).toBe(3);
    expect(traceChart({ t_min: [0, 1], observed_degC: [25, 26], forecast_degC: null, forecast_lo_degC: null, forecast_hi_degC: null })!.forecast).toBeNull();
    expect(traceChart(null)).toBeNull();
    expect(traceChart({ t_min: [0], observed_degC: [null] })).toBeNull();
  });
});

describe("investigation from the engineer's records", () => {
  const stretch = {
    ref: "lgm50-cells/cell02", segment: 27, mode: "discharge", t_start_s: 96190.21, t_end_s: 103280.74, test_current: 2.50004,
    test_c_rate: 0.5, test_duration: 118.175, temperature_start: 26.8489, temperature_rise: 7.5637, ambient_temperature: 26.8791, current_departure: 0.00024,
  };
  test("a lab test with a record is a record test; a lab charge is not", () => {
    expect(isRecordTest({ record: stretch })).toBe(true);
    expect(isRecordTest({})).toBe(false);
    expect(isRecordTest({ record: null })).toBe(false);
  });
  test("a record test reads as its stretch: ref, segment, mode, C-rate, duration, start temperature and rise", () => {
    expect(recordStretchLine(stretch)).toBe("lgm50-cells/cell02 segment 27 · discharge at 0.5C for 118 min · from 26.8 °C, +7.56 K");
    expect(recordStretchLine({ ref: "r", segment: 3, mode: "charge", temperature_rise: -0.5 })).toBe("r segment 3 · charge · from –, −0.5 K");
    expect(recordStretchLine({})).toBe("record");
  });
  test("sizes in binary units and spans in the unit that reads best", () => {
    expect(bytesWord(312)).toBe("312 B");
    expect(bytesWord(597715)).toBe("584 KiB");
    expect(bytesWord(64 * 1024 * 1024)).toBe("64 MiB");
    expect(bytesWord(undefined)).toBe("–");
    expect(spanWord([0, 173454.11])).toBe("0–48.2 h");
    expect(spanWord([0, 1800])).toBe("0–30 min");
    expect(spanWord([10, 70])).toBe("10–70 s");
    expect(spanWord([0])).toBe("–");
    expect(spanWord(null)).toBe("–");
  });
  test("an uncertainty the manifest did not declare reads as not declared, never as a number", () => {
    expect(uncertaintyWord({ value: null, kind: "none", method: "not declared in the manifest" }, "degC")).toBe("not declared");
    expect(uncertaintyWord(null)).toBe("not declared");
    expect(uncertaintyWord({ value: 0.5, kind: "standard" }, "degC")).toBe("± 0.5 °C (standard)");
  });
  test("the prior's parameters, without its version or its words", () => {
    expect(priorLines({ version: 0, h: 10, k: 1, source: "as shipped" })).toEqual(["Heat transfer h 10 W/m²/K", "Thermal-mass scale k 1"]);
    expect(priorLines(null)).toEqual([]);
  });
  test("the engineer's constraints: the objective's, which the server points to, or the investigation's own list", () => {
    const v = sample();
    v.investigation = { engineer_constraints: "objective.constraints (only an engineer's amendment changes them)" };
    expect(engineerConstraints(v)).toBe(v.objective.constraints);
    const own = [{ quantity: "max_temperature", op: "<=", limit: 45, unit: "degC" }];
    v.investigation = { engineer_constraints: own };
    expect(engineerConstraints(v)).toBe(own);
    delete v.investigation;
    expect(engineerConstraints(v)).toBe(v.objective.constraints);
  });
});
