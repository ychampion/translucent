"use client";
// The right-hand panel of a campaign: the recording (video, frames strip, cell voltage) and, when a finding is
// opened, that finding with the video moved to its moment and a link to its receipt.
import Link from "next/link";
import { useEffect, useMemo, useRef } from "react";
import { experiment } from "@/lib/campaigns";
import { VideoClock, useClock } from "@/lib/clock";
import type { Series } from "@/lib/contracts";
import { momentsOf, type CampaignStore, type CampaignView, type Finding, type Moment } from "@/lib/store";
import { STATE_WORD, isLong, keepUnits, momentSeconds, plainReason, timeOn } from "@/lib/words";
import { StateMark, findingHref, receiptHref } from "./Findings";
import { IconClose } from "./Icons";
import { SignalPanel, isSynced, sampleAt, shortSpans } from "./SignalPanel";
import { Stage } from "./Stage";
import { Timeline, type Marker } from "./Timeline";

const TONE = { earned: "gold", checking: "steel", "not-proven": "grey", refused: "crimson" } as const;

/** First video frame at or after a moment on the recording's clock. */
export function frameAtTime(t: number, frameTimes: number[] | null, series?: Series): number | null {
  if (frameTimes?.length) {
    const i = frameTimes.findIndex((x) => x >= t);
    return i < 0 ? frameTimes.length - 1 : i;
  }
  if (series && isSynced(series)) {
    const i = series.t.findIndex((x) => x >= t);
    return series.frame_of_t[i < 0 ? series.t.length - 1 : i];
  }
  return null;
}

/** "Reisecker et al., Nat. Commun. 14, 2432 (2023), Supplementary Movie 4" -> "Reisecker et al. 2023, Movie 4". */
export function shortCitation(c: string) {
  const m = c.match(/^([^,]+?et al\.)[^()]*\((\d{4})\)(?:,\s*Supplementary\s*(Movie \d+))?/);
  return m ? `${m[1]} ${m[2]}${m[3] ? `, ${m[3]}` : ""} · CC BY 4.0` : c;
}

/** The frame a finding is about: its first cited frame, else the moment its measurement marks. */
export function findingFrame(f: Finding, store: CampaignStore): number | null {
  if (f.frames.length) return f.frames[0];
  const media = store.media;
  const series = media.series.find((s) => /volt/i.test(s.name)) ?? media.series[0];
  for (const id of f.claim.predicate.measurement_ids) {
    const t = momentSeconds(store.catalog.measurements.get(id));
    if (t != null) return frameAtTime(t, media.frameTimes, series);
  }
  return null;
}

interface Props {
  campaignKey: string;
  store: CampaignStore;
  view: CampaignView;
  clock: VideoClock;
  finding: Finding | null;
  onClose(): void;
  /** Play the video on its own (a replay that is playing). */
  autoPlay: boolean;
}

