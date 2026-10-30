"use client";
import Link from "next/link";
import { useEffect, useMemo } from "react";
import { experiment } from "@/lib/campaigns";
import { VideoClock } from "@/lib/clock";
import { momentsOf } from "@/lib/store";
import { useCampaignLoad } from "@/lib/useCampaign";
import {
  SECOND_WORD, answerParts, clean, disagreedLine, framesWord, isLong, keepUnits, modelName, plainReason, timeOn,
} from "@/lib/words";
import { frameAtTime, shortCitation } from "./EvidencePanel";
import { CheckYourself } from "./CheckYourself";
import { OnsetCompare } from "./OnsetCompare";
import { StateMark, findingHref, measurementsOf, paperFor } from "./Findings";
import { FrameStrip } from "./FrameStrip";
import { SignalPanel, isSynced } from "./SignalPanel";
import { PageHeader } from "./Shell";
import { LoadState } from "./Status";

export function SummaryScreen({ campaignKey }: { campaignKey: string }) {
  const { c, error, retry } = useCampaignLoad(campaignKey);
  const ex = experiment(campaignKey);
  const still = useMemo(() => new VideoClock(), []);
  useEffect(() => {
    if (c && c.store.index < c.store.total && !c.view.finished) c.store.showUpTo(c.store.total);
  }, [c]);
  if (!c) return <div className="screen loading"><PageHeader /><LoadState error={error} onRetry={retry} /></div>;
  const { view, store } = c;
  const media = store.media;
  const earned = view.findings.filter((f) => f.state === "earned");
  const open = view.findings.filter((f) => f.state === "not-proven");
  const refused = view.findings.filter((f) => f.state === "refused");
  const track = media.tracks[0];
  const answer = answerParts(view.answer);

  // Voltage story: the earned moments, with the captured image at each.
  const series = media.series.find((s) => /volt/i.test(s.name)) ?? media.series[0];
  const moments = momentsOf(view.findings, store.catalog).filter((m) => m.state === "earned");
  const momentFrames = moments.map((m) => frameAtTime(m.t, media.frameTimes, series)).filter((x): x is number => x != null);
  const long = !!series && isLong(series.t[series.t.length - 1] - series.t[0]);
  const momentWord = (i: number) => {
    const m = moments[i];
    const again = moments.slice(0, i).some((x) => x.kind === m.kind);
    const what = m.kind === "short" ? (again ? "Shorts again" : "Shorts") : m.kind === "recovery" ? "Recovers" : m.label;
    return `${what}, ${timeOn(m.t, long)}`;
  };
  // Measured beside stated: the earned first drop below 0 V against the source's stated onset.
  const drop = ex.reference ? moments.find((m) => m.kind === "below-zero") : undefined;
  const dropMeasurement = drop && store.catalog.claims.get(drop.findingId)?.predicate.measurement_ids
    .map((id) => store.catalog.measurements.get(id)).find(Boolean);
  // The growth, one tile per captured image, from the finding that was timed on distinct images.
  const stepFrames = earned.find((f) => f.frames.length >= 4)?.frames ?? earned[0]?.frames ?? [];
  // Credit SAM only when one of its steps returned outlines.
  const usedSam = view.events.some((e) => /segment|track|sam/i.test(e.actor) && e.type === "tool.finished"
    && (e.data?.status == null || e.data.status === "ok"));
  const critic = view.findings.find((f) => f.second)?.second;
  // The paper's window beside each earned moment, and one line when the measured times fall inside them.
  const paperOf = (f: (typeof earned)[number]) =>
    measurementsOf(f, store.catalog).map((m) => paperFor(ex, m)).find((p) => p != null) ?? null;
  const papers = earned.map(paperOf).filter((p): p is NonNullable<typeof p> => p != null);
  const matched = papers.filter((p) => p.overlaps).length;

  return (
    <div className="screen summary">
      <PageHeader title="The answer">
        <Link className="btn btn-quiet" href={`/c/${campaignKey}/?at=end`}>Back to the campaign</Link>
      </PageHeader>
      <main className="summary-grid">
        <section className="summary-text">
          <p className="summary-asked">{view.campaign?.objective}</p>
          <p className={`summary-answer ${(answer?.lead.length ?? 0) > 150 ? "is-long" : ""}`}>{answer ? keepUnits(answer.lead) : "The campaign hasn’t finished yet."}</p>
          <ul className="summary-list is-compact">
            {earned.map((f) => (
              <li key={f.claim.id}>
                <Link href={findingHref(campaignKey, f.claim.id)} className="summary-item">
                  <StateMark state="earned" />
                  <span>
                    <span className="summary-statement">{keepUnits(f.claim.statement)}</span>
                    {f.frames.length > 0 && <span className="summary-meta num">{framesWord(f.frames)}</span>}
                    {paperOf(f) && <span className="summary-meta num">{paperOf(f)!.text}</span>}
                    {f.second && SECOND_WORD[f.second.verdict] === "disagrees" && (
                      <span className="summary-meta">{disagreedLine(measurementsOf(f, store.catalog))}</span>
                    )}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          {matched > 0 && ex.paper && (
            <p className="summary-paper">
              {matched === papers.length ? "Matches" : `${matched} of ${papers.length} times match`} {ex.paper.source}
            </p>
          )}

          {(open.length > 0 || refused.length > 0 || view.notProven) && (
            <div className="summary-open">
              <h2 className="detail-h">What it couldn&rsquo;t prove</h2>
              {view.notProven && (
                <div className="summary-item">
                  <StateMark state="not-proven" />
                  <span className="summary-statement is-small">{clean(view.notProven)}</span>
                </div>
              )}
              {[...open.slice(0, 1), ...refused.slice(0, view.notProven ? 1 : 2)].map((f) => (
                <Link key={f.claim.id} href={findingHref(campaignKey, f.claim.id)} className="summary-item">
                  <StateMark state={f.state} />
                  <span>
                    <span className="summary-statement is-small">{keepUnits(f.claim.statement)}</span>
                    <span className="summary-why">{plainReason(f.review, f.claim)}</span>
                  </span>
                </Link>
              ))}
            </div>
          )}
        </section>

        <section className="summary-visual">
          {moments.length > 0 && isSynced(series) && drop && ex.reference && (
            <>
              <SignalPanel clock={still} series={series} moments={moments} still seekable={false} reference={ex.reference} />
              <OnsetCompare media={media} region={ex.region} moment={drop} measurement={dropMeasurement}
                reference={ex.reference} note={ex.referenceNote} />
            </>
          )}
          {moments.length > 0 && isSynced(series) && !drop && (
            <>
              <SignalPanel clock={still} series={series} moments={moments} still seekable={false} reference={ex.reference} />
              <FrameStrip
                src={media.video}
                fps={media.fps}
                size={media.videoSize}
                frames={momentFrames}
                region={ex.region}
                columns={momentFrames.length}
                caption={(_, i) => momentWord(i)}
                className="frames-tight"
              />
            </>
          )}
          {!moments.length && stepFrames.length > 0 && (
            <>
              <FrameStrip
                src={media.video}
                fps={media.fps}
                size={media.videoSize}
                frames={[stepFrames[stepFrames.length - 1]]}
                track={track}
                aspect={16 / 8.5}
                columns={1}
                caption={(f) => `Frame ${f}, the last captured image tracked`}
              />
              <FrameStrip
                src={media.video}
                fps={media.fps}
                size={media.videoSize}
                frames={stepFrames}
                track={track}
                aspect={1}
                columns={stepFrames.length}
                caption={(f, i) => `Image ${i + 1}, frame ${f}`}
                className="frames-tight"
              />
            </>
          )}
          <div className="summary-tally num">
            <span><StateMark state="earned" /> {earned.length} supported</span>
            {refused.length > 0 && <span><StateMark state="refused" /> {refused.length} turned down</span>}
            {open.length > 0 && <span><StateMark state="not-proven" /> {open.length} not proven yet</span>}
          </div>

          {view.lessonsLearned.length > 0 && (
            <div className="summary-lessons">
              <h2 className="detail-h">Kept for the next campaign</h2>
              <ul>
                {view.lessonsLearned.map((l) => (
                  <li key={l.id}><span className="mark mark-memory" aria-hidden /> {clean(l.text)}</li>
                ))}
              </ul>
            </div>
          )}

          {ex.verified && <CheckYourself v={ex.verified} />}

          <p className="credits">
            {media.citation && <span>Footage: {shortCitation(media.citation)}</span>}
            {usedSam && <span>Outlines: Meta SAM 3.1</span>}
            {critic && <span>Second opinions: {modelName(critic.model)}</span>}
          </p>
        </section>
      </main>
    </div>
  );
}
