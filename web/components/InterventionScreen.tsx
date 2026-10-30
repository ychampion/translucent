"use client";
// An intervention campaign: the decision moment first (one trial, the committed call, then the reveal), then the
// scorecard on held-out trials with corrected comparisons, the running totals in trial order, the hypotheses,
// procedure and proposed experiments, and how to check it all. Loads the split summary and one trial at a time.
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useCommands } from "@/lib/commands";
import {
  agentPolicies, conditionWord, cumulative, DEFAULT_TRIAL, expWord, fixed, flagWord, headlineAgent, INTERVENTION_NAV, interventionEntry, interventionHref,
  modelLine, OUTCOME_WORD, originWord, pct, pctRange, policyById, policyByName, policyNote, policyWord, pWord, scorecard, shortHash,
  splitVerdict, SPLIT_WORD, SPLITS, trialOrder, verdictLines,
  type InterventionSummary, type InterventionTrialFile, type IvClaim, type IvComparison, type IvPolicy, type IvScore, type IvTrial, type Split,
} from "@/lib/intervention";
import { AllCommitments, DecisionMoment, trialOption, useWidth } from "./DecisionMoment";
import { IconCheck } from "./Icons";
import { PageHeader } from "./Shell";
import { LoadState } from "./Status";

const base = (key: string) => `/fixtures/${key}`;
const trialCache = new Map<string, Promise<InterventionTrialFile>>();
function loadTrial(key: string, trial: string): Promise<InterventionTrialFile> {
  const id = `${key}/${trial}`;
  let p = trialCache.get(id);
  if (!p) {
    p = fetch(`${base(key)}/trials/${encodeURIComponent(trial)}.json`).then((r) => {
      if (!r.ok) throw new Error(`trial ${trial}: HTTP ${r.status}`);
      return r.json() as Promise<InterventionTrialFile>;
    });
    p.catch(() => trialCache.delete(id));
    trialCache.set(id, p);
  }
  return p;
}

export function InterventionScreen({ fixture }: { fixture: string }) {
  const [summary, setSummary] = useState<InterventionSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let gone = false;
    setError(null);
    fetch(`${base(fixture)}/summary.json`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((s: InterventionSummary) => { if (!gone) setSummary(s); })
      .catch((e: unknown) => { if (!gone) setError(e); });
    return () => { gone = true; };
  }, [fixture, attempt]);
  useEffect(() => { document.title = `${interventionEntry(fixture).name} – Translucent`; }, [fixture]);
  if (!summary) {
    return (
      <div className="screen loading">
        <PageHeader title={interventionEntry(fixture).name} />
        <LoadState error={error} what="this benchmark" onRetry={() => setAttempt((n) => n + 1)} />
      </div>
    );
  }
  return <Body fixture={fixture} s={summary} />;
}

