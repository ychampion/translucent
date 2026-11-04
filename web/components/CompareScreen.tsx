"use client";
// Two campaigns side by side: what changed first, then both recordings on one lab clock, then their steps aligned.
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { EXPERIMENTS, experiment } from "@/lib/campaigns";
import { VideoClock } from "@/lib/clock";
import type { Claim, Event, Measurement, ReplayBundle } from "@/lib/contracts";
import { diffCampaigns, groupSteps, type CampaignDiff, type FindingPair, type MeasurementChange, type StepRow } from "@/lib/diff";
import { useCommands } from "@/lib/commands";
import { loadBundleAt, storeFrom } from "@/lib/sources";
import { momentsOf, stateFor, type CampaignStore, type FindingState } from "@/lib/store";
import { STATE_WORD, durationWord, isPhrasedTool, keepUnits, plainLabel, timeOn, unitWord, toolPhrase, valueWord } from "@/lib/words";
import { frameAtTime } from "./EvidencePanel";
import { StateMark, findingHref } from "./Findings";
import { PageHeader } from "./Shell";
import { SignalPanel } from "./SignalPanel";
import { Stage } from "./Stage";
import { LoadState } from "./Status";
import { Timeline } from "./Timeline";
import { withBase } from "@/lib/base";

/** The exports that can be compared, by folder. Only these are fetched. */
export const COMPARABLE: { folder: string; label: string; experiment: string }[] = [
  { folder: "m4-team", label: "Movie 4, with the team", experiment: "m4" },
  { folder: "m4-meta", label: "Movie 4, earlier run", experiment: "m4" },
  { folder: "m4-dev", label: "Movie 4, first run", experiment: "m4" },
  { folder: "lu-live", label: "Graphite, live run", experiment: "lu" },
  { folder: "lu-meta", label: "Graphite, earlier run", experiment: "lu" },
  { folder: "lu-dev", label: "Graphite, first run", experiment: "lu" },
];
const known = (f: string | null) => COMPARABLE.find((x) => x.folder === f) ?? null;

/** The campaign page that shows this folder, when there is one: only the offered export of each experiment. */
const pageKey = (folder: string) => EXPERIMENTS.find((x) => x.folder === folder)?.key ?? null;

export const compareHref = (a: string, b: string) => `/compare/?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`;

/** The other export of the same experiment to compare an offered one with. */
export function compareTargetFor(campaignKey: string): string | null {
  const ex = experiment(campaignKey);
  if (ex.key !== campaignKey || !ex.folder) return null;
  return COMPARABLE.find((x) => x.experiment === ex.key && x.folder !== ex.folder)?.folder ?? null;
}

interface Side { folder: string; label: string; bundle: ReplayBundle; store: CampaignStore }

async function loadSide(folder: string): Promise<Side> {
  const k = known(folder)!;
  const bundle = await loadBundleAt(withBase(`/fixtures/${folder}`));
  const store = storeFrom(bundle, true);
  store.showUpTo(store.total);
  return { folder, label: k.label, bundle, store };
}

export function CompareScreen() {
  const [pair, setPair] = useState<{ a: string; b: string } | null>(null);
  const [sides, setSides] = useState<[Side, Side] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const a = known(q.get("a")) ? q.get("a")! : "m4-team";
    const b = known(q.get("b")) ? q.get("b")! : COMPARABLE.find((x) => x.folder !== a && x.experiment === known(a)!.experiment)!.folder;
    setPair({ a, b });
  }, []);

  useEffect(() => {
    if (!pair) return;
    let gone = false;
    setError(null);
    Promise.all([loadSide(pair.a), loadSide(pair.b)])
      .then((s) => { if (!gone) setSides(s); })
      .catch((e: unknown) => { if (!gone) setError(e instanceof Error ? e.message : String(e)); });
    return () => { gone = true; };
  }, [pair, attempt]);

  useEffect(() => { document.title = "Compare – Translucent"; }, []);

  if (!sides) {
    return (
      <div className="screen loading">
        <PageHeader title="Compare" />
        <LoadState error={error} onRetry={() => setAttempt((n) => n + 1)} />
      </div>
    );
  }
  return <CompareBody a={sides[0]} b={sides[1]} />;
}

