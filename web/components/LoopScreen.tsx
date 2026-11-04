"use client";
// An engineering campaign's workspace. Live from the server (/w/?ledger=<lid>&id=<cid>, refreshed on each
// `loop.recorded` event of its cookie-authenticated stream) or a recorded export (/w/?recording=<name>, or a file
// opened here), which has no write controls.
//   Top (above the fold): the current objective summary and constraints, the best result, the next decision.
//   Tabs: Overview (three separate answers: feasibility, speed against a valid reference, objective status; charts),
//   Experiments (a selectable sequence; selecting one updates parameters, prediction vs result, checks and charts),
//   Data (baseline and records), Evidence (comparisons, hashes, timeline, source, export).
import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, loadRecording, newIdempotencyKey, openEvents, v1 } from "@/lib/api";
import {
  assessmentOf, feasibilityWords, objectiveHeader, outcomeWords, seriesLimits, objectiveWords, requestRole, ROLE_TAG, speedWords, type Assessment,
} from "@/lib/assessment";
import {
  actorIn, actorWord, bannerLine, baselineFor, BASIS_WORD, byRecency, canAct, categoriesLine, constraintLine, constraintOf,
  decidedByLimits, executorWord, limitNames, limitValue, num, opWord, OUTCOME_WORD, paramsLine, parseLoopFile, plannerIsAgent,
  plannerWord, predictedLine, prose, RECORDING_NAME, requestTitle, seriesFromBlob, showsH, spendLines, STATE_SHORT, STATE_WORD,
  STATUS_WORD, stateTone, thin, unitWord, validityWarning, valueLine, withUnit, wordsFor,
  bestIsOlderModel, cRatesLine, designCaveat, designHeading, designShown, labTestOf, leadLine, marginLine, nextStepOf, PARAM_RECORDED, paramLine, paramText, traceChart, versionOf,
  bytesWord, engineerConstraints, humanize, idWords, isRecordTest, priorLines, recordStretchLine, spanWord, targetLine, uncertaintyWord,
  type Band, type Investigation, type Comparison, type Design, type Learning, type LoopRequest, type LoopView, type Measurement, type NextAction, type PackedFile, type Recording,
  type Series, type SystemSpecLite, type Words,
} from "@/lib/loop";
import { plainConstraintLabel, relabel } from "@/lib/newcampaign";
import { nextStream, type Stream } from "@/lib/stream";
import { canWrite, session, useSession, type Who } from "@/lib/session";
import { apiConfigured } from "@/lib/sources";
import { useWidth } from "./DecisionMoment";
import { IconCheck, IconClose, IconUpload, Spinner } from "./Icons";
import { ApiProblem, Dialog } from "./Session";
import { PageHeader } from "./Shell";
import { ReturnResult } from "./ReturnResult";
import type { ResultSpec } from "@/lib/result";

type Where = { mode: "live"; ledger: string; id: string } | { mode: "recording"; name: string } | { mode: "file" };

/** Everything the workspace needs about where the view came from. */
interface Origin {
  mode: "live" | "recorded";
  ledger?: string;
  fetchedAt?: number;
  stream?: Stream;
  file?: string;
  receipt?: boolean;
  blobs?: Record<string, PackedFile>;
  refresh?: () => void;
  /** Recorded from a live page's export: go back to the live campaign. */
  back?: () => void;
}

function whereFromQuery(): Where {
  const q = new URLSearchParams(window.location.search);
  const ledger = q.get("ledger"), id = q.get("id"), rec = q.get("recording");
  if (ledger && id) return { mode: "live", ledger, id };
  if (rec && RECORDING_NAME.test(rec)) return { mode: "recording", name: rec };
  return { mode: "file" };
}

export function LoopScreen() {
  const [where, setWhere] = useState<Where | null>(null);
  useEffect(() => { setWhere(whereFromQuery()); }, []);
  if (!where) return <div className="screen loading"><PageHeader title="Campaign" /></div>;
  if (where.mode === "live") return <LiveLoop ledger={where.ledger} id={where.id} />;
  if (where.mode === "recording") return <RecordedLoop name={where.name} />;
  return <OpenFile />;
}

// ---------------------------------------------------------------- live

/** Debounce for refetching after a burst of `loop.recorded` events. */
const REFRESH_MS = 500;
/** Reconnect backoff after the stream closes (server restart, network): 1, 2, 4, 8, then every 10 s. */
const backoff = (n: number) => Math.min(10000, 1000 * 2 ** Math.max(0, n - 1));
function useLiveView(ledger: string, id: string) {
  const { epoch } = useSession();
  const [view, setView] = useState<LoopView | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [fetchedAt, setFetchedAt] = useState(0);
  const [stream, setStream] = useState<Stream>("connecting");
  const [attempt, setAttempt] = useState(0);
  const seen = useRef(0);          // the view's events_cursor: events at or below it are already in the view
  const ticket = useRef(0);        // drops an older response that lands after a newer one
  const concluded = useRef(false); // the server closes a concluded campaign's stream: nothing more will come

  const refresh = useCallback(async () => {
    const mine = ++ticket.current;
    try {
      const v = await v1.view(ledger, id);
      if (mine !== ticket.current) return;
      seen.current = Math.max(seen.current, v.events_cursor);
      concluded.current = v.conclusion != null;
      setView(v);
      setError(null);
      setFetchedAt(Date.now());
    } catch (err) {
      if (mine === ticket.current) setError(err);
    }
  }, [ledger, id]);

  useEffect(() => { refresh(); }, [refresh, attempt, epoch]);

  useEffect(() => {
    let es: EventSource | null = null;
    let debounce: ReturnType<typeof setTimeout> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let st = { stream: "connecting" as Stream, failures: 0 };
    let dropped = false;
    let gone = false;
    const onEvent = (m: MessageEvent) => {
      let seq = Infinity;
      try { seq = (JSON.parse(m.data) as { seq?: number }).seq ?? Infinity; } catch { /* refresh anyway */ }
      if (seq <= seen.current) return;   // the stream replays from seq 0; the view already holds these
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(() => { debounce = null; refresh(); }, REFRESH_MS);
    };
    const connect = () => {
      if (gone) return;
      es = openEvents(ledger, id);
      for (const t of ["loop.recorded", "cost.updated"]) es.addEventListener(t, onEvent as EventListener);
      es.onopen = () => {
        st = nextStream(st, "open", concluded.current);
        setStream(st.stream);
        if (dropped) { dropped = false; refresh(); }   // catch up on what happened while the stream was down
      };
      es.onerror = () => {
        st = nextStream(st, "error", concluded.current);
        setStream(st.stream);
        if (st.stream === "closed") { es?.close(); return; }
        dropped = true;
        // The browser retries a dropped connection itself; a refused one (server restarting, 401, 5xx) is closed for
        // good, so reconnect by hand with backoff, and re-read the view (a 401 there reopens sign-in).
        if (es?.readyState === EventSource.CLOSED) {
          es.close();
          if (retry) clearTimeout(retry);
          retry = setTimeout(() => { retry = null; refresh(); connect(); }, backoff(st.failures));
        }
      };
    };
    connect();
    return () => { gone = true; es?.close(); if (debounce) clearTimeout(debounce); if (retry) clearTimeout(retry); };
  }, [ledger, id, refresh, epoch]);

  return { view, error, fetchedAt, stream, refresh, retry: () => setAttempt((n) => n + 1) };
}

function LiveLoop({ ledger, id }: { ledger: string; id: string }) {
  const live = useLiveView(ledger, id);
  const [exported, setExported] = useState<{ rec: Recording; file: string } | null>(null);
  if (exported) {
    return <Workspace v={exported.rec.view} origin={{ mode: "recorded", file: exported.file, receipt: exported.rec.receipt, blobs: exported.rec.blobs, back: () => setExported(null) }} />;
  }
  if (!live.view) {
    return (
      <div className="screen loading">
        <PageHeader title="Campaign" />
        {!apiConfigured() && live.error ? <p className="lp-plain">This page needs the team server. Set NEXT_PUBLIC_API_BASE, or open a recorded export.</p> : null}
        {live.error ? <ApiProblem error={live.error} what="this campaign" onRetry={live.retry} /> : <WorkspaceSkeleton />}
      </div>
    );
  }
  return (
    <Workspace v={live.view} loadError={live.error} onExported={(rec, file) => setExported({ rec, file })}
      origin={{ mode: "live", ledger, fetchedAt: live.fetchedAt, stream: live.stream, refresh: live.refresh }} />
  );
}

// ---------------------------------------------------------------- recorded

function RecordedLoop({ name }: { name: string }) {
  const [rec, setRec] = useState<Recording | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setError(null);
    loadRecording(name, ctl.signal).then(setRec).catch((e) => { if (!ctl.signal.aborted) setError(e); });
    return () => ctl.abort();
  }, [name, attempt]);
  if (!rec) {
    return (
      <div className="screen loading">
        <PageHeader title="Recorded campaign" />
        {error instanceof Error && !(error instanceof ApiError && error.status === 0) ? <p className="lp-plain" role="alert">{error.message}</p> : null}
        {error ? <ApiProblem error={error} what="this recording" onRetry={() => setAttempt((n) => n + 1)} /> : <WorkspaceSkeleton />}
      </div>
    );
  }
  return <Workspace v={rec.view} origin={{ mode: "recorded", file: `${name.replace(/\.loop\.json$/, "")}.loop.json`, receipt: rec.receipt, blobs: rec.blobs }} />;
}

/** No address: open a `.loop.json` from this computer. Read in the browser only; nothing is sent anywhere. */
function OpenFile() {
  const [rec, setRec] = useState<(Recording & { file: string }) | null>(null);
  const [error, setError] = useState("");
  const open = async (f: File | undefined) => {
    if (!f) return;
    setError("");
    try {
      const r = parseLoopFile(JSON.parse(await f.text()));
      setRec({ ...r, file: f.name });
    } catch (err) {
      setError(err instanceof SyntaxError ? "That file is not JSON." : err instanceof Error ? err.message : "That file couldn’t be read.");
    }
  };
  if (rec) return <Workspace v={rec.view} origin={{ mode: "recorded", file: rec.file, receipt: rec.receipt, blobs: rec.blobs }} />;
  return (
    <div className="screen">
      <PageHeader title="Open a recorded campaign" />
      <main className="lp-main lp-open">
        <p className="lp-plain">Choose a loop export (<code>&lt;name&gt;.loop.json</code>, from <code>translucent export</code> or a campaign’s Export button). It is read in this browser only and shown as a recording.</p>
        <label className="file-pick">
          <IconUpload size={18} />
          <span className="file-pick-label">Choose a .loop.json file</span>
          <input type="file" accept=".json,application/json" onChange={(e) => open(e.target.files?.[0])} />
        </label>
        {error && <p className="lp-error" role="alert">{error}</p>}
        <p className="lp-plain"><Link className="text-link" href="/campaigns/">Campaigns on the server</Link></p>
      </main>
    </div>
  );
}

/** While a campaign loads: the workspace's own geometry in quiet blocks (status strip, three top cards, tabs, panels),
 *  so the page doesn't jump when the view lands. Shown only after a short wait, like the spinner it replaces. */
