import Link from "next/link";
import { LogoMark } from "./Logo";

/** The name and mark, for printed pages (the receipt). The app frame carries its own in the sidebar. */
export function Wordmark() {
  return (
    <Link href="/" className="wordmark" aria-label="Translucent, start">
      <LogoMark size={20} />
      Translucent
    </Link>
  );
}
