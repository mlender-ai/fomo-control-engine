import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  distDir: process.env.FCE_NEXT_DIST_DIR ?? ".next",
  reactStrictMode: true,
  turbopack: {
    root
  }
};

export default nextConfig;
