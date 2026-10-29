"use client";
import { useEffect, useMemo, useRef } from "react";
import type { Track } from "@/lib/contracts";
import type { Region } from "@/lib/campaigns";
import { fitBox, trackBox, type Box } from "@/lib/geometry";

interface Props {
  src: string;
  fps: number;
  size: [number, number];
  frames: number[];
  track?: Track;
  /** Width / height of each tile. */
  aspect?: number;
  columns?: number;
  caption?: (f: number, i: number) => string;
  className?: string;
  /** Crop to this part of the video (source pixels) when no track decides the crop. */
  region?: Region;
  /** Index of the tile to emphasise. */
  highlight?: number;
}

/** Still frames cut from the recorded video at exact frame indices, with the tracked mask drawn on each. */
export function FrameStrip({ src, fps, size, frames, track, aspect, columns, caption, className, region, highlight }: Props) {
  const [W, H] = size;
  const ar = aspect ?? (region ? region.w / region.h : 4 / 3);
  const roi: Box = useMemo(() => {
    const b = track ? trackBox(track) : null;
    if (b) return fitBox(b, ar, 1.35, W, H);
    return fitBox(region ?? { x: 0, y: 0, w: W, h: H }, ar, 1, W, H);
  }, [track, ar, W, H, region]);
  const canvases = useRef<(HTMLCanvasElement | null)[]>([]);
  const key = frames.join(",");

  useEffect(() => {
    let cancelled = false;
    const v = document.createElement("video");
    v.muted = true; v.preload = "auto"; v.src = src; v.crossOrigin = "anonymous";
    const once = (ev: string) => new Promise<void>((r) => v.addEventListener(ev, () => r(), { once: true }));
    (async () => {
      if (v.readyState < 2) await once("loadeddata");
      for (let i = 0; i < frames.length && !cancelled; i++) {
        v.currentTime = (frames[i] + 0.5) / fps;
        await once("seeked");
        const c = canvases.current[i];
        const ctx = c?.getContext("2d");
        if (!c || !ctx) continue;
        ctx.drawImage(v, roi.x, roi.y, roi.w, roi.h, 0, 0, c.width, c.height);
        c.dataset.ready = "1";
      }
    })();
    return () => { cancelled = true; v.removeAttribute("src"); v.load(); };
  }, [src, fps, key, roi]); // eslint-disable-line react-hooks/exhaustive-deps

  const cols = columns ?? (frames.length <= 5 ? frames.length : 5);
  const cw = 640, ch = Math.round(640 / ar);
  return (
    <div className={`frames ${className ?? ""}`} style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
      {frames.map((f, i) => {
        const j = track ? track.frames.indexOf(f) : -1;
        const tip = j >= 0 ? track!.points?.[j] : null;
        const u = roi.w / 640;
        return (
          <figure key={`${f}-${i}`} className={`frame ${highlight === i ? "is-focus" : ""}`}>
            <div className="frame-img" style={{ aspectRatio: `${ar}` }}>
              <canvas ref={(el) => { canvases.current[i] = el; }} width={cw} height={ch} />
              {j >= 0 && (
                <svg viewBox={`${roi.x} ${roi.y} ${roi.w} ${roi.h}`} preserveAspectRatio="none" aria-hidden>
                  <path d={track!.mask_paths[j]} transform={`translate(${track!.mask_origins[j][0]} ${track!.mask_origins[j][1]})`} className="mask" vectorEffect="non-scaling-stroke" />
                  {tip && <circle cx={tip[0]} cy={tip[1]} r={10 * u} className="tip-ring" vectorEffect="non-scaling-stroke" />}
                </svg>
              )}
            </div>
            <figcaption className="num">{caption ? caption(f, i) : `Frame ${f}`}</figcaption>
          </figure>
        );
      })}
    </div>
  );
}
