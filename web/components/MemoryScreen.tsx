"use client";
import Link from "next/link";
import { useEffect } from "react";
import { experiment, lessonSource, type LessonSource } from "@/lib/campaigns";
import { useCampaign } from "@/lib/useCampaign";
import { clean } from "@/lib/words";
import { PageHeader } from "./Shell";
import { Spinner } from "./Icons";

/** "It starts with ...": what was loaded, and from where when that is one place. */
function leadLine(n: number, sources: LessonSource[]) {
  if (!n) return "Nothing learned yet. This is the first campaign.";
  const what = n === 1 ? "the lesson" : `${n} lessons`;
  if (sources.length === 1) return `It starts with ${what} it kept from ${sources[0].short}`;
  if (sources.length > 1) return `It starts with ${what} kept from earlier campaigns`;
  return `It starts with ${n} ${n === 1 ? "thing" : "things"} it already knows`;
}

/** The calm moment at the start of a campaign: what it already knows, and where it learned it. */
export function MemoryScreen({ campaignKey }: { campaignKey: string }) {
  const c = useCampaign(campaignKey);
  const ex = experiment(campaignKey);
  // Show the campaign up to the moment its memory is loaded.
  useEffect(() => {
    if (!c) return;
    const i = Array.from({ length: c.store.total }, (_, k) => c.store.eventAt(k)).findIndex((e) => e.type === "memory.loaded");
    if (i >= 0 && c.store.index <= i) c.store.showUpTo(i + 1);
  }, [c]);
  if (!c) return <div className="screen loading"><div className="center-spin"><Spinner size={22} /></div></div>;
  const { view, store } = c;
  const lessons = view.lessonsLoaded ?? [];
  const sources = [...new Map(lessons.map(lessonSource).filter((s): s is LessonSource => !!s).map((s) => [s.label, s])).values()];
  const n = lessons.length;

  return (
    <div className="memory">
      <section className="memory-text">
        <PageHeader />
        <div className={`memory-body ${n > 1 ? "is-several" : ""}`}>
          <p className="memory-kicker">New campaign</p>
          <h1 className={`memory-question ${(view.campaign?.objective.length ?? 0) > 100 ? "is-long" : ""}`}>{view.campaign?.objective}</h1>

          {sources.length > 0 && (
            <div className="carry" aria-label={`${sources.map((s) => s.label).join("; ")}, carried into this campaign`}>
              {sources.map((s) => (
                <figure key={s.label} className="carry-end">
                  <img src={s.thumb} alt="" />
                  <figcaption>{s.short}<span>{s.source}</span></figcaption>
                </figure>
              ))}
              <div className="carry-thread" aria-hidden>
                <span className="carry-line" />
                <span className="mark mark-memory carry-mark" />
                <span className="carry-line" />
              </div>
              <figure className="carry-end">
                <img src={ex.thumb} alt="" />
                <figcaption>This campaign<span>{ex.source}</span></figcaption>
              </figure>
            </div>
          )}

          <p className="memory-lead">{leadLine(n, sources)}</p>
          <ol className="lessons">
            {lessons.map((l, i) => {
              const src = lessonSource(l);
              return (
                <li key={l.id} style={{ animationDelay: `${900 + i * 900}ms` }}>
                  <span className="mark mark-memory" aria-hidden />
                  <div>
                    <p className="lesson-text">{clean(l.text)}</p>
                    <span className="lesson-origin">{src ? `${src.label}, ${src.source}` : "Learned in an earlier campaign"}</span>
                  </div>
                </li>
              );
            })}
          </ol>
          <Link className="btn memory-begin" href={`/c/${campaignKey}/`} style={{ animationDelay: `${900 + n * 900}ms` }}>
            Open the campaign
          </Link>
        </div>
      </section>
      <section className="memory-visual">
        <video src={store.media.video} poster={ex.poster || undefined} autoPlay muted loop playsInline
          className={ex.region ? "is-cropped" : undefined}
          onLoadedMetadata={(e) => { const v = e.currentTarget; if (v.duration > 20) v.currentTime = v.duration * 0.3; }} />
        {store.media.citation && <p className="memory-source">{store.media.citation}, CC BY 4.0</p>}
      </section>
    </div>
  );
}
