"use client";
// Home: engineering campaigns. The server's campaigns, a New campaign button, and the recorded engineering examples
// (model learning against a simulated lab first, then evaluation case B and its browser demonstration).
// The microscopy question box and the other recorded examples live on /examples/.
import Link from "next/link";
import { useEffect } from "react";
import { FASTCHARGE_DEMO_HREF, FASTCHARGE_HREF, FASTCHARGE_LAB_HREF, RECORDS_TO_DESIGN_HREF } from "@/lib/loop";
import { canWrite, useApiConfigured, useSession } from "@/lib/session";
import { ServerCampaigns } from "./CampaignList";
import { IconNew } from "./Icons";
import { PageHeader } from "./Shell";

export function Home() {
  const s = useSession();
  const configured = useApiConfigured();
  useEffect(() => { document.title = "Translucent"; }, []);
  const viewer = s.who && !canWrite(s.who);
  return (
    <div className="screen ws-home">
      <PageHeader />
      <main className="ws-home-main">
        <header className="ws-home-head">
          <div>
            <h1 className="ws-home-title">Engineering campaigns</h1>
            <p className="ws-home-lead">Records, a failing baseline, an objective and its constraints. A planner proposes each experiment, a person or a standing rule approves it, the solver runs it and a fixed rule compares the result with the baseline.</p>
          </div>
          {configured && (
            viewer ? <span className="ws-muted">Viewers read campaigns; an engineer opens them.</span>
              : <Link href="/new/" className="btn ws-new-btn"><IconNew size={16} />New campaign</Link>
          )}
        </header>

        {configured && <ServerCampaigns limit={8} />}

        <section className="ws-home-section" aria-labelledby="ws-rec-h">
          <h2 className="ws-home-h2" id="ws-rec-h">Recorded engineering examples</h2>
          <p className="ws-home-note ws-muted">Replays of finished runs. Every experiment in them ran in simulation: the PyBaMM solver or a simulated lab, never a physical cell.</p>
          <Link href={RECORDS_TO_DESIGN_HREF} className="ws-rec-card">
            <span className="ws-rec-top">
              <span className="ws-rec-title">From raw cell records to a charging design, with no one stepping in</span>
            </span>
            <span className="ws-rec-line">Given two raw exports from published LG M50 cell tests, Muse Wonder wrote its own import and fixed four refused mappings. Its first thermal model fitted one cell and failed on the other, so it measured the simulated lab fixture instead. That model passed a held-out check, and its design charges in 35.875 min, matching a reference controller that was told the hidden lab condition. 13 Muse turns, 12 experiments, 15.7 minutes, $1.99; a standing rule approved every experiment. Its request to verify the design in the lab was refused: the lab-test budget was spent.</span>
          </Link>
          <Link href={FASTCHARGE_LAB_HREF} className="ws-rec-card">
            <span className="ws-rec-top">
              <span className="ws-rec-title">Learning the lab fixture&rsquo;s thermal model</span>
            </span>
            <span className="ws-rec-line">Muse Wonder ran a first lab test at 1.5C, fitted the fixture&rsquo;s cooling and thermal mass (model v1, 0.15 K RMSE) and ruled out the nominal model. v1 passed held-out checks on two more lab tests; its design, 1.5, 1.35, 0.9 then 0.5C, charged 10 to 80 % in 47.4 min at 37.7 °C in the simulated lab. Seven experiments, $0.78 of model spend. Feasible only in simulation; speed not compared.</span>
          </Link>
          <Link href={FASTCHARGE_HREF} className="ws-rec-card">
            <span className="ws-rec-top">
              <span className="ws-rec-title">Faster LG M50 charge (evaluation case B)</span>
            </span>
            <span className="ws-rec-line">Muse Wonder proposed eight PyBaMM experiments; midway an engineer raised the plating limit from +10 to +25 mV. The 1.5C baseline (28.2 min) breaks both limits; the best candidate meets them at 37.3 min. Feasibility recovered; speed not compared.</span>
          </Link>
          <Link href={FASTCHARGE_DEMO_HREF} className="ws-rec-card">
            <span className="ws-rec-top">
              <span className="ws-rec-title">Demonstration run of case B, made in the browser</span>
            </span>
            <span className="ws-rec-line">One run through the web app, every request approved by hand, the plating limit raised to +25 mV after four experiments. Five experiments before the $1.50 model budget ran out; the best, 37.2 min, meets both limits and beats the 70 min single-step reference found under the same limits. A demonstration, not a benchmark.</span>
          </Link>
        </section>

        <p className="ws-home-other"><Link className="text-link" href="/examples/">Other recorded examples</Link> <span className="ws-muted">microscopy questions and intervention benchmarks</span></p>
      </main>
    </div>
  );
}
