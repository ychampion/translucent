// The evidence graph of a campaign: each finding, the measurements it cites, the runs that produced them (with their
// code, parameters, model and cost), the runs' inputs, the calibrations behind each number, the frames and outlines
// they were computed from, and the recording at the root. Failed runs and refused findings are nodes too: they are
// drawn dashed and greyed, never hidden. Pure functions over a bundle's tables and events, so it works the same on a
// replay, a live run, and on older bundles that have no `runs` table (runs are then rebuilt from the tool events).
import type { Artifact, Check, Claim, Event, Measurement, Review, Run } from "./contracts";
import { stateFor, type FindingState } from "./store";
import { groupChecks } from "./words";

export type NodeKind = "finding" | "measurement" | "run" | "calibration" | "frame" | "artifact" | "recording";
export type EdgeKind = "cites" | "produced_by" | "read" | "calibrated_by" | "computed_from" | "derived_from" | "replaces";

export interface GraphNode {
  id: string;
  kind: NodeKind;
  /** Order of appearance (event seq, else creation time), for stable rows. */
  order: number;
  /** A failed run, a refused or crashed finding, a cited number that was never recorded: dashed, greyed, kept. */
  failed?: boolean;
  claim?: Claim;
  state?: FindingState;
  review?: Review;
  checks?: Check[];
  measurement?: Measurement;
  run?: Run;
  artifact?: Artifact;
}

export interface GraphEdge { from: string; to: string; kind: EdgeKind }

export interface Graph {
  nodes: Map<string, GraphNode>;
  edges: GraphEdge[];
  /** Runs came from the bundle's `runs` table (true) or were rebuilt from tool events (false). */
  recordedRuns: boolean;
}

export interface GraphSource {
  measurements: Map<string, Measurement>;
  claims: Map<string, Claim>;
  reviews: Map<string, Review>;
  artifacts: Map<string, Artifact>;
  runs: Map<string, Run>;
}

const ID = /^(?:sha256:[0-9a-f]{16,}|m_[0-9a-f]{6,})$/;

/** Every recorded id named anywhere in a run's arguments (video, outlines, scale, inputs of an analysis). */
function idsIn(v: unknown, out: string[] = []): string[] {
  if (typeof v === "string") { if (ID.test(v) && !out.includes(v)) out.push(v); }
  else if (Array.isArray(v)) for (const x of v) idsIn(x, out);
  else if (v && typeof v === "object") for (const x of Object.values(v)) idsIn(x, out);
  return out;
}

/** Runs rebuilt from `tool.started` / `tool.finished` events, for bundles without a `runs` table. */
export function runsFromEvents(events: Event[], campaignId = ""): Map<string, Run> {
  const runs = new Map<string, Run>();
  for (const e of events) {
    const id = e.refs?.[0];
    if (!id || (e.type !== "tool.started" && e.type !== "tool.finished")) continue;
    const d = (e.data ?? {}) as Record<string, unknown>;
    if (e.type === "tool.started") {
      const params = (d.args ?? {}) as Record<string, unknown>;
      runs.set(id, {
        id, campaign_id: e.campaign_id || campaignId, step_id: "", tool: e.actor, idempotency_key: "",
        input_ids: idsIn(params), output_ids: [], params, status: "ok", started_at: e.t, ended_at: e.t,
      });
    } else {
      const r = runs.get(id);
      if (!r) continue;
      const result = (d.result ?? {}) as Record<string, unknown>;
      const ok = d.status == null || d.status === "ok";
      r.status = ok ? "ok" : d.status === "invalid_result" ? "invalid_result" : "tool_failure";
      if (!ok && typeof d.summary === "string") r.error = d.summary;
      if (typeof d.cost_usd === "number") r.cost_usd = d.cost_usd;
      if (typeof result.code_sha === "string") r.code_sha = result.code_sha;
      for (const k of ["model", "provider"] as const) if (typeof d[k] === "string") r[k] = d[k] as string;
      r.output_ids = (e.refs ?? []).slice(1);
      r.ended_at = e.t;
    }
  }
  return runs;
}

