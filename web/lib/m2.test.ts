// M2 web: the session-cookie client, the CSRF header, sign-in on 401, the legacy assessment fallback, the objective
// summary rule, planner readiness gating, the New campaign body, and the event-stream state.
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ApiError, CLIENT_HEADER, sessionApi, v1 } from "./api";
import { assessmentOf, feasibilityWords, legacyAssessment, objectiveHeader, objectiveWords, requestRole, seriesLimits, speedWords, type Assessment } from "./assessment";
import { OUTCOME_WORD, parseLoopFile, summaryOutcome, type LoopView, type SummaryFindings } from "./loop";
import {
  defaultPlanner, draftFor, draftProblems, FALLBACK_PLANNERS, FASTEST_STATEMENT, openBody, plannerGate, relabel, templateOf,
  fileNameProblem, newUploadId, RECORDS_SYSTEM, recordsProblems,
  type DatasetDetail, type Draft, type PlannerInfo, type SystemSpecFull,
} from "./newcampaign";
import { canWrite, session } from "./session";
import { nextStream } from "./stream";

// ---------------------------------------------------------------- fixtures

const FASTCHARGE = parseLoopFile(JSON.parse(readFileSync(join(import.meta.dir, "..", "public", "fixtures", "loop", "fastcharge.loop.json"), "utf-8"))).view;
const fc = () => structuredClone(FASTCHARGE) as LoopView;

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
const headers = (c: Call) => (c.init.headers ?? {}) as Record<string, string>;
beforeEach(() => session.reset());
afterEach(() => { globalThis.fetch = realFetch; });

// ---------------------------------------------------------------- the client: cookie, CSRF, no key, no author

