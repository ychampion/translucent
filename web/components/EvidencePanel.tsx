"use client";
// The right-hand panel of a campaign, in tabs. Recording: the video, frames strip and cell voltage and, when a finding
// is opened, that finding with the video moved to its moment. The other tabs (the evidence trail, comparing runs)
// appear only when the campaign has data for them. The selected finding is the context for every tab.
import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { experiment } from "@/lib/campaigns";
import { VideoClock, useClock } from "@/lib/clock";
import type { Series } from "@/lib/contracts";
import { momentsOf, type CampaignStore, type CampaignView, type Finding, type Moment } from "@/lib/store";
import { STATE_WORD, isLong, keepUnits, momentSeconds, plainReason, timeOn } from "@/lib/words";
import { StateMark, findingHref, receiptHref } from "./Findings";
import { IconClose, IconWiden } from "./Icons";
import { SignalPanel, isSynced, sampleAt, shortSpans } from "./SignalPanel";
import { Stage } from "./Stage";
import { Timeline, type Marker } from "./Timeline";
import { TrailTab } from "./Trail";
import { CompareTab, compareTargetFor } from "./CompareScreen";

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

export interface PanelProps {
  campaignKey: string;
  store: CampaignStore;
  view: CampaignView;
  clock: VideoClock;
  finding: Finding | null;
  /** Play the video on its own (a replay that is playing). */
  autoPlay: boolean;
  /** Open a finding in the panel (from another tab). */
  onOpenFinding?(id: string): void;
}

export type TabId = "recording" | "trail" | "branches" | "compare" | "methods";

export interface PanelTab {
  id: TabId;
  label: string;
  /** Whether this campaign has anything to show in the tab; tabs without data stay hidden. */
  available(p: PanelProps): boolean;
  render(p: PanelProps): ReactNode;
}

/** Every tab the panel knows, in order. Branches and methods have no data in any bundle yet, so they stay hidden. */
export const PANEL_TABS: PanelTab[] = [
  { id: "recording", label: "Recording", available: () => true, render: (p) => <RecordingTab {...p} /> },
  { id: "trail", label: "Trail", available: (p) => p.view.findings.length > 0, render: (p) => <TrailTab store={p.store} view={p.view} finding={p.finding} campaignKey={p.campaignKey} /> },
  { id: "branches", label: "Branches", available: () => false, render: () => null },
  { id: "compare", label: "Compare", available: (p) => compareTargetFor(p.campaignKey) != null, render: (p) => <CompareTab campaignKey={p.campaignKey} campaignId={p.view.campaign?.id} /> },
  { id: "methods", label: "Methods", available: () => false, render: () => null },
];

/** Per-campaign panel layout, kept in this browser: the open tab, its width in px, and whether it is widened. */
export interface PanelLayout { tab: TabId; width: number | null; wide: boolean }
const LAYOUT_KEY = (key: string) => `translucent.panel.${key}`;
export function readLayout(key: string): PanelLayout {
  try {
    const v = JSON.parse(localStorage.getItem(LAYOUT_KEY(key)) ?? "null");
    if (v && typeof v === "object") {
      return {
        tab: PANEL_TABS.some((t) => t.id === v.tab) ? v.tab : "recording",
        width: typeof v.width === "number" && v.width >= 320 ? v.width : null,
        wide: !!v.wide,
      };
    }
  } catch { /* storage blocked or bad JSON */ }
  return { tab: "recording", width: null, wide: false };
}
export function saveLayout(key: string, l: PanelLayout) {
  try { localStorage.setItem(LAYOUT_KEY(key), JSON.stringify(l)); } catch { /* this tab only */ }
}

interface HostProps extends PanelProps {
  layout: PanelLayout;
  onLayout(l: PanelLayout): void;
  onClose(): void;
}

/** The panel frame: tabs (a segmented control on a phone), a resize edge, widen and close. */
export function EvidencePanel(props: HostProps) {
  const { layout, onLayout, onClose } = props;
  const tabs = PANEL_TABS.filter((t) => t.available(props));
  const active = tabs.find((t) => t.id === layout.tab) ?? tabs[0];
  const tabRefs = useRef(new Map<TabId, HTMLButtonElement | null>());
  const onTabKey = (e: KeyboardEvent) => {
    const i = tabs.indexOf(active);
    const j = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : null;
    if (j == null) return;
    e.preventDefault();
    const t = tabs[(j + tabs.length) % tabs.length];
    onLayout({ ...layout, tab: t.id });
    tabRefs.current.get(t.id)?.focus();
  };

  // Drag the left edge to resize; the width is kept per campaign.
  const aside = useRef<HTMLElement>(null);
  const startDrag = (e: ReactPointerEvent) => {
    const right = aside.current?.getBoundingClientRect().right ?? window.innerWidth;
    const el = e.currentTarget as HTMLElement;
    el.setPointerCapture(e.pointerId);
    let w = layout.width;
    const move = (ev: PointerEvent) => {
      w = Math.round(Math.min(window.innerWidth * 0.8, Math.max(340, right - ev.clientX)));
      onLayout({ ...layout, width: w, wide: false });
    };
    const up = () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
  };
  const nudge = (e: KeyboardEvent) => {
    const step = e.key === "ArrowLeft" ? 24 : e.key === "ArrowRight" ? -24 : 0;
    if (!step) return;
    e.preventDefault();
    const cur = aside.current?.getBoundingClientRect().width ?? 480;
    onLayout({ ...layout, width: Math.round(Math.min(window.innerWidth * 0.8, Math.max(340, cur + step))), wide: false });
  };

  return (
    <aside className="evidence" aria-label="Evidence" ref={aside}>
      <div className="panel-edge" role="separator" aria-orientation="vertical" aria-label="Resize the panel" tabIndex={0}
        onPointerDown={startDrag} onKeyDown={nudge} onDoubleClick={() => onLayout({ ...layout, width: null, wide: false })} />
      <div className="evidence-head">
        {tabs.length > 1 ? (
          <div className="panel-tabs" role="tablist" aria-label="Panel" onKeyDown={onTabKey}>
            {tabs.map((t) => (
              <button key={t.id} ref={(el) => { tabRefs.current.set(t.id, el); }} type="button" role="tab" id={`tab-${t.id}`}
                aria-selected={t === active} aria-controls="panel-body" tabIndex={t === active ? 0 : -1}
                className={`panel-tab ${t === active ? "is-on" : ""}`} onClick={() => onLayout({ ...layout, tab: t.id })}>
                {t.label}
              </button>
            ))}
          </div>
        ) : <h2>{props.finding ? "Evidence" : active.label}</h2>}
        <span className="evidence-tools">
          <button className={`icon-btn only-wide ${layout.wide ? "is-on" : ""}`} onClick={() => onLayout({ ...layout, wide: !layout.wide })}
            aria-label={layout.wide ? "Narrow the panel" : "Widen the panel"} aria-pressed={layout.wide} title={layout.wide ? "Narrow" : "Widen"}>
            <IconWiden />
          </button>
          <button className="icon-btn" onClick={onClose} aria-label="Close the panel"><IconClose /></button>
        </span>
      </div>
      <div className="evidence-body" id="panel-body" role={tabs.length > 1 ? "tabpanel" : undefined} aria-labelledby={tabs.length > 1 ? `tab-${active.id}` : undefined}>
        {active.render(props)}
      </div>
    </aside>
  );
}

/** The recording: video, timeline and voltage, with the opened finding on top. */
function RecordingTab({ campaignKey, store, view, clock, finding, autoPlay }: PanelProps) {
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
    <>
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
    </>
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
