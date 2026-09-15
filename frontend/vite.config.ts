/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// ThreadLine frontend. `vite dev` proxies /api + /health to the real
// FastAPI backend (default http://127.0.0.1:8000, override with
// THREADLINE_API_PROXY). No mock data in development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.THREADLINE_API_PROXY ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/health": {
        target: process.env.THREADLINE_API_PROXY ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  // `vite preview` serves the production build for release validation. It
  // needs the same backend proxy as the dev server; nothing here affects
  // the production bundle itself.
  preview: {
    port: 4173,
    proxy: {
      "/api": {
        target: process.env.THREADLINE_API_PROXY ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/health": {
        target: process.env.THREADLINE_API_PROXY ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    testTimeout: 120000,
    hookTimeout: 120000,
    pool: "forks",
    maxWorkers: 2,
  },
});
