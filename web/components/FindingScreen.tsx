"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { experiment } from "@/lib/campaigns";
import { VideoClock } from "@/lib/clock";
import type { Measurement } from "@/lib/contracts";
import { momentKind, momentLabel, momentsOf, type CampaignStore, type CampaignView, type Finding, type Moment } from "@/lib/store";
import { connectLive, liveIdFor } from "@/lib/sources";
import { useCampaign } from "@/lib/useCampaign";
import {
  SECOND_WORD, STATE_WORD, checkDetail, checkLabel, disagreedLine, framesWord, keepUnits, groupChecks, isLong, modelName, momentSeconds, plainLabel,
  plainReason, subjectWord, timeOn, uncertaintyWord, valueWord,
} from "@/lib/words";
import { frameAtTime, shortCitation } from "./EvidencePanel";
import { CheckGlyph, StateMark, findingHref, paperFor } from "./Findings";
import { FrameStrip } from "./FrameStrip";
import { Share } from "./Share";
import { Spinner } from "./Icons";
import { SignalPanel, isSynced } from "./SignalPanel";
import { PageHeader } from "./Shell";

/** Label for a number the runtime has not produced yet (the measurement is referenced but absent). */
function missingLabel(id: string) {
  if (/speed|velocity/.test(id)) return "Growth speed";
  return id.replace(/^m-/, "").replace(/[-_]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** The finding a page is about (?id=), and its campaign shown as finished when the page is opened directly. */
export function useFindingPage(campaignKey: string) {
  const c = useCampaign(campaignKey);
  const [findingId, setFindingId] = useState<string | null>(null);
  useEffect(() => {
    const read = () => setFindingId(new URLSearchParams(window.location.search).get("id"));
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);
  // Opened directly (not from the campaign): show the campaign as finished. A live campaign follows its stream.
  const store = c?.store;
  useEffect(() => {
    if (!store) return;
    const liveId = liveIdFor(campaignKey);
    if (liveId) return connectLive(liveId, store, (e) => console.warn("Live stream error", e));
    if (store.index === 0 && store.total > 0) store.showUpTo(store.total);
  }, [store, campaignKey]);
  return { c, findingId };
}

/**
 * What a finding stands on: the frames it cites; or, for a moment on the voltage log, the images either side of that
 * moment, and the trace with its moments.
 */
export function findingEvidence(view: CampaignView, store: CampaignStore, f: Finding) {
  const media = store.media;
  const ids = f.claim.predicate.measurement_ids;
  const series = media.series.find((s) => /volt/i.test(s.name)) ?? media.series[0];
  const moments = momentsOf(view.findings, store.catalog);
  // This finding's own moments, in its own state (refused findings are not on the shared trace).
  const mine: Moment[] = ids.flatMap((id) => {
    const m = store.catalog.measurements.get(id);
    const t = momentSeconds(m);
    if (t == null || !m) return [];
    return [{ t, label: momentLabel(m), state: f.state, findingId: f.claim.id, kind: momentKind(m) }];
  });
  const long = !!series && isLong(series.t[series.t.length - 1] - series.t[0]);
  // Its own moments, in the best state any finding gave them (a refused reading of an earned moment keeps the moment
  // gold); other findings' moments only when checked or earned.
  const ownTimes = new Set(mine.map((x) => x.t));
  const traceMoments = [
    ...moments.filter((m) => !ownTimes.has(m.t) && m.state !== "not-proven"),
    ...mine.map((x) => {
      const shared = moments.find((m) => m.t === x.t);
      return shared && shared.state === "earned" ? { ...x, state: shared.state } : x;
    }),
  ];
  let frames = f.frames;
  let caption: ((fr: number, i: number) => string) | undefined;
  if (!frames.length && mine.length) {
    const at = frameAtTime(mine[0].t, media.frameTimes, series);
    const d = view.distinctFrames;
    if (at != null) {
      const g = d ? d.findIndex((x, i) => x <= at && (d[i + 1] ?? Infinity) > at) : -1;
      const pick = d && g >= 0 ? [d[Math.max(0, g - 2)], d[g], d[Math.min(d.length - 1, g + 2)]] : [Math.max(0, at - 20), at, at + 20];
      frames = [...new Set(pick)];
      const tOf = (fr: number) => media.frameTimes?.[fr] ?? series?.t[fr];
      caption = (fr) => { const t = tOf(fr); return t == null ? `Frame ${fr}` : `${timeOn(t, long)} into the recording`; };
    }
  }
  return { series, mine, traceMoments, frames, caption };
}

export function FindingScreen({ campaignKey }: { campaignKey: string }) {
  const { c, findingId } = useFindingPage(campaignKey);
  const ex = experiment(campaignKey);
  const still = useMemo(() => new VideoClock(), []);

  if (!c || findingId == null) return <div className="screen loading"><div className="center-spin"><Spinner size={22} /></div></div>;
  const { view, store } = c;
  const f = view.findings.find((x) => x.claim.id === findingId);
  if (!f) {
    return (
      <div className="screen">
        <PageHeader title={view.campaign?.title} />
        <main className="detail"><p className="soft">This finding hasn&rsquo;t been put to the check yet. <Link className="text-link" href={`/c/${campaignKey}/`}>Back to the campaign</Link></p></main>
      </div>
    );
  }
  const media = store.media;
  const subjects = media.tracks.map((t) => t.subject);
  const checks = groupChecks(f.review?.checks ?? []);
  const passed = checks.filter((x) => x.passed).length;
  const ids = f.claim.predicate.measurement_ids;
  const rows: { id: string; label: string; m?: Measurement }[] = ids.map((id) => {
    const m = store.catalog.measurements.get(id);
    return { id, label: plainLabel(subjectWord(m?.label ?? missingLabel(id), subjects)), m };
  });
  const why = f.review ? plainReason(f.review, f.claim) : f.failed ? "The check couldn’t run." : null;
  const replaces = f.claim.supersedes ? view.findings.find((x) => x.claim.id === f.claim.supersedes) : undefined;
  const replacedBy = f.replacedBy ? view.findings.find((x) => x.claim.id === f.replacedBy) : undefined;
  const repeatedOneImage = f.frames.length >= 2 && f.frames.every((x, i) => i === 0 || x === f.frames[i - 1] + 1) && checks.some((x) => x.name === "distinct_frames" && !x.passed);

  const { series, mine, traceMoments, frames, caption } = findingEvidence(view, store, f);

  return (
    <div className={`screen detail-screen state-${f.state}`}>
      <PageHeader title={view.campaign?.title}>
        <Share campaignKey={campaignKey} findingId={f.claim.id} />
        <Link className="btn btn-quiet" href={`/c/${campaignKey}/?at=end`}>Back to the campaign</Link>
      </PageHeader>
      <main className="detail">
        <section className="detail-text">
          <div className="detail-state">
            <StateMark state={f.state} />
            <span className="finding-state">{STATE_WORD[f.state]}</span>
            {checks.length > 0 && <span className="soft">{passed} of {checks.length} checks passed</span>}
          </div>
          <h1 className="detail-statement">{keepUnits(f.claim.statement)}</h1>
          {why && <p className="detail-why">{why}</p>}

          {rows.length > 0 && (
            <>
              <h2 className="detail-h">What was measured</h2>
              <dl className="numbers">
                {rows.map(({ id, label, m }) => (
                  <div key={id} className="number-row">
                    <dt>{label}</dt>
                    <dd>
                      <span className="num number-value">{valueWord(m)}</span>
                      <span className="number-note">{uncertaintyWord(m)}</span>
                      {paperFor(ex, m) && <span className="number-note num">{paperFor(ex, m)!.text}</span>}
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          )}

          {checks.length > 0 && (
            <>
              <h2 className="detail-h">Checks</h2>
              <ul className="check-rows">
                {checks.map((x) => (
                  <li key={x.name} className={x.passed ? "pass" : "fail"}>
                    <CheckGlyph ok={x.passed} />
                    <span className="check-name">{checkLabel(x.name)}</span>
                    <span className="check-detail">{checkDetail(x.detail, subjects)}</span>
                  </li>
                ))}
              </ul>
            </>
          )}

          {f.second && (f.state === "earned" && SECOND_WORD[f.second.verdict] === "disagrees" ? (
            <p className="second-opinion" title={f.second.reason}>{disagreedLine(rows.map((r) => r.m))}</p>
          ) : (
            <p className="second-opinion" title={f.second.reason}>
              Second opinion from {modelName(f.second.model)}: {SECOND_WORD[f.second.verdict]}. It doesn&rsquo;t change the verdict.
            </p>
          ))}

          {(replaces || replacedBy) && (
            <div className="history">
              {replaces && (
                <Link href={findingHref(campaignKey, replaces.claim.id)} className="history-link">
                  <StateMark state={replaces.state} /> Replaces a refused finding: {replaces.claim.statement}
                </Link>
              )}
              {replacedBy && (
                <Link href={findingHref(campaignKey, replacedBy.claim.id)} className="history-link">
                  <StateMark state={replacedBy.state} /> Tested again and {STATE_WORD[replacedBy.state].toLowerCase()}: {replacedBy.claim.statement}
                </Link>
              )}
            </div>
          )}
        </section>

        <section className="detail-evidence">
          <div className="detail-evidence-head">
            <h2 className="detail-h">Evidence</h2>
            {f.frames.length > 0 && <span className="soft num">{framesWord(f.frames)}</span>}
          </div>
          {mine.length > 0 && isSynced(series) && (
            <div className="detail-signal">
              <SignalPanel clock={still} series={series} moments={traceMoments} still focus={f.claim.id} seekable={false} reference={ex.reference} />
            </div>
          )}
          {frames.length > 0 ? (
            <FrameStrip
              src={media.video}
              fps={media.fps}
              size={media.videoSize}
              frames={frames}
              track={ex.region ? undefined : media.tracks[0]}
              region={ex.region}
              columns={repeatedOneImage ? 5 : undefined}
              caption={caption}
              highlight={mine.length ? 1 : undefined}
            />
          ) : (
            mine.length === 0 && <p className="soft">No frames cited.</p>
          )}
          {repeatedOneImage && <p className="evidence-note">These {f.frames.length} frames are the same captured image. Timing them as {f.frames.length} moments makes the speed wrong.</p>}
          {media.citation && <p className="evidence-source">{shortCitation(media.citation)}</p>}
        </section>
      </main>
    </div>
  );
}
