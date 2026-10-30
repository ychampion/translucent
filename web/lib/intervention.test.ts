import { describe, expect, test } from "bun:test";
import { splitFile } from "../scripts/split-intervention";
import {
  afterOff, callVerdict, cumulative, headlineAgent, originWord, policyByName, scorecard, splitVerdict, verdictLines,
  type IvDecision,
} from "./intervention";
import { holm, signFlip, splitIntervention } from "./intervention-split";

// The real export: web/public/fixtures/nist-bench.json (scripted NIST campaign).
const { summary: s, trials: files } = splitFile("nist-bench");
const file = (key: string) => files.find((f) => f.key === key)!;
const trial = (key: string) => s.trials.find((t) => t.key === key)!;

describe("splitting the NIST intervention bundle", () => {
  test("one trial per physical experiment, each with its trace and every split's decisions", () => {
    expect(s.trials.length).toBe(50);
    expect(files.length).toBe(50);
    expect(new Set(s.trials.map((t) => t.key)).size).toBe(50);
    for (const f of files) {
      expect(f.t.length).toBeGreaterThan(100);
      expect(f.t.length).toBe(f.y.length);
      expect(Object.keys(f.seals).sort()).toEqual(["loco", "lofo", "prequential"]);
      // 10 scored policies x 3 splits
      expect(f.decisions.length).toBe(30);
    }
  });

  test("every decision is committed before its outcome is revealed", () => {
    for (const f of files) for (const d of f.decisions) {
      expect(d.revealed_seq).not.toBeNull();
      expect(d.committed_seq).toBeLessThan(d.revealed_seq!);
      expect(d.terminal_committed_seq).toBeLessThan(d.revealed_seq!);
      expect(d.simulated).toBe(true);
    }
  });

  test("Exp 81 cooled 14.6 °C after heater-off and ignited 297 s later", () => {
    const t = trial("Exp81");
    expect(t.condition).toEqual({ format: "21700", soc: 75 });
    expect(t.outcome).toBe("delayed_ignition");
    expect(t.corroboration?.agree).toBe(true);
    const a = afterOff(file("Exp81"), t)!;
    expect(a.ignitionAfterOff).toBe(297);
    expect(a.drop).toBeCloseTo(14.6, 1);
  });

  test("nine cells, all 21700, cooled at least 2 °C after heater-off and still ignited", () => {
    const cooled = s.trials.filter((t) => t.outcome !== "rescued" && (t.drop_after_off ?? 0) >= 2);
    expect(cooled.map((t) => t.exp).sort((a, b) => a - b)).toEqual([61, 66, 71, 75, 76, 79, 80, 81, 82]);
    expect(cooled.every((t) => t.condition.format === "21700")).toBe(true);
  });

  test("the planner comes from the receipt: a scripted search with no model budget", () => {
    expect(s.origin).toEqual({ kind: "scripted", planners: ["scripted generic search v1"], budget_usd: 0 });
    expect(originWord(s.origin).tag).toBe("Scripted");
    expect(policyByName(s, "agent_threshold")!.planner).toBe("scripted generic search v1");
    expect(policyByName(s, "always_escalate")!.planner).toBeNull();
  });

  test("decisions carry the fitted model they came from", () => {
    const d = file("Exp81").decisions.find((x) => x.split === "loco" && x.policy_id === policyByName(s, "agent_threshold")!.id)!;
    expect(d.fit).toMatchObject({ family: "threshold", features: ["slope_20"], grouping: "format" });
    expect(d.fit!.n_train).toBeGreaterThan(0);
    expect(d.decision).toBe("escalate");
    expect(d.cost).toBe(0);
  });

  test("prequential running cost per trial reproduces each policy's recorded mean cost", () => {
    expect(s.prequential.order.length).toBe(50);
    for (const row of s.prequential.rows) {
      const score = s.scores.find((x) => x.split === "prequential" && x.policy_id === row.policy_id)!;
      const total = cumulative(row.cost).at(-1)!;
      expect(total / 50).toBeCloseTo(score.mean_cost!, 6);
      expect(cumulative(row.unsafe).at(-1)).toBe(score.unsafe);
    }
  });

  test("only agent updates are kept, each placed after a revealed prequential trial", () => {
    expect(s.updates.length).toBe(22 + 27 + 20 + 29);
    const order = new Set(s.prequential.order);
    for (const u of s.updates) expect(order.has(u.after_trial_key)).toBe(true);
    expect(s.updates.some((u) => !u.accepted && u.reason.startsWith("rejected"))).toBe(true);
  });

  test("procedure claims point at gate verdicts", () => {
    expect(s.procedure!.claim_ids.length).toBe(3);
    for (const id of s.procedure!.claim_ids) expect(s.claims.find((c) => c.id === id)?.verdict).toBeTruthy();
  });

  test("next experiments stay proposed and simulated", () => {
    expect(s.next_experiments.length).toBe(3);
    for (const x of s.next_experiments) {
      expect(x.status).toBe("proposed");
      expect(x.simulated).toBe(true);
      expect(x.per_hypothesis.length).toBe(3);
    }
  });

  test("a bundle without an intervention field is refused", () => {
    expect(() => splitIntervention({ fixture: "x", bundle: { campaign: {} }, records: [], series: () => null, file: { path: "", sha256: "", bytes: 0 } })).toThrow();
  });
});

