// One store per campaign. Replay (static bundle) and live (Server-Sent Events) both call `ingest(event)`;
// everything the UI shows is derived from the events received so far plus the bundle's lookup tables.
import type {
  Artifact, Campaign, Claim, Event, Lesson, Measurement, ReplayBundle, Review, Run, Series, Track,
} from "./contracts";
import {
  FAILED_WORD, isMoment, isRuntimeTool, measurementLine, momentSeconds, momentsLine, plainLabel, planLine, toolLine,
} from "./words";

export type FindingState = "checking" | "earned" | "refused" | "not-proven";

export interface Finding {
  claim: Claim;
  review?: Review;
  /** Advisory review by a second model (never changes the state). */
  second?: Review;
  state: FindingState;
  proposedSeq: number;
  reviewedSeq?: number;
  frames: number[];
  replacedBy?: string;
  /** The check itself could not run (tool failure); no verdict. */
  failed?: boolean;
  /** People's sign-offs ("I agree") and challenges, latest per person. Never change the state. */
  people: Review[];
}

export type LineKind = "plan" | "action" | "finding" | "memory" | "milestone" | "person" | "question";

export interface StoryLine {
  key: string;
  seq: number;
  t: number;
  kind: LineKind;
  title: string;
  frames?: number[];
  lessonIds: string[];
  findingId?: string;
  /** Repeated consecutive actions are one line. */
  count: number;
  /** A person's own entry (a note, an answer, a pause): their name. */
  author?: string;
  running?: boolean;
  failed?: boolean;
  /** A replay had no recorded reply for this new request (not a fault in the step itself). */
  unrecorded?: boolean;
  /** The runtime tool behind an action line, and the tool runs folded into it (in order). */
  tool?: string;
  runs?: string[];
}

/** The replay's SAM stand-in found no recorded reply for this prompt ("NoRecording: ..."). */
const NO_RECORDING = /\bNoRecording\b/;

/** Line for a step the replay could not answer: "Outlining each image with Meta SAM 3.1: no recorded outline to replay". */
export const UNRECORDED_WORD = "no recorded outline to replay";

/** A question the agent put to the team. */
export interface Question {
  id: string;
  question: string;
  options: string[] | null;
  status: "open" | "answered" | "expired";
  answer?: string;
  author?: string;
  seq: number;
}

/** Where a live campaign stands. `provider_error`: it stopped after repeated connection trouble. */
export type RunState = "running" | "paused" | "stopped" | "finished" | "provider_error";

export interface CampaignView {
  ready: boolean;
  campaign?: Campaign;
  events: Event[];
  story: StoryLine[];
  findings: Finding[];
  lessonsLoaded: Lesson[] | null;
  lessonsLearned: Lesson[];
  spentUsd: number | null;
  budgetUsd: number | null;
  visibleTracks: string[];
  distinctFrames: number[] | null;
  finished: boolean;
  answer: string | null;
  notProven: string | null;
  questions: Question[];
  /** null until the campaign has started. */
  runState: RunState | null;
  /** The last thing that happened was a retry after a passing provider error. */
  hiccup: boolean;
  /** Which model played each role (planner, pointer, second_opinion), as the run recorded it; null for older runs. */
  models: Record<string, string> | null;
  replay: { playing: boolean; index: number; total: number };
  /** A live event stream's health: "retrying" while the browser reconnects, "lost" once it gave up. */
  stream: StreamState;
}

export type StreamState = "ok" | "retrying" | "lost";

export interface Catalog {
  measurements: Map<string, Measurement>;
  claims: Map<string, Claim>;
  reviews: Map<string, Review>;
  lessons: Map<string, Lesson>;
  /** Tool runs as the bundle recorded them (newer exports); empty for older ones. */
  runs: Map<string, Run>;
  artifacts: Map<string, Artifact>;
}

export interface Media {
  video: string;
  videoSize: [number, number];
  fps: number;
  tracks: Track[];
  series: Series[];
  /** Recorded seconds of each video frame, when the experiment kept a clock. */
  frameTimes: number[] | null;
  citation?: string;
}

const EMPTY_REPLAY = { playing: false, index: 0, total: 0 };

function indexBy<T extends { id: string }>(xs: T[] | undefined) {
  return new Map((xs ?? []).map((x) => [x.id, x] as const));
}

