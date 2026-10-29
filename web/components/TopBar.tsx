import Link from "next/link";

/** The name and mark, for printed pages (the receipt). The app frame carries its own in the sidebar. */
export function Wordmark() {
  return (
    <Link href="/" className="wordmark" aria-label="Translucent, start">
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
        <circle cx="8" cy="10" r="6.5" className="wm-a" />
        <circle cx="12" cy="10" r="6.5" className="wm-b" />
      </svg>
      Translucent
    </Link>
  );
}