function CompareBody({ a, b }: { a: Side; b: Side }) {
  const diff = useMemo(() => diffCampaigns(a.bundle, b.bundle), [a.bundle, b.bundle]);
  const swap = compareHref(b.folder, a.folder);
  useCommands("compare", () => [
    { id: "compare-swap", label: "Swap the two sides", group: "Compare", run: () => { window.location.href = withBase(swap); } },
  ], [swap]);
  return (
    <div className="screen compare-screen">
      <PageHeader title="Compare">
        <a className="btn btn-quiet" href={swap}>Swap sides</a>
      </PageHeader>
      <main className="compare-main">
        <header className="compare-head">
          <h1 className="compare-title">What changed</h1>
          <p className="compare-sides">
            <SideName tag="A" side={a} /> <span className="soft">compared with</span> <SideName tag="B" side={b} />
          </p>
        </header>
        <Summary diff={diff} />
        <Findings diff={diff} a={a} b={b} />
        <Lanes a={a} b={b} />
        <Steps diff={diff} a={a} b={b} />
      </main>
    </div>
  );
}

function SideName({ tag, side }: { tag: string; side: Side }) {
  const key = pageKey(side.folder);
  const name = <><span className="side-tag" aria-hidden>{tag}</span>{side.label}</>;
  return key ? <Link className="compare-side" href={`/c/${key}/?at=end`}>{name}</Link> : <span className="compare-side">{name}</span>;
}

/** The panel's Compare tab: this run against the experiment's other recorded run, in brief. */
export function CompareTab({ campaignKey, campaignId }: { campaignKey: string; campaignId?: string }) {
  const target = compareTargetFor(campaignKey);
  const folder = experiment(campaignKey).folder;
  const [diff, setDiff] = useState<CampaignDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!target) return;
    let gone = false;
    setError(null);
    Promise.all([loadBundleAt(withBase(`/fixtures/${folder}`)), loadBundleAt(withBase(`/fixtures/${target}`))])
      .then(([x, y]) => { if (!gone) setDiff(diffCampaigns(x, y)); })
      .catch((e: unknown) => { if (!gone) setError(e instanceof Error ? e.message : String(e)); });
    return () => { gone = true; };
  }, [folder, target, attempt]);
  if (!target) return null;
  const other = known(target)!.label;
  return (
    <div className="compare-tab">
      <p className="compare-sides">
        <span className="compare-side"><span className="side-tag" aria-hidden>A</span>This run</span>{" "}
        <span className="soft">compared with</span>{" "}
        <span className="compare-side"><span className="side-tag" aria-hidden>B</span>{other}</span>
      </p>
      {diff && campaignId && diff.a !== campaignId ? (
        <p className="soft small">This campaign isn’t one of the recorded runs; the comparison below is between the two recordings.</p>
      ) : null}
      {diff ? (
        <>
          <Summary diff={diff} />
          <p className="soft small">
            {diff.findings.filter((p) => !p.same).length} paired findings differ; {diff.onlyA.length} only here, {diff.onlyB.length} only in the other run.
          </p>
        </>
      ) : <LoadState error={error} onRetry={() => setAttempt((n) => n + 1)} />}
      <p><Link className="pill-link" href={compareHref(folder, target)}>Open the full comparison</Link></p>
    </div>
  );
}

// ---------------------------------------------------------------- summary

