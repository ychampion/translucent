"use client";
// A campaign as a conversation. The thread fills the middle, a small at-a-glance card sits top right, the composer is
// docked at the bottom, and the recording opens in a panel on the right (or over the page on a phone).
import { useRouter } from "next/navigation";
import { useCommands } from "@/lib/commands";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { OWN, experiment } from "@/lib/campaigns";
import { VideoClock } from "@/lib/clock";
import { identity, peopleApi } from "@/lib/people";
import { markRead, markUnread, remember, setAttention, setRunning } from "@/lib/recents";
import { ReplayPlayer, connectLive, liveIdFor } from "@/lib/sources";
import type { CampaignStore, CampaignView, Question } from "@/lib/store";
import { attentionCount, openQuestions, toSignOff } from "@/lib/attention";
import { useCampaignLoad } from "@/lib/useCampaign";
import { STATE_WORD, dayWord, roleModel, runModel } from "@/lib/words";
import { Announcer } from "./Announcer";
import { Composer } from "./Composer";
import { FindBar } from "./FindBar";
import { findingHref, receiptHref } from "./Findings";
import { compareHref, compareTargetFor } from "./CompareScreen";
import { EvidencePanel, readLayout, saveLayout, type PanelLayout } from "./EvidencePanel";
import { Glance } from "./Glance";
import { IconFilm, IconPause, IconPlay, IconRestart } from "./Icons";
import { PageHeader } from "./Shell";
import { Boundary, LoadState } from "./Status";
import { Thread } from "./Thread";
import { withBase } from "@/lib/base";
import { Initial, TeamProvider, useTeam } from "./Team";

/** The planner a live run uses when the server does not name one. */
const LIVE_MODEL = "Muse Spark Open";

