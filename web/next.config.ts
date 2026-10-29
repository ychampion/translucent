import type { NextConfig } from "next";

// Static export: replays are deployed as plain files. Live mode adds an SSE endpoint (NEXT_PUBLIC_EVENTS_URL).
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  devIndicators: false,
};

export default nextConfig;