export function catalogFrom(b: Partial<ReplayBundle>): Catalog {
  return {
    measurements: indexBy(b.measurements),
    claims: indexBy(b.claims),
    reviews: indexBy(b.reviews),
    lessons: indexBy(b.lessons),
    runs: indexBy(b.runs),
    artifacts: indexBy(b.artifacts),
  };
}

const asNumbers = (v: unknown): number[] | undefined =>
  Array.isArray(v) && v.every((x) => typeof x === "number") ? (v as number[]) : undefined;

function framesOf(e: Event): number[] | undefined {
  const d = e.data ?? {};
  const list = asNumbers(d.frames);
  if (list?.length) return list;
  const r = asNumbers(d.frame_range);
  if (r && r.length === 2) return [r[0], r[1]];
  return undefined;
}

export function stateFor(review: Review | undefined): FindingState {
  if (!review) return "checking";
  switch (review.verdict) {
    case "supported": return "earned";
    case "refuted":
    case "unsupported": return "refused";
    case "inconclusive": return "not-proven";
  }
}

/** First frame of each distinct captured image, from the recorded clock. */
export function distinctFromTimes(times: number[] | null): number[] | null {
  if (!times?.length) return null;
  const out = [0];
  for (let i = 1; i < times.length; i++) if (times[i] !== times[i - 1]) out.push(i);
  return out.length < times.length ? out : null;
}

export interface DeriveContext {
  media?: Media;
  campaign?: Campaign;
  /** People's reviews posted from this page that the event stream has not delivered (e.g. after it closed). */
  extraReviews?: Review[];
}

/** "person:Ana" -> "Ana"; data.author wins when present. */
export function personOf(e: Event): string {
  const a = e.data?.author;
  if (typeof a === "string" && a.trim()) return a.trim();
  return e.actor.startsWith("person:") ? e.actor.slice(7) : e.actor;
}

/** Keep one review per person (the latest), in arrival order. */
function addPersonReview(f: Finding, r: Review) {
  if (f.people.some((x) => x.id === r.id)) return;
  const who = (r.author ?? "").toLowerCase();
  f.people = [...f.people.filter((x) => (x.author ?? "").toLowerCase() !== who), r];
}

