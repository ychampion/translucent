"use client";
import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Track } from "@/lib/contracts";
import type { Region } from "@/lib/campaigns";
import { VideoClock, useClock } from "@/lib/clock";
import { fitBox, trackBox, type Box } from "@/lib/geometry";

interface Props {
  clock: VideoClock;
  src: string;
  poster?: string;
  size: [number, number];
  fps: number;
  tracks: Track[];
  visible: string[];
  /** The microscope part of the video, in source pixels. The stage frames exactly this. */
  region?: Region;
  /** Zoom to the tracked region once something is being tracked. */
  closeUp: boolean;
  label?: string;
  /** Small credit shown while outlines are drawn. */
  credit?: string;
  /** First frame of each distinct image, once known: the tip is marked once per image. */
  distinct?: number[] | null;
  /** A quiet state chip over the footage (e.g. "Battery shorted"). */
  chip?: ReactNode;
  autoPlay?: boolean;
}

/** The video, framed on the microscope, with what the system sees drawn over it in source-pixel coordinates. */
export const Stage = memo(function Stage({ clock, src, poster, size, fps, tracks, visible, region, closeUp, label, credit, distinct, chip, autoPlay }: Props) {
  const [W, H] = size;
  const base: Box = region ?? { x: 0, y: 0, w: W, h: H };
  const videoRef = useRef<HTMLVideoElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [boxPx, setBoxPx] = useState(1200);
  const [whole, setWhole] = useState(false);

  // Frame-accurate sync: requestVideoFrameCallback where available, otherwise animation frames.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    clock.video = v;
    clock.fps = fps;
    let handle = 0, raf = 0, stopped = false;
    const total = () => (Number.isFinite(v.duration) ? Math.round(v.duration * fps) : 0);
    const update = (mediaTime?: number) => {
      const t = mediaTime ?? v.currentTime;
      const f = mediaTime != null ? Math.round(t * fps) : Math.floor(t * fps + 1e-4);
      const n = total();
      clock.set(Math.max(0, n ? Math.min(n - 1, f) : f), n, !v.paused);
    };
    const hasRVFC = "requestVideoFrameCallback" in HTMLVideoElement.prototype;
    const onFrame = (_: number, meta: VideoFrameCallbackMetadata) => {
      if (stopped) return;
      update(meta.mediaTime);
      handle = v.requestVideoFrameCallback(onFrame);
    };
    const loop = () => { if (stopped) return; update(); raf = requestAnimationFrame(loop); };
    if (hasRVFC) handle = v.requestVideoFrameCallback(onFrame); else raf = requestAnimationFrame(loop);
    const sync = () => update();
    v.addEventListener("seeked", sync);
    v.addEventListener("loadedmetadata", sync);
    v.addEventListener("play", sync);
    v.addEventListener("pause", sync);
    return () => {
      stopped = true;
      if (hasRVFC && handle) v.cancelVideoFrameCallback(handle);
      cancelAnimationFrame(raf);
      v.removeEventListener("seeked", sync);
      v.removeEventListener("loadedmetadata", sync);
      v.removeEventListener("play", sync);
      v.removeEventListener("pause", sync);
      if (clock.video === v) clock.video = null;
    };
  }, [clock, fps]);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBoxPx(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const shown = useMemo(() => tracks.filter((t) => visible.includes(t.subject)), [tracks, visible]);
  // Close up on the tracked object only when it is much smaller than the view; otherwise the view already shows it.
  const roi: Box | null = useMemo(() => {
    const b = shown.length ? trackBox(shown[0]) : null;
    if (!b) return null;
    const r = fitBox(b, base.w / base.h, 2.1, W, H);
    return r.w < base.w * 0.7 ? r : null;
  }, [shown, W, H, base.w, base.h]);

  const aspect = base.w / base.h;
  // What fills the frame: the microscope region, a close-up, or the whole video letterboxed in the same frame.
  const view: Box = whole ? letterbox(W, H, aspect) : closeUp && roi ? roi : base;
  const z = base.w / view.w;
  const transform = `translate(${(-view.x * z / W) * 100}%, ${(-view.y * z / H) * 100}%) scale(${z})`;
  const unit = view.w / boxPx; // source px per screen px, for constant-size marks
  const hasView = !!region || !!roi;

  return (
    <div className="stage" style={{ ["--ar" as string]: aspect }}>
      <div className="stage-box" ref={boxRef}>
        <div
          className="stage-view"
          style={{ width: `${(W / base.w) * 100}%`, height: `${(H / base.h) * 100}%`, transform }}
        >
          <video
            ref={videoRef}
            src={src}
            poster={poster}
            muted
            playsInline
            loop
            preload="auto"
            autoPlay={autoPlay ?? tracks.length === 0}
            className="stage-video"
          />
          <Overlay clock={clock} W={W} H={H} tracks={shown} unit={unit} distinct={distinct ?? null} />
        </div>
      </div>
      {hasView && (
        <button className="stage-toggle" onClick={() => setWhole((x) => !x)}>
          {region ? (whole ? "Microscope" : "Original video") : whole ? "Close up" : "Whole frame"}
        </button>
      )}
      {chip}
      <div className="stage-caption">
        {label && <span>{label}</span>}
        {credit && shown.length > 0 && <span>{credit}</span>}
      </div>
    </div>
  );
});