describe("api client: cookie session and CSRF", () => {
  test("every read rides the cookie and carries no key and no CSRF header", async () => {
    const calls = stubFetch((c) => json(c.url.endsWith("/ledgers") ? [] : {}));
    await v1.ledgers();
    await v1.view("lab", "loop_1");
    await v1.planners("lab");
    for (const c of calls) {
      expect(c.init.credentials).toBe("include");
      expect(headers(c).Authorization).toBeUndefined();
      expect(headers(c)["X-Translucent-Client"]).toBeUndefined();
    }
    expect(new URL(calls[2].url).pathname).toBe("/api/v1/ledgers/lab/planners");
  });
  test("every write adds X-Translucent-Client: web and an idempotency key, rides the cookie, and names no author", async () => {
    const calls = stubFetch(() => json({ request_id: "rq_1", version: 2, state: "queued", content_hash: "h" }));
    const hash = "a".repeat(64);
    await v1.approve("lab", "loop_1", "rq_1", { content_hash: hash }, "web:fixed");
    await v1.reject("lab", "loop_1", "rq_1", { content_hash: hash, reason: "Too hot" });
    await v1.amend("lab", "loop_1", { reason: "Safer margin", constraints: [] });
    await v1.conclude("lab", "loop_1", { text: "Done" });
    await v1.open("lab", { title: "T", objective: {} });
    expect(CLIENT_HEADER).toEqual({ "X-Translucent-Client": "web" });
    for (const c of calls) {
      expect(c.init.method).toBe("POST");
      expect(c.init.credentials).toBe("include");
      expect(headers(c)["X-Translucent-Client"]).toBe("web");
      expect(headers(c)["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/);
      expect(headers(c).Authorization).toBeUndefined();
      const body = JSON.parse(String(c.init.body));
      expect(body.author).toBeUndefined();
      expect(body.agent).toBeUndefined();
    }
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ note: "", content_hash: hash });
    expect(headers(calls[0])["Idempotency-Key"]).toBe("web:fixed");
    expect(new URL(calls[2].url).pathname).toBe("/api/v1/ledgers/lab/campaigns/loop_1/amendments");
    expect(new URL(calls[3].url).pathname).toBe("/api/v1/ledgers/lab/campaigns/loop_1/conclusion");
  });
  test("sign-in sends the key once, in the body, to POST /api/v1/session; sign-out posts /session/end", async () => {
    const who = { name: "Ana", role: "engineer", kind: "person", ledgers: "*" };
    const calls = stubFetch((c) => (c.url.endsWith("/session/end") ? new Response(null, { status: 204 }) : json(who)));
    expect(await sessionApi.signIn("  k3y  ".trim())).toEqual(who as never);
    expect(new URL(calls[0].url).pathname).toBe("/api/v1/session");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ key: "k3y" });
    expect(headers(calls[0])["X-Translucent-Client"]).toBe("web");
    expect(calls[0].init.credentials).toBe("include");
    await sessionApi.signOut();
    expect(new URL(calls[1].url).pathname).toBe("/api/v1/session/end");
  });
  test("a 401 anywhere opens sign-in and keeps the error; the session probe's 401 does not", async () => {
    stubFetch((c) => json({ detail: "Sign in" }, 401));
    expect(await sessionApi.current()).toBeNull();
    expect(session.get().prompt).toBeNull();
    const err = await v1.view("lab", "loop_1").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(session.get().prompt).not.toBeNull();
    expect(session.get().status).toBe("out");
  });
  test("signing in clears the prompt and bumps the epoch pages reload on", () => {
    session.expired();
    const before = session.get().epoch;
    session.signedIn({ name: "Ana", role: "engineer", kind: "person", ledgers: "*" });
    expect(session.get().prompt).toBeNull();
    expect(session.get().epoch).toBe(before + 1);
  });
  test("403, 409 and 422 keep the server's sentence; 422 splits its reasons; unreachable is status 0", async () => {
    stubFetch(() => json({ detail: "This key acts as Ana" }, 403));
    expect((await v1.amend("l", "c", { reason: "r" }).catch((e) => e)).message).toBe("This key acts as Ana");
    stubFetch(() => json({ detail: "request rq_1 is at version 2; review it" }, 409));
    expect((await v1.approve("l", "c", "rq_1", { content_hash: "b".repeat(64) }).catch((e) => e)).stale).toBe(true);
    stubFetch(() => json({ detail: "model budget above the cap; experiments must be at least 1" }, 422));
    expect((await v1.open("l", { title: "t", objective: {} }).catch((e) => e)).reasons).toEqual(["model budget above the cap", "experiments must be at least 1"]);
    globalThis.fetch = (async () => { throw new TypeError("fetch failed"); }) as unknown as typeof fetch;
    const e = await v1.ledgers().catch((x) => x);
    expect(e.unreachable).toBe(true);
  });
  test("viewers and planners cannot write; engineers and administrators can", () => {
    const who = (role: string) => ({ name: "x", role, kind: "person", ledgers: "*" }) as never;
    expect(canWrite(who("viewer"))).toBe(false);
    expect(canWrite(who("planner"))).toBe(false);
    expect(canWrite(null)).toBe(false);
    expect(canWrite(who("engineer"))).toBe(true);
    expect(canWrite(who("admin"))).toBe(true);
  });
});

// ---------------------------------------------------------------- the legacy assessment fallback

