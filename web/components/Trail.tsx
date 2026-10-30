"use client";
// The evidence trail on screen: a layered left-to-right graph (finding, measurements, runs, sources, recording) where
// hovering or focusing a node lights its whole path; on a phone, the same trail as an indented outline. "How we know"
// is one finding's trail read top to bottom: its checks, each number, the run that made it (code, parameters, model,
// cost), the calibrations behind it, the frames and the recording. Failed attempts are dashed and grey, never hidden.
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { experiment } from "@/lib/campaigns";
import type { Run } from "@/lib/contracts";
import {
  buildGraph, childrenOf, layoutGraph, pathThrough, trailOf, type Graph, type GraphNode, type NodeKind, type Trail,
} from "@/lib/graph";
import type { CampaignStore, CampaignView, Finding } from "@/lib/store";
import {
  STATE_WORD, checkDetail, checkLabel, clean, durationWord, isPhrasedTool, keepUnits, money, plainLabel, subjectWord, toolPhrase,
  uncertaintyWord, valueWord,
} from "@/lib/words";
import { shortCitation } from "./EvidencePanel";
import { CheckGlyph, StateMark, findingHref } from "./Findings";
import { FrameStrip } from "./FrameStrip";

/** The campaign's graph from what has been shown so far. */
export function useGraph(store: CampaignStore, view: CampaignView): Graph {
  return useMemo(() => buildGraph(store.catalog, view.events), [store.catalog, view.events]);
}

const KIND_WORD: Record<NodeKind, string> = {
  finding: "Findings", measurement: "Measurements", run: "Runs", calibration: "Calibrations", frame: "Frames",
  artifact: "Outlines and tables", recording: "Recording",
};

const short = (s: string, n = 90) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const sha = (s: string | null | undefined) => (s ? s.replace(/^sha256:/, "").slice(0, 7) : null);

export function runTitle(r: Run | undefined): string {
  if (!r) return "A run not in this record";
  if (isPhrasedTool(r.tool)) return toolPhrase(r.tool, { running: false, frames: typeof r.params?.frame === "number" ? [r.params.frame] : [] });
  if (r.tool === "propose_finding") return "Put a finding to the check";
  if (r.tool === "record_lesson") return "Kept a lesson";
  return String(r.tool).replace(/_/g, " ");
}

/** "ok · 1.2 s · $0.02 · code a1b2c3d" */
function runFacts(r: Run | undefined): string {
  if (!r) return "";
  const parts = [r.status === "ok" ? "worked" : "didn’t work"];
  const secs = r.ended_at - r.started_at;
  if (secs >= 0.05) parts.push(durationWord(secs));
  if (r.cost_usd) parts.push(money(r.cost_usd));
  if (r.model) parts.push(r.model.split("/").pop()!);
  if (r.code_sha) parts.push(`code ${sha(r.code_sha)}`);
  return parts.join(" · ");
}

/** Where a calibration came from, in words: who declared the scale or the clock, and how. */
export function declaredBy(n: GraphNode): string {
  const m = n.measurement, r = n.run;
  const how = m ? clean(m.uncertainty.method) : "";
  const who = r?.params?.declared_by ?? r?.params?.author;
  if (typeof who === "string" && who) return `Declared by ${who}${how ? `: ${how}` : ""}`;
  if (r?.tool === "lookup_calibration") return `Declared by the experiment’s records${how ? `: ${how}` : ""}`;
  if (r?.tool === "read_cell_voltage") return `Read from the recorded voltage log${how ? `: ${how}` : ""}`;
  return how ? `Recorded with the experiment: ${how}` : "Recorded with the experiment";
}

function measurementName(n: GraphNode, subjects: string[]) {
  const m = n.measurement;
  if (!m) return "A number that was never recorded";
  return plainLabel(subjectWord(m.label ?? m.quantity.replace(/^derived:/, "").replace(/_/g, " "), subjects));
}