export function CampaignScreen({ campaignKey }: { campaignKey: string }) {
  const { c, error, retry } = useCampaignLoad(campaignKey);
  const clock = useMemo(() => new VideoClock(), []);
  const router = useRouter();
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
  const [layout, setLayoutState] = useState<PanelLayout>({ tab: "recording", width: null, wide: false });
  useEffect(() => { setLayoutState(readLayout(campaignKey)); }, [campaignKey]);
  const setLayout = useCallback((l: PanelLayout) => { setLayoutState(l); saveLayout(campaignKey, l); }, [campaignKey]);

  // The event source: a live campaign streams from the server; otherwise the recorded replay plays. ?at=end or
  // ?at=<n> opens a replay at that point. `reconnect` opens the live stream again after it was lost.
  const [reconnect, setReconnect] = useState(0);
  useEffect(() => {
    if (!store || !liveIdFor(campaignKey)) return;
    return connectLive(liveIdFor(campaignKey)!, store);
  }, [store, campaignKey, reconnect]);
  useEffect(() => {
    if (!store) return;
    if (liveIdFor(campaignKey)) return;
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
  // A title the server cut from the objective mid-word reads as broken; show the whole question and let the header
  // ellipsize it at whatever width there is.
  const objective = view?.campaign?.objective ?? "";
  const heading = objective && title && objective !== title && objective.startsWith(title.replace(/[….\s]+$/, "")) ? objective : title;
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

  // Typing a letter anywhere outside a field starts a message in the composer (shortcut letters excepted).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || (e.target as HTMLElement)?.closest?.("input,textarea,select,[contenteditable=true]")) return;
      if (e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.metaKey && !e.altKey && !TYPED_KEYS.test(e.key)) {
        const field = document.getElementById("composer-field") as HTMLTextAreaElement | null;
        if (field && !field.disabled) field.focus();
      }
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

  // Find in the thread.
  const [findQuery, setFind] = useState<string | null>(null);

  // What needs this person: a count in the sidebar and the tab title; a run that finished while hidden is unread.
  const href = liveId && typeof window !== "undefined" ? `${window.location.pathname}${window.location.search}` : `/c/${campaignKey}/`;
  const need = liveId && view ? attentionCount(view, identity.name()) : 0;
  useEffect(() => {
    if (!title) return;
    setAttention(href, need);
    const short = SHORT_TITLE[campaignKey] && !liveId ? SHORT_TITLE[campaignKey] : title.length > 48 ? `${title.slice(0, 46)}…` : title;
    document.title = `${need ? `(${need}) ` : ""}${short} – Translucent`;
  }, [need, title, href, campaignKey, liveId]);
  useEffect(() => () => { document.title = "Translucent"; }, []);
  const finished = !!view?.finished;
  const wasRunning = useRef(false);
  useEffect(() => {
    if (!liveId) return;
    if (!finished) { wasRunning.current = true; return; }
    if (wasRunning.current && document.hidden) markUnread(href);
  }, [finished, liveId, href]);
  useEffect(() => {
    const read = () => { if (!document.hidden) markRead(href); };
    read();
    document.addEventListener("visibilitychange", read);
    return () => document.removeEventListener("visibilitychange", read);
  }, [href]);

  // Commands: the palette, the shortcut sheet and the keys all read these.
  const nextNeed = useRef(-1);
  useCommands(`campaign:${campaignKey}`, () => {
    if (!view || !store) return [];
    const p = () => playerRef.current;
    const order = view.findings.map((f) => f.claim.id);
    const at = selected ? order.indexOf(selected) : -1;
    const goFinding = (id: string | undefined) => { if (!id) return; setSelected(id); scrollToFinding(id); };
    return [
      ...view.findings.map((f) => ({
        id: `go:${f.claim.id}`, label: f.claim.statement, hint: STATE_WORD[f.state], group: "Findings" as const, run: () => goFinding(f.claim.id),
      })),
      { id: "next-finding", label: "Next finding", group: "Findings", keys: ["J"], when: () => order.length > 0, quiet: true, run: () => goFinding(order[Math.min(order.length - 1, at + 1)]) },
      { id: "prev-finding", label: "Previous finding", group: "Findings", keys: ["K"], when: () => order.length > 0, quiet: true, run: () => goFinding(order[Math.max(0, at < 0 ? 0 : at - 1)]) },
      {
        id: "needs-me", label: "Next thing that needs me", hint: "An open question or a finding to sign off", group: "Findings", keys: ["N"],
        when: () => !!liveId,
        run: () => {
          const targets = [
            ...openQuestions(view).map((q) => `[data-question="${CSS.escape(q.id)}"]`),
            ...toSignOff(view, identity.name()).map((f) => `[data-finding="${CSS.escape(f.claim.id)}"]`),
          ];
          if (!targets.length) return;
          nextNeed.current = (nextNeed.current + 1) % targets.length;
          const el = document.querySelector<HTMLElement>(targets[nextNeed.current]);
          el?.scrollIntoView({ behavior: "smooth", block: "center" });
          el?.querySelector<HTMLElement>("button,input")?.focus({ preventScroll: true });
        },
      },
      ...(selected ? [
        { id: "receipt", label: "Open the receipt", hint: view.findings.find((f) => f.claim.id === selected)?.claim.statement, group: "Findings" as const, run: () => router.push(receiptHref(campaignKey, selected)) },
        { id: "finding-page", label: "Open all the checks", hint: view.findings.find((f) => f.claim.id === selected)?.claim.statement, group: "Findings" as const, run: () => router.push(findingHref(campaignKey, selected)) },
      ] : []),
      { id: "panel", label: panel ? "Hide the panel" : "Show the recording", group: "Campaign", run: () => { setPanel((x) => !x); setSelected(null); } },
      { id: "graph", label: "Open the evidence graph", group: "Campaign", run: () => router.push(`/c/${campaignKey}/graph/`) },
      ...(!liveId && compareTargetFor(campaignKey) ? [{ id: "compare-this", label: "Compare with the other recorded run", group: "Compare" as const, run: () => router.push(compareHref(experiment(campaignKey).folder, compareTargetFor(campaignKey)!)) }] : []),
      ...(selected ? [{ id: "trail", label: "Show this finding’s trail", group: "Findings" as const, run: () => { setLayout({ ...layout, tab: "trail" }); setPanel(true); } }] : []),
      { id: "close-panel", label: "Close the panel", group: "Campaign", keys: ["Escape"], quiet: true, when: () => panel && !document.querySelector(".menu,.sheet"), run: () => { setPanel(false); setSelected(null); } },
      { id: "find", label: "Find in this campaign", group: "Campaign", keys: ["Ctrl+F"], inFields: true, run: () => setFind((q) => q ?? "") },
      { id: "answer", label: "Jump to the answer", group: "Campaign", when: () => view.finished, run: () => document.querySelector(".msg-answer")?.scrollIntoView({ behavior: "smooth", block: "start" }) },
      { id: "summary", label: "Full answer", group: "Campaign", when: () => view.finished, run: () => router.push(`/c/${campaignKey}/summary/`) },
      { id: "play", label: "Play or pause the replay", group: "Campaign", keys: ["Space"], when: () => !!p(), run: () => p()?.toggle() },
      { id: "restart", label: "Replay from the start", group: "Campaign", when: () => !!p(), run: () => p()?.restart() },
      { id: "end", label: "Go to the end", group: "Campaign", keys: ["End"], when: () => !!p(), run: () => p()?.jump(Number.MAX_SAFE_INTEGER) },
      { id: "start", label: "Go to the start", group: "Campaign", keys: ["Home"], quiet: true, when: () => !!p(), run: () => p()?.jump(0) },
      { id: "step", label: "Step forward", group: "Campaign", keys: ["ArrowRight"], quiet: true, when: () => !!p(), run: () => p()?.next() },
      { id: "back", label: "Step back", group: "Campaign", keys: ["ArrowLeft"], quiet: true, when: () => !!p(), run: () => p()?.prev() },
    ];
  }, [view?.findings, selected, panel, liveId, view?.finished, campaignKey, !!store, layout]);

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
          <a className="chip" href={withBase("/examples/")}>Start a campaign</a>
        </div>
      </div>
    );
  }

  if (!c || !view || !store) {
    return <div className="campaign is-loading"><PageHeader /><LoadState error={error} onRetry={retry} /></div>;
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
    <div className={`campaign ${panel ? "has-panel" : ""} ${panel && layout.wide ? "is-wide" : ""}`}
      style={panel && layout.width && !layout.wide ? { ["--panel-w" as string]: `${layout.width}px` } : undefined}>
      <div className="campaign-main">
        <PageHeader title={<span title={heading}>{heading}</span>}>
          {liveId && <Presence view={view} />}
          <button className={`icon-btn ${panel ? "is-on" : ""}`} onClick={() => { setPanel((p) => !p); setSelected(null); }}
            aria-label={panel ? "Hide the recording" : "Show the recording"} title="Recording" aria-pressed={panel}>
            <IconFilm />
          </button>
        </PageHeader>
        {!panel && <Glance view={view} model={model} who={who} verified={liveId ? undefined : ex.verified} />}
        <Boundary label="The conversation" className="is-page">
          {findQuery != null && (
            <FindBar query={findQuery} onQuery={setFind} onClose={() => setFind(null)} version={`${view.story.length}:${view.findings.length}`} />
          )}
          <Thread campaignKey={campaignKey} store={store} view={view} active={active} selected={selected}
            onOpenFinding={openFinding} onFrames={onFrames} query={findQuery ?? ""} />
        </Boundary>
        <Announcer view={view} />
        {liveId && <PeopleCommands view={view} selected={selected} />}
        <div className="dock">
          {liveId && view.stream !== "ok" && (
            <p className="stream-line" role="status">
              {view.stream === "lost" ? "Lost the live feed." : "Lost the live feed, reconnecting…"}
              {view.stream === "lost" && <button type="button" className="text-quiet" onClick={() => setReconnect((n) => n + 1)}>Reconnect</button>}
            </p>
          )}
          {liveId && !view.finished ? (
            <LiveDock view={view} model={model} onOpenFinding={openFinding} />
          ) : (
            <ReplayDock store={store} view={view} campaignKey={campaignKey} player={playerRef} model={model} live={!!liveId} />
          )}
        </div>
      </div>
      {panel && (
        <>
          <div className="panel-scrim" onClick={() => setPanel(false)} aria-hidden />
          <Boundary label="The evidence panel" className="is-panel">
            <EvidencePanel campaignKey={campaignKey} store={store} view={view} clock={clock} finding={finding}
              onClose={() => { setPanel(false); setSelected(null); }} autoPlay={!liveId}
              layout={layout} onLayout={setLayout} onOpenFinding={openFinding} />
          </Boundary>
        </>
      )}
    </div>
  );
  return liveId ? <TeamProvider liveId={liveId} store={store}>{screen}</TeamProvider> : screen;
}