/** The whole W×H video centred in a box of the given aspect (in source pixels; may extend past the video). */
function letterbox(W: number, H: number, aspect: number): Box {
  if (W / H > aspect) { const h = W / aspect; return { x: 0, y: -(h - H) / 2, w: W, h }; }
  const w = H * aspect;
  return { x: -(w - W) / 2, y: 0, w, h: H };
}

function Overlay({ clock, W, H, tracks, unit, distinct }: { clock: VideoClock; W: number; H: number; tracks: Track[]; unit: number; distinct: number[] | null }) {
  const { frame } = useClock(clock);
  return (
    <svg className="overlay" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden>
      {tracks.map((t) => {
        // Outlines exist for some captured images; hold the latest one until the next.
        let i = -1;
        for (let k = 0; k < t.frames.length; k++) if (t.frames[k] <= frame) i = k;
        if (i < 0 || (distinct ? false : t.frames[i] !== frame && frame - t.frames[i] > 12)) return null;
        if (distinct) {
          const img = (f: number) => { let g = 0; for (let k = 0; k < distinct.length; k++) if (distinct[k] <= f) g = k; return g; };
          if (img(t.frames[i]) !== img(frame)) return null;
        }
        const [ox, oy] = t.mask_origins[i];
        const tip = t.points?.[i] ?? null;
        // One mark per captured image (not per repeated frame), once the repeats are known.
        const steps = distinct && t.points
          ? t.frames.map((f, j) => (distinct.includes(f) && f <= frame ? t.points?.[j] : null)).filter((p): p is [number, number] => !!p)
          : [];
        return (
          <g key={t.subject}>
            <path
              d={t.mask_paths[i]}
              transform={`translate(${ox} ${oy})`}
              className="mask"
              fillRule="evenodd"
              vectorEffect="non-scaling-stroke"
            />
            {steps.length > 1 && steps.map((p, j) => (
              <circle key={j} cx={p[0]} cy={p[1]} r={3.2 * unit} className="step-dot" />
            ))}
            {tip && tracks.length === 1 && (
              <g transform={`translate(${tip[0]} ${tip[1]})`}>
                <circle r={9 * unit} className="tip-ring" vectorEffect="non-scaling-stroke" />
                <circle r={2.2 * unit} className="tip-dot" />
                <text x={-16 * unit} y={-14 * unit} className="tip-label" style={{ fontSize: 15 * unit }}>
                  Tip
                </text>
              </g>
            )}
          </g>
        );
      })}
    </svg>
  );
}