const ROLE_WORD: [string, string][] = [["planner", "Planner"], ["pointer", "Pointer"], ["second_opinion", "Second opinion"]];
const modelWord = (m: string | undefined) => (m ? m.replace(/^accounts\/[^/]+\/models\//, "") : "Not recorded");
const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);
const toolWord = (t: string | null) => (t == null ? "nothing more" : isPhrasedTool(t) ? lower(toolPhrase(t, { running: false, frames: [] })) : String(t).replace(/_/g, " "));

function Summary({ diff }: { diff: CampaignDiff }) {
  const supported = (v: string[]) => v.filter((x) => x === "supported").length;
  const verdictsA = [...diff.findings.map((p) => p.aVerdict), ...diff.onlyA.map((x) => x.verdict)];
  const verdictsB = [...diff.findings.map((p) => p.bVerdict), ...diff.onlyB.map((x) => x.verdict)];
  const tc = diff.toolCalls;
  const rows: [string, string, string, boolean][] = [];
  for (const [role, word] of ROLE_WORD) {
    const x = diff.models.a[role], y = diff.models.b[role];
    if (x || y) rows.push([word, modelWord(x), modelWord(y), x !== y]);
  }
  rows.push(
    ["Findings supported", `${supported(verdictsA)} of ${verdictsA.length}`, `${supported(verdictsB)} of ${verdictsB.length}`, true],
    ["Tool calls", String(tc.a), String(tc.b), tc.a !== tc.b],
    ["Running time", durationWord(diff.activeSeconds.a), durationWord(diff.activeSeconds.b), true],
    ["Model cost", `$${diff.cost.a.toFixed(2)}`, `$${diff.cost.b.toFixed(2)}`, diff.cost.a.toFixed(2) !== diff.cost.b.toFixed(2)],
    ["Lessons loaded", String(diff.lessons.a.length), String(diff.lessons.b.length), diff.lessons.a.length !== diff.lessons.b.length],
  );
  const split = tc.aNext == null && tc.bNext == null
    ? `Both made the same ${tc.a} tool calls.`
    : `Both began with the same ${tc.sameFirst} tool calls. Then A ${toolWord(tc.aNext)} and B ${toolWord(tc.bNext)}.`;
  return (
    <section className="compare-summary" aria-label="Summary">
      <table className="compare-table">
        <thead><tr><th scope="col"><span className="visually-hidden">What</span></th><th scope="col">A</th><th scope="col">B</th></tr></thead>
        <tbody>
          {rows.map(([what, x, y, differs]) => (
            <tr key={what} className={differs ? "is-diff" : ""}><th scope="row">{what}</th><td>{x}</td><td>{y}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="compare-split">{split}</p>
    </section>
  );
}

// ---------------------------------------------------------------- findings

const stateOf = (verdict: string): FindingState =>
  verdict === "proposed" ? "checking" : stateFor({ verdict } as Parameters<typeof stateFor>[0]);

function Verdict({ v }: { v: string }) {
  const s = stateOf(v);
  return <span className="compare-verdict"><StateMark state={s} />{v === "proposed" ? "Not checked" : STATE_WORD[s]}</span>;
}

function FindingLink({ side, claim }: { side: Side; claim: Claim }) {
  const key = pageKey(side.folder);
  const text = keepUnits(claim.statement);
  return key ? <Link href={findingHref(key, claim.id)}>{text}</Link> : <span>{text}</span>;
}

const mName = (m: Measurement) => plainLabel(m.label ?? m.quantity.replace(/^derived:/, "").replace(/_/g, " "));
const mValue = (m: Measurement) => `${valueWord(m)}${typeof m.value === "number" && m.uncertainty?.value != null ? ` ± ${+m.uncertainty.value.toPrecision(2)}` : ""}`;

function measurementLine(c: MeasurementChange): string | null {
  if (c.a && c.b) return c.moved ? `${mName(c.b)}: ${mValue(c.a)} in A, ${mValue(c.b)} in B, further apart than their uncertainties.` : null;
  const m = (c.a ?? c.b)!;
  return `Only ${c.a ? "A" : "B"} cites ${mName(m)} (${mValue(m)}).`;
}

function rangeLine(p: FindingPair): string | null {
  if (!p.rangeChanged) return null;
  const ra = p.a.predicate.params as { lo?: number; hi?: number; unit?: string }, rb = p.b.predicate.params as typeof ra;
  if (typeof ra.lo === "number" && typeof rb.lo === "number") {
    const n = (x: number | undefined) => (x == null ? "?" : +x.toPrecision(3));
    return `Accepted range ${n(ra.lo)} to ${n(ra.hi)} in A, ${n(rb.lo)} to ${n(rb.hi)} in B${rb.unit ? ` ${unitWord(rb.unit)}` : ""}.`;
  }
  return "The test settings differ.";
}

function Findings({ diff, a, b }: { diff: CampaignDiff; a: Side; b: Side }) {
  const changed = diff.findings.filter((p) => !p.same);
  const same = diff.findings.filter((p) => p.same);
  return (
    <section className="compare-findings" aria-labelledby="cf-h">
      <h2 id="cf-h" className="compare-h2">Findings</h2>
      <p className="soft small">Paired by what they test and the quantities they cite, not by their wording.</p>
      {changed.length > 0 && (
        <ul className="compare-pairs">
          {changed.map((p) => (
            <li key={p.a.id + p.b.id} className="compare-pair">
              <div className="pair-sides">
                <div className="pair-side"><span className="side-tag" aria-label="A">A</span><div><FindingLink side={a} claim={p.a} /><Verdict v={p.aVerdict} /></div></div>
                <div className="pair-side"><span className="side-tag" aria-label="B">B</span><div><FindingLink side={b} claim={p.b} /><Verdict v={p.bVerdict} /></div></div>
              </div>
              <ul className="pair-notes">
                {p.aVerdict !== p.bVerdict && <li>Verdict changed: {STATE_WORD[stateOf(p.aVerdict)].toLowerCase()} in A, {STATE_WORD[stateOf(p.bVerdict)].toLowerCase()} in B.</li>}
                {rangeLine(p) && <li>{rangeLine(p)}</li>}
                {p.measurements.map(measurementLine).filter(Boolean).map((t, i) => <li key={i}>{t}</li>)}
              </ul>
            </li>
          ))}
        </ul>
      )}
      {same.length > 0 && (
        <details className="compare-more">
          <summary>{same.length} {same.length === 1 ? "finding reads" : "findings read"} differently but {same.length === 1 ? "says" : "say"} the same</summary>
          <ul className="compare-list">
            {same.map((p) => <li key={p.a.id}><Verdict v={p.bVerdict} /><FindingLink side={b} claim={p.b} /></li>)}
          </ul>
        </details>
      )}
      <OnlyIn tag="A" items={diff.onlyA} side={a} />
      <OnlyIn tag="B" items={diff.onlyB} side={b} />
    </section>
  );
}

function OnlyIn({ tag, items, side }: { tag: string; items: { claim: Claim; verdict: string }[]; side: Side }) {
  if (!items.length) return null;
  return (
    <details className="compare-more">
      <summary>Only in {tag}: {items.length} {items.length === 1 ? "finding" : "findings"}</summary>
      <ul className="compare-list">
        {items.map((x) => <li key={x.claim.id}><Verdict v={x.verdict} /><FindingLink side={side} claim={x.claim} /></li>)}
      </ul>
    </details>
  );
}

// ---------------------------------------------------------------- recordings on one lab clock

function Lanes({ a, b }: { a: Side; b: Side }) {
  const clocks = useRef([new VideoClock(), new VideoClock()]).current;
  const span = useMemo(() => {
    const ends = [a, b].flatMap((s) => {
      const ft = s.store.media.frameTimes, sr = s.store.media.series[0];
      return ft ? [ft[0], ft[ft.length - 1]] : sr?.t.length ? [sr.t[0], sr.t[sr.t.length - 1]] : [0, 0];
    });
    return [Math.min(...ends), Math.max(...ends)] as const;
  }, [a, b]);
  const [t, setT] = useState(span[0]);
  const long = span[1] - span[0] > 3600;

  const seek = (s: number) => {
    setT(s);
    [a, b].forEach((side, i) => {
      const series = side.store.media.series.find((x) => /volt/i.test(x.name)) ?? side.store.media.series[0];
      const fr = frameAtTime(s, side.store.media.frameTimes, series) ?? Math.round(s * side.store.media.fps);
      clocks[i].pause();
      clocks[i].seek(fr);
    });
  };

  return (
    <section className="compare-lanes" aria-labelledby="cl-h">
      <h2 id="cl-h" className="compare-h2">The recordings</h2>
      <div className="lab-scrub">
        <label htmlFor="lab-t" className="soft small">Lab clock</label>
        <input id="lab-t" type="range" min={span[0]} max={span[1]} step={(span[1] - span[0]) / 1000 || 0.1} value={t}
          onChange={(e) => seek(Number(e.target.value))} aria-valuetext={timeOn(t, long)} />
        <output htmlFor="lab-t" className="lab-now">{timeOn(t, long)}</output>
      </div>
      <div className="lanes">
        <Lane tag="A" side={a} clock={clocks[0]} />
        <Lane tag="B" side={b} clock={clocks[1]} />
      </div>
    </section>
  );
}

function Lane({ tag, side, clock }: { tag: string; side: Side; clock: VideoClock }) {
  const view = side.store.getSnapshot();
  const media = side.store.media;
  const ex = experiment(known(side.folder)!.experiment);
  const series = media.series.find((s) => /volt/i.test(s.name)) ?? media.series[0];
  const moments = useMemo(() => momentsOf(view.findings, side.store.catalog).filter((m) => m.state === "earned"), [view.findings, side.store.catalog]);
  return (
    <div className="lane">
      <p className="lane-name"><span className="side-tag" aria-hidden>{tag}</span>{side.label}</p>
      <div className="evidence-media" style={{ ["--ar" as string]: ex.region ? ex.region.w / ex.region.h : media.videoSize[0] / media.videoSize[1] }}>
        <Stage clock={clock} src={media.video} poster={ex.poster || undefined} size={media.videoSize} fps={media.fps}
          tracks={media.tracks} visible={view.visibleTracks} region={ex.region} closeUp={false} distinct={view.distinctFrames} />
        <Timeline clock={clock} distinct={view.distinctFrames} tracked={null} markers={[]} />
        <SignalPanel clock={clock} series={series} moments={moments} reference={ex.reference} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- steps, aligned

const ID = /^(sha256:[0-9a-f]+|(m|run|claim|cmp|lesson|art|step|q)_[0-9a-f]+)$/;
function argsWord(e: Event): string {
  const args = (e.data?.args ?? {}) as Record<string, unknown>;
  return Object.entries(args)
    .filter(([k, v]) => k !== "code" && !(typeof v === "string" && ID.test(v)) && !k.endsWith("_id") && !k.endsWith("_ids"))
    .map(([k, v]) => `${k.replace(/_/g, " ")} ${Array.isArray(v) ? (v.length > 4 ? `${v.length} items` : v.join(", ")) : typeof v === "object" ? "…" : String(v).slice(0, 40)}`)
    .join(" · ");
}
function stepWord(e: Event): string {
  const frame = (e.data?.args as { frame?: unknown } | undefined)?.frame;
  return isPhrasedTool(e.actor) ? toolPhrase(e.actor, { running: false, frames: typeof frame === "number" ? [frame] : [] }) : String(e.actor).replace(/_/g, " ");
}

function StepCell({ e }: { e?: Event }) {
  if (!e) return <div className="step-cell is-empty" aria-label="No step" />;
  const args = argsWord(e);
  return <div className="step-cell"><span>{stepWord(e)}</span>{args && <span className="step-args">{args}</span>}</div>;
}

function Steps({ diff }: { diff: CampaignDiff; a: Side; b: Side }) {
  const groups = useMemo(() => groupSteps(diff.steps), [diff.steps]);
  const differ = diff.steps.filter((r) => r.kind !== "same").length;
  return (
    <section className="compare-steps" aria-labelledby="cs-h">
      <h2 id="cs-h" className="compare-h2">Steps</h2>
      <p className="soft small">{diff.steps.length - differ} identical, {differ} that differ or happen on one side only. Identical runs are folded.</p>
      <div className="steps-grid" role="table" aria-label="Tool calls, A beside B">
        <div className="steps-row steps-headrow" role="row"><span role="columnheader">A</span><span role="columnheader">B</span></div>
        {groups.map((g, i) => g.kind === "same"
          ? <SameRun key={i} rows={g.rows} />
          : <DifferRow key={i} row={g.row} />)}
      </div>
    </section>
  );
}

function SameRun({ rows }: { rows: StepRow[] }) {
  if (rows.length === 1) return <DifferRow row={rows[0]} />;
  return (
    <details className="steps-same" role="row">
      <summary>{rows.length} identical steps</summary>
      {rows.map((r, i) => <DifferRow key={i} row={r} />)}
    </details>
  );
}

const ROW_WORD: Record<StepRow["kind"], string> = { same: "Same", changed: "Same tool, different settings", a: "Only in A", b: "Only in B" };
function DifferRow({ row }: { row: StepRow }) {
  return (
    <div className={`steps-row kind-${row.kind}`} role="row" title={ROW_WORD[row.kind]}>
      <span role="cell"><StepCell e={"a" in row ? row.a : undefined} /></span>
      <span role="cell"><StepCell e={"b" in row ? row.b : undefined} /></span>
    </div>
  );
}