/** Pure derivation: catalog + ordered events -> what the screen shows. */
export function derive(catalog: Catalog, events: Event[], ctx: DeriveContext = {}): Omit<CampaignView, "replay" | "ready" | "stream"> {
  let campaign: Campaign | undefined = ctx.campaign;
  const subjects = (ctx.media?.tracks ?? []).map((t) => t.subject);
  const story: StoryLine[] = [];
  const findings = new Map<string, Finding>();
  const proposedIn = new Map<string, string>(); // claim id -> propose run id
  const lineOfRun = new Map<string, StoryLine>();
  let proposeRun: string | null = null;
  let lessonsLoaded: Lesson[] | null = null;
  const lessonsLearned: Lesson[] = [];
  let spentUsd: number | null = null;
  let budgetUsd: number | null = campaign && campaign.budget_usd > 0 ? campaign.budget_usd : null;
  const visibleTracks: string[] = [];
  let distinctFrames: number[] | null = null;
  let finished = false;
  let finishLine: StoryLine | null = null;
  let answer: string | null = null;
  let notProven: string | null = null;
  const questions = new Map<string, Question>();
  let runState: RunState | null = null;
  let hiccup = false;
  let models: Record<string, string> | null = null;
  // Moments ("Battery shorts at 90 s") from one tool run are gathered on one line per label.
  let momentGroup: { run: string; lines: Map<string, { line: StoryLine; values: number[] }> } | null = null;

  const push = (e: Event, kind: LineKind, title: string, extra: Partial<StoryLine> = {}): StoryLine => {
    const line: StoryLine = { key: String(e.seq), seq: e.seq, t: e.t, kind, title, lessonIds: [], count: 1, ...extra };
    story.push(line);
    return line;
  };

  for (const e of events) {
    const d = (e.data ?? {}) as Record<string, unknown>;
    const lessonIds = (Array.isArray(d.lesson_ids) ? d.lesson_ids : []) as string[];
    if (e.type !== "measurement.recorded" && e.type !== "cost.updated") momentGroup = null;
    // A retry is a passing state: anything the runtime or agent does next clears it.
    if (e.type === "campaign.retry") hiccup = true;
    else if (e.type !== "cost.updated" && !String(e.actor ?? "").startsWith("person:")) hiccup = false;

    switch (e.type) {
      case "campaign.started": {
        if (d.models && typeof d.models === "object") models = d.models as Record<string, string>;
        campaign = (d.campaign as Campaign) ?? campaign;
        if (campaign && campaign.budget_usd > 0) budgetUsd = campaign.budget_usd;
        runState = "running";
        const title = campaign && (e.title === campaign.title || e.title === campaign.objective) ? "Started" : e.title;
        push(e, "milestone", title);
        break;
      }
      case "campaign.finished":
        finished = true;
        runState = d.status === "stopped" ? "stopped" : d.status === "provider_error" ? "provider_error" : "finished";
        answer = typeof d.answer === "string" ? d.answer : null;
        notProven = typeof d.not_proven === "string" ? d.not_proven : null;
        finishLine = push(e, "milestone", e.title);
        break;
      case "memory.loaded": {
        // Design fixtures carry whole lessons; the runtime carries their texts and refs to the recorded lessons.
        const given = Array.isArray(d.lessons) ? (d.lessons as unknown[]) : [];
        const byRef = (e.refs ?? []).map((id) => catalog.lessons.get(id)).filter(Boolean) as Lesson[];
        const ls: Lesson[] = given.length && typeof given[0] === "object" ? (given as Lesson[])
          : byRef.length ? byRef
          : given.filter((x): x is string => typeof x === "string").map((text, i) => ({
            id: e.refs?.[i] ?? `lesson-${e.seq}-${i}`, text, scope: "", evidence_ids: [], learned_in: "", created_at: e.t,
          }));
        lessonsLoaded = ls;
        push(e, "memory", e.title, { lessonIds });
        break;
      }
      case "memory.learned": {
        const l = (d.lesson as Lesson | undefined) ?? catalog.lessons.get((e.refs ?? [])[0] ?? "");
        if (l) lessonsLearned.push(l);
        const raw = l && l.text.startsWith(e.title.slice(0, 40));
        push(e, "memory", raw ? "Kept a lesson for the next campaign" : e.title);
        break;
      }
      case "step.started":
        push(e, "plan", planLine(e), { lessonIds });
        break;
      case "step.finished":
        // A worker's answer to a plan step ("Found 4 places to track") is worth a line; the rest is bookkeeping.
        if (e.actor === "worker" && e.title.length <= 60) push(e, "action", e.title);
        break;
      case "tool.started": {
        const run = (e.refs ?? [])[0] ?? `t${e.seq}`;
        if (e.actor === "propose_finding") proposeRun = run;
        const title = toolLine(e);
        if (title == null) break;
        const last = story[story.length - 1];
        if (last && last.kind === "action" && last.title === title && !last.failed && !last.unrecorded && !last.running) {
          last.count += 1;
          last.running = true;
          last.runs = [...(last.runs ?? []), run];
          lineOfRun.set(run, last);
        } else {
          lineOfRun.set(run, push(e, "action", title, { running: true, frames: framesOf(e), lessonIds, tool: e.actor, runs: [run] }));
        }
        break;
      }
      case "tool.finished": {
        const run = (e.refs ?? [])[0] ?? "";
        const ok = d.status == null || d.status === "ok";
        const unrecorded = !ok && typeof d.summary === "string" && NO_RECORDING.test(d.summary);
        const line = lineOfRun.get(run);
        if (line) {
          line.running = false;
          if (unrecorded) line.unrecorded = true;
          else if (!ok) line.failed = true;
        }
        if (!ok && run === proposeRun) {
          for (const [cid, r] of proposedIn) if (r === run) { const f = findings.get(cid); if (f && !f.review) f.failed = true; }
        }
        if (run === proposeRun) proposeRun = null;
        // Hand-written replays put the result in the finished title ("Nothing found by name"): show it as a line.
        if (!isRuntimeTool(e.actor) && (!line || e.title !== line.title)) {
          push(e, "action", e.title, { frames: framesOf(e), lessonIds, failed: !ok });
        }
        if (typeof d.track === "string" && !visibleTracks.includes(d.track)) visibleTracks.push(d.track);
        if (asNumbers(d.distinct_frames)) distinctFrames = asNumbers(d.distinct_frames)!;
        const result = (d.result ?? {}) as Record<string, unknown>;
        if (ok && typeof result.repeats === "number" && result.repeats > 1 && !distinctFrames) {
          distinctFrames = distinctFromTimes(ctx.media?.frameTimes ?? null);
        }
        if (ok && /segment|track/.test(e.actor)) for (const s of subjects) if (!visibleTracks.includes(s)) visibleTracks.push(s);
        break;
      }
      case "measurement.recorded": {
        const m = (d.measurement as Measurement | undefined) ?? catalog.measurements.get((e.refs ?? [])[0] ?? "");
        const generated = m && (e.title === m.label || !e.title);
        if (!m || !generated) { push(e, "action", e.title, { frames: framesOf(e), lessonIds }); break; }
        if (isMoment(m)) {
          const run = m.run_id;
          if (!momentGroup || momentGroup.run !== run) momentGroup = { run, lines: new Map() };
          const label = m.label ?? "Event";
          const g = momentGroup.lines.get(label);
          if (g) { g.values.push(m.value); g.line.title = momentsLine(label, g.values); }
          else momentGroup.lines.set(label, { line: push(e, "action", momentsLine(label, [m.value])), values: [m.value] });
          break;
        }
        const title = measurementLine(m, subjects);
        if (title) push(e, "action", title);
        break;
      }
      case "claim.proposed": {
        let claim = (d.claim as Claim | undefined) ?? catalog.claims.get((e.refs ?? [])[0] ?? "");
        // Live: the claim may not be in the catalog yet. Show its statement until the bundle catches up.
        if (!claim && (e.refs ?? [])[0]) {
          claim = { id: e.refs![0], campaign_id: e.campaign_id, author: e.actor, statement: e.title, created_at: e.t,
            predicate: { type: "exists", params: {}, measurement_ids: [] } };
        }
        if (claim) {
          findings.set(claim.id, { claim, state: "checking", proposedSeq: e.seq, frames: framesOf(e) ?? [], people: [] });
          if (claim.supersedes && findings.has(claim.supersedes)) findings.get(claim.supersedes)!.replacedBy = claim.id;
          if (proposeRun) proposedIn.set(claim.id, proposeRun);
          push(e, "finding", claim.statement.replace(/^Finding:\s*/i, ""), { findingId: claim.id, frames: framesOf(e) });
        }
        break;
      }
      case "claim.reviewed": {
        const review = (d.review as Review | undefined) ?? catalog.reviews.get((e.refs ?? [])[1] ?? "");
        const f = review && findings.get(review.claim_id);
        if (!f || !review) break;
        // Only the gate decides a finding's state; a critic's review is a second opinion, a person's sits beside it.
        if (review.reviewer === "human") {
          addPersonReview(f, { ...review, author: review.author ?? personOf(e) });
        } else if (review.reviewer === "gate") {
          f.review = review;
          f.state = stateFor(review);
          f.reviewedSeq = e.seq;
        } else {
          f.second = review;
        }
        break;
      }
      case "cost.updated":
        if (typeof d.spent_usd === "number") spentUsd = d.spent_usd;
        else if (typeof d.usd === "number") spentUsd = (spentUsd ?? 0) + d.usd;
        budgetUsd = typeof d.budget_usd === "number" && d.budget_usd > 0 ? d.budget_usd : budgetUsd;
        break;
      case "human.note": {
        const text = typeof d.text === "string" ? d.text : e.title;
        if (text) push(e, "person", text, { author: personOf(e) });
        break;
      }
      case "question.asked": {
        const id = typeof d.question_id === "string" ? d.question_id : `q${e.seq}`;
        const text = typeof d.question === "string" ? d.question : e.title;
        const options = Array.isArray(d.options) ? d.options.filter((x): x is string => typeof x === "string") : null;
        questions.set(id, { id, question: text, options: options?.length ? options : null, status: "open", seq: e.seq });
        push(e, "question", text);
        break;
      }
      case "question.answered": {
        const q = questions.get(String(d.question_id ?? ""));
        const text = typeof d.text === "string" ? d.text : e.title;
        if (q) { q.status = "answered"; q.answer = text; q.author = personOf(e); }
        if (text) push(e, "person", text, { author: personOf(e) });
        break;
      }
      case "question.expired": {
        const q = questions.get(String(d.question_id ?? ""));
        if (q && q.status === "open") q.status = "expired";
        push(e, "action", "No reply yet, carried on");
        break;
      }
      case "campaign.control": {
        const word = ({ pause: "paused", resume: "resumed", stop: "stopped" } as Record<string, string>)[String(d.action)];
        if (word) push(e, "person", word, { author: personOf(e) });
        break;
      }
      case "campaign.paused":
        if (!finished) runState = "paused";
        break;
      case "campaign.resumed": {
        if (d.models && typeof d.models === "object") models = d.models as Record<string, string>;
        // After a pause it just carries on; after a stop or an interruption it picks up from its records.
        const picked = finished || runState == null;
        // A run picked up from its records gets its own budget on top of what was already spent.
        if (d.by === "runtime" && typeof d.budget_usd === "number" && d.budget_usd > 0) {
          budgetUsd = (spentUsd ?? 0) + d.budget_usd;
        }
        finished = false;
        answer = null;
        notProven = null;
        runState = "running";
        if (picked) push(e, "milestone", "Picked up where it left off");
        break;
      }
      default:
        // campaign.retry is handled above; unknown types are ignored.
        break;
    }
  }
  for (const r of ctx.extraReviews ?? []) {
    const f = findings.get(r.claim_id);
    if (f && r.reviewer === "human") addPersonReview(f, r);
  }

  // A check that crashed is not a verdict. If the same finding was put to the check again, the crashed attempt is
  // dropped; otherwise it stays open as not proven.
  const list = [...findings.values()];
  const dropped = new Set<string>();
  for (const f of list) {
    if (!f.failed || f.review) continue;
    const retried = list.some((g) => g !== f && g.proposedSeq > f.proposedSeq && g.claim.statement === f.claim.statement);
    if (retried) dropped.add(f.claim.id);
    else f.state = "not-proven";
  }
  if (finished) for (const f of list) if (f.state === "checking") f.state = "not-proven";
  const shownFindings = list.filter((f) => !dropped.has(f.claim.id));
  const shownStory = story.filter((l) => !(l.findingId && dropped.has(l.findingId)));
  for (const l of shownStory) {
    if (l.failed) l.title = `${l.title.replace(/\s*[—–-]\s*failed$/i, "")}: ${FAILED_WORD}`;
    else if (l.unrecorded) l.title = `${l.title}: ${UNRECORDED_WORD}`;
  }

  // Stopped by a person: their own line already says so.
  const stoppedBy = story.some((l) => l.kind === "person" && l.title === "stopped" && finishLine && l.seq < finishLine.seq);
  if (finishLine && runState === "stopped" && stoppedBy) shownStory.splice(shownStory.indexOf(finishLine), 1);
  else if (finishLine && runState === "stopped") finishLine.title = "Stopped";
  else if (finishLine && runState === "provider_error") finishLine.title = "Stopped: the model could not be reached";
  else if (finishLine && /^Campaign finished$/i.test(finishLine.title)) {
    const n = (s: FindingState) => shownFindings.filter((f) => f.state === s).length;
    const parts = [`${n("earned")} supported`];
    if (n("refused")) parts.push(`${n("refused")} turned down`);
    if (n("not-proven")) parts.push(`${n("not-proven")} not proven yet`);
    finishLine.title = `Finished: ${parts.join(", ")}`;
  }

  return {
    campaign, events, story: shownStory, findings: shownFindings, lessonsLoaded, lessonsLearned,
    spentUsd, budgetUsd, visibleTracks, distinctFrames, finished, answer, notProven,
    questions: [...questions.values()], runState, hiccup: hiccup && !finished, models,
  };
}