describe("assessment: a recording without one", () => {
  test("the fast-charge recording (loop-compare/2, decided by feasibility) reads as feasibility recovered, speed not compared", () => {
    const v = fc();
    expect(v.assessment).toBeUndefined();
    const { a, derived } = assessmentOf(v);
    expect(derived).toBe(true);
    expect(a.basis).toBe("computational");
    expect(a.feasibility.baseline).toBe("fails");
    expect(a.feasibility.best).toBe("meets");
    expect(a.feasibility.recovered).toBe(true);
    expect(a.feasibility.best_request_id).toBe("rq_77f33c2c47352e45d9a2");   // 37.275 min, the fastest feasible
    expect(a.speed.status).toBe("not_compared");
    expect(a.speed.reference).toBeNull();
    expect(a.objective.status).toBe("open");
    const words = [a.headline, feasibilityWords(a).answer, speedWords(a).answer, speedWords(a).detail].join(" ").toLowerCase();
    expect(words).not.toMatch(/improv|faster than|optim/);
    expect(speedWords(a).answer).toBe("Not compared");
  });
  test("a baseline that meets the limits is a valid reference: speed is compared", () => {
    const v = fc();
    for (const b of v.baseline) { b.constraints = b.constraints.map((c) => ({ ...c, ok: true })); b.fails = []; }
    const a = legacyAssessment(v);
    expect(a.feasibility.baseline).toBe("meets");
    expect(a.speed.status).toBe("not_faster");          // 37.3 min against a 28.2 min baseline
    expect(a.speed.reference?.source).toBe("baseline");
    expect(a.speed.delta).toBeLessThan(0);
  });
  test("no feasible candidate: best fails, not recovered", () => {
    const v = fc();
    v.comparisons = v.comparisons.map((c) => ({ ...c, constraints: c.constraints.map((x, i) => (i === 0 ? { ...x, ok: false } : x)) }));
    const a = legacyAssessment(v);
    expect(a.feasibility.best).toBe("fails");
    expect(a.feasibility.recovered).toBe(false);
    expect(a.feasibility.best_request_id).toBeNull();
  });
  test("a hard target is met or not met, never open", () => {
    const v = fc();
    v.objective.target.limit = 40;
    expect(legacyAssessment(v).objective.status).toBe("met");
    v.objective.target.limit = 30;
    expect(legacyAssessment(v).objective.status).toBe("not_met");
    expect(objectiveWords(legacyAssessment(v), v).answer).toBe("Not met");
  });
  test("the baseline re-run is not an experiment", () => {
    const v = fc();
    expect(requestRole(v.requests[0])).toBe("baseline");
    expect(requestRole(v.requests[1])).toBe("experiment");
    expect(requestRole({ role: "reference", proposed_by: "system:reference" })).toBe("reference");
  });
  test("a server assessment is used as it is", () => {
    const v = fc();
    const server: Assessment = { ...legacyAssessment(v), headline: "From the server" };
    v.assessment = server;
    expect(assessmentOf(v)).toEqual({ a: server, derived: false });
  });
  test("a speed answer against the reference search says so, and never 'optimum'", () => {
    const a = legacyAssessment(fc());
    a.speed = { reference: { label: "Constant current 1.1C", source: "reference_search", value: 40, unit: "min", objective_version: 2, request_id: "rq_ref" },
      best: { request_id: "rq_77f33c2c47352e45d9a2", value: 37.275, unit: "min" }, delta: 2.725, faster: true, status: "faster", reason: "Same constraints (v2)." };
    const w = speedWords(a);
    expect(w.answer).toBe("Faster by 2.73 min");
    expect(w.detail).toContain("best found by the reference search");
    expect(w.detail.toLowerCase()).not.toContain("optim");
  });
  test("the browser demonstration (loop-compare/3) keeps its server assessment: recovered, faster than a same-limits reference, open", () => {
    const demo = parseLoopFile(JSON.parse(readFileSync(join(import.meta.dir, "..", "public", "fixtures", "loop", "fastcharge-demo.loop.json"), "utf-8"))).view;
    const { a, derived } = assessmentOf(demo);
    expect(derived).toBe(false);
    expect(demo.objective.version).toBe(2);
    expect(a.feasibility.recovered).toBe(true);
    expect(a.speed.status).toBe("faster");
    expect(a.speed.reference?.source).toBe("reference_search");
    expect(a.speed.reference?.objective_version).toBe(2);     // the reference was found again under the amended limits
    expect(a.objective.status).toBe("open");
    expect(demo.conclusion?.outcome).toBe("faster_than_reference_in_simulation");
  });
  test("/3 outcomes have words, with and without the simulation suffix", () => {
    expect(OUTCOME_WORD.feasible_only_in_simulation).toBe("Feasible only in simulation");
    expect(OUTCOME_WORD.faster_than_reference).toBe("Faster than the reference");
    expect(OUTCOME_WORD.improved_in_simulation).toBe("Improved in simulation");   // old values stay valid
  });
});

