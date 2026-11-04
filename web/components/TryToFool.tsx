"use client";
import { useEffect, useState } from "react";
import { DOUBLE_CHECK } from "@/lib/proof";
import { withBase } from "@/lib/base";
import "./TryToFool.css";

/** web/public/fixtures/evals.json, written by scripts/export_eval_cases.py from a real eval run. */
interface Value { value: number; unit: string }
interface EvalCase { id: string; label: string; quantity: string; reason: string; plain: Value; truth?: Value }
interface Evals {
  source: { file: string; generated_at: string };
  totals: { planted: number; caught: number; plain_caught: number };
  cases: EvalCase[];
}

const fmt = (v: Value) => `${v.value.toLocaleString("en-GB", { maximumFractionDigits: 3 })} ${v.unit}`;

/** "18× too high", from the two numbers in the file; nothing when they can't be compared. */
function howFar(plain: Value, truth?: Value) {
  if (!truth || truth.unit !== plain.unit || truth.value <= 0) return null;
  const r = plain.value / truth.value;
  return r >= 1.5 ? `${Math.round(r)}× too high` : null;
}

/** Plant one mistake a normal analysis makes, and see what each side reports. Every number is from the eval run. */
export function TryToFool() {
  const [data, setData] = useState<Evals | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    fetch(withBase("/fixtures/evals.json")).then((r) => (r.ok ? r.json() : null)).then((d: Evals | null) => { if (live && d) { setData(d); setPicked((p) => p ?? d.cases[0]?.id ?? null); } }).catch(() => {});
    return () => { live = false; };
  }, []);
  if (!data) return null;

  const c = data.cases.find((x) => x.id === picked);
  const { planted, caught, plain_caught } = data.totals;
  const ran = new Date(data.source.generated_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const far = c ? howFar(c.plain, c.truth) : null;

  return (
    <section className="fool" aria-label="Try to fool it">
      <div className="fool-head">
        <h2 className="fool-title">Try to fool it</h2>
        <p className="fool-lead">Plant a mistake a normal analysis makes. See what each side reports.</p>
      </div>

      <div className="fool-picks" role="group" aria-label="Planted mistakes">
        {data.cases.map((x) => (
          <button key={x.id} type="button" className="fool-pick" aria-pressed={x.id === picked} onClick={() => setPicked(x.id)}>
            {x.label}
          </button>
        ))}
      </div>

      <div className="fool-result" aria-live="polite">
        {c ? (
          <div className="fool-sides" key={c.id}>
            <div className="fool-side is-plain">
              <p className="fool-who">A normal pipeline</p>
              <p className="fool-value num">{fmt(c.plain)}</p>
              <p className="fool-sub">{c.quantity}, reported with full confidence{far ? `. ${far}.` : "."}</p>
            </div>
            <div className="fool-side is-refused">
              <p className="fool-who">Translucent</p>
              <p className="fool-value">Refused</p>
              <p className="fool-why">{c.reason}</p>
            </div>
            <p className="fool-foot">
              <span className="fool-tag">Planted mistake</span>
              {c.truth && <span className="num">True value {fmt(c.truth)}</span>}
            </p>
          </div>
        ) : (
          <p className="fool-empty">Pick a mistake to plant.</p>
        )}
      </div>

      <p className="fool-tally num">
        Planted mistakes caught: <strong>{caught} of {planted}</strong>. Normal pipeline: <strong>{plain_caught}</strong>.
        <span className="fool-source" title={data.source.file}>Test run of {ran}</span>
      </p>
      <p className="fool-aside">
        Asking the model to double-check, given each finding and its numbers, let{" "}
        <strong className="num">{DOUBLE_CHECK.letThrough} of {DOUBLE_CHECK.judged}</strong> through. Given the full
        evidence record, it caught all {DOUBLE_CHECK.fullCaught}: the record is what makes the check possible.
      </p>
    </section>
  );
}
