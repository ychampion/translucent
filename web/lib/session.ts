// Who is signed in to the team server, for this browser session. The key is typed once into the sign-in dialog and
// sent once (POST /api/v1/session); the server answers with an HttpOnly cookie that every later call and the event
// stream ride. Nothing about the key is kept in this page or in storage: only the name and role the server returned.
import { useSyncExternalStore } from "react";
import { apiConfigured } from "./sources";

export type Role = "admin" | "engineer" | "viewer" | "planner";
/** GET/POST /api/v1/session (docs M2 contracts §1). */
export interface Who {
  name: string; role: Role; kind: "person" | "agent"; ledgers: "*" | string[]; expires_at?: number | null;
}

export interface SessionState {
  /** unknown: not asked yet; in: signed in; out: no session (or it ended). */
  status: "unknown" | "in" | "out";
  who: Who | null;
  /** The sign-in dialog is open, and why ("Sign in to read this campaign", a 401's sentence). */
  prompt: string | null;
  /** Bumped on every sign-in, so pages that failed with a 401 load again without a reload. */
  epoch: number;
}

let state: SessionState = { status: "unknown", who: null, prompt: null, epoch: 0 };
const listeners = new Set<() => void>();
const emit = (next: Partial<SessionState>) => { state = { ...state, ...next }; for (const l of listeners) l(); };

export const session = {
  get: () => state,
  subscribe(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; },
  /** The server confirmed a session. */
  signedIn(who: Who) { emit({ status: "in", who, prompt: null, epoch: state.epoch + 1 }); },
  /** No session (GET answered 401, or sign out). Does not open the dialog by itself. */
  signedOut() { emit({ status: "out", who: null }); },
  /** A call was refused with 401: the session is gone. Opens sign-in; the page keeps its state underneath. */
  expired(reason = "Sign in to continue. Your page stays as it is.") {
    emit({ status: "out", who: null, prompt: state.prompt ?? reason });
  },
  ask(reason = "Sign in with your team key.") { emit({ prompt: reason }); },
  dismiss() { emit({ prompt: null }); },
  /** Tests only. */
  reset() { state = { status: "unknown", who: null, prompt: null, epoch: 0 }; for (const l of listeners) l(); },
};

const SERVER: SessionState = { status: "unknown", who: null, prompt: null, epoch: 0 };
export function useSession(): SessionState {
  return useSyncExternalStore(session.subscribe, session.get, () => SERVER);
}

/** Roles that may write as a person (approve, amend, conclude, open a free-planner campaign). Viewers only read. */
export const canWrite = (who: Who | null | undefined) => !!who && (who.role === "engineer" || who.role === "admin");

export const ROLE_WORD: Record<Role, string> = { admin: "administrator", engineer: "engineer", viewer: "viewer", planner: "planner" };

const noSubscribe = () => () => {};
/** apiConfigured() for render: false while hydrating (the static HTML was built without a window), then the real
 * answer. Reading window.location during the first render would mismatch the prerendered page, and React would
 * re-render the whole document, dropping the theme set before first paint. */
export function useApiConfigured(): boolean {
  return useSyncExternalStore(noSubscribe, apiConfigured, () => false);
}