// ---------------------------------------------------------------- the objective summary rule

describe("objective summary display", () => {
  test("the server's summary leads; the registered statement shows only when it differs, as 'as registered, vN'", () => {
    const v = fc();
    v.objective.summary = "Fastest 10-80 % charge with anode potential >= +25 mV and peak temperature <= 45 degC";
    v.objective.statement_version = 1;
    const h = objectiveHeader(v);
    expect(h.summary).toBe(v.objective.summary);
    expect(h.registered).toEqual({ text: v.objective.statement, version: 1 });
    v.objective.statement = v.objective.summary;
    expect(objectiveHeader(v).registered).toBeNull();
  });
  test("an old recording's header is built from the CURRENT constraints, never the stale +10 mV statement", () => {
    const v = fc();
    const h = objectiveHeader(v);
    expect(h.summary).toContain("25 mV");
    expect(h.summary).not.toContain("10 mV");
    expect(h.registered?.text).toContain("+10 mV");
    expect(h.registered?.version).toBe(1);
  });
});

// ---------------------------------------------------------------- planner readiness gating

const BO: PlannerInfo = { id: "bo", kind: "domain_baseline", label: "Bayesian optimisation (no model)", paid: false, ready: true, reason: null };
const MUSE_OFF: PlannerInfo = { id: "muse", kind: "muse", label: "Muse Wonder", model: "muse-wonder-1.0", profile: "meta", paid: true, ready: false,
  reason: "live mode off", cap_usd: 145, spent_usd: 12, remaining_usd: 133, default_budget_usd: 1.5 };
const MUSE_ON: PlannerInfo = { ...MUSE_OFF, ready: true, reason: null };

describe("planner readiness", () => {
  test("Bayesian optimisation is the default; a paid model never is, even when ready", () => {
    expect(defaultPlanner([MUSE_ON, BO])?.id).toBe("bo");
    expect(defaultPlanner([MUSE_ON])).toBeNull();
    expect(defaultPlanner(FALLBACK_PLANNERS)?.id).toBe("bo");
  });
  test("a not-ready planner cannot launch and says the server's reason; nothing replaces it", () => {
    expect(plannerGate(MUSE_OFF)).toEqual({ ok: false, reason: "live mode off" });
    expect(plannerGate({ ...MUSE_OFF, reason: null }).reason).toContain("not ready");
    expect(plannerGate(MUSE_ON).ok).toBe(true);
    expect(plannerGate(null).ok).toBe(false);
    const d = { ...draft(), planner: "muse", modelUsd: "1.5" };
    expect(draftProblems(d, MUSE_OFF, { canWrite: true })).toContain("live mode off");
  });
  test("a paid planner needs a budget within what remains under the cap", () => {
    expect(draftProblems({ ...draft(), modelUsd: "" }, MUSE_ON, { canWrite: true }).join(" ")).toContain("model budget");
    expect(draftProblems({ ...draft(), modelUsd: "200" }, MUSE_ON, { canWrite: true }).join(" ")).toContain("remains under the cap");
    expect(draftProblems({ ...draft(), modelUsd: "1.5" }, MUSE_ON, { canWrite: true })).toEqual([]);
  });
  test("a viewer can read the form but not launch it", () => {
    expect(draftProblems(draft(), BO, { canWrite: false, role: "viewer" })[0]).toContain("Viewers");
  });
});

// ---------------------------------------------------------------- the New campaign body

