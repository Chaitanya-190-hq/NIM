import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser only ever talks to /api on this origin; Vite proxies it to the
// FastAPI process. That is what keeps API keys out of frontend JavaScript.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
        timeout: 120000,
      },
    },
  },
});