/** A moment on the recording's clock that a finding is about, with that finding's state. */
export interface Moment {
  t: number;
  label: string;
  state: FindingState;
  findingId: string;
  kind: "short" | "recovery" | "below-zero" | "other";
}

export function momentKind(m: Measurement): Moment["kind"] {
  if (/collapse|short/.test(m.quantity)) return "short";
  if (/recover/.test(m.quantity)) return "recovery";
  if (/below 0 V/i.test(m.label ?? "")) return "below-zero";
  return "other";
}

/** Short tag for a moment on the trace: "First charge: below 0 V" -> "Below 0 V". */
export function momentLabel(m: Measurement): string {
  const label = plainLabel(m.label ?? "Event");
  const tail = label.split(": ").pop() ?? label;
  return tail.charAt(0).toUpperCase() + tail.slice(1);
}

const RANK: Record<FindingState, number> = { earned: 3, checking: 2, "not-proven": 1, refused: 0 };

/** Moments cited by findings (not refused ones), best state first per moment. */
export function momentsOf(findings: Finding[], catalog: Catalog): Moment[] {
  const byId = new Map<string, Moment>();
  for (const f of findings) {
    if (f.state === "refused") continue;
    for (const id of f.claim.predicate.measurement_ids) {
      const m = catalog.measurements.get(id);
      const t = momentSeconds(m);
      if (t == null || !m) continue;
      const prev = byId.get(id);
      if (prev && RANK[prev.state] >= RANK[f.state]) continue;
      byId.set(id, { t, label: momentLabel(m), state: f.state, findingId: f.claim.id, kind: momentKind(m) });
    }
  }
  return [...byId.values()].sort((a, b) => a.t - b.t);
}

