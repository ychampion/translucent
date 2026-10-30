"use client";
// A campaign's whole evidence graph: every finding, what it stands on, and every attempt that didn't work.
import Link from "next/link";
import { useEffect, useState } from "react";
import type { GraphNode } from "@/lib/graph";
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

function GraphBody({ campaignKey, c, focus, picked, onPick }: {
  campaignKey: string; c: NonNullable<ReturnType<typeof useFindingPage>["c"]>; focus: string | null;
  picked: GraphNode | null; onPick(n: GraphNode | null): void;
}) {
  const { store, view } = c;
  const graph = useGraph(store, view);
  const subjects = store.media.tracks.map((t) => t.subject);
  const roots = [...graph.nodes.values()].filter((n) => n.kind === "finding").sort((a, b) => a.order - b.order);
  const failed = [...graph.nodes.values()].filter((n) => n.failed).length;
  const shownFocus = focus && graph.nodes.has(focus) ? focus : null;
  return (
    <div className="screen graph-screen">
      <PageHeader title={view.campaign?.title}>
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
          <GraphCanvas graph={graph} focus={shownFocus} subjects={subjects} picked={picked?.id} onPick={(n) => onPick(picked?.id === n.id ? null : n)} />
          {picked && (
            <aside className="graph-detail" aria-label="Selected node">
              <button type="button" className="text-quiet small" onClick={() => onPick(null)}>Close</button>
              <NodeDetail node={picked} subjects={subjects} campaignKey={campaignKey} />
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
