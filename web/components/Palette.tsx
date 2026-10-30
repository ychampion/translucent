"use client";
// The command palette (Ctrl+K) and the shortcut sheet (?), both drawn from the command registry.
import { useEffect, useMemo, useRef, useState } from "react";
import { GROUP_ORDER, keyLabel, useCommandList, type Command } from "@/lib/commands";
import { IconClose } from "./Icons";

const usable = (c: Command) => !c.quiet && (!c.when || c.when());

function Keys({ keys }: { keys?: string[] }) {
  if (!keys?.length) return null;
  return <span className="palette-keys">{keys.slice(0, 2).map((k) => <kbd key={k} className="key-hint">{keyLabel(k)}</kbd>)}</span>;
}

/** Filter by every word of the query, in any order, across label, hint and group. */
function matches(c: Command, q: string) {
  const hay = `${c.label} ${c.hint ?? ""} ${c.group}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
}

export function Palette({ onClose }: { onClose(): void }) {
  const all = useCommandList();
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const field = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  // Evaluate `when` once per opening, not per keystroke.
  const open = useMemo(() => all.filter(usable), [all]);
  const shown = useMemo(() => {
    const hits = open.filter((c) => matches(c, q));
    return GROUP_ORDER.flatMap((g) => hits.filter((c) => c.group === g));
  }, [open, q]);
  useEffect(() => { field.current?.focus(); }, []);
  useEffect(() => { setI(0); }, [q]);
  useEffect(() => { list.current?.querySelector(".is-active")?.scrollIntoView({ block: "nearest" }); }, [i]);
  const run = (c: Command | undefined) => { if (!c) return; onClose(); setTimeout(() => c.run(), 0); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setI((x) => Math.min(shown.length - 1, x + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setI((x) => Math.max(0, x - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); run(shown[i]); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
  };
  let group: string | null = null;
  return (
    <div className="sheet palette-sheet" role="dialog" aria-modal="true" aria-label="Commands">
      <div className="sheet-scrim" onClick={onClose} />
      <div className="palette" onKeyDown={onKey}>
        <input ref={field} className="palette-field" placeholder="Search findings and commands" value={q} onChange={(e) => setQ(e.target.value)}
          role="combobox" aria-expanded aria-controls="palette-list" aria-activedescendant={shown[i] ? `cmd-${i}` : undefined} aria-label="Search findings and commands" autoComplete="off" />
        <div className="palette-list" id="palette-list" role="listbox" ref={list}>
          {shown.length === 0 && <p className="palette-empty">Nothing matches.</p>}
          {shown.map((c, k) => {
            const head = c.group !== group ? (group = c.group) : null;
            return (
              <div key={c.id}>
                {head && <p className="palette-group">{head}</p>}
                <button type="button" id={`cmd-${k}`} role="option" aria-selected={k === i} tabIndex={-1}
                  className={`palette-item ${k === i ? "is-active" : ""}`} onMouseMove={() => setI(k)} onClick={() => run(c)}>
                  <span className="palette-label"><span>{c.label}</span>{c.hint && <span className="palette-hint">{c.hint}</span>}</span>
                  <Keys keys={c.keys} />
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function ShortcutSheet({ onClose }: { onClose(): void }) {
  const all = useCommandList();
  const groups = GROUP_ORDER.map((g) => {
    const seen = new Set<string>();
    return [g, all.filter((c) => c.group === g && c.keys?.length && !seen.has(c.label) && seen.add(c.label))] as const;
  }).filter(([, cs]) => cs.length);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.focus(); }, []);
  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="keys-title"
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
      <div className="sheet-scrim" onClick={onClose} />
      <div className="sheet-body keys-sheet" ref={box} tabIndex={-1}>
        <div className="sheet-head">
          <h2 id="keys-title">Keyboard shortcuts</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><IconClose /></button>
        </div>
        <div className="keys-groups">
          {groups.map(([g, cs]) => (
            <section key={g}>
              <h3 className="keys-h">{g}</h3>
              <dl className="keys-list">
                {cs.map((c) => (
                  <div key={c.id} className="keys-row"><dt>{c.label}</dt><dd><Keys keys={c.keys} /></dd></div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
