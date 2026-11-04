// The path the site is served under (NEXT_PUBLIC_BASE_PATH, e.g. "/demo"), or "" at the root. Next's <Link> and router
// add it themselves; a plain fetch or <a href> of a site file must add it with withBase.
export const BASE_PATH = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/$/, "");

/** A same-origin site path (starting with "/") under the base path. */
export const withBase = (path: string) => `${BASE_PATH}${path}`;