const SPEC: SystemSpecFull = {
  id: "pybamm-fastcharge", title: "LG M50 fast-charge protocol", solver: { name: "PyBaMM", version: "26" },
  quantities: [
    { name: "charge_time_80", unit: "min", kind: "scalar", label: "Time from 10 to 80 % SOC" },
    { name: "min_anode_potential", unit: "mV", kind: "scalar", label: "Lowest anode potential vs Li/Li+ at the separator (plating onset below 0)" },
    { name: "max_temperature", unit: "degC", kind: "scalar", label: "Peak lumped cell temperature" },
    { name: "voltage", unit: "V", kind: "series" },
  ],
  actions: [],
  objective_template: {
    target: { quantity: "charge_time_80", unit: "min", direction: "minimize", label: "Time from 10 to 80 % SOC" },
    constraints: [
      { quantity: "min_anode_potential", op: ">=", limit: 10, unit: "mV", label: "Plating margin: anode potential at or above +10 mV" },
      { quantity: "max_temperature", op: "<=", limit: 45, unit: "degC", label: "Cell at or below 45 degC" },
    ],
    hard_target: { quantity: "charge_time_80", unit: "min" },
    budget: { experiments: 8 },
  },
};
const DS: DatasetDetail = { id: "chen2020-lgm50", title: "LG M50", system: "pybamm-fastcharge", records: [
  { id: "cell02", role: "history", channels: [{ quantity: "voltage", unit: "V" }] },
  { id: "failing-baseline-1p5c", role: "baseline", channels: [] },
] };
function draft(): Draft {
  return {
    title: "Faster charge", system: "", dataset: "", baseline: "", baselineSimulation: true, target: { quantity: "", unit: "", direction: "minimize", label: "" },
    mode: "fastest_feasible", hardLimit: "", constraints: [], planner: "bo", experiments: "8", modelUsd: "", autoApprove: false,
    autoMaxWall: "300", autoMaxCount: "8", memory: "on", ...draftFor(SPEC, DS),
  };
}