const CALIBRATIONS = new Set(["spatial_scale", "time_base"]);
const isCalibration = (m: Measurement) => CALIBRATIONS.has(m.quantity);

function artifactKind(a: Artifact | undefined): NodeKind {
  if (!a) return "artifact";
  if (a.kind === "video") return "recording";
  if (a.kind === "frame") return "frame";
  return "artifact";
}

/**
 * The campaign's evidence graph from the events received so far (a replay grows its graph as it plays) and the
 * bundle's tables. With no events, every claim in the tables is included.
 */
export function buildGraph(src: GraphSource, events: Event[]): Graph {
  const nodes = new Map<string, GraphNode>();
  const edges: GraphEdge[] = [];
  const seenEdge = new Set<string>();
  const edge = (from: string, to: string, kind: EdgeKind) => {
    const k = `${from}>${to}>${kind}`;
    if (from === to || seenEdge.has(k)) return;
    seenEdge.add(k);
    edges.push({ from, to, kind });
  };
  const recordedRuns = src.runs.size > 0;
  const runs = recordedRuns ? src.runs : runsFromEvents(events);
  const seqOf = new Map<string, number>();
  for (const e of events) for (const r of e.refs ?? []) if (!seqOf.has(r)) seqOf.set(r, e.seq);
  const order = (id: string, fallback: number) => seqOf.get(id) ?? fallback;

  // Findings, in the order they were put to the check, with the checker's latest verdict.
  const claims: Claim[] = [];
  const gate = new Map<string, Review>();
  const proposeRun = new Map<string, string>();
  let lastPropose: string | null = null;
  if (events.length) {
    for (const e of events) {
      if (e.type === "tool.started" && e.actor === "propose_finding") lastPropose = e.refs?.[0] ?? null;
      if (e.type === "claim.proposed") {
        const c = (e.data?.claim as Claim | undefined) ?? src.claims.get(e.refs?.[0] ?? "");
        if (c && !claims.some((x) => x.id === c.id)) { claims.push(c); if (lastPropose) proposeRun.set(c.id, lastPropose); }
      } else if (e.type === "claim.reviewed") {
        const r = (e.data?.review as Review | undefined) ?? src.reviews.get(e.refs?.[1] ?? "");
        if (r && r.reviewer === "gate") gate.set(r.claim_id, r);
      }
    }
  } else {
    claims.push(...[...src.claims.values()].sort((a, b) => a.created_at - b.created_at));
    for (const r of [...src.reviews.values()].sort((a, b) => a.created_at - b.created_at)) if (r.reviewer === "gate") gate.set(r.claim_id, r);
  }

  const addArtifact = (id: string): string => {
    if (nodes.has(id)) return id;
    const a = src.artifacts.get(id);
    nodes.set(id, { id, kind: artifactKind(a), order: order(id, a?.created_at ?? 0), artifact: a });
    for (const p of a?.transform?.parent_ids ?? []) edge(id, addArtifact(p), "derived_from");
    return id;
  };

  const addRun = (id: string): string => {
    if (nodes.has(id)) return id;
    const r = runs.get(id);
    nodes.set(id, { id, kind: "run", order: order(id, r?.started_at ?? 0), run: r, failed: !!r && r.status !== "ok" });
    for (const input of r?.input_ids ?? []) {
      if (src.measurements.has(input)) edge(id, addMeasurement(input), "read");
      else if (src.artifacts.has(input)) edge(id, addArtifact(input), "read");
    }
    return id;
  };

  function addMeasurement(id: string): string {
    if (nodes.has(id)) return id;
    const m = src.measurements.get(id);
    if (!m) {
      nodes.set(id, { id, kind: "measurement", order: order(id, 0), failed: true });
      return id;
    }
    // A calibration's producing run is read as "declared by": it stays on the calibration, not as a node of its own.
    const cal = isCalibration(m);
    nodes.set(id, { id, kind: cal ? "calibration" : "measurement", order: order(id, 0), measurement: m, run: cal ? runs.get(m.run_id) : undefined });
    if (!cal) edge(id, addRun(m.run_id), "produced_by");
    for (const c of m.calibration_ids ?? []) edge(id, addMeasurement(c), "calibrated_by");
    for (const a of m.artifact_ids ?? []) edge(id, addArtifact(a), "computed_from");
    return id;
  }

  for (const c of claims) {
    const review = gate.get(c.id);
    const propose = runs.get(proposeRun.get(c.id) ?? "");
    const crashed = !review && !!propose && propose.status !== "ok";
    const state = crashed ? "not-proven" : stateFor(review);
    nodes.set(c.id, {
      id: c.id, kind: "finding", order: order(c.id, c.created_at), claim: c, review, state,
      checks: groupChecks(review?.checks ?? []), failed: state === "refused" || crashed,
    });
    for (const m of c.predicate.measurement_ids) edge(c.id, addMeasurement(m), "cites");
  }
  for (const c of claims) if (c.supersedes && nodes.has(c.supersedes)) edge(c.id, c.supersedes, "replaces");

  // Failed attempts are kept even when nothing cites them.
  for (const r of runs.values()) if (r.status !== "ok" && (!events.length || seqOf.has(r.id))) addRun(r.id);

  return { nodes, edges, recordedRuns };
}

