"use client";
// The rounded composer docked at the bottom of a page: a growing text field, a "+" menu of actions, a quiet chip on
// the left, a label on the right, and one round button that sends, or shows progress, or stops a live run.
// Keys in the field: Enter sends, Shift+Enter starts a new line, Escape hands back to the page (or to onEscape).
import { cloneElement, isValidElement, useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import { IconPause, IconPlay, IconPlus, IconRestart, IconStop, IconUp, Spinner } from "./Icons";
import { MetaMark } from "./MetaMark";

/**
 * The model label on the right ("Muse Wonder", "Muse Spark Open"): Meta's models carry Meta's mark before the name.
 * Pages pass a plain `<span className="model-label">`; anything else is shown as given.
 */
function withMark(right: ReactNode): ReactNode {
  if (!isValidElement(right)) return right;
  const el = right as ReactElement<{ className?: string; children?: ReactNode }>;
  const text = el.props.children;
  if (!el.props.className?.split(/\s+/).includes("model-label") || typeof text !== "string" || !/^Muse\b/.test(text)) return right;
  return cloneElement(el, { className: `${el.props.className} has-mark` }, <MetaMark height={9} />, <span className="model-name">{text}</span>);
}

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
  /** A short progress track in the bottom row (a replay's position), 0 to 1. */
  progress?: number;
  /** Escape in the field (e.g. leave answering a question). */
  onEscape?: () => void;
  /** The field shows text that can't be changed (a recorded question); the button still acts on it. */
  readOnly?: boolean;
  /** What the round button does, for its label (default "Send"). */
  sendLabel?: string;
  /** A recorded run: with no text typed, the round button plays, pauses or restarts the replay. */
  replay?: { playing: boolean; done: boolean; onToggle(): void; onRestart(): void };
}

export function Composer({ value, onChange, onSubmit, placeholder, canSend, busy, onStop, plus, left, right, tray, disabled, autoFocus, label, progress, onEscape, readOnly, sendLabel = "Send", replay }: Props) {
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
  const replaying = !!replay && !value.trim();
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
  ) : replaying ? (
    <button type="button" className="send" onClick={replay!.done ? replay!.onRestart : replay!.onToggle} title="Space"
      aria-label={replay!.done ? "Replay from the start" : replay!.playing ? "Pause the replay" : "Play the replay"}>
      {replay!.playing ? <IconPause size={16} /> : replay!.done ? <IconRestart size={17} /> : <IconPlay size={16} />}
    </button>
  ) : (
    <button type="submit" className="send" disabled={!canSend || disabled} aria-label={sendLabel} title={sendLabel === "Send" ? undefined : sendLabel}>{readOnly ? <IconPlay size={15} /> : <IconUp size={18} />}</button>
  );

  return (
    <div className="composer">
      {/* outside the form: the tray may hold its own form (Join), and HTML ignores a form nested in another */}
      {tray && <div className="composer-tray">{tray}</div>}
      <form className={`composer-box ${disabled ? "is-disabled" : ""}`} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="visually-hidden" htmlFor="composer-field">{label}</label>
        <textarea
          id="composer-field"
          ref={field}
          className="composer-field"
          rows={1}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          readOnly={readOnly}
          maxLength={2000}
          // Grammar extensions and the browser's form restore write to a text field before React hydrates; the
          // value is controlled from state either way.
          suppressHydrationWarning
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
            else if (e.key === "Escape") {
              if (onEscape) { e.stopPropagation(); onEscape(); }
              else if (!menu) e.currentTarget.blur();
            }
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
          {progress != null && (
            <span className="composer-progress" role="progressbar" aria-label="Replay position"
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(1, Math.max(0, progress)) * 100)}>
              <span style={{ transform: `scaleX(${Math.min(1, Math.max(0, progress))})` }} />
            </span>
          )}
          <div className="composer-right">{withMark(right)}</div>
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
