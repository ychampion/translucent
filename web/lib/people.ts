// People in the loop: the team's actions on a live campaign. People steer, answer,
// sign off and challenge; only the checker decides whether a finding holds. Nothing here can change a verdict.
import type { Campaign, Event, Review } from "./contracts";
import { EXPERIMENTS, OWN } from "./campaigns";
import { API_BASE } from "./sources";

/** `model`: the planner new campaigns run on (the server picks the profile). */
export interface Session { live: boolean; team: boolean; model?: string }
export interface RecordedExperiment { experiment: string; video_id: string; label: string; citation?: string | null; licence?: string | null }
export type ControlAction = "pause" | "resume" | "stop";

/** A new experiment's records, as docs/BRING-YOUR-OWN.md describes them. Files are named, and uploaded beside it. */
export interface Manifest {
  id: string; title: string; citation: string; licence: string; author: string; video: string;
  scale?: { um_per_px?: number; bar_px?: number; bar_um?: number; uncertainty?: number; source: string };
  clock?: { timestamps_csv?: string; interval_s?: number; uncertainty_s?: number; source: string };
  voltage?: { csv: string; offset_s: number; source: string };
  notes?: string;
}

export interface PeopleApi {
  session(): Promise<Session>;
  experiments(): Promise<RecordedExperiment[]>;
  start(body: { objective: string; experiment_id: string; budget: number }, key: string): Promise<Campaign>;
  note(id: string, text: string, author: string, key: string): Promise<Event>;
  answer(id: string, questionId: string, text: string, author: string, key: string): Promise<Event>;
  review(id: string, claimId: string, verdict: "agree" | "challenge", reason: string, author: string, key: string): Promise<Review>;
  control(id: string, action: ControlAction, author: string, key: string): Promise<Event>;
  resume(id: string, budget: number, key: string): Promise<Campaign>;
  /** The server's job status for a campaign ("interrupted" when the server restarted mid-run), or null. */
  jobStatus(id: string): Promise<string | null>;
  /** One file of a new experiment; `progress` gets the bytes sent so far. */
  upload(uploadId: string, name: string, file: Blob, key: string, progress: (sent: number) => void): Promise<{ name: string; bytes: number }>;
  /** Registers the uploaded files under the manifest. A bad manifest is refused with the server's own sentence. */
  addExperiment(uploadId: string, manifest: Manifest, key: string): Promise<RecordedExperiment>;
}

/** A refused or failed request, with a short line a person can act on. */
export class PeopleError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function plainError(status: number): string {
  if (status === 401 || status === 403) return "That key didn’t work.";
  if (status === 409) return "The campaign isn’t taking that right now.";
  if (status === 404) return "Couldn’t find that campaign.";
  if (status === 422 || status === 400) return "That didn’t go through. Check what you typed.";
  if (status === 0) return "Couldn’t reach the team server.";
  return "Something went wrong. Try again.";
}

/** The server's own sentence for a refused upload or manifest (400, 404, 413, 422), else the plain line. */
function detailOf(status: number, text: string): string {
  if ([400, 404, 413, 422].includes(status)) {
    try {
      const d = (JSON.parse(text) as { detail?: unknown }).detail;
      if (typeof d === "string" && d.trim()) return d;
    } catch { /* not JSON */ }
  }
  return plainError(status);
}

