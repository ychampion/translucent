"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { experiment } from "@/lib/campaigns";
import { VideoClock } from "@/lib/clock";
import { findingUrl } from "@/lib/share";
import { STATE_WORD, checkLabel, groupChecks, keepUnits, plainReason } from "@/lib/words";
import { CheckGlyph, StateMark, findingHref } from "./Findings";
import { LoadState } from "./Status";
import { Know, findingEvidence, useFindingPage } from "./FindingScreen";
import { FrameStrip } from "./FrameStrip";
import { QrCode } from "./QrCode";
import { SignalPanel, isSynced } from "./SignalPanel";
import { Wordmark } from "./TopBar";
import { ReceiptPeople } from "./Team";
import "./Receipt.css";

/** At most `n` frames, evenly spread, always keeping the first and last. */
function spread(frames: number[], n: number) {
  if (frames.length <= n) return frames;
  return Array.from({ length: n }, (_, i) => frames[Math.round((i * (frames.length - 1)) / (n - 1))]);
}

/** One printable page for one finding: what it says, its state, the evidence, the checks, the paper, the re-check. */
export function ReceiptScreen({ campaignKey }: { campaignKey: string }) {
  const { c, findingId, error, retry } = useFindingPage(campaignKey);
  const ex = experiment(campaignKey);
  const still = useMemo(() => new VideoClock(), []);
  const [url, setUrl] = useState("");
  useEffect(() => { if (findingId) setUrl(findingUrl(campaignKey, findingId)); }, [campaignKey, findingId]);

  if (!c || findingId == null) return <div className="screen loading"><LoadState error={error} onRetry={retry} what="this receipt" /></div>;
  const { view, store } = c;
  const f = view.findings.find((x) => x.claim.id === findingId);
  if (!f) {
    return (
      <div className="screen receipt-screen">
        <main className="receipt paper"><p className="soft">This finding hasn&rsquo;t been put to the check yet. <Link className="text-link" href={`/c/${campaignKey}/`}>Back to the campaign</Link></p></main>
      </div>
    );
  }
  const media = store.media;
  const checks = groupChecks(f.review?.checks ?? []);
  const passed = checks.filter((x) => x.passed).length;
  const why = f.review ? plainReason(f.review, f.claim) : f.failed ? "The check couldn’t run." : null;
  const { series, mine, traceMoments, frames, caption } = findingEvidence(view, store, f);
  const shown = spread(frames, 5);

  return (
    <div className={`screen receipt-screen state-${f.state}`}>
      <div className="receipt-actions">
        <Link className="btn btn-quiet" href={findingHref(campaignKey, f.claim.id)}>Back to the finding</Link>
        <button type="button" className="btn" onClick={() => window.print()}>Print</button>
      </div>

      <main className="receipt paper">
        <header className="receipt-top">
          <Wordmark />
          <span className="soft">Receipt</span>
        </header>

        <div className="receipt-head">
          <div className="receipt-claim">
            {view.campaign?.objective && <p className="receipt-asked">{view.campaign.objective}</p>}
            <div className="detail-state">
              <StateMark state={f.state} />
              <span className="finding-state">{STATE_WORD[f.state]}</span>
            </div>
            <h1 className="receipt-statement">{keepUnits(f.claim.statement)}</h1>
            {why && <p className="receipt-why">{why}</p>}
            <ReceiptPeople f={f} />
          </div>
          {url && (
            <figure className="receipt-qr">
              <QrCode text={url} size={112} />
              <figcaption>Scan to open this finding</figcaption>
            </figure>
          )}
        </div>

        <section className="receipt-evidence">
          {mine.length > 0 && isSynced(series) && (
            <div className="receipt-signal">
              <SignalPanel clock={still} series={series} moments={traceMoments} still focus={f.claim.id} seekable={false} reference={ex.reference} />
            </div>
          )}
          {shown.length > 0 && (
            <FrameStrip
              src={media.video}
              fps={media.fps}
              size={media.videoSize}
              frames={shown}
              track={ex.region ? undefined : media.tracks[0]}
              region={ex.region}
              caption={caption}
              highlight={mine.length ? 1 : undefined}
              className="frames-tight"
            />
          )}
        </section>

        {checks.length > 0 && (
          <section>
            <h2 className="detail-h">{passed} of {checks.length} checks passed</h2>
            <ul className="receipt-checks">
              {checks.map((x) => (
                <li key={x.name} className={x.passed ? "pass" : "fail"}>
                  <CheckGlyph ok={x.passed} />
                  <span>{checkLabel(x.name)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <Know store={store} view={view} f={f} campaignKey={campaignKey} frames={frames} compact />

        {media.citation && <p className="receipt-source">{media.citation}</p>}

        <footer className="receipt-foot">
          {ex.verified && (
            <p>Re-check it yourself, offline: <code>{ex.verified.command}</code></p>
          )}
          {url && <p className="receipt-url">{url}</p>}
        </footer>
      </main>
    </div>
  );
}
