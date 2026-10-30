"use client";
// The campaign as a conversation: the question, then the agent's steps as messages. Each plan sentence is a headline;
// tool runs fold into quiet one-line steps that open to show what they saw; findings land as cards; questions,
// notes and team actions appear where they happened. New lines are revealed at a steady pace, and the view follows
// the newest line until the reader scrolls up.
import Link from "next/link";
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { experiment, lessonSource } from "@/lib/campaigns";
import type { Event } from "@/lib/contracts";
import { peopleApi } from "@/lib/people";
import type { CampaignStore, CampaignView, Question, StoryLine } from "@/lib/store";
import { reducedMotion, scrollBehavior } from "@/lib/motion";
import { activeSeconds, answerParts, clean, clock, durationWord, isPhrasedTool, keepUnits, money, toolPhrase } from "@/lib/words";
import { FindingCard, StateMark } from "./Findings";
import { FrameStrip } from "./FrameStrip";
import { IconBolt, IconBook, IconChevron, IconDown, IconEye, IconMemory, IconRuler, IconShape, IconWave, Spinner } from "./Icons";
import { Boundary } from "./Status";
import { Initial, useTeam } from "./Team";

// ---------------------------------------------------------------- steady reveal

/**
 * Show lines one at a time at a calm pace; a large jump (a replay skipped ahead, a reload) shows at once. When lines
 * piled up while the page was hidden, `newFrom` is where the reader left off.
 */
