"use client";
import { useSyncExternalStore } from "react";

/** Shared playback position for one video: the stage writes it, the timeline and signal panel read it. */
export class VideoClock {
  frame = 0;
  frames = 0;
  playing = false;
  video: HTMLVideoElement | null = null;
  fps = 25;
  private ls = new Set<() => void>();
  private snap = { frame: 0, frames: 0, playing: false };

  subscribe = (l: () => void) => { this.ls.add(l); return () => { this.ls.delete(l); }; };
  get = () => this.snap;

  set(frame: number, frames = this.frames, playing = this.playing) {
    if (frame === this.frame && frames === this.frames && playing === this.playing) return;
    this.frame = frame; this.frames = frames; this.playing = playing;
    this.snap = { frame, frames, playing };
    this.ls.forEach((l) => l());
  }

  seek(frame: number) {
    const v = this.video;
    const f = Math.max(0, Math.min(this.frames - 1, Math.round(frame)));
    if (v) v.currentTime = (f + 0.5) / this.fps;
    this.set(f);
  }

  play() { void this.video?.play().catch(() => {}); }
  pause() { this.video?.pause(); }
  toggle() { if (this.video?.paused) this.play(); else this.pause(); }
}

export function useClock(c: VideoClock) {
  return useSyncExternalStore(c.subscribe, c.get, c.get);
}