async function call<T>(path: string, init: { method?: string; body?: unknown; key?: string; detail?: boolean; timeout?: number } = {}): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${path}`, {
      method: init.method ?? "GET",
      headers: {
        ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(init.key ? { Authorization: `Bearer ${init.key}` } : {}),
      },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: AbortSignal.timeout(init.timeout ?? 15000),
    });
  } catch {
    throw new PeopleError(0, plainError(0));
  }
  if (!r.ok) throw new PeopleError(r.status, init.detail ? detailOf(r.status, await r.text().catch(() => "")) : plainError(r.status));
  return (await r.json()) as T;
}

const c = (id: string) => `/api/campaigns/${encodeURIComponent(id)}`;

const serverApi: PeopleApi = {
  session: () => call<Session>("/api/session"),
  experiments: () => call<RecordedExperiment[]>("/api/experiments"),
  start: async (body, key) => call<Campaign>("/api/campaigns", { method: "POST", body, key }),
  note: async (id, text, author, key) => (await call<{ event: Event }>(`${c(id)}/notes`, { method: "POST", body: { text, author }, key })).event,
  answer: async (id, question_id, text, author, key) =>
    (await call<{ event: Event }>(`${c(id)}/answers`, { method: "POST", body: { question_id, text, author }, key })).event,
  review: async (id, claim_id, verdict, reason, author, key) =>
    (await call<{ review: Review }>(`${c(id)}/reviews`, { method: "POST", body: { claim_id, verdict, reason, author }, key })).review,
  control: async (id, action, author, key) => (await call<{ event: Event }>(`${c(id)}/control`, { method: "POST", body: { action, author }, key })).event,
  jobStatus: async (id) => (await call<{ job?: { status?: string } | null }>(c(id))).job?.status ?? null,
  resume: async (id, budget, key) => (await call<{ campaign: Campaign }>(`${c(id)}/resume`, { method: "POST", body: { budget }, key })).campaign,
  upload: (uploadId, name, file, key, progress) => new Promise((resolve, reject) => {
    // XHR, not fetch: it reports upload progress.
    const x = new XMLHttpRequest();
    x.open("PUT", `${API_BASE}/api/uploads/${uploadId}/${encodeURIComponent(name)}`);
    x.setRequestHeader("Authorization", `Bearer ${key}`);
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = (e) => progress(e.loaded);
    x.onload = () => {
      if (x.status >= 200 && x.status < 300) {
        try { resolve(JSON.parse(x.responseText)); } catch { resolve({ name, bytes: file.size }); }
      } else reject(new PeopleError(x.status, detailOf(x.status, x.responseText)));
    };
    x.onerror = () => reject(new PeopleError(0, plainError(0)));
    x.send(file);
  }),
  // Registering copies, hashes and reads every frame of the video: give it time.
  addExperiment: (upload_id, manifest, key) =>
    call<RecordedExperiment>("/api/experiments", { method: "POST", body: { upload_id, manifest }, key, detail: true, timeout: 600000 }),
};

let override: PeopleApi | null = null;
/** The demo mock (lib/mock.ts) installs itself here. */
export function setPeopleApi(api: PeopleApi) { override = api; }
export const peopleApi = (): PeopleApi => override ?? serverApi;

// ---------------------------------------------------------------- who you are

const NAME = "translucent.name";
const KEY = "translucent.key";

function read(storage: () => Storage, k: string): string {
  try { return storage().getItem(k) ?? ""; } catch { return ""; }
}
function write(storage: () => Storage, k: string, v: string) {
  try { if (v) storage().setItem(k, v); else storage().removeItem(k); } catch { /* storage blocked: kept in memory only */ }
}

/** In-memory stand-ins (the demo mock), used only when nothing is stored. Never written to storage. */
const seed = { name: "", key: "" };
export function seedIdentity(name: string, key: string) { seed.name = name; seed.key = key; }

/** Your name stays on this device; the access key only for this tab. */
export const identity = {
  name: () => read(() => localStorage, NAME) || seed.name,
  setName: (v: string) => { seed.name = ""; write(() => localStorage, NAME, v.trim().slice(0, 80)); },
  key: () => read(() => sessionStorage, KEY) || seed.key,
  setKey: (v: string) => { seed.key = ""; write(() => sessionStorage, KEY, v.trim()); },
};

/**
 * The app's page for a recorded experiment the server offers: a lab's own recording gets the generic page; a published
 * one is matched by the paper's first author, else the generic page too (another experiment's framing would mislead).
 */
export function keyForExperiment(x: RecordedExperiment): string {
  if (x.experiment.startsWith("user/")) return OWN.key;
  const text = `${x.experiment} ${x.label} ${x.citation ?? ""}`.toLowerCase();
  const hit = EXPERIMENTS.find((e) => new RegExp(String.raw`\b${e.source.split(" ")[0].toLowerCase()}\b`).test(text));
  return (hit ?? OWN).key;
}
