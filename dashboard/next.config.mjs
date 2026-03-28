import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

// Resolve the REAL on-disk casing to prevent webpack duplicate module issues on Windows
const __filename = fileURLToPath(import.meta.url);
const __dirname = fs.realpathSync.native(path.dirname(__filename));

/** @type {import('next').NextConfig} */
const nextConfig = {
  webpack: (config) => {
    // Force webpack to use the real (on-disk) casing for all paths
    config.resolve.symlinks = false;

    // Pin node_modules to real path so webpack doesn't create duplicates
    // from "Projects" vs "projects" path casing on Windows
    const realNodeModules = fs.realpathSync.native(
      path.join(__dirname, "node_modules")
    );
    config.resolve.modules = [realNodeModules, "node_modules"];
    config.snapshot = {
      ...config.snapshot,
      managedPaths: [realNodeModules],
    };

    return config;
  },
};

export default nextConfig;
