import type { NextConfig } from "next";

// Static export: replays are deployed as plain files. Live mode adds an SSE endpoint (NEXT_PUBLIC_EVENTS_URL).
// NEXT_PUBLIC_BASE_PATH (e.g. "/demo") serves the site under a path on another host; unset, it is served at the root.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || undefined;
const nextConfig: NextConfig = {
  output: "export",
  basePath,
  trailingSlash: true,
  images: { unoptimized: true },
  devIndicators: false,
};

export default nextConfig;
