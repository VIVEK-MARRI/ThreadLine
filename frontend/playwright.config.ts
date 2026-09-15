import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";

/* Minimal production-oriented browser validation for ThreadLine.
 *
 * - Backend: a dedicated real FastAPI server (in-memory stores, so every
 *   run starts from a clean slate). Seeded by e2e/global-setup.ts.
 * - Frontend: the production build served by `vite preview` (with the same
 *   backend proxy as the dev server), so the validated bundle is the one
 *   `npm run build` produces.
 *
 * Ports are fixed so local runs are reproducible. Override with
 * E2E_BACKEND_URL / E2E_FRONTEND_URL when those ports are unavailable; the
 * frontend proxy target follows E2E_BACKEND_URL automatically.
 *
 * E2E_PYTHON selects the backend interpreter (defaults to the repo venv).
 */

const backendUrl = process.env.E2E_BACKEND_URL ?? "http://127.0.0.1:8000";
const frontendUrl = process.env.E2E_FRONTEND_URL ?? "http://127.0.0.1:4173";
const backendPort = new URL(backendUrl).port || "8000";
const frontendPort = new URL(frontendUrl).port || "4173";
// Resolve everything from this file so the suite works no matter which
// directory the command is launched from.
const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(frontendDir, "..");
const backendPython =
  process.env.E2E_PYTHON ??
  (process.platform === "win32"
    ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
    : path.join(repoRoot, ".venv", "bin", "python"));

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  workers: 2,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: frontendUrl,
    browserName: "chromium",
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  globalSetup: "./e2e/global-setup.ts",
  webServer: [
    {
      command: `"${backendPython}" -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: repoRoot,
      url: `${backendUrl}/health`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: `npm run preview -- --host 127.0.0.1 --port ${frontendPort} --strictPort`,
      cwd: frontendDir,
      url: frontendUrl,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        ...process.env,
        THREADLINE_API_PROXY: backendUrl,
      },
    },
  ],
});
