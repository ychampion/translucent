import { afterEach, describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";
import { ApiError, detailText, loadRecording, v1 } from "./api";
import {
  actorWord, artifactIdOf, bannerLine, baselineFor, bestComparison, byRecency, canAct, change, constraintLine, limitFor,
  num, outcomeOfVerdict, paramsLine, parseLoopFile, predictedLine, RECORDING_NAME, recordingUrl, requestTitle,
  categoriesLine, seriesFromBlob, spendLines, validityWarning, valueLine, standing, stateCounts, targetLine, thin, withUnit,
  type Comparison, type LoopView,
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
    expect(paramsLine(done.params)).toBe("c_rate_1 2.5C · switch_soc 0.5");
    expect(requestTitle(done)).toBe("Simulate protocol: c_rate_1 2.5C · switch_soc 0.5");
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
    expect(limitFor(v, "temperature", "degC")).toEqual({ value: 45, label: "≤ 45 °C" });
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
    expect(categoriesLine({ categories: { status: "ok", validity: "extrapolation", cv_entered: "no" } })).toBe("Status ok · Cv entered no");
  });
});

describe("live versus recorded", () => {
  test("a recording never offers write controls; live needs a name and the team key", () => {
    expect(canAct("recorded", "team-key", "Ana")).toBe(false);
    expect(canAct("live", "", "Ana")).toBe(false);
    expect(canAct("live", "team-key", " ")).toBe(false);
    expect(canAct("live", "team-key", "Ana")).toBe(true);
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
  test("approve sends the content hash, the author, the team key and an idempotency key", async () => {
    const calls = stubFetch(() => json({ request_id: "rq_1", version: 1, state: "queued", content_hash: "h" }));
    const hash = "a".repeat(64);
    await v1.approve("acme", "loop_1", "rq_1", { author: "Ana", content_hash: hash }, "team-key", "web:fixed");
    const [c] = calls;
    expect(c.url).toEndWith("/api/v1/ledgers/acme/campaigns/loop_1/requests/rq_1/approve");
    expect(c.init.method).toBe("POST");
    expect(JSON.parse(String(c.init.body))).toEqual({ note: "", author: "Ana", content_hash: hash });
    const h = c.init.headers as Record<string, string>;
    expect(h.Authorization).toBe("Bearer team-key");
    expect(h["Idempotency-Key"]).toBe("web:fixed");
  });
  test("a fresh idempotency key fits the server's rule", async () => {
    const calls = stubFetch(() => json({}));
    await v1.reject("acme", "loop_1", "rq_1", { author: "Ana", content_hash: "b".repeat(64), reason: "Above 3C" }, "k");
    expect((calls[0].init.headers as Record<string, string>)["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/);
  });
  test("a stale hash is a 409 with the server's sentence", async () => {
    stubFetch(() => json({ detail: "request rq_1 is at version 2; review it" }, 409));
    const err = await v1.approve("acme", "loop_1", "rq_1", { author: "Ana", content_hash: "c".repeat(64) }, "k").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.stale).toBe(true);
    expect(err.message).toBe("request rq_1 is at version 2; review it");
  });
  test("reads need no key and name their ledger", async () => {
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
