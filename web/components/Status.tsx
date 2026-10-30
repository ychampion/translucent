"use client";
// Loading, error and retry states shared by every page: a spinner that only appears after a short wait, a plain
// "couldn't load" line with Retry, and a boundary that keeps one broken part from blanking the page.
import { Component, useEffect, useState, type ErrorInfo, type ReactNode } from "react";
import { Spinner } from "./Icons";

/** A spinner shown only once loading has taken longer than `delay` ms, so fast loads never flash. */
export function Pending({ delay = 300, label = "Loading" }: { delay?: number; label?: string }) {
  const [shown, setShown] = useState(false);
  useEffect(() => { const id = setTimeout(() => setShown(true), delay); return () => clearTimeout(id); }, [delay]);
  return (
    <div className="center-spin" role="status" aria-live="polite">
      {shown && <><Spinner size={22} /><span className="visually-hidden">{label}</span></>}
    </div>
  );
}

/** "Couldn't load this campaign." with a Retry button. */
export function LoadError({ what = "this campaign", onRetry }: { what?: string; onRetry(): void }) {
  return (
    <div className="center-empty" role="alert">
      <p>Couldn&rsquo;t load {what}.</p>
      <button type="button" className="chip-btn" onClick={onRetry}>Try again</button>
    </div>
  );
}

/** The page body while a load is pending or has failed. */
export function LoadState({ error, onRetry, what }: { error: unknown; onRetry(): void; what?: string }) {
  return error ? <LoadError what={what} onRetry={onRetry} /> : <Pending />;
}

interface BoundaryProps { children: ReactNode; label?: string; className?: string }

/** Catches a render error in its children and shows a quiet line with a way to try again. */
export class Boundary extends Component<BoundaryProps, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error(`${this.props.label ?? "Part of the page"} failed`, error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className={`boundary ${this.props.className ?? ""}`} role="alert">
        <span>{this.props.label ?? "This part"} couldn&rsquo;t be shown.</span>
        <button type="button" className="text-quiet" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    );
  }
}