/** Tab titles for the recorded runs. */
const SHORT_TITLE: Record<string, string> = { m4: "Movie 4", lu: "Graphite" };

/** Keys that are shortcuts on this page, so typing them does not jump into the composer. */
const TYPED_KEYS = /^[jkn?/]$/i;

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
      onSubmit={() => router.push(`/examples/?x=${campaignKey}&q=${encodeURIComponent(q.trim())}`)}
      placeholder="Ask a new question"
      canSend={!!q.trim()}
      left={left}
      right={model ? <span className="model-label">{model}</span> : null}
      progress={live ? undefined : progress}
    />
  );
}

// ---------------------------------------------------------------- live dock

function LiveDock({ view, model, onOpenFinding }: { view: CampaignView; model: string | null; onOpenFinding(id: string): void }) {
  const team = useTeam();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const [replying, setReplying] = useState<string | null>(null);
  useEffect(() => { if (team) { setName(team.name); setKey(team.key); } }, [team?.name, team?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  const question = openQuestions(view).filter((q) => !dismissed.has(q.id)).pop() ?? null;
  useEffect(() => { if (replying && question?.id !== replying) setReplying(null); }, [question?.id, replying]);
  if (!team) return null;

  const state = view.runState ?? (team.interrupted ? "stopped" : null);
  const ended = state === "stopped" || state === "provider_error" || (team.interrupted && !view.finished && state !== "paused");
  const act = async (fn: (n: string, k: string) => Promise<unknown>) => { setBusy(true); const ok = await team.act(fn); setBusy(false); return ok; };
  const answer = async (qid: string, reply: string) => {
    if (!reply.trim()) return false;
    return act(async (n, k) => team.store.ingest(await peopleApi().answer(team.liveId, qid, reply.trim(), n, k)));
  };
  const note = async () => {
    if (!text.trim()) return;
    if (replying) { if (await answer(replying, text)) { setText(""); setReplying(null); } return; }
    if (await act(async (n, k) => team.store.ingest(await peopleApi().note(team.liveId, text.trim(), n, k)))) setText("");
  };
  const unsigned = team.enabled && team.joined ? toSignOff(view, team.name) : [];
  const needs = team.enabled && (question || unsigned.length > 0) ? (
    <NeedsTray question={question} unsigned={unsigned.length} busy={busy} replying={!!replying}
      onPick={(o) => question && answer(question.id, o)}
      onReply={() => { if (!question) return; setReplying(question.id); document.getElementById("composer-field")?.focus(); }}
      onDismiss={() => { if (question) setDismissed((d) => new Set(d).add(question.id)); setReplying(null); }}
      onReview={() => { const f = unsigned[0]; if (!f) return; onOpenFinding(f.claim.id); scrollToFinding(f.claim.id); }} />
  ) : null;

  const access = !team.enabled ? null : !team.joined ? (
    <form className="tray-join" onSubmit={(e) => { e.preventDefault(); if (name.trim() && key.trim()) team.join(name, key); }}>
      <input ref={team.joinRef} className="field" placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoComplete="name" aria-label="Your name" />
      <input className="field" type="password" placeholder="Team key" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" aria-label="Team key" />
      <button className="btn btn-small" disabled={!name.trim() || !key.trim()}>Join</button>
    </form>
  ) : team.notice ? <p className="team-notice" role="status">{team.notice}</p> : null;
  const tray = access || needs ? <>{access}{needs}</> : null;

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
      label={replying ? "Your answer to the question" : "Tell the agent something"}
      value={text}
      onChange={setText}
      onSubmit={note}
      onEscape={replying ? () => setReplying(null) : undefined}
      placeholder={!team.enabled ? "Following live" : replying ? "Your answer to the question" : "Tell the agent something"}
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

/** Palette commands for people in the loop: sign off or challenge the selected finding, answer the open question. */
function PeopleCommands({ view, selected }: { view: CampaignView; selected: string | null }) {
  const team = useTeam();
  const f = selected ? view.findings.find((x) => x.claim.id === selected) : undefined;
  const mine = !!f && !!team?.name && f.people.some((p) => (p.author ?? "").toLowerCase() === team.name.toLowerCase());
  const open = openQuestions(view).length > 0;
  useCommands("people", () => {
    if (!team?.enabled) return [];
    const can = !!f && f.state === "earned" && !mine;
    return [
      ...(can ? [
        { id: "sign-off", label: "Sign off this finding", hint: f!.claim.statement, group: "People" as const,
          run: () => { void team.act(async (n, k) => team.store.addReview(await peopleApi().review(team.liveId, f!.claim.id, "agree", "", n, k))); } },
        { id: "challenge", label: "Challenge this finding", hint: f!.claim.statement, group: "People" as const,
          run: () => {
            scrollToFinding(f!.claim.id);
            document.querySelector<HTMLButtonElement>(`[data-finding="${CSS.escape(f!.claim.id)}"] .finding-actions .chip-btn:last-child`)?.click();
          } },
      ] : []),
      ...(open ? [{ id: "answer-q", label: "Answer the open question", group: "People" as const,
        run: () => document.querySelector<HTMLButtonElement>(".needs-actions .chip[aria-pressed]")?.click() }] : []),
    ];
  }, [team?.enabled, f?.claim.id, f?.state, mine, open]);
  return null;
}

/** Scroll the thread to a finding's card and focus it. */
export function scrollToFinding(id: string) {
  const el = document.querySelector<HTMLElement>(`[data-finding="${CSS.escape(id)}"]`);
  el?.scrollIntoView({ behavior: "smooth", block: "center" });
  el?.querySelector<HTMLElement>(".fcard-main")?.focus({ preventScroll: true });
}

/**
 * What is waiting on you, docked above the composer: the open question with its options (keys 1 to 9 pick one while
 * the tray has focus, Escape hides the tray but leaves the question open in the thread), and findings to sign off.
 */
function NeedsTray({ question, unsigned, busy, replying, onPick, onReply, onDismiss, onReview }: {
  question: Question | null; unsigned: number; busy: boolean; replying: boolean;
  onPick(option: string): void; onReply(): void; onDismiss(): void; onReview(): void;
}) {
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); onDismiss(); return; }
    const n = /^[1-9]$/.test(e.key) ? +e.key : 0;
    const o = n && question?.options?.[n - 1];
    if (o && !busy && !(e.target as HTMLElement).closest("input,textarea")) { e.preventDefault(); onPick(o); }
  };
  return (
    <div className="needs" role="group" aria-label="Needs you" onKeyDown={onKey}>
      {question && (
        <div className="needs-q">
          <p className="needs-kicker">A question for the team</p>
          <p className="needs-text">{question.question}</p>
          <div className="needs-actions">
            {question.options?.slice(0, 9).map((o, i) => (
              <button key={o} type="button" className="chip-btn" disabled={busy} onClick={() => onPick(o)}>
                <kbd className="key-hint" aria-hidden>{i + 1}</kbd>{o}
              </button>
            ))}
            <button type="button" className={`chip ${replying ? "is-on" : ""}`} onClick={onReply} aria-pressed={replying}>Answer here</button>
            <button type="button" className="chip" onClick={() => document.querySelector(`[data-question="${CSS.escape(question.id)}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>
              Reply in thread ↑
            </button>
          </div>
        </div>
      )}
      {unsigned > 0 && (
        <p className="needs-row">
          <span>{unsigned === 1 ? "1 finding" : `${unsigned} findings`} to sign off</span>
          <button type="button" className="text-quiet" onClick={onReview}>Review</button>
        </p>
      )}
    </div>
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
