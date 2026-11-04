import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    // The Guide page bundles docs/GUIDE.md from outside this folder. Vite resolves these
    // against this folder, which "." keeps allowed as it is by default.
    fs: { allow: [".", "../docs"] },
    proxy: {
      "/api": "http://127.0.0.1:8877",
      "/health": "http://127.0.0.1:8877",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    exclude: ["node_modules/**", "e2e/**"],
  },
});
