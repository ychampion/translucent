"use client";
import type { Region } from "@/lib/campaigns";
import type { Measurement } from "@/lib/contracts";
import type { Media, Moment } from "@/lib/store";
import { timeOn, unitWord } from "@/lib/words";
import { frameAtTime } from "./EvidencePanel";
import { FrameStrip } from "./FrameStrip";

interface Props {
  media: Media;
  region?: Region;
  /** The earned moment, on the recording's clock. */
  moment: Moment;
  /** The measurement behind it, for its uncertainty. */
  measurement?: Measurement;
  reference: { t: number; label: string; source: string };
  note?: string;
}

const ASPECT = 1.7;

const fmtBound =(m?: Measurement) => {
  const u = m?.uncertainty.value;
  return u == null ? null : `give or take ${+u.toPrecision(1)} ${unitWord(m!.unit)}`;
};

/** What was measured beside what the source states, each with the image at that moment. A calm comparison. */
export function OnsetCompare({ media, region, moment, measurement, reference, note }: Props) {
  const series = media.series.find((s) => /volt/i.test(s.name)) ?? media.series[0];
  const fm = frameAtTime(moment.t, media.frameTimes, series);
  const fr = frameAtTime(reference.t, media.frameTimes, series);
  const bound = fmtBound(measurement);
  // Tiles are narrower than the stage: take the left part of the region, at its full height, so the crop never grows
  // past the region (Lu's lower right holds the paper's own plot).
  const crop = region && { ...region, w: Math.min(region.w, Math.round(region.h * ASPECT)) };
  const tile = (frame: number | null) =>
    frame == null ? <div className="compare-blank" /> : (
      <FrameStrip src={media.video} fps={media.fps} size={media.videoSize} frames={[frame]} region={crop} aspect={ASPECT}
        columns={1} caption={() => ""} className="compare-frame" />
    );
  return (
    <section className="compare" aria-label="Measured and stated onset">
      <div className="compare-cell is-measured">
        {tile(fm)}
        <p className="compare-value num">{timeOn(moment.t, true)}</p>
        <p className="compare-label">{moment.kind === "below-zero" ? "Voltage first below 0 V" : moment.label}</p>
        <p className="compare-meta">From the voltage log{bound ? `, ${bound}` : ""}</p>
      </div>
      <div className="compare-cell is-stated">
        {tile(fr)}
        <p className="compare-value num">{timeOn(reference.t, true)}</p>
        <p className="compare-label">{reference.label}</p>
        <p className="compare-meta">As stated by {reference.source}</p>
      </div>
      {note && <p className="compare-note">{note}</p>}
    </section>
  );
}
