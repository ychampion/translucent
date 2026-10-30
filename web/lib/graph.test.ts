import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ReplayBundle } from "./contracts";
import { buildGraph, layoutGraph, reachable, runsFromEvents, trailOf } from "./graph";
import { catalogFrom } from "./store";

const load = (folder: string): ReplayBundle =>
  JSON.parse(readFileSync(join(import.meta.dir, "..", "public", "fixtures", folder, "bundle.json"), "utf-8"));

const edgeKey = (g: ReturnType<typeof buildGraph>) => g.edges.map((e) => `${e.from}>${e.to}>${e.kind}`).sort();

for (const folder of ["m4-team", "lu-live"]) {
  describe(`evidence graph: ${folder}`, () => {
    const b = load(folder);
    const events = [...b.events].sort((x, y) => x.seq - y.seq);
    const g = buildGraph(catalogFrom(b), events);

    test("rebuilds runs from tool events on a bundle without a runs table", () => {
      expect(b.runs ?? []).toHaveLength(0);
      expect(g.recordedRuns).toBe(false);
      const started = events.filter((e) => e.type === "tool.started").length;
      expect(runsFromEvents(events).size).toBe(started);
    });

    test("every finding is in the graph and every measurement it cites is reachable", () => {
      for (const c of b.claims ?? []) {
        expect(g.nodes.get(c.id)?.kind).toBe("finding");
        const seen = new Set(reachable(g, c.id));
        for (const m of c.predicate.measurement_ids) expect(seen.has(m)).toBe(true);
      }
    });

    test("each recorded measurement in a trail leads to the run that made it, and on to the recording", () => {
      const ms = new Map((b.measurements ?? []).map((m) => [m.id, m]));
      for (const c of b.claims ?? []) {
        const seen = new Set(reachable(g, c.id));
        for (const id of c.predicate.measurement_ids) {
          const m = ms.get(id);
          if (!m) { expect(g.nodes.get(id)?.failed).toBe(true); continue; }
          if (m.quantity !== "spatial_scale" && m.quantity !== "time_base") expect(seen.has(m.run_id)).toBe(true);
        }
        const trail = trailOf(g, c.id)!;
        if (trail.nodes.some((n) => n.kind === "measurement" && n.measurement)) {
          expect(trail.nodes.some((n) => n.kind === "recording")).toBe(true);
        }
      }
    });

    test("failed runs are kept as failed nodes", () => {
      const failed = events.filter((e) => e.type === "tool.finished" && e.data?.status && e.data.status !== "ok");
      expect(failed.length).toBeGreaterThan(0);
      for (const e of failed) expect(g.nodes.get(e.refs![0])?.failed).toBe(true);
    });

    test("the same graph from a runs table as from events", () => {
      const runs = [...runsFromEvents(events).values()];
      const withRuns = buildGraph(catalogFrom({ ...b, runs }), events);
      expect(withRuns.recordedRuns).toBe(true);
      expect([...withRuns.nodes.keys()].sort()).toEqual([...g.nodes.keys()].sort());
      expect(edgeKey(withRuns)).toEqual(edgeKey(g));
    });

    test("layout runs left to right: every edge but a replaced finding points to a later column", () => {
      const l = layoutGraph(g);
      const col = new Map(l.placed.map((p) => [p.node.id, p.col]));
      for (const e of g.edges) if (e.kind !== "replaces") expect(col.get(e.to)!).toBeGreaterThan(col.get(e.from)!);
      expect(l.columns[0]).toContain("finding");
      expect(l.columns[l.columns.length - 1]).toEqual(["recording"]);
    });
  });
}

test("analysis code and its cost come from the tool events", () => {
  const b = load("m4-team");
  const runs = runsFromEvents(b.events);
  const analysis = [...runs.values()].filter((r) => r.tool === "run_analysis_code" && r.status === "ok");
  expect(analysis.length).toBeGreaterThan(0);
  expect(typeof analysis[0].params?.code).toBe("string");
  expect(analysis.some((r) => typeof r.code_sha === "string")).toBe(true);
});