/** Nodes reachable from `id` along the graph's edges (not following `replaces`), in breadth-first order. */
export function reachable(g: Graph, id: string, follow: (e: GraphEdge) => boolean = (e) => e.kind !== "replaces"): string[] {
  const out: string[] = [id];
  const seen = new Set(out);
  for (let i = 0; i < out.length; i++) {
    for (const e of g.edges) {
      if (e.from !== out[i] || !follow(e) || seen.has(e.to)) continue;
      seen.add(e.to);
      out.push(e.to);
    }
  }
  return out;
}

/** Everything upstream of a node and downstream of it, for lighting up one path. */
export function pathThrough(g: Graph, id: string): Set<string> {
  const down = reachable(g, id, () => true);
  const up: string[] = [id];
  const seen = new Set(up);
  for (let i = 0; i < up.length; i++) {
    for (const e of g.edges) if (e.to === up[i] && !seen.has(e.from)) { seen.add(e.from); up.push(e.from); }
  }
  return new Set([...down, ...up]);
}

export interface Trail {
  finding: GraphNode;
  /** What it stands on, breadth-first from the finding. */
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Other attempts at the same thing: findings it replaced, and failed runs of the tools it used. */
  others: GraphNode[];
}

/** One finding's evidence trail. */
export function trailOf(g: Graph, findingId: string): Trail | null {
  const finding = g.nodes.get(findingId);
  if (!finding || finding.kind !== "finding") return null;
  const ids = reachable(g, findingId);
  const set = new Set(ids);
  const nodes = ids.map((id) => g.nodes.get(id)!).filter(Boolean);
  const tools = new Set(nodes.filter((n) => n.kind === "run" && n.run).map((n) => n.run!.tool));
  const replaced = reachable(g, findingId, (e) => e.kind === "replaces").slice(1).map((id) => g.nodes.get(id)!);
  const failedRuns = [...g.nodes.values()].filter((n) => n.kind === "run" && n.failed && !set.has(n.id) && n.run && tools.has(n.run.tool));
  return { finding, nodes, edges: g.edges.filter((e) => set.has(e.from) && set.has(e.to) && e.kind !== "replaces"), others: [...replaced, ...failedRuns] };
}

/** Children of a node in the trail, for the indented outline. */
export function childrenOf(g: Graph | Trail, id: string): { node: GraphNode; kind: EdgeKind }[] {
  const nodes = "finding" in g ? new Map(g.nodes.map((n) => [n.id, n] as const)) : g.nodes;
  return g.edges.filter((e) => e.from === id && e.kind !== "replaces").map((e) => ({ node: nodes.get(e.to)!, kind: e.kind })).filter((x) => x.node);
}

// ---------------------------------------------------------------- layout

export interface Placed { node: GraphNode; col: number; row: number }
export interface Layout { placed: Placed[]; cols: number; rows: number; columns: NodeKind[][] }