describe("the scorecard says plainly what won", () => {
  test("held-out condition: lowest cost is the logistic agent, and it does not beat always escalate after correction", () => {
    const rows = scorecard(s, "loco");
    expect(rows[0].policy.name).toBe("agent_logistic");
    expect(rows[0].score.mean_cost).toBeCloseTo(3.72, 6);
    expect(rows.at(-1)!.policy.name).toBe("muse_only");
    const v = splitVerdict(s, "loco");
    expect(v.agentVsEscalate.length).toBe(2);
    expect(v.agentVsEscalate.every((x) => !x.beats)).toBe(true);
    expect(v.familySize).toBe(18);
    const lines = verdictLines(s, "loco");
    expect(lines[0]).toContain("Agent: logistic search, 3.72");
    expect(lines[1]).toStartWith("No agent policy beats always escalate after Holm correction");
  });

  test("no split has an agent policy beating always escalate", () => {
    for (const split of ["loco", "lofo", "prequential"] as const) {
      expect(splitVerdict(s, split).agentVsEscalate.some((x) => x.beats)).toBe(false);
    }
  });

  test("on held-out format, always escalate has the lowest cost", () => {
    expect(splitVerdict(s, "lofo").best.map((r) => r.policy.name)).toEqual(["always_escalate"]);
  });

  test("the procedure's agent is the headline agent", () => {
    expect(headlineAgent(s)!.name).toBe("agent_threshold");
  });
});

// The live export: web/public/fixtures/nist-live.json (Muse Wonder planner, v2 run with the frozen baselines
// registered), merged with nist-muse-only.json (Muse Wonder alone).
describe("the live run against Muse alone", () => {
  const { summary: live, trials: liveFiles } = splitFile("nist-live");
  const byName = (name: string) => policyByName(live, name)!;
  const score = (name: string) => live.scores.find((x) => x.split === "loco" && x.policy_id === byName(name).id)!;
  const vs = (a: string, b: string) => live.comparisons.find((c) => c.split === "loco" && c.a === byName(a).id && c.b === byName(b).id)!;

  test("the strategies come from Muse Wonder, live", () => {
    expect(live.origin.kind).toBe("live");
    expect(live.origin.planners).toEqual(["Muse Wonder (muse-wonder-1.0)"]);
    expect(originWord(live.origin).tag).toBe("Live");
  });

  test("50 held-out trials: procedure policy 0 unsafe, 17 missed, 3.40 from the gate's record; always escalate 0, 19, 3.80", () => {
    const proc = headlineAgent(live)!;
    expect(proc.id).toBe(live.procedure!.policy_id!);
    expect(proc.name).toBe("logistic_curvature");
    const p = score("logistic_curvature"), e = score("always_escalate");
    expect([p.n, p.unsafe, p.missed, p.mean_cost, p.source]).toEqual([50, 0, 17, 3.4, "gate"]);
    expect([e.n, e.unsafe, e.missed, e.source]).toEqual([50, 0, 19, "computed"]);
    expect(e.mean_cost).toBeCloseTo(3.8, 9);
    // The ledger registers always escalate now: no reference row computed from outcomes.
    expect(byName("always_escalate").external).toBeUndefined();
    expect(live.policies.filter((x) => x.name === "always_escalate").length).toBe(1);
  });

  test("the baselines and strategies match the run's recorded results", () => {
    const want: Record<string, [number, number, number]> = {
      logistic_soc_delay: [0, 17, 3.4], logistic_pooled_all: [0, 17, 3.4], search_generic: [0, 17, 3.4],
      logistic_observe60: [0, 15, 3.54], surface_threshold_level: [0, 19, 3.8], constant_escalate_agent: [0, 19, 3.8],
      logistic_observe30: [1, 15, 5.51], thermal_observe30: [4, 6, 9.71], table3_soc: [5, 2, 10.4],
      temperature_refit: [3, 12, 8.4], paper_table3: [5, 2, 10.4], delay_lookup_refit: [6, 11, 14.2],
      always_observe_30: [6, 4, 14.26], fixed_temperature: [13, 8, 27.6], physics_only: [31, 0, 62], two_node_physics: [31, 0, 62],
    };
    for (const [name, [u, m, c]] of Object.entries(want)) {
      const sc = score(name);
      expect([name, sc.unsafe, sc.missed]).toEqual([name, u, m]);
      expect(sc.mean_cost!).toBeCloseTo(c, 2);
    }
  });

  test("against always escalate the comparison is the gate's: 3.4 vs 3.8, p 0.5, Holm 1, family 90", () => {
    const c = vs("logistic_curvature", "always_escalate");
    expect(c.source).toBe("gate");
    expect([c.a_mean_cost, c.b_mean_cost, c.p, c.p_holm, c.family_size]).toEqual([3.4, 3.8, 0.5, 1, 90]);
    expect(splitVerdict(live, "loco").agentVsEscalate.some((x) => x.beats)).toBe(false);
  });

  test("the gate refused the policy_beats claim, and the procedure cites only supported claims", () => {
    const refused = live.claims.filter((c) => c.type === "policy_beats" && c.verdict === "inconclusive");
    expect(refused.length).toBe(1);
    expect(refused[0].reason).toBe("Mean decision cost 3.4 vs 3.8; paired sign-flip p=0.5, Holm p=1, family 90");
    const cited = live.procedure!.claim_ids.map((id) => live.claims.find((c) => c.id === id)!);
    expect(cited.length).toBeGreaterThan(0);
    expect(cited.every((c) => c.verdict === "supported")).toBe(true);
    expect(live.claims.filter((c) => c.verdict === "supported").length).toBe(2);
  });

  test("Muse alone comes from its own ledger score: 6 unsafe, 7 missed, 13.82; not significant after Holm", () => {
    const m = score("muse_only");
    expect([m.n, m.unsafe, m.missed, m.mean_cost, m.source]).toEqual([50, 6, 7, 13.82, "ledger"]);
    expect(byName("muse_only").external).toBe("nist-muse-only");
    expect(live.policies.filter((x) => x.name === "muse_only").length).toBe(1);
    const c = vs("logistic_curvature", "muse_only");
    expect(c.source).toBe("computed");
    expect(c.p_holm).toBe(1);
    expect(c.family_size).toBe(90);
  });

  test("every computed score agrees with the per-trial costs on the decisions", () => {
    for (const sc of live.scores.filter((x) => x.source === "computed")) {
      const ds = liveFiles.flatMap((f) => f.decisions.filter((d) => d.policy_id === sc.policy_id && d.split === "loco"));
      expect(ds.length).toBe(50);
      expect(ds.reduce((a, d) => a + d.cost!, 0) / 50).toBeCloseTo(sc.mean_cost!, 9);
    }
  });

  test("only Muse alone's decisions come from the comparator ledger, one per trial", () => {
    for (const f of liveFiles) {
      const other = f.decisions.filter((d) => d.ledger);
      expect(other.length).toBe(1);
      expect(other[0].policy_id).toBe(byName("muse_only").id);
      expect(other[0].ledger).toBe("nist-muse-only");
      expect(f.decisions.length).toBe(19);
    }
  });
});

