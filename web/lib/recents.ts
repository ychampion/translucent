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
