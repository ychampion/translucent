"use client";
// What a finished tool step returned, laid out for a person: per-filament cards with a small length-over-time line,
// outline tables, the voltage log's protocol and markers, calibration values, the analysis code that ran and what it
// recorded. Everything is read from the step's own recorded summary and fields; nothing here changes a number. A
// tool this file doesn't know falls back to its summary, split into a list when it is long.
import { useState, type ReactNode } from "react";
import type { Measurement } from "@/lib/contracts";
import { clean, unitWord, valueWord } from "@/lib/words";
import "./StepResult.css";

export interface StepData {
  tool: string;
  /** The finished run's own summary, as recorded. */
  summary: string;
  /** Arguments of the started run. */
  args: Record<string, unknown>;
  /** The finished run's structured result. */
  result: Record<string, unknown>;
  measurements: Map<string, Measurement>;
}

// ---------------------------------------------------------------- small pieces

const fmt = (n: number) => {
  const s = Math.abs(n) >= 100 ? n.toFixed(1).replace(/\.0$/, "") : String(+n.toPrecision(3));
  return s.replace(/^-/, "−");
};

/** "+/-0.68 h" -> "± 0.68 h", "mA/cm2" -> "mA/cm²", then the usual text hygiene. */
const tidy = (s: string) => clean(s.replace(/\+\/-\s?/g, "±").replace(/\bcm2\b/g, "cm²").replace(/\s*—\s*$/, ""));
const cap = (s: string) => s.replace(/^\s*\w/, (c) => c.toUpperCase());

function More({ n, noun, open, onToggle }: { n: number; noun: string; open: boolean; onToggle(): void }) {
  return (
    <button type="button" className="sr-more" onClick={onToggle} aria-expanded={open}>
      {open ? "Show less" : `Show all ${n} ${noun}`}
    </button>
  );
}

function Notes({ items }: { items: string[] }) {
  if (!items.length) return null;
  return <ul className="sr-notes">{items.map((t, i) => <li key={i}>{t}</li>)}</ul>;
}

/** Split free text into sentences, keeping decimals and "et al." whole. */
function sentences(s: string): string[] {
  return s.split(/(?<=[a-z0-9)\]%])\.\s+(?=[A-Z])/).map((x) => x.trim().replace(/\.$/, "")).filter(Boolean);
}

// ---------------------------------------------------------------- measure_growth

interface Grown { n: string; images: number; flagged: number; unit: string; points: [number, number][]; speed: string; speedUnit: string; clock?: string }
interface NotGrown { n: string; note: string }