/** The words on one node: a title and a quiet second line. */
export function nodeText(n: GraphNode, subjects: string[] = []): { title: string; sub: string } {
  switch (n.kind) {
    case "finding": {
      const checks = n.checks ?? [];
      const passed = checks.filter((c) => c.passed).length;
      return { title: short(keepUnits(n.claim?.statement ?? ""), 96), sub: `${STATE_WORD[n.state ?? "checking"]}${checks.length ? ` · ${passed}/${checks.length} checks` : ""}` };
    }
    case "measurement":
      return n.measurement
        ? { title: short(measurementName(n, subjects), 60), sub: `${valueWord(n.measurement)}${n.measurement.uncertainty.value != null ? ` ± ${+n.measurement.uncertainty.value.toPrecision(2)}` : ""}` }
        : { title: "Not a recorded measurement", sub: short(n.id, 40) };
    case "calibration":
      return { title: short(measurementName(n, subjects), 60), sub: n.measurement ? valueWord(n.measurement) : "" };
    case "run":
      return { title: runTitle(n.run), sub: runFacts(n.run) || "No record of this run" };
    case "frame":
      return { title: n.artifact?.label ?? "Frame", sub: "Image cut from the recording" };
    case "artifact":
      return { title: n.artifact?.label ?? (n.artifact?.kind === "masks" ? "Outlines" : "Table"), sub: n.artifact ? `${n.artifact.kind === "masks" ? "outlines" : n.artifact.kind}${n.artifact.transform ? ` · ${n.artifact.transform.op.replace(/_/g, " ")}` : ""}` : "" };
    case "recording":
      return { title: n.artifact?.label ?? "The recording", sub: n.artifact?.source?.citation ? shortCitation(n.artifact.source.citation) : "" };
  }
}

// ---------------------------------------------------------------- the layered graph

const W = 212, H = 66, GAP_X = 52, GAP_Y = 10, HEAD = 26;

