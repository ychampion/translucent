"use client";
import { memo, useCallback, useRef } from "react";
import { VideoClock, useClock } from "@/lib/clock";

export interface Marker { frame: number; tone: "gold" | "crimson" | "steel" | "grey"; label: string }

interface Props {
  clock: VideoClock;
  /** First frame of each distinct image, once repeats have been found. */
  distinct: number[] | null;
  tracked: [number, number] | null;
  markers: Marker[];
}

const H = 46;

/** Frames strip. Before repeats are found: one tick per frame. After: ticks collapse into one block per image. */
export const Timeline = memo(function Timeline({ clock, distinct, tracked, markers }: Props) {
  const { frame, frames, playing } = useClock(clock);
  const ref = useRef<SVGSVGElement>(null);
  const N = Math.max(frames, 1);
  const G = distinct?.length ?? 0;
  const groupOf = (f: number) => {
    if (!distinct) return 0;
    let g = 0;
    for (let i = 0; i < distinct.length; i++) if (distinct[i] <= f) g = i;
    return g;
  };
  // Positions in percent of width.
  const frameX = (f: number) => ((f + 0.5) / N) * 100;
  const blockX = (g: number) => ((g + 0.5) / G) * 100;
  const xOf = (f: number) => (distinct ? blockX(groupOf(f)) : frameX(f));

  const seekAt = useCallback((clientX: number) => {
    const el = ref.current;
    if (!el || !frames) return;
    const r = el.getBoundingClientRect();
    const p = Math.max(0, Math.min(0.9999, (clientX - r.left) / r.width));
    if (distinct) clock.seek(distinct[Math.floor(p * distinct.length)]);
    else clock.seek(Math.floor(p * N));
  }, [clock, distinct, frames, N]);

  const dragging = useRef(false);
  const inTracked = (f: number) => !!tracked && f >= tracked[0] && f <= tracked[1];
  const gImage = groupOf(frame);
  const ticks = frames <= 600;
  const tickW = Math.max(0.6, Math.min(1.5, 600 / Math.max(frames, 1) * 0.5));
  const labelEvery = G <= 20 ? 1 : G <= 60 ? 5 : 10;

  return (
    <div className="timeline">
      <button className="round-btn" onClick={() => clock.toggle()} aria-label={playing ? "Pause video" : "Play video"}>
        {playing ? (
          <svg width="12" height="12" viewBox="0 0 12 12"><rect x="2" y="1.5" width="2.6" height="9" rx="0.6" /><rect x="7.4" y="1.5" width="2.6" height="9" rx="0.6" /></svg>
        ) : (
          <svg width="12" height="12" viewBox="0 0 12 12"><path d="M3 1.6v8.8a.5.5 0 0 0 .76.43l7.1-4.4a.5.5 0 0 0 0-.86L3.76 1.17A.5.5 0 0 0 3 1.6Z" /></svg>
        )}
      </button>
      <div className="timeline-body">
        <div className="timeline-meta">
          <span className="num">
            {distinct ? <>Image {gImage + 1} of {G}<span className="soft">{`, frame ${frame}`}</span></> : <>Frame {frame} of {frames || "—"}</>}
          </span>
          {tracked && <span className="soft">Tracked {tracked[0]}–{tracked[1]}</span>}
        </div>
        <svg
          ref={ref}
          className="timeline-strip"
          width="100%"
          height={H}
          role="slider"
          aria-label="Video position"
          aria-valuemin={0}
          aria-valuemax={N - 1}
          aria-valuenow={frame}
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight") { clock.pause(); clock.seek(distinct ? distinct[Math.min(G - 1, gImage + 1)] : frame + 1); e.preventDefault(); e.stopPropagation(); }
            if (e.key === "ArrowLeft") { clock.pause(); clock.seek(distinct ? distinct[Math.max(0, gImage - 1)] : frame - 1); e.preventDefault(); e.stopPropagation(); }
          }}
          onPointerDown={(e) => { dragging.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); clock.pause(); seekAt(e.clientX); }}
          onPointerMove={(e) => dragging.current && seekAt(e.clientX)}
          onPointerUp={() => { dragging.current = false; }}
        >
          {/* Image blocks (after repeats are found). */}
          {distinct?.map((f0, g) => {
            const w = 100 / G;
            const last = g === G - 1 ? N - 1 : distinct[g + 1] - 1;
            return (
              <g key={`b${g}`} className="tl-block-in">
                <rect
                  x={`${g * w + (G > 30 ? 0.1 : 0.35)}%`} y={8} width={`${w - (G > 30 ? 0.2 : 0.7)}%`} height={22} rx={G > 30 ? 1.5 : 3}
                  className={`tl-block ${inTracked(f0) || inTracked(last) ? "is-tracked" : ""} ${g === gImage ? "is-current" : ""}`}
                />
                {(g + 1) % labelEvery === 0 || g === 0 ? <text x={`${blockX(g)}%`} y={44} className="tl-num" textAnchor="middle">{g + 1}</text> : null}
              </g>
            );
          })}
          {/* Frame ticks: they slide into their image's block when repeats are found. */}
          {!ticks && <rect className="tl-rail" x={0} y={14} width="100%" height={10} rx={5} />}
          {ticks && Array.from({ length: frames }, (_, f) => (
            <rect
              key={f}
              className={`tl-tick ${inTracked(f) ? "is-tracked" : ""} ${distinct ? "is-collapsed" : ""}`}
              x={0} y={10} width={tickW} height={18}
              style={{ transform: `translateX(calc(${xOf(f)}% - ${tickW / 2}px))` }}
            />
          ))}
          {!distinct && tracked && (
            <rect className="tl-tracked" x={`${(tracked[0] / N) * 100}%`} y={34} width={`${((tracked[1] - tracked[0] + 1) / N) * 100}%`} height={3} rx={1.5} />
          )}
          {markers.map((m) => (
            <circle key={`${m.frame}-${m.label}`} cx={`${xOf(m.frame)}%`} cy={4} r={3.5} className={`tl-dot tone-${m.tone}`}>
              <title>{m.label}</title>
            </circle>
          ))}
          <line x1={`${xOf(frame)}%`} x2={`${xOf(frame)}%`} y1={2} y2={distinct ? 31 : 34} className="tl-head" />
        </svg>
      </div>
    </div>
  );
});
