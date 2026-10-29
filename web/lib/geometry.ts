import type { Track } from "./contracts";

export interface Box { x: number; y: number; w: number; h: number }

/** Bounding box of an SVG path's coordinates (M/L absolute pairs, as produced by the mask encoder). */
export function pathBox(d: string, ox = 0, oy = 0): Box | null {
  const nums = d.match(/-?\d*\.?\d+(?:e-?\d+)?/g);
  if (!nums || nums.length < 2) return null;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i + 1 < nums.length; i += 2) {
    const x = +nums[i] + ox, y = +nums[i + 1] + oy;
    if (x < x0) x0 = x; if (x > x1) x1 = x;
    if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/** Union of all mask boxes in a track. */
export function trackBox(t: Track): Box | null {
  let u: Box | null = null;
  t.mask_paths.forEach((d, i) => {
    const b = pathBox(d, t.mask_origins[i][0], t.mask_origins[i][1]);
    if (!b) return;
    if (!u) { u = b; return; }
    const x0 = Math.min(u.x, b.x), y0 = Math.min(u.y, b.y);
    const x1 = Math.max(u.x + u.w, b.x + b.w), y1 = Math.max(u.y + u.h, b.y + b.h);
    u = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  });
  return u;
}

/** Grow a box to an aspect ratio and a padding factor, clamped inside the frame. */
export function fitBox(b: Box, aspect: number, pad: number, W: number, H: number): Box {
  let w = b.w * pad, h = b.h * pad;
  if (w / h < aspect) w = h * aspect; else h = w / aspect;
  w = Math.min(w, W); h = Math.min(h, H);
  const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
  const x = Math.max(0, Math.min(W - w, cx - w / 2));
  const y = Math.max(0, Math.min(H - h, cy - h / 2));
  return { x, y, w, h };
}

/** Index of a frame in a track, or -1. */
export function trackIndex(t: Track, frame: number) {
  return t.frames.indexOf(frame);
}
