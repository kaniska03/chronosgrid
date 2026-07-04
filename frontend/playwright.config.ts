import { defineConfig } from "@playwright/test";

/** E2E against the full docker-compose stack (frontend on :5173 dev or :3000 compose). */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
  },
  retries: 1,
});
