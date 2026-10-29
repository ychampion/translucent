// Links for sharing a finding. The site has no public address yet: NEXT_PUBLIC_SITE_URL sets it at build time, and
// otherwise the address the page was opened from is used.
import { findingHref } from "@/components/Findings";

export function siteUrl(): string {
  const env = process.env.NEXT_PUBLIC_SITE_URL;
  if (env) return env.replace(/\/$/, "");
  return typeof window === "undefined" ? "" : window.location.origin;
}

/** The finding's own page: what the link and the QR code open. */
export const findingUrl = (key: string, id: string) => `${siteUrl()}${findingHref(key, id)}`;

/** The one-page, printable receipt for a finding. */
export const receiptHref = (key: string, id: string) => `/c/${key}/receipt/?id=${encodeURIComponent(id)}`;