function WorkspaceSkeleton() {
  const [shown, setShown] = useState(false);
  useEffect(() => { const id = setTimeout(() => setShown(true), 200); return () => clearTimeout(id); }, []);
  return (
    <div className="ws-skel" role="status" aria-live="polite" aria-busy="true">
      <span className="visually-hidden">Loading the campaign</span>
      {shown && (
        <div aria-hidden className="ws-skel-body">
          <div className="ws-skel-status"><i style={{ width: 74 }} /><i style={{ width: 96 }} /><i style={{ width: 64 }} /><i style={{ width: 80 }} /></div>
          <div className="ws-top">
            {[[52, 88, 70], [40, 34, 62], [44, 76, 58]].map((ws, i) => (
              <div key={i} className="ws-card ws-skel-card">
                <i style={{ width: `${ws[0]}%` }} />
                <i className={i === 1 ? "is-big" : "is-title"} style={{ width: `${ws[1]}%` }} />
                <i style={{ width: `${ws[2]}%` }} />
              </div>
            ))}
          </div>
          <div className="ws-skel-tabs"><i style={{ width: 64 }} /><i style={{ width: 88 }} /><i style={{ width: 40 }} /><i style={{ width: 60 }} /></div>
          <div className="ws-answers">
            {[0, 1, 2].map((i) => <div key={i} className="ws-card ws-skel-card is-tall"><i style={{ width: "38%" }} /><i className="is-title" style={{ width: "24%" }} /><i style={{ width: "72%" }} /><i style={{ width: "60%" }} /></div>)}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * True for a moment after `key` changes, never on the first render: a card whose model version or design just changed
 * marks itself so the change reads as a change. Recordings never change after loading, so only a live view shows it.
 */
function useChanged(key: string | number | null | undefined, ms = 1600): boolean {
  const first = useRef(key);
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (key === first.current) return;
    first.current = key;
    setOn(true);
    const id = setTimeout(() => setOn(false), ms);
    return () => clearTimeout(id);
  }, [key, ms]);
  return on;
}

/** A design's charge steps as bars of C-rate: the protocol's shape at a glance. `ghost` draws a second design behind it
 *  (outlined), so a before and after read as one picture. Heights share one scale. */
function StepProfile({ rates, ghost, label, values }: { rates: (number | null)[]; ghost?: (number | null)[] | null; label: string; values?: boolean }) {
  const ok = (x: number | null | undefined): x is number => x != null && Number.isFinite(x);
  const all = [...rates, ...(ghost ?? [])].filter(ok);
  if (!all.length) return null;
  const top = Math.max(...all);
  const n = Math.max(rates.length, ghost?.length ?? 0);
  const pctOf = (x: number) => `${Math.max(4, (x / top) * 100).toFixed(1)}%`;
  return (
    <div className="ws-stepfig" role="img" aria-label={label}>
      {Array.from({ length: n }, (_, i) => {
        const x = rates[i], g = ghost?.[i];
        return (
          <span key={i} className={`ws-stepfig-col ${ok(g) && g === x ? "is-same" : ""}`} style={{ ["--i" as string]: i }}>
            <span className="ws-stepfig-track">
              {ok(g) && <span className="ws-stepfig-bar is-ghost" style={{ height: pctOf(g) }} />}
              {ok(x) && <span className="ws-stepfig-bar" style={{ height: pctOf(x) }} />}
            </span>
            {values && <span className="ws-stepfig-v num" aria-hidden>{ok(x) ? `${num(x)}C` : "–"}</span>}
          </span>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- shared context

const WordsContext = createContext<Words | null>(null);
const useWords = (v: LoopView) => useContext(WordsContext) ?? wordsFor(v);

/** A live campaign's system specs from its ledger (labels, max run time); a recording words things from its ids. */
type SpecWithWall = Omit<SystemSpecLite, "actions"> & { actions: (SystemSpecLite["actions"][number] & { max_wall_s?: number })[] };
function useSpecs(origin: Origin): SpecWithWall[] | null {
  const [specs, setSpecs] = useState<SpecWithWall[] | null>(null);
  useEffect(() => {
    if (origin.mode !== "live" || !origin.ledger) return;
    const ctl = new AbortController();
    v1.systems(origin.ledger, ctl.signal).then((x) => setSpecs(x as SpecWithWall[])).catch(() => { /* the ids as words, then */ });
    return () => ctl.abort();
  }, [origin.mode, origin.ledger]);
  return specs;
}

/** Who may act here: a live campaign, signed in as an engineer or administrator. Why not, when not. */
function useActor(origin: Origin): { able: boolean; who: Who | null; why: ReactNode } {
  const s = useSession();
  const able = canAct(origin.mode, s.who?.role);
  const why = origin.mode !== "live" ? "Recorded: read only."
    : !s.who ? <><button type="button" className="text-link" onClick={() => session.ask()}>Sign in</button> to decide.</>
      : !canWrite(s.who) ? `Signed in as a ${s.who.role}: read only.` : null;
  return { able, who: s.who, why };
}

const upperFirst = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const approvedWord = (actor: string) => (actor.startsWith("policy:") && actor !== "policy:registration" ? "auto-approved by policy" : `approved by ${actorWord(actor)}`);

/** The planner as the chip names it: the exact model, or "Bayesian optimisation, no model". */
export function plannerChip(v: Pick<LoopView, "campaign">): string {
  const { kind, models } = v.campaign.planner;
  if (kind === "domain_baseline") return "Bayesian optimisation, no model";
  if (kind === "muse" || kind === "muse_no_feedback") return models.planner ? `${plannerWord(v)} · model ${models.planner}` : plannerWord(v);
  if (kind === "external") return `Your agent${models.planner ? `: ${models.planner}` : ""}`;
  if (kind === "scripted") return "Scripted planner, no model";
  return models.planner ?? kind;
}

/** A comparison's verdict for a row: a /2 feasibility verdict is said as such, never "better" or "faster". */
function verdictChip(c: Comparison): { text: string; tone: "good" | "bad" | "off" } {
  if (decidedByLimits(c)) return { text: "Meets the limits the baseline breaks", tone: "good" };
  if (c.verdict === "candidate_better") return { text: "Better than the baseline", tone: "good" };
  if (c.verdict === "violates_constraints") return { text: "Breaks a limit", tone: "bad" };
  if (c.verdict === "not_better") return { text: "Not better", tone: "off" };
  if (c.verdict === "inconclusive") return { text: "Inconclusive", tone: "off" };
  return { text: "Not comparable", tone: "off" };
}

/** The comparison recorded for a request: on the current objective version if there is one, else its latest. */
function comparisonOf(v: LoopView, requestId: string): Comparison | null {
  const all = v.comparisons.filter((c) => c.request_id === requestId);
  return all.find((c) => c.objective_version === v.objective.version) ?? all[all.length - 1] ?? null;
}

// ---------------------------------------------------------------- the workspace

type Tab = "overview" | "experiments" | "data" | "evidence";
const TABS: [Tab, string][] = [["overview", "Overview"], ["experiments", "Experiments"], ["data", "Data"], ["evidence", "Evidence"]];

function Workspace({ v, origin, loadError, onExported }: { v: LoopView; origin: Origin; loadError?: unknown; onExported?: (rec: Recording, file: string) => void }) {
  useEffect(() => { document.title = `${v.campaign.title} – Translucent`; }, [v.campaign.title]);
  const specs = useSpecs(origin);
  const words = useMemo(() => wordsFor(v, specs), [v, specs]);
  const { a, derived } = useMemo(() => assessmentOf(v), [v]);
  const [tab, setTab] = useState<Tab>("overview");
  const [sel, setSel] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"" | "amend" | "conclude">("");
  const actor = useActor(origin);
  const experiments = useMemo(() => v.requests, [v]);
  const fallbackSel = (v.next_action.kind === "awaiting_approval" || v.next_action.kind === "running" || v.next_action.kind === "awaiting_result")
    ? v.next_action.request_id : a.feasibility.best_request_id ?? byRecency(v).find((r) => r.result)?.request_id ?? experiments[experiments.length - 1]?.request_id ?? null;
  const selected = experiments.find((r) => r.request_id === sel) ?? experiments.find((r) => r.request_id === fallbackSel) ?? null;
  const openExperiment = (id: string) => { setSel(id); setTab("experiments"); };

  return (
    <WordsContext.Provider value={words}>
      <div className={`screen ws-screen is-${origin.mode}`}>
        <PageHeader title={v.campaign.title}>
          {origin.back && <button type="button" className="pill-link" onClick={origin.back}>Back to the live campaign</button>}
          {origin.mode === "live" && origin.ledger && <ExportButton v={v} ledger={origin.ledger} onOpen={onExported} />}
        </PageHeader>
        <StatusBar v={v} a={a} origin={origin} />
        <main className="ws-main">
          {loadError ? <p className="ws-note is-warn" role="status">Couldn’t refresh: {loadError instanceof ApiError ? (loadError.unreachable ? "the server didn’t answer" : loadError.message) : "the server didn’t answer"}. Showing the last view; it catches up when the server is back.</p> : null}
          <section className="ws-top" aria-label="Campaign at a glance">
            <ObjectiveCard v={v} canAmend={actor.able && !v.conclusion} onAmend={() => setDialog("amend")} />
            <BestCard v={v} a={a} onOpen={openExperiment} />
            <DecisionCard v={v} a={a} origin={origin} actor={actor} onConclude={() => setDialog("conclude")} onOpen={openExperiment} />
          </section>
          <TabBar tab={tab} setTab={setTab} counts={{ experiments: v.requests.length }} />
          <div className="ws-panel" role="tabpanel" id={`ws-panel-${tab}`} aria-labelledby={`ws-tab-${tab}`} tabIndex={0}>
            {tab === "overview" && <Overview v={v} a={a} derived={derived} origin={origin} onOpen={openExperiment} />}
            {tab === "experiments" && <Experiments v={v} origin={origin} selected={selected} onSelect={setSel} actor={actor} />}
            {tab === "data" && <DataTab v={v} origin={origin} />}
            {tab === "evidence" && <Evidence v={v} a={a} derived={derived} origin={origin} />}
          </div>
        </main>
        {dialog === "amend" && origin.ledger && <AmendDialog v={v} ledger={origin.ledger} onClose={() => setDialog("")} onDone={() => { setDialog(""); origin.refresh?.(); }} />}
        {dialog === "conclude" && origin.ledger && <ConcludeDialog v={v} a={a} ledger={origin.ledger} onClose={() => setDialog("")} onDone={() => { setDialog(""); origin.refresh?.(); }} />}
      </div>
    </WordsContext.Provider>
  );
}

function TabBar({ tab, setTab, counts }: { tab: Tab; setTab(t: Tab): void; counts: { experiments: number } }) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const onKey = (e: React.KeyboardEvent) => {
    const i = TABS.findIndex(([t]) => t === tab);
    const next = e.key === "ArrowRight" ? (i + 1) % TABS.length : e.key === "ArrowLeft" ? (i - 1 + TABS.length) % TABS.length
      : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    setTab(TABS[next][0]);
    refs.current[TABS[next][0]]?.focus();
  };
  return (
    <div className="ws-tabs" role="tablist" aria-label="Campaign" onKeyDown={onKey}>
      {TABS.map(([t, label]) => (
        <button key={t} ref={(el) => { refs.current[t] = el; }} id={`ws-tab-${t}`} role="tab" type="button" aria-selected={tab === t} aria-controls={`ws-panel-${t}`}
          tabIndex={tab === t ? 0 : -1} className={`ws-tab ${tab === t ? "is-on" : ""}`} onClick={() => setTab(t)}>
          {label}{t === "experiments" && counts.experiments ? <span className="ws-tab-n num">{counts.experiments}</span> : null}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- status chips

function StatusBar({ v, a, origin }: { v: LoopView; a: Assessment; origin: Origin }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (origin.mode !== "live") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [origin.mode]);
  const s = spendLines(v.spend);
  const conn = origin.mode === "recorded" ? { text: "Recorded file", tone: "rec" }
    : origin.stream === "live" ? { text: "Live, connected", tone: "ok" }
      : origin.stream === "closed" ? { text: "Live, concluded", tone: "ok" }
        : origin.stream === "reconnecting" ? { text: "Reconnecting…", tone: "warn" }
          : origin.stream === "offline" ? { text: "Offline, retrying", tone: "bad" } : { text: "Connecting…", tone: "warn" };
  const rec = origin.mode === "recorded" ? bannerLine("recorded", v, { file: origin.file, receipt: origin.receipt }) : null;
  const since = origin.fetchedAt ? Math.max(0, Math.round((now - origin.fetchedAt) / 1000)) : null;
  return (
    <div className={`ws-status is-${origin.mode}`} role="status" aria-live="polite" aria-label="Status">
      {origin.mode === "recorded" && <span className="ws-rec-tag">RECORDED</span>}
      <span className={`ws-chip is-conn is-${conn.tone}`} title="Connection">{conn.tone === "ok" && <span className="lp-live-dot" aria-hidden />}{conn.tone === "warn" && <Spinner size={11} />}{conn.text}</span>
      <span className="ws-chip" title="Campaign state">{STATUS_WORD[v.campaign.status] ?? v.campaign.status}</span>
      <span className="ws-chip" title="Planner">{plannerChip(v)}</span>
      <span className={`ws-chip is-basis is-${a.basis}`} title="Basis of the results">{a.basis === "computational" ? "Simulated" : a.basis === "physical" ? "Operator-uploaded" : "From records"}</span>
      <span className="ws-status-spend num">{s.experiments} experiments{v.spend.model_budget_usd > 0 ? ` · model ${s.model}` : ""}</span>
      <span className="ws-status-src">
        {origin.mode === "live" ? <>{origin.ledger}{since != null ? ` · updated ${since < 60 ? `${since} s` : `${Math.round(since / 60)} min`} ago` : ""}</> : rec?.line}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------- the top: objective, best result, next decision

function ObjectiveCard({ v, canAmend, onAmend }: { v: LoopView; canAmend: boolean; onAmend(): void }) {
  const o = v.objective;
  const h = objectiveHeader(v);
  const amendedNow = new Set((o.amendments[o.amendments.length - 1]?.changes ?? []).map((c) => c.split(" ")[0]));
  return (
    <section className="ws-card ws-objective" aria-labelledby="ws-obj-h">
      <p className="ws-card-k">Objective{o.version > 1 ? ` · version ${o.version}` : ""}{o.kind === "hard_target" ? " · hard target" : ""}</p>
      <h1 className="ws-summary" id="ws-obj-h">{prose(h.summary)}</h1>
      <ul className="ws-cons-chips" aria-label="Current constraints">
        {o.constraints.map((c) => (
          <li key={c.quantity} className="ws-con-chip">
            <span>{constraintLine(c)}</span>
            {amendedNow.has(c.quantity) && <span className="ws-amended" title={o.amendments[o.amendments.length - 1]?.reason}>amended v{o.version}</span>}
          </li>
        ))}
        {o.target.limit != null && <li className="ws-con-chip is-target">Hard target {opWord(o.target.direction === "maximize" ? ">=" : "<=")} {withUnit(o.target.limit, o.target.unit)}</li>}
      </ul>
      {h.registered && (
        <details className="ws-registered">
          <summary>As registered, v{h.registered.version}</summary>
          <p>{prose(h.registered.text)}</p>
        </details>
      )}
      {canAmend && <button type="button" className="chip-btn ws-amend-btn" onClick={onAmend}>Amend a constraint…</button>}
    </section>
  );
}

/** With model versions, the best result is the learning's best design: made under a version, never the incumbent. */
function BestDesignCard({ v, d, lead, onOpen }: { v: LoopView; d: Design; lead?: Design | null; onOpen(id: string): void }) {
  const ad = adequacyWord(d.model_adequate);
  const req = d.request_id ? v.requests.find((r) => r.request_id === d.request_id) : undefined;
  const changed = useChanged(`${d.model_version}:${d.request_id ?? ""}:${d.charge_time_80 ?? ""}`);
  return (
    <section className={`ws-card ws-best ${changed ? "is-updated" : ""}`} aria-labelledby="ws-best-h">
      <p className="ws-card-k" id="ws-best-h">{designHeading(d)} (simulated lab, under v{d.model_version})</p>
      <p className="ws-big num">{withUnit(d.charge_time_80, "min")}</p>
      <p className="ws-card-sub">Time 10 to 80 % SOC, slowest member{req ? <> · <button type="button" className="text-link" onClick={() => onOpen(req.request_id)}>#{v.requests.indexOf(req) + 1}</button></> : null}</p>
      <p className="ws-card-sub num">Steps {cRatesLine(d.c_rates)}</p>
      <p className="ws-card-sub"><span className={`ws-verdict is-${ad.tone}`}>Model v{d.model_version}: {ad.text.toLowerCase()}</span></p>
      {designCaveat(d) && <p className="lp-warn" role="note">{designCaveat(d)}</p>}
      {lead && <p className="lp-warn" role="note">{leadLine(lead)}</p>}
      <p className="ws-card-foot num">Anode potential {marginLine(d)} · max {withUnit(d.max_temperature, "degC")}. Both are forecasts for the worst member; the anode potential is never measured.</p>
    </section>
  );
}

function BestCard({ v, a, onOpen }: { v: LoopView; a: Assessment; onOpen(id: string): void }) {
  const shown = designShown(v.learning);
  if (shown.main) return <BestDesignCard v={v} d={shown.main} lead={shown.lead} onOpen={onOpen} />;
  const t = v.objective.target;
  const id = a.feasibility.best_request_id;
  const c = id ? comparisonOf(v, id) : null;
  const req = id ? v.requests.find((r) => r.request_id === id) : undefined;
  const base = baselineFor(v, a.basis);
  const words = useWords(v);
  const n = req ? v.requests.indexOf(req) + 1 : 0;
  return (
    <section className="ws-card ws-best" aria-labelledby="ws-best-h">
      <p className="ws-card-k" id="ws-best-h">Best result found{a.basis === "computational" ? " (simulated)" : ""}</p>
      {c && c.target.candidate != null && a.feasibility.best === "meets" ? (
        <>
          <p className="ws-big num">{withUnit(c.target.candidate, t.unit)}</p>
          <p className="ws-card-sub">{t.label || words.quantity(t.quantity)} · <button type="button" className="text-link" onClick={() => onOpen(id!)}>#{n} {req ? paramsLine(req.params, words) : id}</button></p>
          <Checks v={v} checks={c.constraints} />
        </>
      ) : (
        <>
          <p className="ws-big is-none">None feasible yet</p>
          <p className="ws-card-sub">{v.comparisons.length ? "No candidate meets every constraint so far." : "No experiment has returned a result yet."}</p>
          {/* with none feasible, the assessment's best is the closest attempt: shown as that, never as a result */}
          {c && c.target.candidate != null && (
            <>
              <p className="ws-card-sub">Closest so far: <button type="button" className="text-link" onClick={() => onOpen(id!)}>#{n} {req ? paramsLine(req.params, words) : id}</button>, {withUnit(c.target.candidate, t.unit)}</p>
              <Checks v={v} checks={c.constraints} />
            </>
          )}
        </>
      )}
      {base && (
        <p className="ws-card-foot">
          Baseline {base.values[t.quantity] ? valueLine(base.values[t.quantity]) : "–"}
          {a.feasibility.baseline === "fails" ? <span className="ws-bad"> · breaks {base.constraints.filter((x) => x.ok === false).length} limit{base.constraints.filter((x) => x.ok === false).length === 1 ? "" : "s"}</span>
            : a.feasibility.baseline === "meets" ? <span className="ws-good"> · meets the limits</span> : null}
        </p>
      )}
      <p className="ws-card-foot">Speed: {speedWords(a).answer.toLowerCase()}</p>
    </section>
  );
}

function Checks({ v, checks }: { v: LoopView; checks: Comparison["constraints"] }) {
  const words = useWords(v);
  return (
    <ul className="ws-checks">
      {checks.map((c) => {
        const con = constraintOf(v, c.quantity);
        return (
          <li key={c.quantity} className={c.ok === true ? "is-ok" : c.ok === false ? "is-fail" : ""}>
            <span className="ws-mark" aria-label={c.ok === true ? "holds" : c.ok === false ? "broken" : "too close to call"}>{c.ok === true ? <IconCheck size={13} /> : c.ok === false ? <IconClose size={13} /> : "?"}</span>
            <span className="ws-check-q">{words.quantity(c.quantity)}</span>
            <span className="num">{limitValue(c.value, c.limit, con?.unit ?? "")}</span>
            <span className="ws-muted num">{con ? `${opWord(con.op)} ${withUnit(c.limit, con.unit)}` : ""}</span>
          </li>
        );
      })}
    </ul>
  );
}

function useElapsed(since: number | null): number | null {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (since == null) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [since]);
  return since == null ? null : Math.max(0, now - since);
}
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function DecisionCard({ v, a, origin, actor, onConclude, onOpen }: { v: LoopView; a: Assessment; origin: Origin; actor: ReturnType<typeof useActor>; onConclude(): void; onOpen(id: string): void }) {
  const words = useWords(v);
  const n = v.next_action;
  const req = "request_id" in n ? v.requests.find((r) => r.request_id === n.request_id) : undefined;
  const idx = req ? v.requests.indexOf(req) + 1 : 0;
  const runningSince = req?.state === "running" ? [...req.timeline].reverse().find((s) => s.state === "running")?.t ?? null : null;
  const elapsed = useElapsed(runningSince);
  const specs = useSpecs(origin);
  const maxWall = req ? specs?.find((s) => s.id === v.campaign.system.id)?.actions.find((x) => x.name === req.action)?.max_wall_s : undefined;
  const concludeBtn = actor.able && !v.conclusion ? <button type="button" className="btn btn-small btn-quiet" onClick={onConclude}>Conclude…</button> : null;

  let body: ReactNode;
  if (v.conclusion) {
    body = (
      <>
        <p className="ws-card-k">Concluded</p>
        <p className={`ws-outcome is-${outcomeWords(v, a).recorded ? "feasible_only" : v.conclusion.outcome}`}>{outcomeWords(v, a).word}</p>
        <p className="ws-card-sub">{outcomeWords(v, a).recorded ? <>Recorded by an earlier comparator as <code>{outcomeWords(v, a).recorded}</code>: the limits decided it, not speed. </> : null}Computed by the server from the recorded comparisons; see Overview for the three answers.</p>
      </>
    );
  } else if (n.kind === "awaiting_approval") {
    body = (
      <>
        <p className="ws-card-k is-ask">Decision needed · request #{idx} · version {n.version}</p>
        <h2 className="ws-decide-title">{req ? paramsLine(req.params, words) || requestTitle(req, words) : n.title}</h2>
        <p className="ws-card-sub">{req ? `Proposed by ${actorIn(v, req.proposed_by)}` : ""} · {executorWord(n.executor)}{n.cost?.wall_s != null ? `, about ${withUnit(Number(n.cost.wall_s), "s")}` : ""}</p>
        <PredictionLine v={v} n={n} />
        <p className="ws-hash">Version {n.version} · content <code title={n.content_hash}>{n.content_hash.slice(0, 12)}</code> · <button type="button" className="text-link" onClick={() => onOpen(n.request_id)}>details</button></p>
        {actor.able ? <Decide v={v} requestId={n.request_id} version={n.version} hash={n.content_hash} origin={origin} /> : <p className="ws-muted">{actor.why}</p>}
      </>
    );
  } else if (n.kind === "running" || (req && req.state === "queued")) {
    const run = req?.state === "running";
    body = (
      <>
        <p className="ws-card-k is-busy"><Spinner size={12} /> {run ? "Solver running" : "Approved, queued for the solver"} · request #{idx}</p>
        <h2 className="ws-decide-title">{req ? paramsLine(req.params, words) : "title" in n ? n.title : ""}</h2>
        <RunSteps r={req} />
        {run && elapsed != null && (
          <p className="ws-card-sub num">Running for {clock(elapsed)}{maxWall ? ` · stops at ${clock(maxWall)} at most` : ""} · progress arrives over the live stream</p>
        )}
        {req?.approved_by && <p className="ws-muted">{upperFirst(approvedWord(req.approved_by))}</p>}
      </>
    );
  } else if (n.kind === "awaiting_result") {
    body = (
      <>
        <p className="ws-card-k is-ask">Waiting for a person’s result · request #{idx}</p>
        <h2 className="ws-decide-title">{req ? paramsLine(req.params, words) : n.title}</h2>
        <p className="ws-card-sub">Approved. A qualified person runs it within the lab’s rules and returns the logged file; the server checks it against this request before the campaign continues from it.</p>
        {origin.mode === "live" && origin.ledger
          ? <ReturnResult ledger={origin.ledger} cid={v.campaign.id} requestId={n.request_id} words={words}
              spec={specs?.find((s) => s.id === v.campaign.system.id) as unknown as ResultSpec | undefined}
              able={actor.able} why={actor.why} onDone={() => origin.refresh?.()} />
          : null}
      </>
    );
  } else if (n.kind === "planner_stopped") {
    body = (
      <>
        <p className="ws-card-k is-ask">The planner stopped · model budget</p>
        <p className="ws-card-sub">{prose(n.title)}.</p>
        <p className="ws-card-sub">Nothing runs until a person decides: conclude with what is recorded, or an administrator raises the model budget (an amendment with a budget, over the API).</p>
      </>
    );
  } else if (n.kind === "planner_thinking") {
    body = (
      <>
        <p className="ws-card-k is-busy"><Spinner size={12} /> {plannerIsAgent(v) ? "The agent is choosing" : "Choosing"} the next experiment</p>
        <p className="ws-card-sub">{n.title}</p>
      </>
    );
  } else {
    body = (
      <>
        <p className="ws-card-k">Next</p>
        <p className="ws-card-sub">Nothing is pending. {upperFirst(plannerIsAgent(v) ? "the agent" : plannerWord(v))} proposes the next experiment when its turn comes{v.spend.experiments_budget && v.spend.experiments_used >= v.spend.experiments_budget ? "; the experiment budget is spent, so conclude when ready" : ""}.</p>
      </>
    );
  }
  const shownRunning = (n.kind === "running" || req?.state === "queued") && req ? req.request_id : null;
  const wallOf = (r: LoopRequest) => specs?.find((s) => s.id === v.campaign.system.id)?.actions.find((x) => x.name === r.action)?.max_wall_s;
  return (
    <section className={`ws-card ws-decision ${n.kind === "awaiting_approval" && !v.conclusion ? "is-ask" : ""}`} id="lp-next" aria-label="Next decision">
      {!v.conclusion && <InProgress v={v} exclude={shownRunning} onOpen={onOpen} wallOf={wallOf} />}
      {body}
      {concludeBtn && <div className="ws-decision-foot">{concludeBtn}</div>}
    </section>
  );
}

/** Solver work under way besides the decision shown: each running or queued request, its step and its run time. */
function InProgress({ v, exclude, onOpen, wallOf }: { v: LoopView; exclude: string | null; onOpen(id: string): void; wallOf(r: LoopRequest): number | undefined }) {
  const active = v.requests.filter((r) => (r.state === "running" || r.state === "queued") && r.request_id !== exclude);
  if (!active.length) return null;
  return (
    <ul className="ws-inprog" aria-label="In progress">
      {active.map((r) => <InProgressRow key={r.request_id} v={v} r={r} onOpen={onOpen} maxWall={wallOf(r)} />)}
    </ul>
  );
}

function InProgressRow({ v, r, onOpen, maxWall }: { v: LoopView; r: LoopRequest; onOpen(id: string): void; maxWall?: number }) {
  const words = useWords(v);
  const since = r.state === "running" ? [...r.timeline].reverse().find((s) => s.state === "running")?.t ?? null : null;
  const elapsed = useElapsed(since);
  const role = requestRole(r);
  return (
    <li className="ws-inprog-row">
      <Spinner size={12} />
      <button type="button" className="text-link" onClick={() => onOpen(r.request_id)}>#{v.requests.indexOf(r) + 1}</button>
      <span className="ws-inprog-what">{r.state === "running" ? "Solver running" : "Queued"}{ROLE_TAG[role] ? ` · ${ROLE_TAG[role].toLowerCase()}` : ""} · {paramsLine(r.params, words)}</span>
      {elapsed != null && <span className="num ws-muted">{clock(elapsed)}{maxWall ? ` of ≤ ${clock(maxWall)}` : ""}</span>}
    </li>
  );
}

function PredictionLine({ v, n }: { v: LoopView; n: Extract<NextAction, { kind: "awaiting_approval" }> }) {
  const words = useWords(v);
  const pred = Object.entries(n.prediction);
  if (!pred.length) return null;
  return (
    <p className="ws-pred-line num">
      Predicts {pred.slice(0, 3).map(([q, p]) => `${words.quantity(q).toLowerCase()} ${predictedLine(p)}`).join(" · ")}
    </p>
  );
}

/** Approved → queued → running → result → compared, with the step it is on. */
function RunSteps({ r }: { r?: LoopRequest }) {
  if (!r) return null;
  const has = (s: string) => r.timeline.some((x) => x.state === s);
  const steps: [string, boolean][] = [
    ["Approved", !!r.approved_by || has("queued")], ["Queued", has("queued")], ["Running", has("running") || r.state === "running"],
    ["Result", !!r.result || r.state === "completed"], ["Compared", r.state === "completed"],
  ];
  return (
    <ol className="ws-steps" aria-label="Run progress">
      {steps.map(([label, done]) => <li key={label} className={done ? "is-done" : ""}>{label}</li>)}
    </ol>
  );
}

/** Approve or reject one version, by the content hash the person read. A stale hash is a 409 naming the new version. */
function Decide({ v, requestId, version, hash, origin }: { v: LoopView; requestId: string; version: number; hash: string; origin: Origin }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"" | "approve" | "reject">("");
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const idem = useRef<Record<string, string>>({});
  useEffect(() => { setRejecting(false); setReason(""); idem.current = {}; }, [hash]);
  useEffect(() => { setNote(null); }, [requestId]);
  const ledger = origin.ledger!;
  const act = async (what: "approve" | "reject") => {
    setBusy(what);
    setNote(null);
    const k = idem.current[what] ??= newIdempotencyKey();
    try {
      if (what === "approve") await v1.approve(ledger, v.campaign.id, requestId, { content_hash: hash }, k);
      else await v1.reject(ledger, v.campaign.id, requestId, { content_hash: hash, reason: reason.trim() }, k);
      setNote({ text: what === "approve" ? `Approved version ${version}. Recorded.` : `Rejected version ${version}. Recorded.`, error: false });
      setRejecting(false);
      origin.refresh?.();
    } catch (err) {
      const e = err instanceof ApiError ? err : null;
      if (e?.stale) {
        const now = await v1.request(ledger, v.campaign.id, requestId).catch(() => null);
        setNote({ text: `This request changed after you read version ${version}${now ? `: it is now version ${now.version} (${now.state.replace(/_/g, " ")}, content ${now.content_hash.slice(0, 12)})` : ""}. The latest is shown; read it again before deciding.`, error: true });
        origin.refresh?.();
      } else if (e?.status === 403) {
        setNote({ text: `Refused: ${e.message}`, error: true });
      } else if (e?.status === 401) {
        setNote({ text: "Your session ended. Sign in, then decide again.", error: true });
      } else {
        setNote({ text: e ? (e.unreachable ? "The server didn’t answer. Nothing was recorded; try again." : e.message) : "That didn’t go through.", error: true });
      }
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="ws-decide">
      {rejecting ? (
        <form className="ws-reject" onSubmit={(e) => { e.preventDefault(); if (reason.trim()) act("reject"); }}>
          <label className="visually-hidden" htmlFor={`why-${requestId}`}>Why reject it</label>
          <input id={`why-${requestId}`} className="field" placeholder="Why not? The planner reads this." value={reason} onChange={(e) => setReason(e.target.value)} autoFocus maxLength={2000}
            onKeyDown={(e) => { if (e.key === "Escape") setRejecting(false); }} />
          <button className="btn btn-small" type="submit" disabled={!reason.trim() || !!busy}>{busy === "reject" ? <Spinner size={14} /> : null}Reject v{version}</button>
          <button className="btn btn-small btn-quiet" type="button" onClick={() => setRejecting(false)}>Back</button>
        </form>
      ) : (
        <div className="ws-decide-row">
          <button className="btn ws-approve" onClick={() => act("approve")} disabled={!!busy}>{busy === "approve" ? <Spinner size={14} /> : <IconCheck size={16} />}Approve v{version} and run</button>
          <button className="btn btn-quiet" onClick={() => setRejecting(true)} disabled={!!busy}>Reject…</button>
        </div>
      )}
      {note && <p className={note.error ? "lp-error" : "lp-ok"} role={note.error ? "alert" : "status"}>{note.text}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- overview

function Overview({ v, a, derived, origin, onOpen }: { v: LoopView; a: Assessment; derived: boolean; origin: Origin; onOpen(id: string): void }) {
  const f = feasibilityWords(a), s = speedWords(a), o = objectiveWords(a, v);
  const best = a.feasibility.best_request_id ? v.requests.find((r) => r.request_id === a.feasibility.best_request_id) : undefined;
  const charted = best?.result?.series.length ? best : byRecency(v).find((r) => r.result?.series.length && requestRole(r) === "experiment");
  return (
    <div className="ws-overview">
      {v.investigation && <InvestigationSection v={v} inv={v.investigation} onOpen={onOpen} />}
      {v.learning && <LearningSection v={v} l={v.learning} />}
      <section aria-labelledby="ws-assess-h">
        <div className="ws-section-head">
          <h2 className="ws-h2" id="ws-assess-h">Assessment</h2>
          {derived && <span className="ws-muted">Derived from the recorded comparisons: this file predates assessments.</span>}
        </div>
        <p className="ws-headline">{prose(a.headline)}</p>
        {bestIsOlderModel(v, a.feasibility.best_request_id) && (
          <p className="ws-card-sub ws-older-note">The assessment ranks every simulated result, including forecasts from older model versions; the design under the current model is shown above.</p>
        )}
        <div className="ws-answers">
          <Answer k="Feasibility" w={f} />
          <Answer k="Speed against a reference meeting the same constraints" w={s} />
          <Answer k="Objective" w={o} />
        </div>
        <dl className="ws-uncert">
          <div><dt>Solver tolerance (numerical)</dt><dd>{a.uncertainty.solver ? [a.uncertainty.solver.rtol != null ? `rtol ${a.uncertainty.solver.rtol}` : "", a.uncertainty.solver.atol != null ? `atol ${a.uncertainty.solver.atol}` : "", a.uncertainty.solver.note ? prose(a.uncertainty.solver.note) : ""].filter(Boolean).join(" · ") : "Not stated"}</dd></div>
          <div><dt>Model uncertainty (against a real cell)</dt><dd>{prose(a.uncertainty.model)}</dd></div>
        </dl>
        {a.lines.length > 0 && <Expand label="The assessment, line by line"><ul className="ws-lines">{a.lines.map((l) => <li key={l}>{prose(l)}</li>)}</ul></Expand>}
      </section>

      {charted && (
        <section aria-labelledby="ws-chart-h">
          <div className="ws-section-head">
            <h2 className="ws-h2" id="ws-chart-h">Baseline against {charted !== best ? "the latest result" : a.feasibility.best === "meets" ? "the best feasible candidate" : "the closest candidate (not feasible)"}</h2>
            <button type="button" className="text-quiet" onClick={() => onOpen(charted.request_id)}>#{v.requests.indexOf(charted) + 1} in Experiments</button>
          </div>
          <SeriesPanel v={v} r={charted} origin={origin} />
        </section>
      )}

      {v.conclusion && <ConclusionCard v={v} a={a} />}

      {v.objective.amendments.length > 0 && (
        <section aria-labelledby="ws-amend-h">
          <h2 className="ws-h2" id="ws-amend-h">Amendments</h2>
          <ol className="ws-amends">
            {v.objective.amendments.map((m) => (
              <li key={m.seq}><span className="ws-chip">v{m.version}</span> {m.changes.map(prose).join("; ") || "no change"} · by {actorWord(m.author.includes(":") ? m.author : `person:${m.author}`)} · <span className="ws-muted">{prose(m.reason)}</span></li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- investigation (a campaign opened from raw records)

const TEST_KIND: Record<string, string> = { record: "Record test", lab: "Lab charge", bench: "Bench test" };

/** What came from the engineer's files, what the model assumes and what the engineer set, kept apart; and what the
 *  agent says it could not establish, first. Nothing here is a calibration: the files say only what they say. */
function InvestigationSection({ v, inv, onOpen }: { v: LoopView; inv: Investigation; onOpen(id: string): void }) {
  const missing = inv.missing_information ?? [];
  const facts = inv.instrument_facts ?? [];
  const tests = inv.tests ?? [];
  const segs = Object.entries(inv.segments ?? {});
  const ma = inv.model_assumptions;
  const prior = priorLines(ma?.prior);
  const families = Object.entries(ma?.families ?? {});
  const states = ma?.record_test_states ?? [];
  const cons = engineerConstraints(v);
  const t = v.objective.target;
  return (
    <section className="ws-inv" aria-labelledby="ws-inv-h">
      <div className="ws-section-head">
        <h2 className="ws-h2" id="ws-inv-h">Investigation</h2>
        <span className="ws-muted">Started from the engineer’s own records. What the files show, what the model assumes and what the engineer set are kept apart.</span>
      </div>

      <section className={`ws-card ws-inv-missing ${missing.length ? "is-open" : ""}`} aria-labelledby="ws-miss-h">
        <p className="ws-card-k" id="ws-miss-h">Missing information{missing.length ? ` · ${missing.length} for the engineer` : ""}</p>
        {missing.length ? (
          <ul className="ws-inv-todo">
            {missing.map((m, i) => <li key={`${m.item}-${i}`}><strong>{upperFirst(prose(m.item))}</strong>{m.why_it_matters ? <span className="ws-muted"> · matters for {prose(m.why_it_matters)}</span> : null}</li>)}
          </ul>
        ) : <p className="ws-card-sub">{facts.length ? "The agent has listed nothing as missing." : "Nothing listed yet: the agent lists what the files and documentation do not establish when it imports them."}</p>}
      </section>

      <div className="ws-inv-grid">
        <section className="ws-card ws-inv-part ws-inv-records" aria-labelledby="ws-inv-rec-h">
          <h3 className="ws-inv-h" id="ws-inv-rec-h">From the records</h3>
          <div className="ws-inv-block">
            <p className="ws-h3">Uploaded files</p>
            {inv.uploads?.length ? (
              <ul className="ws-inv-files">
                {inv.uploads.map((u) => <li key={u.name}><code className="ws-wrap">{u.name}</code> <span className="ws-muted num">{bytesWord(u.bytes)}</span>{u.sha256 ? <span className="ws-muted ws-inv-hash" title={u.sha256}> · {u.sha256.replace(/^sha256:/, "").slice(0, 12)}</span> : null}</li>)}
              </ul>
            ) : <p className="ws-card-sub">No files recorded.</p>}
          </div>
          {inv.documentation ? <Expand label="Documentation, as the engineer wrote it"><p className="ws-pre ws-wrap">{inv.documentation}</p></Expand> : <p className="ws-muted">No documentation was given.</p>}
          <div className="ws-inv-block">
            <p className="ws-h3">Instrument facts</p>
            {facts.length === 0 && <p className="ws-card-sub">None yet: the agent has not imported the files. Until it does, nothing is established from them.</p>}
            {facts.map((f) => (
              <div key={f.record} className="ws-inv-record">
                <p className="ws-card-sub"><strong>Record {f.record}</strong> · <span className="ws-wrap">{f.ref}</span>{f.specimen?.id ? ` · specimen ${f.specimen.id}` : ""}
                  {f.time?.source ? <span className="ws-muted"> · time from “{f.time.source}”{f.time.origin ? `, origin ${f.time.origin}` : ""}</span> : null}</p>
                {(f.channels?.length ?? 0) > 0 && (
                  <div className="ws-table-wrap">
                    <table className="ws-table is-tight">
                      <thead><tr><th scope="col">Quantity</th><th scope="col">Column</th><th scope="col">Unit</th><th scope="col">Points</th><th scope="col">Span</th><th scope="col">Uncertainty</th></tr></thead>
                      <tbody>
                        {f.channels!.map((c) => (
                          <tr key={`${c.quantity}-${c.column}`}>
                            <th scope="row">{idWords(c.quantity)}</th>
                            <td>{c.column ?? "–"}</td>
                            <td>{c.unit ? unitWord(c.unit) || c.unit : "–"}</td>
                            <td className="num">{c.n ?? "–"}</td>
                            <td className="num">{spanWord(c.t_range_s)}</td>
                            <td className={c.uncertainty?.value == null ? "ws-muted" : "num"}>{uncertaintyWord(c.uncertainty, c.unit)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {f.ignored_columns?.length ? <p className="ws-muted">Columns not used: {f.ignored_columns.join(", ")}</p> : null}
              </div>
            ))}
          </div>
          {segs.map(([n, lines]) => (
            <Expand key={n} label={`Segments of record ${n} (${lines.length} line${lines.length === 1 ? "" : "s"})`}>
              <ol className="ws-inv-segs">{lines.map((l, i) => <li key={i}>{l}</li>)}</ol>
            </Expand>
          ))}
          {tests.length > 0 && (
            <div className="ws-inv-block">
              <p className="ws-h3">Tests</p>
              <div className="ws-table-wrap">
                <table className="ws-table is-tight">
                  <thead><tr><th scope="col">#</th><th scope="col">Kind</th><th scope="col">From</th><th scope="col">Mode</th></tr></thead>
                  <tbody>
                    {tests.map((x) => (
                      <tr key={x.test}>
                        <th scope="row" className="num">{x.request_id ? <button type="button" className="text-link" onClick={() => onOpen(x.request_id!)}>{x.test}</button> : x.test}</th>
                        <td>{TEST_KIND[x.kind] ?? humanize(x.kind)}</td>
                        <td className="ws-wrap">{x.kind === "record" ? `${x.record_ref ?? `record ${x.record ?? "?"}`}, segment ${x.segment ?? "?"}` : "–"}</td>
                        <td>{x.mode ?? "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>

        <section className="ws-card ws-inv-part" aria-labelledby="ws-inv-model-h">
          <h3 className="ws-inv-h" id="ws-inv-model-h">Assumed by the model</h3>
          <div className="ws-inv-block">
            <p className="ws-h3">Prior{ma?.prior?.version != null ? ` (v${ma.prior.version})` : ""}</p>
            {prior.length ? <ul className="ws-learn-list">{prior.map((x) => <li key={x} className="num">{x}</li>)}</ul> : <p className="ws-card-sub">No prior recorded.</p>}
            {ma?.prior?.source ? <p className="ws-card-foot">{upperFirst(prose(ma.prior.source))}.</p> : null}
          </div>
          {families.length > 0 && (
            <div className="ws-inv-block">
              <p className="ws-h3">Model families it may fit</p>
              <ul className="ws-learn-list">{families.map(([k, x]) => <li key={k}><span className="num">{k}</span>: {x}</li>)}</ul>
            </div>
          )}
          <div className="ws-inv-block">
            <p className="ws-h3">States the agent assumed for record tests</p>
            {states.length ? <ul className="ws-lines">{states.map((x) => <li key={x}>{x}</li>)}</ul> : <p className="ws-card-sub">None yet.</p>}
            <p className="ws-card-foot">Stated, not measured: where a record test starts is the agent’s reading of the record.</p>
          </div>
        </section>

        <section className="ws-card ws-inv-part" aria-labelledby="ws-inv-eng-h">
          <h3 className="ws-inv-h" id="ws-inv-eng-h">Set by the engineer</h3>
          <ul className="ws-learn-list">
            <li><strong>{targetLine(t)}</strong>{t.limit != null ? ` · ${t.direction === "maximize" ? "at least" : "at most"} ${withUnit(t.limit, t.unit)}` : ""}</li>
            {cons.map((c) => <li key={`${c.quantity}${c.op}${c.limit}`}>{constraintLine(c)}</li>)}
          </ul>
          <p className="ws-card-foot">Objective v{v.objective.version}. Only an engineer’s amendment changes these; the agent cannot.</p>
        </section>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- learning (a system that keeps model versions)

const adequacyWord = (a: boolean | null | undefined) =>
  a === true ? { text: "Fit meets the RMSE rule", tone: "good" } : a === false ? { text: "Fit misses the RMSE rule", tone: "bad" } : { text: "No fit of its own", tone: "off" };
const pct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x * 100)} %`);
const rangeLine = (r: number[] | undefined, unit: string) => (!r?.length ? null : r.length === 1 || r[0] === r[r.length - 1] ? withUnit(r[0], unit) : `${num(r[0])}–${withUnit(r[r.length - 1], unit)}`);

/** What the campaign believes and why: the model in use, the latest evidence, the next experiment, the best design. */
/** What the lab tests in this campaign are: simulations, and any result a person uploaded for an approved bench request. */
const labWords = (l: Learning) => l.lab_tests?.some((t) => t.basis === "physical")
  ? "lab tests are simulations, except results a person uploaded for an approved bench request."
  : "every lab test here is a simulation, not a physical cell.";

function LearningSection({ v, l }: { v: LoopView; l: Learning }) {
  return (
    <>
      <section aria-labelledby="ws-learn-h">
        <div className="ws-section-head">
          <h2 className="ws-h2" id="ws-learn-h">What the agent has learned</h2>
          <span className="ws-muted">{l.lab_tests?.some(isRecordTest)
            ? `Record tests are stretches of the engineer’s records; ${labWords(l)}`
            : `Simulated lab (computational): ${labWords(l)}`}</span>
        </div>
        <div className="ws-learn">
          <ModelPanel l={l} />
          <EvidencePanelCard l={l} />
          <NextPanel v={v} l={l} />
          <BestDesignPanel l={l} />
        </div>
        {l.model_gap?.established && (
          <section className="ws-card ws-gap" aria-labelledby="ws-gap-h" role="status">
            <p className="ws-card-k" id="ws-gap-h">No supported model explains the tests</p>
            <p>{l.model_gap.summary}</p>
            <ul className="ws-learn-list">
              {l.model_gap.needed.map((n, i) => (
                <li key={i}><strong>{n.kind === "evidence" ? "Evidence needed" : "Modelling capability needed"}:</strong> {n.what}. It would establish {n.establishes}.</li>
              ))}
            </ul>
          </section>
        )}
        {l.limits && <p className="ws-learn-foot">{upperFirst(prose(l.limits))}.</p>}
      </section>
      {(l.lab_tests?.length ?? 0) > 0 && <PredictionChart v={v} l={l} />}
      {(l.first_design || l.best_design) && <BeforeAfter l={l} />}
    </>
  );
}

function ModelPanel({ l }: { l: Learning }) {
  const m = versionOf(l, l.model_in_use ?? 0) ?? l.versions?.[0];
  if (!m) return <section className="ws-card is-empty" aria-label="Current model"><p className="ws-card-k">Current model</p><p className="ws-card-sub">No model version recorded.</p></section>;
  return <ModelPanelBody l={l} m={m} />;
}

/** v0 → v1 → …: every recorded model version in order, the one in use marked, each with its held-out verdict. */
function VersionTrack({ l }: { l: Learning }) {
  const vs = l.versions ?? [];
  if (vs.length < 2) return null;
  return (
    <ol className="ws-vtrack" aria-label="Model versions, oldest first">
      {vs.map((x, i) => {
        const inUse = x.version === l.model_in_use;
        const tone = x.adequate === true ? "good" : x.adequate === false ? "bad" : "off";
        const word = x.adequate === true ? "adequate" : x.adequate === false ? "not adequate" : "unchecked";
        const ruledOut = !inUse && x.version === 0 && vs.some((y) => y.version > 0 && y.consistent_with_nominal === false);
        return (
          <li key={x.version} className={`ws-vstep is-${tone} ${inUse ? "is-current" : ""}`} style={{ ["--i" as string]: i }}>
            {i > 0 && <span className="ws-varrow" aria-hidden>→</span>}
            <span className="ws-vpill">
              <span className="num ws-vnum">v{x.version}</span>
              <span className="ws-vword">{x.family ?? ""}</span>
              <span className="ws-vstate">{inUse ? `in use · ${word}` : ruledOut ? "ruled out" : word}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function ModelPanelBody({ l, m }: { l: Learning; m: NonNullable<Learning["versions"]>[number] }) {
  const ad = adequacyWord(m.adequate);
  const changed = useChanged(m.version);
  const val = m.validity;
  const validity = [
    rangeLine(val?.ambient_degC, "degC") && `ambient ${rangeLine(val?.ambient_degC, "degC")}`,
    rangeLine(val?.c_rate, "1/h") && `charge ${rangeLine(val?.c_rate, "1/h")}`,
    rangeLine(val?.rest_min as number[] | undefined, "min") && `rest ${rangeLine(val?.rest_min as number[] | undefined, "min")}`,
  ].filter(Boolean).join(", ");
  // a records campaign numbers record tests and lab tests together: "test n" there, "lab test n" in a lab campaign
  const noun = l.lab_tests?.some(isRecordTest) ? "test" : "lab test";
  return (
    <section className={`ws-card ws-learn-card ws-learn-model ${changed ? "is-updated" : ""}`} aria-labelledby="ws-model-h">
      <p className="ws-card-k" id="ws-model-h">Current model · in use</p>
      <p className="ws-learn-big"><span className="num">v{m.version}</span> <span className="ws-learn-fam">{m.family ?? ""}</span></p>
      <VersionTrack l={l} />
      <ul className="ws-learn-list">
        {Object.entries(m.estimate ?? {}).map(([k, x]) => <li key={k} className="num">{paramLine(k, x, m.region_95?.[k] ?? null)}</li>)}
        <li>{m.members?.length ?? 0} member{m.members?.length === 1 ? "" : "s"}{m.consistent_with_nominal === false ? " · the nominal parameters (v0) lie outside its fitted region" : m.consistent_with_nominal ? " · consistent with the nominal model" : ""}</li>
      </ul>
      <p><span className={`ws-verdict is-${ad.tone}`}>{ad.text}</span></p>
      {m.checks?.length ? m.checks.map((c, i) => (
        <p key={c.request_id ?? i} className="ws-card-sub num">Held-out check on {noun} {c.lab_tests?.join(", ") ?? "?"}: RMSE {withUnit(c.heldout_rmse_K, "K")}, max error {withUnit(c.heldout_max_error_K, "K")}, coverage {pct(c.coverage)}</p>
      )) : <p className="ws-card-sub">No held-out check yet.</p>}
      <p className="ws-card-foot">
        {m.sources?.length ? `Fitted on ${noun} ${m.sources.join(", ")}${m.fit_rmse_K != null ? ` (fit RMSE ${withUnit(m.fit_rmse_K, "K")})` : ""}. ` : m.source ? `${upperFirst(prose(m.source))}. ` : ""}
        {validity ? `Fitted under ${validity}.` : ""}
      </p>
    </section>
  );
}

function EvidencePanelCard({ l }: { l: Learning }) {
  const t = labTestOf(l, l.latest_lab_test) ?? l.lab_tests?.[l.lab_tests.length - 1];
  if (!t) return <section className="ws-card is-empty" aria-label="Evidence just received"><p className="ws-card-k">Evidence just received</p><p className="ws-card-sub">No lab test has run yet.</p></section>;
  const f = t.forecast;
  const obs = t.observed?.max_temperature, fc = f?.max_temperature;
  const diff = obs != null && fc != null ? obs - fc : null;
  const p = t.protocol ?? {};
  const rates = ["c_rate_1", "c_rate_2", "c_rate_3", "c_rate_4"].map((k) => (typeof p[k] === "number" ? (p[k] as number) : null));
  if (isRecordTest(t)) return <RecordEvidence t={t} />;
  return <EvidenceBody t={t} diff={diff} rates={rates} />;
}

/** A record test: a constant-current stretch of the engineer's record, shown as the record has it. */
function RecordEvidence({ t }: { t: NonNullable<Learning["lab_tests"]>[number] }) {
  const r = t.record!;
  const changed = useChanged(t.n);
  return (
    <section className={`ws-card ws-learn-card ws-learn-evid ${changed ? "is-updated" : ""}`} aria-labelledby="ws-evid-h">
      <p className="ws-card-k" id="ws-evid-h">Evidence just received · test {t.n}, from the record</p>
      <p className="ws-card-sub num">{recordStretchLine(r)}</p>
      <RecordStretchTable r={r} />
      <p className="ws-card-foot">{t.basis ? upperFirst(t.basis) : "The engineer’s record"}. A stretch of a record, not a charge protocol.</p>
    </section>
  );
}

function RecordStretchTable({ r }: { r: NonNullable<NonNullable<Learning["lab_tests"]>[number]["record"]> }) {
  return (
    <table className="ws-table is-tight">
      <tbody>
        <tr><th scope="row">Record</th><td className="ws-wrap">{r.ref ?? "–"}</td></tr>
        <tr><th scope="row">Segment</th><td className="num">{r.segment ?? "–"}{r.t_start_s != null && r.t_end_s != null ? <span className="ws-muted"> · {spanWord([r.t_start_s, r.t_end_s])}</span> : null}</td></tr>
        <tr><th scope="row">Mode</th><td>{r.mode ?? "–"}</td></tr>
        <tr><th scope="row">C-rate</th><td className="num">{withUnit(r.test_c_rate, "1/h")}{r.test_current != null ? <span className="ws-muted"> · {withUnit(r.test_current, "A")}</span> : null}</td></tr>
        <tr><th scope="row">Duration</th><td className="num">{withUnit(r.test_duration, "min")}</td></tr>
        <tr className="is-focal"><th scope="row">Start temperature</th><td className="num">{withUnit(r.temperature_start, "degC")}</td></tr>
        <tr className="is-focal"><th scope="row">Temperature rise</th><td className="num">{r.temperature_rise != null && r.temperature_rise >= 0 ? "+" : ""}{withUnit(r.temperature_rise, "K")}</td></tr>
        {r.ambient_temperature != null && <tr><th scope="row">Ambient (chamber)</th><td className="num">{withUnit(r.ambient_temperature, "degC")}</td></tr>}
      </tbody>
    </table>
  );
}

function EvidenceBody({ t, diff, rates }: { t: NonNullable<Learning["lab_tests"]>[number]; diff: number | null; rates: (number | null)[] }) {
  const f = t.forecast, p = t.protocol ?? {};
  const obs = t.observed?.max_temperature, fc = f?.max_temperature;
  const changed = useChanged(t.n);
  return (
    <section className={`ws-card ws-learn-card ws-learn-evid ${changed ? "is-updated" : ""}`} aria-labelledby="ws-evid-h">
      <p className="ws-card-k" id="ws-evid-h">Evidence just received · lab test {t.n}</p>
      <p className="ws-card-sub num">{cRatesLine(rates)}{p.ambient != null ? ` · ambient ${withUnit(p.ambient, "degC")}` : ""}{p.rest_min != null ? ` · rest ${withUnit(p.rest_min, "min")}` : ""}</p>
      <table className="ws-table is-tight">
        <thead><tr><th scope="col">Quantity</th><th scope="col">Forecast{f ? ` (v${f.model_version})` : ""}</th><th scope="col">Observed</th></tr></thead>
        <tbody>
          <tr className={fc != null && obs != null ? "is-focal" : ""}><th scope="row">Max temperature</th><td className="num">{withUnit(fc, "degC")}</td><td className="num">{withUnit(obs, "degC")}</td></tr>
          <tr><th scope="row">Time 10 to 80 % SOC</th><td className="num">–</td><td className="num">{withUnit(t.observed?.charge_time_80, "min")}</td></tr>
          <tr><th scope="row">Temperature at the end</th><td className="num">–</td><td className="num">{withUnit(t.observed?.temperature_end, "degC")}</td></tr>
          <tr><th scope="row">Anode potential, min</th><td className="num">{withUnit(f?.min_anode_potential, "mV")}</td><td className="ws-muted">not measured</td></tr>
        </tbody>
      </table>
      <p className="ws-card-sub">
        {f ? <>Forecast made before the test, under model v{f.model_version}. </> : <>No forecast of this protocol was made before the test. </>}
        {diff != null && <span className={Math.abs(diff) >= 1 ? "ws-bad" : ""}>The lab ran {num(Math.abs(diff))} K {diff < 0 ? "cooler" : "hotter"} than forecast.</span>}
      </p>
      <p className="ws-card-foot">{t.basis ? upperFirst(t.basis) : "Simulated lab"}. The anode potential is a forecast only.</p>
    </section>
  );
}

function NextPanel({ v, l }: { v: LoopView; l: Learning }) {
  const words = useWords(v);
  const s = nextStepOf(l);
  const r = s.kind === "run" || s.kind === "none" ? s.ranking : null;
  return (
    <section className={`ws-card ws-learn-card ws-learn-next ${s.kind === "pending" ? "is-ask" : ""}`} aria-labelledby="ws-next-h">
      <p className="ws-card-k" id="ws-next-h">Next experiment{r?.model_version != null ? ` · ranked under v${r.model_version}` : ""}</p>
      {s.kind === "pending" && (
        <>
          <p className="ws-learn-big">{words.action(s.next.action)}</p>
          <p className="ws-card-sub">{STATE_WORD[s.next.state as keyof typeof STATE_WORD] ?? s.next.state.replace(/_/g, " ")}</p>
          {s.next.rationale && <p className="ws-card-sub">{prose(s.next.rationale)}</p>}
          {s.next.expected_effect && <p className="ws-card-foot">Expected: {prose(s.next.expected_effect)}</p>}
        </>
      )}
      {s.kind === "none" && (
        <>
          <p className="ws-learn-big">No candidate test has positive value under this cost rule</p>
          {s.ranking.verdict && <p className="ws-card-sub">{upperFirst(prose(s.ranking.verdict))}.</p>}
        </>
      )}
      {s.kind === "run" && (
        <>
          <p className="ws-learn-big">Run option {s.option?.option ?? s.ranking.best_measurement}{s.option?.label ? `: ${s.option.label}` : ""}</p>
          {s.ranking.verdict && <p className="ws-card-sub">{upperFirst(prose(s.ranking.verdict))}.</p>}
        </>
      )}
      {s.kind === "unranked" && <p className="ws-card-sub">No experiment is pending and the candidate tests have not been ranked yet.</p>}
      {r?.decision_risk_min != null && <p className="ws-card-foot num">Expected loss of the best decision now: {withUnit(r.decision_risk_min, "min")} (charge time plus any limit breach).</p>}
      {r?.options?.length ? (
        <Expand label={`Candidate tests, by decision value (${r.options.length})`}>
          <div className="ws-table-wrap">
            <table className="ws-table is-tight">
              <thead><tr><th scope="col">#</th><th scope="col">Test</th><th scope="col">Duration</th><th scope="col">Value, net of cost</th></tr></thead>
              <tbody>
                {[...r.options].sort((a, b) => (b.value_min ?? -Infinity) - (a.value_min ?? -Infinity)).map((o) => (
                  <tr key={o.option} className={o.option === r.best_measurement ? "is-baseline" : ""}>
                    <th scope="row" className="num">{o.option}</th>
                    <td>{o.label ?? "–"}<br /><span className="ws-muted num">{cRatesLine(o.c_rates)}</span></td>
                    <td className="num">{withUnit(o.duration_min, "min")}</td>
                    <td className={`num ${(o.value_min ?? 0) > 0 ? "is-good" : ""}`}>{withUnit(o.value_min, "min")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="ws-muted">Value: the expected reduction in modelled decision loss (charge minutes, plus 100 for a design that breaks a limit), less a fixed test penalty of one loss-minute. Worth running only above zero.</p>
        </Expand>
      ) : null}
    </section>
  );
}

function BestDesignPanel({ l }: { l: Learning }) {
  const { main: d, lead } = designShown(l);
  if (!d) return <section className="ws-card is-empty" aria-label="Best design"><p className="ws-card-k">Best design</p><p className="ws-card-sub">No design found yet.</p></section>;
  return <BestDesignBody d={d} first={l.first_design ?? null} lead={lead} />;
}

function BestDesignBody({ d, first, lead }: { d: Design; first: Design | null; lead?: Design | null }) {
  const ad = adequacyWord(d.model_adequate);
  const changed = useChanged(`${d.model_version}:${d.request_id ?? ""}:${d.charge_time_80 ?? ""}`);
  const ghost = first && first !== d && first.request_id !== d.request_id ? first.c_rates : null;
  return (
    <section className={`ws-card ws-learn-card ws-learn-best ${changed ? "is-updated" : ""}`} aria-labelledby="ws-bestd-h">
      <p className="ws-card-k" id="ws-bestd-h">{designHeading(d)} · under v{d.model_version}</p>
      <p className="ws-big num">{withUnit(d.charge_time_80, "min")}</p>
      <p className="ws-card-sub">Time 10 to 80 % SOC, slowest member</p>
      {d.c_rates?.length ? (
        <StepProfile rates={d.c_rates} ghost={ghost} values
          label={`Charge steps ${cRatesLine(d.c_rates)}${ghost ? `; the first design, outlined, was ${cRatesLine(ghost)}` : ""}`} />
      ) : null}
      <p className="ws-card-sub num">Steps {cRatesLine(d.c_rates)}{ghost ? <span className="ws-muted"> · first design outlined</span> : null}</p>
      <ul className="ws-learn-list">
        <li className="num">Anode potential, worst member (forecast): {marginLine(d)}</li>
        <li className="num">Max temperature, worst member (forecast): {withUnit(d.max_temperature, "degC")}{d.limits?.t_limit_C != null ? ` of ≤ ${withUnit(d.limits.t_limit_C, "degC")}` : ""}</li>
      </ul>
      <p><span className={`ws-verdict is-${ad.tone}`}>Model v{d.model_version}: {ad.text.toLowerCase()}</span></p>
      {designCaveat(d) && <p className="lp-warn" role="note">{designCaveat(d)}</p>}
      {lead && <p className="lp-warn" role="note">{leadLine(lead)}</p>}
    </section>
  );
}

/** Observed surface temperature against the forecast made before the test, with the members' band. */
function PredictionChart({ v, l }: { v: LoopView; l: Learning }) {
  const tests = l.lab_tests ?? [];
  const [n, setN] = useState<number>(l.latest_lab_test ?? tests[tests.length - 1]?.n ?? 1);
  const t = labTestOf(l, n) ?? tests[tests.length - 1];
  const data = useMemo(() => traceChart(t?.trace), [t]);
  const limits = seriesLimits(v, "surface_temperature", "degC");
  const fv = t?.forecast?.model_version;
  return (
    <section aria-labelledby="ws-pvo-h">
      <div className="ws-section-head">
        <h2 className="ws-h2" id="ws-pvo-h">Prediction against observation</h2>
        <span className="ws-muted">Surface temperature, {t && isRecordTest(t) ? "test" : "lab test"} {t?.n}{t?.record ? ` · ${recordStretchLine(t.record)}` : ""}{fv != null ? ` · forecast under v${fv}` : ""}</span>
      </div>
      <figure className="ws-figure">
        {tests.length > 1 && (
          <div className="iv-splits" role="tablist" aria-label="Lab test">
            {tests.map((x) => (
              <button key={x.n} type="button" role="tab" aria-selected={x.n === t?.n} className={`iv-split ${x.n === t?.n ? "is-on" : ""}`} onClick={() => setN(x.n)}>
                {isRecordTest(x) ? `Test ${x.n} · record` : `Lab test ${x.n}`}{x.forecast ? ` · v${x.forecast.model_version}` : ""}
              </button>
            ))}
          </div>
        )}
        {data ? (
          <Chart cand={data.observed} base={null} forecast={data.forecast} band={data.spread > 0 ? data.band : []} limits={limits} label="Surface temperature" simulated={!t?.record && t?.basis !== "physical"}
            names={{ cand: t?.record ? "Observed (engineer’s record)" : t?.basis === "physical" ? "Observed (operator-uploaded)" : "Observed (simulated lab)", forecast: fv != null ? `Forecast, v${fv}` : "Forecast", band: "Members’ range" }} drawKey={t?.n}
            peaks={{ gap: t?.observed?.max_temperature != null && t?.forecast?.max_temperature != null ? t.observed.max_temperature - t.forecast.max_temperature : null, min: 1 }} />
        ) : <div className="lp-chart-empty">This lab test has no recorded temperature trace.</div>}
        <figcaption className="lp-caption">
          {t?.record ? `The record’s own surface temperature over the stretch (${t.basis ?? "the engineer’s record"}).`
            : "Observed once a minute in the simulated lab; the forecast was made before the test ran."}
          {data && !data.forecast ? (t?.record ? " No forecast of this stretch was made before it was read." : " No forecast of this protocol was made before the test.") : ""}
          {data?.forecast && data.spread === 0 ? ` Model v${fv ?? "?"} has one member, so there is no band.` : ""}
        </figcaption>
      </figure>
    </section>
  );
}

/** The first design against the best, side by side, with the model version each rests on. */
function BeforeAfter({ l }: { l: Learning }) {
  const a = l.first_design, b = designShown(l).main;
  const cols = [a && { k: "First design", d: a }, b && b !== a && b.request_id !== a?.request_id && { k: designHeading(b), d: b }].filter(Boolean) as { k: string; d: Design }[];
  const steps = Math.max(0, ...cols.map((c) => c.d.c_rates?.length ?? 0));
  const v0Miss = (l.lab_tests ?? []).find((t) => t.forecast?.model_version === (a?.model_version ?? -1) && t.forecast?.max_temperature != null && t.observed?.max_temperature != null);
  return (
    <section aria-labelledby="ws-ba-h">
      <div className="ws-section-head">
        <h2 className="ws-h2" id="ws-ba-h">Before and after</h2>
        <span className="ws-muted">Each design meets the limits for every member of the model version it used.</span>
      </div>
      {b && designCaveat(b) && <p className="lp-warn" role="note">{designCaveat(b)}</p>}
      {cols.length === 2 && (
        <div className="ws-ba-fig">
          {cols.map((c, i) => (
            <div key={c.k} className={`ws-ba-side ${i ? "is-after" : "is-before"}`}>
              <p className="ws-card-k">{c.k} · v{c.d.model_version}</p>
              <p className="ws-ba-big num">{withUnit(c.d.charge_time_80, "min")}</p>
              <StepProfile rates={c.d.c_rates ?? []} ghost={i ? cols[0].d.c_rates : null} values
                label={`${c.k}: charge steps ${cRatesLine(c.d.c_rates)}, ${withUnit(c.d.charge_time_80, "min")} from 10 to 80 % SOC`} />
            </div>
          ))}
        </div>
      )}
      <div className="ws-table-wrap">
        <table className="ws-table ws-ba">
          <thead><tr><th scope="col"><span className="visually-hidden">Quantity</span></th>{cols.map((c) => <th key={c.k} scope="col">{c.k}</th>)}</tr></thead>
          <tbody>
            <tr><th scope="row">Model version</th>{cols.map((c) => <td key={c.k}>v{c.d.model_version}{versionOf(l, c.d.model_version)?.family ? ` (${versionOf(l, c.d.model_version)!.family})` : ""} · <span className={`ws-verdict is-${adequacyWord(c.d.model_adequate).tone}`}>{adequacyWord(c.d.model_adequate).text.toLowerCase()}</span></td>)}</tr>
            {Array.from({ length: steps }, (_, i) => (
              <tr key={i}><th scope="row">Step {i + 1}</th>{cols.map((c) => {
                const x = c.d.c_rates?.[i] ?? null, other = cols.find((o) => o !== c)?.d.c_rates?.[i];
                return <td key={c.k} className={`num ${c === cols[1] && other != null && x != null && x !== other ? "is-changed" : ""}`}>{withUnit(x, "1/h")}</td>;
              })}</tr>
            ))}
            <tr><th scope="row">Time 10 to 80 % SOC</th>{cols.map((c) => <td key={c.k} className="num">{withUnit(c.d.charge_time_80, "min")}</td>)}</tr>
            <tr><th scope="row">Anode potential, worst member (forecast)</th>{cols.map((c) => <td key={c.k} className="num">{marginLine(c.d)}</td>)}</tr>
            <tr><th scope="row">Max temperature, worst member (forecast)</th>{cols.map((c) => <td key={c.k} className="num">{withUnit(c.d.max_temperature, "degC")}</td>)}</tr>
          </tbody>
        </table>
      </div>
      {v0Miss && a && b && a !== b && (
        <p className="ws-card-sub ws-ba-note">
          Model v{a.model_version} forecast lab test {v0Miss.n} at {withUnit(v0Miss.forecast!.max_temperature, "degC")} max; the lab observed {withUnit(v0Miss.observed!.max_temperature, "degC")}.
          The best design was re-optimized under v{b.model_version}, fitted to what the lab showed.
        </p>
      )}
    </section>
  );
}

function Answer({ k, w }: { k: string; w: { answer: string; tone: string; detail: string } }) {
  return (
    <div className={`ws-answer is-${w.tone}`}>
      <p className="ws-answer-k">{k}</p>
      <p className="ws-answer-v">{w.answer}</p>
      <p className="ws-answer-d">{prose(w.detail)}</p>
    </div>
  );
}

function ConclusionCard({ v, a }: { v: LoopView; a: Assessment }) {
  const c = v.conclusion!;
  const ow = outcomeWords(v, a);
  return (
    <section className="ws-conclusion" aria-labelledby="ws-concl-h">
      <h2 className="ws-h2" id="ws-concl-h">Conclusion · <span className={`ws-outcome is-${ow.recorded ? "feasible_only" : c.outcome}`}>{ow.word}</span></h2>
      {ow.recorded && <p className="ws-muted">Recorded outcome <code>{ow.recorded}</code> (an earlier comparator): the baseline broke the limits, so the candidate counted as better by meeting them. That is a feasibility answer; speed was not compared.</p>}
      {c.findings && (
        <ul className="ws-lines">
          <li>Feasibility: baseline {c.findings.feasibility.baseline}, best {c.findings.feasibility.best}{c.findings.feasibility.recovered ? " (recovered)" : ""}</li>
          <li>Speed: {c.findings.speed.status.replace(/_/g, " ")}{c.findings.speed.reason ? `. ${prose(c.findings.speed.reason)}` : ""}</li>
          <li>Objective: {c.findings.objective.status.replace(/_/g, " ")}</li>
        </ul>
      )}
      <Expand label="The conclusion’s text"><p className="ws-pre">{prose(c.text)}</p></Expand>
      {c.unresolved_reasons.length > 0 && (
        <Expand label={`Unresolved (${c.unresolved_reasons.length})`}><ul className="ws-lines">{c.unresolved_reasons.map((r) => <li key={r}>{prose(r)}</li>)}</ul></Expand>
      )}
    </section>
  );
}

function Expand({ label, children, open }: { label: string; children: ReactNode; open?: boolean }) {
  return <details className="ws-expand" open={open}><summary>{label}</summary><div className="ws-expand-body">{children}</div></details>;
}

// ---------------------------------------------------------------- experiments

function Experiments({ v, origin, selected, onSelect, actor }: { v: LoopView; origin: Origin; selected: LoopRequest | null; onSelect(id: string): void; actor: ReturnType<typeof useActor> }) {
  const words = useWords(v);
  const list = v.requests;
  const listRef = useRef<HTMLUListElement>(null);
  const onKey = (e: React.KeyboardEvent) => {
    if (!selected) return;
    const i = list.indexOf(selected);
    const j = e.key === "ArrowDown" ? Math.min(list.length - 1, i + 1) : e.key === "ArrowUp" ? Math.max(0, i - 1)
      : e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : -1;
    if (j < 0) return;
    e.preventDefault();
    onSelect(list[j].request_id);
    listRef.current?.querySelectorAll<HTMLElement>("[role=option]")[j]?.scrollIntoView({ block: "nearest" });
  };
  if (!list.length) return <p className="lp-plain">No experiments yet. The first proposal appears here.</p>;
  return (
    <div className="ws-exp">
      <ul ref={listRef} className="ws-exp-list" role="listbox" aria-label="Experiments, in order" tabIndex={0} onKeyDown={onKey}
        aria-activedescendant={selected ? `ws-opt-${selected.request_id}` : undefined}>
        {list.map((r, i) => {
          const c = comparisonOf(v, r.request_id);
          const vc = c ? verdictChip(c) : null;
          const role = requestRole(r);
          return (
            <li key={r.request_id} id={`ws-opt-${r.request_id}`} role="option" aria-selected={selected === r} className={`ws-exp-row ${selected === r ? "is-on" : ""} ${role !== "experiment" ? "is-system" : ""}`} onClick={() => onSelect(r.request_id)}>
              <span className="ws-exp-n num">#{i + 1}{r.version > 1 ? ` v${r.version}` : ""}</span>
              <span className="ws-exp-main">
                <span className="ws-exp-params">{requestTitle(r, words).replace(": ", " · ")}</span>
                <span className="ws-exp-meta">
                  <StateChip v={v} r={r} />
                  {ROLE_TAG[role] && <span className="ws-chip is-role">{ROLE_TAG[role]}</span>}
                  {vc && <span className={`ws-verdict is-${vc.tone}`}>{vc.text}</span>}
                  {r.result && <span className="num ws-muted">{withUnit(r.result.measurements.find((m) => m.quantity === v.objective.target.quantity)?.value ?? null, v.objective.target.unit)}</span>}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
      {selected && <ExperimentDetail key={selected.request_id} v={v} r={selected} origin={origin} actor={actor} />}
    </div>
  );
}

/** A request's state; one still waiting when the campaign was concluded reads "Not run", not "Awaiting approval". */
function StateChip({ v, r }: { v: LoopView; r: LoopRequest }) {
  const unrun = !!v.conclusion && (r.state === "awaiting_approval" || r.state === "queued");
  return unrun
    ? <span className="lp-state is-off" title="Not run: the campaign was concluded before it was approved">Not run</span>
    : <span className={`lp-state is-${stateTone(r.state)}`} title={STATE_WORD[r.state]}>{STATE_SHORT[r.state]}</span>;
}

function ExperimentDetail({ v, r, origin, actor }: { v: LoopView; r: LoopRequest; origin: Origin; actor: ReturnType<typeof useActor> }) {
  const words = useWords(v);
  const c = comparisonOf(v, r.request_id);
  const role = requestRole(r);
  const n = v.requests.indexOf(r) + 1;
  const pred = Object.entries(r.prediction);
  const meas = r.result ? ordered(v, r.result.measurements) : [];
  const warn = validityWarning(r.result);
  return (
    <article className="ws-detail" aria-labelledby="ws-detail-h">
      <header className="ws-detail-head">
        <h2 className="ws-h2" id="ws-detail-h">#{n} {words.action(r.action)}{ROLE_TAG[role] ? ` · ${ROLE_TAG[role]}` : ""}</h2>
        <StateChip v={v} r={r} />
      </header>
      <p className="ws-card-sub">Proposed by {actorIn(v, r.proposed_by)}{r.approved_by ? ` · ${approvedWord(r.approved_by)}` : ""} · {executorWord(r.executor)}{r.objective_version ? ` · under objective v${r.objective_version}` : ""}</p>
      {r.state === "awaiting_approval" && (actor.able
        ? <Decide v={v} requestId={r.request_id} version={r.version} hash={r.content_hash} origin={origin} />
        : <p className="ws-muted">{actor.why}</p>)}
      {r.state === "running" && <RunSteps r={r} />}
      {warn && <p className="lp-warn" role="note">{warn}</p>}

      <div className="ws-detail-grid">
        <section aria-label="Parameters">
          <h3 className="ws-h3">Parameters</h3>
          <table className="ws-table is-tight">
            <tbody>{Object.entries(r.params).map(([k, p]) => <tr key={k}><th scope="row">{words.param(k)}{/_\d+$/.test(k) ? ` ${k.match(/_(\d+)$/)![1]}` : ""}</th><td className="num">{paramText(p) === PARAM_RECORDED
              ? <details className="ws-param-long"><summary>{PARAM_RECORDED}</summary><pre className="ws-pre">{typeof p.value === "string" ? p.value : JSON.stringify(p.value, null, 1)}</pre></details>
              : paramText(p)}</td></tr>)}</tbody>
          </table>
        </section>
        <section aria-label="Prediction against result">
          <h3 className="ws-h3">Prediction against {r.result ? (r.result.basis === "computational" ? "simulated result" : "measured result") : "result"}</h3>
          <table className="ws-table is-tight">
            <thead><tr><th scope="col">Quantity</th><th scope="col">Predicted</th><th scope="col">Result</th></tr></thead>
            <tbody>
              {[...new Set([...pred.map(([q]) => q), ...meas.slice(0, 4).map((m) => m.quantity)])].map((q) => {
                const p = r.prediction[q];
                const m = r.result?.measurements.find((x) => x.quantity === q);
                const e = r.result?.prediction_error[q];
                return (
                  <tr key={q}>
                    <th scope="row">{words.quantity(q)}</th>
                    <td className="num">{p ? predictedLine(p) : "–"}</td>
                    <td className={`num ${e ? (e.inside === false ? "is-miss" : e.inside ? "is-good" : "") : ""}`}>{m ? `${withUnit(m.value, m.unit)}${showsH(m.value, m.h) ? ` ± ${num(m.h)}` : ""}` : "–"}{e ? (e.inside === false ? " · outside" : e.inside ? " · inside" : "") : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
        <section aria-label="Constraint checks">
          <h3 className="ws-h3">Constraint checks{c ? ` (objective v${c.objective_version})` : ""}</h3>
          {c ? (
            <>
              <Checks v={v} checks={c.constraints} />
              <p className="ws-card-sub">{verdictChip(c).text}{c.objective_version !== v.objective.version ? ` · checked against v${c.objective_version}, before the latest amendment` : ""}</p>
            </>
          ) : <p className="ws-muted">{r.result ? "No comparison recorded." : "Checked once a result returns."}</p>}
        </section>
      </div>
      {r.result?.series.length ? <SeriesPanel v={v} r={r} origin={origin} /> : null}
      {r.result && categoriesLine(r.result) && <p className="ws-muted">{categoriesLine(r.result)}</p>}
      {r.rejections.map((x) => <p key={x.seq} className="lp-error">A returned result was refused: {x.reasons.map(prose).join("; ")}</p>)}
      {r.failure && <p className="lp-error">Failed on attempt {r.failure.attempt}: {r.failure.reason}</p>}
      {r.rationale && <Expand label="Rationale"><p className="ws-pre">{r.rationale}</p></Expand>}
      <Expand label="Technical details">
        <dl className="ws-tech">
          <div><dt>Request</dt><dd><code>{r.request_id}</code> · version {r.version}</dd></div>
          <div><dt>Content hash</dt><dd><code className="ws-wrap">{r.content_hash}</code></dd></div>
          <div><dt>Executor</dt><dd>{r.executor}</dd></div>
          {r.result && <div><dt>Result</dt><dd><code>{r.result.result_id}</code> · {BASIS_WORD[r.result.basis] ?? r.result.basis}</dd></div>}
          {c && <div><dt>Comparison</dt><dd><code>{c.id}</code></dd></div>}
          <div><dt>Timeline</dt><dd><ol className="ws-timeline">{r.timeline.map((s) => <li key={s.seq}><span className="num">{new Date(s.t * 1000).toLocaleString()}</span> · {s.state.replace(/_/g, " ")} · {actorIn(v, s.actor)} · seq {s.seq}</li>)}</ol></dd></div>
        </dl>
      </Expand>
    </article>
  );
}

/** A result's measurements, the target and the limited quantities first. */
function ordered(v: LoopView, ms: Measurement[]) {
  const rank = (q: string) => (q === v.objective.target.quantity ? 0 : constraintOf(v, q) ? 1 : 2);
  return [...ms].sort((a, b) => rank(a.quantity) - rank(b.quantity));
}

// ---------------------------------------------------------------- series

function SeriesPanel({ v, r, origin }: { v: LoopView; r: LoopRequest; origin: Origin }) {
  const words = useWords(v);
  const res = r.result!;
  // Constrained quantities first: their limit lines are drawn.
  const series = useMemo(() => [...res.series].sort((a, b) => Number(v.objective.constraints.some((c) => limitNames(c.quantity, b.quantity))) - Number(v.objective.constraints.some((c) => limitNames(c.quantity, a.quantity)))), [res.series, v.objective.constraints]);
  const [qi, setQi] = useState(0);
  const ref = series[Math.min(qi, series.length - 1)];
  const [lines, setLines] = useState<{ cand: Series | null; base: Series | null; error: string } | null>(null);
  const base = baselineFor(v, res.basis);
  const baseRun = base?.basis === "computational" ? v.requests.find((q) => requestRole(q) === "baseline" && q.state === "completed") : undefined;
  const baseRef = requestRole(r) === "baseline" ? undefined : baseRun?.request_id ?? base?.refs[0];
  const baseUrl = baseRun?.result?.series.find((s) => s.quantity === ref?.quantity)?.url;

  useEffect(() => {
    if (!ref) return;
    let gone = false;
    setLines(null);
    (async () => {
      try {
        if (origin.mode === "live" && origin.ledger) {
          const cand = await v1.series(origin.ledger, v.campaign.id, r.request_id, ref.quantity);
          const b = baseRef ? await v1.series(origin.ledger, v.campaign.id, baseRef, ref.quantity).catch(() => null) : null;
          if (!gone) setLines({ cand, base: b, error: "" });
        } else {
          const cand = await seriesFromBlob(origin.blobs ?? {}, ref.url);
          const b = baseUrl ? await seriesFromBlob(origin.blobs ?? {}, baseUrl).catch(() => null) : null;
          if (!gone) setLines({ cand: cand && thin(cand), base: b && thin(b), error: cand ? "" : "This file doesn’t carry the series bytes." });
        }
      } catch (err) {
        if (!gone) setLines({ cand: null, base: null, error: err instanceof Error ? err.message : "Couldn’t load the series." });
      }
    })();
    return () => { gone = true; };
  }, [v.campaign.id, r.request_id, ref?.quantity, ref?.url, origin.mode, origin.ledger, origin.blobs, baseRef, baseUrl]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!ref) return null;
  const limits = lines?.cand ? seriesLimits(v, ref.quantity, lines.cand.unit) : [];
  return (
    <figure className="ws-figure">
      {series.length > 1 && (
        <div className="iv-splits" role="tablist" aria-label="Series">
          {series.map((s, i) => <button key={s.quantity} type="button" role="tab" aria-selected={i === qi} className={`iv-split ${i === qi ? "is-on" : ""}`} onClick={() => setQi(i)}>{words.quantity(s.quantity)}</button>)}
        </div>
      )}
      {lines?.cand ? <Chart cand={lines.cand} base={lines.base} limits={limits} label={words.quantity(ref.quantity)} simulated={res.basis === "computational"} />
        : <div className="lp-chart-empty">{lines?.error ? lines.error : <Spinner size={18} />}</div>}
      <figcaption className="lp-caption">
        {words.quantity(ref.quantity)} over time{lines?.cand?.downsampled ? `, ${lines.cand.t.length} of ${lines.cand.n} points` : ""}
        {res.basis === "computational" ? " · simulated, not a bench measurement" : ""}{lines && !lines.base && baseRef ? " · no baseline series recorded for this quantity" : ""}
      </figcaption>
    </figure>
  );
}

/**
 * A plain series chart: candidate, baseline, the constraint limit, and earlier limits an amendment changed. The learning
 * view adds a forecast line and the members' band, and names its lines (observed, forecast) in place of candidate.
 */
function Chart({ cand, base, limits, label, simulated, forecast, band, names, drawKey, peaks }: {
  cand: Series; base: Series | null; limits: { value: number; label: string; past: boolean }[]; label: string; simulated: boolean;
  forecast?: Series | null; band?: Band[]; names?: { cand: string; forecast?: string; band?: string };
  /** Changing it draws the lines on again from the left (the learning chart, when another lab test is picked). */
  drawKey?: string | number;
  /** Mark the candidate's and the forecast's peaks with a leader between them when the recorded gap (observed minus
   *  forecast maximum, `gap`) is at least `min`. The label quotes the recorded gap, not one read off the sampled trace. */
  peaks?: { gap: number | null | undefined; min: number };
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const h = 240, pad = { l: 46, r: 14, t: 14, b: 30 };
  const all = [cand, ...(base ? [base] : []), ...(forecast ? [forecast] : [])];
  const tMax = Math.max(...all.map((s) => s.t[s.t.length - 1] ?? 0), ...(band ?? []).map((b) => b.t[b.t.length - 1] ?? 0), 1);
  const ys = all.flatMap((s) => s.y).concat(limits.map((l) => l.value), (band ?? []).flatMap((b) => [...b.lo, ...b.hi]));
  let yMin = Math.min(...ys), yMax = Math.max(...ys);
  if (yMax - yMin < 1e-9) { yMin -= 1; yMax += 1; }
  const span = yMax - yMin;
  yMin -= span * 0.06; yMax += span * 0.06;
  const minutes = tMax > 600;
  const X = (t: number) => pad.l + (t / tMax) * Math.max(10, w - pad.l - pad.r);
  const Y = (y: number) => pad.t + (1 - (y - yMin) / (yMax - yMin)) * (h - pad.t - pad.b);
  const path = (s: Series) => s.t.map((t, i) => `${i ? "L" : "M"}${X(t).toFixed(1)},${Y(s.y[i]).toFixed(1)}`).join("");
  const yt = ticks(yMin, yMax, 5), xt = ticks(0, minutes ? tMax / 60 : tMax, 6);
  const u = unitWord(cand.unit);
  const peakOf = (s: Series) => s.y.reduce((b, y, i) => (y > s.y[b] ? i : b), 0);
  const pk = peaks?.gap != null && Number.isFinite(peaks.gap) && Math.abs(peaks.gap) >= peaks.min && forecast?.y.length ? (() => {
    const a = peakOf(cand), b = peakOf(forecast);
    return { obs: { t: cand.t[a], y: cand.y[a] }, fc: { t: forecast.t[b], y: forecast.y[b] }, gap: peaks.gap as number };
  })() : null;
  const gapUnit = cand.unit === "degC" ? "K" : u;
  return (
    <div ref={ref} className="lp-chart">
      {w > 0 && (
        <svg width={w} height={h} role="img" aria-label={`${label} (${u}) against time, ${names ? names.cand.toLowerCase() : `${simulated ? "simulated" : "measured"} candidate`}${base ? " and the baseline" : ""}${forecast && names?.forecast ? ` and ${names.forecast.toLowerCase()}` : ""}${limits.length ? `, limit ${limits.map((l) => l.label).join("; ")}` : ""}`} className="iv-svg">
          {yt.map((y) => <g key={`y${y}`}><line className="iv-grid" x1={pad.l} x2={w - pad.r} y1={Y(y)} y2={Y(y)} /><text className="iv-tick" x={pad.l - 6} y={Y(y) + 4} textAnchor="end">{num(y)}</text></g>)}
          {xt.map((x) => <text key={`x${x}`} className="iv-tick" x={X(minutes ? x * 60 : x)} y={h - 10} textAnchor="middle">{num(x)}</text>)}
          <text className="iv-tick" x={w - pad.r} y={h - 10} textAnchor="end" dy="-12">{minutes ? "min" : "s"}</text>
          <text className="iv-tick" x={pad.l - 6} y={pad.t - 2} textAnchor="end">{u}</text>
          {limits.map((l) => (
            <g key={l.label}>
              <line className={`lp-limit ${l.past ? "is-past" : ""}`} x1={pad.l} x2={w - pad.r} y1={Y(l.value)} y2={Y(l.value)} />
              <text className={`lp-limit-label ${l.past ? "is-past" : ""}`} x={l.past ? pad.l + 4 : w - pad.r} y={Y(l.value) - 5} textAnchor={l.past ? "start" : "end"}>{l.label}</text>
            </g>
          ))}
          <g key={drawKey ?? "lines"} className={drawKey != null ? "lp-draw" : undefined}>
          {(band ?? []).map((b, i) => (
            <path key={`band${i}`} className="lp-band" d={`${b.t.map((t, j) => `${j ? "L" : "M"}${X(t).toFixed(1)},${Y(b.hi[j]).toFixed(1)}`).join("")}${b.t.map((_, j) => b.t.length - 1 - j).map((j) => `L${X(b.t[j]).toFixed(1)},${Y(b.lo[j]).toFixed(1)}`).join("")}Z`} />
          ))}
          {base && <path className="lp-line is-base" d={path(base)} />}
          {forecast && <path className="lp-line is-fc" d={path(forecast)} />}
          <path className={`lp-line is-cand ${simulated ? "is-sim" : ""}`} d={path(cand)} />
          </g>
          {pk && (() => {
            // A leader from the forecast's peak to the observed one, labelled with the gap; the label sits on the side with room.
            const x1 = X(pk.fc.t), y1 = Y(pk.fc.y), x2 = X(pk.obs.t), y2 = Y(pk.obs.y);
            const lx = Math.max(x1, x2) + 10, right = lx + 150 < w - pad.r;
            const tx = right ? lx : Math.min(x1, x2) - 10;
            return (
              <g key={`pk${drawKey ?? ""}`} className="lp-peaks">
                <line className="lp-peak-lead" x1={x1} y1={y1} x2={x1} y2={y2} />
                {Math.abs(x2 - x1) > 2 && <line className="lp-peak-lead" x1={x1} y1={y2} x2={x2} y2={y2} />}
                <circle className="lp-peak is-fc" cx={x1} cy={y1} r={3.5} />
                <circle className="lp-peak is-obs" cx={x2} cy={y2} r={3.5} />
                <text className="lp-peak-label" x={tx} y={(y1 + y2) / 2 + 4} textAnchor={right ? "start" : "end"}>
                  Peak {num(Math.abs(pk.gap))} {gapUnit} {pk.gap < 0 ? "below" : "above"} the forecast
                </text>
              </g>
            );
          })()}
        </svg>
      )}
      <div className="lp-legend">
        <span><i className={`lp-key is-cand ${simulated ? "is-sim" : ""}`} />{names ? names.cand : `Candidate${simulated ? " (simulated)" : ""}`}</span>
        {forecast && names?.forecast && <span><i className="lp-key is-fc" />{names.forecast}</span>}
        {band?.length && names?.band ? <span><i className="lp-key is-band" />{names.band}</span> : null}
        {base && <span><i className="lp-key is-base" />Baseline</span>}
        {limits.some((l) => !l.past) && <span><i className="lp-key is-limit" />Limit</span>}
        {limits.some((l) => l.past) && <span><i className="lp-key is-limit is-past" />Limit before an amendment</span>}
      </div>
    </div>
  );
}

function ticks(lo: number, hi: number, n: number): number[] {
  const raw = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let x = Math.ceil(lo / step) * step; x <= hi + 1e-9; x += step) out.push(+x.toFixed(10));
  return out;
}

// ---------------------------------------------------------------- data

function DataTab({ v, origin }: { v: LoopView; origin: Origin }) {
  const words = useWords(v);
  const refs = [...new Set(v.baseline.flatMap((b) => b.refs))];
  const dsIds = [...new Set(refs.map((r) => r.split("/")[0]).filter(Boolean))];
  const [records, setRecords] = useState<Record<string, import("@/lib/newcampaign").DatasetDetail | "error">>({});
  useEffect(() => {
    if (origin.mode !== "live" || !origin.ledger) return;
    const ctl = new AbortController();
    for (const id of dsIds) v1.dataset(origin.ledger, id, ctl.signal).then((d) => setRecords((x) => ({ ...x, [id]: d }))).catch(() => { if (!ctl.signal.aborted) setRecords((x) => ({ ...x, [id]: "error" })); });
    return () => ctl.abort();
  }, [origin.mode, origin.ledger, dsIds.join(",")]);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="ws-data">
      <section aria-labelledby="ws-base-h">
        <h2 className="ws-h2" id="ws-base-h">Baseline</h2>
        {v.baseline.length === 0 && <p className="lp-plain">No baseline recorded.</p>}
        {v.baseline.map((b) => (
          <div key={b.basis} className="ws-base">
            <p className="ws-card-sub">{BASIS_WORD[b.basis] ?? b.basis} baseline · from {b.refs.join(", ") || "–"}{b.fails.length ? ` · breaks ${b.fails.map((q) => words.quantity(q).toLowerCase()).join(" and ")}` : ""}</p>
            <table className="ws-table is-tight">
              <thead><tr><th scope="col">Quantity</th><th scope="col">Value</th><th scope="col">Limit (current version)</th></tr></thead>
              <tbody>
                {Object.entries(b.values).map(([q, val]) => {
                  const ch = b.constraints.find((c) => c.quantity === q);
                  const con = constraintOf(v, q);
                  return (
                    <tr key={q}>
                      <th scope="row">{words.quantity(q)}</th>
                      <td className="num">{valueLine(val)}</td>
                      <td className={ch?.ok === false ? "ws-bad" : ch?.ok ? "ws-good" : ""}>{con ? `${opWord(con.op)} ${withUnit(con.limit, con.unit)} · ${ch?.ok === false ? "broken" : ch?.ok ? "holds" : "unknown"}` : "–"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}
      </section>
      <section aria-labelledby="ws-rec-h2">
        <h2 className="ws-h2" id="ws-rec-h2">Records</h2>
        {origin.mode !== "live" && <p className="lp-plain">This recording names its records ({refs.join(", ") || "none"}); their files stay on the server they came from.</p>}
        {dsIds.map((id) => {
          const d = records[id];
          if (origin.mode !== "live") return null;
          if (!d) return <p key={id} className="ws-muted"><Spinner size={12} /> Loading {id}…</p>;
          if (d === "error") return <p key={id} className="lp-error">Couldn’t load dataset {id}.</p>;
          return (
            <div key={id} className="ws-table-wrap">
              <table className="ws-table">
                <caption className="ws-caption">{d.title}{d.citation ? ` · ${d.citation}` : ""}{d.licence ? ` · ${d.licence}` : ""}</caption>
                <thead><tr><th scope="col">Record</th><th scope="col">Role</th><th scope="col">Configuration</th><th scope="col">Channels (unit, points)</th></tr></thead>
                <tbody>
                  {d.records.map((r) => (
                    <tr key={r.id} className={r.role === "baseline" ? "is-baseline" : ""}>
                      <th scope="row">{r.id}</th>
                      <td>{r.role === "baseline" ? <span className="ws-chip is-base">Baseline</span> : r.role}</td>
                      <td className="num">{Object.entries(r.configuration ?? {}).map(([k, val]) => `${k} ${typeof val === "number" ? withUnit(val, r.configuration_units?.[k] ?? "") : String(val)}`).join(", ") || "–"}</td>
                      <td>{r.channels.length ? r.channels.map((c) => `${c.quantity} (${unitWord(c.unit) || "–"}${c.n ? `, ${c.n}` : ""})`).join(", ") : <span className="ws-muted">{r.provenance?.notes ?? "no measured channels"}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
      </section>
      {v.measurements_defined.length > 0 && (
        <section aria-labelledby="ws-def-h">
          <h2 className="ws-h2" id="ws-def-h">Measurements defined during the campaign</h2>
          <ul className="ws-lines">{v.measurements_defined.map((m) => <li key={m.name}><code>{m.name}</code> · {m.op} · {unitWord(m.unit)} · defined by {actorIn(v, m.defined_by)}</li>)}</ul>
        </section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- evidence

function Evidence({ v, a, derived, origin }: { v: LoopView; a: Assessment; derived: boolean; origin: Origin }) {
  const words = useWords(v);
  const t = v.objective.target;
  const s = spendLines(v.spend);
  const src = v.source;
  return (
    <div className="ws-evidence">
      <section aria-labelledby="ws-cmp-h">
        <h2 className="ws-h2" id="ws-cmp-h">Comparisons</h2>
        <div className="ws-table-wrap">
          <table className="ws-table">
            <thead><tr><th scope="col">Request</th><th scope="col">Version</th><th scope="col">Verdict</th><th scope="col">{t.label || words.quantity(t.quantity)}</th><th scope="col">Checks</th></tr></thead>
            <tbody>
              {v.comparisons.map((c) => {
                const r = v.requests.find((x) => x.request_id === c.request_id);
                return (
                  <tr key={c.id}>
                    <th scope="row">#{r ? v.requests.indexOf(r) + 1 : "?"} <code className="ws-muted">{c.id.slice(0, 12)}</code></th>
                    <td className="num">v{c.objective_version}</td>
                    <td>{verdictChip(c).text}</td>
                    <td className="num">{withUnit(c.target.candidate, t.unit)}{c.target.baseline != null ? <span className="ws-muted"> vs baseline {withUnit(c.target.baseline, t.unit)}</span> : null}</td>
                    <td>{c.constraints.map((x) => `${x.ok === true ? "✓" : x.ok === false ? "✗" : "?"} ${words.quantity(x.quantity).toLowerCase()}`).join(" · ")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="ws-muted">A verdict decided by the limits (the baseline breaks them, the candidate meets them) is a feasibility answer, not a speed one.</p>
      </section>
      <section aria-labelledby="ws-src-h">
        <h2 className="ws-h2" id="ws-src-h">Source and spend</h2>
        <dl className="ws-tech">
          <div><dt>Source</dt><dd>{src.kind === "live" ? `Live: ${src.api} · ledger ${src.ledger} · as of seq ${src.as_of_seq}` : `Recorded file ${origin.file ?? src.file} · sha256 ${src.sha256.slice(0, 16)}…`}</dd></div>
          {src.kind === "recorded" && <div><dt>Verification</dt><dd>{src.verified ? `${src.verified.ok ? "verified" : "failed"} ${src.verified.ran_on}: ${src.verified.summary}` : <>not verified here; run <code>translucent verify {origin.file ?? src.file}</code></>}</dd></div>}
          <div><dt>System</dt><dd>{v.campaign.system.title} (v{v.campaign.system.version}){v.campaign.system.solver ? ` · ${v.campaign.system.solver.name} ${v.campaign.system.solver.version}` : ""}</dd></div>
          {v.campaign.system.solver?.validity && <div><dt>Model validity</dt><dd>{prose(v.campaign.system.solver.validity)}</dd></div>}
          <div><dt>Planner</dt><dd>{plannerChip(v)}</dd></div>
          <div><dt>Spend</dt><dd className="num">{s.experiments} experiments · model {s.model}</dd></div>
          {v.conclusion && <div><dt>Recorded outcome</dt><dd><code>{v.conclusion.outcome}</code>{derived ? " (the outcome this campaign’s comparator recorded; the assessment above reads it as three answers)" : ""}</dd></div>}
          <div><dt>Assessment basis</dt><dd>{BASIS_WORD[a.basis] ?? a.basis}{derived ? " · derived here from the comparisons" : " · computed by the server"}</dd></div>
        </dl>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- export

function ExportButton({ v, ledger, onOpen }: { v: LoopView; ledger: string; onOpen?: (rec: Recording, file: string) => void }) {
  const [state, setState] = useState<{ busy: boolean; error: string; rec: Recording | null }>({ busy: false, error: "", rec: null });
  const file = `${v.campaign.id}.loop.json`;
  const run = async () => {
    setState({ busy: true, error: "", rec: null });
    try {
      const r = await fetch(v1.exportUrl(ledger, v.campaign.id), { credentials: "include" });
      if (r.status === 401) { session.expired(); throw new Error("Sign in to export."); }
      if (!r.ok) throw new Error(`The export failed (HTTP ${r.status}).`);
      const text = await r.text();
      const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url; link.download = file;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      setState({ busy: false, error: "", rec: parseLoopFile(JSON.parse(text)) });
    } catch (err) {
      setState({ busy: false, error: err instanceof Error ? err.message : "The export failed.", rec: null });
    }
  };
  return (
    <span className="ws-export">
      <button type="button" className="pill-link" onClick={run} disabled={state.busy}>{state.busy ? <Spinner size={12} /> : null}Export .loop.json</button>
      {state.rec && onOpen && <button type="button" className="pill-link" onClick={() => onOpen(state.rec!, file)}>Open the export as a recording</button>}
      {state.error && <span className="lp-error" role="alert">{state.error}</span>}
    </span>
  );
}

// ---------------------------------------------------------------- amend and conclude

function AmendDialog({ v, ledger, onClose, onDone }: { v: LoopView; ledger: string; onClose(): void; onDone(): void }) {
  const words = useWords(v);
  const o = v.objective;
  const [limits, setLimits] = useState<string[]>(() => o.constraints.map((c) => String(c.limit)));
  const [target, setTarget] = useState(o.target.limit != null ? String(o.target.limit) : "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; reasons: string[] } | null>(null);
  const idem = useRef(newIdempotencyKey());
  const changed = o.constraints.map((c, i) => limits[i].trim() !== "" && Number(limits[i]) !== c.limit);
  const targetChanged = o.target.limit != null && target.trim() !== "" && Number(target) !== o.target.limit;
  const valid = limits.every((l) => l.trim() !== "" && Number.isFinite(Number(l))) && (o.target.limit == null || Number.isFinite(Number(target)));
  const any = changed.some(Boolean) || targetChanged;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!any || !valid || !reason.trim()) return;
    setBusy(true);
    setError(null);
    const constraints = o.constraints.map((c, i) => {
      const n = Number(limits[i]);
      return { quantity: c.quantity, op: c.op, limit: n, unit: c.unit,
        label: relabel(c.label ?? "", c.limit, n, plainConstraintLabel(words.quantity(c.quantity), c.op as "<=" | ">=", n, c.unit)) };
    });
    const { label: _label, ...t } = o.target;
    try {
      await v1.amend(ledger, v.campaign.id, { reason: reason.trim(), constraints, ...(targetChanged ? { target: { ...t, limit: Number(target) } } : {}) }, idem.current);
      onDone();
    } catch (err) {
      const x = err instanceof ApiError ? err : null;
      setError({ text: x ? (x.status === 422 ? "The server refused the amendment:" : x.status === 409 ? `The campaign moved on: ${x.message}` : x.status === 403 ? `Refused: ${x.message}` : x.unreachable ? "The server didn’t answer; nothing was recorded." : x.message) : "The amendment failed.", reasons: x?.reasons ?? [] });
      setBusy(false);
    }
  };
  return (
    <Dialog title={`Amend the objective (version ${o.version} → ${o.version + 1})`} onClose={onClose} wide>
      <form className="ws-amend" onSubmit={submit}>
        <p className="ws-dialog-lead">An amendment is recorded under your name as the objective’s next version. Results already compared keep their version’s verdict; the planner’s next proposal works to the new limits.</p>
        <table className="ws-table is-tight">
          <thead><tr><th scope="col">Constraint</th><th scope="col">Now</th><th scope="col">New limit</th></tr></thead>
          <tbody>
            {o.constraints.map((c, i) => (
              <tr key={c.quantity} className={changed[i] ? "is-changed" : ""}>
                <th scope="row">{words.quantity(c.quantity)}</th>
                <td className="num">{opWord(c.op)} {withUnit(c.limit, c.unit)}</td>
                <td><span className="ws-limit"><span aria-hidden>{opWord(c.op)}</span><input className="field num" aria-label={`New limit for ${words.quantity(c.quantity)} in ${unitWord(c.unit)}`} inputMode="decimal" value={limits[i]} data-autofocus={i === 0 ? true : undefined}
                  onChange={(e) => setLimits((ls) => ls.map((l, j) => (j === i ? e.target.value.replace(/[^\d.-]/g, "") : l)))} /><span className="ws-unit">{unitWord(c.unit)}</span></span></td>
              </tr>
            ))}
            {o.target.limit != null && (
              <tr className={targetChanged ? "is-changed" : ""}>
                <th scope="row">Hard target: {o.target.label || words.quantity(o.target.quantity)}</th>
                <td className="num">{withUnit(o.target.limit, o.target.unit)}</td>
                <td><span className="ws-limit"><input className="field num" aria-label="New hard target" inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value.replace(/[^\d.-]/g, ""))} /><span className="ws-unit">{unitWord(o.target.unit)}</span></span></td>
              </tr>
            )}
          </tbody>
        </table>
        <label className="ws-label" htmlFor="ws-amend-why">Reason (recorded, and read by the planner)</label>
        <textarea id="ws-amend-why" className="field ws-textarea" rows={3} maxLength={10000} value={reason} onChange={(e) => setReason(e.target.value)} />
        {error && <div className="lp-error" role="alert"><p>{error.text}</p>{error.reasons.length > 0 && <ul>{error.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}</div>}
        <div className="ws-dialog-foot">
          <button type="button" className="btn btn-small btn-quiet" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-small" disabled={busy || !any || !valid || !reason.trim()}>{busy ? <Spinner size={14} /> : null}Record amendment</button>
        </div>
      </form>
    </Dialog>
  );
}

function ConcludeDialog({ v, a, ledger, onClose, onDone }: { v: LoopView; a: Assessment; ledger: string; onClose(): void; onDone(): void }) {
  const [text, setText] = useState("");
  const [unresolved, setUnresolved] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const idem = useRef(newIdempotencyKey());
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError("");
    try {
      await v1.conclude(ledger, v.campaign.id, { text: text.trim(), unresolved_reasons: unresolved.split("\n").map((x) => x.trim()).filter(Boolean) }, idem.current);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? (err.unreachable ? "The server didn’t answer; nothing was recorded." : err.status === 403 ? `Refused: ${err.message}` : err.message) : "Concluding failed.");
      setBusy(false);
    }
  };
  return (
    <Dialog title="Conclude the campaign" onClose={onClose} wide>
      <form className="ws-amend" onSubmit={submit}>
        <p className="ws-dialog-lead">The server computes the outcome from the recorded comparisons; you write what it means. After this every write to the campaign is refused.</p>
        <p className="ws-headline">Standing now: {prose(a.headline)}</p>
        <label className="ws-label" htmlFor="ws-concl-text">Conclusion</label>
        <textarea id="ws-concl-text" className="field ws-textarea" rows={4} maxLength={10000} value={text} onChange={(e) => setText(e.target.value)} data-autofocus />
        <label className="ws-label" htmlFor="ws-concl-un">Unresolved (one per line, optional)</label>
        <textarea id="ws-concl-un" className="field ws-textarea" rows={3} value={unresolved} onChange={(e) => setUnresolved(e.target.value)} />
        {error && <p className="lp-error" role="alert">{error}</p>}
        <div className="ws-dialog-foot">
          <button type="button" className="btn btn-small btn-quiet" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-small" disabled={busy || !text.trim()}>{busy ? <Spinner size={14} /> : null}Conclude</button>
        </div>
      </form>
    </Dialog>
  );
}
