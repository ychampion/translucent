"use client";
import { useEffect, useState } from "react";
import { EXPERIMENTS } from "@/lib/campaigns";
import { FAULTS, SECOND_OPINION } from "@/lib/proof";
import { loadBundle } from "@/lib/sources";
import { STOPPED_S, money } from "@/lib/words";
import { CheckYourself } from "./CheckYourself";
import { PageHeader } from "./Shell";
import { TryToFool } from "./TryToFool";

/**
 * Cost and running time of the hero campaign, summed from its own recorded events. A runtime resume grants a fresh
 * budget on top of what was spent (as in the store); time while it was stopped is left out.
 */
function useHeroCost() {
  const [cost, setCost] = useState<{ usd: number; budget: number; minutes: number } | null>(null);
  useEffect(() => {
    let live = true;
    loadBundle("m4").then((b) => {
      let usd = 0, budget = b.campaign.budget_usd, running = 0;
      b.events.forEach((e, i) => {
        if (i > 0 && e.t - b.events[i - 1].t <= STOPPED_S) running += e.t - b.events[i - 1].t;
        if (e.type === "cost.updated" && typeof e.data?.usd === "number") usd += e.data.usd as number;
        if (e.type === "campaign.resumed" && e.data?.by === "runtime" && typeof e.data?.budget_usd === "number"
            && (e.data.budget_usd as number) > 0) budget = usd + (e.data.budget_usd as number);
      });
      if (live) setCost({ usd, budget, minutes: running / 60 });
    }).catch(() => {});
    return () => { live = false; };
  }, []);
  return cost;
}

/** Three numbers worth remembering, and the one command that lets anyone check. */
export function ProofScreen() {
  const cost = useHeroCost();
  const verified = EXPERIMENTS.find((x) => x.verified)?.verified;
  const { planted, caught, plainCaught } = FAULTS;
  const { wrongShown, agreed, runs } = SECOND_OPINION;

  return (
    <div className="screen proof">
      <PageHeader title="How it’s checked" />
      <main className="proof-main">
        <header className="proof-head">
          <p className="memory-kicker">How it&rsquo;s checked</p>
          <h1 className="proof-title">A finding only counts when it traces back to the recorded experiment.</h1>
        </header>

        <div className="proof-grid">
          <section className="proof-cell">
            <p className="proof-number num">{caught} <span>of {planted}</span></p>
            <p className="proof-caption">planted mistakes caught</p>
            <div className="proof-bars" aria-hidden>
              <span className="bar-label">Translucent</span>
              <span className="bar"><span className="bar-fill is-gold" style={{ width: `${(caught / planted) * 100}%` }} /></span>
              <span className="bar-num num">{caught}</span>
              <span className="bar-label">Plain script</span>
              <span className="bar"><span className="bar-fill" style={{ width: `${(plainCaught / planted) * 100}%` }} /></span>
              <span className="bar-num num">{plainCaught}</span>
            </div>
            <p className="proof-note">Wrong scales, wrong clocks, swapped videos, bad outlines and made-up numbers, planted one at a time.</p>
          </section>

          <section className="proof-cell">
            <p className="proof-number num">{agreed} <span>of {wrongShown}</span></p>
            <p className="proof-caption">wrong findings the second opinion agreed with</p>
            <div className="proof-dots" aria-hidden>
              {Array.from({ length: wrongShown }, (_, i) => <span key={i} className={i < agreed ? "is-agreed" : ""} />)}
            </div>
            <p className="proof-note">A second model looks at the picture behind each finding. Across {runs} real runs it never backed a wrong one.</p>
          </section>

          <section className="proof-cell">
            <p className="proof-number num">{cost ? money(cost.usd) : "—"}</p>
            <p className="proof-caption">cloud cost of a whole campaign</p>
            {cost && (
              <div className="proof-bars is-one" aria-hidden>
                <span className="bar"><span className="bar-fill is-ink" style={{ width: `${Math.min(1, cost.usd / cost.budget) * 100}%` }} /></span>
                <span className="bar-num num">of {money(cost.budget)}</span>
              </div>
            )}
            <p className="proof-note">
              {cost ? `Movie 4, from question to checked answer in about ${Math.round(cost.minutes)} minutes of running time.` : "Movie 4, from question to checked answer."}
            </p>
          </section>
        </div>

        <TryToFool />

        {verified && <CheckYourself v={verified} className="proof-check" />}
      </main>
    </div>
  );
}
