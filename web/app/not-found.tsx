import type { Metadata } from "next";
import Link from "next/link";
import { LogoMark } from "@/components/Logo";
import { PageHeader } from "@/components/Shell";
import { FASTCHARGE_HREF, RECORDS_TO_DESIGN_HREF } from "@/lib/loop";

export const metadata: Metadata = { title: "Page not found – Translucent" };

/** Any address the site doesn't have: in the app's frame and theme, with the way back. */
export default function NotFound() {
  return (
    <div className="screen">
      <PageHeader />
      <main className="nf-main">
        <Link href="/" className="wordmark nf-mark" aria-label="Translucent, home"><LogoMark size={28} />Translucent</Link>
        <p className="nf-code num">404</p>
        <h1 className="nf-title">This page doesn&rsquo;t exist</h1>
        <p className="nf-lead">The link may be old, or the address mistyped. The recorded campaigns are all still here.</p>
        <div className="nf-actions">
          <Link href="/" className="btn">Home</Link>
          <Link href={RECORDS_TO_DESIGN_HREF} className="btn btn-quiet">Watch a recorded campaign</Link>
        </div>
        <p className="nf-more">
          <Link className="text-link" href={FASTCHARGE_HREF}>Faster LG M50 charge</Link>
          <span aria-hidden> · </span>
          <Link className="text-link" href="/examples/">Other recorded examples</Link>
        </p>
      </main>
    </div>
  );
}