function Body({ fixture, s }: { fixture: string; s: InterventionSummary }) {
  const trials = useMemo(() => trialOrder(s), [s]);
  const agents = useMemo(() => agentPolicies(s), [s]);
  const xMax = useMemo(() => Math.ceil(Math.max(...s.trials.map((t) => t.t_end)) / 100) * 100, [s]);
  const [trialKey, setTrialKey] = useState(DEFAULT_TRIAL);
  const splits = useMemo(() => registeredSplits(s), [s]);
  const [split, setSplit] = useState<Split>(splits[0] ?? "loco");
  const [agentId, setAgentId] = useState(headlineAgent(s)?.id ?? "");
  const [revealed, setRevealed] = useState(false);
  const [file, setFile] = useState<InterventionTrialFile | null>(null);
  const [fileError, setFileError] = useState(false);

  // ?trial=Exp81&split=prequential
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const t = q.get("trial");
    if (t && s.trials.some((x) => x.key === t)) setTrialKey(t);
    else if (!s.trials.some((x) => x.key === DEFAULT_TRIAL)) setTrialKey(trials[0]?.key ?? "");
    const sp = q.get("split");
    if (sp && (splits as string[]).includes(sp)) setSplit(sp as Split);
  }, [s, trials, splits]);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    q.set("trial", trialKey);
    if (split === "loco") q.delete("split"); else q.set("split", split);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${q}`);
  }, [trialKey, split]);

  useEffect(() => {
    let gone = false;
    setRevealed(false);
    setFileError(false);
    setFile(null);
    loadTrial(fixture, trialKey).then((f) => { if (!gone) setFile(f); }).catch(() => { if (!gone) setFileError(true); });
    // Prefetch the neighbours so J/K is instant.
    const i = trials.findIndex((t) => t.key === trialKey);
    for (const n of [trials[i + 1], trials[i - 1]]) if (n) loadTrial(fixture, n.key).catch(() => {});
    return () => { gone = true; };
  }, [fixture, trialKey, trials]);
  useEffect(() => { setRevealed(false); }, [split, agentId]);

  const trial = trials.find((t) => t.key === trialKey) ?? trials[0];
  const agent = policyById(s, agentId) ?? agents[0];
  const move = useCallback((by: number) => {
    const i = trials.findIndex((t) => t.key === trialKey);
    const n = trials[(i + by + trials.length) % trials.length];
    if (n) setTrialKey(n.key);
  }, [trials, trialKey]);

  useCommands("intervention", () => [
    { id: "iv-next", label: "Next trial", group: "Campaign", keys: ["J", "ArrowRight"], run: () => move(1) },
    { id: "iv-prev", label: "Previous trial", group: "Campaign", keys: ["K", "ArrowLeft"], run: () => move(-1) },
    { id: "iv-reveal", label: revealed ? "Hide the outcome again" : "Reveal what happened", group: "Campaign", keys: ["R"], run: () => setRevealed((r) => !r) },
    ...splits.map((sp, i) => ({ id: `iv-split-${sp}`, label: `Score on: ${SPLIT_WORD[sp].name}`, group: "Campaign" as const, keys: [String(i + 1)], run: () => setSplit(sp) })),
    { id: "iv-exp81", label: "Go to Exp 81", hint: "Cooled 14.6 °C after heater-off, then ignited", group: "Campaign", when: () => s.trials.some((t) => t.key === "Exp81"), run: () => setTrialKey("Exp81") },
  ], [revealed, move, s, splits]);

  const origin = originWord(s.origin);
  const ignitedN = s.trials.filter((t) => t.outcome && t.outcome !== "rescued").length;
  const cooled = s.trials.filter((t) => t.outcome && t.outcome !== "rescued" && (t.drop_after_off ?? 0) >= 2);
  const physics = policyByName(s, "physics_only");
  const physicsScore = physics && s.scores.find((x) => x.split === "loco" && x.policy_id === physics.id);

  return (
    <div className="screen iv-screen">
      <PageHeader title={interventionEntry(fixture).name}>
        <span className={`iv-origin is-${s.origin.kind}`} title={origin.line}>{origin.tag}</span>
      </PageHeader>
      <main className="iv-main">
        <SourceSwitch fixture={fixture} />
        <header className="iv-head">
          <p className="iv-eyebrow">{s.system.title}</p>
          <h1 className="iv-title">{s.campaign.objective || "Stand down, escalate, or watch longer?"}</h1>
          <p className="iv-lead">
            At recorded heater-off the agent commits one of: stand down (stopping the heat is enough), escalate, or pay to
            watch {s.protocol.observe_options.join(" or ")} s more. Each call is sealed in the ledger before the trial’s
            recorded outcome is revealed, then scored with registered costs.
          </p>
          <p className="iv-hard">
            {cooled.length > 0 && <>In {cooled.length} trials the cell cooled at least 2 °C after heater-off and still ignited. </>}
            {physicsScore && physicsScore.unsafe === ignitedN && ignitedN > 0 && <>One-node physics stood down on all {ignitedN} trials that ignited.</>}
          </p>
          <p className="iv-origin-line small"><span className={`iv-origin is-${s.origin.kind}`}>{origin.tag}</span> {origin.line}</p>
        </header>

        <LiveResult s={s} fixture={fixture} />

        <section className="iv-section" aria-labelledby="iv-decide-h">
          <div className="iv-section-head">
            <h2 id="iv-decide-h" className="iv-h2">The decision</h2>
            <p className="soft small">J / K or ← / → move between trials. R reveals.</p>
          </div>
          <div className="iv-bar">
            <div className="iv-trialpick">
              <button type="button" className="icon-btn" onClick={() => move(-1)} aria-label="Previous trial" aria-keyshortcuts="K">‹</button>
              <label className="visually-hidden" htmlFor="iv-trial">Trial</label>
              <select id="iv-trial" className="field iv-select" value={trial.key} onChange={(e) => setTrialKey(e.target.value)}>
                {trials.map((t) => <option key={t.key} value={t.key}>{trialOption(t)}</option>)}
              </select>
              <button type="button" className="icon-btn" onClick={() => move(1)} aria-label="Next trial" aria-keyshortcuts="J">›</button>
            </div>
            {agents.length > 1 && (
              <div className="iv-agentpick">
                <label htmlFor="iv-agent" className="soft small">Agent policy</label>
                <select id="iv-agent" className="field iv-select" value={agent.id} onChange={(e) => setAgentId(e.target.value)}>
                  {agents.map((p) => <option key={p.id} value={p.id}>{policyWord(p)}{p.id === s.procedure?.policy_id ? " (procedure)" : ""}</option>)}
                </select>
              </div>
            )}
            <SplitPicker splits={splits} split={split} setSplit={setSplit} name="decide" />
          </div>
          <p className="iv-trialfacts small">
            <span>{expWord(trial)}</span>
            <span>{conditionWord(trial.condition)}</span>
            {trial.chemistry && <span>{trial.chemistry}</span>}
            <span>Heater cut {trial.delay_after_vent_s} s after vent</span>
            {trial.flags.filter((f) => f === "heater_off_inferred" || f === "suspect_soc").map((f) => <span key={f} className="is-warn">{flagWord(f)}</span>)}
          </p>
          {agent && (
            <DecisionMoment summary={s} trial={trial} file={file} fileError={fileError} split={split} agent={agent}
              revealed={revealed} onReveal={() => setRevealed(true)} xMax={xMax} />
          )}
          {file && (
            <details className="iv-more">
              <summary>What every registered policy committed for {expWord(trial)}</summary>
              <AllCommitments summary={s} trial={trial} file={file} split={split} revealed={revealed} />
            </details>
          )}
        </section>

        <Scorecard s={s} split={split} setSplit={setSplit} />
        <Timeline s={s} agentId={agent?.id ?? ""} />
        <Science s={s} />
        <Trust s={s} fixture={fixture} trials={trials} onPick={(k) => { setTrialKey(k); document.getElementById("iv-decide-h")?.scrollIntoView({ block: "start" }); }} />
      </main>
    </div>
  );
}

/** The splits this campaign registered, in display order. */
const registeredSplits = (s: InterventionSummary) => SPLITS.filter((sp) => s.protocol.splits.includes(sp));

function SplitPicker({ splits, split, setSplit, name }: { splits: Split[]; split: Split; setSplit(s: Split): void; name: string }) {
  if (splits.length < 2) return null;
  return (
    <div className="iv-splits" role="radiogroup" aria-label="Scored on">
      {splits.map((sp, i) => (
        <button key={sp} type="button" role="radio" aria-checked={split === sp} className={`iv-split ${split === sp ? "is-on" : ""}`}
          onClick={() => setSplit(sp)} id={`${name}-${sp}`} title={`${SPLIT_WORD[sp].name} (${i + 1})`}>
          {SPLIT_WORD[sp].short}
          {sp === "lofo" && <span className="iv-split-warn"> · known transfer failure</span>}
        </button>
      ))}
    </div>
  );
}

/** Scripted benchmark or live run: the campaigns on the same trials, one link each. */
function SourceSwitch({ fixture }: { fixture: string }) {
  if (INTERVENTION_NAV.length < 2) return null;
  return (
    <nav className="iv-source" aria-label="Which run">
      {INTERVENTION_NAV.map((x) => (
        <Link key={x.key} href={interventionHref(x.key)} className={`iv-source-tab ${x.key === fixture ? "is-on" : ""}`}
          aria-current={x.key === fixture ? "page" : undefined}>
          <span className={`iv-source-kind is-${x.source}`}>{x.source === "live" ? "Live" : "Scripted"}</span>
          <span>{x.tab}</span>
        </Link>
      ))}
    </nav>
  );
}

const SCORE_SOURCE: Record<IvScore["source"], string> = {
  ledger: "Score record in its ledger",
  gate: "Score the gate recorded in its review",
  computed: "Computed from the committed decisions",
};

function ResultTile({ s, policy, label, split }: { s: InterventionSummary; policy: IvPolicy; label: string; split: Split }) {
  const score = s.scores.find((x) => x.policy_id === policy.id && x.split === split && x.status === "scored");
  if (!score) return null;
  const where = policy.external && policy.external !== "outcomes" ? "Muse-alone run: " : "";
  return (
    <li className={`iv-tile ${policy.kind === "agent" ? "is-agent" : ""}`}>
      <p className="iv-tile-label">{label}</p>
      <p className="iv-tile-name">{policyWord(policy)}</p>
      <p className="iv-tile-main"><span className="num iv-tile-n">{score.unsafe}</span> unsafe stand-downs</p>
      <p className="iv-tile-sub num">{score.missed} missed rescues · mean cost {fixed(score.mean_cost)}{score.watch_time_s > 0 ? ` · ${Math.round(score.watch_time_s)} s watched` : ""}</p>
      <p className="iv-tile-src small">{where}{policy.external === "outcomes" ? "Computed from the recorded outcomes" : SCORE_SOURCE[score.source]}</p>
    </li>
  );
}

const nameIn = (p: IvPolicy) => (p.name === "always_escalate" ? "always escalate" : policyWord(p));

/** The live run's result against its comparators, from the summary. Says when a difference is not significant. */
function LiveResult({ s, fixture }: { s: InterventionSummary; fixture: string }) {
  const entry = interventionEntry(fixture);
  if (!entry.comparator) return null;
  const split: Split = s.protocol.splits.includes("loco") ? "loco" : s.protocol.splits[0];
  const agent = headlineAgent(s);
  const escalate = policyByName(s, "always_escalate");
  const alone = s.policies.find((p) => p.external === entry.comparator);
  if (!agent) return null;
  const scoreOf = (p?: IvPolicy) => p && s.scores.find((x) => x.policy_id === p.id && x.split === split && x.status === "scored");
  const a = scoreOf(agent), e = scoreOf(escalate), m = scoreOf(alone);
  const vs = (b?: IvPolicy) => b && s.comparisons.find((c) => c.split === split && c.a === agent.id && c.b === b.id);
  const cs = [{ b: escalate, c: vs(escalate) }, { b: alone, c: vs(alone) }].filter((x): x is { b: IvPolicy; c: IvComparison } => !!x.b && !!x.c);
  const wins = cs.filter((x) => x.c.p_holm < 0.05 && x.c.a_mean_cost < x.c.b_mean_cost);
  const anySource = (src: IvScore["source"]) => s.scores.some((x) => x.split === split && x.source === src) || s.comparisons.some((x) => x.split === split && x.source === src);
  return (
    <section className="iv-section iv-live" aria-labelledby="iv-live-h">
      <div className="iv-section-head">
        <h2 id="iv-live-h" className="iv-h2">Live run result{a ? `, ${a.n} held-out trials` : ""}</h2>
        <p className="soft small">{SPLIT_WORD[split].name}. Lower mean decision cost per trial is better.</p>
      </div>
      <ul className="iv-tiles">
        <ResultTile s={s} policy={agent} split={split} label="Agent: the planner’s procedure policy" />
        {escalate && <ResultTile s={s} policy={escalate} split={split} label="Registered baseline" />}
        {alone && <ResultTile s={s} policy={alone} split={split} label="Comparator: Muse Wonder with no harness" />}
      </ul>
      {a && (
        <p className="iv-live-line">
          The planner’s procedure policy made {a.unsafe} unsafe stand-downs and {a.missed} missed rescues, mean cost{" "}
          <span className="num">{fixed(a.mean_cost)}</span>{e ? <> against always escalate’s <span className="num">{fixed(e.mean_cost)}</span></> : null}.{" "}
          {m && <>Muse Wonder alone, a separate live run with no harness, made {m.unsafe} unsafe stand-downs and {m.missed} missed rescues, mean cost <span className="num">{fixed(m.mean_cost)}</span>. </>}
          {cs.length > 0 && (
            <>
              {wins.length ? "After Holm correction: " : `At ${a.n} trials no difference is significant after Holm correction: `}
              {cs.map((x, i) => (
                <span key={x.b.id}>{i ? "; " : ""}against {nameIn(x.b)}, paired sign-flip p <span className="num">{pWord(x.c.p)}</span>, adjusted <span className="num">{pWord(x.c.p_holm)}</span>{x.c.source === "gate" ? " (the gate’s own test)" : ""}</span>
              ))}
              {` (family of ${cs[0].c.family_size}).`}
            </>
          )}
        </p>
      )}
      <p className="soft small">
        Every probability is a model forecast and is simulated; outcomes and traces are recorded.
        {anySource("gate") && " Scores and comparisons marked as the gate’s come from its review records in this ledger."}
        {anySource("computed") && " The rest are computed by the page build from the committed decisions and recorded outcomes with the registered costs; offline verification re-checks those decisions, not the computed numbers."}
        {alone && " Muse alone is a separate live run on the same trials, with its own ledger and receipt."}
      </p>
      <GateRefusal s={s} />
    </section>
  );
}

const COVERAGE = /^Complete held-out coverage required/;
const VERDICT_WORD: Record<string, string> = { supported: "Supported", inconclusive: "Refused: inconclusive", unsupported: "Refused", refuted: "Refused: refuted" };
const verdictWord = (v: string | null) => (v ? VERDICT_WORD[v] ?? v : "No verdict");

/**
 * The agent claimed a win; the deterministic gate re-ran the registered test and refused it; the procedure the agent
 * then wrote cites only supported claims. Shown when the ledger holds such a refusal.
 */
function GateRefusal({ s }: { s: InterventionSummary }) {
  const refused = s.claims.filter((c) => c.type === "policy_beats" && c.verdict && c.verdict !== "supported" && !COVERAGE.test(c.reason ?? ""))
    .sort((x, y) => y.seq - x.seq)[0];
  if (!refused) return null;
  const early = s.claims.filter((c) => c.verdict !== "supported" && COVERAGE.test(c.reason ?? "")).length;
  const cited = (s.procedure?.claim_ids ?? []).map((id) => s.claims.find((c) => c.id === id)).filter((c): c is IvClaim => !!c);
  const allSupported = cited.length > 0 && cited.every((c) => c.verdict === "supported");
  return (
    <div className="iv-gate" aria-labelledby="iv-gate-h">
      <h3 id="iv-gate-h" className="iv-h3">The gate refused the agent’s claim</h3>
      <ol className="iv-beat">
        <li>
          <span className="iv-beat-n" aria-hidden>1</span>
          <div>
            <p className="iv-beat-k">The agent claimed <span className="soft num">claim #{refused.seq}</span></p>
            <p>{predicateWord(s, refused.predicate)}</p>
          </div>
        </li>
        <li>
          <span className="iv-beat-n" aria-hidden>2</span>
          <div>
            <p className="iv-beat-k">The deterministic gate re-ran the registered test <span className="soft num">{refused.review_seq != null ? `review #${refused.review_seq}` : ""}</span></p>
            <p><span className={`iv-verdict-tag is-${refused.verdict}`}>{verdictWord(refused.verdict)}</span> {refused.reason}</p>
          </div>
        </li>
        {cited.length > 0 && (
          <li>
            <span className="iv-beat-n" aria-hidden>3</span>
            <div>
              <p className="iv-beat-k">The operating procedure the agent then wrote {allSupported ? "cites only supported claims" : "cites these claims"}</p>
              <ul className="iv-claims">
                {cited.map((c) => (
                  <li key={c.id}><span className={`iv-verdict-tag is-${c.verdict ?? "none"}`}>{verdictWord(c.verdict)}</span><span>{c.reason}</span><span className="soft small num">claim #{c.seq}</span></li>
                ))}
              </ul>
            </div>
          </li>
        )}
      </ol>
      {early > 0 && <p className="soft small">{early} earlier claims were refused because the held-out trials were not all scored yet.</p>}
    </div>
  );
}

