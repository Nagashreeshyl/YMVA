import type { NextConfig } from "next";

// In development the dashboard proxies /api to the local FastAPI server.
// In Docker, Caddy routes /api to the API container, so no proxy is needed.
const apiProxyUrl =
  process.env.API_PROXY_URL ??
  (process.env.NODE_ENV === "production" ? undefined : "http://127.0.0.1:8000");

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return apiProxyUrl ? [{ source: "/api/:path*", destination: `${apiProxyUrl}/api/:path*` }] : [];
  },
};

export default nextConfig;
