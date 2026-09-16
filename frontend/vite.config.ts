import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies the backend WebSocket so the browser talks to the
// same origin (no CORS concerns). The backend is unchanged.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
      "/health": { target: "http://127.0.0.1:8000" },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
