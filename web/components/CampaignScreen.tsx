"use client";
// A campaign as a conversation. The thread fills the middle, a small at-a-glance card sits top right, the composer is
// docked at the bottom, and the recording opens in a panel on the right (or over the page on a phone).
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { OWN, experiment } from "@/lib/campaigns";
import { VideoClock } from "@/lib/clock";
import { peopleApi } from "@/lib/people";
import { remember, setRunning } from "@/lib/recents";
import { ReplayPlayer, connectLive, liveIdFor } from "@/lib/sources";
import type { CampaignStore, CampaignView } from "@/lib/store";
import { useCampaign } from "@/lib/useCampaign";
import { dayWord, roleModel, runModel } from "@/lib/words";
import { Composer } from "./Composer";
import { EvidencePanel } from "./EvidencePanel";
import { Glance } from "./Glance";
import { IconFilm, IconPause, IconPlay, IconRestart, Spinner } from "./Icons";
import { PageHeader } from "./Shell";
import { Thread } from "./Thread";
import { Initial, TeamProvider, useTeam } from "./Team";

/** The planner a live run uses when the server does not name one. */
const LIVE_MODEL = "Muse Spark Open";

export function CampaignScreen({ campaignKey }: { campaignKey: string }) {
  const c = useCampaign(campaignKey);
  const clock = useMemo(() => new VideoClock(), []);
  const playerRef = useRef<ReplayPlayer | null>(null);
  const store = c?.store;
  const view = c?.view;
  const liveId = store ? liveIdFor(campaignKey) : null;
  const [liveModel, setLiveModel] = useState(LIVE_MODEL);
  useEffect(() => {
    if (liveId) peopleApi().session().then((s) => s.model && setLiveModel(s.model), () => {});
  }, [liveId]);
  const [panel, setPanel] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  // The event source: a live campaign streams from the server; otherwise the recorded replay plays. ?at=end or
  // ?at=<n> opens a replay at that point.
  useEffect(() => {
    if (!store) return;
    const id = liveIdFor(campaignKey);
    if (id) return connectLive(id, store, (e) => console.warn("Live stream error", e));
    const player = new ReplayPlayer(store);
    playerRef.current = player;
    const at = new URLSearchParams(window.location.search).get("at");
    if (at === "end") player.jump(store.total);
    else if (at && /^\d+$/.test(at)) player.jump(+at);
    else if (store.index < store.total) player.play();
    return () => { player.dispose(); store.setPlaying(false); playerRef.current = null; };
  }, [store, campaignKey]);

  // Remember it in the sidebar; mark a running live campaign there.
  const title = view?.campaign?.title || view?.campaign?.objective || "";
  useEffect(() => {
    if (!store || !title) return;
    const href = liveId ? `${window.location.pathname}${window.location.search}` : `/c/${campaignKey}/`;
    remember({ href, title: title.slice(0, 80), live: liveId ?? undefined });
  }, [store, title, liveId, campaignKey]);
  const running = !!liveId && view?.runState === "running";
  useEffect(() => {
    setRunning(running ? `${window.location.pathname}${window.location.search}` : null);
    return () => setRunning(null);
  }, [running]);

  // Replay keys: space plays or pauses, arrows step, Home and End jump.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const p = playerRef.current;
      if (!p || (e.target as HTMLElement)?.closest?.("input,textarea,button,[role=slider],select")) return;
      if (e.key === " ") { e.preventDefault(); p.toggle(); }
      else if (e.key === "ArrowRight") p.next();
      else if (e.key === "ArrowLeft") p.prev();
      else if (e.key === "End") p.jump(Number.MAX_SAFE_INTEGER);
      else if (e.key === "Home") p.jump(0);
      else if (e.key === "Escape") setPanel(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const openFinding = useCallback((id: string) => { setSelected(id); setPanel(true); }, []);
  const onFrames = useCallback((frames: number[]) => {
    setSelected(null);
    setPanel(true);
    const go = () => { clock.pause(); clock.seek(frames[0]); };
    if (clock.video?.readyState) go(); else setTimeout(go, 500);
  }, [clock]);

  // A lab's own recording has no replay of its own: without a run to follow there is nothing to load.
  const [nothingToFollow, setNothingToFollow] = useState(false);
  useEffect(() => {
    setNothingToFollow(campaignKey === OWN.key && !new URLSearchParams(window.location.search).get("live"));
  }, [campaignKey]);
  if (nothingToFollow) {
    return (
      <div className="campaign is-empty"><PageHeader />
        <div className="center-empty">
          <p>Nothing running on your recordings yet.</p>
          <a className="chip" href="/">Start a campaign</a>
        </div>
      </div>
    );
  }

  if (!c || !view || !store) {
    return <div className="campaign is-loading"><PageHeader /><div className="center-spin"><Spinner size={22} /></div></div>;
  }

  const finding = selected ? view.findings.find((f) => f.claim.id === selected) ?? null : null;
  const ex = experiment(campaignKey);
  // The run's own record of who played each role; older replays carry it on the experiment (sourced), else the reviews.
  const ids = view.models ?? (liveId ? null : ex.models ?? null);
  const second = [...store.catalog.reviews.values()].map((r) => r.model);
  const model = liveId && !ids ? liveModel
    : roleModel(ids?.planner) ?? shortModel(runModel(second));
  const who = ids ? { Planner: roleModel(ids.planner), Pointer: roleModel(ids.pointer),
                      "Second opinion": roleModel((ids as Record<string, string>).second_opinion) ?? shortModel(runModel(second)) } : undefined;
  const active = liveId ? view.runState === "running" : view.replay.playing;

  const screen = (
    <div className={`campaign ${panel ? "has-panel" : ""}`}>
      <div className="campaign-main">
        <PageHeader title={title}>
          {liveId && <Presence view={view} />}
          <button className={`icon-btn ${panel ? "is-on" : ""}`} onClick={() => { setPanel((p) => !p); setSelected(null); }}
            aria-label={panel ? "Hide the recording" : "Show the recording"} title="Recording" aria-pressed={panel}>
            <IconFilm />
          </button>
        </PageHeader>
        {!panel && <Glance view={view} model={model} who={who} verified={liveId ? undefined : ex.verified} />}
        <Thread campaignKey={campaignKey} store={store} view={view} active={active} selected={selected}
          onOpenFinding={openFinding} onFrames={onFrames} />
        <div className="dock">
          {liveId && !view.finished ? (
            <LiveDock view={view} model={model} />
          ) : (
            <ReplayDock store={store} view={view} campaignKey={campaignKey} player={playerRef} model={model} live={!!liveId} />
          )}
        </div>
      </div>
      {panel && (
        <>
          <div className="panel-scrim" onClick={() => setPanel(false)} aria-hidden />
          <EvidencePanel campaignKey={campaignKey} store={store} view={view} clock={clock} finding={finding}
            onClose={() => { setPanel(false); setSelected(null); }} autoPlay={!liveId} />
        </>
      )}
    </div>
  );
  return liveId ? <TeamProvider liveId={liveId} store={store}>{screen}</TeamProvider> : screen;
}

/** "Muse Spark Open on Fireworks" -> "Muse Spark Open". */
const shortModel = (m: string | null) => (m ? m.replace(/\s+on\s+\w+$/, "") : null);

// ---------------------------------------------------------------- replay dock

function ReplayDock({ store, view, campaignKey, player, model, live }: {
  store: CampaignStore; view: CampaignView; campaignKey: string; player: React.RefObject<ReplayPlayer | null>; model: string | null; live: boolean;
}) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const unix = view.campaign?.created_at ?? store.eventAt(0)?.t;
  const samReplayed = useMemo(
    () => Array.from({ length: store.total }, (_, i) => store.eventAt(i)).some((e) => /no live SAM|NoRecording/.test(String(e?.data?.summary ?? ""))),
    [store],
  );
  const progress = view.replay.total ? view.replay.index / view.replay.total : 0;
  const left = live ? null : (
    <>
      <button type="button" className="icon-btn" onClick={() => player.current?.toggle()} aria-label={view.replay.playing ? "Pause the replay" : "Play the replay"} title="Space">
        {view.replay.playing ? <IconPause size={16} /> : <IconPlay size={16} />}
      </button>
      <button type="button" className="icon-btn" onClick={() => player.current?.restart()} aria-label="Replay from the start" title="From the start">
        <IconRestart size={16} />
      </button>
      <span className="replay-label" title={samReplayed ? "SAM 3.1 replies are replayed from earlier recordings" : undefined}>
        Replay of a recorded run{unix ? ` · ${dayWord(unix)}` : ""}
      </span>
    </>
  );
  return (
    <Composer
      label="Ask a new question"
      value={q}
      onChange={setQ}
      onSubmit={() => router.push(`/?x=${campaignKey}&q=${encodeURIComponent(q.trim())}`)}
      placeholder="Ask a new question"
      canSend={!!q.trim()}
      left={left}
      right={model ? <span className="model-label">{model}</span> : null}
      progress={live ? undefined : progress}
    />
  );
}

