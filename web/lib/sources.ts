// Event sources. Both feed the same CampaignStore:
//   - replay: a static ReplayBundle export in public/fixtures/<folder>, paced by a player (play / pause / step / jump)
//   - live:   the API server (translucent/server.py). The bundle gives the lookup tables and media; the SSE stream
//             gives events (SSE id = seq, `event:` = event type, data = one contract Event; Last-Event-ID resumes).
import type { Event, ReplayBundle } from "./contracts";
import { fixtureBase } from "./campaigns";
import { CampaignStore, catalogFrom, type Media } from "./store";
import { isRuntimeTool } from "./words";

/**
 * API server for live mode, e.g. http://127.0.0.1:8000. Without NEXT_PUBLIC_API_BASE, a page served locally talks to
 * port 8000 under its own host name (localhost or 127.0.0.1), so the session cookie (SameSite=Strict) is same-site.
 */
export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE
  ?? (typeof window !== "undefined" && /^(localhost|127\.0\.0\.1)$/.test(window.location.hostname)
    ? `http://${window.location.hostname}:8000` : "http://127.0.0.1:8000")).replace(/\/$/, "");

/**
 * Whether to look for the API server at all: an explicit NEXT_PUBLIC_API_BASE, or the default local server while the
 * page itself is served locally (a public static deploy never probes a visitor's machine).
 */
export function apiConfigured(): boolean {
  if (process.env.NEXT_PUBLIC_API_BASE) return true;
  return typeof window !== "undefined" && /^(localhost|127\.0\.0\.1|\[::1\])$/.test(window.location.hostname);
}

/**
 * Where live campaigns come from. Normally the API server; the in-browser demo mock (lib/mock.ts, `?mock=1`) swaps in
 * its own so every screen can be seen without a server.
 */
export interface LiveDriver {
  open(campaignId: string, key: string): Promise<CampaignStore>;
  connect(campaignId: string, store: CampaignStore, onError?: (e: unknown) => void): () => void;
}
let driver: LiveDriver | null = null;
export function setLiveDriver(d: LiveDriver) { driver = d; }

type Bundle = ReplayBundle & { source?: { citation?: string } };

/** JSON from bytes. Exports are UTF-8; a Windows-encoded (cp1252) export is still read correctly. */
async function readJson(r: Response): Promise<unknown> {
  const bytes = await r.arrayBuffer();
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    text = new TextDecoder("windows-1252").decode(bytes);
  }
  return JSON.parse(text);
}

const absolute = (u: string) => /^(https?:|data:|blob:)/.test(u);

/** A replay export: bundle.json. */
export const loadBundle = (key: string) => loadBundleAt(fixtureBase(key));

/** The replay export in one folder under public/fixtures (the compare page loads two by folder). */
export async function loadBundleAt(base: string): Promise<Bundle> {
  const r = await fetch(`${base}/bundle.json`);
  if (!r.ok) throw new Error(`No replay at ${base}/bundle.json`);
  const bundle = (await readJson(r)) as Bundle;
  bundle.events ??= [];
  if (!absolute(bundle.video) && !bundle.video.startsWith("/")) bundle.video = `${base}/${bundle.video}`;
  return bundle;
}

/** Citation for the footage: the design fixtures carry `source`; runtime exports carry it on the video artifact. */
function citationOf(b: Bundle): string | undefined {
  if (b.source?.citation) return b.source.citation;
  const video = (b.artifacts ?? []).find((a) => a.kind === "video" && a.source?.citation);
  return video?.source?.citation ?? undefined;
}

function mediaOf(b: Bundle): Media {
  return {
    video: b.video, videoSize: b.video_size, fps: b.fps,
    tracks: b.tracks ?? [], series: b.series ?? [],
    frameTimes: b.frame_time_s?.length ? b.frame_time_s : null,
    citation: citationOf(b),
  };
}

export function storeFrom(b: Bundle, ingest: boolean) {
  const store = new CampaignStore(catalogFrom(b), mediaOf(b), b.campaign);
  if (ingest) for (const e of [...b.events].sort((a, c) => a.seq - c.seq)) store.ingest(e, false);
  return store;
}

const stores = new Map<string, Promise<CampaignStore>>();
const live = new Map<string, string>(); // campaign key -> live campaign id

/**
 * One store per campaign for the page session, so campaign, finding and summary views share state. Once a key has
 * been opened live (`/c/m4/?live=<campaign id>`), the same key keeps the live store as you move between views.
 */
export function getStore(key: string): Promise<CampaignStore> {
  const liveId = live.get(key);
  const id = liveId ? `live:${liveId}` : key;
  let p = stores.get(id);
  if (!p) {
    p = liveId ? (driver ? driver.open(liveId, key) : openLive(liveId)) : loadBundle(key).then((b) => storeFrom(b, true));
    stores.set(id, p);
    // A failed load is not cached, so Retry fetches again.
    p.catch(() => { if (stores.get(id) === p) stores.delete(id); });
  }
  return p;
}

/** Route this key to a live campaign for the rest of the page session. */
export function setLive(key: string, campaignId: string) {
  live.set(key, campaignId);
}
export const liveIdFor = (key: string) => live.get(key) ?? null;

// ---------------------------------------------------------------- live

const api = (path: string) => `${API_BASE}${path}`;

async function fetchLiveBundle(id: string): Promise<Bundle> {
  const r = await fetch(api(`/api/campaigns/${encodeURIComponent(id)}/bundle`));
  if (!r.ok) throw new Error(`Bundle for ${id}: HTTP ${r.status}`);
  const b = (await readJson(r)) as Bundle;
  if (b.video.startsWith("/")) b.video = api(b.video);
  return b;
}

