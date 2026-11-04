"use client";
// Signing in once per browser session, and the shared modal dialog. The key is typed into the sign-in dialog and sent
// once to POST /api/v1/session; the HttpOnly cookie that comes back carries every later call. The dialog opens by
// itself when any call answers 401, over the page, which keeps its state; signing in reloads what failed.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ApiError, sessionApi } from "@/lib/api";
import { canWrite, ROLE_WORD, session, useSession } from "@/lib/session";
import { apiConfigured } from "@/lib/sources";
import { IconClose, Spinner } from "./Icons";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** A modal dialog: focus moves in and stays in (Tab wraps), Escape and the scrim close it, focus returns on close. */
export function Dialog({ title, onClose, children, wide, describedBy }: { title: string; onClose(): void; children: ReactNode; wide?: boolean; describedBy?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const first = box.current?.querySelector<HTMLElement>("[autofocus], [data-autofocus]") ?? box.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    return () => { before?.focus?.(); };
  }, []);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
    if (e.key !== "Tab" || !box.current) return;
    const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((x) => x.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  return (
    <div className="sheet" onKeyDown={onKey}>
      <div className="sheet-scrim" onClick={onClose} />
      <div ref={box} className={`sheet-body ws-dialog ${wide ? "is-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={id} aria-describedby={describedBy}>
        <div className="sheet-head">
          <h2 id={id}>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><IconClose /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Asks the server once who this browser is signed in as. Mounted once, in the Shell. */
export function useSessionProbe() {
  useEffect(() => {
    if (!apiConfigured()) return;
    const ctl = new AbortController();
    sessionApi.current(ctl.signal)
      .then((who) => { if (who) session.signedIn(who); else session.signedOut(); })
      .catch(() => { /* server unreachable: pages say so where it matters */ });
    return () => ctl.abort();
  }, []);
}

/** The sign-in dialog, shown whenever the session store asks for it. */
export function SignInDialog() {
  const s = useSession();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const descId = useId();
  useEffect(() => { if (s.prompt) { setError(""); setKey(""); } }, [s.prompt]);
  if (!s.prompt) return null;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim()) return;
    setBusy(true);
    setError("");
    try {
      const who = await sessionApi.signIn(key.trim());
      setKey("");
      session.signedIn(who);
    } catch (err) {
      setError(err instanceof ApiError
        ? err.status === 401 ? "That key didn’t work. Check it and try again."
          : err.status === 403 ? (err.message || "This key can’t open a browser session.")
            : err.status === 429 ? "Too many tries. Wait a minute and try again."
              : err.unreachable ? "Couldn’t reach the team server." : err.message
        : "Couldn’t sign in.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title="Sign in to the team server" onClose={() => session.dismiss()} describedBy={descId}>
      <form className="ws-signin" onSubmit={submit}>
        <p id={descId} className="ws-dialog-lead">{s.prompt} Your key is sent once; the server keeps you signed in for this browser session.</p>
        <label className="ws-label" htmlFor="ws-key">Team key</label>
        <input id="ws-key" className="field" type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" data-autofocus spellCheck={false} />
        {error && <p className="lp-error" role="alert">{error}</p>}
        <div className="ws-dialog-foot">
          <button type="button" className="btn btn-small btn-quiet" onClick={() => session.dismiss()}>Not now</button>
          <button type="submit" className="btn btn-small" disabled={!key.trim() || busy}>{busy ? <Spinner size={14} /> : null}Sign in</button>
        </div>
      </form>
    </Dialog>
  );
}

/** Who is signed in and their role, with sign out; or a Sign in button. In the page header, on every page. */
export function Account() {
  const s = useSession();
  const [busy, setBusy] = useState(false);
  if (!apiConfigured() || s.status === "unknown") return null;
  if (!s.who) return <button type="button" className="pill-link ws-account" onClick={() => session.ask()}>Sign in</button>;
  const out = async () => {
    setBusy(true);
    try { await sessionApi.signOut(); } catch { /* the cookie is gone either way once it expires */ }
    session.signedOut();
    setBusy(false);
  };
  return (
    <span className="ws-account is-in" title={canWrite(s.who) ? "Can approve, amend and conclude" : "Read only"}>
      <span className="initial" aria-hidden>{s.who.name.trim().charAt(0).toUpperCase()}</span>
      <span className="ws-account-name">{s.who.name}</span>
      <span className={`ws-role is-${s.who.role}`}>{ROLE_WORD[s.who.role] ?? s.who.role}</span>
      <button type="button" className="text-quiet ws-signout" onClick={out} disabled={busy}>Sign out</button>
    </span>
  );
}

/** A failed load said for what it is: unreachable, signed out, not allowed, not found; with the action that helps. */
export function ApiProblem({ error, what, onRetry }: { error: unknown; what: string; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  const line = !e ? `Couldn’t load ${what}.`
    : e.unreachable ? "The team server didn’t answer. Is it running? This page retries when you ask."
      : e.status === 401 ? `Sign in to read ${what}.`
        : e.status === 403 ? `Your key can’t read ${what}. ${e.message}`
          : e.status === 404 ? `There is no ${what} here, or your key can’t see it.`
            : `Couldn’t load ${what}: ${e.message}`;
  return (
    <div className="center-empty ws-problem" role="alert">
      <p>{line}</p>
      <div className="ws-problem-actions">
        {e?.status === 401 && <button type="button" className="btn btn-small" onClick={() => session.ask()}>Sign in</button>}
        {onRetry && <button type="button" className="chip-btn" onClick={onRetry}>Try again</button>}
      </div>
    </div>
  );
}
