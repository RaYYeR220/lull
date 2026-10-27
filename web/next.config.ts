import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // web/ is its own pnpm project; keep file tracing from walking up to other lockfiles
  outputFileTracingRoot: __dirname,
};

export default nextConfig;
