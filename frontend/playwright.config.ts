import { defineConfig, devices } from "@playwright/test";

const port = 4173;

export default defineConfig({
  testDir: "e2e",
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: `http://127.0.0.1:${String(port)}` },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // The built bundle, as Caddy would serve it (no backend needed for the shell).
  webServer: {
    command: `node_modules/.bin/vite build && node_modules/.bin/vite preview --host 127.0.0.1 --strictPort --port ${String(port)}`,
    url: `http://127.0.0.1:${String(port)}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
