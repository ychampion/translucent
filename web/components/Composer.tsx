"use client";
// The rounded composer docked at the bottom of a page: a growing text field, a "+" menu of actions, a quiet chip on
// the left, a label on the right, and one round button that sends, or shows progress, or stops a live run.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { IconPlus, IconStop, IconUp, Spinner } from "./Icons";

export interface PlusAction { label: string; hint?: string; icon?: ReactNode; onSelect(): void }

interface Props {
  value: string;
  onChange(v: string): void;
  onSubmit(): void;
  placeholder: string;
  /** Sending is allowed (text present and anything else the page needs). */
  canSend: boolean;
  /** Something is in flight: the button shows a spinner. */
  busy?: boolean;
  /** A live run that can be stopped: with no text typed, the button stops it (asks once first). */
  onStop?: () => void;
  plus?: PlusAction[];
  left?: ReactNode;
  right?: ReactNode;
  /** A strip above the field (key entry, a notice). */
  tray?: ReactNode;
  disabled?: boolean;
  autoFocus?: boolean;
  label: string;
  /** A thin progress line along the top edge (a replay's position), 0 to 1. */
  progress?: number;
  /** Escape in the field (e.g. leave answering a question). */
  onEscape?: () => void;
}

export function Composer({ value, onChange, onSubmit, placeholder, canSend, busy, onStop, plus, left, right, tray, disabled, autoFocus, label, progress, onEscape }: Props) {
  const field = useRef<HTMLTextAreaElement>(null);
  const [menu, setMenu] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Grow with the text, up to a few lines.
  useEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);
  useEffect(() => { if (autoFocus) field.current?.focus({ preventScroll: true }); }, [autoFocus]);
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [menu]);
  useEffect(() => {
    if (!confirmStop) return;
    const id = setTimeout(() => setConfirmStop(false), 4000);
    return () => clearTimeout(id);
  }, [confirmStop]);

  const stopping = !!onStop && !value.trim();
  const submit = () => { if (canSend && !busy && !disabled) onSubmit(); };
  const button = busy ? (
    <button type="button" className="send is-busy" disabled aria-label="Working"><Spinner /></button>
  ) : stopping ? (
    <button type="button" className={`send is-stop ${confirmStop ? "is-confirm" : ""}`}
      onClick={() => (confirmStop ? (setConfirmStop(false), onStop!()) : setConfirmStop(true))}
      aria-label={confirmStop ? "Stop for good" : "Stop the run"} title={confirmStop ? "Click again to stop for good" : "Stop"}>
      <IconStop size={16} />
      {confirmStop && <span className="send-confirm">Stop for good?</span>}
    </button>
  ) : (
    <button type="submit" className="send" disabled={!canSend || disabled} aria-label="Send"><IconUp size={18} /></button>
  );

  return (
    <div className="composer">
      {/* outside the form: the tray may hold its own form (Join), and HTML ignores a form nested in another */}
      {tray && <div className="composer-tray">{tray}</div>}
      <form className="composer-box" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {progress != null && progress > 0 && progress < 1 && <span className="composer-progress" aria-hidden><span style={{ transform: `scaleX(${progress})` }} /></span>}
        <label className="visually-hidden" htmlFor="composer-field">{label}</label>
        <textarea
          id="composer-field"
          ref={field}
          className="composer-field"
          rows={1}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          maxLength={2000}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
            else if (e.key === "Escape" && onEscape) { e.stopPropagation(); onEscape(); }
          }}
        />
        <div className="composer-bar">
          {!!plus?.length && (
            <div className="plus" ref={menuRef}>
              <button type="button" className="icon-btn plus-btn" onClick={() => setMenu((m) => !m)} aria-label="More actions" aria-expanded={menu}>
                <IconPlus size={18} />
              </button>
              {menu && (
                <div className="menu plus-menu" role="menu">
                  {plus.map((a) => (
                    <button key={a.label} type="button" role="menuitem" className="menu-item" onClick={() => { setMenu(false); a.onSelect(); }}>
                      {a.icon}
                      <span><span className="menu-label">{a.label}</span>{a.hint && <span className="menu-hint">{a.hint}</span>}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="composer-left">{left}</div>
          <div className="composer-right">{right}</div>
          {button}
        </div>
      </form>
    </div>
  );
}

/** A small popover anchored to a chip in the composer. Closes on outside click and Escape. */
export function ChipMenu({ label, children, className, title }: { label: ReactNode; children: (close: () => void) => ReactNode; className?: string; title?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  return (
    <div className={`chip-menu ${className ?? ""}`} ref={ref}>
      <button type="button" className="chip" onClick={() => setOpen((o) => !o)} aria-expanded={open} title={title}>{label}</button>
      {open && <div className="menu" role="menu">{children(() => setOpen(false))}</div>}
    </div>
  );
}
