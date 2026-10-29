"use client";
// The people side of a live campaign: who you are, a note to the agent, its questions, pause / resume / stop, and
// signing off or challenging a finding. Only the checker decides a finding; nothing here changes its verdict.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { Review } from "@/lib/contracts";
import { PeopleError, identity, peopleApi } from "@/lib/people";
import type { CampaignStore, Finding } from "@/lib/store";
import { STATE_WORD } from "@/lib/words";

interface Team {
  liveId: string;
  store: CampaignStore;
  name: string;
  key: string;
  joined: boolean;
  /** People's actions are switched on at the server. */
  enabled: boolean;
  /** The server lost this run mid-way (restart): it can be picked up again. */
  interrupted: boolean;
  notice: string;
  join(name: string, key: string): void;
  forget(): void;
  /** Run an action as this person; failures become one plain line. Resolves true on success. */
  act(fn: (name: string, key: string) => Promise<unknown>): Promise<boolean>;
  askJoin(): void;
  markResumed(): void;
  joinRef: React.RefObject<HTMLInputElement | null>;
}

const TeamContext = createContext<Team | null>(null);
export const useTeam = () => useContext(TeamContext);

export function TeamProvider({ liveId, store, children }: { liveId: string; store: CampaignStore; children: ReactNode }) {
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [interrupted, setInterrupted] = useState(false);
  const [notice, setNotice] = useState("");
  const joinRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setName(identity.name());
    setKey(identity.key());
    const api = peopleApi();
    api.session().then((s) => setEnabled(s.team), () => {});
    api.jobStatus(liveId).then((s) => setInterrupted(s === "interrupted" || s === "failed"), () => {});
  }, [liveId]);

  const join = useCallback((n: string, k: string) => {
    identity.setName(n);
    identity.setKey(k);
    setName(identity.name());
    setKey(identity.key());
    setNotice("");
  }, []);
  const forget = useCallback(() => { identity.setKey(""); setKey(""); }, []);
  const askJoin = useCallback(() => { setNotice("Add your name and key first."); joinRef.current?.focus(); }, []);

  const act = useCallback(async (fn: (n: string, k: string) => Promise<unknown>) => {
    if (!name || !key) { askJoin(); return false; }
    try {
      await fn(name, key);
      setNotice("");
      return true;
    } catch (err) {
      setNotice(err instanceof PeopleError ? err.message : "Something went wrong. Try again.");
      return false;
    }
  }, [name, key, askJoin]);

  const markResumed = useCallback(() => setInterrupted(false), []);
  const value: Team = {
    liveId, store, name, key, joined: !!name && !!key, enabled, interrupted, notice, join, forget, act, askJoin, markResumed, joinRef,
  };
  return <TeamContext.Provider value={value}>{children}</TeamContext.Provider>;
}

/** First letter of a name, for a small round badge. */
export function Initial({ name }: { name: string }) {
  return <span className="initial" aria-hidden>{(name.trim()[0] ?? "?").toUpperCase()}</span>;
}

// ---------------------------------------------------------------- sign off or challenge a finding

const names = (rs: Review[]) => rs.map((r) => r.author || "Someone").join(", ");

/** "Signed off by Ana" and "Challenged by Ben: reason". Shown beside the checker's verdict, never instead of it. */
export function PeopleLine({ f, compact }: { f: Finding; compact?: boolean }) {
  const agreed = f.people.filter((r) => r.verdict === "supported");
  const challenged = f.people.filter((r) => r.verdict !== "supported");
  if (!f.people.length) return null;
  return (
    <div className={`people ${compact ? "is-compact" : ""}`}>
      {agreed.length > 0 && (
        <p className="people-row is-agree">
          <span className="initials" aria-hidden>{agreed.map((r) => <Initial key={r.id} name={r.author ?? "?"} />)}</span>
          Signed off by {names(agreed)}
        </p>
      )}
      {challenged.map((r) => (
        <p key={r.id} className="people-row is-challenge">
          <span className="initials" aria-hidden><Initial name={r.author ?? "?"} /></span>
          <span>Challenged by {r.author || "someone"}{r.reason ? <span className="people-reason">: {r.reason}</span> : null}</span>
        </p>
      ))}
    </div>
  );
}

/** Receipt lines: "Signed off by Ana · checker: earned". The checker's word is always printed beside a person's. */
export function ReceiptPeople({ f }: { f: Finding }) {
  if (!f.people.length) return null;
  const checker = <span className="receipt-checker">checker: {STATE_WORD[f.state].toLowerCase()}</span>;
  const agreed = f.people.filter((r) => r.verdict === "supported");
  const challenged = f.people.filter((r) => r.verdict !== "supported");
  return (
    <div className="receipt-people">
      {agreed.length > 0 && <p>Signed off by {names(agreed)} · {checker}</p>}
      {challenged.map((r) => (
        <p key={r.id} className="is-challenge">Challenged by {r.author || "someone"}{r.reason ? `: ${r.reason}` : ""} · {checker}</p>
      ))}
    </div>
  );
}

export function FindingActions({ f }: { f: Finding }) {
  const team = useTeam();
  const [mode, setMode] = useState<"idle" | "challenge">("idle");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => { if (mode === "challenge") field.current?.focus(); }, [mode]);
  // Signing off or challenging is about what the checker earned; refused findings already carry the checker's reason.
  if (!team || !team.enabled || f.state !== "earned") return null;
  const mine = f.people.find((r) => (r.author ?? "").toLowerCase() === team.name.toLowerCase() && team.name);

  const send = async (verdict: "agree" | "challenge") => {
    if (verdict === "challenge" && !reason.trim()) return;
    setBusy(true);
    const ok = await team.act(async (n, k) => team.store.addReview(await peopleApi().review(team.liveId, f.claim.id, verdict, reason.trim(), n, k)));
    setBusy(false);
    if (ok) { setMode("idle"); setReason(""); }
  };

  if (mode === "challenge") {
    return (
      <form className="inline-form finding-challenge" onSubmit={(e) => { e.preventDefault(); send("challenge"); }}
        onKeyDown={(e) => { if (e.key === "Escape") setMode("idle"); }}>
        <label htmlFor={`why-${f.claim.id}`} className="visually-hidden">Why do you challenge it?</label>
        <input id={`why-${f.claim.id}`} ref={field} className="field" placeholder="Why? One line" value={reason}
          onChange={(e) => setReason(e.target.value)} maxLength={300} autoComplete="off" />
        <button className="btn btn-small" disabled={busy || !reason.trim()}>Challenge</button>
        <button type="button" className="text-quiet" onClick={() => setMode("idle")}>Cancel</button>
      </form>
    );
  }
  if (mine) return null;
  return (
    <div className="finding-actions">
      <button className="chip-btn" disabled={busy} onClick={() => send("agree")}>Sign off</button>
      <button className="chip-btn" disabled={busy} onClick={() => (team.joined ? setMode("challenge") : team.askJoin())}>Challenge</button>
    </div>
  );
}