type Listener = () => void;

export class CampaignStore {
  catalog: Catalog;
  media: Media;
  /** Every event the source has (replay: the whole bundle; live: what has arrived). */
  private received: Event[] = [];
  private shown = 0;
  private view: CampaignView;
  private listeners = new Set<Listener>();
  private playing = false;
  private initialCampaign?: Campaign;
  /** Live: called when an event names something the catalog doesn't have yet. */
  onMissing?: () => void;
  /** People's reviews posted from this page, shown until (and after) the stream delivers them. */
  private localReviews: Review[] = [];

  constructor(catalog: Catalog, media: Media, initialCampaign?: Campaign) {
    this.catalog = catalog;
    this.media = media;
    this.initialCampaign = initialCampaign;
    this.view = { ...derive(catalog, [], this.ctx()), ready: true, replay: EMPTY_REPLAY, stream: "ok" };
  }

  private ctx(): DeriveContext { return { media: this.media, campaign: this.initialCampaign, extraReviews: this.localReviews }; }

  /** A person's review the server accepted (POST .../reviews). */
  addReview(r: Review) {
    if (this.localReviews.some((x) => x.id === r.id)) return;
    this.localReviews.push(r);
    this.emit();
  }

  subscribe = (l: Listener) => { this.listeners.add(l); return () => { this.listeners.delete(l); }; };
  getSnapshot = () => this.view;

