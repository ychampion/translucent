// The v1 API (translucent/api_v1.py): one server, many ledgers. Every call rides the browser session's HttpOnly
// cookie (`credentials: "include"`); the key itself is sent once, to POST /api/v1/session, and never kept here.
// Identity comes from the credential, so no body names an author. Every write carries the CSRF header
// `X-Translucent-Client: web` and an Idempotency-Key: the caller passes one per decision, so trying the same decision
// again (after a timeout, say) is recorded once. A 401 anywhere reopens sign-in; the page keeps its state.
import type { CampaignPage, LedgerInfo, LoopView, Recording, Series, SystemSpecLite } from "./loop";
import { parseLoopFile, recordingUrl } from "./loop";
import type { DatasetDetail, DatasetSummary, PlannerInfo, SystemSpecFull } from "./newcampaign";
import { session, type Who } from "./session";
import { API_BASE } from "./sources";

/** A refused or failed call: the HTTP status (0: the server could not be reached) and the server's own sentence. */
export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) { super(message); }
  /** The approval named a version that is no longer the latest, or the campaign moved on: refresh and decide again. */
  get stale() { return this.status === 409; }
  get unreachable() { return this.status === 0; }
  /** A 422's reasons: the server's sentence split at "; " (the objective budget check joins its problems so). */
  get reasons(): string[] { return this.status === 422 ? this.message.split(/;\s+/).filter(Boolean) : []; }
}

/** The server's sentence from a FastAPI error body: `detail` as a string, `detail.message`, or the first field error. */
export function detailText(body: unknown): string | null {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string" && d.trim()) return d;
  if (d && typeof d === "object" && !Array.isArray(d)) {
    const m = (d as { message?: unknown }).message;
    if (typeof m === "string" && m.trim()) return m;
  }
  if (Array.isArray(d) && d.length) {
    const first = d[0] as { msg?: unknown; loc?: unknown[] };
    if (typeof first?.msg === "string") return Array.isArray(first.loc) ? `${first.loc.slice(1).join(".")}: ${first.msg}` : first.msg;
  }
  return null;
}

function fallback(status: number): string {
  if (status === 0) return "Couldn’t reach the server.";
  if (status === 401) return "Sign in";
  if (status === 403) return "Your role can’t do that here.";
  if (status === 404) return "Couldn’t find that on the server.";
  if (status === 409) return "That changed since you read it.";
  if (status === 429) return "Too many tries. Wait a minute and try again.";
  return `The server refused that (HTTP ${status}).`;
}

/** The header every cookie-authenticated write carries (the server refuses a cookie write without it: CSRF guard). */
export const CLIENT_HEADER = { "X-Translucent-Client": "web" } as const;

type Init = {
  method?: "GET" | "POST" | "PUT"; body?: unknown;
  /** A file's bytes as they are (an upload), instead of a JSON body. */
  raw?: Blob; idem?: string; signal?: AbortSignal; timeout?: number;
  /** A 401 here is an answer, not an expired session: don't open sign-in (GET/POST /session). */
  quiet401?: boolean;
  /** No JSON reply expected (204). */
  empty?: boolean;
};

