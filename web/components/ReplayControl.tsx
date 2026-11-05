"use client";
// The replay control for a recorded run: start over, what this is, and where the replay is, as a track the reader
// can click or drag to move through the run, with the share played beside it. Play and pause live on the
// composer's round button.
import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { IconRestart } from "./Icons";
import "./ReplayControl.css";

interface Props {
  playing: boolean;
  /** Events shown so far, and in the whole recording. */
  index: number;
  total: number;
  /** "Oct 29": the day the run was recorded, when known. */
  day?: string | null;
  /** A hover note on the label (e.g. which replies are replayed from earlier recordings). */
  note?: string;
  onRestart(): void;
  /** Move to an event index. Omitted: the track only shows position. */
  onSeek?(index: number): void;
}

export function ReplayControl({ playing, index, total, day, note, onRestart, onSeek }: Props) {
  const track = useRef<HTMLSpanElement>(null);
  const share = total ? Math.min(1, Math.max(0, index / total)) : 0;
  const done = total > 0 && index >= total;
  const at = (x: number) => {
    const r = track.current?.getBoundingClientRect();
    if (!r || !r.width) return index;
    return Math.round(Math.min(1, Math.max(0, (x - r.left) / r.width)) * total);
  };
  const down = (e: PointerEvent<HTMLSpanElement>) => {
    if (!onSeek) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    onSeek(at(e.clientX));
  };
  const move = (e: PointerEvent<HTMLSpanElement>) => {
    if (onSeek && e.currentTarget.hasPointerCapture(e.pointerId)) onSeek(at(e.clientX));
  };
  const key = (e: KeyboardEvent<HTMLSpanElement>) => {
    if (!onSeek) return;
    const stepBy = Math.max(1, Math.round(total / 20));
    const to = e.key === "ArrowRight" ? index + stepBy : e.key === "ArrowLeft" ? index - stepBy
      : e.key === "Home" ? 0 : e.key === "End" ? total : null;
    if (to == null) return;
    e.preventDefault();
    onSeek(Math.min(total, Math.max(0, to)));
  };
  return (
    <div className="rc">
      {!done && (
        <button type="button" className="rc-icon" onClick={onRestart} aria-label="Replay from the start" title="From the start">
          <IconRestart size={15} />
        </button>
      )}
      <span className="rc-label" title={note}>
        <span className="rc-state">{done ? "Replayed" : playing ? "Replaying" : "Paused"}</span>
        <span className="rc-what">recorded run{day ? ` · ${day}` : ""}</span>
      </span>
      <span ref={track} className={`rc-track ${onSeek ? "can-seek" : ""}`} role={onSeek ? "slider" : "progressbar"} tabIndex={onSeek ? 0 : -1}
        aria-label="Replay position" aria-valuemin={0} aria-valuemax={total} aria-valuenow={index}
        aria-valuetext={`${index} of ${total} events`}
        onPointerDown={down} onPointerMove={move} onKeyDown={key}>
        <span className="rc-rail"><span className="rc-fill" style={{ transform: `scaleX(${share})` }} /></span>
        <span className="rc-knob" style={{ left: `${share * 100}%` }} />
      </span>
      <span className="rc-count num" aria-hidden>{Math.round(share * 100)}%</span>
    </div>
  );
}