describe("new campaign", () => {
  test("the form is filled from the system's objective template, with units", () => {
    const d = draft();
    expect(d.target).toEqual({ quantity: "charge_time_80", unit: "min", direction: "minimize", label: "Time from 10 to 80 % SOC" });
    expect(d.constraints.map((c) => [c.quantity, c.op, c.limit, c.unit])).toEqual([["min_anode_potential", ">=", "10", "mV"], ["max_temperature", "<=", "45", "degC"]]);
    expect(d.baseline).toBe("failing-baseline-1p5c");
    expect(d.baselineSimulation).toBe(true);
    expect(templateOf({ ...SPEC, objective_template: undefined })).toBeNull();
  });
  test("the default objective is the fastest feasible protocol; no author; the kind goes to a template-serving server", () => {
    const b = openBody(draft(), SPEC, BO);
    const o = b.objective as Record<string, any>;
    expect(o.statement).toBe(FASTEST_STATEMENT);
    expect(o.kind).toBe("fastest_feasible");
    expect(o.target.limit).toBeUndefined();
    expect(o.registered_by).toBeUndefined();
    expect(JSON.stringify(b)).not.toContain("author");
    expect(o.planner).toEqual({ kind: "domain_baseline", profile: null, client: null });
    expect(o.budget).toEqual({ model_usd: 0, experiments: 8, experiment_usd: 0 });
    expect(o.baseline).toEqual(["chen2020-lgm50/failing-baseline-1p5c"]);
    expect(o.auto_approve).toEqual([]);
    expect(o.quantities.map((q: { name: string }) => q.name)).toEqual(["charge_time_80", "min_anode_potential", "max_temperature"]);
  });
  test("an older server (no template) is not sent a kind it would refuse", () => {
    const o = openBody(draft(), { ...SPEC, objective_template: undefined }, BO).objective;
    expect("kind" in o).toBe(false);
  });
  test("a hard target is explicit: kind hard_target, the target's limit, and a 'Meet …' statement", () => {
    const o = openBody({ ...draft(), mode: "hard_target", hardLimit: "30" }, SPEC, BO).objective as Record<string, any>;
    expect(o.kind).toBe("hard_target");
    expect(o.target.limit).toBe(30);
    expect(o.statement).toMatch(/^Meet time from 10 to 80 % SOC at or below 30 min while meeting these constraints\.$/);
    expect(draftProblems({ ...draft(), mode: "hard_target", hardLimit: "" }, BO, { canWrite: true }).join(" ")).toContain("hard target");
  });
  test("the server's template shape: a statement per kind, the hard target's own words, its quantities and margin", () => {
    const real: SystemSpecFull = { ...SPEC, objective_template: {
      evaluator: "loop-compare/3", kinds: ["fastest_feasible", "hard_target"],
      statement: { fastest_feasible: "Find the fastest protocol meeting these constraints.", hard_target: "Meet {target} while meeting these constraints." },
      target: { quantity: "charge_time_80", unit: "min", direction: "minimize", margin: 0.0 },
      hard_target: { quantity: "charge_time_80", unit: "min", op: "<=", label: "Time from 10 to 80 % SOC at most", words: "a 10-80 % charge in at most {limit:g} {unit}" },
      constraints: (SPEC.objective_template as { constraints: unknown[] }).constraints,
      quantities: [{ name: "charge_time_80", unit: "min", source: "result" }, { name: "min_anode_potential", unit: "mV", source: "result" }, { name: "max_temperature", unit: "degC", source: "result" }],
      model_uncertainty: "Not quantified: confirm on a physical cell.",
    } };
    const d = { ...draft(), ...draftFor(real, DS) };
    expect(d.target.label).toBe("Time from 10 to 80 % SOC");            // from the spec's quantity label
    const fast = openBody(d, real, BO).objective as Record<string, any>;
    expect(fast.statement).toBe("Find the fastest protocol meeting these constraints.");
    expect(fast.target.margin).toBe(0);
    const hard = openBody({ ...d, mode: "hard_target", hardLimit: "30" }, real, BO).objective as Record<string, any>;
    expect(hard.statement).toBe("Meet a 10-80 % charge in at most 30 min while meeting these constraints.");
    expect(hard.kind).toBe("hard_target");
    expect(templateOf(real)?.model_uncertainty).toContain("physical cell");
  });
  test("auto-approval is off by default and, when on, is computational only with stated limits", () => {
    const o = openBody({ ...draft(), autoApprove: true, autoMaxWall: "120", autoMaxCount: "4" }, SPEC, BO).objective as Record<string, any>;
    expect(o.auto_approve).toEqual([{ executor: "computational", max_usd: 0, max_wall_s: 120, max_count: 4 }]);
  });
  test("a paid planner carries its profile and the model budget", () => {
    const o = openBody({ ...draft(), planner: "muse", modelUsd: "1.5" }, SPEC, MUSE_ON).objective as Record<string, any>;
    expect(o.planner).toEqual({ kind: "muse", profile: "meta", client: null });
    expect(o.budget.model_usd).toBe(1.5);
  });
  test("a changed limit rewrites the label's number, never leaving a stale one", () => {
    expect(relabel("Plating margin: anode potential at or above +10 mV", 10, 25, "x")).toBe("Plating margin: anode potential at or above +25 mV");
    expect(relabel("Cell at or below 45 degC", 45, 45, "x")).toBe("Cell at or below 45 degC");
    expect(relabel("From 10 to 10 mV", 10, 12, "fallback")).toBe("fallback");          // ambiguous: the plain label instead
    expect(relabel("Between 10 and 10.5", 10, 12, "x")).toBe("Between 12 and 10.5"); // 10.5 is another number
    expect(relabel("", undefined, 3, "fallback")).toBe("fallback");
    const d = draft();
    d.constraints[0].limit = "25";
    const c = (openBody(d, SPEC, BO).objective as Record<string, any>).constraints[0];
    expect(c).toEqual({ quantity: "min_anode_potential", op: ">=", limit: 25, unit: "mV", label: "Plating margin: anode potential at or above +25 mV" });
  });
});

