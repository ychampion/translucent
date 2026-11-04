// The live event stream's state, as the workspace's connection chip shows it.

/** connecting: first open; live: the stream is open; reconnecting: it dropped, retrying; offline: several retries
 *  failed (still retrying); closed: the server closed a concluded campaign's stream (nothing more will come). */
export type Stream = "connecting" | "live" | "reconnecting" | "offline" | "closed";

/** After this many failed reconnects in a row the chip says "offline" (it keeps retrying). */
const OFFLINE_AFTER = 4;

/**
 * The state of the event stream from its events, as a pure step (tested): an open resets the failures; an error
 * on a concluded campaign closes; otherwise it counts a failure and reads reconnecting, then offline.
 */
export function nextStream(prev: { stream: Stream; failures: number }, e: "open" | "error", concluded: boolean): { stream: Stream; failures: number } {
  if (e === "open") return { stream: "live", failures: 0 };
  if (concluded) return { stream: "closed", failures: prev.failures };
  const failures = prev.failures + 1;
  return { stream: failures >= OFFLINE_AFTER ? "offline" : "reconnecting", failures };
}