export function GraphCanvas({ graph, only, focus, subjects, onPick, picked }: {
  graph: Graph; only?: Set<string>; focus?: string | null; subjects?: string[];
  onPick?(n: GraphNode): void; picked?: string | null;
}) {
  const layout = useMemo(() => layoutGraph(graph, only), [graph, only]);
  const [hover, setHover] = useState<string | null>(null);
  const lit = useMemo(() => {
    const id = hover ?? picked ?? focus;
    return id && graph.nodes.has(id) ? pathThrough(graph, id) : null;
  }, [graph, hover, picked, focus]);
  const pos = new Map(layout.placed.map((p) => [p.node.id, { x: p.col * (W + GAP_X), y: HEAD + p.row * (H + GAP_Y) }] as const));
  const width = layout.cols * (W + GAP_X) - GAP_X;
  const height = HEAD + layout.rows * (H + GAP_Y);
  const edges = graph.edges.filter((e) => pos.has(e.from) && pos.has(e.to) && e.kind !== "replaces");
  if (!layout.placed.length) return <p className="soft small">Nothing on record yet.</p>;
  return (
    <div className="graph-scroll">
      <div className="graph-canvas" style={{ width, height }}>
        {layout.columns.map((kinds, i) => (
          <p key={i} className="graph-col-h" style={{ left: i * (W + GAP_X), width: W }}>{kinds.map((k) => KIND_WORD[k]).join(" · ")}</p>
        ))}
        <svg className="graph-edges" width={width} height={height} aria-hidden>
          {edges.map((e) => {
            const a = pos.get(e.from)!, b = pos.get(e.to)!;
            const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
            const on = !lit || (lit.has(e.from) && lit.has(e.to));
            const failed = graph.nodes.get(e.from)?.failed || graph.nodes.get(e.to)?.failed;
            return <path key={`${e.from}>${e.to}>${e.kind}`} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
              className={`graph-edge ${on ? "" : "is-dim"} ${failed ? "is-failed" : ""} ${lit && on ? "is-lit" : ""}`} />;
          })}
        </svg>
        {layout.placed.map(({ node }) => {
          const p = pos.get(node.id)!;
          const t = nodeText(node, subjects);
          const on = !lit || lit.has(node.id);
          return (
            <button key={node.id} type="button" className={`gnode kind-${node.kind} ${node.failed ? "is-failed" : ""} ${on ? "" : "is-dim"} ${picked === node.id ? "is-picked" : ""} ${node.state ? `state-${node.state}` : ""}`}
              style={{ left: p.x, top: p.y, width: W, height: H }}
              onMouseEnter={() => setHover(node.id)} onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(node.id)} onBlur={() => setHover(null)} onClick={() => onPick?.(node)}
              aria-label={`${KIND_WORD[node.kind].replace(/s$/, "")}: ${t.title}. ${t.sub}${node.failed ? ". Didn’t hold or didn’t work" : ""}`}>
              <span className="gnode-title">{node.kind === "finding" && <StateMark state={node.state ?? "checking"} />}{t.title}</span>
              <span className="gnode-sub num">{t.sub}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- the outline (phones, and the panel)

function OutlineNode({ g, node, depth, seen, subjects }: { g: Graph | Trail; node: GraphNode; depth: number; seen: Set<string>; subjects: string[] }) {
  const t = nodeText(node, subjects);
  const repeat = seen.has(node.id);
  seen.add(node.id);
  const kids = repeat || depth > 7 ? [] : childrenOf(g, node.id);
  return (
    <li className={`outline-item kind-${node.kind} ${node.failed ? "is-failed" : ""}`}>
      <span className="outline-row">
        {node.kind === "finding" ? <StateMark state={node.state ?? "checking"} /> : <span className={`outline-dot kind-${node.kind}`} aria-hidden />}
        <span className="outline-text"><span>{t.title}</span>{t.sub && <span className="outline-sub num">{repeat ? "shown above" : t.sub}</span>}</span>
      </span>
      {kids.length > 0 && (
        <ul className="outline">{kids.map((k) => <OutlineNode key={k.node.id} g={g} node={k.node} depth={depth + 1} seen={seen} subjects={subjects} />)}</ul>
      )}
    </li>
  );
}

export function GraphOutline({ graph, roots, subjects = [], campaignKey }: { graph: Graph; roots: GraphNode[]; subjects?: string[]; campaignKey?: string }) {
  const failedRuns = [...graph.nodes.values()].filter((n) => n.kind === "run" && n.failed && !graph.edges.some((e) => e.to === n.id));
  return (
    <div className="graph-outline">
      {roots.map((r, i) => (
        <details key={r.id} className={`outline-finding ${r.failed ? "is-failed" : ""}`} open={i === 0}>
          <summary>
            <StateMark state={r.state ?? "checking"} />
            <span>{nodeText(r, subjects).title}</span>
          </summary>
          <ul className="outline">{childrenOf(graph, r.id).map((k) => <OutlineNode key={k.node.id} g={graph} node={k.node} depth={1} seen={new Set([r.id])} subjects={subjects} />)}</ul>
          {campaignKey && <Link className="text-quiet small" href={findingHref(campaignKey, r.id)}>All the checks</Link>}
        </details>
      ))}
      {failedRuns.length > 0 && (
        <details className="outline-finding is-failed">
          <summary><span className="outline-dot kind-run" aria-hidden /><span>{failedRuns.length === 1 ? "1 attempt" : `${failedRuns.length} attempts`} that didn’t work</span></summary>
          <ul className="outline">{failedRuns.map((n) => <OutlineNode key={n.id} g={graph} node={n} depth={1} seen={new Set()} subjects={subjects} />)}</ul>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- one node in full

/** Parameters without the code, as short "name: value" lines. */
function paramLines(r: Run): [string, string][] {
  return Object.entries(r.params ?? {})
    .filter(([k]) => k !== "code")
    .map(([k, v]) => [k.replace(/_/g, " "), short(typeof v === "string" ? v : JSON.stringify(v), 140)]);
}

export function RunDetail({ run }: { run: Run | undefined }) {
  if (!run) return <p className="soft small">This run isn&rsquo;t in the record.</p>;
  const code = typeof run.params?.code === "string" ? run.params.code : null;
  const params = paramLines(run);
  return (
    <div className="run-detail">
      <p className="run-facts num">{runFacts(run)}</p>
      {run.error && <p className="run-error">{clean(run.error)}</p>}
      {(code || params.length > 0) && (
        <details className="code-drawer">
          <summary>{code ? "View code" : "Parameters"}</summary>
          {params.length > 0 && <dl className="run-params">{params.map(([k, v]) => <div key={k}><dt>{k}</dt><dd className="num">{v}</dd></div>)}</dl>}
          {code && <pre className="code-block"><code>{code}</code></pre>}
        </details>
      )}
    </div>
  );
}

export function NodeDetail({ node, subjects = [], campaignKey }: { node: GraphNode; subjects?: string[]; campaignKey: string }) {
  const t = nodeText(node, subjects);
  return (
    <div className={`node-detail ${node.failed ? "is-failed" : ""}`}>
      <p className="node-kind">{KIND_WORD[node.kind].replace(/s$/, "")}{node.failed ? " · didn’t hold" : ""}</p>
      <p className="node-title">{node.kind === "finding" ? keepUnits(node.claim?.statement ?? "") : t.title}</p>
      {node.kind !== "run" && t.sub && <p className="node-sub num">{t.sub}</p>}
      {node.kind === "finding" && <Link className="pill-link" href={findingHref(campaignKey, node.id)}>All the checks</Link>}
      {node.kind === "measurement" && node.measurement && <p className="node-sub">{uncertaintyWord(node.measurement)}</p>}
      {node.kind === "calibration" && <p className="node-sub">{declaredBy(node)}</p>}
      {node.kind === "run" && <RunDetail run={node.run} />}
      {node.kind === "recording" && node.artifact?.source && (
        <p className="node-sub">{node.artifact.source.citation}{node.artifact.source.licence ? ` · ${node.artifact.source.licence}` : ""}</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- how we know (finding page, receipt)

function Step({ kind, failed, children }: { kind: string; failed?: boolean; children: ReactNode }) {
  return <li className={`know-step kind-${kind} ${failed ? "is-failed" : ""}`}>{children}</li>;
}

/**
 * One finding's lineage, top to bottom: the checks, each cited number, the run behind it, its calibrations, the
 * frames, the recording; and the other attempts, greyed.
 */
export function HowWeKnow({ graph, f, store, campaignKey, frames, compact }: {
  graph: Graph; f: Finding; store: CampaignStore; campaignKey: string; frames?: number[]; compact?: boolean;
}) {
  const trail = trailOf(graph, f.claim.id);
  const media = store.media;
  const subjects = media.tracks.map((t) => t.subject);
  const ex = experiment(campaignKey);
  if (!trail) return null;
  const node = (id: string) => trail.nodes.find((n) => n.id === id);
  const cited = f.claim.predicate.measurement_ids.map((id) => node(id)).filter((n): n is GraphNode => !!n);
  const recording = trail.nodes.find((n) => n.kind === "recording");
  const checks = trail.finding.checks ?? [];
  const runsShown = new Set<string>();

  const lineage = (m: GraphNode, depth: number): ReactNode => {
    const kids = childrenOf(trail, m.id);
    const runEdge = kids.find((k) => k.kind === "produced_by");
    const cals = kids.filter((k) => k.kind === "calibrated_by").map((k) => k.node);
    const run = runEdge?.node;
    const inputs = run ? childrenOf(trail, run.id).filter((k) => k.node.kind === "measurement").map((k) => k.node) : [];
    const firstTime = run && !runsShown.has(run.id);
    if (run) runsShown.add(run.id);
    const t = nodeText(m, subjects);
    return (
      <Step key={`${m.id}-${depth}`} kind={m.kind} failed={m.failed}>
        <p className="know-what"><span className="know-label">{t.title}</span><span className="num know-value">{t.sub}</span></p>
        {m.measurement && <p className="know-note">{uncertaintyWord(m.measurement)}</p>}
        {run && (
          <ul className="know">
            <Step kind="run" failed={run.failed}>
              <p className="know-what"><span className="know-label">{runTitle(run.run)}</span></p>
              {firstTime ? (compact ? <p className="know-note num">{runFacts(run.run)}</p> : <RunDetail run={run.run} />) : <p className="know-note">The same run as above.</p>}
              {firstTime && depth < 3 && inputs.length > 0 && (
                <ul className="know">{inputs.map((i) => lineage(i, depth + 1))}</ul>
              )}
            </Step>
          </ul>
        )}
        {cals.length > 0 && (
          <ul className="know">
            {cals.map((c) => (
              <Step key={c.id} kind="calibration">
                <p className="know-what"><span className="know-label">{nodeText(c, subjects).title}</span><span className="num know-value">{nodeText(c, subjects).sub}</span></p>
                <p className="know-note">{declaredBy(c)}</p>
              </Step>
            ))}
          </ul>
        )}
      </Step>
    );
  };

  return (
    <section className={`how-we-know ${compact ? "is-compact" : ""}`} aria-labelledby={`know-${f.claim.id}`}>
      <h2 className="detail-h" id={`know-${f.claim.id}`}>How we know</h2>
      <ol className="know is-root">
        <Step kind="finding" failed={trail.finding.failed}>
          <p className="know-what"><StateMark state={f.state} /><span className="know-label">{STATE_WORD[f.state]}</span></p>
          {checks.length > 0 && (
            <ul className="know-checks">
              {checks.map((c) => (
                <li key={c.name} className={c.passed ? "pass" : "fail"}>
                  <CheckGlyph ok={c.passed} /><span>{checkLabel(c.name)}</span>
                  {!compact && checkDetail(c.detail, subjects) && <span className="know-note"> · {checkDetail(c.detail, subjects)}</span>}
                </li>
              ))}
            </ul>
          )}
          <ul className="know">{cited.map((m) => lineage(m, 0))}</ul>
        </Step>
        {frames && frames.length > 0 && (
          <Step kind="frame">
            <p className="know-what"><span className="know-label">Source frames</span></p>
            {!compact && (
              <FrameStrip src={media.video} fps={media.fps} size={media.videoSize} frames={frames.slice(0, 5)} region={ex.region}
                track={ex.region ? undefined : media.tracks[0]} className="frames-tight" />
            )}
            {compact && <p className="know-note num">{frames.slice(0, 8).map((x) => `frame ${x}`).join(", ")}</p>}
          </Step>
        )}
        {recording && (
          <Step kind="recording">
            <p className="know-what"><span className="know-label">{nodeText(recording, subjects).title}</span></p>
            {recording.artifact?.source && (
              <p className="know-note">{recording.artifact.source.citation}{recording.artifact.source.licence ? ` · ${recording.artifact.source.licence}` : ""}</p>
            )}
          </Step>
        )}
      </ol>
      {trail.others.length > 0 && (
        <div className="know-others">
          <p className="know-note">Other attempts</p>
          <ul className="know">
            {trail.others.map((n) => (
              <Step key={n.id} kind={n.kind} failed>
                <p className="know-what">
                  {n.kind === "finding" ? <Link className="know-label" href={findingHref(campaignKey, n.id)}>{nodeText(n, subjects).title}</Link>
                    : <span className="know-label">{nodeText(n, subjects).title}</span>}
                  <span className="know-value">{n.kind === "finding" ? STATE_WORD[n.state ?? "checking"] : nodeText(n, subjects).sub}</span>
                </p>
                {n.kind === "run" && n.run?.error && <p className="know-note">{short(clean(n.run.error), 160)}</p>}
              </Step>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- the panel tab

export function TrailTab({ store, view, finding, campaignKey }: { store: CampaignStore; view: CampaignView; finding: Finding | null; campaignKey: string }) {
  const graph = useGraph(store, view);
  const subjects = store.media.tracks.map((t) => t.subject);
  const only = useMemo(() => {
    if (!finding) return undefined;
    const t = trailOf(graph, finding.claim.id);
    return t ? new Set(t.nodes.map((n) => n.id)) : undefined;
  }, [graph, finding]);
  const [picked, setPicked] = useState<GraphNode | null>(null);
  const roots = [...graph.nodes.values()].filter((n) => n.kind === "finding").sort((a, b) => a.order - b.order);
  return (
    <div className="trail-tab">
      <p className="trail-links">
        <Link className="pill-link" href={`/c/${campaignKey}/graph/${finding ? `?focus=${encodeURIComponent(finding.claim.id)}` : ""}`}>The whole graph</Link>
        {finding && <Link className="pill-link" href={findingHref(campaignKey, finding.claim.id)}>How we know</Link>}
      </p>
      {finding && only ? (
        <>
          <p className="trail-statement">{keepUnits(finding.claim.statement)}</p>
          <div className="only-wide-panel">
            <GraphCanvas graph={graph} only={only} focus={finding.claim.id} subjects={subjects} picked={picked?.id} onPick={setPicked} />
            {picked && <NodeDetail node={picked} subjects={subjects} campaignKey={campaignKey} />}
          </div>
          <div className="only-narrow-panel"><GraphOutline graph={graph} roots={[graph.nodes.get(finding.claim.id)!]} subjects={subjects} /></div>
        </>
      ) : (
        <GraphOutline graph={graph} roots={roots} subjects={subjects} campaignKey={campaignKey} />
      )}
    </div>
  );
}
