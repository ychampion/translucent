"use client";
// Campaigns this browser has opened, for the sidebar. A per-viewer convenience only: kept in localStorage, never
// shared, and the page works the same without it.
import { useSyncExternalStore } from "react";
import { EXPERIMENTS } from "./campaigns";

export interface Recent {
  href: string;
  title: string;
  /** Live campaign id, when it was followed live. */
  live?: string;
  at: number;
}

const KEY = "translucent.recents";
const listeners = new Set<() => void>();
let cache: Recent[] | null = null;

/** The recorded runs, always listed so a first visit has somewhere to go. */
const RECORDED: Recent[] = EXPERIMENTS.map((x, i) => ({ href: `/c/${x.key}/`, title: x.name, at: -i }));

function read(): Recent[] {
  if (cache) return cache;
  let saved: Recent[] = [];
  try {
    const raw = localStorage.getItem(KEY);
    const v = raw ? JSON.parse(raw) : [];
    if (Array.isArray(v)) saved = v.filter((r) => r && typeof r.href === "string" && typeof r.title === "string");
  } catch { /* storage blocked or bad JSON: start empty */ }
  const seen = new Set(saved.map((r) => r.href));
  cache = [...saved, ...RECORDED.filter((r) => !seen.has(r.href))];
  return cache;
}

/** Remember a campaign (newest first). Recorded runs keep their place; their title is updated in place. */
export function remember(r: Omit<Recent, "at">) {
  const list = read().filter((x) => x.href !== r.href);
  const recorded = RECORDED.some((x) => x.href === r.href);
  const next = recorded
    ? read().map((x) => (x.href === r.href ? { ...x, title: r.title } : x))
    : [{ ...r, at: Date.now() }, ...list].slice(0, 20);
  cache = next;
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* kept in memory */ }
  listeners.forEach((l) => l());
}

const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
export const useRecents = () => useSyncExternalStore(subscribe, read, () => RECORDED);

// ---------------------------------------------------------------- what is running right now (sidebar live dot)

let running: string | null = null;
const runListeners = new Set<() => void>();
/** The href of the campaign running live on this page, or null. */
export function setRunning(href: string | null) {
  if (href === running) return;
  running = href;
  runListeners.forEach((l) => l());
}
export const useRunning = () =>
  useSyncExternalStore((l) => { runListeners.add(l); return () => { runListeners.delete(l); }; }, () => running, () => null);

// ---------------------------------------------------------------- what needs this person (sidebar badges)

/** Per-href count of open questions and findings to sign off, from the campaign open on this page. */
let attention: Record<string, number> = {};
const attentionListeners = new Set<() => void>();
export function setAttention(href: string, n: number) {
  if ((attention[href] ?? 0) === n) return;
  attention = { ...attention, [href]: n };
  attentionListeners.forEach((l) => l());
}
export const useAttention = () =>
  useSyncExternalStore((l) => { attentionListeners.add(l); return () => { attentionListeners.delete(l); }; }, () => attention, () => attention);

/** Live runs that finished while their page was hidden, until they are opened again. Kept in localStorage. */
const UNREAD_KEY = "translucent.unread";
let unread: string[] | null = null;
const unreadListeners = new Set<() => void>();
function readUnread(): string[] {
  if (unread) return unread;
  try {
    const v = JSON.parse(localStorage.getItem(UNREAD_KEY) ?? "[]");
    unread = Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch { unread = []; }
  return unread;
}
function writeUnread(next: string[]) {
  unread = next;
  try { localStorage.setItem(UNREAD_KEY, JSON.stringify(next)); } catch { /* kept in memory */ }
  unreadListeners.forEach((l) => l());
}
export function markUnread(href: string) { if (!readUnread().includes(href)) writeUnread([...readUnread(), href]); }
export function markRead(href: string) { if (readUnread().includes(href)) writeUnread(readUnread().filter((h) => h !== href)); }
const NO_UNREAD: string[] = [];
export const useUnread = () =>
  useSyncExternalStore((l) => { unreadListeners.add(l); return () => { unreadListeners.delete(l); }; }, readUnread, () => NO_UNREAD);
