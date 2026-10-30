"use client";
// One registry of things a person can do, by name and by key. Pages and the app frame register their commands while
// mounted; the palette (Ctrl+K), the shortcut sheet (?) and the keyboard all read the same list, so a shortcut can't
// exist without being discoverable.
import { useEffect, useRef, useSyncExternalStore } from "react";

export type CommandGroup = "Findings" | "Campaign" | "People" | "Compare" | "Navigate" | "Help";
export const GROUP_ORDER: CommandGroup[] = ["Findings", "Campaign", "People", "Compare", "Navigate", "Help"];

export interface Command {
  id: string;
  label: string;
  group: CommandGroup;
  /** Shortcuts, e.g. "Ctrl+K", "?", "J", "Space", "ArrowRight". Ctrl also means Cmd on a Mac. */
  keys?: string[];
  /** A second line in the palette. */
  hint?: string;
  /** Hidden from the palette (keys still work), e.g. replay stepping. */
  quiet?: boolean;
  /** Fires even while typing in a field (only for keys with Ctrl). */
  inFields?: boolean;
  /** Checked when the key is pressed or the palette opens; false hides and disables it. */
  when?(): boolean;
  run(): void;
}

const owners = new Map<string, Command[]>();
const listeners = new Set<() => void>();
let snapshot: Command[] = [];
const emit = () => { snapshot = [...owners.values()].flat(); listeners.forEach((l) => l()); };

/** Register (or replace) one owner's commands. Returns a function that removes them. */
export function registerCommands(owner: string, list: Command[]): () => void {
  owners.set(owner, list);
  emit();
  return () => { if (owners.get(owner) === list) { owners.delete(owner); emit(); } };
}

export const allCommands = () => snapshot;
export const useCommandList = () =>
  useSyncExternalStore((l) => { listeners.add(l); return () => { listeners.delete(l); }; }, () => snapshot, () => snapshot);

/**
 * Register commands while a component is mounted. `make` runs on every render so `run` sees fresh state; the list
 * is re-registered only when `deps` change (labels and keys).
 */
export function useCommands(owner: string, make: () => Command[], deps: unknown[]) {
  const latest = useRef(make);
  latest.current = make;
  useEffect(() => {
    const list = latest.current().map((c) => ({
      ...c,
      run: () => latest.current().find((x) => x.id === c.id)?.run(),
      when: c.when ? () => latest.current().find((x) => x.id === c.id)?.when?.() ?? false : undefined,
    }));
    return registerCommands(owner, list);
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
}

const isMac = () => typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

/** Does this key press match a shortcut string? */
export function matchKey(e: KeyboardEvent, key: string): boolean {
  const parts = key.split("+");
  const name = parts.pop()!;
  const ctrl = parts.includes("Ctrl"), shift = parts.includes("Shift"), alt = parts.includes("Alt");
  if (ctrl !== (e.ctrlKey || e.metaKey) || alt !== e.altKey) return false;
  const k = name === "Space" ? " " : name;
  if (k.length === 1 && /[a-z]/i.test(k)) return e.key.toLowerCase() === k.toLowerCase() && shift === e.shiftKey;
  if (k === "?" || k === "/") return e.key === k || (ctrl && e.code === "Slash");
  return e.key === k && shift === e.shiftKey;
}

/** "Ctrl+K" as the reader's platform writes it. */
export function keyLabel(key: string): string {
  return key
    .replace("Ctrl", isMac() ? "⌘" : "Ctrl")
    .replace("ArrowRight", "→").replace("ArrowLeft", "←").replace("ArrowUp", "↑").replace("ArrowDown", "↓")
    .replace("Escape", "Esc");
}

const inField = (t: EventTarget | null) =>
  !!(t as HTMLElement | null)?.closest?.("input,textarea,select,[contenteditable=true],[role=slider]");

/** The one keyboard handler: the first command whose key matches and whose `when` holds runs. */
export function dispatchKey(e: KeyboardEvent): boolean {
  if (e.defaultPrevented || e.isComposing) return false;
  const typing = inField(e.target);
  // Space and arrows on a focused button, tab or resize edge belong to that control.
  const control = /^( |Arrow\w+|Home|End)$/.test(e.key) && !!(e.target as HTMLElement | null)?.closest?.("button,a,[role=tab],[role=separator]");
  for (const c of snapshot) {
    if (!c.keys?.some((k) => matchKey(e, k))) continue;
    if ((typing || control) && !c.inFields) continue;
    if (c.when && !c.when()) continue;
    e.preventDefault();
    c.run();
    return true;
  }
  return false;
}
