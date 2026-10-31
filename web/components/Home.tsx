"use client";
// Home: one question, the composer, and a chip per experiment. Recorded runs replay from here; with the team server
// switched on, the same composer starts a live campaign, and "+" adds a lab's own recording.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { EXPERIMENTS } from "@/lib/campaigns";
import { INTERVENTION_NAV, interventionHref } from "@/lib/intervention";
import { identity, keyForExperiment, peopleApi, PeopleError, type RecordedExperiment } from "@/lib/people";
import { remember } from "@/lib/recents";
import { apiConfigured } from "@/lib/sources";
import { ensureMock } from "@/lib/useCampaign";
import { AddRecording } from "./AddRecording";
import { ServerCampaigns } from "./CampaignList";
import { ChipMenu, Composer, type PlusAction } from "./Composer";
import { LogoMark } from "./Logo";
import { IconBolt, IconCheck, IconClose, IconDown, IconFilm, IconUpload } from "./Icons";
import { PageHeader } from "./Shell";

/** What the server allows: live campaigns on its experiments, and adding recordings. Null without a server. */
function useServer() {
  const [server, setServer] = useState<{ live: boolean; canAdd: boolean; model: string; list: RecordedExperiment[] } | null>(null);
  useEffect(() => {
    const mock = new URLSearchParams(window.location.search).has("mock");
    if (!mock && !apiConfigured()) return;
    let on = true;
    ensureMock()
      .then(() => peopleApi().session())
      .then(async (s) => ({ live: s.live, canAdd: s.live || s.team, model: s.model ?? LIVE_MODEL,
        list: s.live ? await peopleApi().experiments() : [] }))
      .then((x) => { if (on && (x.canAdd || x.list.length)) setServer(x); })
      .catch(() => { /* no team server: recorded runs only */ });
    return () => { on = false; };
  }, []);
  return [server, setServer] as const;
}

const isUserRecording = (x: RecordedExperiment) => x.experiment.startsWith("user/");

/** The run the composer will start: a recorded replay, or a live run on one of the server's experiments. */
type Pick = { kind: "replay"; key: string } | { kind: "live"; x: RecordedExperiment };

/** The planner a live run uses when the server does not name one. */
const LIVE_MODEL = "Muse Spark Open";

/** A short label for each experiment's suggested question. */
const CHIP: Record<string, string> = { m4: "Filaments and shorts", lu: "When plating starts" };

