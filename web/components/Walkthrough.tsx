"use client";
// The investigation's turning points, inside the recorded workspace: five steps from the engineer's records to the
// design that is still untested. Each step says what was believed, what was seen, what Muse chose and what the numerical
// tools computed, with the request it rests on one click away. Choosing a step moves the workspace below to it.
// Everything shown comes from lib/walkthrough.ts, which reads it from the recording.
import { num, withUnit, type LoopView } from "@/lib/loop";
import type { Walkthrough as Walk } from "@/lib/walkthrough";
import "./Walkthrough.css";

const upperFirst = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function Walkthrough({ v, w, step, onStep, onOpen, onShow }: {
  v: LoopView; w: Walk; step: number; onStep(i: number): void; onOpen(requestId: string): void; onShow(): void;
}) {
  const s = w.steps[step];
  const n = (id: string) => v.requests.findIndex((r) => r.request_id === id) + 1;
  const last = w.steps.length - 1;
  const chooseFirst = s.n >= 4;
  const rows: [string, string][] = chooseFirst
    ? [["Believed", s.believed], [`${w.planner} chose`, s.chose], ["What came back", s.observed]]
    : [["Believed", s.believed], ["Then observed", s.observed], [`${w.planner} chose next`, s.chose]];
  return (
    <section className="wk" aria-labelledby="wk-h">
      <div className="wk-head">
        <h2 className="ws-h2" id="wk-h">How the evidence changed the plan</h2>
        <p className="wk-sub">
          Five points where the evidence changed what Translucent did next. Its planner, {w.planner}, chose each step; the numerical
          tools{w.solver ? ` (${w.solver} fits and checks, and the simulated lab)` : ""} computed every number; {w.approvals}.
        </p>
      </div>
      {/* all five on one row; narrower, they wrap; on a phone, numbers only with the chosen step named, and arrows */}
      <div className="wk-stepbar">
        <ol className="wk-steps" aria-label="Turning points">
          {w.steps.map((x, i) => (
            <li key={x.n} className={i === step ? "is-on" : ""}>
              <button type="button" className={`wk-step ${i === step ? "is-on" : ""} ${i < step ? "is-past" : ""}`} aria-current={i === step ? "step" : undefined} onClick={() => onStep(i)}>
                <span className="wk-step-n num" aria-hidden>{x.n}</span>
                <span className="wk-step-t"><span className="visually-hidden">Step {x.n}: </span>{x.short}</span>
              </button>
            </li>
          ))}
        </ol>
        <div className="wk-mini">
          <button type="button" className="btn btn-small btn-quiet" aria-label="Previous step" disabled={step === 0} onClick={() => onStep(step - 1)}>←</button>
          <button type="button" className="btn btn-small btn-quiet" aria-label="Next step" disabled={step === last} onClick={() => onStep(step + 1)}>→</button>
        </div>
      </div>

      <article className="wk-panel" aria-labelledby="wk-step-h">
        <h3 className="wk-title" id="wk-step-h"><span className="wk-title-n num">{s.n} of {w.steps.length}</span>{s.title}</h3>
        <div className="wk-grid">
          <dl className="wk-story">
            {rows.map(([k, text]) => <div key={k}><dt>{k}</dt><dd>{text}</dd></div>)}
          </dl>
          <div className="wk-side">
            <section className="wk-computed" aria-labelledby="wk-comp-h">
              <p className="wk-k" id="wk-comp-h">Computed by the numerical tools</p>
              <ul>
                {s.computed.map((f) => (
                  <li key={f.label} className={f.tone ? `is-${f.tone}` : ""}><span className="wk-fact-k">{f.label}</span> <span className="wk-fact-v num">{f.value}</span></li>
                ))}
              </ul>
            </section>
            {s.quote && (
              <figure className="wk-quote">
                <blockquote>{s.quote.text}</blockquote>
                <figcaption>
                  {w.planner}, in the rationale of{" "}
                  <button type="button" className="text-link" onClick={() => onOpen(s.quote!.requestId)}>experiment {n(s.quote.requestId)}</button>
                </figcaption>
              </figure>
            )}
          </div>
        </div>

        {s.n === 1 && (
          <p className="wk-note">The project’s documentation reports that in its evaluated intake runs Muse wrote its own import mapping and corrected the mappings Translucent refused. Those refusals are not part of this recording; the accepted import is, under Data.</p>
        )}

        {s.n === w.steps.length && <Outcome w={w} />}

        <div className="wk-foot">
          <p className="wk-links">
            <span className="wk-links-k">In Experiments:</span>{" "}
            {s.links.map((l, i) => (
              <span key={l.requestId}>{i ? " · " : ""}<button type="button" className="text-link" onClick={() => onOpen(l.requestId)}>{l.label} <span className="ws-muted num">#{n(l.requestId)}</span></button></span>
            ))}
          </p>
          <div className="wk-nav">
            <button type="button" className="btn btn-small btn-quiet" onClick={onShow}>Show in the workspace</button>
            <button type="button" className="btn btn-small btn-quiet" disabled={step === 0} onClick={() => onStep(step - 1)}>Previous</button>
            <button type="button" className="btn btn-small" disabled={step === last} onClick={() => onStep(step + 1)}>{step === last ? "Last step" : `Next: ${w.steps[step + 1].short.toLowerCase()}`}</button>
          </div>
        </div>
      </article>
    </section>
  );
}

/** The design beside what it still lacks: the lab-test allowance spent, its own protocol never run, and the conclusion's
 *  unresolved points in one list (the caveats said once, not on every card). */
function Outcome({ w }: { w: Walk }) {
  const o = w.outcome;
  const lab = o.labTests;
  return (
    <section className="wk-outcome" aria-labelledby="wk-out-h">
      <div className="wk-out-main">
        <p className="wk-k" id="wk-out-h">Provisional design · simulation only · model v{o.modelVersion}</p>
        <p className="wk-out-big num">{withUnit(o.minutes, "min")}</p>
        <p className="wk-out-sub num">{o.rates} · 10 to 80 % SOC{o.members != null ? `, slowest of ${num(o.members)} members` : ""}</p>
      </div>
      <dl className="wk-out-facts">
        <div className="is-bad">
          <dt>Simulated lab tests</dt>
          <dd className="num">{lab.cap != null ? `${lab.used} of ${lab.cap} used` : `${lab.used} used`}{lab.numbers.length ? ` (tests ${lab.numbers.join(" and ")})` : ""}</dd>
        </div>
        <div className={o.designTested ? "" : "is-bad"}>
          <dt>This design’s own protocol</dt>
          <dd>{o.designTested ? "run as a lab test" : "not run as a lab test"}</dd>
        </div>
        <div>
          <dt>Requests of every kind</dt>
          <dd className="num">{o.experiments.used} of {o.experiments.budget} allowed <span className="ws-muted">(record reads, fits, checks, lab tests, the design search)</span></dd>
        </div>
      </dl>
      <div className="wk-unresolved">
        <p className="wk-k">What remains unproven</p>
        <ul>{o.unresolved.map((r) => <li key={r}>{upperFirst(r)}</li>)}</ul>
      </div>
    </section>
  );
}
