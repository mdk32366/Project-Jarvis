import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy /api to the FastAPI backend so the SPA and API share an origin
// (mirrors production, where FastAPI serves both). Build output goes to ../backend/static
// so the Docker image / FastAPI can serve it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../backend/static",
    emptyOutDir: true,
  },
});
