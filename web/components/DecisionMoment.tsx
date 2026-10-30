"use client";
// The decision moment: one trial's thermocouple trace up to heater-off, the agent's committed call stamped with its
// ledger sequence and sealed outcome, and only then "Reveal what happened". Every forecast is a model output and is
// marked simulated; the outcome and the trace after heater-off are recorded.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  actionLine, afterOff, callVerdict, conditionWord, expWord, fixed, flagWord, modelLine, OUTCOME_WORD,
  pct, pctRange, policyById, policyNote, policyWord, shortHash, SPLIT_WORD,
  type InterventionSummary, type InterventionTrialFile, type IvDecision, type IvPolicy, type IvTrial, type Split,
} from "@/lib/intervention";

/** Width of an element, kept current. Charts draw in pixels so their text stays legible at any width. */
export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth);
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

const UNIT: Record<string, string> = { level: "°C", soc: "%", delay_after_vent_s: "s", time_since_vent: "s", time_heated: "s", rise_since_vent: "°C" };
const featureUnit = (f: string) => UNIT[f] ?? (/^slope_/.test(f) || f === "post_slope" ? "°C/s" : /^rise_/.test(f) ? "°C" : "");

function ruleWord(p: IvPolicy, s: InterventionSummary): string {
  const r = p.decision_rule;
  const c = s.protocol.costs;
  if (r.mode === "cost") return `Lowest registered cost: unsafe stand-down ${c.unsafe}, unnecessary escalation ${c.unnecessary_escalation}, watching ${c.watch_per_second} per second`;
  if (r.mode === "always_observe") return `Always watch ${r.observe_s} s, then escalate when P(ignition) ≥ ${r.threshold ?? 0.5}`;
  return `Escalate when P(ignition) ≥ ${r.threshold ?? 0.5}`;
}

function fitWord(d: IvDecision): string | null {
  const f = d.fit;
  if (!f) return null;
  const thr = f.threshold != null && f.features.length === 1 ? `; threshold ${+f.threshold.toPrecision(3)} ${featureUnit(f.features[0])}`.trimEnd() : "";
  const trained = f.n_train ? `; fitted on ${f.n_train} revealed training trials` : "";
  const chose = f.candidates > 1 ? `, chosen from ${f.candidates} candidates by inner cross-validation` : "";
  return `${modelLine(f)}${thr}${trained}${chose}`;
}

// ---------------------------------------------------------------- the trace

interface TraceProps {
  file: InterventionTrialFile;
  trial: IvTrial;
  revealed: boolean;
  /** Seconds after heater-off the shown decision paid to watch (0: none). */
  watchS: number;
  xMax: number;
  full: boolean;
}

const PAD = { l: 42, r: 14, t: 24, b: 30 };

function niceStep(span: number, target: number) {
  const raw = span / target;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * p).find((x) => x >= raw) ?? raw;
}