// ---------------------------------------------------------------- live dock

function LiveDock({ view, model }: { view: CampaignView; model: string | null }) {
  const team = useTeam();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  useEffect(() => { if (team) { setName(team.name); setKey(team.key); } }, [team?.name, team?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!team) return null;

  const state = view.runState ?? (team.interrupted ? "stopped" : null);
  const ended = state === "stopped" || state === "provider_error" || (team.interrupted && !view.finished && state !== "paused");
  const act = async (fn: (n: string, k: string) => Promise<unknown>) => { setBusy(true); const ok = await team.act(fn); setBusy(false); return ok; };
  const note = async () => {
    if (!text.trim()) return;
    if (await act(async (n, k) => team.store.ingest(await peopleApi().note(team.liveId, text.trim(), n, k)))) setText("");
  };

  const tray = !team.enabled ? null : !team.joined ? (
    <form className="tray-join" onSubmit={(e) => { e.preventDefault(); if (name.trim() && key.trim()) team.join(name, key); }}>
      <input ref={team.joinRef} className="field" placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoComplete="name" aria-label="Your name" />
      <input className="field" type="password" placeholder="Team key" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" aria-label="Team key" />
      <button className="btn btn-small" disabled={!name.trim() || !key.trim()}>Join</button>
    </form>
  ) : team.notice ? <p className="team-notice" role="status">{team.notice}</p> : null;

  const left = (
    <>
      <span className={`run-state state-${ended ? "stopped" : state ?? "waiting"}`} aria-live="polite">
        <span className={`live-dot ${state === "running" && !view.hiccup ? "" : "is-still"}`} aria-hidden />
        {ended ? "Stopped" : view.hiccup ? "Retrying" : state === "paused" ? "Paused" : state === "running" ? "Running" : "Starting"}
      </span>
      {team.enabled && state === "running" && (
        <button type="button" className="chip" onClick={() => act((n, k) => peopleApi().control(team.liveId, "pause", n, k))} disabled={busy}>
          <IconPause size={14} />Pause
        </button>
      )}
      {team.enabled && state === "paused" && (
        <button type="button" className="chip" onClick={() => act((n, k) => peopleApi().control(team.liveId, "resume", n, k))} disabled={busy}>
          <IconPlay size={14} />Resume
        </button>
      )}
      {team.enabled && ended && (
        <button type="button" className="chip" disabled={busy}
          onClick={async () => { if (await act((_, k) => peopleApi().resume(team.liveId, 1, k))) team.markResumed(); }}>
          Pick up again
        </button>
      )}
    </>
  );

  return (
    <Composer
      label="Tell the agent something"
      value={text}
      onChange={setText}
      onSubmit={note}
      placeholder={team.enabled ? "Tell the agent something" : "Following live"}
      canSend={!!text.trim() && team.enabled && (state === "running" || state === "paused")}
      busy={busy}
      disabled={!team.enabled}
      onStop={team.enabled && state === "running" ? () => act((n, k) => peopleApi().control(team.liveId, "stop", n, k)) : undefined}
      left={left}
      right={model ? <span className="model-label">{model}</span> : null}
      tray={tray}
    />
  );
}

/** Who has taken part in this live campaign: you first, then the team, as small initials. */
function Presence({ view }: { view: CampaignView }) {
  const team = useTeam();
  const names = useMemo(() => {
    const seen = new Map<string, string>();
    if (team?.name) seen.set(team.name.toLowerCase(), team.name);
    for (const l of view.story) if (l.author) seen.set(l.author.toLowerCase(), seen.get(l.author.toLowerCase()) ?? l.author);
    for (const f of view.findings) for (const p of f.people) if (p.author) seen.set(p.author.toLowerCase(), seen.get(p.author.toLowerCase()) ?? p.author);
    return [...seen.values()];
  }, [team?.name, view.story, view.findings]);
  if (!names.length) return null;
  return (
    <span className="presence" title={names.join(", ")} aria-label={`Here: ${names.join(", ")}`}>
      {names.slice(0, 4).map((n) => <Initial key={n} name={n} />)}
    </span>
  );
}
