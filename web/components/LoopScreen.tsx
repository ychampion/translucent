"use client";
// A loop campaign: what the engineer asked for, where the best candidate stands against the failing baseline, what the
// agent wants to run next and why (approve or reject it here), every experiment's state and returned measurements,
// and the spend. Live from the server (/w/?ledger=<lid>&id=<cid>, refreshed on each `loop.recorded` event) or a
// recorded export (/w/?recording=<name>, or a file opened here), which has no write controls.
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, loadRecording, newIdempotencyKey, v1 } from "@/lib/api";
import {
  actorWord, bannerLine, baselineFor, BASIS_WORD, bestComparison, byRecency, canAct, change, constraintLine, constraintOf,
  executorWord, humanize, limitFor, num, OUTCOME_WORD, parseLoopFile, predictedLine, RECORDING_NAME, requestTitle,
  seriesFromBlob, spendLines, standing, STATE_SHORT, STATE_WORD, stateCounts, stateTone, targetLine, thin, valueLine,
  categoriesLine, showsH, validityWarning, VERDICT_WORD, withUnit,
  type Baseline, type Comparison, type LoopRequest, type LoopView, type NextAction, type PackedFile, type Series,
} from "@/lib/loop";
import { identity } from "@/lib/people";
import { apiConfigured } from "@/lib/sources";
import { useWidth } from "./DecisionMoment";
import { IconCheck, IconClose, IconUpload, Spinner } from "./Icons";
import { PageHeader } from "./Shell";
import { LoadState } from "./Status";

type Where = { mode: "live"; ledger: string; id: string } | { mode: "recording"; name: string } | { mode: "file" };
type Stream = "connecting" | "ok" | "retrying" | "lost";

/** Everything the body needs about where the view came from. */
interface Origin {
  mode: "live" | "recorded";
  ledger?: string;
  /** Live: seconds since the view was fetched. */
  fetchedAt?: number;
  stream?: Stream;
  /** Recorded: the file's name, whether it carried a receipt, and its series bytes. */
  file?: string;
  receipt?: boolean;
  blobs?: Record<string, PackedFile>;
  refresh?: () => void;
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

/** Debounce for refetching after a burst of `loop.recorded` events (design §2.12). */
const REFRESH_MS = 500;

function useLiveView(ledger: string, id: string) {
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

  useEffect(() => { refresh(); }, [refresh, attempt]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const es = new EventSource(v1.eventsUrl(ledger, id));
    const onEvent = (m: MessageEvent) => {
      setStream("ok");
      let seq = Infinity;
      try { seq = (JSON.parse(m.data) as { seq?: number }).seq ?? Infinity; } catch { /* refresh anyway */ }
      if (seq <= seen.current) return;   // the stream replays from seq 0; the view already holds these
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { timer = null; refresh(); }, REFRESH_MS);
    };
    // Loop records change the view; cost updates change the spend.
    for (const t of ["loop.recorded", "cost.updated"]) es.addEventListener(t, onEvent as EventListener);
    es.onopen = () => setStream("ok");
    es.onerror = () => {
      if (concluded.current) { es.close(); setStream("ok"); return; }   // don't reconnect to a finished campaign
      setStream(es.readyState === EventSource.CLOSED ? "lost" : "retrying");
    };
    return () => { es.close(); if (timer) clearTimeout(timer); };
  }, [ledger, id, refresh]);

  return { view, error, fetchedAt, stream, refresh, retry: () => setAttempt((n) => n + 1) };
}

function LiveLoop({ ledger, id }: { ledger: string; id: string }) {
  const live = useLiveView(ledger, id);
  if (!live.view) {
    return (
      <div className="screen loading">
        <PageHeader title="Campaign" />
        {!apiConfigured() && live.error ? <p className="lp-plain">This page needs the team server. Set NEXT_PUBLIC_API_BASE, or open a recorded export.</p> : null}
        <LoadState error={live.error} what="this campaign" onRetry={live.retry} />
      </div>
    );
  }
  return <Body v={live.view} origin={{ mode: "live", ledger, fetchedAt: live.fetchedAt, stream: live.stream, refresh: live.refresh }} loadError={live.error} />;
}

// ---------------------------------------------------------------- recorded

