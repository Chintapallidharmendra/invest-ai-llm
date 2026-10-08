import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The backend (uvicorn) in dev; Caddy serves the same paths in deployment.
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
const proxy = { "/api": { target: backend } };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: { proxy },
  preview: { proxy },
  build: { sourcemap: false },
});
