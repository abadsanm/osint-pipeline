import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

// Use the real (on-disk) casing so webpack never sees two different paths
const __dirname = fs.realpathSync.native(
  path.dirname(fileURLToPath(import.meta.url))
);

/** @type {import('next').NextConfig} */
const nextConfig = {
  webpack: (config) => {
    // Fix Windows path casing mismatch that causes duplicate module resolution
    config.resolve.symlinks = false;
    config.snapshot = {
      ...config.snapshot,
      managedPaths: [path.join(__dirname, "node_modules")],
    };
    return config;
  },
};

export default nextConfig;
