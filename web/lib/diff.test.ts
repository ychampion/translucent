import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Event, ReplayBundle } from "./contracts";
import { alignSteps, diffCampaigns, groupSteps, movedBeyond, stepKey } from "./diff";

const load = (folder: string): ReplayBundle =>
  JSON.parse(readFileSync(join(import.meta.dir, "..", "public", "fixtures", folder, "bundle.json"), "utf-8"));

interface Expected {
  a: string; b: string;
  tool_calls: { a: number; b: number; same_first_calls: number; a_next: string | null; b_next: string | null };
  cost_usd: { a: number; b: number };
  lessons_loaded: { a: string[]; b: string[] };
  findings: { a: string; b: string; a_verdict: string; b_verdict: string }[];
  only_a: { id: string; verdict: string }[];
  only_b: { id: string; verdict: string }[];
  sentences: string[];
}

const expected: Expected = JSON.parse(
  readFileSync(join(import.meta.dir, "..", "..", "tests", "data", "diff-m4-meta-vs-m4-team.json"), "utf-8"),
);

describe("compare m4-meta (A) with m4-team (B), against the recorded diff", () => {
  const d = diffCampaigns(load("m4-meta"), load("m4-team"));

  test("same campaigns", () => {
    expect(d.a).toBe(expected.a);
    expect(d.b).toBe(expected.b);
  });

  test("pairs the same findings, with the same verdicts", () => {
    const got = d.findings.map((p) => `${p.a.id}>${p.b.id}>${p.aVerdict}>${p.bVerdict}`).sort();
    const want = expected.findings.map((p) => `${p.a}>${p.b}>${p.a_verdict}>${p.b_verdict}`).sort();
    expect(got).toEqual(want);
  });

  test("leaves the same findings unpaired", () => {
    expect(d.onlyA.map((x) => `${x.claim.id}>${x.verdict}`).sort()).toEqual(expected.only_a.map((x) => `${x.id}>${x.verdict}`).sort());
    expect(d.onlyB.map((x) => `${x.claim.id}>${x.verdict}`).sort()).toEqual(expected.only_b.map((x) => `${x.id}>${x.verdict}`).sort());
  });

  test("tool calls, first divergence, cost and lessons agree", () => {
    expect(d.toolCalls).toEqual({
      a: expected.tool_calls.a, b: expected.tool_calls.b, sameFirst: expected.tool_calls.same_first_calls,
      aNext: expected.tool_calls.a_next, bNext: expected.tool_calls.b_next,
    });
    expect(d.cost.a).toBeCloseTo(expected.cost_usd.a, 5);
    expect(d.cost.b).toBeCloseTo(expected.cost_usd.b, 5);
    expect(d.lessons).toEqual(expected.lessons_loaded);
  });

  test("the summary sentences match", () => {
    expect(d.sentences).toEqual(expected.sentences);
  });

  test("step alignment keeps every call, in order, on each side", () => {
    const as = d.steps.flatMap((r) => ("a" in r ? [r.a.seq] : []));
    const bs = d.steps.flatMap((r) => ("b" in r ? [r.b.seq] : []));
    expect(as).toHaveLength(expected.tool_calls.a);
    expect(bs).toHaveLength(expected.tool_calls.b);
    expect([...as].sort((x, y) => x - y)).toEqual(as);
    expect([...bs].sort((x, y) => x - y)).toEqual(bs);
    const groups = groupSteps(d.steps);
    expect(groups[0]).toMatchObject({ kind: "same" });
    expect(groups.reduce((n, g) => n + (g.kind === "same" ? g.rows.length : 1), 0)).toBe(d.steps.length);
  });
});

describe("pieces", () => {
  const ev = (actor: string, args: Record<string, unknown>, seq = 0): Event =>
    ({ seq, campaign_id: "c", t: seq, type: "tool.started", actor, title: "", data: { args } });

  test("step keys ignore record ids but not settings", () => {
    expect(stepKey(ev("measure", { boxes_id: "sha256:ab12", frame: 3 }))).toBe(stepKey(ev("measure", { frame: 3, boxes_id: "sha256:cd34" })));
    expect(stepKey(ev("measure", { frame: 3 }))).not.toBe(stepKey(ev("measure", { frame: 4 })));
  });

  test("alignment marks same, changed and one-sided calls", () => {
    const rows = alignSteps([ev("x", {}, 1), ev("y", { f: 1 }, 2), ev("z", {}, 3)], [ev("x", {}, 1), ev("y", { f: 2 }, 2), ev("w", {}, 3)]);
    expect(rows.map((r) => r.kind)).toEqual(["same", "changed", "a", "b"]);
  });

  test("a measurement moves only beyond both uncertainties", () => {
    const m = (value: number, u: number) => ({ id: "", run_id: "", quantity: "q", value, unit: "s", uncertainty: { value: u, kind: "std" as const, method: "" } });
    expect(movedBeyond(m(10, 1), m(11.5, 1))).toBe(false);
    expect(movedBeyond(m(10, 1), m(12.5, 1))).toBe(true);
  });
});
