"use client";
import { useEffect, useState, useSyncExternalStore } from "react";
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

/** Subscribe a component to a campaign's store. Returns null until the bundle has loaded. */
export function useCampaign(key: string): { store: CampaignStore; view: CampaignView } | null {
  const [store, setStore] = useState<CampaignStore | null>(null);
  useEffect(() => {
    let live = true;
    // ?live=<campaign id> follows a campaign on the API server instead of the recorded replay.
    const id = new URLSearchParams(window.location.search).get("live");
    if (id && /^[\w.-]{1,128}$/.test(id)) setLive(key, id);
    ensureMock()
      .then(() => getStore(key))
      .then((s) => live && setStore(s), (err) => console.error("Could not load the campaign", err));
    return () => { live = false; };
  }, [key]);
  const view = useSyncExternalStore(
    store ? store.subscribe : noopSubscribe,
    store ? store.getSnapshot : () => NOOP_VIEW,
    () => NOOP_VIEW,
  );
  return store && view ? { store, view } : null;
}

const noopSubscribe = () => () => {};
