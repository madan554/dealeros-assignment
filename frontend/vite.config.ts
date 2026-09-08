import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind IPv4 explicitly. On macOS, Vite's default "localhost" can be
    // IPv6-only while Django's runserver is IPv4-only; the browser then
    // reaches the UI but /api is proxied at a stack the backend is not
    // listening on, which surfaces as a 502 the moment you need the API
    // (commonly right when signing in as the second demo user).
    host: "127.0.0.1",
    port: 5173,
    // Fail loudly if 5173 is taken. Silently moving to 5174 leaves a stale
    // tab talking to a Vite whose proxied Django has since died — again a
    // 502 on login, with nothing wrong in the auth code.
    strictPort: true,
    // Proxying keeps the browser on one origin, so the app never depends on
    // CORS being configured and the token is sent as a plain header.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
