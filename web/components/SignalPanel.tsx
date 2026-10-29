"use client";
import { memo, useMemo } from "react";
import type { Series } from "@/lib/contracts";
import type { Moment } from "@/lib/store";
import { VideoClock, useClock } from "@/lib/clock";
import { isLong, timeOn } from "@/lib/words";

const VW = 1000, VH = 100;

/** A value the source states, drawn as a quiet dashed line (seconds on the recording's clock). */
export interface Reference { t: number; label: string }

/**
 * "Cell voltage (movie time is approximate, +/-1224.25 s)" -> name "Cell voltage", note "Video lined up to ± 20 min".
 * The export names a log-clock series this way when the movie's own clock is coarse.
 */
export function seriesTitle(name: string): { name: string; note: string | null } {
  const m = name.match(/^(.*?)\s*\(movie time is approximate, \+\/-([\d.]+) s\)$/);
  if (!m) return { name, note: null };
  const s = +m[2];
  const bound = s >= 90 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`;
  return { name: m[1], note: `Video lined up to ± ${bound}` };
}

/** Tick spacing for a recording span: every minute for short ones, every 2 or 5 hours for long ones. */
function tickStep(span: number) {
  if (!isLong(span)) return 60;
  return span > 20 * 3600 ? 5 * 3600 : 2 * 3600;
}

/** A series that can follow the video: it says which frame each sample belongs to. */
export const isSynced = (s: Series | undefined): s is Series & { frame_of_t: number[] } =>
  !!s && !!s.frame_of_t?.length && s.frame_of_t.length === s.t.length;

/** Spans between an earned short and the earned recovery after it. */
export function shortSpans(moments: Moment[]): [number, number][] {
  const earned = moments.filter((m) => m.state === "earned").sort((a, b) => a.t - b.t);
  const out: [number, number][] = [];
  earned.forEach((m, i) => {
    if (m.kind !== "short") return;
    const next = earned.slice(i + 1).find((x) => x.kind !== "other");
    if (next?.kind === "recovery") out.push([m.t, next.t]);
  });
  return out;
}

/** Index of the last sample at or before this frame. */
export function sampleAt(frameOf: number[], frame: number) {
  let lo = 0, hi = frameOf.length - 1, best = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (frameOf[mid] <= frame) { best = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return best;
}

interface Props {
  clock: VideoClock;
  series: Series | undefined;
  moments: Moment[];
  /** Static: the whole trace, no playhead (summary and finding views). */
  still?: boolean;
  /** Emphasise one moment (finding view). */
  focus?: string;
  /** Click on the trace seeks the video to that moment. */
  seekable?: boolean;
  /** A stated value to compare against (e.g. the paper's onset). */
  reference?: Reference;
}

/** Cell voltage, drawn under the video and written in as the video plays. Hidden when it can't follow the video. */
export const SignalPanel = memo(function SignalPanel(p: Props) {
  if (!p.series) {
    return (
      <div className="signal signal-empty">
        <div className="signal-absent">
          <svg width="100%" height="2" aria-hidden><line x1="0" x2="100%" y1="1" y2="1" /></svg>
          <span>No voltage recorded with this movie</span>
        </div>
      </div>
    );
  }
  if (!isSynced(p.series)) return null; // a trace that can't be lined up with the video would only mislead
  return <SignalPlot {...p} series={p.series} />;
});

function SignalPlot({ clock, series, moments, still, focus, seekable = true, reference }: Props & { series: Series & { frame_of_t: number[] } }) {
  const { frame } = useClock(clock);
  const geo = useMemo(() => {
    // A log-clock export repeats a sample while the movie holds one image: drop the repeats from the drawn line.
    const keep = series.t.map((t, i) => i === 0 || t !== series.t[i - 1]);
    const t0 = series.t[0], t1 = series.t[series.t.length - 1];
    let y0 = Math.min(...series.y, 0), y1 = Math.max(...series.y, 0);
    const pad = (y1 - y0) * 0.1; y0 -= pad; y1 += pad;
    const X = (t: number) => ((t - t0) / (t1 - t0 || 1)) * VW;
    const Y = (y: number) => VH - ((y - y0) / (y1 - y0 || 1)) * VH;
    const d = series.t.map((t, i) => (keep[i] ? `${i ? "L" : "M"}${X(t).toFixed(1)} ${Y(series.y[i]).toFixed(1)}` : "")).join("");
    const long = isLong(t1 - t0);
    const step = tickStep(t1 - t0);
    const ticks: number[] = [];
    for (let m = Math.ceil(t0 / step) * step; m <= t1; m += step) ticks.push(m);
    return { d, X, Y, t0, t1, zero: Y(0), ticks, long };
  }, [series]);
  const at = (t: number) => timeOn(t, geo.long);
  const title = seriesTitle(series.name);
  const ref = reference && reference.t >= geo.t0 && reference.t <= geo.t1 ? reference : null;

  const i = sampleAt(series.frame_of_t, frame);
  const now = { t: series.t[i], y: series.y[i] };
  const px = still ? VW : geo.X(now.t);
  const spans = shortSpans(moments);
  const shorted = spans.some(([a, b]) => now.t >= a && now.t < b);
  const pct = (x: number) => `${(x / VW) * 100}%`;
  const id = `clip-${series.name.replace(/\W+/g, "")}${still ? "-s" : ""}`;

  const seek = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!seekable || !clock.video) return;
    const r = e.currentTarget.getBoundingClientRect();
    const t = geo.t0 + ((e.clientX - r.left) / r.width) * (geo.t1 - geo.t0);
    let k = 0;
    for (let j = 0; j < series.t.length; j++) if (series.t[j] <= t) k = j;
    clock.pause();
    clock.seek(series.frame_of_t[k]);
  };

  return (
    <div className={`signal ${still ? "is-still" : ""}`}>
      <div className="signal-head">
        <span className="signal-name">{title.name}</span>
        {title.note && <span className="signal-note soft">{title.note}</span>}
        {!still && (
          <span className="signal-now num">
            <span className="signal-value">{now.y.toFixed(Math.abs(now.y) < 1 ? 2 : 1).replace(/^-/, "−")} {series.unit}</span>
            <span className="soft">{at(now.t)} into the recording</span>
            {shorted && <span className="signal-state">Shorted</span>}
          </span>
        )}
      </div>
      <div className="signal-body" onPointerDown={seek}>
        <svg className="signal-plot" viewBox={`0 0 ${VW} ${VH}`} preserveAspectRatio="none" aria-label={`${series.name} over the recording`}>
          <defs>
            <clipPath id={id}><rect x={0} y={-10} width={px} height={VH + 20} /></clipPath>
          </defs>
          {spans.map(([a, b]) => (
            <rect key={a} x={geo.X(a)} width={geo.X(b) - geo.X(a)} y={0} height={VH} className="signal-span" />
          ))}
          <line x1={0} x2={VW} y1={geo.zero} y2={geo.zero} className="signal-zero" vectorEffect="non-scaling-stroke" />
          {ref && (
            <line x1={geo.X(ref.t)} x2={geo.X(ref.t)} y1={0} y2={VH} className="signal-reference" vectorEffect="non-scaling-stroke" />
          )}
          <path d={geo.d} className="signal-future" vectorEffect="non-scaling-stroke" />
          <path d={geo.d} className="signal-line" clipPath={`url(#${id})`} vectorEffect="non-scaling-stroke" />
          {moments.map((m) => (
            <line
              key={`${m.findingId}-${m.t}`}
              x1={geo.X(m.t)} x2={geo.X(m.t)} y1={0} y2={VH}
              className={`signal-moment tone-${m.state} ${focus && focus !== m.findingId ? "is-dim" : ""}`}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        {moments.map((m) => (
          <span
            key={`l-${m.findingId}-${m.t}`}
            className={`signal-tag tone-${m.state} ${focus && focus !== m.findingId ? "is-dim" : ""} ${
              geo.X(m.t) > VW * 0.8 || (ref && geo.X(ref.t) > geo.X(m.t) && geo.X(ref.t) - geo.X(m.t) < VW * 0.16) ? "is-left" : ""}`}
            style={{ left: pct(geo.X(m.t)) }}
          >
            {m.kind === "short" ? "Shorts" : m.kind === "recovery" ? "Recovers" : m.label}{" "}
            <span className="num">{geo.long ? at(m.t) : `${Math.round(m.t)} s`}</span>
          </span>
        ))}
        {ref && (
          <span className={`signal-tag tone-reference ${geo.X(ref.t) > VW * 0.8 ? "is-left" : ""}`} style={{ left: pct(geo.X(ref.t)) }}>
            {ref.label} <span className="num">{geo.long ? at(ref.t) : `${Math.round(ref.t)} s`}</span>
          </span>
        )}
        {!still && (
          <>
            <span className="signal-head-line" style={{ left: pct(px) }} />
            <span className="signal-dot" style={{ left: pct(px), top: `${(geo.Y(now.y) / VH) * 100}%` }} />
          </>
        )}
        <span className="signal-zero-label num" style={{ top: `${(geo.zero / VH) * 100}%` }}>0 {series.unit}</span>
      </div>
      <div className="signal-axis num" aria-hidden>
        {geo.ticks.map((t) => (
          <span key={t} style={{ left: pct(geo.X(t)) }}>{at(t)}</span>
        ))}
      </div>
    </div>
  );
}
