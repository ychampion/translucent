"use client";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { CampaignStore, type CampaignView } from "./store";
import { getStore, setLive } from "./sources";

const NOOP_VIEW = null;

/** `?mock=<scene>`: install the in-browser demo server (lib/mock.ts) once, before any store opens. */
let mockReady: Promise<void> | null = null;
export function ensureMock(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  const scene = new URLSearchParams(window.location.search).get("mock");
  if (!scene) return Promise.resolve();
  mockReady ??= import("./mock").then((m) => m.installMock(m.sceneOf(scene)));
  return mockReady;
}

export interface CampaignLoad {
  /** null until the bundle has loaded. */
  c: { store: CampaignStore; view: CampaignView } | null;
  /** The bundle could not be loaded (network, missing export, bad JSON). */
  error: unknown;
  /** Try loading it again. */
  retry(): void;
}

/** Subscribe a component to a campaign's store, with the load's error and a way to try again. */
export function useCampaignLoad(key: string): CampaignLoad {
  const [store, setStore] = useState<CampaignStore | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setError(null);
    // ?live=<campaign id> follows a campaign on the API server instead of the recorded replay.
    const id = new URLSearchParams(window.location.search).get("live");
    if (id && /^[\w.-]{1,128}$/.test(id)) setLive(key, id);
    ensureMock()
      .then(() => getStore(key))
      .then((s) => live && setStore(s), (err) => { if (live) setError(err ?? new Error("Could not load the campaign")); });
    return () => { live = false; };
  }, [key, attempt]);
  const view = useSyncExternalStore(
    store ? store.subscribe : noopSubscribe,
    store ? store.getSnapshot : () => NOOP_VIEW,
    () => NOOP_VIEW,
  );
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { c: store && view ? { store, view } : null, error: store ? null : error, retry };
}

/** Subscribe a component to a campaign's store. Returns null until the bundle has loaded. */
export function useCampaign(key: string): { store: CampaignStore; view: CampaignView } | null {
  return useCampaignLoad(key).c;
}

const noopSubscribe = () => () => {};