export function EvidencePanel({ campaignKey, store, view, clock, finding, onClose, autoPlay }: Props) {
  const ex = experiment(campaignKey);
  const media = store.media;
  const series = media.series.find((s) => /volt/i.test(s.name)) ?? media.series[0];
  const long = !!series && isLong(series.t[series.t.length - 1] - series.t[0]);
  const synced = isSynced(series);

  // The shared trace shows moments being checked or supported; a not-proven moment has no place to stand on it.
  const moments = useMemo(
    () => momentsOf(view.findings, store.catalog).filter((m) => m.state !== "not-proven"),
    [view.findings, store.catalog],
  );
  const markers = useMemo<Marker[]>(() => {
    const out: Marker[] = view.findings
      .filter((f) => f.state === "earned" && f.claim.predicate.type === "exists" && typeof f.claim.predicate.params.frame === "number")
      .map((f) => ({ frame: f.claim.predicate.params.frame as number, tone: "gold", label: f.claim.statement }));
    for (const m of moments) {
      const fr = frameAtTime(m.t, media.frameTimes, series);
      if (fr != null && m.state !== "refused") out.push({ frame: fr, tone: TONE[m.state], label: `${m.label}, ${long ? timeOn(m.t, true) : `${Math.round(m.t)} s`}` });
    }
    return out;
  }, [view.findings, moments, media, series, long]);
  const tracked = useMemo<[number, number] | null>(() => {
    if (ex.region) return null; // whole-movie outlines: the strip shows images, not a tracked span
    const t = media.tracks.find((x) => view.visibleTracks.includes(x.subject));
    return t ? [t.frames[0], t.frames[t.frames.length - 1]] : null;
  }, [media, view.visibleTracks, ex.region]);

  // Opening a finding moves the video to its moment and holds it there.
  const shownFor = useRef<string | null>(null);
  useEffect(() => {
    if (!finding || shownFor.current === finding.claim.id) return;
    shownFor.current = finding.claim.id;
    const fr = findingFrame(finding, store);
    if (fr == null) return;
    const go = () => { clock.pause(); clock.seek(fr); };
    if (clock.video?.readyState) go(); else setTimeout(go, 500);
  }, [finding, store, clock]);
  useEffect(() => { if (!finding) shownFor.current = null; }, [finding]);

  const why = finding ? (finding.review ? plainReason(finding.review, finding.claim) : finding.failed ? "The check couldn’t run." : null) : null;

  return (
    <aside className="evidence" aria-label="Evidence">
      <div className="evidence-head">
        <h2>{finding ? "Evidence" : "Recording"}</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Close the panel"><IconClose /></button>
      </div>
      <div className="evidence-body">
        {finding && (
          <section className={`evidence-finding state-${finding.state}`}>
            <p className="fcard-head"><StateMark state={finding.state} /><span className="fcard-state">{STATE_WORD[finding.state]}</span></p>
            <p className="evidence-statement">{keepUnits(finding.claim.statement)}</p>
            {why && <p className="evidence-why">{why}</p>}
            <p className="evidence-links">
              <Link className="pill-link" href={receiptHref(campaignKey, finding.claim.id)}>Receipt</Link>
              <Link className="pill-link" href={findingHref(campaignKey, finding.claim.id)}>All the checks</Link>
            </p>
          </section>
        )}
        <div className="evidence-media" style={{ ["--ar" as string]: ex.region ? ex.region.w / ex.region.h : media.videoSize[0] / media.videoSize[1] }}>
          <Stage
            clock={clock}
            src={media.video}
            poster={ex.poster || undefined}
            size={media.videoSize}
            fps={media.fps}
            tracks={media.tracks}
            visible={view.visibleTracks}
            region={ex.region}
            closeUp={view.visibleTracks.length > 0 && !ex.region}
            autoPlay={autoPlay && !finding && (!media.tracks.length || !!ex.region)}
            label={media.citation ? shortCitation(media.citation) : undefined}
            credit="Outlines: SAM 3.1"
            distinct={view.distinctFrames}
            chip={synced ? <StateChip clock={clock} series={series!} moments={moments} /> : null}
          />
          <Timeline clock={clock} distinct={view.distinctFrames} tracked={tracked} markers={markers} />
          <SignalPanel clock={clock} series={series} moments={moments} reference={ex.reference} focus={finding?.claim.id} />
        </div>
      </div>
    </aside>
  );
}

/**
 * A quiet state over the footage, from supported moments only: "Battery shorted" inside a supported short, or
 * "Voltage below 0 V" once the playhead is past a supported first drop below 0 V while the voltage is below zero.
 */
function StateChip({ clock, series, moments }: { clock: VideoClock; series: Series; moments: Moment[] }) {
  const { frame } = useClock(clock);
  if (!series.frame_of_t) return null;
  const spans = shortSpans(moments);
  const drop = moments.find((m) => m.state === "earned" && m.kind === "below-zero");
  if (!spans.length && !drop) return null;
  const i = sampleAt(series.frame_of_t, frame);
  const t = series.t[i];
  const on = spans.length ? spans.some(([a, b]) => t >= a && t < b) : !!drop && t >= drop.t && series.y[i] < 0;
  const text = spans.length ? "Battery shorted" : "Voltage below 0 V";
  return <div className={`stage-chip ${on ? "is-on" : ""}`} aria-live="polite"><span className="chip-dot" aria-hidden />{text}</div>;
}