describe("new campaign from the engineer's records", () => {
  const recs = (): Draft => ({ ...draft(), system: RECORDS_SYSTEM, source: "records", dataset: "", baseline: "", documentation: "Maccor export, one file per cell." });
  test("the objective names no dataset, baseline or uploads, and carries the documentation", () => {
    const o = openBody(recs(), { ...SPEC, id: RECORDS_SYSTEM }, BO).objective as Record<string, any>;
    expect(o.datasets).toEqual([]);
    expect(o.baseline).toEqual([]);
    expect(o.baseline_simulation).toBe(false);
    expect(o.documentation).toBe("Maccor export, one file per cell.");
    expect("uploads" in o).toBe(false);
    // a dataset campaign is unchanged: no documentation key
    expect("documentation" in (openBody(draft(), SPEC, BO).objective)).toBe(false);
  });
  test("records need no dataset or baseline, but the lab system and an agent planner", () => {
    expect(draftProblems({ ...recs(), modelUsd: "1.5" }, MUSE_ON, { canWrite: true })).toEqual([]);
    // the free planner has no policy for the lab system and cannot read records: refused before anything is sent
    expect(draftProblems(recs(), BO, { canWrite: true }).join(" ")).toContain("needs an agent planner");
    expect(draftProblems({ ...recs(), modelUsd: "1.5", system: "pybamm-fastcharge" }, MUSE_ON, { canWrite: true }).join(" ")).toContain("lab system");
    expect(draftProblems({ ...draft(), dataset: "" }, BO, { canWrite: true }).join(" ")).toContain("dataset");
  });
  test("file names are checked against the upload rule before anything is sent", () => {
    expect(fileNameProblem("LGM50_cell02.csv")).toBeNull();
    expect(fileNameProblem("run-1.v2.txt")).toBeNull();
    expect(fileNameProblem("cell 02.csv")).toContain("plain letters");
    expect(fileNameProblem(".hidden.csv")).toContain("plain letters");
    expect(fileNameProblem("x".repeat(129))).toContain("plain letters");
    expect(fileNameProblem("manifest.json")).toContain("reserved");
    expect(fileNameProblem("CON.csv")).toContain("device name");
    expect(fileNameProblem("com1")).toContain("device name");
    expect(fileNameProblem("console.csv")).toBeNull();
  });
  test("1 to 32 files, each at most 64 MiB, distinct names, and documentation of at most 20000 characters", () => {
    const f = (name: string, size = 10) => ({ name, size });
    expect(recordsProblems([f("a.csv")], "doc")).toEqual([]);
    expect(recordsProblems([], "doc").join(" ")).toContain("Choose the record files");
    expect(recordsProblems(Array.from({ length: 33 }, (_, i) => f(`r${i}.csv`)), "doc").join(" ")).toContain("at most 32");
    expect(recordsProblems([f("a.csv", 64 * 1024 * 1024)], "doc")).toEqual([]);
    expect(recordsProblems([f("a.csv", 64 * 1024 * 1024 + 1)], "doc").join(" ")).toContain("64 MiB");
    expect(recordsProblems([f("a.csv"), f("a.csv")], "doc").join(" ")).toContain("two files");
    expect(recordsProblems([f("a b.csv")], "doc").join(" ")).toContain("a b.csv");
    expect(recordsProblems([f("a.csv")], "  ").join(" ")).toContain("Describe the files");
    expect(recordsProblems([f("a.csv")], "x".repeat(20000))).toEqual([]);
    expect(recordsProblems([f("a.csv")], "x".repeat(20001)).join(" ")).toContain("at most 20000");
  });
  test("an upload id is 32 hex digits, fresh each time", () => {
    const a = newUploadId(), b = newUploadId();
    expect(a).toMatch(/^[0-9a-f]{32}$/);
    expect(a).not.toBe(b);
  });
  test("a file goes up as its bytes with a PUT, then the campaign opens naming the upload; the server's words come back", async () => {
    const calls = stubFetch((c) => c.init.method === "PUT" ? json({ name: "run.csv", bytes: 4 }, 201)
      : json({ detail: "Upload the records first" }, 404));
    const file = new Blob(["t,T\n"]);
    await v1.upload("lab", "a".repeat(32), "run.csv", file);
    expect(new URL(calls[0].url).pathname).toBe(`/api/v1/ledgers/lab/uploads/${"a".repeat(32)}/run.csv`);
    expect(calls[0].init.method).toBe("PUT");
    expect(calls[0].init.body).toBe(file);
    expect(headers(calls[0])["Content-Type"]).toBe("application/octet-stream");
    expect(headers(calls[0])["X-Translucent-Client"]).toBe("web");
    const err = await v1.open("lab", { title: "T", objective: {}, records_upload_id: "b".repeat(32) }).catch((e) => e);
    expect(JSON.parse(String(calls[1].init.body)).records_upload_id).toBe("b".repeat(32));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.message).toBe("Upload the records first");
  });
});