function useSteady(target: number, gap = 180) {
  const [shown, setShown] = useState(target);
  const [newFrom, setNewFrom] = useState<number | null>(null);
  const away = useRef(false);
  useEffect(() => {
    const on = () => { if (document.hidden) away.current = true; };
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  useEffect(() => {
    if (shown === target) return;
    if (shown > target || target - shown > 24 || reducedMotion()) {
      if (target - shown > 24 && shown > 0 && away.current) setNewFrom(shown);
      away.current = false;
      setShown(target);
      return;
    }
    const id = setTimeout(() => setShown((s) => Math.min(target, s + 1)), gap);
    return () => clearTimeout(id);
  }, [shown, target, gap]);
  return { shown, newFrom: newFrom != null && newFrom < target ? newFrom : null };
}

// ---------------------------------------------------------------- grouping

type Item =
  | { kind: "step"; line: StoryLine }
  | { kind: "fact"; line: StoryLine }
  | { kind: "memory"; line: StoryLine }
  | { kind: "finding"; line: StoryLine };

type Block =
  | { kind: "agent"; key: string; headline?: string; items: Item[] }
  | { kind: "person"; key: string; author: string; text: string }
  | { kind: "system"; key: string; text: string }
  | { kind: "question"; key: string; q: Question | undefined; text: string };

const CONTROL_WORD: Record<string, string> = { paused: "paused the run", resumed: "resumed the run", stopped: "stopped the run" };

function group(lines: StoryLine[], bySeq: Map<number, Event>, questions: Question[]): Block[] {
  const out: Block[] = [];
  let agent: Extract<Block, { kind: "agent" }> | null = null;
  const into = (item: Item) => {
    if (!agent) { agent = { kind: "agent", key: `a${item.line.key}`, items: [] }; out.push(agent); }
    agent.items.push(item);
  };
  for (const line of lines) {
    switch (line.kind) {
      case "plan":
        agent = { kind: "agent", key: `a${line.key}`, headline: line.title, items: [] };
        out.push(agent);
        break;
      case "action":
        into({ kind: isPhrasedTool(line.tool) ? "step" : "fact", line });
        break;
      case "memory":
        into({ kind: "memory", line });
        break;
      case "finding":
        into({ kind: "finding", line });
        break;
      case "milestone":
        agent = null;
        if (line.title !== "Started") out.push({ kind: "system", key: line.key, text: line.title });
        break;
      case "person": {
        agent = null;
        const e = bySeq.get(line.seq);
        if (e?.type === "campaign.control") out.push({ kind: "system", key: line.key, text: `${line.author ?? "Someone"} ${CONTROL_WORD[line.title] ?? line.title}` });
        else out.push({ kind: "person", key: line.key, author: line.author ?? "Someone", text: line.title });
        break;
      }
      case "question":
        agent = null;
        out.push({ kind: "question", key: line.key, q: questions.find((q) => q.seq === line.seq), text: line.title });
        break;
    }
  }
  return out;
}

/**
 * Runs of the same finished tool become one row: ten "Looked at a frame" lines read as "Looked at 10 frames". A step
 * the replay had no recording for only joins others like it, so the row stays honest about what happened.
 */
function mergeSteps(items: Item[]): Item[] {
  const out: Item[] = [];
  for (const i of items) {
    const prev = out[out.length - 1];
    const a = prev?.line, b = i.line;
    if (prev?.kind === "step" && i.kind === "step" && a.tool === b.tool && !a.running && !b.running
      && !!a.unrecorded === !!b.unrecorded && !!a.failed === !!b.failed) {
      out[out.length - 1] = {
        kind: "step",
        line: { ...a, count: a.count + b.count, runs: [...(a.runs ?? []), ...(b.runs ?? [])], frames: [...(a.frames ?? []), ...(b.frames ?? [])] },
      };
    } else if (prev?.kind === "fact" && i.kind === "fact" && a.title === b.title && !a.frames?.length && !b.frames?.length) {
      out[out.length - 1] = { kind: "fact", line: { ...a, count: a.count + b.count } };
    } else out.push(i);
  }
  return out;
}

/** Opens and closes its content with a short height animation; the content mounts on first open. */
function Reveal({ open, children, className = "" }: { open: boolean; children: ReactNode; className?: string }) {
  const [mounted, setMounted] = useState(open);
  useEffect(() => { if (open) setMounted(true); }, [open]);
  return (
    <div className={`reveal ${open ? "is-open" : ""} ${className}`} aria-hidden={!open} inert={!open}>
      <div className="reveal-inner">{mounted && children}</div>
    </div>
  );
}

// ---------------------------------------------------------------- the thread

interface Props {
  campaignKey: string;
  store: CampaignStore;
  view: CampaignView;
  /** The campaign is moving right now (a playing replay or a running live run). */
  active: boolean;
  selected: string | null;
  onOpenFinding(id: string): void;
  onFrames(frames: number[]): void;
  /** Find in thread: folded steps that match open. */
  query?: string;
}

export const Thread = memo(function Thread({ campaignKey, store, view, active, selected, onOpenFinding, onFrames, query = "" }: Props) {
  const { shown, newFrom } = useSteady(view.story.length);
  const lines = view.story.slice(0, shown);
  const caughtUp = shown >= view.story.length;
  const bySeq = useMemo(() => new Map(view.events.map((e) => [e.seq, e] as const)), [view.events]);
  const runs = useMemo(() => {
    const started = new Map<string, Event>(), finished = new Map<string, Event>();
    for (const e of view.events) {
      const run = e.refs?.[0];
      if (!run) continue;
      if (e.type === "tool.started") started.set(run, e);
      else if (e.type === "tool.finished") finished.set(run, e);
    }
    return { started, finished };
  }, [view.events]);
  const blocks = useMemo(() => group(lines, bySeq, view.questions), [lines, bySeq, view.questions]); // eslint-disable-line react-hooks/exhaustive-deps
  const findings = useMemo(() => new Map(view.findings.map((f) => [f.claim.id, f] as const)), [view.findings]);
  const lastSeq = view.events[view.events.length - 1]?.seq ?? 0;
  const lastAgent = [...blocks].reverse().find((b) => b.kind === "agent");
  const lastLine = lines[lines.length - 1];
  const answer = view.finished && caughtUp ? answerParts(view.answer) : null;
  const holds = view.findings.filter((f) => f.state === "earned" && !f.replacedBy);

  // ---- follow the newest line unless the reader has scrolled up
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [stuck, setStuck] = useState(true);
  const stuckRef = useRef(true);
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onScroll = () => {
      const near = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
      if (near !== stuckRef.current) { stuckRef.current = near; setStuck(near); }
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  useLayoutEffect(() => {
    const el = scroller.current, inner = content.current;
    if (!el || !inner) return;
    const follow = () => { if (stuckRef.current) el.scrollTo({ top: el.scrollHeight, behavior: scrollBehavior() }); };
    const ro = new ResizeObserver(follow);
    ro.observe(inner);
    return () => ro.disconnect();
  }, []);
  const jump = () => {
    const el = scroller.current;
    if (!el) return;
    stuckRef.current = true;
    setStuck(true);
    el.scrollTo({ top: el.scrollHeight, behavior: scrollBehavior() });
  };

  const fresh = (id: string) => {
    const f = findings.get(id);
    return active && !!f && f.reviewedSeq != null && f.reviewedSeq >= lastSeq - 1;
  };

  const ctx: StepCtx = { campaignKey, store, view, runs, onFrames, query: query.trim().toLowerCase() };
  const newKey = newFrom != null ? view.story[newFrom]?.key : undefined;
  const worked = answer ? workedLine(view) : null;

  return (
    <div className="thread-wrap">
    <div className="thread-scroll" ref={scroller}>
      <div className="thread" ref={content}>
        {view.campaign?.objective && (
          <div className="msg msg-user"><p className="bubble">{view.campaign.objective}</p></div>
        )}
        {blocks.map((b) => (
          newKey && blockHas(b, newKey)
            ? [<p key={`new-${b.key}`} className="msg-system is-new">New since you left</p>, renderBlock(b)]
            : renderBlock(b)
        ))}
        {view.hiccup && <p className="msg-system">Connection hiccup, trying again</p>}
        {active && !view.finished && !lastLine?.running && (
          <p className="working"><Spinner size={14} /><span>{liveLine(view, lines)}</span></p>
        )}
        {answer && worked && <p className="msg-system worked num">{worked}</p>}
        {answer && (
          <div className="msg msg-agent msg-answer">
            <p className="answer-lead">{keepUnits(answer.lead)}</p>
            {answer.rest && <p className="answer-rest">{keepUnits(answer.rest)}</p>}
            {holds.length > 0 && (
              <div className="holds">
                <p className="holds-h">What holds</p>
                <ul className="holds-list">
                  {holds.map((f) => (
                    <li key={f.claim.id}>
                      <button type="button" className="holds-row" onClick={() => onOpenFinding(f.claim.id)}>
                        <StateMark state="earned" /><span>{keepUnits(f.claim.statement)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {view.notProven && <NotProven text={clean(view.notProven)} />}
            <Link className="pill-link" href={`/c/${campaignKey}/summary/`}>Full answer</Link>
          </div>
        )}
      </div>
    </div>
    <button type="button" className={`jump ${stuck ? "" : "is-on"}`} onClick={jump} tabIndex={stuck ? -1 : 0} aria-hidden={stuck}>
      <IconDown size={14} />Latest
    </button>
    </div>
  );

  function renderBlock(b: Block) {
    switch (b.kind) {
      case "agent": {
        const steps = mergeSteps(b.items.filter((i) => i.kind !== "finding"));
        const done = b !== lastAgent || view.finished;
        const fold = done && steps.length > 2;
        const shownItems = mergeSteps(b.items);
        return (
          <div key={b.key} className={`msg msg-agent ${done ? "is-done" : ""}`}>
            {b.headline && <p className="headline">{b.headline}</p>}
            {fold ? (
              <>
                <Folded steps={steps} ctx={ctx} />
                {b.items.filter((i) => i.kind === "finding").map((i) => renderFinding(i))}
              </>
            ) : (
              shownItems.map((i) => (i.kind === "finding" ? renderFinding(i) : <StepRow key={i.line.key} item={i} ctx={ctx} />))
            )}
          </div>
        );
      }
      case "person":
        return <PersonMsg key={b.key} author={b.author} text={b.text} />;
      case "system":
        return <p key={b.key} className="msg-system">{b.text}</p>;
      case "question":
        return <QuestionMsg key={b.key} q={b.q} text={b.text} />;
    }
  }

  function renderFinding(i: Item) {
    const f = i.line.findingId ? findings.get(i.line.findingId) : undefined;
    if (!f) return null;
    return (
      <Boundary key={i.line.key} label="This finding">
        <FindingCard f={f} fresh={fresh(f.claim.id)} selected={selected === f.claim.id} onOpen={() => onOpenFinding(f.claim.id)} />
      </Boundary>
    );
  }
});

// ---------------------------------------------------------------- steps

interface StepCtx {
  campaignKey: string;
  store: CampaignStore;
  view: CampaignView;
  runs: { started: Map<string, Event>; finished: Map<string, Event> };
  onFrames(frames: number[]): void;
  /** Lower-cased find query, or "". */
  query: string;
}

const TOOL_ICON: Record<string, ReactNode> = {
  inspect_video: <IconEye size={15} />,
  lookup_calibration: <IconBook size={15} />,
  read_cell_voltage: <IconWave size={15} />,
  segment_images: <IconShape size={15} />,
  track_objects: <IconShape size={15} />,
  measure_growth: <IconRuler size={15} />,
  run_analysis_code: <IconBolt size={15} />,
};

/** Several finished steps, folded into one quiet line that opens to the list. */
function Folded({ steps, ctx }: { steps: Item[]; ctx: StepCtx }) {
  const [userOpen, setOpen] = useState(false);
  const found = !!ctx.query && steps.some((i) => stepWords(i, ctx).toLowerCase().includes(ctx.query));
  const open = userOpen || found;
  const n = steps.reduce((s, i) => s + (i.kind === "memory" ? 1 : i.line.count), 0);
  return (
    <div className={`fold ${open ? "is-open" : ""}`}>
      <button type="button" className="step step-fold" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="step-text">{foldLine(steps, n, ctx)}</span>
        <IconChevron size={13} className="step-chev" />
      </button>
      <Reveal open={open}>
        <div className="fold-body">{steps.map((i) => <StepRow key={i.line.key} item={i} ctx={ctx} />)}</div>
      </Reveal>
    </div>
  );
}

/** "12 steps · 3 min · 1 didn’t work": how many, how long, and what went wrong. */
function foldLine(steps: Item[], n: number, ctx: StepCtx) {
  const parts = [`${n} ${n === 1 ? "step" : "steps"}`];
  const ts = steps.map((i) => i.line.t);
  const lastRun = steps[steps.length - 1]?.line.runs?.slice(-1)[0];
  const end = lastRun ? ctx.runs.finished.get(lastRun)?.t : undefined;
  if (end != null) ts.push(end);
  const secs = activeSeconds(ts);
  if (secs >= 1) parts.push(durationWord(secs));
  const count = (pick: (l: StoryLine) => boolean | undefined) => steps.reduce((s, i) => s + (pick(i.line) ? i.line.count : 0), 0);
  const failed = count((l) => l.failed), unrecorded = count((l) => l.unrecorded);
  if (failed) parts.push(`${failed} didn’t work`);
  if (unrecorded) parts.push(`${unrecorded} not in this recording`);
  return parts.join(" · ");
}

/** The words a step shows, for find in thread. */
function stepWords(i: Item, ctx: StepCtx): string {
  const l = i.line;
  if (i.kind === "step" && l.tool) return `${toolPhrase(l.tool, { running: false, frames: l.frames ?? [] })} ${l.title}`;
  if (i.kind === "memory") return `${l.title} ${(ctx.view.lessonsLoaded ?? []).map((x) => x.text).join(" ")}`;
  return l.title;
}

/** Does this block hold the story line with this key? */
function blockHas(b: Block, key: string) {
  return b.kind === "agent" ? b.key === `a${key}` || b.items.some((i) => i.line.key === key) : b.key === key;
}

/** What the agent is doing right now, when no step of its own is running. */
function liveLine(view: CampaignView, lines: StoryLine[]) {
  if (view.questions.some((q) => q.status === "open")) return "Waiting for the team";
  const running = [...lines].reverse().find((l) => l.running && l.tool);
  if (running) return toolPhrase(running.tool!, { running: true, frames: [] });
  if (view.findings.some((f) => f.state === "checking" && !f.failed)) return "Checking a finding";
  const last = lines[lines.length - 1];
  if (!last || last.kind === "plan") return "Planning the next step";
  const plan = [...lines].reverse().find((l) => l.kind === "plan");
  return plan ? `Working on: ${plan.title.length > 72 ? `${plan.title.slice(0, 70)}…` : plan.title}` : "Working";
}

/** "Worked for 18 min · $0.37", counting only the time it was running. */
export function workedLine(view: CampaignView): string | null {
  const ts = view.events.map((e) => e.t);
  if (ts.length < 2) return null;
  const parts = [`Worked for ${durationWord(activeSeconds(ts))}`];
  if (view.spentUsd != null) parts.push(money(view.spentUsd));
  return parts.join(" · ");
}

/** What a replayed request to the outliner would have done, for a step the recording has no reply for. */
const ASKED: Record<string, string> = {
  segment_images: "Asked SAM 3.1 to outline the images",
  track_objects: "Asked SAM 3.1 to follow the filaments",
};

function StepRow({ item, ctx }: { item: Item; ctx: StepCtx }) {
  const [open, setOpen] = useState(false);
  const line = item.line;
  const ex = experiment(ctx.campaignKey);
  const media = ctx.store.media;

  if (item.kind === "memory") {
    const lessons = ctx.view.lessonsLoaded ?? [];
    const learned = /^Kept a lesson/.test(line.title);
    const canOpen = !learned || !!ctx.view.lessonsLearned.length;
    return (
      <div className={`step-wrap ${open ? "is-open" : ""}`}>
        <button type="button" className="step is-memory" onClick={() => setOpen((o) => !o)} aria-expanded={open} disabled={!canOpen}>
          <span className="step-icon"><IconMemory size={15} /></span>
          <span className="step-text">{learned ? "Kept a lesson for next time" : lessons.length ? `Started with ${lessons.length === 1 ? "a lesson" : `${lessons.length} lessons`} from before` : clean(line.title)}</span>
          {canOpen && <IconChevron size={13} className="step-chev" />}
        </button>
        <Reveal open={open}>
          <div className="step-detail">
            <ul className="lesson-list">
              {(learned ? ctx.view.lessonsLearned : lessons).map((l) => (
                <li key={l.id}>{clean(l.text)}{lessonSource(l) && <span className="soft"> · {lessonSource(l)!.label}</span>}</li>
              ))}
            </ul>
            {!learned && <Link className="pill-link" href={`/c/${ctx.campaignKey}/memory/`}>Memory</Link>}
          </div>
        </Reveal>
      </div>
    );
  }

  if (item.kind === "fact") {
    const frames = line.frames ?? [];
    return (
      <div className="step-wrap">
        {frames.length ? (
          <button type="button" className="step" onClick={() => ctx.onFrames(frames)}>
            <span className="step-icon"><IconEye size={15} /></span>
            <span className="step-text">{clean(line.title)}</span>
          </button>
        ) : (
          <p className="step is-fact">
            <span className="step-icon"><IconRuler size={15} /></span>
            <span className="step-text">{clean(line.title)}{line.count > 1 && <span className="step-count num">{line.count}×</span>}</span>
          </p>
        )}
      </div>
    );
  }

  // A tool step (possibly several runs of the same tool, merged).
  const tool = line.tool!;
  const runIds = line.runs ?? [];
  const startedArgs = runIds.map((r) => (ctx.runs.started.get(r)?.data?.args ?? {}) as Record<string, unknown>);
  const frames = [...new Set(startedArgs.map((a) => a.frame).filter((x): x is number => typeof x === "number"))].sort((a, b) => a - b);
  const last = ctx.runs.finished.get(runIds[runIds.length - 1] ?? "");
  const result = (last?.data?.result ?? {}) as Record<string, unknown>;
  const images = typeof result.images === "number" ? result.images : Array.isArray(result.frames) ? result.frames.length : null;
  const summary = typeof last?.data?.summary === "string" ? last.data.summary : "";
  const detail = line.unrecorded ? "This recorded run has no outline for that request, so the agent carried on without it."
    : line.failed ? "This step didn’t work. The agent carried on without it."
    : tool === "inspect_video" && frames.length ? "" // the frames say it; the summary is playback detail
    : summary ? plainSummary(summary) : "";
  const canOpen = !line.running && (frames.length > 0 || !!detail);
  const text = line.unrecorded && ASKED[tool] ? ASKED[tool] : toolPhrase(tool, { running: !!line.running, frames, images });
  const repeat = line.count > 1 && !(tool === "inspect_video" && frames.length > 1) ? line.count : 0;
  const note = line.unrecorded ? "not in this recording" : line.failed ? "didn’t work" : null;

  return (
    <div className={`step-wrap ${open ? "is-open" : ""}`}>
      <button type="button" className={`step ${line.failed || line.unrecorded ? "is-quiet" : ""} ${line.running ? "is-running" : ""}`}
        onClick={() => canOpen && setOpen((o) => !o)} aria-expanded={canOpen ? open : undefined} disabled={!canOpen}>
        <span className="step-icon">{line.running ? <Spinner size={13} /> : TOOL_ICON[tool]}</span>
        <span className="step-text">
          {text}
          {repeat > 0 && <span className="step-count num">{repeat}×</span>}
          {note && <span className="step-note">{note}</span>}
        </span>
        {canOpen && <IconChevron size={13} className="step-chev" />}
      </button>
      <Reveal open={open}>
        <div className="step-detail">
          {frames.length > 0 && (
            <FrameStrip src={media.video} fps={media.fps} size={media.videoSize} frames={frames.slice(0, 6)}
              region={ex.region} columns={Math.min(frames.length, 6)} className="frames-tight"
              caption={(f) => clock(f / (media.fps || 1))} />
          )}
          {frames.length > 6 && <p className="step-summary">And {frames.length - 6} more.</p>}
          {detail && <p className="step-summary">{detail}</p>}
          {(frames.length > 0 || tool === "read_cell_voltage") && (
            <button type="button" className="pill-link" onClick={() => ctx.onFrames(frames.length ? frames : [0])}>Show in the recording</button>
          )}
        </div>
      </Reveal>
    </div>
  );
}

/** The final answer's list of what it couldn't prove, behind a quiet disclosure. */
function NotProven({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`disclose ${open ? "is-open" : ""}`}>
      <button type="button" className="disclose-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        What it couldn’t prove<IconChevron size={13} className="step-chev" />
      </button>
      <Reveal open={open}><p className="answer-rest disclose-body">{text}</p></Reveal>
    </div>
  );
}

/** A tool's own summary, without ids, pixel sizes or gridline notes. */
function plainSummary(s: string) {
  return clean(s)
    .replace(/\s*Image shows frame[^.]*\./g, "")
    .replace(/\b\d{3,4}x\d{3,4} px,\s*/g, "")
    .replace(/\s*(?:Voltage series|Measurement)\s*\.?$/g, "")
    .replace(/\s*—\s*/g, "; ")
    .trim();
}

// ---------------------------------------------------------------- people

function PersonMsg({ author, text }: { author: string; text: string }) {
  const team = useTeam();
  const mine = !!team?.name && team.name.toLowerCase() === author.toLowerCase();
  if (mine) return <div className="msg msg-user is-note"><p className="bubble">{text}</p></div>;
  return (
    <div className="msg msg-person">
      <Initial name={author} />
      <div><p className="person-name">{author}</p><p className="person-text">{text}</p></div>
    </div>
  );
}

function QuestionMsg({ q, text }: { q: Question | undefined; text: string }) {
  const team = useTeam();
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const open = q?.status === "open" && !!team?.enabled;
  const send = async (answer: string) => {
    if (!team || !q || !answer.trim()) return;
    setBusy(true);
    const ok = await team.act(async (n, k) => team.store.ingest(await peopleApi().answer(team.liveId, q.id, answer.trim(), n, k)));
    setBusy(false);
    if (ok) setReply("");
  };
  return (
    <div className={`msg msg-agent msg-question ${open ? "is-open" : ""}`} data-question={q?.id}>
      <p className="question-kicker">{open ? "A question for the team" : "Asked the team"}</p>
      <p className="headline">{text}</p>
      {open && (
        <>
          {q!.options && (
            <div className="answer-chips">
              {q!.options.map((o) => <button key={o} className="chip-btn" disabled={busy} onClick={() => send(o)}>{o}</button>)}
            </div>
          )}
          <form className="inline-form" onSubmit={(e) => { e.preventDefault(); send(reply); }}>
            <label htmlFor={`reply-${q!.id}`} className="visually-hidden">Your reply</label>
            <input id={`reply-${q!.id}`} className="field" placeholder={q!.options ? "Or write a reply" : "Your reply"} value={reply}
              onChange={(e) => setReply(e.target.value)} maxLength={2000} autoComplete="off" />
            <button className="btn btn-small" disabled={busy || !reply.trim()}>Reply</button>
          </form>
          {team?.notice && <p className="team-notice" role="status">{team.notice}</p>}
        </>
      )}
      {q?.status === "expired" && <p className="soft small">No reply in time. It carried on.</p>}
    </div>
  );
}
