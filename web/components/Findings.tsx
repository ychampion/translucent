"use client";
import { useEffect, useState } from "react";
import { paperMatch, type Experiment } from "@/lib/campaigns";
import type { Measurement } from "@/lib/contracts";
import { momentKind, type Catalog, type Finding, type FindingState } from "@/lib/store";
import { STATE_WORD, checkLabel, groupChecks, keepUnits, momentSeconds, plainReason } from "@/lib/words";
import { FindingActions, PeopleLine } from "./Team";

/** The measurements a finding rests on. */
export const measurementsOf = (f: Finding, catalog: Catalog): (Measurement | undefined)[] =>
  f.claim.predicate.measurement_ids.map((id) => catalog.measurements.get(id));

/** The paper's window beside a measured moment ("Paper: 86–93 s"), and whether the measured time overlaps it. */
export function paperFor(ex: Experiment, m: Measurement | undefined) {
  const t = momentSeconds(m);
  if (t == null || !m) return null;
  const give = (m.uncertainty.value ?? 0) * (m.unit === "h" ? 3600 : 1);
  return paperMatch(ex, momentKind(m), t, give);
}

export const findingHref = (key: string, id: string) => `/c/${key}/finding/?id=${encodeURIComponent(id)}`;
export const receiptHref = (key: string, id: string) => `/c/${key}/receipt/?id=${encodeURIComponent(id)}`;

export function StateMark({ state }: { state: FindingState }) {
  return <span className={`mark mark-${state}`} aria-hidden />;
}

export function CheckGlyph({ ok }: { ok: boolean }) {
  return ok ? (
    <svg className="glyph glyph-pass" width="14" height="14" viewBox="0 0 14 14" aria-label="passed"><path d="M2.5 7.4 5.6 10.4 11.5 3.8" /></svg>
  ) : (
    <svg className="glyph glyph-fail" width="14" height="14" viewBox="0 0 14 14" aria-label="failed"><path d="M3.5 3.5 10.5 10.5M10.5 3.5 3.5 10.5" /></svg>
  );
}

const STEP_MS = 480;

/** Reveal a fresh review's checks one at a time; the verdict shows only after the last check lands. */
function useReveal(f: Finding, fresh: boolean): { shown: number; state: FindingState } {
  const n = groupChecks(f.review?.checks ?? []).length;
  const [shown, setShown] = useState(fresh ? 0 : n);
  useEffect(() => {
    if (!fresh || !f.review) { setShown(n); return; }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setShown(n); return; }
    setShown(0);
    let i = 0;
    const id = setInterval(() => { i += 1; setShown(i); if (i >= n) clearInterval(id); }, STEP_MS);
    return () => clearInterval(id);
  }, [f.review?.id, n]); // eslint-disable-line react-hooks/exhaustive-deps -- start only when a review arrives
  return { shown, state: f.review && shown < n ? "checking" : f.state };
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "2 measurements · 12 frames": what a finding rests on, counted. */
export function evidenceCount(f: Finding) {
  const parts = [];
  const m = f.claim.predicate.measurement_ids.length;
  if (m) parts.push(plural(m, "measurement"));
  if (f.frames.length) parts.push(plural(new Set(f.frames).size, "frame"));
  return parts.join(" · ");
}

/** A finding in the conversation: what it says, the checker's verdict and why, and the team's sign-offs. */
export function FindingCard({ f, fresh, selected, onOpen }: { f: Finding; fresh: boolean; selected: boolean; onOpen(): void }) {
  const { shown, state } = useReveal(f, fresh);
  const checks = groupChecks(f.review?.checks ?? []);
  const revealing = !!f.review && shown < checks.length;
  const why = f.review ? plainReason(f.review, f.claim) : f.failed ? "The check couldn’t run." : null;
  const evidence = evidenceCount(f);
  return (
    <article className={`fcard state-${state} ${selected ? "is-selected" : ""} ${f.replacedBy ? "is-replaced" : ""}`} data-finding={f.claim.id}>
      <button type="button" className="fcard-main" onClick={onOpen} aria-label={`Open the evidence for: ${f.claim.statement}`}>
        <span className="fcard-head">
          <StateMark state={state} />
          <span className="fcard-state">{STATE_WORD[state]}</span>
          {evidence && !revealing && <span className="fcard-count num">{evidence}</span>}
        </span>
        <span className="fcard-statement">{keepUnits(f.claim.statement)}</span>
        {(revealing || (state === "checking" && !f.review && !f.failed)) && (
          <span className="checks-live">
            {checks.slice(0, shown).map((c) => (
              <span key={c.name} className={`check ${c.passed ? "pass" : "fail"}`}><CheckGlyph ok={c.passed} /> {checkLabel(c.name)}</span>
            ))}
            {!f.review && <span className="check pending">Checking</span>}
          </span>
        )}
        {!revealing && why && state !== "earned" && <span className="fcard-why">{why}</span>}
        {!revealing && f.review && state === "earned" && <span className="fcard-why is-quiet">{checks.length} of {checks.length} checks passed</span>}
        {!revealing && f.replacedBy && <span className="fcard-why is-quiet">Tried again below</span>}
      </button>
      {!revealing && (f.people.length > 0 || state === "earned") && (
        <div className="fcard-people">
          <PeopleLine f={f} compact />
          <FindingActions f={f} />
        </div>
      )}
    </article>
  );
}