const SOURCE_KINDS = new Set<NodeKind>(["calibration", "frame", "artifact"]);

/**
 * A layered left-to-right layout: findings first; then measurements and runs by their longest path from a finding
 * (an analysis run that read other measurements pushes those further right); then calibrations, frames and outlines;
 * the recording last. Rows within a column follow the average row of each node's parents, so lines mostly run flat.
 */
export function layoutGraph(g: Graph, only?: Set<string>): Layout {
  const nodes = [...g.nodes.values()].filter((n) => !only || only.has(n.id));
  const keep = new Set(nodes.map((n) => n.id));
  const edges = g.edges.filter((e) => keep.has(e.from) && keep.has(e.to) && e.kind !== "replaces");
  const depth = new Map<string, number>();
  for (const n of nodes) if (n.kind === "finding") depth.set(n.id, 0);
  // Longest path over measurement/run nodes (a DAG; bounded passes guard against a malformed cycle).
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const e of edges) {
      const to = g.nodes.get(e.to)!;
      if (to.kind !== "measurement" && to.kind !== "run") continue;
      const d = (depth.get(e.from) ?? (g.nodes.get(e.from)!.kind === "run" ? 0 : -1)) + 1;
      if (d > (depth.get(e.to) ?? -1)) { depth.set(e.to, d); changed = true; }
    }
    if (!changed) break;
  }
  // Unattached runs (failed attempts nothing cites) sit just left of what they read, else in the first run column.
  for (const n of nodes) {
    if ((n.kind !== "run" && n.kind !== "measurement") || depth.has(n.id)) continue;
    const reads = edges.filter((e) => e.from === n.id).map((e) => depth.get(e.to)).filter((x): x is number => x != null);
    depth.set(n.id, reads.length ? Math.max(0, Math.min(...reads) - 1) : n.kind === "run" ? 2 : 1);
  }
  const maxInner = Math.max(0, ...[...depth.values()]);
  // Sources point at each other too (a calibration cites a table, outlines come from frames): layer them likewise.
  const src = new Map<string, number>();
  for (const n of nodes) if (SOURCE_KINDS.has(n.kind)) src.set(n.id, 0);
  for (let pass = 0; pass < src.size; pass++) {
    let changed = false;
    for (const e of edges) {
      if (!src.has(e.from) || !src.has(e.to)) continue;
      if (src.get(e.from)! + 1 > src.get(e.to)!) { src.set(e.to, src.get(e.from)! + 1); changed = true; }
    }
    if (!changed) break;
  }
  const maxSrc = Math.max(0, ...[...src.values()]);
  const colOf = (n: GraphNode) =>
    n.kind === "recording" ? maxInner + maxSrc + 2 : src.has(n.id) ? maxInner + 1 + src.get(n.id)! : depth.get(n.id) ?? 0;

  const byCol = new Map<number, GraphNode[]>();
  for (const n of nodes) { const c = colOf(n); byCol.set(c, [...(byCol.get(c) ?? []), n]); }
  const cols = [...byCol.keys()].sort((a, b) => a - b);
  const rowOf = new Map<string, number>();
  const parents = new Map<string, string[]>();
  for (const e of edges) parents.set(e.to, [...(parents.get(e.to) ?? []), e.from]);
  const placed: Placed[] = [];
  const columns: NodeKind[][] = [];
  cols.forEach((c, ci) => {
    const list = byCol.get(c)!;
    const score = (n: GraphNode) => {
      const ps = (parents.get(n.id) ?? []).map((p) => rowOf.get(p)).filter((x): x is number => x != null);
      return ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : Infinity;
    };
    list.sort((a, b) => (ci === 0 ? a.order - b.order : score(a) - score(b) || a.order - b.order));
    list.forEach((n, i) => { rowOf.set(n.id, i); placed.push({ node: n, col: ci, row: i }); });
    columns.push([...new Set(list.map((n) => n.kind))]);
  });
  return { placed, cols: cols.length, rows: Math.max(0, ...cols.map((c) => byCol.get(c)!.length)), columns };
}
