import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  webpack: (config) => {
    // Fix Windows path casing mismatch that causes duplicate module resolution
    config.resolve.symlinks = false;
    config.snapshot = {
      ...config.snapshot,
      managedPaths: [path.resolve(__dirname, "node_modules")],
    };
    return config;
  },
};

export default nextConfig;