async function call<T>(path: string, init: Init = {}): Promise<T> {
  const write = (init.method ?? "GET") !== "GET";
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${path}`, {
      method: init.method ?? "GET",
      credentials: "include",
      headers: {
        ...(init.body !== undefined ? { "Content-Type": "application/json" } : init.raw ? { "Content-Type": "application/octet-stream" } : {}),
        ...(write ? { ...CLIENT_HEADER, "Idempotency-Key": init.idem ?? newIdempotencyKey() } : {}),
      },
      body: init.body !== undefined ? JSON.stringify(init.body) : init.raw,
      signal: init.signal ?? AbortSignal.timeout(init.timeout ?? 20000),
    });
  } catch (err) {
    if ((err as { name?: string })?.name === "AbortError") throw err;
    throw new ApiError(0, fallback(0));
  }
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    const err = new ApiError(r.status, detailText(body) ?? fallback(r.status), (body as { detail?: unknown } | null)?.detail);
    if (r.status === 401 && !init.quiet401) session.expired();
    throw err;
  }
  if (init.empty || r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

/** A fresh Idempotency-Key: 1-128 letters, digits or _.:- (the server's rule). Keep it for retries of one decision. */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto;
  if (c?.randomUUID) return `web:${c.randomUUID()}`;
  return `web:${Date.now().toString(36)}.${Math.random().toString(36).slice(2)}`;
}

const L = (lid: string) => `/api/v1/ledgers/${encodeURIComponent(lid)}`;
const C = (lid: string, cid: string) => `${L(lid)}/campaigns/${encodeURIComponent(cid)}`;
const R = (lid: string, cid: string, rq: string) => `${C(lid, cid)}/requests/${encodeURIComponent(rq)}`;

/** A request as the server returns it after a person's action (RequestView, with its content hash). */
export interface RequestReply { request_id: string; version: number; state: string; content_hash: string; [k: string]: unknown }

/** The browser session: sign in once with the key, then the cookie. */
export const sessionApi = {
  /** Who this browser is signed in as, or null (401). Never opens the dialog by itself. */
  async current(signal?: AbortSignal): Promise<Who | null> {
    try {
      return await call<Who>("/api/v1/session", { signal, quiet401: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return null;
      throw err;
    }
  },
  /** Exchange the key for the session cookie. The key goes in this one body and nowhere else. */
  signIn: (key: string) => call<Who>("/api/v1/session", { method: "POST", body: { key }, quiet401: true }),
  signOut: () => call<void>("/api/v1/session/end", { method: "POST", empty: true, quiet401: true }),
};

/** A new loop campaign (POST .../campaigns): title, objective (ObjectiveData without version) and memory. With
 *  records_upload_id, the engineer's raw files uploaded under that id; the server lists them in the objective. */
export interface OpenBody { title: string; objective: Record<string, unknown>; memory?: "on" | "off"; records_upload_id?: string }

export const v1 = {
  ledgers: (signal?: AbortSignal) => call<LedgerInfo[]>("/api/v1/ledgers", { signal }),
  /** The ledger's registered systems: the labels its quantities, parameters and actions carry. */
  systems: (lid: string, signal?: AbortSignal) => call<SystemSpecLite[]>(`${L(lid)}/systems`, { signal }),
  /** The same, with what the New campaign form needs (objective_template, solver, actions with bounds). */
  systemSpecs: (lid: string, signal?: AbortSignal) => call<SystemSpecFull[]>(`${L(lid)}/systems`, { signal }),
  planners: (lid: string, signal?: AbortSignal) => call<PlannerInfo[]>(`${L(lid)}/planners`, { signal }),
  datasets: (lid: string, signal?: AbortSignal) => call<DatasetSummary[]>(`${L(lid)}/datasets`, { signal }),
  dataset: (lid: string, id: string, signal?: AbortSignal) => call<DatasetDetail>(`${L(lid)}/datasets/${encodeURIComponent(id)}`, { signal }),
  campaigns: (lid: string, cursor?: number | null, signal?: AbortSignal) =>
    call<CampaignPage>(`${L(lid)}/campaigns${cursor ? `?cursor=${cursor}` : ""}`, { signal }),
  open: (lid: string, body: OpenBody, idem?: string) =>
    call<{ campaign: { campaign_id: string } & Record<string, unknown>; view: LoopView }>(`${L(lid)}/campaigns`, { method: "POST", body, idem, timeout: 120000 }),
  /** One file into the ledger's upload space (PUT .../uploads/{upload_id}/{name}): a plain name, its bytes as they are. */
  upload: (lid: string, uploadId: string, name: string, file: Blob) =>
    call<{ name: string; bytes: number }>(`${L(lid)}/uploads/${encodeURIComponent(uploadId)}/${encodeURIComponent(name)}`, { method: "PUT", raw: file, timeout: 600000 }),
  view: (lid: string, cid: string, signal?: AbortSignal) => call<LoopView>(`${C(lid, cid)}/view`, { signal }),
  /** A result's (ref: request or result id) or a baseline record's (ref: dataset/record) series, in the system's unit. */
  series: (lid: string, cid: string, ref: string, quantity: string, maxPoints = 1200, signal?: AbortSignal) =>
    call<Series>(`${C(lid, cid)}/series?${new URLSearchParams({ ref, quantity, max_points: String(maxPoints) })}`, { signal }),
  request: (lid: string, cid: string, rq: string) => call<RequestReply>(R(lid, cid, rq)),
  /** Approve the version whose content hash the person read; an older hash is a 409. */
  approve: (lid: string, cid: string, rq: string, body: { content_hash: string; note?: string }, idem?: string) =>
    call<RequestReply>(`${R(lid, cid, rq)}/approve`, { method: "POST", body: { note: "", ...body }, idem }),
  reject: (lid: string, cid: string, rq: string, body: { content_hash: string; reason: string }, idem?: string) =>
    call<RequestReply>(`${R(lid, cid, rq)}/reject`, { method: "POST", body, idem }),
  cancel: (lid: string, cid: string, rq: string, body: { reason: string }, idem?: string) =>
    call<RequestReply>(`${R(lid, cid, rq)}/cancel`, { method: "POST", body, idem }),
  /** A person returns the result of an approved request a person runs: the HumanUpload JSON and, with upload_id,
   *  the files uploaded under it first. A refusal is a 422 whose detail holds the recorded reasons. */
  submitResult: (lid: string, cid: string, rq: string, body: Record<string, unknown>, idem?: string) =>
    call<RequestReply>(`${R(lid, cid, rq)}/results`, { method: "POST", body, idem, timeout: 300000 }),
  /** The engineer amends constraints (and a hard target) as the objective's next version. */
  amend: (lid: string, cid: string, body: { reason: string; constraints?: unknown[]; target?: unknown }, idem?: string) =>
    call<unknown>(`${C(lid, cid)}/amendments`, { method: "POST", body, idem }),
  /** Conclude: the server computes the outcome; the person writes only the text. */
  conclude: (lid: string, cid: string, body: { text: string; unresolved_reasons?: string[] }, idem?: string) =>
    call<unknown>(`${C(lid, cid)}/conclusion`, { method: "POST", body, idem, timeout: 60000 }),
  /** The SSE stream (named events; `loop.recorded` for every loop record). Opened with `withCredentials: true`. */
  eventsUrl: (lid: string, cid: string) => `${API_BASE}${C(lid, cid)}/events`,
  /** The campaign's loop export (a recorded file, `translucent verify`-able). */
  exportUrl: (lid: string, cid: string) => `${API_BASE}${C(lid, cid)}/export`,
  /** The export, read as a recording (the export endpoint's file, never shown as live). */
  exported: async (lid: string, cid: string): Promise<Recording> => parseLoopFile(await call<unknown>(`${C(lid, cid)}/export`, { timeout: 60000 })),
};

/** The event stream, on the session cookie. */
export const openEvents = (lid: string, cid: string) => new EventSource(v1.eventsUrl(lid, cid), { withCredentials: true });

/** A recorded loop export served beside the app, at /fixtures/loop/<name>.loop.json (same origin, not the API). */
export async function loadRecording(name: string, signal?: AbortSignal): Promise<Recording> {
  const r = await fetch(recordingUrl(name), { signal });
  if (!r.ok) throw new ApiError(r.status, r.status === 404 ? `No recording named ${name}.` : fallback(r.status));
  return parseLoopFile(await r.json());
}
