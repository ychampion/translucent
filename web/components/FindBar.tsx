"use client";
// Find in the thread (Ctrl+F): highlights every match in the conversation's text (story lines, finding statements,
// people's notes), steps through them with Enter and Shift+Enter, and opens folded steps that hold a match.
import { useEffect, useRef, useState } from "react";
import { IconChevron, IconClose } from "./Icons";

type Highlights = { set(name: string, h: unknown): void; delete(name: string): void };
const registry = (): Highlights | null =>
  typeof CSS !== "undefined" && "highlights" in CSS && typeof Highlight !== "undefined" ? (CSS as unknown as { highlights: Highlights }).highlights : null;

/** Every case-insensitive match of `q` in the text under `root`, skipping hidden folds. */
function rangesIn(root: Element, q: string): Range[] {
  const out: Range[] = [];
  const needle = q.toLowerCase();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement?.closest("[aria-hidden=true],.visually-hidden,.jump") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = (n.nodeValue ?? "").toLowerCase();
    for (let i = text.indexOf(needle); i >= 0; i = text.indexOf(needle, i + needle.length)) {
      const r = document.createRange();
      r.setStart(n, i);
      r.setEnd(n, i + needle.length);
      out.push(r);
    }
  }
  return out;
}

export function FindBar({ query, onQuery, onClose, version }: {
  query: string; onQuery(q: string): void; onClose(): void;
  /** Changes when the thread's content changes, so matches are found again. */
  version: unknown;
}) {
  const field = useRef<HTMLInputElement>(null);
  const [ranges, setRanges] = useState<Range[]>([]);
  const [at, setAt] = useState(0);
  useEffect(() => { field.current?.focus(); field.current?.select(); }, []);

  // Folds open a render after the query changes; look for matches once they have.
  useEffect(() => {
    const id = setTimeout(() => {
      const root = document.querySelector(".thread");
      setRanges(root && query.trim() ? rangesIn(root, query.trim()) : []);
    }, 80);
    return () => clearTimeout(id);
  }, [query, version]);
  useEffect(() => { setAt(0); }, [query]);

  const current = ranges.length ? ranges[Math.min(at, ranges.length - 1)] : null;
  useEffect(() => {
    const h = registry();
    if (h) {
      h.set("find", new Highlight(...ranges));
      if (current) h.set("find-now", new Highlight(current)); else h.delete("find-now");
    }
    current?.startContainer.parentElement?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [ranges, current]);
  useEffect(() => () => { const h = registry(); h?.delete("find"); h?.delete("find-now"); }, []);

  const step = (d: number) => { if (ranges.length) setAt((i) => (i + d + ranges.length) % ranges.length); };
  return (
    <div className="find-bar" role="search">
      <input ref={field} className="field find-field" placeholder="Find in this campaign" value={query} aria-label="Find in this campaign"
        onChange={(e) => onQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
          else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
        }} />
      <span className="find-count num" aria-live="polite">{query.trim() ? (ranges.length ? `${Math.min(at, ranges.length - 1) + 1} of ${ranges.length}` : "No matches") : ""}</span>
      <button type="button" className="icon-btn" onClick={() => step(-1)} aria-label="Previous match" disabled={!ranges.length}><IconChevron size={15} className="find-up" /></button>
      <button type="button" className="icon-btn" onClick={() => step(1)} aria-label="Next match" disabled={!ranges.length}><IconChevron size={15} className="find-down" /></button>
      <button type="button" className="icon-btn" onClick={onClose} aria-label="Close find"><IconClose size={16} /></button>
    </div>
  );
}
