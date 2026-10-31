// The v1 API (translucent/api_v1.py): one server, many ledgers. Reads need no token; people's writes need the team
// token, and name the person as `author`. Every write sends an Idempotency-Key: the caller passes one per decision, so
// trying the same decision again (after a timeout, say) is recorded once.
import type { CampaignPage, LedgerInfo, LoopView, Recording, Series, SystemSpecLite } from "./loop";
import { parseLoopFile, recordingUrl } from "./loop";
import { API_BASE } from "./sources";

/** A refused or failed call: the HTTP status (0: the server could not be reached) and the server's own sentence. */
export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) { super(message); }
  /** The approval named a version that is no longer the latest, or the campaign moved on: refresh and decide again. */
  get stale() { return this.status === 409; }
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
  if (status === 401 || status === 403) return "That team key didn’t work.";
  if (status === 404) return "Couldn’t find that on the server.";
  if (status === 409) return "That changed since you read it.";
  return `The server refused that (HTTP ${status}).`;
}

type Init = { method?: "GET" | "POST"; body?: unknown; key?: string; idem?: string; signal?: AbortSignal; timeout?: number };

async function call<T>(path: string, init: Init = {}): Promise<T> {
  const write = (init.method ?? "GET") !== "GET";
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${path}`, {
      method: init.method ?? "GET",
      headers: {
        ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(init.key ? { Authorization: `Bearer ${init.key}` } : {}),
        ...(write ? { "Idempotency-Key": init.idem ?? newIdempotencyKey() } : {}),
      },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: init.signal ?? AbortSignal.timeout(init.timeout ?? 20000),
    });
  } catch (err) {
    if ((err as { name?: string })?.name === "AbortError") throw err;
    throw new ApiError(0, fallback(0));
  }
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new ApiError(r.status, detailText(body) ?? fallback(r.status), (body as { detail?: unknown } | null)?.detail);
  }
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

export const v1 = {
  ledgers: (signal?: AbortSignal) => call<LedgerInfo[]>("/api/v1/ledgers", { signal }),
  /** The ledger's registered systems: the labels its quantities, parameters and actions carry. */
  systems: (lid: string, signal?: AbortSignal) => call<SystemSpecLite[]>(`${L(lid)}/systems`, { signal }),
  campaigns: (lid: string, cursor?: number | null, signal?: AbortSignal) =>
    call<CampaignPage>(`${L(lid)}/campaigns${cursor ? `?cursor=${cursor}` : ""}`, { signal }),
  view: (lid: string, cid: string, signal?: AbortSignal) => call<LoopView>(`${C(lid, cid)}/view`, { signal }),
  /** A result's (ref: request or result id) or a baseline record's (ref: dataset/record) series, in the system's unit. */
  series: (lid: string, cid: string, ref: string, quantity: string, maxPoints = 1200, signal?: AbortSignal) =>
    call<Series>(`${C(lid, cid)}/series?${new URLSearchParams({ ref, quantity, max_points: String(maxPoints) })}`, { signal }),
  request: (lid: string, cid: string, rq: string) => call<RequestReply>(R(lid, cid, rq)),
  /** Approve the version whose content hash the person read; an older hash is a 409. */
  approve: (lid: string, cid: string, rq: string, body: { author: string; content_hash: string; note?: string }, key: string, idem?: string) =>
    call<RequestReply>(`${R(lid, cid, rq)}/approve`, { method: "POST", body: { note: "", ...body }, key, idem }),
  reject: (lid: string, cid: string, rq: string, body: { author: string; content_hash: string; reason: string }, key: string, idem?: string) =>
    call<RequestReply>(`${R(lid, cid, rq)}/reject`, { method: "POST", body, key, idem }),
  cancel: (lid: string, cid: string, rq: string, body: { author: string; reason: string }, key: string, idem?: string) =>
    call<RequestReply>(`${R(lid, cid, rq)}/cancel`, { method: "POST", body, key, idem }),
  /** The SSE stream (named events; `loop.recorded` for every loop record). */
  eventsUrl: (lid: string, cid: string) => `${API_BASE}${C(lid, cid)}/events`,
  /** The campaign's loop export (a recorded file, `translucent verify`-able). */
  exportUrl: (lid: string, cid: string) => `${API_BASE}${C(lid, cid)}/export`,
  /** The export, read as a recording (the export endpoint's file, never shown as live). */
  exported: async (lid: string, cid: string): Promise<Recording> => parseLoopFile(await call<unknown>(`${C(lid, cid)}/export`, { timeout: 60000 })),
};

/** A recorded loop export served beside the app, at /fixtures/loop/<name>.loop.json (same origin, not the API). */
export async function loadRecording(name: string, signal?: AbortSignal): Promise<Recording> {
  const r = await fetch(recordingUrl(name), { signal });
  if (!r.ok) throw new ApiError(r.status, r.status === 404 ? `No recording named ${name}.` : fallback(r.status));
  return parseLoopFile(await r.json());
}
