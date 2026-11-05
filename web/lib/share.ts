// Links for sharing a finding. The site has no public address yet: NEXT_PUBLIC_SITE_URL sets it at build time, and
// otherwise the address the page was opened from is used. A link that leaves the page (copied, or in the QR code)
// must carry the base path the site is served under (NEXT_PUBLIC_BASE_PATH, e.g. "/demo"): Next adds it only to its
// own <Link> and router.
import { findingHref } from "@/components/Findings";
import { BASE_PATH } from "./base";

export function siteUrl(): string {
  const env = process.env.NEXT_PUBLIC_SITE_URL;
  if (env) return env.replace(/\/$/, "");
  return typeof window === "undefined" ? "" : window.location.origin;
}

/**
 * An absolute link to a site path: the site's address, then the base path, then the path. A site address that
 * already ends in the base path ("https://x.com/demo") does not get it twice.
 */
export function absoluteUrl(site: string, base: string, path: string): string {
  const s = site.replace(/\/$/, ""), b = base.replace(/\/$/, "");
  return `${b && s.endsWith(b) ? s : s + b}${path}`;
}

/** The finding's own page: what the link and the QR code open. */
export const findingUrl = (key: string, id: string) => absoluteUrl(siteUrl(), BASE_PATH, findingHref(key, id));

/** The one-page, printable receipt for a finding (a site path; Next's <Link> adds the base path). */
export const receiptHref = (key: string, id: string) => `/c/${key}/receipt/?id=${encodeURIComponent(id)}`;