// ---------------------------------------------------------------- the stream and the charts

describe("event stream state", () => {
  test("drops read reconnecting, then offline after repeated failures; an open catches up and resets", () => {
    let s = { stream: "connecting" as const as import("./stream").Stream, failures: 0 };
    s = nextStream(s, "error", false);
    expect(s.stream).toBe("reconnecting");
    for (let i = 0; i < 3; i++) s = nextStream(s, "error", false);
    expect(s.stream).toBe("offline");
    s = nextStream(s, "open", false);
    expect(s).toEqual({ stream: "live", failures: 0 });
    expect(nextStream(s, "error", true).stream).toBe("closed");
  });
});

describe("chart limits", () => {
  test("a constrained series gets its current limit, and the amended-away limit marked with its version", () => {
    const v = fc();
    const anode = seriesLimits(v, "anode_potential", "mV");
    expect(anode.map((l) => [l.value, l.past])).toEqual([[25, false], [10, true]]);
    expect(anode[1].label).toContain("v1");
    expect(seriesLimits(v, "cell_temperature", "degC").map((l) => l.value)).toEqual([45]);
    expect(seriesLimits(v, "voltage", "V")).toEqual([]);
    expect(seriesLimits(v, "cell_temperature", "degF")).toEqual([]);
  });
});

// ---------------------------------------------------------------- the campaign list's outcome (review fix 2)

describe("campaign list: a feasibility recovery never reads as improved", () => {
  const findings = (over: Partial<SummaryFindings> = {}): SummaryFindings => ({
    basis: "computational", feasibility: { baseline: "fails", best: "meets", recovered: true },
    speed: { status: "not_compared" }, objective: { kind: "legacy", status: "open" }, ...over,
  });
  test("a /2 conclusion decided by feasibility says Feasibility recovered (simulation)", () => {
    const o = summaryOutcome({ outcome: "improved_in_simulation", findings: findings(), assessment_headline: "Feasibility recovered in simulation: x" });
    expect(o?.word).toBe("Feasibility recovered (simulation)");
    expect(o?.word.toLowerCase()).not.toContain("improved");
    expect(o?.title).toBe("Feasibility recovered in simulation: x");
  });
  test("without findings (an older server) an improved outcome is not repeated as Improved", () => {
    expect(summaryOutcome({ outcome: "improved_in_simulation" })?.word).toBe("Concluded (simulation)");
    expect(summaryOutcome({ outcome: "improved" })?.word).toBe("Concluded");
  });
  test("a /2 improvement shown faster against a feasible baseline stays Improved", () => {
    const f = findings({ feasibility: { baseline: "meets", best: "meets", recovered: false }, speed: { status: "faster" } });
    expect(summaryOutcome({ outcome: "improved_in_simulation", findings: f })?.word).toBe("Improved (simulation)");
  });
  test("/3 outcomes: feasible only after a failing baseline is a recovery; the others keep their words", () => {
    const f3 = findings({ objective: { kind: "fastest_feasible", status: "open" }, speed: { status: "not_faster" } });
    expect(summaryOutcome({ outcome: "feasible_only_in_simulation", findings: f3 })?.word).toBe("Feasibility recovered (simulation)");
    expect(summaryOutcome({ outcome: "faster_than_reference_in_simulation", findings: findings({ speed: { status: "faster" } }) }))
      .toMatchObject({ word: "Faster than the reference (simulation)", tone: "improved" });
    expect(summaryOutcome({ outcome: "no_feasible_result" })?.word).toBe("No feasible result");
    expect(summaryOutcome({ outcome: null })).toBeNull();
  });
});