export function Home() {
  const router = useRouter();
  const [server, setServer] = useServer();
  const [pick, setPick] = useState<Pick>({ kind: "replay", key: EXPERIMENTS[0].key });
  const [q, setQ] = useState("");
  const [budget, setBudget] = useState("1");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState("");
  const [wantOwn, setWantOwn] = useState(false);

  // ?x=<key> preselects an experiment (sidebar); ?x=own goes to the lab's own recordings; ?add=1 opens the add form.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const x = p.get("x");
    const ex = EXPERIMENTS.find((e) => e.key === x);
    if (ex) { setPick({ kind: "replay", key: ex.key }); setQ(""); }
    if (x === "own") setWantOwn(true);
    if (p.has("add")) setAdding(true);
    const text = p.get("q");
    if (text) setQ(text.slice(0, 2000));
    setKey(identity.key());
  }, []);
  useEffect(() => { if (server) setKey((k) => k || identity.key()); }, [server]);
  useEffect(() => {
    if (!wantOwn) return;
    const own = server?.list.find(isUserRecording);
    if (own) { setPick({ kind: "live", x: own }); setWantOwn(false); }
    else if (server?.canAdd) { setAdding(true); setWantOwn(false); }
    else setNotice("Your own recordings need the team server.");
  }, [wantOwn, server]);

  const live = pick.kind === "live";
  const spend = Number(budget);
  const canSend = !!q.trim() && (!live || (!!key.trim() && spend > 0 && spend <= 100));

  const send = async () => {
    if (pick.kind === "replay") {
      router.push(`/c/${pick.key}/`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const c = await peopleApi().start({ objective: q.trim(), experiment_id: pick.x.experiment, budget: spend }, key.trim());
      identity.setKey(key);
      const mock = new URLSearchParams(window.location.search).has("mock") ? "&mock=1" : "";
      const href = `/c/${keyForExperiment(pick.x)}/?live=${encodeURIComponent(c.id)}${mock}`;
      remember({ href, title: q.trim().slice(0, 80), live: c.id });
      router.push(href);
    } catch (err) {
      setError(err instanceof PeopleError && err.status === 409 ? "Another campaign is running. Try again when it ends."
        : err instanceof PeopleError ? err.message : "Couldn’t start it. Try again.");
      setBusy(false);
    }
  };

  const onAdded = (x: RecordedExperiment) => {
    identity.setKey(key);
    const list = [...(server?.list ?? []).filter((y) => y.experiment !== x.experiment), x];
    setServer((s) => (s ? { ...s, list } : s));
    setAdding(false);
    if (server?.live) { setPick({ kind: "live", x }); setNotice(`Added “${x.label}”. Ask it something.`); }
    else setNotice(`Added “${x.label}”. Live runs aren’t switched on here yet.`);
  };

  const liveList = server?.live ? server.list : [];
  const plus: PlusAction[] = [];
  if (liveList.length) {
    plus.push({ label: "Run your own", hint: "A live campaign, paid from your budget", icon: <IconBolt />,
      onSelect: () => { setPick({ kind: "live", x: liveList[0] }); setNotice(""); } });
  }
  if (server?.canAdd) plus.push({ label: "Add your own recording", hint: "Video and lab records", icon: <IconUpload />, onSelect: () => setAdding(true) });

  const pickName = pick.kind === "replay" ? EXPERIMENTS.find((e) => e.key === pick.key)!.name : pick.x.label;
  const suggestion = pick.kind === "replay" ? EXPERIMENTS.find((e) => e.key === pick.key)!.suggestion
    : EXPERIMENTS.find((e) => e.key === keyForExperiment(pick.x))?.suggestion ?? "";

  const left = (
    <>
      <ChipMenu label={<>{live ? <IconBolt size={15} /> : <IconFilm size={15} />}<span className="chip-text">{pickName}</span><IconDown size={14} /></>} title="Experiment">
        {(close) => (
          <>
            <p className="menu-head">Recorded runs</p>
            {EXPERIMENTS.map((x) => (
              <button key={x.key} type="button" role="menuitemradio" aria-checked={pick.kind === "replay" && pick.key === x.key} className="menu-item"
                onClick={() => { setPick({ kind: "replay", key: x.key }); setQ((t) => (EXPERIMENTS.some((e) => e.suggestion === t) ? "" : t)); close(); }}>
                <img src={x.thumb} alt="" className="menu-thumb" />
                <span><span className="menu-label">{x.name}</span><span className="menu-hint">{x.source}</span></span>
                {pick.kind === "replay" && pick.key === x.key && <IconCheck size={16} className="menu-tick" />}
              </button>
            ))}
            {liveList.length > 0 && <p className="menu-head">Run live</p>}
            {liveList.map((x) => {
              const local = EXPERIMENTS.find((e) => e.key === keyForExperiment(x));
              const on = pick.kind === "live" && pick.x.experiment === x.experiment;
              return (
                <button key={x.experiment} type="button" role="menuitemradio" aria-checked={on} className="menu-item"
                  onClick={() => { setPick({ kind: "live", x }); close(); }}>
                  {isUserRecording(x) || !local ? <span className="menu-thumb is-blank">{x.label.trim().charAt(0).toUpperCase()}</span>
                    : <img src={local.thumb} alt="" className="menu-thumb" />}
                  <span><span className="menu-label">{x.label}</span>{x.citation && <span className="menu-hint">{x.citation}</span>}</span>
                  {on && <IconCheck size={16} className="menu-tick" />}
                </button>
              );
            })}
          </>
        )}
      </ChipMenu>
      {live && (
        <ChipMenu label={<span className="chip-text num">${spend > 0 ? +spend.toFixed(2) : "–"} budget</span>} title="Most it may spend">
          {() => (
            <label className="menu-field">
              <span className="menu-hint">Most it may spend</span>
              <span className="money-field">
                <span aria-hidden>$</span>
                <input className="field" inputMode="decimal" value={budget} autoFocus
                  onChange={(e) => setBudget(e.target.value.replace(/[^\d.]/g, ""))} />
              </span>
            </label>
          )}
        </ChipMenu>
      )}
    </>
  );

  const tray = live && !identity.key() ? (
    <label className="tray-field">
      <span>Access key</span>
      <input className="field" type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />
    </label>
  ) : null;

  return (
    <div className="home">
      <PageHeader />
      <div className="home-center">
        <div className="home-hero">
          <LogoMark size={56} className="home-mark" />
          <h1 className="home-question">What should we find out?</h1>
        </div>
      </div>
      <div className="home-dock">
        {(error || notice) && <p className={`dock-note ${error ? "is-error" : ""}`} role="status">{error || notice}</p>}
        {!q && <ServerCampaigns />}
        {!q && <p className="side-label home-recorded">Recorded examples</p>}
        {!q && INTERVENTION_NAV.map((x) => (
          <Link key={x.key} href={interventionHref(x.key)} className="home-feature">
            <span className="home-feature-top"><span className="home-feature-title">{x.name}</span><span className="home-feature-kind">{x.source === "live" ? "Live run" : "Scripted benchmark"}</span></span>
            <span className="home-feature-line">{x.line}</span>
          </Link>
        ))}
        {!q && suggestion && (
          <div className="suggestions">
            {EXPERIMENTS.map((x) => (
              <button key={x.key} type="button" className="suggestion" onClick={() => { if (pick.kind === "replay") setPick({ kind: "replay", key: x.key }); setQ(x.suggestion); }}>
                {CHIP[x.key] ?? x.name}
              </button>
            ))}
          </div>
        )}
        <Composer
          label="Your question"
          value={q}
          onChange={setQ}
          onSubmit={send}
          placeholder={live ? "Ask a question about this recording" : "Ask about a recorded experiment"}
          canSend={canSend}
          busy={busy}
          plus={plus}
          left={left}
          right={<span className="model-label">{live ? server?.model ?? LIVE_MODEL : "Replay"}</span>}
          tray={tray}
          autoFocus
        />
      </div>

      {adding && (
        <div className="sheet" role="dialog" aria-modal="true" aria-label="Add your own recording">
          <div className="sheet-scrim" onClick={() => setAdding(false)} />
          <div className="sheet-body">
            <div className="sheet-head">
              <h2>Add your own recording</h2>
              <button className="icon-btn" onClick={() => setAdding(false)} aria-label="Close"><IconClose /></button>
            </div>
            <AddRecording accessKey={key} setAccessKey={setKey} author={identity.name()} onAdded={onAdded} />
          </div>
        </div>
      )}
    </div>
  );
}