function RecordedLoop({ name }: { name: string }) {
  const [rec, setRec] = useState<Awaited<ReturnType<typeof loadRecording>> | null>(null);
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
        <LoadState error={error} what="this recording" onRetry={() => setAttempt((n) => n + 1)} />
      </div>
    );
  }
  return <Body v={rec.view} origin={{ mode: "recorded", file: `${name.replace(/\.loop\.json$/, "")}.loop.json`, receipt: rec.receipt, blobs: rec.blobs }} />;
}

/** No address: open a `.loop.json` from this computer. Read in the browser only; nothing is sent anywhere. */
function OpenFile() {
  const [rec, setRec] = useState<{ view: LoopView; blobs: Record<string, PackedFile>; receipt: boolean; file: string } | null>(null);
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
  if (rec) return <Body v={rec.view} origin={{ mode: "recorded", file: rec.file, receipt: rec.receipt, blobs: rec.blobs }} />;
  return (
    <div className="screen">
      <PageHeader title="Open a recorded campaign" />
      <main className="lp-main lp-open">
        <p className="lp-plain">Choose a loop export (<code>&lt;name&gt;.loop.json</code>, from <code>translucent export</code> or a campaign’s Export link). It is read in this browser only.</p>
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

// ---------------------------------------------------------------- the page

function Body({ v, origin, loadError }: { v: LoopView; origin: Origin; loadError?: unknown }) {
  useEffect(() => { document.title = `${v.campaign.title} – Translucent`; }, [v.campaign.title]);
  const best = useMemo(() => bestComparison(v), [v]);
  const [chartFor, setChartFor] = useState<string | null>(null);
  const charted = useMemo(() => {
    const withSeries = (r: LoopRequest | undefined) => (r?.result?.series.length ? r : undefined);
    return withSeries(v.requests.find((r) => r.request_id === chartFor))
      ?? withSeries(v.requests.find((r) => r.request_id === best?.request_id))
      ?? byRecency(v).find((r) => r.result?.series.length);
  }, [v, chartFor, best]);

  return (
    <div className={`screen lp-screen is-${origin.mode}`}>
      <PageHeader title={v.campaign.title}>
        {origin.mode === "live" && origin.ledger && (
          <a className="pill-link" href={v1.exportUrl(origin.ledger, v.campaign.id)} download>Export</a>
        )}
      </PageHeader>
      <Banner v={v} origin={origin} />
      <main className="lp-main">
        {loadError ? <p className="lp-error" role="status">Couldn’t refresh: {loadError instanceof Error ? loadError.message : "the server didn’t answer"}. Showing the last view.</p> : null}
        <Objective v={v} />
        <Standing v={v} best={best} />
        <Next v={v} origin={origin} />
        {v.conclusion && <ConclusionCard v={v} />}
        {charted && <Measurements v={v} r={charted} origin={origin} />}
        <Experiments v={v} origin={origin} charted={charted?.request_id ?? null} onChart={setChartFor} />
      </main>
    </div>
  );
}

/** Live or recorded, said so it can't be missed: a persistent band under the header. */
function Banner({ v, origin }: { v: LoopView; origin: Origin }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (origin.mode !== "live") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [origin.mode]);
  const since = origin.fetchedAt ? (now - origin.fetchedAt) / 1000 : undefined;
  const { tag, line } = bannerLine(origin.mode, v, { ledger: origin.ledger, secondsSince: since, file: origin.file, receipt: origin.receipt });
  const verified = v.source.kind === "recorded" ? v.source.verified : null;
  return (
    <div className={`lp-banner is-${origin.mode}`} role="status" aria-live="off">
      <span className="lp-banner-tag">{origin.mode === "live" && <span className="lp-live-dot" aria-hidden />}{tag}</span>
      <span className="lp-banner-line">{line}</span>
      {origin.mode === "live" && origin.stream && origin.stream !== "ok" && (
        <span className="lp-banner-warn">{origin.stream === "connecting" ? "connecting to updates…" : origin.stream === "retrying" ? "updates paused, reconnecting…" : "updates stopped; reload to resume"}</span>
      )}
      {origin.mode === "recorded" && <span className="lp-banner-note">Read only{verified?.command ? <> · <code>{verified.command}</code></> : null}</span>}
    </div>
  );
}

// ---------------------------------------------------------------- objective

function Objective({ v }: { v: LoopView }) {
  const o = v.objective, sys = v.campaign.system;
  const last = o.amendments[o.amendments.length - 1];
  return (
    <header className="lp-head">
      <p className="lp-eyebrow">
        {sys.title}{sys.solver ? <> · solver {sys.solver.name} {sys.solver.version}</> : null}
        {v.campaign.planner.models.planner ? <> · agent {v.campaign.planner.models.planner}</> : null}
      </p>
      <h1 className="lp-statement">{o.statement}</h1>
      <ul className="lp-goals" aria-label="Objective">
        <li className="lp-goal is-target"><span className="lp-goal-k">Goal</span>{targetLine(o.target)}{o.target.limit != null ? ` (to ${withUnit(o.target.limit, o.target.unit)})` : ""}</li>
        {o.constraints.map((c) => <li key={c.quantity} className="lp-goal"><span className="lp-goal-k">Must hold</span>{constraintLine(c)}</li>)}
      </ul>
      {last && (
        <p className="lp-amended">Objective version {o.version}: {last.author} amended it ({last.changes.join("; ") || "no change"}). {last.reason}</p>
      )}
      <Spend v={v} />
    </header>
  );
}

function Spend({ v }: { v: LoopView }) {
  const s = spendLines(v.spend);
  return (
    <dl className="lp-spend">
      <div><dt>Model spend</dt><dd className="num">{s.model}</dd><Meter share={s.modelShare} /></div>
      <div><dt>Experiments</dt><dd className="num">{s.experiments}</dd><Meter share={s.experimentShare} /></div>
    </dl>
  );
}
const Meter = ({ share }: { share: number }) => (
  <span className="lp-meter" aria-hidden><span style={{ width: `${Math.round(share * 100)}%` }} /></span>
);

// ---------------------------------------------------------------- baseline versus the best candidate

function Standing({ v, best }: { v: LoopView; best: Comparison | null }) {
  const st = standing(v);
  const base = baselineFor(v, best?.basis);
  const t = v.objective.target;
  const req = best ? v.requests.find((r) => r.request_id === best.request_id) : undefined;
  const ch = best ? change(best) : null;
  const byLimits = best?.target.decided_by === "feasibility";
  const warn = validityWarning(req?.result);
  return (
    <section className="lp-standing" aria-labelledby="lp-standing-h">
      <div className="lp-section-head">
        <h2 className="lp-h2" id="lp-standing-h">Baseline against the best candidate</h2>
        {st ? (
          <span className={`lp-outcome is-${st.outcome}`}>{OUTCOME_WORD[st.outcome]}{st.final ? "" : " so far"}</span>
        ) : <span className="lp-outcome is-none">No result yet</span>}
      </div>
      {v.current_result && <p className="lp-sentence">{v.current_result.sentence}</p>}
      <div className="lp-pair">
        <Side title="Failing baseline" tone="base" basis={base?.basis}
          value={base?.values[t.quantity] ? valueLine(base.values[t.quantity]) : best?.target.baseline != null ? withUnit(best.target.baseline, t.unit) : "–"}
          label={t.label} checks={base ? baseChecks(v, base) : []} sub={base ? (base.refs.length ? `From ${base.refs.join(", ")}` : "") : "No baseline recorded yet."} />
        <Side title="Best candidate" tone={best?.verdict === "candidate_better" ? "good" : best?.verdict === "not_better" || best?.verdict === "violates_constraints" ? "bad" : "none"} basis={best?.basis}
          value={best?.target.candidate != null ? withUnit(best.target.candidate, t.unit) : "–"} label={t.label}
          delta={ch ? `${ch.delta > 0 ? "+" : ""}${withUnit(ch.delta, t.unit)} against the baseline${byLimits ? ", the cost of meeting the limits it breaks" : ""}` : undefined}
          deltaGood={byLimits ? null : ch?.better ?? null} warn={warn}
          checks={best ? best.constraints.map((c) => ({ label: constraintOf(v, c.quantity) ? constraintLine(constraintOf(v, c.quantity)!) : humanize(c.quantity), value: withUnit(c.value, constraintOf(v, c.quantity)?.unit ?? ""), ok: c.ok })) : []}
          sub={best ? `${req ? requestTitle(req) : best.request_id} · ${VERDICT_WORD[best.verdict]}${best.reasons.length ? ` (${best.reasons.join("; ")})` : ""}` : "No experiment has returned a result yet."} />
      </div>
    </section>
  );
}

function baseChecks(v: LoopView, b: Baseline) {
  return b.constraints.map((c) => {
    const con = constraintOf(v, c.quantity);
    return { label: con ? constraintLine(con) : humanize(c.quantity), value: withUnit(c.value, con?.unit ?? b.values[c.quantity]?.unit ?? ""), ok: c.ok };
  });
}

function Side({ title, tone, basis, value, label, delta, deltaGood, warn, checks, sub }: {
  title: string; tone: "base" | "good" | "bad" | "none"; basis?: string; value: string; label: string;
  delta?: string; deltaGood?: boolean | null; warn?: string | null; checks: { label: string; value: string; ok: boolean | null }[]; sub?: string;
}) {
  return (
    <div className={`lp-side is-${tone}`}>
      <p className="lp-side-title">{title}{basis && <span className={`lp-basis is-${basis}`}>{BASIS_WORD[basis] ?? basis}{basis === "computational" ? " · computational" : ""}</span>}</p>
      <p className="lp-side-value num">{value}</p>
      <p className="lp-side-label">{label}</p>
      {warn && <p className="lp-warn" role="note">{warn}</p>}
      {delta && <p className={`lp-delta num ${deltaGood === true ? "is-good" : deltaGood === false ? "is-bad" : ""}`}>{delta}</p>}
      {checks.length > 0 && (
        <ul className="lp-checks">
          {checks.map((c) => (
            <li key={c.label} className={c.ok === true ? "is-ok" : c.ok === false ? "is-fail" : ""}>
              <span className="lp-check-mark" aria-label={c.ok === true ? "holds" : c.ok === false ? "broken" : "unknown"}>{c.ok === true ? <IconCheck size={14} /> : c.ok === false ? <IconClose size={14} /> : "?"}</span>
              <span>{c.label}</span><span className="num lp-check-v">{c.value}</span>
            </li>
          ))}
        </ul>
      )}
      {sub && <p className="lp-side-sub">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- what happens next

function Next({ v, origin }: { v: LoopView; origin: Origin }) {
  const n = v.next_action;
  if (n.kind === "none") {
    if (v.conclusion) return null;
    return <section className="lp-next is-idle"><p className="lp-next-k">Next</p><p className="lp-next-title">Nothing is pending. The agent proposes the next experiment when its turn comes.</p></section>;
  }
  if (n.kind === "planner_thinking") {
    return (
      <section className="lp-next is-busy" aria-live="polite">
        <p className="lp-next-k"><Spinner size={14} /> The agent is choosing the next experiment</p>
        <p className="lp-next-title">{n.title}</p>
      </section>
    );
  }
  const req = v.requests.find((r) => r.request_id === n.request_id);
  if (n.kind === "awaiting_approval") return <Proposal v={v} n={n} req={req} origin={origin} />;
  return (
    <section className={`lp-next ${n.kind === "running" ? "is-busy" : "is-wait"}`} aria-live="polite">
      <p className="lp-next-k">{n.kind === "running" ? <><Spinner size={14} /> Running now</> : "Waiting for a person"}</p>
      <p className="lp-next-title">{n.title}</p>
      <p className="lp-next-meta">{n.kind === "running" ? executorWord(n.executor) : "Approved. A person runs it and returns the measured result (HTTP or MCP)."}{req?.approved_by ? ` · approved by ${actorWord(req.approved_by)}` : ""}</p>
    </section>
  );
}

function Proposal({ v, n, req, origin }: { v: LoopView; n: Extract<NextAction, { kind: "awaiting_approval" }>; req?: LoopRequest; origin: Origin }) {
  const pred = Object.entries(n.prediction);
  return (
    <section className="lp-next is-ask" aria-labelledby="lp-next-h">
      <p className="lp-next-k">The agent proposes{req ? ` · ${actorWord(req.proposed_by)}` : ""}{n.version > 1 ? ` · version ${n.version}` : ""}</p>
      <h2 className="lp-next-title" id="lp-next-h">{n.title}</h2>
      <div className="lp-why">
        <div><p className="lp-why-k">Why</p><p>{n.rationale}</p></div>
        <div><p className="lp-why-k">What it expects</p><p>{n.expected_effect}</p></div>
      </div>
      {pred.length > 0 && (
        <table className="lp-pred">
          <caption className="visually-hidden">Prediction</caption>
          <thead><tr><th scope="col">Predicted</th><th scope="col">Value (range)</th><th scope="col">Baseline</th></tr></thead>
          <tbody>
            {pred.map(([q, p]) => {
              const b = baselineFor(v, n.executor === "computational" ? "computational" : "physical")?.values[q];
              return <tr key={q}><th scope="row">{q === v.objective.target.quantity ? v.objective.target.label : constraintOf(v, q)?.label ?? humanize(q)}</th><td className="num">{predictedLine(p)}</td><td className="num soft">{b ? valueLine(b) : "–"}</td></tr>;
            })}
          </tbody>
        </table>
      )}
      <p className="lp-next-meta">
        {executorWord(n.executor)}
        {n.cost?.wall_s != null ? ` · about ${withUnit(Number(n.cost.wall_s), "s")}` : ""}
        {n.cost?.usd != null ? ` · $${num(Number(n.cost.usd))}` : ""}
        {" · "}<span className="lp-hash" title={n.content_hash}>content {n.content_hash.slice(0, 12)}</span>
      </p>
      <Decide v={v} requestId={n.request_id} hash={n.content_hash} origin={origin} />
    </section>
  );
}

/** Approve or reject one version, by the content hash the person read. Live only, with a name and the team key. */
function Decide({ v, requestId, hash, origin }: { v: LoopView; requestId: string; hash: string; origin: Origin }) {
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [signing, setSigning] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"" | "approve" | "reject">("");
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const idem = useRef<Record<string, string>>({});
  useEffect(() => { setName(identity.name()); setKey(identity.key()); }, []);
  useEffect(() => { setRejecting(false); setReason(""); setNote(null); idem.current = {}; }, [hash]);
  if (origin.mode !== "live" || !origin.ledger) return null;
  const ledger = origin.ledger;
  const able = canAct("live", key, name);

  if (!able || signing) {
    return (
      <form className="lp-sign" onSubmit={(e) => { e.preventDefault(); identity.setName(name); identity.setKey(key); setSigning(false); }}>
        <p className="lp-sign-lead">To approve or reject, say who you are and enter the team key.</p>
        <input className="field" placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" aria-label="Your name" />
        <input className="field" type="password" placeholder="Team key" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" aria-label="Team key" />
        <button className="btn btn-small" type="submit" disabled={!name.trim() || !key.trim()}>Continue</button>
      </form>
    );
  }

  const act = async (what: "approve" | "reject") => {
    setBusy(what);
    setNote(null);
    const k = idem.current[what] ??= newIdempotencyKey();
    try {
      if (what === "approve") await v1.approve(ledger, v.campaign.id, requestId, { author: name.trim(), content_hash: hash }, key.trim(), k);
      else await v1.reject(ledger, v.campaign.id, requestId, { author: name.trim(), content_hash: hash, reason: reason.trim() }, key.trim(), k);
      setNote({ text: what === "approve" ? `Approved by ${name.trim()}. Recorded.` : `Rejected by ${name.trim()}. Recorded.`, error: false });
      setRejecting(false);
      origin.refresh?.();
    } catch (err) {
      if (err instanceof ApiError && err.stale) {
        setNote({ text: `This proposal changed or was decided after you opened it${err.message ? ` (${err.message})` : ""}. The latest is shown now; read it again before deciding.`, error: true });
        origin.refresh?.();
      } else if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setNote({ text: "That team key didn’t work.", error: true });
        setSigning(true);
      } else {
        setNote({ text: err instanceof Error ? err.message : "That didn’t go through.", error: true });
      }
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="lp-decide">
      {rejecting ? (
        <form className="lp-reject" onSubmit={(e) => { e.preventDefault(); if (reason.trim()) act("reject"); }}>
          <label className="visually-hidden" htmlFor={`why-${requestId}`}>Why reject it</label>
          <input id={`why-${requestId}`} className="field" placeholder="Why not? The agent reads this." value={reason} onChange={(e) => setReason(e.target.value)} autoFocus maxLength={2000} />
          <button className="btn btn-small" type="submit" disabled={!reason.trim() || !!busy}>{busy === "reject" ? <Spinner size={14} /> : null}Reject</button>
          <button className="btn btn-small btn-quiet" type="button" onClick={() => setRejecting(false)}>Back</button>
        </form>
      ) : (
        <div className="lp-decide-row">
          <button className="btn lp-approve" onClick={() => act("approve")} disabled={!!busy}>{busy === "approve" ? <Spinner size={14} /> : <IconCheck size={16} />}Approve and run</button>
          <button className="btn btn-quiet" onClick={() => setRejecting(true)} disabled={!!busy}>Reject…</button>
          <span className="lp-as">as {name} · <button className="text-quiet lp-switch" onClick={() => setSigning(true)}>change</button></span>
        </div>
      )}
      {note && <p className={note.error ? "lp-error" : "lp-ok"} role={note.error ? "alert" : "status"}>{note.text}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- the conclusion

function ConclusionCard({ v }: { v: LoopView }) {
  const c = v.conclusion!;
  return (
    <section className={`lp-conclusion is-${c.outcome}`} aria-labelledby="lp-concl-h">
      <p className="lp-next-k" id="lp-concl-h">Concluded · {OUTCOME_WORD[c.outcome]}</p>
      <p className="lp-concl-text">{c.text}</p>
      {c.unresolved_reasons.length > 0 && (
        <ul className="lp-unresolved">{c.unresolved_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- returned measurements

function Measurements({ v, r, origin }: { v: LoopView; r: LoopRequest; origin: Origin }) {
  const res = r.result!;
  const [qi, setQi] = useState(0);
  useEffect(() => { setQi(0); }, [r.request_id]);
  const ref = res.series[Math.min(qi, res.series.length - 1)];
  const [lines, setLines] = useState<{ cand: Series | null; base: Series | null; error: string } | null>(null);
  const base = baselineFor(v, res.basis);
  // A computational baseline's series are its registration re-run's; a record has its own. None for the re-run itself.
  const baseRun = base?.basis === "computational"
    ? v.requests.find((q) => q.proposed_by === "policy:registration" && q.state === "completed") : undefined;
  const baseRef = r.proposed_by === "policy:registration" ? undefined : baseRun?.request_id ?? base?.refs[0];

  useEffect(() => {
    let gone = false;
    setLines(null);
    (async () => {
      try {
        if (origin.mode === "live" && origin.ledger) {
          const cand = await v1.series(origin.ledger, v.campaign.id, r.request_id, ref.quantity);
          // The baseline's own series of the same quantity, when it was recorded (a record or a baseline run).
          const b = baseRef ? await v1.series(origin.ledger, v.campaign.id, baseRef, ref.quantity).catch(() => null) : null;
          if (!gone) setLines({ cand, base: b, error: "" });
        } else {
          const cand = await seriesFromBlob(origin.blobs ?? {}, ref.url);
          if (!gone) setLines({ cand: cand && thin(cand), base: null, error: cand ? "" : "This file doesn’t carry the series bytes." });
        }
      } catch (err) {
        if (!gone) setLines({ cand: null, base: null, error: err instanceof Error ? err.message : "Couldn’t load the series." });
      }
    })();
    return () => { gone = true; };
  }, [v.campaign.id, r.request_id, ref.quantity, ref.url, origin.mode, origin.ledger, origin.blobs, baseRef]);

  const limit = lines?.cand ? limitFor(v, ref.quantity, lines.cand.unit) : null;
  const errs = Object.entries(res.prediction_error);
  return (
    <section className="lp-measure" aria-labelledby="lp-measure-h">
      <div className="lp-section-head">
        <h2 className="lp-h2" id="lp-measure-h">Returned measurements</h2>
        <span className="lp-side-sub">{requestTitle(r)} · <span className={`lp-basis is-${res.basis}`}>{BASIS_WORD[res.basis] ?? res.basis}</span></span>
      </div>
      {res.series.length > 1 && (
        <div className="iv-splits" role="tablist" aria-label="Series">
          {res.series.map((s, i) => <button key={s.quantity} role="tab" aria-selected={i === qi} className={`iv-split ${i === qi ? "is-on" : ""}`} onClick={() => setQi(i)}>{humanize(s.quantity)}</button>)}
        </div>
      )}
      <div className="lp-measure-grid">
        <figure className="lp-figure">
          {lines?.cand ? (
            <Chart cand={lines.cand} base={lines.base} limit={limit} label={humanize(ref.quantity)} simulated={res.basis === "computational"} />
          ) : (
            <div className="lp-chart-empty">{lines?.error ? lines.error : <Spinner size={18} />}</div>
          )}
          <figcaption className="lp-caption">
            {humanize(ref.quantity)} over time{lines?.cand?.downsampled ? `, ${lines.cand.t.length} of ${lines.cand.n} points` : ""}
            {res.basis === "computational" ? " · computational result (simulation), not a bench measurement" : ""}
          </figcaption>
        </figure>
        <div className="lp-values">
          <table className="lp-pred">
            <thead><tr><th scope="col">Measured</th><th scope="col">Value</th><th scope="col">Predicted</th></tr></thead>
            <tbody>
              {res.measurements.map((m) => {
                const e = res.prediction_error[m.quantity];
                return (
                  <tr key={m.id}>
                    <th scope="row">{m.quantity === v.objective.target.quantity ? v.objective.target.label : humanize(m.quantity)}</th>
                    <td className="num">{withUnit(m.value, m.unit)}{showsH(m.value, m.h) ? ` ± ${num(m.h)}` : ""}</td>
                    <td className={`num ${e ? (e.inside === false ? "is-bad" : e.inside ? "is-good" : "") : "soft"}`}>{e ? `${num(e.predicted)} ${e.inside === false ? "· outside the range" : e.inside ? "· inside" : ""}` : "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {validityWarning(res) && <p className="lp-warn" role="note">{validityWarning(res)}</p>}
          {categoriesLine(res) && <p className="lp-side-sub">{categoriesLine(res)}</p>}
          {errs.length > 0 && <p className="lp-side-sub">{errs.filter(([, e]) => e.inside).length} of {errs.length} predictions held inside the agent’s range.</p>}
        </div>
      </div>
    </section>
  );
}

/** A plain series chart: the candidate, the baseline when there is one, and the constraint limit as a dashed line. */
function Chart({ cand, base, limit, label, simulated }: { cand: Series; base: Series | null; limit: { value: number; label: string } | null; label: string; simulated: boolean }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const h = 260, pad = { l: 46, r: 14, t: 14, b: 30 };
  const all = [cand, ...(base ? [base] : [])];
  const tMax = Math.max(...all.map((s) => s.t[s.t.length - 1] ?? 0), 1);
  const ys = all.flatMap((s) => s.y).concat(limit ? [limit.value] : []);
  let yMin = Math.min(...ys), yMax = Math.max(...ys);
  if (yMax - yMin < 1e-9) { yMin -= 1; yMax += 1; }
  const span = yMax - yMin;
  yMin -= span * 0.06; yMax += span * 0.06;
  const minutes = tMax > 600;
  const X = (t: number) => pad.l + (t / tMax) * Math.max(10, w - pad.l - pad.r);
  const Y = (y: number) => pad.t + (1 - (y - yMin) / (yMax - yMin)) * (h - pad.t - pad.b);
  const path = (s: Series) => s.t.map((t, i) => `${i ? "L" : "M"}${X(t).toFixed(1)},${Y(s.y[i]).toFixed(1)}`).join("");
  const yt = ticks(yMin, yMax, 5), xt = ticks(0, minutes ? tMax / 60 : tMax, 6);
  const u = cand.unit === "degC" ? "°C" : cand.unit;
  return (
    <div ref={ref} className="lp-chart">
      {w > 0 && (
        <svg width={w} height={h} role="img" aria-label={`${label} (${u}) against time, ${simulated ? "simulated" : "measured"} candidate${base ? " and the baseline" : ""}${limit ? `, limit ${num(limit.value)} ${u}` : ""}`} className="iv-svg">
          {yt.map((y) => <g key={`y${y}`}><line className="iv-grid" x1={pad.l} x2={w - pad.r} y1={Y(y)} y2={Y(y)} /><text className="iv-tick" x={pad.l - 6} y={Y(y) + 4} textAnchor="end">{num(y)}</text></g>)}
          {xt.map((x) => <text key={`x${x}`} className="iv-tick" x={X(minutes ? x * 60 : x)} y={h - 10} textAnchor="middle">{num(x)}</text>)}
          <text className="iv-tick" x={w - pad.r} y={h - 10} textAnchor="end" dy="-12">{minutes ? "min" : "s"}</text>
          <text className="iv-tick" x={pad.l - 6} y={pad.t - 2} textAnchor="end">{u}</text>
          {limit && <g><line className="lp-limit" x1={pad.l} x2={w - pad.r} y1={Y(limit.value)} y2={Y(limit.value)} /><text className="lp-limit-label" x={w - pad.r} y={Y(limit.value) - 5} textAnchor="end">{limit.label}</text></g>}
          {base && <path className="lp-line is-base" d={path(base)} />}
          <path className={`lp-line is-cand ${simulated ? "is-sim" : ""}`} d={path(cand)} />
        </svg>
      )}
      <div className="lp-legend">
        <span><i className={`lp-key is-cand ${simulated ? "is-sim" : ""}`} />Candidate{simulated ? " (simulated)" : ""}</span>
        {base && <span><i className="lp-key is-base" />Baseline</span>}
        {limit && <span><i className="lp-key is-limit" />Limit</span>}
      </div>
    </div>
  );
}

/** About `n` round tick values between lo and hi. */
function ticks(lo: number, hi: number, n: number): number[] {
  const raw = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let x = Math.ceil(lo / step) * step; x <= hi + 1e-9; x += step) out.push(+x.toFixed(10));
  return out;
}

// ---------------------------------------------------------------- every experiment

function Experiments({ v, origin, charted, onChart }: { v: LoopView; origin: Origin; charted: string | null; onChart(id: string): void }) {
  const list = byRecency(v);
  const counts = stateCounts(v);
  const nextId = v.next_action.kind === "awaiting_approval" ? v.next_action.request_id : null;
  return (
    <section className="lp-experiments" aria-labelledby="lp-exp-h">
      <div className="lp-section-head">
        <h2 className="lp-h2" id="lp-exp-h">Experiments</h2>
        <span className="lp-counts">{counts.map(([s, n]) => <span key={s} className={`lp-state is-${stateTone(s)}`}>{n} {STATE_SHORT[s].toLowerCase()}</span>)}</span>
      </div>
      {list.length === 0 && <p className="lp-plain">No experiments yet. The agent’s first proposal appears here.</p>}
      <ol className="lp-list">
        {list.map((r) => {
          const n = v.requests.indexOf(r) + 1;
          const last = r.timeline[r.timeline.length - 1];
          return (
            <li key={r.request_id} className={`lp-req ${r.request_id === charted ? "is-charted" : ""}`}>
              <div className="lp-req-top">
                <span className={`lp-state is-${stateTone(r.state)}`} title={STATE_WORD[r.state]}>{STATE_SHORT[r.state]}</span>
                <span className="lp-req-n num">#{n}{r.version > 1 ? ` v${r.version}` : ""}</span>
                <span className="lp-req-title">{requestTitle(r)}</span>
                <span className="lp-req-exec">{r.executor === "computational" ? "simulation" : r.executor === "human" ? "bench" : r.executor}</span>
              </div>
              <p className="lp-req-who">
                Proposed by {actorWord(r.proposed_by)}{r.approved_by ? ` · approved by ${actorWord(r.approved_by)}` : ""}
                {last && r.state !== "awaiting_approval" && last.actor !== r.approved_by && last.actor !== r.proposed_by ? ` · ${STATE_SHORT[r.state].toLowerCase()} by ${actorWord(last.actor)}` : ""}
              </p>
              {r.rationale && <p className="lp-req-why">{r.rationale}</p>}
              {r.result && (
                <p className="lp-req-result num">
                  {r.result.measurements.slice(0, 4).map((m) => `${humanize(m.quantity)} ${withUnit(m.value, m.unit)}`).join(" · ")}
                  {r.result.series.length > 0 && r.request_id !== charted && <> · <button className="text-link lp-chart-btn" onClick={() => onChart(r.request_id)}>show the series</button></>}
                </p>
              )}
              {validityWarning(r.result) && <p className="lp-warn is-inline" role="note">{validityWarning(r.result)}</p>}
              {r.rejections.map((x) => <p key={x.seq} className="lp-req-refused">A returned result was refused: {x.reasons.join("; ")}</p>)}
              {r.state === "awaiting_approval" && r.request_id !== nextId && <Decide v={v} requestId={r.request_id} hash={r.content_hash} origin={origin} />}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