export function parseGrowth(raw: string) {
  const grown: Grown[] = [], skipped: NotGrown[] = [], notes: string[] = [];
  const parts = raw.split(/\n+|\s+(?=#\d+: )/).map((x) => x.trim()).filter(Boolean);
  const re = /^#(\d+):\s*(\d+) images? \((\d+) flagged\),\s*length (\S+) by time \[([^\]]*)\];\s*overall linear speed (\S+ ± \S+) (\S+)(?: \[([^\]]+)\])?/;
  const grouped = new Map<string, string[]>();
  for (const p of parts) {
    const m = p.match(re);
    if (m) {
      const points = m[5].split(/,\s*/).map((x) => x.match(/^([\d.]+)s:([−\-\d.]+)$/)).filter((x): x is RegExpMatchArray => !!x)
        .map((x) => [+x[1], +x[2].replace("−", "-")] as [number, number]);
      grown.push({ n: m[1], images: +m[2], flagged: +m[3], unit: unitWord(clean(m[4])), points, speed: tidy(m[6]), speedUnit: tidy(m[7]), clock: m[8] });
      continue;
    }
    const s = p.match(/^#(\d+):\s*(.*?);\s*not measured\.?$/);
    if (s) { skipped.push({ n: s[1], note: tidy(s[2]) }); continue; }
    const o = p.match(/^#+\s*(.+?):\s*(.*)$/);
    if (o) { const k = tidy(o[2]); grouped.set(k, [...(grouped.get(k) ?? []), o[1]]); continue; }
    notes.push(tidy(p));
  }
  for (const [text, who] of grouped) notes.push(`${who.map((w) => (/^\d+$/.test(w) ? `#${w}` : w)).join(", ")}: ${text}`);
  return { grown, skipped, notes };
}

function Spark({ points, label }: { points: [number, number][]; label: string }) {
  if (points.length < 2) return null;
  const W = 220, H = 40, P = 3;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const X = (x: number) => P + ((x - x0) / (x1 - x0 || 1)) * (W - 2 * P);
  const Y = (y: number) => H - P - ((y - y0) / (y1 - y0 || 1)) * (H - 2 * P);
  const d = points.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(" ");
  return (
    <svg className="sr-spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

function GrowthCard({ g }: { g: Grown }) {
  const [open, setOpen] = useState(false);
  const ys = g.points.map((p) => p[1]);
  const t0 = g.points[0]?.[0], t1 = g.points[g.points.length - 1]?.[0];
  return (
    <li className="sr-card">
      <div className="sr-card-head">
        <span className="sr-badge num">#{g.n}</span>
        <span className="sr-meta num">{g.images} images<span className="sr-dot">·</span>{g.flagged} flagged</span>
        <span className="sr-speed num" title={g.clock ? `Overall linear speed, ${g.clock}` : "Overall linear speed"}>
          {g.speed} <span className="sr-unit">{g.speedUnit}</span>
        </span>
      </div>
      {g.points.length > 1 && (
        <div className="sr-card-plot">
          <Spark points={g.points} label={`Length by time for #${g.n}`} />
          <span className="sr-range num">
            <span>{fmt(Math.min(...ys))}–{fmt(Math.max(...ys))} {g.unit}</span>
            <span className="soft">{t0} s – {t1} s</span>
          </span>
        </div>
      )}
      {g.points.length > 0 && (
        <>
          <More n={g.points.length} noun="points" open={open} onToggle={() => setOpen((o) => !o)} />
          {open && (
            <div className="sr-points num" role="table" aria-label={`Length by time for #${g.n}`}>
              <div className="sr-points-h" role="row"><span role="columnheader">Time</span><span role="columnheader">Length</span></div>
              {g.points.map(([t, y], i) => (
                <div key={i} className="sr-point" role="row"><span role="cell">{t} s</span><span role="cell">{fmt(y)} {g.unit}</span></div>
              ))}
            </div>
          )}
        </>
      )}
    </li>
  );
}

function Growth({ raw }: { raw: string }) {
  const { grown, skipped, notes } = parseGrowth(raw);
  if (!grown.length && !skipped.length && !notes.length) return <Fallback raw={raw} />;
  return (
    <div className="sr">
      {grown.length > 0 && (
        <p className="sr-cap">Overall linear speed per outline, on the recorded clock</p>
      )}
      <ul className="sr-cards">{grown.map((g) => <GrowthCard key={g.n} g={g} />)}</ul>
      {skipped.length > 0 && (
        <ul className="sr-skips">
          {skipped.map((s) => <li key={s.n}><span className="sr-badge is-quiet num">#{s.n}</span><span>Not measured: {s.note}</span></li>)}
        </ul>
      )}
      <Notes items={notes} />
    </div>
  );
}

// ---------------------------------------------------------------- segment_images, track_objects

interface Outline { n: string; frames: number; from: string; to: string; flagged: number; why: string }

export function parseOutlines(text: string) {
  const first = text.search(/#\d+:/);
  if (first < 0) return null;
  const head = text.slice(0, first).replace(/[:\s]+$/, "");
  const items: Outline[] = [];
  const rest: string[] = [];
  for (const part of text.slice(first).split(/;\s*(?=#\d+:)/)) {
    const m = part.trim().replace(/\.$/, "").match(/^#(\d+):\s*(\d+) frames \((\d+)-(\d+)\)(?:,\s*(\d+) flagged(?::\s*(.*))?)?$/);
    if (m) items.push({ n: m[1], frames: +m[2], from: m[3], to: m[4], flagged: m[5] ? +m[5] : 0, why: m[6] ? tidy(m[6]) : "" });
    else rest.push(tidy(part));
  }
  return items.length ? { head: tidy(head), items, rest } : null;
}

function Outlines({ raw }: { raw: string }) {
  const p = parseOutlines(clean(raw));
  if (!p) return <Fallback raw={raw} />;
  return (
    <div className="sr">
      {p.head && <p className="sr-cap">{p.head.replace(/(\d)-(\d)/g, "$1–$2")}</p>}
      <div className="sr-table" role="table">
        <div className="sr-tr sr-th" role="row">
          <span role="columnheader">Outline</span><span role="columnheader">Frames</span><span role="columnheader">Flagged</span><span role="columnheader">Why</span>
        </div>
        {p.items.map((o) => (
          <div key={o.n} className="sr-tr" role="row">
            <span role="cell"><span className="sr-badge num">#{o.n}</span></span>
            <span role="cell" className="num">{o.frames} <span className="soft">({o.from}–{o.to})</span></span>
            <span role="cell" className="num sr-flag">
              <span className="sr-bar" aria-hidden><span style={{ width: `${Math.min(100, (o.flagged / (o.frames || 1)) * 100)}%` }} /></span>
              {o.flagged} of {o.frames}
            </span>
            <span role="cell" className="sr-why">{o.why ? cap(o.why) : <span className="soft">—</span>}</span>
          </div>
        ))}
      </div>
      <Notes items={p.rest} />
    </div>
  );
}

// ---------------------------------------------------------------- read_cell_voltage

interface Phase { cycle: string; kind: string; current: string; from: number; to: number; unit: string }
interface Marker { label: string; what: string; value: string; pm: string; note: string }

export function parseVoltage(raw: string) {
  const head = raw.match(/^Voltage log over ([^(]+?)(?:\s*\(([^)]*)\))?\.\s+/);
  if (!head) return null;
  let rest = raw.slice(head[0].length);
  // m4: "short starts at 90 s (± 3 s) — m_...; short ends at ..."
  const events = [...rest.matchAll(/([a-z][a-z ]*?) at ([\d.]+) s \(± ([\d.]+) s\)/g)].map((m) => ({ what: cap(m[1].trim()), at: +m[2], pm: m[3] }));
  const phases: Phase[] = [];
  const pm = rest.match(/^Protocol:\s*(.*?)\.\s+(?=[A-Z])/);
  if (pm) {
    for (const s of pm[1].split(/;\s*/)) {
      const m = s.match(/^cycle (\d+) (.+?) at ([\d.]+ mA\/cm2):\s*([\d.]+)-([\d.]+) (\w+)$/);
      if (m) phases.push({ cycle: m[1], kind: m[2], current: tidy(m[3]), from: +m[4], to: +m[5], unit: m[6] });
    }
    rest = rest.slice(pm[0].length);
  }
  const markers: Marker[] = [];
  const mm = rest.match(/^Plating markers \(([^)]*)\):\s*/);
  let after = rest;
  if (mm) {
    // Each marker ends with the id of the measurement it recorded; what follows the last one is commentary.
    const chunks = rest.slice(mm[0].length).split(/\s*—\s*m_[0-9a-f]+(?:;\s*|\.\s+|\.?$)/);
    after = chunks.pop() ?? "";
    const left: string[] = [];
    for (const c of chunks) {
      const m = c.match(/^(cycle \d+ \w+(?: \([^)]*\))?):\s*(.*?) after ([\d.]+ \w+) \+\/-([\d.]+ \w+)\s*(.*)$/i);
      if (m) markers.push({ label: tidy(cap(m[1])), what: tidy(m[2]), value: m[3], pm: m[4], note: tidy(m[5]) });
      else left.push(c);
    }
    after = [...left, after].join(". ");
  }
  const range = head[1].match(/^([\d.]+)-([\d.]+) (\w+)$/);
  const notes = events.length ? [] : sentences(clean(after)).map(tidy);
  return {
    span: tidy(head[1]).replace(/(\d)-(\d)/, "$1–$2"), detail: head[2] ? tidy(head[2]) : "",
    range: range ? { from: +range[1], to: +range[2], unit: range[3] } : null,
    events, phases, markersFor: mm ? tidy(mm[1]) : "", markers, notes,
  };
}

function Voltage({ raw }: { raw: string }) {
  const v = parseVoltage(raw);
  if (!v) return <Fallback raw={raw} />;
  const total = v.phases.length ? v.phases[v.phases.length - 1].to - v.phases[0].from : 0;
  return (
    <div className="sr">
      <p className="sr-cap num">Log over {v.span}{v.detail && <span className="soft"> · {v.detail}</span>}</p>
      {v.events.length > 0 && v.range && (
        <div className="sr-line" aria-hidden>
          <span className="sr-line-axis" />
          {v.events.map((e, i) => {
            const at = ((e.at - v.range!.from) / (v.range!.to - v.range!.from || 1)) * 100;
            return <span key={i} className={`sr-line-mark ${/ends/i.test(e.what) ? "is-end" : ""}`} style={{ left: `${Math.max(0, Math.min(100, at))}%` }} />;
          })}
          <span className="sr-line-lo num">{v.range.from} {v.range.unit}</span><span className="sr-line-hi num">{v.range.to} {v.range.unit}</span>
        </div>
      )}
      {v.events.length > 0 && (
        <ul className="sr-kv">
          {v.events.map((e, i) => <li key={i}><span className="sr-k">{e.what}</span><span className="sr-v num">{e.at} s <span className="soft">± {e.pm} s</span></span></li>)}
        </ul>
      )}
      {v.phases.length > 0 && (
        <>
          <p className="sr-sub">Protocol</p>
          <div className="sr-phases" aria-hidden>
            {v.phases.map((p, i) => (
              <span key={i} className={`sr-phase is-${p.kind.startsWith("relax") ? "relax" : p.kind.startsWith("dis") ? "dis" : "charge"}`}
                style={{ flexGrow: Math.max(0.01, (p.to - p.from) / (total || 1)) }} title={`Cycle ${p.cycle} ${p.kind}`} />
            ))}
          </div>
          <div className="sr-table is-phases" role="table">
            {v.phases.map((p, i) => (
              <div key={i} className="sr-tr" role="row">
                <span role="cell" className="sr-phase-key"><span className={`sr-swatch is-${p.kind.startsWith("relax") ? "relax" : p.kind.startsWith("dis") ? "dis" : "charge"}`} />Cycle {p.cycle} {p.kind}</span>
                <span role="cell" className="num soft">{p.current}</span>
                <span role="cell" className="num">{p.from.toFixed(2)}–{p.to.toFixed(2)} {p.unit}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {v.markers.length > 0 && (
        <>
          <p className="sr-sub">Plating markers <span className="soft">({v.markersFor})</span></p>
          <ul className="sr-markers">
            {v.markers.map((m, i) => (
              <li key={i}>
                <div className="sr-marker-top"><span className="sr-marker-label">{m.label}</span><span className="sr-v num">{m.value} <span className="soft">± {m.pm}</span></span></div>
                <p className="sr-marker-what">{cap(m.what)}</p>
                {m.note && <p className="sr-marker-note">{cap(m.note)}</p>}
              </li>
            ))}
          </ul>
        </>
      )}
      <Notes items={v.notes} />
    </div>
  );
}

// ---------------------------------------------------------------- lookup_calibration

function Calibration({ raw }: { raw: string }) {
  const parts = sentences(clean(raw));
  const rows: { k: string; v: string; src?: string }[] = [];
  const notes: string[] = [];
  for (const s of parts) {
    const m = s.match(/^([A-Z][\w ]{1,30}?):\s*(.+)$/);
    if (m && !rows.length) {
      const src = m[2].match(/^(.*?)\s*\((.+)\)$/);
      if (src && /\d/.test(src[1])) rows.push({ k: m[1], v: tidy(src[1]), src: tidy(src[2]) });
      else {
        rows.push({ k: m[1], v: "" });
        notes.push(...m[2].split(/,\s*/).map((x) => cap(tidy(x))));
      }
    } else notes.push(...s.split(/;\s*/).map((x) => cap(tidy(x))));
  }
  if (!rows.length) return <div className="sr"><Notes items={notes} /></div>;
  return (
    <div className="sr">
      <ul className="sr-kv">
        {rows.map((r, i) => (
          <li key={i}><span className="sr-k">{r.k}</span>{r.v && <span className="sr-v num">{r.v}</span>}</li>
        ))}
      </ul>
      {rows.some((r) => r.src) && <p className="sr-src">{rows.map((r) => r.src).filter(Boolean).join(" · ")}</p>}
      {notes.length > 0 && (rows[0].v ? <Notes items={notes} /> : <ul className="sr-chips num">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>)}
    </div>
  );
}

// ---------------------------------------------------------------- inspect_video (no frames to show)

function VideoFacts({ result, raw }: { result: Record<string, unknown>; raw: string }) {
  const n = (k: string) => (typeof result[k] === "number" ? (result[k] as number) : null);
  const chips: string[] = [];
  if (n("width") && n("height")) chips.push(`${n("width")} × ${n("height")} px`);
  if (n("frames")) chips.push(`${n("frames")} frames`);
  if (n("fps")) chips.push(`${n("fps")} fps playback`);
  if (n("repeats")) chips.push(`each image shown ${n("repeats")}×`);
  if (!chips.length) return <Fallback raw={raw} />;
  return <div className="sr"><ul className="sr-chips num">{chips.map((c) => <li key={c}>{c}</li>)}</ul></div>;
}

// ---------------------------------------------------------------- run_analysis_code

const CODE_LINES = 10;

export function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [open, setOpen] = useState(false);
  const lines = code.replace(/\s+$/, "").split("\n");
  const long = lines.length > CODE_LINES;
  return (
    <div className="sr-code">
      <div className="sr-code-head"><span>{lang}</span><span className="soft num">{lines.length} {lines.length === 1 ? "line" : "lines"}</span></div>
      <pre className={`sr-code-body ${long && !open ? "is-clipped" : ""}`}><code>{(open ? lines : lines.slice(0, CODE_LINES)).join("\n")}</code></pre>
      {long && <More n={lines.length} noun="lines" open={open} onToggle={() => setOpen((o) => !o)} />}
    </div>
  );
}

function Analysis({ d }: { d: StepData }) {
  const code = typeof d.args.code === "string" ? d.args.code : "";
  const outs = Array.isArray(d.args.outputs) ? (d.args.outputs as { name?: string; label?: string; unit?: string }[]) : [];
  const made = (d.result.measurements ?? {}) as Record<string, string>;
  const names = Object.keys(made);
  const rows = names.map((name) => {
    const m = d.measurements.get(made[name]);
    const label = outs.find((o) => o.name === name)?.label ?? m?.label ?? cap(name.replace(/_/g, " "));
    const u = m?.uncertainty;
    const pm = m && !Array.isArray(m.value) && u && u.kind !== "none" && u.value != null ? `± ${fmt(u.value)} ${unitWord(m.unit)}` : "";
    return { name, label: tidy(label), value: m ? valueWord(m) : null, pm };
  });
  return (
    <div className="sr">
      {rows.length > 0 && (
        <>
          <p className="sr-cap">Recorded {rows.length} {rows.length === 1 ? "measurement" : "measurements"}</p>
          <ul className="sr-kv">
            {rows.map((r) => (
              <li key={r.name}><span className="sr-k">{r.label}</span>{r.value && <span className="sr-v num">{r.value}{r.pm && <span className="soft"> {r.pm}</span>}</span>}</li>
            ))}
          </ul>
        </>
      )}
      {!rows.length && d.summary && <p className="sr-cap">{tidy(d.summary)}</p>}
      {code && <CodeBlock code={code} lang="Python · sandboxed" />}
    </div>
  );
}

// ---------------------------------------------------------------- fallback

function Fallback({ raw }: { raw: string }) {
  const text = tidy(raw.replace(/\s*Image shows frame[^.]*\./g, "").replace(/\s*—\s*/g, "; "));
  if (!text) return null;
  const parts = text.length > 180 ? text.split(/;\s+|(?<=[a-z0-9)])\.\s+(?=[A-Z])/).map((x) => x.replace(/\.$/, "")).filter(Boolean) : [text];
  return parts.length > 1 ? <div className="sr"><Notes items={parts} /></div> : <p className="step-summary">{text}</p>;
}

// ---------------------------------------------------------------- the switch

/** The body of an opened tool step. */
export function StepResult({ d }: { d: StepData }): ReactNode {
  switch (d.tool) {
    case "measure_growth": return <Growth raw={d.summary} />;
    case "segment_images":
    case "track_objects": return <Outlines raw={d.summary} />;
    case "read_cell_voltage": return <Voltage raw={d.summary} />;
    case "lookup_calibration": return <Calibration raw={d.summary} />;
    case "inspect_video": return <VideoFacts result={d.result} raw={d.summary} />;
    case "run_analysis_code": return <Analysis d={d} />;
    default: return d.summary ? <Fallback raw={d.summary} /> : null;
  }
}

/** A short muted line beside the step's title: what came back, at a glance. */
export function stepDigest(d: StepData): string | null {
  switch (d.tool) {
    case "measure_growth": {
      const { grown, skipped } = parseGrowth(d.summary);
      if (!grown.length && !skipped.length) return null;
      return [grown.length && `${grown.length} measured`, skipped.length && `${skipped.length} not`].filter(Boolean).join(", ");
    }
    case "segment_images":
    case "track_objects": {
      const p = parseOutlines(clean(d.summary));
      return p ? `${p.items.length} ${p.items.length === 1 ? "outline" : "outlines"}` : null;
    }
    case "read_cell_voltage": {
      const v = parseVoltage(d.summary);
      if (!v) return null;
      if (v.events.length) return `${v.events.length} events over ${v.span}`;
      return v.markers.length ? `${v.markers.length} markers over ${v.span}` : `over ${v.span}`;
    }
    case "lookup_calibration": {
      const m = clean(d.summary).match(/^Scale:\s*(.+? per \w+)/);
      if (m) return tidy(m[1]);
      const c = d.summary.match(/^Recorded clock found: .*?one image every ([\d.]+ s)/);
      return c ? `one image every ${c[1]}` : null;
    }
    case "run_analysis_code": {
      const n = Object.keys((d.result.measurements ?? {}) as object).length;
      return n ? `${n} ${n === 1 ? "measurement" : "measurements"}` : null;
    }
    default: return null;
  }
}

/** The error a failed run reported, without the "The tool failed:" lead. */
export function failureText(summary: string): string {
  return tidy(summary.replace(/^The tool failed:\s*/, ""));
}