describe("the ported statistics", () => {
  test("exact sign-flip: four same-sign differences give 1/8, none give 1", () => {
    expect(signFlip([-10, -10, -10, -10, 0, 0]).p).toBe(0.125);
    expect(signFlip([0, 0]).p).toBe(1);
    expect(signFlip([1, -1]).p).toBe(1);
  });
  test("Holm over a family larger than the p values given", () => {
    const h = holm(new Map([["a", 0.01], ["b", 0.04]]), 4);
    expect(h.get("a")).toBeCloseTo(0.04, 12);
    expect(h.get("b")).toBeCloseTo(0.12, 12);
  });
});

describe("was the call right", () => {
  const d = (x: Partial<IvDecision>): IvDecision => ({
    id: "d", trial_key: "Exp1", policy_id: "p", split: "loco", p_ignite: 0.5, interval: [0, 1], decision: "escalate",
    initial_action: "escalate", observe_s: 0, watch_time_s: 0, ignited_during_watch: false, committed_seq: 1,
    terminal_committed_seq: 1, revealed_seq: 2, correct: true, unsafe: 0, simulated: true, cost: 0, fit: null, ...x,
  });
  test("escalating on an ignition is right", () => expect(callVerdict(d({}), { outcome: "delayed_ignition" }).kind).toBe("right"));
  test("standing down on an ignition is unsafe", () =>
    expect(callVerdict(d({ decision: "stand_down", correct: false, unsafe: 1 }), { outcome: "ignited" }).kind).toBe("unsafe"));
  test("escalating when the cell did not ignite is a missed rescue", () =>
    expect(callVerdict(d({ correct: false }), { outcome: "rescued" }).kind).toBe("missed"));
  test("ignition during a paid watch is unsafe whatever came after", () =>
    expect(callVerdict(d({ initial_action: "observe", observe_s: 30, ignited_during_watch: true, unsafe: 1 }), { outcome: "ignited" }).kind).toBe("watch_ignition"));
  test("standing down when the cell did not ignite is right", () =>
    expect(callVerdict(d({ decision: "stand_down" }), { outcome: "rescued" }).kind).toBe("right"));
  test("an unrevealed outcome is not judged", () => expect(callVerdict(d({ correct: null }), { outcome: null }).kind).toBe("unknown"));
});