function TraceChart({ file, trial, revealed, watchS, xMax, full }: TraceProps) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const narrow = w > 0 && w < 520;
  const h = narrow ? 230 : 280;
  const off = trial.t_heater_off;
  const shownUntil = revealed ? Infinity : off + watchS;
  const geo = useMemo(() => {
    const pre = file.y.filter((_, i) => file.t[i] <= off + watchS);
    const beforeIgnition = file.y.filter((_, i) => trial.t_ignition == null || file.t[i] < trial.t_ignition);
    const lo = Math.min(...(revealed ? (full ? file.y : [...pre, ...beforeIgnition]) : pre));
    const top = revealed ? (full ? Math.max(...file.y) : Math.max(...beforeIgnition, ...pre) + 30) : Math.max(...pre) + 30;
    const step = niceStep(top - lo, narrow ? 4 : 5);
    const y0 = Math.floor(lo / step) * step, y1 = Math.ceil(top / step) * step;
    return { y0, y1, step, peak: Math.max(...file.y) };
  }, [file, off, watchS, revealed, full, trial.t_ignition, narrow]);

  const clipped = revealed && !full && geo.peak > geo.y1;
  // The off-scale peak gets its own line under the time axis, clear of the trace and the event labels.
  const svgH = clipped ? h + 18 : h;
  const iw = Math.max(0, w - PAD.l - PAD.r), ih = h - PAD.t - PAD.b;
  const X = (t: number) => PAD.l + (t / xMax) * iw;
  const Y = (y: number) => PAD.t + ih - ((y - geo.y0) / (geo.y1 - geo.y0 || 1)) * ih;
  const path = (from: number, to: number) => {
    let d = "";
    for (let i = 0; i < file.t.length; i++) {
      const t = file.t[i];
      if (t < from || t > to) continue;
      d += `${d ? "L" : "M"}${X(t).toFixed(1)} ${Y(file.y[i]).toFixed(1)}`;
    }
    return d;
  };
  const xStep = narrow ? 300 : 200;
  const xTicks: number[] = [];
  for (let t = 0; t <= xMax; t += xStep) xTicks.push(t);
  const yTicks: number[] = [];
  for (let y = geo.y0; y <= geo.y1 + 1e-9; y += geo.step) yTicks.push(y);
  const ign = revealed && trial.t_ignition != null ? trial.t_ignition : null;
  const offEnd = Math.min(off + watchS, trial.t_end);
  const label = (x: number, text: string, cls: string, row = 0) => {
    const right = x > PAD.l + iw * 0.72;
    return (
      <text x={right ? x - 5 : x + 5} y={PAD.t + 11 + row * 14} textAnchor={right ? "end" : "start"} className={`iv-trace-label ${cls}`}>{text}</text>
    );
  };
  const summary = revealed
    ? `Surface temperature of ${expWord(trial)} over the whole recording. ${trial.outcome ? OUTCOME_WORD[trial.outcome] : ""}${ign != null ? ` at ${ign} s, ${ign - off} s after heater-off` : ""}.`
    : `Surface temperature of ${expWord(trial)} from heater-on to heater-off at ${off} s${watchS ? `, plus the ${watchS} s watch the policy paid for` : ""}. The rest is sealed until reveal.`;

  return (
    <div className="iv-trace" ref={ref}>
      {w > 0 && (
        <svg width={w} height={svgH} role="img" aria-label={summary} className="iv-svg">
          <defs>
            <clipPath id={`iv-clip-${trial.key}`}><rect x={PAD.l} y={PAD.t - 4} width={iw} height={ih + 4} /></clipPath>
            <pattern id="iv-sealed" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="7" className="iv-hatch" />
            </pattern>
          </defs>
          {yTicks.map((y) => (
            <g key={y}>
              <line x1={PAD.l} x2={PAD.l + iw} y1={Y(y)} y2={Y(y)} className="iv-grid" />
              <text x={PAD.l - 6} y={Y(y) + 4} textAnchor="end" className="iv-tick">{Math.round(y)}</text>
            </g>
          ))}
          <text x={PAD.l - 6} y={PAD.t - 12} textAnchor="end" className="iv-tick">°C</text>
          {xTicks.map((t) => (
            <text key={t} x={X(t)} y={h - 10} textAnchor={t === 0 ? "start" : t + xStep > xMax ? "end" : "middle"} className="iv-tick">{t} s</text>
          ))}
          {!revealed && (
            <g>
              <rect x={X(offEnd)} y={PAD.t} width={Math.max(0, PAD.l + iw - X(offEnd))} height={ih} fill="url(#iv-sealed)" />
              <text x={(X(offEnd) + PAD.l + iw) / 2} y={PAD.t + ih / 2} textAnchor="middle" className="iv-sealed-text">Sealed until reveal</text>
            </g>
          )}
          {watchS > 0 && (
            <rect x={X(off)} y={PAD.t} width={X(offEnd) - X(off)} height={ih} className="iv-watch" />
          )}
          <line x1={X(trial.t_vent)} x2={X(trial.t_vent)} y1={PAD.t} y2={PAD.t + ih} className="iv-mark is-vent" />
          <line x1={X(off)} x2={X(off)} y1={PAD.t} y2={PAD.t + ih} className="iv-mark is-off" />
          <g clipPath={`url(#iv-clip-${trial.key})`}>
            <path d={path(0, Math.min(off + watchS, shownUntil))} className="iv-line" />
            {revealed && <path d={path(off + watchS, Infinity)} className="iv-line is-after" />}
          </g>
          {label(X(trial.t_vent), narrow ? "Vent" : `Vent ${trial.t_vent} s`, "is-vent", 0)}
          {label(X(off), narrow ? "Heater off" : `Heater off ${off} s`, "is-off", 1)}
          {ign != null && (
            <g>
              <line x1={X(ign)} x2={X(ign)} y1={PAD.t} y2={PAD.t + ih} className="iv-mark is-ignition" />
              {label(X(ign), `Ignition +${ign - off} s`, "is-ignition", 2)}
            </g>
          )}
          {revealed && trial.t_ignition == null && (
            label(X(trial.t_end), trial.flags.includes("render_safe_reheat") ? "Trace ends: render-safe reheat" : "Trace ends", "is-end", 2)
          )}
          {clipped && (
            <text x={PAD.l + iw} y={svgH - 4} textAnchor="end" className="iv-tick">Peak {Math.round(geo.peak)} °C is above this scale</text>
          )}
        </svg>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- the card

/** "Fell 14.6 °C after heater-off (170.4 → 155.8 °C), then ignited 297 s after heater-off." */
function outcomeSentence(trial: IvTrial, after: ReturnType<typeof afterOff>): string {
  if (!trial.outcome) return "No outcome recorded.";
  const window = trial.flags.includes("censored") ? " The watch window was finite, so this is censored." : "";
  if (trial.t_ignition == null) return `No ignition in the ${Math.round(trial.t_end - trial.t_heater_off)} s recorded after heater-off.${window}`;
  const when = `${trial.t_ignition - trial.t_heater_off} s after heater-off`;
  if (after && after.drop >= 1) return `Fell ${after.drop.toFixed(1)} °C after heater-off (${after.peak.toFixed(1)} → ${after.min.toFixed(1)} °C), then ignited ${when}.`;
  return `Ignited ${when}.`;
}

const CORRO: [key: "image" | "vector" | "trace", label: string][] = [
  ["image", "Table image read by Muse Spark"],
  ["vector", "PDF vector layer"],
  ["trace", "Trace ignition detector"],
];

export function CorroborationBadges({ trial }: { trial: IvTrial }) {
  const c = trial.corroboration;
  if (!c) return <p className="soft small">No corroboration recorded for this label.</p>;
  return (
    <div className="iv-corro">
      <ul className="iv-corro-list" aria-label="How the recorded label was corroborated">
        {CORRO.map(([k, label]) => (
          <li key={k} className="iv-badge"><span className="soft">{label}</span> {String(c[k]).replace(/_/g, " ")}</li>
        ))}
      </ul>
      <p className={`small ${c.agree ? "" : "is-warn"}`}>{c.agree ? "All three agree." : "They disagree; treat this label with care."}</p>
    </div>
  );
}

interface MomentProps {
  summary: InterventionSummary;
  trial: IvTrial;
  file: InterventionTrialFile | null;
  fileError: boolean;
  split: Split;
  agent: IvPolicy;
  revealed: boolean;
  onReveal(): void;
  xMax: number;
}

export function DecisionMoment({ summary, trial, file, fileError, split, agent, revealed, onReveal, xMax }: MomentProps) {
  const [full, setFull] = useState(false);
  useEffect(() => { setFull(false); }, [trial.key]);
  const decision = file?.decisions.find((d) => d.split === split && d.policy_id === agent.id) ?? null;
  const seal = file?.seals[split];
  const after = file && revealed ? afterOff(file, trial) : null;
  const verdict = decision && revealed ? callVerdict(decision, trial) : null;
  const revealRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (revealed) revealRef.current?.focus({ preventScroll: true }); }, [revealed]);

  return (
    <div className="iv-moment">
      <figure className="iv-figure">
        {file ? (
          <TraceChart file={file} trial={trial} revealed={revealed} watchS={decision?.observe_s ?? 0} xMax={xMax} full={full} />
        ) : (
          <div className="iv-trace iv-trace-empty">{fileError ? "Couldn’t load this trace." : ""}</div>
        )}
        <figcaption className="iv-caption">
          <span>{summary.system.sensor}. Vent time is reconstructed from the published delay; heater-off includes a {trial.heater_off_lag_s} s thermocouple lag.</span>
          {trial.flags.length > 0 && revealed && <span> Flags: {trial.flags.map(flagWord).join(", ")}.</span>}
          {revealed && trial.t_ignition != null && (
            <button type="button" className="text-quiet iv-scale" onClick={() => setFull((f) => !f)} aria-pressed={full}>
              {full ? "Zoom to the cooling" : "Show the full temperature range"}
            </button>
          )}
        </figcaption>
      </figure>

      <article className="iv-card" aria-labelledby="iv-card-h">
        {!decision ? (
          <p className="soft">{file ? `${policyWord(agent)} committed no decision for ${expWord(trial)} in this split.` : ""}</p>
        ) : (
          <>
            <p className="iv-kicker">
              <span>Committed before the outcome was revealed</span>
              <span className="iv-seq num">ledger #{decision.terminal_committed_seq}</span>
            </p>
            <h3 id="iv-card-h" className="iv-action">{actionLine(decision)}</h3>
            <p className="iv-prob">
              <span className="iv-prob-n num">{pct(decision.p_ignite)}</span>
              <span>chance of ignition</span>
              <span className="soft num">interval {pctRange(decision.interval)}</span>
              {decision.simulated && <span className="iv-tag" title="A model output, not a measurement">Simulated</span>}
            </p>
            <dl className="iv-facts">
              <div><dt>Policy</dt><dd>{policyWord(agent)}{policyNote(agent) ? <span className="soft">, {policyNote(agent)}</span> : null}</dd></div>
              {fitWord(decision) && <div><dt>World model</dt><dd>{fitWord(decision)}</dd></div>}
              <div><dt>Decision rule</dt><dd>{ruleWord(agent, summary)}</dd></div>
              <div><dt>Strategy from</dt><dd>{agent.planner ?? "Not recorded"}{summary.origin.kind === "scripted" ? ", no model calls" : ""}</dd></div>
              <div><dt>Split</dt><dd>{SPLIT_WORD[split].name}</dd></div>
              {seal && (
                <div>
                  <dt>Sealed outcome</dt>
                  <dd><code className="iv-hash" title={seal.sealed_hash}>{shortHash(seal.sealed_hash)}…</code>{seal.revealed_seq != null && <span className="soft num">, revealed at #{seal.revealed_seq}</span>}</dd>
                </div>
              )}
            </dl>
            {!revealed ? (
              <button type="button" className="btn iv-reveal" onClick={onReveal} aria-keyshortcuts="R">
                Reveal what happened <kbd className="iv-kbd">R</kbd>
              </button>
            ) : (
              <div className={`iv-outcome is-${verdict?.kind ?? "unknown"}`} ref={revealRef} tabIndex={-1} role="status">
                <p className="iv-outcome-word">{verdict?.word}</p>
                <p className="iv-outcome-line">
                  <span className="iv-tag is-recorded">Recorded</span>{" "}
                  {outcomeSentence(trial, after)}
                </p>
                <p>{verdict?.line} Registered cost of this call: <span className="num">{fixed(decision.cost, 2)}</span>.</p>
                <CorroborationBadges trial={trial} />
                <p className="soft small">Escalation is scored as a risk call. Whether escalating would have prevented an ignition was not observed.</p>
              </div>
            )}
          </>
        )}
      </article>
    </div>
  );
}

/** Every registered policy's commitment for this trial and split; results appear only after reveal. */
export function AllCommitments({ summary, trial, file, split, revealed }: { summary: InterventionSummary; trial: IvTrial; file: InterventionTrialFile; split: Split; revealed: boolean }) {
  const rows = file.decisions
    .filter((d) => d.split === split)
    .map((d) => ({ d, p: policyById(summary, d.policy_id) }))
    .filter((x): x is { d: IvDecision; p: IvPolicy } => !!x.p)
    .sort((a, b) => (a.p.kind === b.p.kind ? a.p.proposed_seq - b.p.proposed_seq : a.p.kind === "agent" ? -1 : 1));
  return (
    <div className="iv-all">
      <table className="iv-table">
        <caption className="visually-hidden">What every policy committed for {expWord(trial)}, {SPLIT_WORD[split].short}</caption>
        <thead>
          <tr>
            <th scope="col">Policy</th>
            <th scope="col">P(ignition) <span className="iv-tag">Simulated</span></th>
            <th scope="col">Committed</th>
            <th scope="col">Against the record</th>
            <th scope="col" className="is-num">Cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ d, p }) => {
            const v = revealed ? callVerdict(d, trial) : null;
            return (
              <tr key={d.id} className={p.kind === "agent" ? "is-agent" : ""}>
                <th scope="row" data-label="Policy">{policyWord(p)}{d.ledger && <span className="iv-pnote">separate live run</span>}</th>
                <td data-label="P(ignition)" className="num">{pct(d.p_ignite)} <span className="soft">{pctRange(d.interval)}</span></td>
                <td data-label="Committed">{actionLine(d)} <span className="soft num" title={d.ledger ? "Sequence number in the Muse-alone run’s own ledger" : undefined}>#{d.terminal_committed_seq}{d.ledger ? " in its ledger" : ""}</span></td>
                <td data-label="Against the record">{v ? <span className={`iv-result is-${v.kind}`}>{v.word}</span> : <span className="soft">Sealed</span>}</td>
                <td data-label="Cost" className="num is-num">{revealed ? fixed(d.cost, 2) : "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export const trialOption = (t: IvTrial) => `${expWord(t)} · ${conditionWord(t.condition)}`;
