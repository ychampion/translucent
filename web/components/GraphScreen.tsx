"use client";
// A campaign's whole evidence graph: every finding, what it stands on, and every attempt that didn't work.
// Shown in people's words: the agent's working names for its numbers ("Window slope s2c first3") are spelled out, run
// code hashes stay in the run's own detail, and the columns shrink a little to fit before the canvas scrolls.
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Graph, GraphNode } from "@/lib/graph";
import { humanLabel } from "@/lib/labels";
import { useFindingPage } from "./FindingScreen";
import { PageHeader } from "./Shell";
import { LoadState } from "./Status";
import { GraphCanvas, GraphOutline, NodeDetail, useGraph } from "./Trail";

export function GraphScreen({ campaignKey }: { campaignKey: string }) {
  const { c, error, retry } = useFindingPage(campaignKey);
  const [focus, setFocus] = useState<string | null>(null);
  const [picked, setPicked] = useState<GraphNode | null>(null);
  useEffect(() => { setFocus(new URLSearchParams(window.location.search).get("focus")); }, []);
  if (!c) return <div className="screen loading"><PageHeader /><LoadState error={error} onRetry={retry} /></div>;
  return <GraphBody campaignKey={campaignKey} c={c} focus={focus} picked={picked} onPick={setPicked} />;
}

/** The same graph with measurement labels in words and run code hashes left to the run's detail panel. */
function forPeople(graph: Graph): Graph {
  const nodes = new Map<string, GraphNode>();
  for (const [id, n] of graph.nodes) {
    let x = n;
    if (n.measurement?.label) x = { ...x, measurement: { ...n.measurement, label: humanLabel(n.measurement.label) } };
    if (n.run?.code_sha) x = { ...x, run: { ...n.run, code_sha: undefined } as typeof n.run };
    nodes.set(id, x);
  }
  return { ...graph, nodes };
}

/** Shrinks the canvas (to 80 % at most) so its columns fit the space; past that it scrolls, with an edge shadow. */
function useFit() {
  const ref = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const canvas = el.querySelector<HTMLElement>(".graph-canvas");
      const need = canvas ? parseFloat(canvas.style.width) : 0;
      const have = el.clientWidth;
      setZoom(need > 0 && have > 0 ? Math.max(0.8, Math.min(1, have / need)) : 1);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, zoom };
}

function GraphBody({ campaignKey, c, focus, picked, onPick }: {
  campaignKey: string; c: NonNullable<ReturnType<typeof useFindingPage>["c"]>; focus: string | null;
  picked: GraphNode | null; onPick(n: GraphNode | null): void;
}) {
  const { store, view } = c;
  const raw = useGraph(store, view);
  const graph = useMemo(() => forPeople(raw), [raw]);
  const fit = useFit();
  const subjects = store.media.tracks.map((t) => t.subject);
  const roots = [...graph.nodes.values()].filter((n) => n.kind === "finding").sort((a, b) => a.order - b.order);
  const failed = [...graph.nodes.values()].filter((n) => n.failed).length;
  const shownFocus = focus && graph.nodes.has(focus) ? focus : null;
  // the detail panel shows the run as recorded, code hash included
  const pickedRaw = picked ? { ...(graph.nodes.get(picked.id) ?? picked), run: raw.nodes.get(picked.id)?.run ?? picked.run } : null;
  return (
    <div className="screen graph-screen">
      {/* the stored title is cut at a fixed length; the objective is the whole question (the header ellipsises it) */}
      <PageHeader title={<span title={view.campaign?.objective ?? view.campaign?.title}>{view.campaign?.objective ?? view.campaign?.title}</span>}>
        <Link className="btn btn-quiet" href={`/c/${campaignKey}/?at=end`}>Back to the campaign</Link>
      </PageHeader>
      <main className="graph-main">
        <header className="graph-head">
          <h1 className="graph-title">Evidence graph</h1>
          <p className="soft">
            {roots.length} findings, {[...graph.nodes.values()].filter((n) => n.kind === "measurement").length} measurements,{" "}
            {[...graph.nodes.values()].filter((n) => n.kind === "run").length} runs.
            {failed > 0 && ` Dashed: ${failed} that didn’t hold or didn’t work, kept on the record.`}
            {!graph.recordedRuns && " Runs rebuilt from the recorded tool events."}
          </p>
        </header>
        <div className="graph-wide">
          <div className="graph-fit" ref={fit.ref} style={{ ["--graph-zoom" as string]: fit.zoom }}>
            <GraphCanvas graph={graph} focus={shownFocus} subjects={subjects} picked={picked?.id} onPick={(n) => onPick(picked?.id === n.id ? null : n)} />
          </div>
          {pickedRaw && (
            <aside className="graph-detail" aria-label="Selected node">
              <button type="button" className="text-quiet small" onClick={() => onPick(null)}>Close</button>
              <NodeDetail node={pickedRaw} subjects={subjects} campaignKey={campaignKey} />
            </aside>
          )}
        </div>
        <div className="graph-narrow">
          <GraphOutline graph={graph} roots={shownFocus ? [graph.nodes.get(shownFocus)!, ...roots.filter((r) => r.id !== shownFocus)] : roots} subjects={subjects} campaignKey={campaignKey} />
        </div>
      </main>
    </div>
  );
}