async function openLive(id: string): Promise<CampaignStore> {
  const b = await fetchLiveBundle(id);
  const store = storeFrom(b, false); // events come from the stream (it replays from seq 0)
  // Events can name claims, measurements or outlines recorded after this bundle was exported: refetch, debounced.
  let timer: ReturnType<typeof setTimeout> | null = null;
  store.onMissing = () => {
    if (timer) return;
    timer = setTimeout(async () => {
      timer = null;
      try {
        const nb = await fetchLiveBundle(id);
        store.refresh(catalogFrom(nb), { tracks: nb.tracks ?? [], series: nb.series ?? [], frameTimes: nb.frame_time_s?.length ? nb.frame_time_s : null });
      } catch (err) {
        console.warn("Live bundle refresh failed", err);
      }
    }, 800);
  };
  return store;
}

const EVENT_TYPES = [
  "campaign.started", "campaign.finished", "memory.loaded", "memory.learned", "step.started", "step.finished",
  "tool.started", "tool.finished", "artifact.created", "measurement.recorded", "claim.proposed", "claim.reviewed",
  "cost.updated",
  // loop campaigns: one per loop record (the loop screen refetches its view on it)
  "loop.recorded",
  // people in the loop
  "human.note", "question.asked", "question.answered", "question.expired",
  "campaign.control", "campaign.paused", "campaign.resumed", "campaign.retry",
] as const;

/**
 * Subscribe to the server's SSE stream. The server names every message with its event type, so each type needs its
 * own listener (`onmessage` only sees unnamed messages). The browser resends Last-Event-ID on reconnect; the server
 * closes the stream once the campaign has finished.
 */
export function connectLive(campaignId: string, store: CampaignStore, onError?: (e: unknown) => void) {
  if (driver) return driver.connect(campaignId, store, onError);
  const es = new EventSource(api(`/api/campaigns/${encodeURIComponent(campaignId)}/events`));
  const handle = (m: MessageEvent) => {
    store.setStream("ok");
    try {
      const e = JSON.parse(m.data) as Event;
      if (e && typeof e.seq === "number" && typeof e.type === "string") store.ingest(e);
    } catch (err) {
      onError?.(err);
    }
  };
  for (const t of EVENT_TYPES) es.addEventListener(t, handle as EventListener);
  es.onmessage = handle;
  es.onopen = () => store.setStream("ok");
  es.onerror = (err) => {
    // A finished campaign's stream ends; don't reconnect forever.
    if (store.getSnapshot().finished) { es.close(); store.setStream("ok"); return; }
    // The browser retries on its own while the stream is CONNECTING; CLOSED means it gave up.
    store.setStream(es.readyState === EventSource.CLOSED ? "lost" : "retrying");
    onError?.(err);
  };
  return () => { es.close(); store.setStream("ok"); };
}

// ---------------------------------------------------------------- replay pacing

/** Paces a replay. Gaps between recorded event times are compressed so a campaign reads in about a minute. */
export class ReplayPlayer {
  private timer: ReturnType<typeof setTimeout> | null = null;
  constructor(private store: CampaignStore, private pace = { min: 650, max: 3200, scale: 0.4 }) {}

  /** Events that change nothing on screen pass quickly. */
  private quiet(e: Event | undefined) {
    if (!e) return false;
    return e.type === "cost.updated" || e.type === "step.finished" && e.actor !== "worker" || e.type === "artifact.created"
      || e.type === "tool.started" && (e.actor === "propose_finding" || e.actor === "record_lesson")
      || e.type === "tool.finished" && isRuntimeTool(e.actor) || e.type === "claim.reviewed" && e.actor !== "gate";
  }

  private delayBefore(i: number) {
    const cur = this.store.eventAt(i), prev = this.store.eventAt(i - 1);
    if (!cur || !prev) return this.pace.min;
    if (this.quiet(cur)) return 120;
    let ms = (cur.t - prev.t) * 1000 * this.pace.scale;
    if (cur.type === "measurement.recorded") ms = 380;
    // Findings get a beat longer so the checks can be read as they land.
    if (cur.type === "claim.reviewed") ms = Math.max(ms, 2400);
    if (cur.type === "claim.proposed") ms = Math.max(ms, 1400);
    return Math.max(cur.type === "measurement.recorded" ? 0 : this.pace.min, Math.min(this.pace.max, ms));
  }

  play() {
    if (this.store.index >= this.store.total) this.store.showUpTo(0);
    this.store.setPlaying(true);
    this.schedule();
  }
  pause() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.store.setPlaying(false);
  }
  toggle() { (this.timer ? this.pause() : this.play()); }
  next() { this.store.showUpTo(this.store.index + 1); }
  prev() { this.store.showUpTo(this.store.index - 1); }
  jump(n: number) { this.store.showUpTo(n); }
  restart() { this.pause(); this.store.showUpTo(0); this.play(); }
  dispose() { if (this.timer) clearTimeout(this.timer); this.timer = null; }

  private lastVerdict = -Infinity;

  private schedule() {
    if (this.store.index >= this.store.total) { this.pause(); return; }
    let wait = this.store.index === 0 ? 600 : this.delayBefore(this.store.index);
    // After a gate verdict, the next visible line waits until its checks have been revealed.
    if (!this.quiet(this.store.eventAt(this.store.index))) wait = Math.max(wait, 2800 - (performance.now() - this.lastVerdict));
    this.timer = setTimeout(() => {
      this.store.showUpTo(this.store.index + 1);
      const shown = this.store.eventAt(this.store.index - 1);
      if (shown?.type === "claim.reviewed" && shown.actor === "gate") this.lastVerdict = performance.now();
      this.schedule();
    }, wait);
  }
}