  /** Add an event (live or replay). Duplicates by seq are ignored, order is by seq. */
  ingest(e: Event, show = true) {
    if (this.received.some((x) => x.seq === e.seq)) return;
    this.received.push(e);
    this.received.sort((a, b) => a.seq - b.seq);
    if (show) this.shown = this.received.length;
    if (this.onMissing && this.refersToUnknown(e)) this.onMissing();
    this.emit();
  }

  /** Live: the bundle caught up; swap in its tables and re-derive. */
  refresh(catalog: Catalog, media: Partial<Media>) {
    this.catalog = catalog;
    this.media = { ...this.media, ...media };
    this.emit();
  }

  private refersToUnknown(e: Event) {
    const r = e.refs ?? [];
    if (e.type === "claim.proposed") return !e.data?.claim && !!r[0] && !this.catalog.claims.has(r[0]);
    if (e.type === "measurement.recorded") return !!r[0] && !this.catalog.measurements.has(r[0]);
    if (e.type === "memory.learned") return !e.data?.lesson && !!r[0] && !this.catalog.lessons.has(r[0]);
    if (e.type === "tool.finished") return /segment|track/.test(e.actor) && !this.media.tracks.length;
    return false;
  }

  /** Show the first n received events (replay cursor). */
  showUpTo(n: number) {
    this.shown = Math.max(0, Math.min(n, this.received.length));
    this.emit();
  }

  setPlaying(p: boolean) { this.playing = p; this.emit(); }

  private stream: StreamState = "ok";
  /** Live: the event stream's health, set by the source. */
  setStream(s: StreamState) {
    if (s === this.stream) return;
    this.stream = s;
    this.view = { ...this.view, stream: s };
    this.listeners.forEach((l) => l());
  }
  get total() { return this.received.length; }
  get index() { return this.shown; }
  eventAt(i: number) { return this.received[i]; }

  private emit() {
    const derived = derive(this.catalog, this.received.slice(0, this.shown), this.ctx());
    this.view = {
      ...derived,
      ready: true,
      replay: { playing: this.playing, index: this.shown, total: this.received.length },
      stream: this.stream,
    };
    this.listeners.forEach((l) => l());
  }
}
