import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const backendOrigin = (process.env.FCE_BACKEND_ORIGIN ?? "http://127.0.0.1:8875").replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  distDir: process.env.FCE_NEXT_DIST_DIR ?? ".next",
  reactStrictMode: true,
  turbopack: {
    root
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`
      }
    ];
  }
};

export default nextConfig;