// ---------------------------------------------------------------- scorecard

function CostBar({ mean, ci, max }: { mean: number | null; ci: [number, number] | null; max: number }) {
  if (mean == null) return null;
  const x = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  return (
    <span className="iv-ci" aria-hidden>
      {ci && <span className="iv-ci-range" style={{ left: x(ci[0]), width: `calc(${x(ci[1])} - ${x(ci[0])})` }} />}
      <span className="iv-ci-dot" style={{ left: x(mean) }} />
    </span>
  );
}

function compareWord(c: IvComparison): string {
  if (Math.abs(c.a_mean_cost - c.b_mean_cost) < 1e-9) return "Same cost";
  if (c.a_mean_cost > c.b_mean_cost) return "Higher cost";
  return c.p_holm < 0.05 ? "Lower cost, holds after correction" : "Lower cost, not significant after correction";
}

function Scorecard({ s, split, setSplit }: { s: InterventionSummary; split: Split; setSplit(s: Split): void }) {
  const rows = scorecard(s, split);
  const lines = verdictLines(s, split);
  const v = splitVerdict(s, split);
  const max = Math.max(10, ...rows.map((r) => r.score.mean_cost_interval?.[1] ?? r.score.mean_cost ?? 0));
  const agents = agentPolicies(s);
  const f = s.protocol.funnel;
  const c = s.protocol.costs;
  const cmp0 = s.comparisons.find((x) => x.split === split);
  const computedCmp = s.comparisons.some((x) => x.split === split && x.source === "computed");
  const gateCmp = s.comparisons.filter((x) => x.split === split && x.source === "gate").length;
  return (
    <section className="iv-section" aria-labelledby="iv-score-h">
      <div className="iv-section-head">
        <h2 id="iv-score-h" className="iv-h2">Scorecard on held-out trials</h2>
        <SplitPicker splits={registeredSplits(s)} split={split} setSplit={setSplit} name="score" />
      </div>
      <p className="soft small">{SPLIT_WORD[split].note}</p>
      <div className={`iv-verdict ${v.agentVsEscalate.some((x) => x.beats) ? "" : "is-null"}`}>
        {lines.map((l) => <p key={l}>{l}</p>)}
        <p className="soft small">
          Registered costs: unsafe stand-down or ignition while watching {c.unsafe}, unnecessary escalation {c.unnecessary_escalation},
          watching {c.watch_per_second} per second, abstaining {c.abstain}. Escalating on a trial that ignited costs 0.
        </p>
      </div>
      <div className="iv-tablewrap">
        <table className="iv-table iv-score">
          <caption className="visually-hidden">Policies scored on {SPLIT_WORD[split].name}, lowest mean cost first</caption>
          <thead>
            <tr>
              <th scope="col">Policy</th>
              <th scope="col" className="is-num">Unsafe stand-downs</th>
              <th scope="col" className="is-num">Missed rescues</th>
              <th scope="col" className="is-num">Watch time</th>
              <th scope="col">Mean cost, 95% interval</th>
              <th scope="col">Accuracy, 95% interval</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ policy, score }) => (
              <tr key={policy.id} className={`${policy.kind === "agent" ? "is-agent" : ""} ${score.status !== "scored" ? "is-off" : ""}`}>
                <th scope="row" data-label="Policy">
                  <span className="iv-pname">{policyWord(policy)}</span>
                  {policy.kind === "agent" && <span className="iv-tag is-agent">Agent</span>}
                  {policy.id === s.procedure?.policy_id && <span className="iv-tag">Procedure</span>}
                  {policyNote(policy) && <span className="iv-pnote">{policyNote(policy)}</span>}
                </th>
                {score.status === "scored" ? (
                  <>
                    <td data-label="Unsafe stand-downs" className="num is-num">{score.unsafe}<span className="soft"> / {score.n}</span></td>
                    <td data-label="Missed rescues" className="num is-num">{score.missed}</td>
                    <td data-label="Watch time" className="num is-num">{Math.round(score.watch_time_s)} s</td>
                    <td data-label="Mean cost" className="iv-costcell">
                      <span className="num">{fixed(score.mean_cost)}</span> <span className="soft num">{score.mean_cost_interval ? `${fixed(score.mean_cost_interval[0], 1)}–${fixed(score.mean_cost_interval[1], 1)}` : ""}</span>
                      <CostBar mean={score.mean_cost} ci={score.mean_cost_interval} max={max} />
                    </td>
                    <td data-label="Accuracy" className="num">{score.accuracy == null ? "–" : pct(score.accuracy)} <span className="soft">{pctRange(score.accuracy_interval)}</span></td>
                  </>
                ) : (
                  <td colSpan={5} data-label="Status" className="soft">Not run: {score.reason ?? "no recorded predictions"}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="soft small">
        Unsafe stand-down: it judged stopping the heat enough and the cell ignited. Missed rescue: it escalated and the cell
        did not ignite. Intervals: Wilson 95% for counts, condition-block bootstrap for cost. Zero unsafe in 50 trials still allows a rate up to about 7%.
        {s.post_hoc.scores + s.post_hoc.comparisons > 0 && <> This bundle also holds {s.post_hoc.scores} post-hoc sensitivity scores and {s.post_hoc.comparisons} comparisons, added after the primary results were known and outside the registered correction; they are not shown here.</>}
        {rows.some((r) => r.score.source === "computed") && <> Rows without a cost interval are computed by the page build from the committed decisions and recorded outcomes; the export has no score record for them.</>}
      </p>

      <h3 className="iv-h3">Agent against each comparator</h3>
      <p className="soft small">
        Mean decision cost, paired sign-flip test on per-trial cost differences, Holm-corrected over
        {v.familySize ? ` ${v.familySize} comparisons` : " the registered family"}{computedCmp
          ? <> ({cmp0!.candidates_submitted} submitted agent strategies × {cmp0!.baselines_submitted} comparators).</>
          : <> ({f.submitted} submitted agent strategies × {f.baselines_registered ?? "the"} registered comparators).</>}
        {gateCmp > 0 && <> {gateCmp === 1 ? "One comparison is" : `${gateCmp} comparisons are`} the gate’s own record, from its review of a claim.</>}
        {computedCmp && <> The others are computed by the page build with the exact test, because the ledger records a comparison only when a claim asks for one.</>}
      </p>
      <div className="iv-compare-grid">
        {agents.map((a) => {
          const cs = s.comparisons.filter((x) => x.split === split && x.a === a.id).sort((x, y) => x.p_holm - y.p_holm);
          if (!cs.length) return null;
          return (
            <div key={a.id} className="iv-tablewrap">
              <table className="iv-table iv-cmp">
                <caption className="iv-cmp-cap">{policyWord(a)}</caption>
                <thead><tr><th scope="col">Against</th><th scope="col" className="is-num">Cost, agent vs it</th><th scope="col" className="is-num">p</th><th scope="col" className="is-num">Holm p</th><th scope="col">Result</th></tr></thead>
                <tbody>
                  {cs.map((x) => {
                    const b = policyById(s, x.b);
                    return (
                      <tr key={x.id} className={x.a_mean_cost < x.b_mean_cost && x.p_holm < 0.05 ? "is-win" : ""}>
                        <th scope="row" data-label="Against">{policyWord(b)}</th>
                        <td data-label="Cost" className="num is-num">{fixed(x.a_mean_cost)} vs {fixed(x.b_mean_cost)}</td>
                        <td data-label="p" className="num is-num">{pWord(x.p)}</td>
                        <td data-label="Holm p" className="num is-num">{pWord(x.p_holm)}</td>
                        <td data-label="Result">{compareWord(x)}{x.source === "gate" && <span className="iv-pnote">the gate’s record</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>

      <h3 className="iv-h3">How many strategies were tried</h3>
      <ol className="iv-funnel" aria-label="Agent strategy funnel">
        <li><span className="iv-funnel-n num">{f.proposed}</span> agent strategies proposed</li>
        <li><span className="iv-funnel-n num">{f.screened_out}</span> screened out ({f.screen})</li>
        <li><span className="iv-funnel-n num">{f.submitted}</span> submitted to held-out scoring, of a cap K = {s.protocol.heldout_cap_K}</li>
      </ol>
      <p className="soft small">
        {!!f.baselines_registered && <>{f.baselines_registered} frozen baselines are registered comparators and do not use K. </>}
        Inside training folds, {f.candidate_models_proposed.toLocaleString("en-US")} candidate models were fitted and {f.candidate_models_screened_out.toLocaleString("en-US")} screened out.
        {f.pending > 0 && ` ${f.pending} still pending.`}
      </p>
    </section>
  );
}

// ---------------------------------------------------------------- prequential running totals

const TL_PAD = { l: 40, r: 12, t: 12, b: 26 };

function lineClass(s: InterventionSummary, p: IvPolicy | undefined, agentId: string) {
  if (!p) return "is-base";
  if (p.kind === "agent") return p.id === agentId ? "is-agent" : "is-agent-2";
  if (p.name === "always_escalate") return "is-escalate";
  return "is-base";
}

/** Minimum pixels between update markers: closer updates share one marker. */
const MARK_GAP = 14;

/**
 * World-model updates as markers on the agent's line, merged so no two are closer than MARK_GAP pixels. A merged
 * marker is filled when any of its updates was kept; its tooltip gives the counts.
 */
function updateMarks(updates: InterventionSummary["updates"], idx: Map<string, number>, X: (i: number) => number) {
  const placed = updates.map((u) => ({ u, i: idx.get(u.after_trial_key) })).filter((x): x is { u: typeof x.u; i: number } => x.i != null)
    .sort((a, b) => a.i - b.i || a.u.seq - b.u.seq);
  const marks: { key: string; i: number; first: number; kept: number; rejected: number }[] = [];
  for (const { u, i } of placed) {
    const last = marks.at(-1);
    if (last && X(i) - X(last.first) < MARK_GAP) {
      last.i = i;
      if (u.accepted) last.kept++; else last.rejected++;
    } else {
      marks.push({ key: u.id, i, first: i, kept: u.accepted ? 1 : 0, rejected: u.accepted ? 0 : 1 });
    }
  }
  const trialWord = (i: number) => `trial ${i + 1}`;
  return marks.map((m) => ({
    key: m.key, i: m.i, kept: m.kept > 0,
    title: `After ${m.first === m.i ? trialWord(m.i) : `${trialWord(m.first)} to ${m.i + 1}`}: ${m.kept} update${m.kept === 1 ? "" : "s"} kept, ${m.rejected} rejected`,
  }));
}

function TimelineChart({ s, metric, agentId }: { s: InterventionSummary; metric: "unsafe" | "cost"; agentId: string }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const h = w < 520 ? 190 : 220;
  const n = s.prequential.order.length;
  const lines = s.prequential.rows.map((r) => ({ id: r.policy_id, ys: cumulative(metric === "unsafe" ? r.unsafe : r.cost) }));
  const yMaxRaw = Math.max(1, ...lines.flatMap((l) => l.ys));
  const step = yMaxRaw <= 10 ? 2 : yMaxRaw <= 40 ? 10 : yMaxRaw <= 400 ? 100 : yMaxRaw <= 2000 ? 500 : 1000;
  const yMax = Math.ceil(yMaxRaw / step) * step;
  const iw = Math.max(0, w - TL_PAD.l - TL_PAD.r), ih = h - TL_PAD.t - TL_PAD.b;
  const X = (i: number) => TL_PAD.l + (n > 1 ? (i / (n - 1)) * iw : 0);
  const Y = (v: number) => TL_PAD.t + ih - (v / yMax) * ih;
  const d = (ys: number[]) => ys.map((y, i) => `${i ? "L" : "M"}${X(i).toFixed(1)} ${Y(y).toFixed(1)}`).join("");
  const ordered = [...lines].sort((a, b) => {
    const rank = (id: string) => ({ "is-base": 0, "is-escalate": 1, "is-agent-2": 2, "is-agent": 3 })[lineClass(s, policyById(s, id), agentId)];
    return rank(a.id) - rank(b.id);
  });
  const agentLine = lines.find((l) => l.id === agentId);
  const updates = metric === "cost" ? s.updates.filter((u) => u.policy_id === agentId) : [];
  const idx = new Map(s.prequential.order.map((k, i) => [k, i]));
  const ticks: number[] = [];
  for (let v = 0; v <= yMax; v += step) ticks.push(v);
  const final = (id: string) => lines.find((l) => l.id === id)?.ys.at(-1) ?? 0;
  const label = metric === "unsafe" ? "Cumulative unsafe stand-downs" : "Cumulative decision cost";
  const desc = `${label} over ${n} trials in Exp-number order. ${s.prequential.rows.map((r) => `${policyWord(policyById(s, r.policy_id))}: ${fixed(final(r.policy_id), metric === "unsafe" ? 0 : 1)}`).join("; ")}.`;
  return (
    <figure className="iv-tl">
      <figcaption className="iv-tl-cap">{label}</figcaption>
      <div ref={ref} className="iv-tl-plot">
        {w > 0 && (
          <svg width={w} height={h} role="img" aria-label={desc} className="iv-svg">
            {ticks.map((v) => (
              <g key={v}>
                <line x1={TL_PAD.l} x2={TL_PAD.l + iw} y1={Y(v)} y2={Y(v)} className="iv-grid" />
                <text x={TL_PAD.l - 6} y={Y(v) + 4} textAnchor="end" className="iv-tick">{v}</text>
              </g>
            ))}
            {[0, Math.floor((n - 1) / 2), n - 1].map((i) => (
              <text key={i} x={X(i)} y={h - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"} className="iv-tick">
                {i === 0 ? `trial 1 (Exp ${s.prequential.order[0]?.replace(/\D/g, "")})` : `trial ${i + 1}`}
              </text>
            ))}
            {ordered.map((l) => <path key={l.id} d={d(l.ys)} className={`iv-tl-line ${lineClass(s, policyById(s, l.id), agentId)}`} />)}
            {agentLine && updateMarks(updates, idx, X).map((m) => (
              <circle key={m.key} cx={X(m.i)} cy={Y(agentLine.ys[m.i])} r={2.6} className={`iv-upd ${m.kept ? "is-acc" : "is-rej"}`}>
                <title>{m.title}</title>
              </circle>
            ))}
          </svg>
        )}
      </div>
    </figure>
  );
}

function Timeline({ s, agentId }: { s: InterventionSummary; agentId: string }) {
  const agent = policyById(s, agentId);
  const ups = s.updates.filter((u) => u.policy_id === agentId);
  const acc = ups.filter((u) => u.accepted).length;
  const legend = [...s.prequential.rows].sort((a, b) => {
    const pa = policyById(s, a.policy_id), pb = policyById(s, b.policy_id);
    return (pa?.kind === "agent" ? 0 : 1) - (pb?.kind === "agent" ? 0 : 1) || (pa?.proposed_seq ?? 0) - (pb?.proposed_seq ?? 0);
  });
  if (!s.prequential.rows.length) return null;
  return (
    <section className="iv-section" aria-labelledby="iv-tl-h">
      <div className="iv-section-head"><h2 id="iv-tl-h" className="iv-h2">In trial order</h2></div>
      <p className="soft small">{SPLIT_WORD.prequential.note} Running totals per policy; the agent can update its world model only after each reveal.</p>
      <div className="iv-tl-grid">
        <TimelineChart s={s} metric="unsafe" agentId={agentId} />
        <TimelineChart s={s} metric="cost" agentId={agentId} />
      </div>
      <ul className="iv-legend" aria-label="Lines and their totals after the last trial">
        {legend.map((r) => {
          const p = policyById(s, r.policy_id);
          return (
            <li key={r.policy_id}>
              <span className={`iv-swatch ${lineClass(s, p, agentId)}`} aria-hidden />
              <span>{policyWord(p)}</span>
              <span className="soft num">{cumulative(r.unsafe).at(-1)} unsafe · cost {fixed(cumulative(r.cost).at(-1), 1)}</span>
            </li>
          );
        })}
        <li><span className="iv-upd-key is-acc" aria-hidden /> update kept <span className="iv-upd-key is-rej" aria-hidden /> update rejected <span className="soft">(updates close together share one marker)</span></li>
      </ul>
      {agent && ups.length > 0 && (
        <details className="iv-more">
          <summary>{policyWord(agent)}: {acc} world-model updates kept, {ups.length - acc} rejected</summary>
          <p className="soft small">An update is kept only if it adds no unsafe errors and worsens neither calibration error nor Brier score by more than 0.01, re-run on every earlier revealed trial. The decision rule stays fixed.</p>
          <ol className="iv-updates">
            {ups.map((u) => (
              <li key={u.id} className={u.accepted ? "is-acc" : "is-rej"}>
                <span className="num soft">#{u.seq}</span>
                <span>After {u.after_trial_key.replace(/^Exp/, "Exp ")}</span>
                <span className="iv-upd-word">{u.accepted ? "Kept" : "Rejected"}</span>
                <span className="soft">{u.reason.replace(/^(accepted|rejected):\s*/, "")}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- hypotheses, procedure, next experiments

function Science({ s }: { s: InterventionSummary }) {
  const hyp = (id: string) => s.hypotheses.find((h) => h.id === id);
  // The same hypothesis text recorded more than once is shown once: the latest record, with every sequence number.
  const hypotheses = [...s.hypotheses.reduce((m, h) => {
    const prev = m.get(h.text);
    return m.set(h.text, { h: !prev || h.seq > prev.h.seq ? h : prev.h, seqs: [...(prev?.seqs ?? []), h.seq].sort((a, b) => a - b) });
  }, new Map<string, { h: InterventionSummary["hypotheses"][number]; seqs: number[] }>()).values()];
  const proc = s.procedure;
  return (
    <section className="iv-section" aria-labelledby="iv-sci-h">
      <div className="iv-section-head"><h2 id="iv-sci-h" className="iv-h2">Hypotheses, procedure and next experiments</h2></div>

      <h3 className="iv-h3">Competing hypotheses</h3>
      <ul className="iv-cards">
        {hypotheses.map(({ h, seqs }) => (
          <li key={h.id} className="iv-cardlet">
            <p className="iv-cardlet-title">{h.text}</p>
            <p className="soft small">World model: {modelLine({ ...h.world_model, grouping: "pooled" })}. {seqs.length > 1 ? `Recorded ${seqs.length} times: ledger ${seqs.map((q) => `#${q}`).join(", ")}.` : `Ledger #${h.seq}.`}</p>
            <details className="iv-mini">
              <summary>Tested by {h.policies.length} registered policies</summary>
              <p className="small">{h.policies.map((id) => policyWord(policyById(s, id))).join(", ")}</p>
            </details>
          </li>
        ))}
      </ul>

      {proc && (
        <>
          <h3 className="iv-h3">Operating procedure</h3>
          <div className="iv-proc">
            <p>{proc.text}</p>
            <p className="iv-proc-h small">Rests on these gate verdicts</p>
            <ul className="iv-claims">
              {proc.claim_ids.map((id) => {
                const c = s.claims.find((x) => x.id === id);
                return (
                  <li key={id}>
                    <a href={`#claim-${id}`} className={`iv-verdict-tag is-${c?.verdict ?? "none"}`} onClick={() => openClaims()}>{c?.verdict ?? "no verdict"}</a>
                    <span>{c ? reasonWord(c.reason) : "No review recorded."}</span>
                    <span className="soft small num">{SPLIT_WORD[(c?.predicate.split as Split) ?? "loco"]?.short ?? ""} · claim #{c?.seq}</span>
                  </li>
                );
              })}
            </ul>
            <p className="iv-proc-h small">Limits</p>
            <ul className="iv-limits small">{proc.limits.map((l) => <li key={l}>{l}</li>)}</ul>
          </div>
        </>
      )}

      {s.next_experiments.length > 0 && (
        <>
          <h3 className="iv-h3">Proposed next experiments</h3>
          <p className="soft small">Ranked by how much the hypotheses disagree about the outcome. None has been run; every probability here is simulated.</p>
          <ul className="iv-cards">
            {s.next_experiments.map((x) => (
              <li key={x.id} className="iv-cardlet">
                <p className="iv-tags"><span className="iv-tag">Proposed</span><span className="iv-tag">Simulated</span><span className="iv-tag">Not run</span></p>
                <p className="iv-cardlet-title">{conditionWord(x.condition)}, heater off {x.heater_off_after_vent_s} s after vent</p>
                <ul className="iv-bars" aria-label="Predicted chance of ignition by hypothesis">
                  {x.per_hypothesis.map((ph) => (
                    <li key={ph.hypothesis_id}>
                      <span className="iv-bar-label small">{hyp(ph.hypothesis_id)?.text ?? ph.hypothesis_id}</span>
                      <span className="iv-bar-track" aria-hidden><span className="iv-bar-fill" style={{ width: `${ph.p_ignite * 100}%` }} /></span>
                      <span className="num small">{pct(ph.p_ignite)}</span>
                    </li>
                  ))}
                </ul>
                <p className="small">Combined: <span className="num">{pct(x.p_ignite)}</span> <span className="soft num">({pctRange(x.interval)})</span> · disagreement {x.disagreement_bits.toFixed(2)} bits</p>
                <p className="small">{x.schedule}</p>
                {x.limits.length > 0 && <ul className="iv-limits small soft">{x.limits.map((l) => <li key={l}>{l}</li>)}</ul>}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function openClaims() {
  const d = document.getElementById("iv-claims") as HTMLDetailsElement | null;
  if (d) d.open = true;
}

// ---------------------------------------------------------------- trust

/** A gate reason as recorded; a bare quoted field name is the gate rejecting a claim that lacks that field. */
function reasonWord(r: string | null): string {
  if (!r) return "No review recorded.";
  const m = r.match(/^'(\w+)'$/);
  return m ? `The claim was missing its “${m[1]}” field, so the gate could not check it.` : r;
}

function predicateWord(s: InterventionSummary, p: Record<string, unknown>): string {
  const name = (id: unknown) => policyWord(policyById(s, String(id)));
  const split = SPLIT_WORD[p.split as Split]?.short ?? String(p.split ?? "");
  switch (p.type) {
    case "policy_performance": return `${name(p.policy_id)}: ${p.unsafe} unsafe in ${p.n} trials (${split})`;
    case "policy_beats": return `${name(p.a)} has lower mean cost than ${policyById(s, String(p.b)) ? nameIn(policyById(s, String(p.b))!) : name(p.b)} (${split})`;
    case "policy_no_more_unsafe_fewer_missed": return `${name(p.a)}: no more unsafe and fewer missed rescues than ${name(p.b)} (${split})`;
    default: return String(p.type ?? "claim").replace(/_/g, " ");
  }
}

function Trust({ s, fixture, trials, onPick }: { s: InterventionSummary; fixture: string; trials: IvTrial[]; onPick(key: string): void }) {
  const entry = interventionEntry(fixture);
  const command = entry.verified?.command ?? `uv run translucent verify ${s.bundle.path}`;
  const supported = s.claims.filter((c) => c.verdict === "supported").length;
  return (
    <section className="iv-section" aria-labelledby="iv-trust-h">
      <div className="iv-section-head"><h2 id="iv-trust-h" className="iv-h2">Check it yourself</h2></div>
      <p className="iv-origin-line small"><span className={`iv-origin is-${s.origin.kind}`}>{originWord(s.origin).tag}</span> {originWord(s.origin).line}</p>
      <div className="iv-verify">
        <p className="small">Re-check this bundle offline. It verifies the receipt hashes, the record and event hash chain, the outcome seals, that every commitment precedes its reveal, fold separation, fit and update provenance, and the arithmetic, then regenerates every bundle field and requires equality.</p>
        <pre className="code-block iv-cmd"><code>{command}</code></pre>
        {entry.verified ? (
          <>
            <p className={`small ${entry.verified.exit === 0 ? "soft" : "is-warn"}`}>
              {entry.verified.exit === 0
                ? `A real run on ${entry.verified.ranOn} passed (exit 0):`
                : `A real run on ${entry.verified.ranOn} did not pass (exit ${entry.verified.exit}). The findings, files and decisions match, but some re-executed model outputs differ from the recorded ones:`}
            </p>
            <pre className="code-block iv-cmd"><code>{entry.verified.output.join("\n")}</code></pre>
          </>
        ) : null}
        {entry.comparator && interventionEntry(entry.comparator).verified && (() => {
          const v = interventionEntry(entry.comparator!).verified!;
          return (
            <>
              <p className="small soft">The Muse-alone comparator is its own bundle. A real run on {v.ranOn} {v.exit === 0 ? "passed (exit 0)" : `did not pass (exit ${v.exit})`}:</p>
              <pre className="code-block iv-cmd"><code>{`${v.command}\n${v.output.join("\n")}`}</code></pre>
            </>
          );
        })()}
        <p className="soft small">It proves internal ordering and consistency, not an external timestamp, and not secrecy from an operator who could read the public source data.</p>
      </div>
      <dl className="iv-hashes small">
        <div><dt>Bundle</dt><dd><code>{s.bundle.path}</code> · {(s.bundle.bytes / 1e6).toFixed(1)} MB</dd></div>
        <div><dt>Bundle SHA-256</dt><dd><code className="iv-hash">{s.bundle.sha256}</code></dd></div>
        {s.receipt && <div><dt>Ledger SHA-256</dt><dd><code className="iv-hash">{s.receipt.ledger_sha256}</code></dd></div>}
        <div><dt>Campaign</dt><dd><code>{s.campaign.id}</code></dd></div>
        <div><dt>Source data</dt><dd>{s.system.citation} · <a className="text-link" href={`https://doi.org/${s.system.doi}`} target="_blank" rel="noreferrer">doi:{s.system.doi}</a> · {s.system.licence}</dd></div>
      </dl>

      <details className="iv-more">
        <summary>All {trials.length} trials: the recorded outcome and how its label was corroborated</summary>
        <div className="iv-tablewrap">
          <table className="iv-table iv-trials">
            <caption className="visually-hidden">Recorded outcomes and label corroboration</caption>
            <thead><tr><th scope="col">Trial</th><th scope="col">Recorded outcome</th><th scope="col">Corroboration</th><th scope="col">Flags</th></tr></thead>
            <tbody>
              {trials.map((t) => (
                <tr key={t.key}>
                  <th scope="row" data-label="Trial"><button type="button" className="text-link" onClick={() => onPick(t.key)}>{expWord(t)}</button> <span className="soft small">{conditionWord(t.condition)}</span></th>
                  <td data-label="Outcome">{t.outcome ? OUTCOME_WORD[t.outcome] : "–"}{t.t_ignition != null ? <span className="soft num"> +{t.t_ignition - t.t_heater_off} s</span> : null}</td>
                  <td data-label="Corroboration">{t.corroboration ? <span className={`iv-agree ${t.corroboration.agree ? "is-yes" : "is-no"}`}>{t.corroboration.agree ? <><IconCheck size={13} /> image, vector, trace agree</> : "disagree"}</span> : <span className="soft">none</span>}</td>
                  <td data-label="Flags" className="soft small">{t.flags.map(flagWord).join(", ") || "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="soft small">Image read: Muse Spark reading the paper’s table images. Vector: the PDF’s vector layer. Trace: first rise over 20 °C in 2 s, or a drop over 10 °C above 120 °C (thermocouple loss). Agreement does not establish a mechanism.</p>
      </details>

      <details className="iv-more" id="iv-claims">
        <summary>Gate verdicts: {supported} of {s.claims.length} claims supported</summary>
        <ul className="iv-claims is-all">
          {s.claims.map((c) => (
            <li key={c.id} id={`claim-${c.id}`}>
              <span className={`iv-verdict-tag is-${c.verdict ?? "none"}`}>{c.verdict ?? "no verdict"}</span>
              <span>{predicateWord(s, c.predicate)}</span>
              <span className="soft small">{reasonWord(c.reason)}</span>
              <span className="soft small num">claim #{c.seq}{c.review_seq != null ? `, reviewed #${c.review_seq}` : ""} · <code>{shortHash(c.id)}</code></span>
            </li>
          ))}
        </ul>
      </details>

      {s.references.map((r) => (
        <p key={r.name} className="soft small">
          Reference, not a comparator: with the heater never cut, {r.ignited} of {r.n} cells ignited ({pctRange(r.ignition_interval)}). {r.limit}.
        </p>
      ))}
      <p className="soft small">Forecasts, world-model probabilities and proposed experiments are simulated. Outcomes, traces and event times are recorded.</p>
    </section>
  );
}
